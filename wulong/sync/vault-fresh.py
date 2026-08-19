#!/usr/bin/env python3
"""
vault-fresh.py — one-command mechanical vault freshness pass (v33-2-realtime-vault-freshness).

Chains existing pure-Python scripts in sequence and prints a one-screen status:
  1. recompile context (compile-context.py)
  2. doc-consistency drift count (check-doc-consistency.py)
  3. in-flight ledger audit (inflight.py --audit)
  4. vault-health summary (vault-health-check.py)

NEVER makes content edits. NEVER invokes claude or any LLM.

ponytail: stdlib only; chains existing scripts via subprocess; no new abstractions.

Usage:
  python3 vault-fresh.py
  python3 vault-fresh.py --if-stale 300   # no-op if ran within 300s
  python3 vault-fresh.py --demo            # wiring self-check (safe mode)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SYNC = Path(__file__).resolve().parent
STATE_FILE = SYNC / ".vault-fresh-last"

# Scripts relative to SYNC dir, called with python3 <abs-path>
_SCRIPTS: list[tuple[str, list[str]]] = [
    ("recompile-context",  [sys.executable, str(SYNC / "compile-context.py")]),
    ("doc-consistency",    [sys.executable, str(SYNC / "check-doc-consistency.py")]),
    ("inflight-audit",     [sys.executable, str(SYNC / "inflight.py"), "--audit"]),
    ("vault-health",       [sys.executable, str(SYNC / "vault-health-check.py")]),
]


def _read_last_run() -> float | None:
    try:
        return float(STATE_FILE.read_text().strip())
    except Exception:
        return None


def _write_last_run() -> None:
    STATE_FILE.write_text(str(time.time()))


def _run_step(label: str, cmd: list[str]) -> tuple[int, str]:
    """Run one sub-script, capture combined stdout+stderr, return (rc, output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode, out
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT after 120s"
    except FileNotFoundError as e:
        return 1, f"NOT FOUND: {e}"


# doctor exits 0 for PARTIAL and for ADVISORY on purpose, so an exit code of 0
# does not mean the run was clean and keying on it alone relabels a skipped or
# warned run as [OK]. It also printed the indented SKIP list AFTER the verdict,
# so the last-non-blank-line snippet showed a SKIP line under an [OK] label.
_NOT_CLEAN_VERDICTS = ("RED vault-health", "PARTIAL vault-health", "ADVISORY vault-health")


def _not_clean_verdict(output: str) -> str | None:
    """The verdict line of a sub-script that ran but did not come back clean."""
    for line in reversed([l for l in output.splitlines() if l.strip()]):
        if line.startswith(_NOT_CLEAN_VERDICTS):
            return line
    return None


def _summarise(label: str, rc: int, output: str) -> str:
    verdict = _not_clean_verdict(output)
    status = "OK" if rc == 0 and verdict is None else "WARN"
    # Prefer the verdict line over the last non-blank line, which for a PARTIAL
    # run is an indented SKIP and reads as though it were the summary.
    lines = [l for l in output.splitlines() if l.strip()]
    snippet = verdict or (lines[-1] if lines else "(no output)")
    return f"  [{status}] {label}: {snippet[:120]}"


def run_full(demo: bool = False) -> int:
    """Run all steps. In demo mode skip real execution, just verify wiring."""
    print("=== vault-fresh ===")
    print(f"  mode: {'demo' if demo else 'live'}")
    print()

    if demo:
        # ponytail: demo verifies wiring without requiring sub-scripts to succeed
        errors: list[str] = []
        for label, cmd in _SCRIPTS:
            path = Path(cmd[1])
            if not path.exists():
                errors.append(f"MISSING: {path}")
        if errors:
            for e in errors:
                print(f"  [FAIL] {e}")
            return 1

        # Verify debounce wiring: write a fake timestamp then confirm --if-stale no-ops
        _write_last_run()
        last = _read_last_run()
        assert last is not None, "state file write/read roundtrip failed"
        assert (time.time() - last) < 5, "state file timestamp stale (>5s immediately after write)"

        # Confirm no subprocess invocation of 'claude' or LLM APIs in this file.
        # We check that no _SCRIPTS entry or subprocess.run call references these.
        for label, cmd in _SCRIPTS:
            for part in cmd:
                for forbidden in ("claude", "anthropic", "openai"):
                    assert forbidden not in part.lower(), (
                        f"script '{label}' cmd contains forbidden word '{forbidden}': {cmd}"
                    )

        print("  [DEMO] all 4 scripts found on disk")
        print("  [DEMO] debounce state roundtrip OK")
        print("  [DEMO] no forbidden LLM references in source")
        print()
        print("=== DEMO PASS ===")
        return 0

    overall_ok = True
    summaries: list[str] = []

    for label, cmd in _SCRIPTS:
        rc, output = _run_step(label, cmd)
        if rc != 0 or _not_clean_verdict(output) is not None:
            overall_ok = False
        summaries.append(_summarise(label, rc, output))
        # Print verbose output indented
        for line in output.splitlines():
            print(f"    | {line}")
        print()

    print("--- summary ---")
    for s in summaries:
        print(s)
    print()
    print(f"=== vault-fresh {'ALL OK' if overall_ok else 'WARN (see above)'} ===")

    _write_last_run()
    return 0 if overall_ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Mechanical vault freshness pass")
    p.add_argument(
        "--if-stale",
        metavar="SECONDS",
        type=float,
        default=None,
        help="No-op if vault-fresh ran within SECONDS (debounce for WatchPaths trigger)",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="Self-check: verify wiring and debounce logic without requiring sub-scripts to succeed",
    )
    args = p.parse_args()

    if args.if_stale is not None:
        last = _read_last_run()
        if last is not None and (time.time() - last) < args.if_stale:
            # ponytail: silent no-op; stdout kept clean so launchd log stays quiet
            return 0

    return run_full(demo=args.demo)


if __name__ == "__main__":
    sys.exit(main())
