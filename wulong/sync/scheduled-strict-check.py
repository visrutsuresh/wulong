#!/usr/bin/env python3
"""
scheduled-strict-check.py — strict-mode observability reporter for harness-loop audit.

Runs THREE validators in --strict mode and captures their verdicts, surfacing
violations that are silently exit-0 in normal enforcement-sweep operation.
Writes a dated report to Meta/doctor/strict-enforcement-report-YYYY-MM-DD.md.

THIS SCRIPT ALWAYS EXITS 0. It is a reporter, never a session gate. It has no
authority to block, fail, or abort any session, pipeline, or deploy. The word
"strict" in the validator flags means the VALIDATORS report their real verdict
(instead of swallowing it), NOT that this runner blocks on findings.

Validators run:
  1. validate-receipt-graph.py --strict  (global, no --since filter)
  2. verify-change.py --strict           (per change_id, last 7 days)
  3. validate-receipts.py --strict

Pure Python / stdlib only. No LLM, no network, no spend.

Usage:
  python3 Meta/sync/scheduled-strict-check.py
  python3 Meta/sync/scheduled-strict-check.py --verbose
"""
from __future__ import annotations

import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

VAULT = Path(__file__).resolve().parent.parent.parent
SYNC = VAULT / "Meta" / "sync"
DOCTOR = VAULT / "Meta" / "doctor"
RECEIPTS_DIR = VAULT / "Meta" / "receipts"

LOOKBACK_DAYS = 7
# Per-run timeout (seconds). Graph validation is the heaviest — allow 10s.
_GRAPH_TIMEOUT = 10
_VERIFY_TIMEOUT = 8
_RECEIPTS_TIMEOUT = 6


class ValidatorResult(NamedTuple):
    label: str
    cmd: list[str]
    exit_code: int
    stdout: str
    stderr: str
    error: str


def _run(label: str, cmd: list[str], timeout: int) -> ValidatorResult:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(VAULT),
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        return ValidatorResult(
            label=label,
            cmd=cmd,
            exit_code=r.returncode,
            stdout=r.stdout,
            stderr=r.stderr,
            error="",
        )
    except subprocess.TimeoutExpired:
        return ValidatorResult(
            label=label, cmd=cmd, exit_code=-1, stdout="", stderr="",
            error=f"TIMEOUT after {timeout}s",
        )
    except FileNotFoundError:
        return ValidatorResult(
            label=label, cmd=cmd, exit_code=-1, stdout="", stderr="",
            error=f"Script not found: {cmd[1] if len(cmd) > 1 else cmd}",
        )
    except Exception:  # noqa: BLE001
        return ValidatorResult(
            label=label, cmd=cmd, exit_code=-1, stdout="", stderr="",
            error=traceback.format_exc(limit=3),
        )


def _discover_recent_change_ids(since: datetime) -> list[str]:
    """Scan Meta/receipts/ for change_ids on receipts modified within the lookback window."""
    since_date = since.date()
    seen: dict[str, None] = {}  # ordered-dedupe via insertion order
    for path in sorted(RECEIPTS_DIR.glob("*.md")):
        # Fast date filter from filename (YYYY-MM-DD is always in positions 1-3 of dash-split)
        parts = path.stem.split("-")
        if len(parts) < 4:
            continue
        try:
            file_date_str = "-".join(parts[1:4])  # agent-YYYY-MM-DD-...
            from datetime import date as _date
            file_date = _date.fromisoformat(file_date_str)
        except ValueError:
            continue
        if file_date < since_date:
            continue
        # Read frontmatter for change_id
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_frontmatter = False
        for line in text.splitlines():
            if line.strip() == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                else:
                    break  # end of frontmatter
            if in_frontmatter and line.startswith("change_id:"):
                cid = line.split(":", 1)[1].strip()
                if cid:
                    seen[cid] = None
    return list(seen.keys())


def _run_graph_strict() -> ValidatorResult:
    cmd = ["python3", str(SYNC / "validate-receipt-graph.py"), "--strict"]
    return _run("validate-receipt-graph (--strict, global)", cmd, _GRAPH_TIMEOUT)


def _run_verify_per_change_id(since: datetime, verbose: bool) -> list[ValidatorResult]:
    change_ids = _discover_recent_change_ids(since)
    if not change_ids:
        # Return a synthetic PASS-note so the report shows "0 change_ids found"
        return [ValidatorResult(
            label="verify-change (no change_ids found in lookback window)",
            cmd=[],
            exit_code=0,
            stdout=f"No change_ids found in receipts within last {LOOKBACK_DAYS} days.",
            stderr="",
            error="",
        )]
    results: list[ValidatorResult] = []
    since_str = since.strftime("%Y-%m-%d")
    for cid in change_ids:
        cmd = [
            "python3", str(SYNC / "verify-change.py"),
            "--change-id", cid,
            "--strict",
            "--since", since_str,
        ]
        r = _run(f"verify-change --change-id {cid} --strict", cmd, _VERIFY_TIMEOUT)
        results.append(r)
        if verbose:
            tag = "PASS" if r.exit_code == 0 else "RED"
            print(f"    [{tag}] {cid} (exit={r.exit_code})")
    return results


def _run_receipts_strict() -> ValidatorResult:
    cmd = ["python3", str(SYNC / "validate-receipts.py"), "--strict"]
    return _run("validate-receipts (--strict)", cmd, _RECEIPTS_TIMEOUT)


def _write_report(
    date_str: str,
    graph_result: ValidatorResult,
    verify_results: list[ValidatorResult],
    receipts_result: ValidatorResult,
    elapsed: float,
) -> Path:
    lines: list[str] = [
        f"# Strict Enforcement Report — {date_str}",
        "",
        (
            "> NOTE: This report surfaces violations that are silently exit-0 in normal "
            "enforcement-sweep operation. This script itself always exits 0. "
            "It is an observability reporter, not a session gate."
        ),
        "",
        f"Generated: {date_str} | Elapsed: {elapsed:.1f}s",
        "",
        "---",
        "",
        "## 1. validate-receipt-graph (--strict, global)",
        "",
        f"**Exit code:** {graph_result.exit_code}",
        "",
    ]
    if graph_result.error:
        lines.append(f"ERROR: {graph_result.error}")
    elif graph_result.exit_code != 0:
        combined = (graph_result.stdout + graph_result.stderr).strip()
        lines.append("**RED violations (would block under strict):**")
        lines.append("```")
        lines.extend(combined.splitlines()[:40])
        lines.append("```")
    else:
        lines.append("CLEAN — no violations found.")
    lines.append("")

    lines += [
        f"## 2. verify-change (--strict, last {LOOKBACK_DAYS} days)",
        "",
    ]
    red_count = sum(1 for r in verify_results if r.exit_code != 0)
    total = len(verify_results)
    lines.append(f"**change_ids checked:** {total} | **RED:** {red_count}")
    lines.append("")
    for r in verify_results:
        if r.error:
            lines.append(f"- {r.label}: ERROR — {r.error}")
        elif r.exit_code != 0:
            combined = (r.stdout + r.stderr).strip()
            short = combined[:200].replace("\n", " ")
            lines.append(f"- RED: {r.label}: {short}")
        else:
            lines.append(f"- PASS: {r.label}")
    lines.append("")

    lines += [
        "## 3. validate-receipts (--strict)",
        "",
        f"**Exit code:** {receipts_result.exit_code}",
        "",
    ]
    if receipts_result.error:
        lines.append(f"ERROR: {receipts_result.error}")
    elif receipts_result.exit_code != 0:
        combined = (receipts_result.stdout + receipts_result.stderr).strip()
        lines.append("**RED violations (would block under strict):**")
        lines.append("```")
        lines.extend(combined.splitlines()[:40])
        lines.append("```")
    else:
        lines.append("CLEAN — no violations found.")
    lines.append("")

    lines += [
        "---",
        "",
        "## Summary",
        "",
        f"| Validator | Exit code | Status |",
        f"|---|---|---|",
        f"| validate-receipt-graph --strict | {graph_result.exit_code} | {'CLEAN' if graph_result.exit_code == 0 else 'RED'} |",
        f"| verify-change --strict ({total} change_ids) | {red_count} RED | {'CLEAN' if red_count == 0 else f'{red_count}/{total} RED'} |",
        f"| validate-receipts --strict | {receipts_result.exit_code} | {'CLEAN' if receipts_result.exit_code == 0 else 'RED'} |",
        "",
        (
            "*RED here means 'would block under strict enforcement'. "
            "This script does not block anything.*"
        ),
    ]

    DOCTOR.mkdir(parents=True, exist_ok=True)
    out_path = DOCTOR / f"strict-enforcement-report-{date_str}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    since = now - timedelta(days=LOOKBACK_DAYS)

    import time
    t0 = time.monotonic()

    if verbose:
        print(f"[strict-check] Running 3 validators in strict mode...")
        print(f"  Lookback: {LOOKBACK_DAYS} days (since {since.date()})")

    graph_result = _run_graph_strict()
    if verbose:
        tag = "PASS" if graph_result.exit_code == 0 else "RED"
        print(f"  [1] validate-receipt-graph --strict: [{tag}] exit={graph_result.exit_code}")
        if graph_result.error:
            print(f"      ERROR: {graph_result.error}")

    if verbose:
        print(f"  [2] verify-change --strict per change_id (last {LOOKBACK_DAYS}d):")
    verify_results = _run_verify_per_change_id(since, verbose)

    receipts_result = _run_receipts_strict()
    if verbose:
        tag = "PASS" if receipts_result.exit_code == 0 else "RED"
        print(f"  [3] validate-receipts --strict: [{tag}] exit={receipts_result.exit_code}")
        if receipts_result.error:
            print(f"      ERROR: {receipts_result.error}")

    elapsed = time.monotonic() - t0
    out_path = _write_report(date_str, graph_result, verify_results, receipts_result, elapsed)

    if verbose:
        print(f"\nReport written: {out_path}")
        print(f"Elapsed: {elapsed:.1f}s")

    # ALWAYS exit 0 — this is a reporter, never a gate
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"[scheduled-strict-check] unhandled error:\n{traceback.format_exc()}\n")
        sys.exit(0)  # still exit 0 — reporter, not a gate
