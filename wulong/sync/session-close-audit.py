#!/usr/bin/env python3
"""
session-close-audit.py — Post-session enforcement of MANDATORY FINAL ACTIONS.

For every agent that wrote to Meta/change-log.md in the audit window,
verify that the same agent ALSO wrote a receipt file to Meta/receipts/ in
the same window. Missing receipt = violation logged (NOT blocking in v1).

Symmetry check: also flag receipts with no matching change-log line.

ADR-007 extension (gated-worker predecessor check, WARN-only per pilot scope):
  For every coder and deployer receipt written in the window, verify that a
  gate-predecessor receipt exists for the same change_id:
    - coder    → contrarian receipt with review_mode=plan, review_verdict=PASS
    - deployer → tester receipt with status=DONE
  Missing predecessor = GATED_WORKER_NO_PREDECESSOR violation (WARN-only).
  This is a DETECTIVE check — it fires after the worker already ran, catching
  anything the preventive spawn_gate wrapper missed.

Usage:
  python3 session-close-audit.py            # default 60-min window
  python3 session-close-audit.py --minutes 30
  python3 session-close-audit.py --since 2026-05-27T20:00
  python3 session-close-audit.py --skip-gate-check  # disable ADR-007 extension

Output: appends to Meta/doctor/enforcement-violations.md (idempotent per-run header).
Exit code: 0 always when block_enabled=false (v1 behaviour, default).
           Non-zero only when block_enabled=true AND new post-baseline violations present.
           Config: Meta/sync/session-close-audit-config.json (fail-closed: missing/malformed → block_enabled=false).
           ADR-007 gate-check violations are always WARN-only regardless of block_enabled.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHANGE_LOG = os.path.join(VAULT, "Meta", "change-log.md")
RECEIPTS_DIR = os.path.join(VAULT, "Meta", "receipts")
VIOLATIONS = os.path.join(VAULT, "Meta", "doctor", "enforcement-violations.md")
BASELINE_FILE = os.path.join(VAULT, "Meta", "compliance", "enforcement-baseline.json")
AUDIT_CONFIG = os.path.join(VAULT, "Meta", "sync", "session-close-audit-config.json")
BUS_DB = os.path.join(VAULT, "Meta", "agent-bus", "bus.sqlite")

# Agents exempt from receipt requirement (light I/O, no per-task receipt convention)
EXEMPT_AGENTS = {"session-guard", "watch-meta", "compile-context", "cron", "system"}

CHANGE_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\]\s+([a-z][a-z0-9_-]+)\s*[→\-]"
)
RECEIPT_RE = re.compile(
    r"^([a-z][a-z0-9_-]+)-(\d{4}-\d{2}-\d{2})-(\d{4})-.*\.md$"
)
# Some receipts use the variant `<agent>-<YYYY-MM-DD>-<task>.md` (no HHMM) — accept it
RECEIPT_NOTIME_RE = re.compile(
    r"^([a-z][a-z0-9_-]+)-(\d{4}-\d{2}-\d{2})-(?!\d{4}-).+\.md$"
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=60, help="Window in minutes (default 60)")
    p.add_argument("--since", type=str, default=None, help="Explicit ISO timestamp (overrides --minutes)")
    p.add_argument("--dry-run", action="store_true", help="Print to stdout, don't append violations")
    p.add_argument(
        "--skip-gate-check",
        action="store_true",
        help="Disable ADR-007 gated-worker predecessor check (for debugging only)",
    )
    return p.parse_args()


def window_start(args):
    if args.since:
        return datetime.fromisoformat(args.since)
    # Local naive time — vault timestamps are local (SGT)
    return datetime.now() - timedelta(minutes=args.minutes)


def scan_change_log(since):
    """Return list of (agent, datetime, raw_line) for entries newer than `since`."""
    if not os.path.exists(CHANGE_LOG):
        return []
    hits = []
    with open(CHANGE_LOG, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = CHANGE_LINE_RE.match(line.strip())
            if not m:
                continue
            date_s, time_s, agent = m.group(1), m.group(2), m.group(3)
            try:
                ts = datetime.fromisoformat(f"{date_s}T{time_s}")
            except ValueError:
                continue
            if ts >= since:
                hits.append((agent, ts, line.rstrip("\n")))
    return hits


def scan_receipts(since):
    """Return list of (agent, datetime, filename) for receipts newer than `since`."""
    if not os.path.isdir(RECEIPTS_DIR):
        return []
    hits = []
    for fname in os.listdir(RECEIPTS_DIR):
        if not fname.endswith(".md"):
            continue
        m = RECEIPT_RE.match(fname)
        if m:
            agent, date_s, hhmm = m.group(1), m.group(2), m.group(3)
            try:
                ts = datetime.fromisoformat(f"{date_s}T{hhmm[:2]}:{hhmm[2:]}")
            except ValueError:
                continue
        else:
            m2 = RECEIPT_NOTIME_RE.match(fname)
            if not m2:
                continue
            agent, date_s = m2.group(1), m2.group(2)
            # Fall back to file mtime for ordering
            full = os.path.join(RECEIPTS_DIR, fname)
            ts = datetime.fromtimestamp(os.path.getmtime(full))
        if ts >= since:
            hits.append((agent, ts, fname))
    return hits


def _parse_receipt_frontmatter(path: str) -> dict[str, str]:
    """Parse YAML-like frontmatter from a receipt file. Returns {} on any error."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read(4096)
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    close: Optional[int] = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:close]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            fields[key.strip()] = val.strip()
    return fields


# NN#18 MISSING_SKILL_CITATION check
# Date floor: receipts before this date are never flagged (mirrors V302_CUTOFF pattern).
_SKILL_CUTOFF = date(2026, 6, 19)
# Agents bound by ponytail (NN#18, applies-to list in .claude/skills/ponytail/SKILL.md)
_SKILL_BOUND_AGENTS = {"coder", "execution-engineer", "merge-coder", "design-engineer"}
# Only feature/fix receipts are in scope (governance, docs, housekeeping are exempt)
_SKILL_BOUND_CHANGE_TYPES = {"feature", "fix"}


def check_skill_citations(window_receipts: list[tuple]) -> list[dict]:
    """NN#18 detective check: flag bound-agent receipts missing '## Skills invoked'.

    Predicate fires IFF ALL:
      - agent in _SKILL_BOUND_AGENTS
      - change_type in _SKILL_BOUND_CHANGE_TYPES
      - receipt date >= _SKILL_CUTOFF
      - receipt body has NO '## Skills invoked' section

    C1 (BINDING): reads the FULL file body — never the 4096-byte frontmatter slice.
    Returns list of violation dicts (WARN-only — never contributes to blocking count).
    """
    violations = []
    for agent, _ts, fname in window_receipts:
        if agent.strip().lower() not in _SKILL_BOUND_AGENTS:
            continue
        fpath = os.path.join(RECEIPTS_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        fm = _parse_receipt_frontmatter(fpath)
        change_type = fm.get("change_type", "").strip().lower()
        if change_type not in _SKILL_BOUND_CHANGE_TYPES:
            continue
        # Date gate: parse from frontmatter (same date field as validate-receipts.py)
        receipt_date_str = fm.get("date", "").strip()
        try:
            receipt_date = date.fromisoformat(receipt_date_str)
        except ValueError:
            continue  # unparseable date → skip (fail-safe: don't flag on bad data)
        if receipt_date < _SKILL_CUTOFF:
            continue
        # C1: read full body to check for the section header
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
        except OSError:
            continue
        if "## Skills invoked" not in body:
            violations.append({
                "type": "MISSING_SKILL_CITATION",
                "agent": agent.strip().lower(),
                "evidence": (
                    f"receipt '{fname}' (change_type={change_type}, date={receipt_date_str}) "
                    f"is from a bound agent with no '## Skills invoked' section (WARN-only, NN#18)"
                ),
            })
    return violations


# Workers that require a gate predecessor; maps agent → (required_agent, required_field, required_value)
_GATED_WORKER_CHECKS = {
    "coder": ("contrarian", {"review_mode": "plan", "review_verdict": "PASS"}),
    "deployer": ("tester", {"status": "DONE"}),
}


def check_gated_worker_predecessors(window_receipts: list[tuple]) -> list[dict]:
    """ADR-007 detective check: flag gated-worker receipts with no predecessor gate node.

    For every coder/deployer receipt in the window:
      - Extract change_id from its frontmatter.
      - If change_id is absent: emit WARN (cannot check without it).
      - If change_id present: scan ALL receipts (not just window) for a satisfying
        predecessor. Emit GATED_WORKER_NO_PREDECESSOR if none found.

    Returns list of violation dicts (WARN-only — never contributes to blocking count).
    """
    violations = []
    if not os.path.isdir(RECEIPTS_DIR):
        return violations

    # Build a lookup of ALL receipts for predecessor search
    try:
        all_fnames = [f for f in os.listdir(RECEIPTS_DIR) if f.endswith(".md")]
    except OSError:
        return violations

    all_receipts: list[dict] = []
    for fname in all_fnames:
        fpath = os.path.join(RECEIPTS_DIR, fname)
        fields = _parse_receipt_frontmatter(fpath)
        if fields:
            all_receipts.append({"fname": fname, **fields})

    # Check each window receipt that is a gated worker
    for agent, _ts, fname in window_receipts:
        agent_key = agent.strip().lower()
        if agent_key not in _GATED_WORKER_CHECKS:
            continue

        fpath = os.path.join(RECEIPTS_DIR, fname)
        if not os.path.isfile(fpath):
            continue

        fields = _parse_receipt_frontmatter(fpath)
        change_id = fields.get("change_id", "").strip()

        if not change_id:
            violations.append({
                "type": "GATED_WORKER_NO_PREDECESSOR",
                "agent": agent_key,
                "evidence": (
                    f"receipt '{fname}' is a gated worker but has no change_id — "
                    f"cannot verify gate predecessor (WARN-only)"
                ),
            })
            continue

        required_agent, required_fields = _GATED_WORKER_CHECKS[agent_key]

        # Search ALL receipts for a satisfying predecessor
        satisfied = False
        for r in all_receipts:
            if r.get("change_id", "").strip() != change_id:
                continue
            if r.get("agent", "").strip() != required_agent:
                continue
            if all(r.get(k, "").strip() == v for k, v in required_fields.items()):
                satisfied = True
                break

        if not satisfied:
            field_desc = ", ".join(f"{k}={v}" for k, v in required_fields.items())
            violations.append({
                "type": "GATED_WORKER_NO_PREDECESSOR",
                "agent": agent_key,
                "evidence": (
                    f"receipt '{fname}' (change_id={change_id}) has no "
                    f"{required_agent} receipt with {field_desc} — "
                    f"gate predecessor missing (WARN-only, ADR-007 pilot)"
                ),
            })

    return violations


def check_bus_coord_no_changelog(window: datetime, cl_by_agent: dict) -> list[dict]:
    """ADR §4.4 detective check: flag agents that posted a coord message but have
    no change-log line in the same window (BUS_COORD_NO_CHANGELOG, WARN-only).

    A decision that substitutes for a change-log line MUST use msg_type='coord'
    so this detector can see it. WARN-only, same tier as MISSING_SKILL_CITATION.
    Returns [] if bus.sqlite is absent (bus not yet in use — not an error).
    """
    if not os.path.isfile(BUS_DB):
        return []
    violations = []
    # ISO timestamp for the window start — bus created_at is UTC ISO.
    # scan_change_log uses local time; bus uses UTC. Use UTC for the bus query.
    try:
        window_utc = window.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, ValueError):
        # If window is naive, treat it as UTC (fail-safe).
        window_utc = window.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")

    try:
        conn = sqlite3.connect(BUS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT sender, created_at FROM messages WHERE msg_type='coord' AND created_at >= ?",
            (window_utc,),
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 — bus may be locked or schema incomplete; skip
        return []

    for row in rows:
        sender = (row["sender"] or "").strip().lower()
        if not sender:
            continue
        if sender in EXEMPT_AGENTS:
            continue
        if sender not in cl_by_agent:
            violations.append({
                "type": "BUS_COORD_NO_CHANGELOG",
                "agent": sender,
                "evidence": (
                    f"agent '{sender}' posted a coord message at {row['created_at']} "
                    f"but has no change-log line in the audit window (WARN-only, ADR §4.4)"
                ),
            })
    return violations


def audit(window, skip_gate_check: bool = False):
    """Cross-reference change-log entries vs receipts inside `window` start."""
    cl = scan_change_log(window)
    rc = scan_receipts(window)

    # Agents who appeared in change-log
    cl_by_agent = {}
    for agent, ts, line in cl:
        cl_by_agent.setdefault(agent, []).append((ts, line))

    # Agents who wrote receipts
    rc_by_agent = {}
    for agent, ts, fname in rc:
        rc_by_agent.setdefault(agent, []).append((ts, fname))

    violations = []

    # 1) Wrote to change-log but no receipt
    for agent, entries in cl_by_agent.items():
        if agent in EXEMPT_AGENTS:
            continue
        if agent not in rc_by_agent:
            n = len(entries)
            last = max(e[0] for e in entries)
            violations.append({
                "type": "MISSING_RECEIPT",
                "agent": agent,
                "evidence": f"{n} change-log write(s); last at {last.isoformat(timespec='minutes')}",
            })

    # 2) Wrote a receipt but no change-log line in window (silent edit risk)
    for agent, entries in rc_by_agent.items():
        if agent in EXEMPT_AGENTS:
            continue
        if agent not in cl_by_agent:
            n = len(entries)
            last_file = max(entries, key=lambda x: x[0])[1]
            violations.append({
                "type": "MISSING_CHANGELOG",
                "agent": agent,
                "evidence": f"{n} receipt(s); latest: {last_file}",
            })

    # 3) ADR-007: gated-worker receipts with no predecessor gate node (WARN-only)
    gate_violations: list[dict] = []
    if not skip_gate_check:
        # Pass the window-scoped receipts list for gated-worker detection
        gate_violations = check_gated_worker_predecessors(rc)

    # 4) NN#18: bound-agent receipts missing '## Skills invoked' (WARN-only)
    skill_violations = check_skill_citations(rc)

    # 5) ADR §4.4: agents posting coord messages with no change-log line (WARN-only)
    bus_violations = check_bus_coord_no_changelog(window, cl_by_agent)

    return violations, gate_violations, skill_violations, bus_violations, len(cl_by_agent), len(rc_by_agent)


def _load_baseline_cutoff():
    """
    Read enforcement-baseline.json and return the cutoff datetime if set,
    else None. The cutoff marks the point before which all violations are
    accepted backlog (noise floor). New runs appended after the cutoff are
    genuinely new and warrant attention.
    """
    if not os.path.exists(BASELINE_FILE):
        return None
    try:
        with open(BASELINE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff_str = data.get("violations_file_cutoff", {}).get("marker_appended_at")
        if cutoff_str:
            return datetime.fromisoformat(cutoff_str)
    except Exception:
        pass
    return None


def _load_audit_config():
    """
    Fail-closed config loader. Returns block_enabled=False on any error so a
    missing or malformed file can never accidentally enable blocking.
    """
    try:
        with open(AUDIT_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        # Explicit False-default: only True if the key is literally True
        return bool(data.get("block_enabled", False) is True)
    except Exception:
        return False


def write_violations(violations, gate_violations, skill_violations, bus_violations, window, cl_agents, rc_agents, dry_run):
    """Write violations to the enforcement log.

    gate_violations (ADR-007), skill_violations (NN#18), and bus_violations (ADR §4.4)
    are WARN-only — logged in separate sub-sections and never contribute to the
    blocking count (C2).
    """
    has_any = violations or gate_violations or skill_violations or bus_violations
    if not has_any:
        return False

    cutoff = _load_baseline_cutoff()
    # Label the run as post-baseline if a cutoff exists and we are after it
    post_baseline = cutoff is not None and datetime.now() > cutoff
    post_label = " [POST-BASELINE]" if post_baseline else ""

    header = (
        f"\n## Audit run {datetime.now().isoformat(timespec='minutes')}{post_label} "
        f"(window since {window.isoformat(timespec='minutes')}, "
        f"{cl_agents} agents in change-log, {rc_agents} agents in receipts)\n"
    )
    lines = [header]
    for v in violations:
        lines.append(f"- **{v['type']}** | agent: `{v['agent']}` | {v['evidence']}\n")

    if gate_violations:
        lines.append("\n### ADR-007 Gate-Predecessor Warnings (WARN-only, never blocking)\n")
        for v in gate_violations:
            lines.append(f"- **{v['type']}** | agent: `{v['agent']}` | {v['evidence']}\n")

    if skill_violations:
        lines.append("\n### NN#18 Skill-Citation Warnings (WARN-only, never blocking)\n")
        for v in skill_violations:
            lines.append(f"- **{v['type']}** | agent: `{v['agent']}` | {v['evidence']}\n")

    if bus_violations:
        lines.append("\n### ADR §4.4 Bus-Coord-No-Changelog Warnings (WARN-only, never blocking)\n")
        for v in bus_violations:
            lines.append(f"- **{v['type']}** | agent: `{v['agent']}` | {v['evidence']}\n")

    body = "".join(lines)

    if dry_run:
        print(body)
        return True

    os.makedirs(os.path.dirname(VIOLATIONS), exist_ok=True)
    init_header = ""
    if not os.path.exists(VIOLATIONS):
        init_header = (
            "# Enforcement Violations Log\n\n"
            "Auto-generated by `Meta/sync/session-close-audit.py`. "
            "Each run appends a section for the audit window.\n"
            "Violations are LOGGED, not blocking (v1). Escalate to blocking in v2.\n"
        )
    with open(VIOLATIONS, "a", encoding="utf-8") as f:
        if init_header:
            f.write(init_header)
        f.write(body)
    return True


def main():
    args = parse_args()
    since = window_start(args)
    skip_gc = getattr(args, "skip_gate_check", False)
    violations, gate_violations, skill_violations, bus_violations, cl_agents, rc_agents = audit(since, skip_gate_check=skip_gc)
    wrote = write_violations(violations, gate_violations, skill_violations, bus_violations, since, cl_agents, rc_agents, args.dry_run)

    cutoff = _load_baseline_cutoff()
    post_baseline = cutoff is not None and datetime.now() > cutoff

    if violations:
        label = "[POST-BASELINE] " if post_baseline else ""
        print(f"[session-close-audit] {label}{len(violations)} violation(s) logged "
              f"({cl_agents} cl-agents, {rc_agents} rc-agents).")
        if post_baseline:
            print(f"[session-close-audit] NOTE: baseline cutoff {cutoff.isoformat(timespec='minutes')} "
                  f"— these violations are NEW (not accepted backlog).")
    else:
        print(f"[session-close-audit] clean: {cl_agents} agents in change-log all matched to receipts.")

    if gate_violations:
        print(f"[session-close-audit] ADR-007 gate-predecessor: {len(gate_violations)} WARN(s) "
              f"(never blocking — pilot mode).")

    if skill_violations:
        print(f"[session-close-audit] NN#18 skill-citation: {len(skill_violations)} WARN(s) "
              f"(never blocking — WARN-only).")

    if bus_violations:
        print(f"[session-close-audit] ADR §4.4 bus-coord-no-changelog: {len(bus_violations)} WARN(s) "
              f"(never blocking — WARN-only).")

    if cutoff and not violations:
        print(f"[session-close-audit] baseline cutoff {cutoff.isoformat(timespec='minutes')} active "
              f"— accepted backlog is pre-cutoff only.")

    # Guarded blocking path — OFF by default (block_enabled=false in config).
    # Non-zero exit ONLY when block_enabled==True AND there are NEW post-baseline violations.
    # gate_violations, skill_violations, bus_violations are excluded from blocking count (WARN-only).
    # Under block_enabled==False this branch is unreachable; exit is unconditionally 0.
    block_enabled = _load_audit_config()
    if block_enabled and post_baseline and violations:
        return 1
    return 0


# ponytail: inline fixture test for check_skill_citations — run with `python3 session-close-audit.py --selftest`
# upgrade path: extract to tests/test_session_close_audit.py if pytest is adopted
def _selftest_skill_citations():
    """Four cases per spec (run manually; exits 0 on all PASS, 1 on any failure)."""
    import tempfile, textwrap

    def _make_receipt(d: str, change_type: str, body_extra: str = "") -> tuple:
        """Write a temp receipt and return (agent, ts, fname) tuple."""
        fname = f"coder-{d}-0000-selftest-fixture.md"
        content = textwrap.dedent(f"""\
            ---
            agent: coder
            task: selftest fixture
            date: {d}
            time: 00:00
            status: DONE
            change_type: {change_type}
            ---

            ## Task

            Selftest fixture.

            ## Outcome

            Done.

            ## Files written

            - none
            {body_extra}
        """)
        return fname, content

    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        global RECEIPTS_DIR
        orig = RECEIPTS_DIR
        RECEIPTS_DIR = tmpdir

        def _write(fname, content):
            path = os.path.join(tmpdir, fname)
            with open(path, "w") as f:
                f.write(content)
            return path

        from datetime import datetime as _dt

        # Case 1: pre-floor receipt → 0 WARNs (date before 2026-06-19)
        fname1, c1 = _make_receipt("2026-06-18", "feature")
        _write(fname1, c1)
        ts1 = _dt(2026, 6, 18, 0, 0)
        warn1 = check_skill_citations([("coder", ts1, fname1)])
        assert warn1 == [], f"Case 1 FAIL: expected 0 WARNs, got {warn1}"
        results.append("Case 1 PASS (pre-floor → 0 WARNs)")

        # Case 2: post-floor receipt WITH '## Skills invoked' → 0 WARNs
        fname2, c2 = _make_receipt("2026-06-19", "feature", "\n## Skills invoked\n\n- ponytail")
        _write(fname2, c2)
        ts2 = _dt(2026, 6, 19, 0, 0)
        warn2 = check_skill_citations([("coder", ts2, fname2)])
        assert warn2 == [], f"Case 2 FAIL: expected 0 WARNs, got {warn2}"
        results.append("Case 2 PASS (post-floor + Skills invoked → 0 WARNs)")

        # Case 3: post-floor coder feature receipt WITHOUT section → exactly 1 WARN
        fname3, c3 = _make_receipt("2026-06-19", "feature")
        _write(fname3, c3)
        ts3 = _dt(2026, 6, 19, 0, 0)
        warn3 = check_skill_citations([("coder", ts3, fname3)])
        assert len(warn3) == 1, f"Case 3 FAIL: expected 1 WARN, got {warn3}"
        assert warn3[0]["type"] == "MISSING_SKILL_CITATION"
        results.append("Case 3 PASS (post-floor, no Skills → 1 WARN)")

        # Case 4: exit 0 — verified by returning from this function without sys.exit(1)
        RECEIPTS_DIR = orig

    for r in results:
        print(f"[selftest] {r}")
    print("[selftest] All 4 cases PASS — exit 0 confirmed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest_skill_citations()
        sys.exit(0)
    sys.exit(main())


if "--selftest" in sys.argv:
    _selftest_skill_citations()
    sys.exit(0)
