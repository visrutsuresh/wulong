#!/usr/bin/env python3
"""
verify-change.py — End-to-end Definition-of-Done verifier for one change_id.

Answers: "Did this change actually land correctly?"

Checks D1-D8 from Meta/definition-of-done.md against a single change_id.
HARD failures (D1-D4, D6) flip the verdict RED.
SOFT checks (D5, D8) print as WARN and do not flip GREEN→RED in v1.
D7 is always N/A: its plug-in dispatch was removed in 0.4.0 (see SECURITY.md).

Usage:
  python3 verify-change.py --change-id X [--strict] [--json] [--report-out PATH]
                           [--since YYYY-MM-DD]

Exit codes:
  0  GREEN verdict, OR RED in default log-only mode (change_id found + ran).
  1  RED in --strict mode.
  2  Usage error (missing/invalid --change-id, or receipts dir not found).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from typing import Optional

from wulong._frontmatter import parse_frontmatter
from wulong._root import resolve_root

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Script lives at <vault>/Meta/sync/verify-change.py
# META_DIR = <vault>/Meta
# VAULT_ROOT = <vault>  (the Obsidian root — receipts claim paths relative to this)
# Install-relative FLOOR only, reached when no root was handed down. This script
# runs as a child of an entry point, which passes the resolved root in the
# environment, so this tier fires only on direct manual invocation.
VAULT_ROOT = resolve_root(
    fallback=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    tool="verify-change",
)
META_DIR   = os.path.join(VAULT_ROOT, "Meta")

VAULT    = META_DIR   # keep alias for sub-module calls that expect Meta dir
RECEIPTS = os.path.join(META_DIR, "receipts")
SYNC_DIR = os.path.join(META_DIR, "sync")

VALIDATE_RECEIPTS      = os.path.join(SYNC_DIR, "validate-receipts.py")
VALIDATE_GRAPH         = os.path.join(SYNC_DIR, "validate-receipt-graph.py")
SESSION_CLOSE_AUDIT    = os.path.join(SYNC_DIR, "session-close-audit.py")

# Remote path prefixes that cannot be checked locally — skip with NOTE
REMOTE_PATH_PREFIXES = ("/root/", "/home/", "root@", "~root/")

def _parse_body(content: str) -> str:
    """Return body text (after closing frontmatter ---)."""
    if not content.startswith("---"):
        return content
    lines = content.split("\n")
    close = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        return content
    return "\n".join(lines[close + 1:])

# ---------------------------------------------------------------------------
# D1: member selection
# ---------------------------------------------------------------------------

def _find_members(change_id: str, since: Optional[date]) -> list[dict]:
    """Return list of receipt dicts for the given change_id."""
    if not os.path.isdir(RECEIPTS):
        return []

    members = []
    for entry in os.listdir(RECEIPTS):
        if not entry.endswith(".md"):
            continue

        # Optional date filter for load speed
        if since is not None:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", entry)
            if m:
                try:
                    fdate = date.fromisoformat(m.group(1))
                    if fdate < since:
                        continue
                except ValueError:
                    pass

        path = os.path.join(RECEIPTS, entry)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue

        fields = parse_frontmatter(content)
        if fields.get("change_id", "").strip() == change_id:
            members.append({
                "fname":   entry,
                "path":    path,
                "fields":  fields,
                "body":    _parse_body(content),
                "content": content,
            })

    return members

# ---------------------------------------------------------------------------
# D3: terminal status check
# ---------------------------------------------------------------------------

CANONICAL_STATUSES = {"DONE", "FAIL", "BLOCKED", "PARTIAL"}


def _is_terminal_done(members: list[dict]) -> list[dict]:
    """Return list of receipts whose status is not DONE."""
    non_done = []
    for m in members:
        status = m["fields"].get("status", "").strip().upper()
        if status != "DONE":
            non_done.append(m)
    return non_done

# ---------------------------------------------------------------------------
# D4: phantom-file check
# ---------------------------------------------------------------------------

# Patterns that indicate non-path prose we must NOT false-RED on.
# A line is treated as SKIP-able if it matches these patterns.
_PROSE_SKIP_RE = re.compile(
    r"""
    ^\d+\s+\w          # starts with a number + word: "3 plan docs", "2 files", "1 receipt"
    | ^This\s          # "This receipt."
    | ^NEW:\s*\w.*,    # "NEW: a, b, c" (comma-separated labels, not paths)
    | ^[A-Z][a-z].*\.$  # sentence-like: "Updated planning notes."
    """,
    re.VERBOSE,
)


def _looks_like_path(s: str) -> bool:
    """True if s looks like a checkable local filesystem path.

    Conservative: requires at least one '/' and no whitespace (paths don't have spaces
    unless quoted — we skip quoted-with-spaces as prose). Also skips remote prefixes.
    """
    if not s:
        return False
    if " " in s:
        return False
    if "/" not in s:
        return False
    return True


def _is_remote(path: str) -> bool:
    return any(path.startswith(p) for p in REMOTE_PATH_PREFIXES)


def _extract_files_written(body: str) -> tuple[list[str], list[str]]:
    """Parse '## Files written' section. Returns (paths_to_check, notes).

    paths_to_check: cleanly-parsed local paths to assert exist on disk.
    notes:          human-readable notes about skipped/remote/prose lines.

    Design (C2 condition from spec):
      - Catch genuine absent named paths.
      - FAIL-OPEN on non-path prose: emit a NOTE, never RED, on ambiguous lines.
      - Remote paths (/root/, /home/, etc.): NOTE, never RED.
    """
    # Find the ## Files written section
    section_match = re.search(
        r"^##\s+Files\s+written\s*\n(.*?)(?=^##\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return [], []

    section_text = section_match.group(1)
    paths: list[str] = []
    notes: list[str] = []

    for raw_line in section_text.splitlines():
        # Strip bullet markers and whitespace
        line = raw_line.strip().lstrip("-*•").strip()
        # Strip inline backticks and trailing punctuation
        line = line.strip("`").rstrip(".,;:)")

        if not line:
            continue

        # Split on commas to handle comma-separated lists like "a.py, b.py"
        parts = [p.strip().strip("`").rstrip(".,;:)") for p in line.split(",")]

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Strip common inline description annotations: "path — description" or "path (desc)"
            # Take only the first token if followed by a separator
            annotation_match = re.match(r"^([^\s]+)\s+[—–\-\(]", part)
            if annotation_match:
                part = annotation_match.group(1).strip()

            # Final cleanup: strip backticks (closing inline-code spans) + trailing punctuation
            part = part.strip("`").rstrip(".,;:)(")

            if not part:
                continue

            # Check for prose patterns first (C2: fail-open on prose)
            if _PROSE_SKIP_RE.match(raw_line.strip().lstrip("-*•").strip()):
                notes.append(f"D4: skipped prose line in Files-written: {raw_line.strip()!r}")
                break  # whole original line is prose, skip all parts

            if not _looks_like_path(part):
                # Not a path-shaped token — prose or label, skip
                notes.append(f"D4: skipped non-path token: {part!r}")
                continue

            if _is_remote(part):
                notes.append(f"D4: skipped remote path (not disk-checkable locally): {part}")
                continue

            # Normalize to absolute: vault-relative → absolute
            if not os.path.isabs(part):
                # Paths in receipts are vault-root-relative (e.g. "Meta/sync/foo.py")
                part = os.path.join(VAULT_ROOT, part)

            paths.append(part)

    return paths, notes


def _check_d4(members: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (failures, notes).
    failures: list of {receipt, claimed_path} for paths that don't exist.
    notes: informational skips (remote, prose).
    """
    failures = []
    all_notes: list[str] = []

    for m in members:
        paths, notes = _extract_files_written(m["body"])
        all_notes.extend(notes)
        for p in paths:
            if not os.path.exists(p):
                # Before declaring phantom: check archive/ for handoff paths.
                # Jarvis archives consumed handoffs to Meta/handoffs/archive/ every
                # session, so a receipt written before archiving correctly claimed the
                # original path — the file genuinely moved, not a phantom.
                resolved = False
                handoffs_dir = os.path.join(META_DIR, "handoffs")
                if os.path.normpath(p).startswith(os.path.normpath(handoffs_dir)):
                    archive_candidate = os.path.join(
                        handoffs_dir, "archive", os.path.basename(p)
                    )
                    if os.path.exists(archive_candidate):
                        all_notes.append(
                            f"D4: resolved via archive/: {os.path.basename(p)}"
                        )
                        resolved = True
                if not resolved:
                    failures.append({"receipt": m["fname"], "claimed_path": p})

    return failures, all_notes

# ---------------------------------------------------------------------------
# D2: schema validation via subprocess
# ---------------------------------------------------------------------------

def _check_d2(members: list[dict]) -> list[dict]:
    """Run validate-receipts.py --file per member. Return list of {receipt, detail}."""
    failures = []
    for m in members:
        try:
            result = subprocess.run(
                [sys.executable, VALIDATE_RECEIPTS, "--file", m["path"]],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            failures.append({"receipt": m["fname"], "detail": f"validator unavailable: {e}"})
            continue

        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()
            failures.append({"receipt": m["fname"], "detail": detail or "schema violation (no detail)"})

    return failures

# ---------------------------------------------------------------------------
# D6: graph gate check
# ---------------------------------------------------------------------------

# Gating conditions mirror the spec (A.3)
GATED_CHANGE_TYPES    = {"feature", "fix"}
UNGATED_CHANGE_TYPES  = {"governance", "docs", "housekeeping"}


def _is_gated(members: list[dict]) -> tuple[bool, str]:
    """Return (is_gated, reason_string)."""
    for m in members:
        agent = m["fields"].get("agent", "").strip()
        ctype = m["fields"].get("change_type", "").strip()
        if agent == "coder" and ctype in GATED_CHANGE_TYPES:
            return True, f"coder receipt with change_type={ctype}"
        if agent == "deployer":
            return True, "deployer receipt present"
        if ctype in GATED_CHANGE_TYPES:
            return True, f"change_type={ctype} on {agent} receipt"

    # If all non-empty change_types are explicitly ungated
    change_types = {
        m["fields"].get("change_type", "").strip()
        for m in members
        if m["fields"].get("change_type", "").strip()
    }
    if change_types and change_types.issubset(UNGATED_CHANGE_TYPES):
        types_str = ",".join(sorted(change_types))
        return False, f"change_type in {{{types_str}}} with no coder/deployer receipt"

    # No change_type info → treat as potentially gated (conservative)
    return True, "no change_type on any receipt — conservative: gated"


def _check_d6(change_id: str, members: list[dict], since: Optional[date]) -> tuple[str, str, str]:
    """Return (status, verdict, detail).

    status: 'PASS' | 'FAIL' | 'NA'
    verdict: 'COMPLETE' | 'VIOLATION' | 'IN_PROGRESS' | 'NA' | 'ERROR'
    detail: human-readable explanation
    """
    gated, gate_reason = _is_gated(members)
    if not gated:
        return "NA", "NA", f"not gated ({gate_reason})"

    # Determine earliest receipt date for --since arg to graph validator
    earliest_date: Optional[str] = None
    if since is not None:
        earliest_date = since.isoformat()
    else:
        dates = []
        for m in members:
            d = m["fields"].get("date", "").strip()
            if d:
                dates.append(d)
        if dates:
            earliest_date = min(dates)

    cmd = [sys.executable, VALIDATE_GRAPH, "--change-id", change_id]
    if earliest_date:
        cmd += ["--since", earliest_date]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return "FAIL", "ERROR", f"graph validator unavailable: {e}"

    output = result.stdout + result.stderr

    # Parse per-change_id verdict line: "[OK] change_id: COMPLETE" etc.
    # The graph validator prints: "    [OK] <cid>: COMPLETE" or "    [!!] <cid>: VIOLATION" etc.
    verdict_match = re.search(
        r"\[(?:OK|!!|~)\]\s+" + re.escape(change_id) + r":\s+(\S+)",
        output,
    )

    if not verdict_match:
        # change_id not found in output — either no graph-era receipts or no members
        # Check if any member is in the graph era (post-2026-05-30)
        graph_era = date(2026, 5, 30)
        in_era = False
        for m in members:
            d_str = m["fields"].get("date", "").strip()
            if d_str:
                try:
                    if date.fromisoformat(d_str.split()[0]) >= graph_era:
                        in_era = True
                        break
                except ValueError:
                    pass
        if not in_era:
            return "NA", "NA", "all receipts predate graph era (2026-05-30) — D6 not applicable"
        # In era but not in output — no graph fields stamped
        return "FAIL", "VIOLATION", (
            f"change_id not found in graph validator output — "
            f"receipts may be missing gated_by edges or review_verdict fields. "
            f"Graph output: {output[:400].strip()}"
        )

    graph_verdict = verdict_match.group(1).strip()

    # C3 condition: treat IN_PROGRESS as NOT done (RED under strict, pending otherwise)
    if graph_verdict == "COMPLETE":
        return "PASS", "COMPLETE", f"graph validator: COMPLETE (gated by: {gate_reason})"
    elif graph_verdict == "VIOLATION":
        # Extract the violation detail lines
        viols = re.findall(r"\[NN\w+\].*", output)
        viol_str = "; ".join(viols[:3]) if viols else "see graph validator output"
        return "FAIL", "VIOLATION", (
            f"graph validator: VIOLATION — {viol_str}"
        )
    elif graph_verdict == "IN_PROGRESS":
        # C3: IN_PROGRESS = gates not yet closed — RED (not silently passing)
        return "FAIL", "IN_PROGRESS", (
            f"graph validator: IN_PROGRESS — required gate edges not yet complete "
            f"(contrarian output-PASS not yet reachable from latest coder receipt). "
            f"Change is in-flight, not done."
        )
    else:
        return "FAIL", "UNKNOWN", f"graph validator returned unrecognized verdict: {graph_verdict!r}"

# ---------------------------------------------------------------------------
# D5 + D8: soft checks via session-close-audit
# ---------------------------------------------------------------------------

def _check_d5_d8(members: list[dict], since: Optional[date]) -> list[str]:
    """Run session-close-audit.py --dry-run. Return list of warning strings."""
    # Find the window: use the earliest member date, or 'since'
    window_arg: Optional[str] = None
    if since is not None:
        window_arg = since.isoformat() + "T00:00"
    else:
        dates = []
        for m in members:
            d = m["fields"].get("date", "").strip()
            if d:
                dates.append(d)
        if dates:
            window_arg = min(dates) + "T00:00"

    cmd = [sys.executable, SESSION_CLOSE_AUDIT, "--dry-run"]
    if window_arg:
        cmd += ["--since", window_arg]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return [f"D5/D8: audit tool unavailable: {e}"]

    output = (result.stdout + result.stderr).strip()
    if not output or "No violations" in output or "0 violation" in output:
        return []

    # Filter to violations relevant to member agents
    member_agents = {m["fields"].get("agent", "").strip() for m in members}
    lines = output.splitlines()
    relevant = [
        l for l in lines
        if any(agent in l for agent in member_agents if agent) or "violation" in l.lower()
    ]
    return relevant[:10]  # cap to avoid noise-flooding the report

# ---------------------------------------------------------------------------
# D7: removed in 0.4.0
# ---------------------------------------------------------------------------

# D7 used to load Meta/qa/e2e-plugins.yaml from the scanned vault and run each
# plug-in's "cmd" through the shell, so the scanned directory chose both the
# command and its arguments. No manifest was ever shipped, which made it a
# speculative feature with a shell sink attached. It is gone. wulong still runs
# vault-resident scripts by name, by design; see SECURITY.md.

_D7_DETAIL = (
    "plug-in dispatch removed in 0.4.0: it built a shell command string from a "
    "manifest inside the scanned vault. No project plug-ins exist, so D7 is "
    "always N/A. See SECURITY.md."
)


def _check_d7() -> tuple[str, str]:
    """Always ('NA', reason). The plug-in dispatch was removed in 0.4.0."""
    return "NA", _D7_DETAIL

# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "PASS": "[PASS]",
    "FAIL": "[FAIL]",
    "NA":   "[ N/A]",
    "WARN": "[WARN]",
}


def _format_report(
    change_id: str,
    d1_count: int,
    d2_failures: list[dict],
    d3_non_done: list[dict],
    d4_failures: list[dict],
    d4_notes: list[str],
    d5_d8_warns: list[str],
    d6_status: str,
    d6_verdict: str,
    d6_detail: str,
    d7_status: str,
    d7_detail: str,
    verdict: str,
) -> str:
    lines = [
        f"verify-change.py — change_id: {change_id}",
        f"VERDICT: {verdict}",
        "",
    ]

    # D1
    lines.append(f"  {_STATUS_ICON['PASS']} D1 receipt exists ({d1_count} receipt{'s' if d1_count != 1 else ''})")

    # D2
    if not d2_failures:
        lines.append(f"  {_STATUS_ICON['PASS']} D2 receipt schema valid")
    else:
        lines.append(f"  {_STATUS_ICON['FAIL']} D2 receipt schema:")
        for f in d2_failures:
            first_line = f["detail"].splitlines()[0] if f["detail"] else "violation"
            lines.append(f"         {f['receipt']}: {first_line}")
        lines.append(f"         Fix: correct the frontmatter/body per the schema in cerebrum.md.")

    # D3
    if not d3_non_done:
        lines.append(f"  {_STATUS_ICON['PASS']} D3 terminal status DONE")
    else:
        lines.append(f"  {_STATUS_ICON['FAIL']} D3 terminal status:")
        for m in d3_non_done:
            status = m["fields"].get("status", "(missing)").strip()
            lines.append(f"         {m['fname']}: status={status}")
            # Try to extract Next Step
            next_step_match = re.search(r"##\s*Next Step[^\n]*\n(.+?)(?=\n##|\Z)", m["body"], re.DOTALL)
            if next_step_match:
                ns = next_step_match.group(1).strip()[:120]
                lines.append(f"         Next Step: {ns}")
        lines.append(f"         Fix: update status to DONE once complete, or resolve the BLOCKED/PARTIAL state.")

    # D4
    if not d4_failures:
        lines.append(f"  {_STATUS_ICON['PASS']} D4 claimed files exist on disk")
    else:
        lines.append(f"  {_STATUS_ICON['FAIL']} D4 phantom artifact{'s' if len(d4_failures) > 1 else ''}:")
        for f in d4_failures:
            lines.append(f"         receipt {f['receipt']} claims it wrote")
            lines.append(f"         {f['claimed_path']}  —  THIS FILE DOES NOT EXIST ON DISK.")
        lines.append(f"         Fix: either create the file or remove the claim from '## Files written'.")
    for note in d4_notes[:5]:
        lines.append(f"  [NOTE] {note}")

    # D5/D8
    if not d5_d8_warns:
        lines.append(f"  {_STATUS_ICON['PASS']} D5/D8 change-log + enforcement (no warnings)")
    else:
        lines.append(f"  {_STATUS_ICON['WARN']} D5/D8 change-log/enforcement (SOFT — not blocking):")
        for w in d5_d8_warns[:5]:
            lines.append(f"         {w}")

    # D6
    icon = _STATUS_ICON.get(d6_status, "[????]")
    if d6_status == "NA":
        lines.append(f"  {_STATUS_ICON['NA']} D6 gate edges — {d6_detail}")
    elif d6_status == "PASS":
        lines.append(f"  {_STATUS_ICON['PASS']} D6 gate edges COMPLETE")
    else:
        lines.append(f"  {icon} D6 gate edges: {d6_verdict}")
        lines.append(f"         {d6_detail}")
        if d6_verdict == "IN_PROGRESS":
            lines.append(f"         Fix: complete the contrarian output-review and tester gate for this change.")
        elif d6_verdict == "VIOLATION":
            lines.append(f"         Fix: ensure the contrarian plan+output PASS receipts exist with correct gated_by edges.")

    # D7
    icon7 = _STATUS_ICON.get(d7_status, "[????]")
    if d7_status == "NA":
        lines.append(f"  {_STATUS_ICON['NA']} D7 project smoke test — {d7_detail}")
    elif d7_status == "PASS":
        lines.append(f"  {_STATUS_ICON['PASS']} D7 project smoke test PASS — {d7_detail}")
    else:
        lines.append(f"  {icon7} D7 project smoke test FAIL — {d7_detail}")

    # Footer
    lines.append("")
    hard_fails = sum([
        1 if d2_failures else 0,
        1 if d3_non_done else 0,
        1 if d4_failures else 0,
        1 if d6_status == "FAIL" else 0,
        1 if d7_status == "FAIL" else 0,
    ])
    soft_warns = len(d5_d8_warns)

    if verdict == "GREEN":
        lines.append("RESULT: All HARD criteria pass. The change is DONE (workflow loop closed).")
        lines.append("NOTE: GREEN means: recorded + gated + artifacts exist. It does NOT mean the")
        lines.append("      project's own tests passed. Run those yourself.")
    else:
        lines.append(f"WHAT TO DO: {hard_fails} HARD failure{'s' if hard_fails != 1 else ''}. "
                     f"The change is NOT done until all HARD criteria pass.")
        if soft_warns:
            lines.append(f"            {soft_warns} SOFT warning{'s' if soft_warns != 1 else ''} (advisory, not blocking in v1).")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _build_json(
    change_id: str,
    d1_count: int,
    d2_failures: list[dict],
    d3_non_done: list[dict],
    d4_failures: list[dict],
    d4_notes: list[str],
    d5_d8_warns: list[str],
    d6_status: str,
    d6_verdict: str,
    d6_detail: str,
    d7_status: str,
    d7_detail: str,
    verdict: str,
) -> dict:
    checks = []

    checks.append({"id": "D1", "status": "PASS", "severity": "HARD",
                   "detail": f"{d1_count} receipt(s) found"})

    if d2_failures:
        checks.append({"id": "D2", "status": "FAIL", "severity": "HARD",
                       "detail": "; ".join(f["receipt"] + ": " + f["detail"][:80] for f in d2_failures),
                       "fix": "correct frontmatter/body per cerebrum.md schema"})
    else:
        checks.append({"id": "D2", "status": "PASS", "severity": "HARD", "detail": "schema valid"})

    if d3_non_done:
        checks.append({"id": "D3", "status": "FAIL", "severity": "HARD",
                       "detail": "; ".join(m["fname"] + " status=" + m["fields"].get("status", "?") for m in d3_non_done),
                       "fix": "update status to DONE or resolve BLOCKED/PARTIAL"})
    else:
        checks.append({"id": "D3", "status": "PASS", "severity": "HARD", "detail": "all terminal receipts DONE"})

    if d4_failures:
        for f in d4_failures:
            checks.append({"id": "D4", "status": "FAIL", "severity": "HARD",
                           "detail": f"receipt {f['receipt']} claims {f['claimed_path']} — not on disk",
                           "artifact": f["claimed_path"],
                           "fix": "create file or remove claim from ## Files written"})
    else:
        checks.append({"id": "D4", "status": "PASS", "severity": "HARD",
                       "detail": "all claimed files exist"})

    if d5_d8_warns:
        checks.append({"id": "D5/D8", "status": "WARN", "severity": "SOFT",
                       "detail": "; ".join(d5_d8_warns[:3])})
    else:
        checks.append({"id": "D5/D8", "status": "PASS", "severity": "SOFT",
                       "detail": "no change-log/enforcement warnings"})

    checks.append({"id": "D6", "status": d6_status, "severity": "HARD" if d6_status == "FAIL" else "NA",
                   "verdict": d6_verdict, "detail": d6_detail})

    checks.append({"id": "D7", "status": d7_status, "severity": "HARD" if d7_status == "FAIL" else "NA",
                   "detail": d7_detail})

    hard_fails = sum(1 for c in checks if c.get("status") == "FAIL" and c.get("severity") == "HARD")
    soft_warns = sum(1 for c in checks if c.get("status") == "WARN")
    na_count = sum(1 for c in checks if c.get("status") == "NA")

    return {
        "change_id": change_id,
        "verdict": verdict,
        "checks": checks,
        "hard_fails": hard_fails,
        "soft_warns": soft_warns,
        "na": na_count,
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# change_id reaches an escaped regex in _check_d6 and list argv in the child
# validators, never a shell string. This bound is hygiene, not a sandbox: keep
# the value a plain token so a path fragment cannot ride in from frontmatter.
_CHANGE_ID_RE = re.compile(r"(?!-)(?!\.\.?$)[A-Za-z0-9._-]{1,200}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="verify-change.py — end-to-end DoD verifier for one change_id."
    )
    p.add_argument(
        "--change-id",
        required=True,
        metavar="CHANGE_ID",
        help="The change_id to verify (must match frontmatter of receipts).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit 1 on RED verdict (default: log-only, exit 0 even on RED).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_out",
        help="Output machine-readable JSON instead of plain-English report.",
    )
    p.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only load receipts on or after this date (load-time filter for speed).",
    )
    p.add_argument(
        "--report-out",
        metavar="PATH",
        default=None,
        help="Write JSON report to this path (in addition to stdout).",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    change_id = args.change_id.strip()
    if not _CHANGE_ID_RE.fullmatch(change_id):
        print(
            "ERROR: --change-id must be 1 to 200 characters drawn from "
            "[A-Za-z0-9._-], must not start with '-', and must not be '.' "
            f"or '..'; got {change_id!r}",
            file=sys.stderr,
        )
        return 2

    if not os.path.isdir(RECEIPTS):
        print(f"ERROR: receipts directory not found: {RECEIPTS}", file=sys.stderr)
        return 2

    since: Optional[date] = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 2

    # ---- D1: find members ---------------------------------------------------
    members = _find_members(change_id, since)
    if not members:
        msg = f"verify-change.py — change_id: {change_id}\nVERDICT: RED\n\n  [FAIL] D1 receipt exists: no receipt carries change_id={change_id!r}\n\nWHAT TO DO: No receipt found. Ensure at least one receipt has change_id: {change_id} in its frontmatter."
        if args.json_out:
            out = {"change_id": change_id, "verdict": "RED",
                   "checks": [{"id": "D1", "status": "FAIL", "severity": "HARD",
                               "detail": f"no receipt found for change_id={change_id}"}],
                   "hard_fails": 1, "soft_warns": 0, "na": 0}
            print(json.dumps(out, indent=2))
        else:
            print(msg)
        if args.strict:
            return 1
        return 0

    # ---- D2: schema ---------------------------------------------------------
    d2_failures = _check_d2(members)

    # ---- D3: terminal status ------------------------------------------------
    d3_non_done = _is_terminal_done(members)

    # ---- D4: phantom files --------------------------------------------------
    d4_failures, d4_notes = _check_d4(members)

    # ---- D5/D8: soft audit --------------------------------------------------
    d5_d8_warns = _check_d5_d8(members, since)

    # ---- D6: graph gates ----------------------------------------------------
    d6_status, d6_verdict, d6_detail = _check_d6(change_id, members, since)

    # ---- D7: removed in 0.4.0 -----------------------------------------------
    d7_status, d7_detail = _check_d7()

    # ---- Aggregate verdict --------------------------------------------------
    hard_fail = any([
        bool(d2_failures),
        bool(d3_non_done),
        bool(d4_failures),
        d6_status == "FAIL",
        d7_status == "FAIL",
    ])
    verdict = "RED" if hard_fail else "GREEN"

    # ---- Output -------------------------------------------------------------
    json_data = _build_json(
        change_id, len(members),
        d2_failures, d3_non_done, d4_failures, d4_notes,
        d5_d8_warns, d6_status, d6_verdict, d6_detail,
        d7_status, d7_detail, verdict,
    )

    if args.report_out:
        try:
            with open(args.report_out, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
        except OSError as e:
            print(f"WARNING: could not write --report-out {args.report_out}: {e}", file=sys.stderr)

    if args.json_out:
        print(json.dumps(json_data, indent=2))
    else:
        report = _format_report(
            change_id, len(members),
            d2_failures, d3_non_done, d4_failures, d4_notes,
            d5_d8_warns, d6_status, d6_verdict, d6_detail,
            d7_status, d7_detail, verdict,
        )
        print(report)

    # ---- Exit code ----------------------------------------------------------
    if args.strict and verdict == "RED":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
