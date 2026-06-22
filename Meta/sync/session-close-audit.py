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
  This is a DETECTIVE check — it fires after the worker already ran.

Usage:
  python3 session-close-audit.py            # default 60-min window
  python3 session-close-audit.py --minutes 30
  python3 session-close-audit.py --since 2026-05-27T20:00
  python3 session-close-audit.py --root /path/to/vault
  python3 session-close-audit.py --skip-gate-check  # disable ADR-007 extension

Root resolution order:
  1. WULONG_ROOT environment variable
  2. --root CLI argument
  3. Repo root inferred from this script's location (../../.. from Meta/sync/)

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
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional


def _resolve_root(cli_root: Optional[str] = None) -> str:
    """Resolve vault root: env var > CLI arg > script-relative inference."""
    env = os.environ.get("WULONG_ROOT", "").strip()
    if env:
        return env
    if cli_root:
        return cli_root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Paths are set after argument parsing; see _init_paths().
CHANGE_LOG: str
RECEIPTS_DIR: str
VIOLATIONS: str
BASELINE_FILE: str
AUDIT_CONFIG: str
BUS_DB: str


def _init_paths(root: str) -> None:
    global CHANGE_LOG, RECEIPTS_DIR, VIOLATIONS, BASELINE_FILE, AUDIT_CONFIG, BUS_DB
    CHANGE_LOG   = os.path.join(root, "Meta", "change-log.md")
    RECEIPTS_DIR = os.path.join(root, "Meta", "receipts")
    VIOLATIONS   = os.path.join(root, "Meta", "doctor", "enforcement-violations.md")
    BASELINE_FILE = os.path.join(root, "Meta", "compliance", "enforcement-baseline.json")
    AUDIT_CONFIG = os.path.join(root, "Meta", "sync", "session-close-audit-config.json")
    BUS_DB       = os.path.join(root, "Meta", "agent-bus", "bus.sqlite")


# Agents exempt from receipt requirement (light I/O, no per-task receipt convention)
EXEMPT_AGENTS = {"session-guard", "watch-meta", "cron", "system"}

CHANGE_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\]\s+([a-z][a-z0-9_-]+)\s*[→\-]"
)
RECEIPT_RE = re.compile(
    r"^([a-z][a-z0-9_-]+)-(\d{4}-\d{2}-\d{2})-(\d{4})-.*\.md$"
)
RECEIPT_NOTIME_RE = re.compile(
    r"^([a-z][a-z0-9_-]+)-(\d{4}-\d{2}-\d{2})-(?!\d{4}-).+\.md$"
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=60, help="Window in minutes (default 60)")
    p.add_argument("--since", type=str, default=None, help="Explicit ISO timestamp (overrides --minutes)")
    p.add_argument("--dry-run", action="store_true", help="Print to stdout, don't append violations")
    p.add_argument("--root", type=str, default=None, help="Vault root path (overrides WULONG_ROOT env var)")
    p.add_argument(
        "--skip-gate-check",
        action="store_true",
        help="Disable ADR-007 gated-worker predecessor check (for debugging only)",
    )
    return p.parse_args()


def window_start(args):
    if args.since:
        return datetime.fromisoformat(args.since)
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
_SKILL_CUTOFF = date(2026, 6, 19)
_SKILL_BOUND_AGENTS = {"coder", "execution-engineer", "merge-coder", "design-engineer"}
_SKILL_BOUND_CHANGE_TYPES = {"feature", "fix"}


def check_skill_citations(window_receipts: list[tuple]) -> list[dict]:
    """NN#18 detective check: flag bound-agent receipts missing '## Skills invoked'.

    Predicate fires IFF ALL:
      - agent in _SKILL_BOUND_AGENTS
      - change_type in _SKILL_BOUND_CHANGE_TYPES
      - receipt date >= _SKILL_CUTOFF
      - receipt body has NO '## Skills invoked' section

    Returns list of violation dicts (WARN-only).
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
        receipt_date_str = fm.get("date", "").strip()
        try:
            receipt_date = date.fromisoformat(receipt_date_str)
        except ValueError:
            continue
        if receipt_date < _SKILL_CUTOFF:
            continue
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


_GATED_WORKER_CHECKS = {
    "coder": ("contrarian", {"review_mode": "plan", "review_verdict": "PASS"}),
    "deployer": ("tester", {"status": "DONE"}),
}


def check_gated_worker_predecessors(window_receipts: list[tuple]) -> list[dict]:
    """ADR-007 detective check: flag gated-worker receipts with no predecessor gate node.

    Returns list of violation dicts (WARN-only).
    """
    violations = []
    if not os.path.isdir(RECEIPTS_DIR):
        return violations

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

    Returns [] if bus.sqlite is absent (bus not in use — not an error).
    """
    import sqlite3
    if not os.path.isfile(BUS_DB):
        return []
    violations = []
    try:
        window_utc = window.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, ValueError):
        window_utc = window.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")

    try:
        conn = sqlite3.connect(BUS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT sender, created_at FROM messages WHERE msg_type='coord' AND created_at >= ?",
            (window_utc,),
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
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

    cl_by_agent = {}
    for agent, ts, line in cl:
        cl_by_agent.setdefault(agent, []).append((ts, line))

    rc_by_agent = {}
    for agent, ts, fname in rc:
        rc_by_agent.setdefault(agent, []).append((ts, fname))

    violations = []

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

    gate_violations: list[dict] = []
    if not skip_gate_check:
        gate_violations = check_gated_worker_predecessors(rc)

    skill_violations = check_skill_citations(rc)
    bus_violations = check_bus_coord_no_changelog(window, cl_by_agent)

    return violations, gate_violations, skill_violations, bus_violations, len(cl_by_agent), len(rc_by_agent)


def _load_baseline_cutoff():
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
    """Fail-closed: missing or malformed config → block_enabled=False."""
    try:
        with open(AUDIT_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("block_enabled", False) is True)
    except Exception:
        return False


def write_violations(violations, gate_violations, skill_violations, bus_violations, window, cl_agents, rc_agents, dry_run):
    """Write violations to the enforcement log.

    gate_violations, skill_violations, and bus_violations are WARN-only.
    """
    has_any = violations or gate_violations or skill_violations or bus_violations
    if not has_any:
        return False

    cutoff = _load_baseline_cutoff()
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
    root = _resolve_root(getattr(args, "root", None))
    _init_paths(root)

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

    block_enabled = _load_audit_config()
    if block_enabled and post_baseline and violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
