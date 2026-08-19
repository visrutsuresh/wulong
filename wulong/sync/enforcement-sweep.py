#!/usr/bin/env python3
"""
enforcement-sweep.py — surface-only enforcement sweep (NON-BLOCKING, v1).

Runs 6 validators in sequence via subprocess, aggregates results, writes
Meta/doctor/enforcement-sweep-latest.md, and posts ONE warning line to
Meta/agent-messages.md ONLY if something failed.

Silent on all-clean (no false-alarm fatigue).

Validators run:
  1. cerebrum-health.py
  2. validate-surface-manifest.py
  3. validate-notebook-count.py
  4. validate-receipts.py --since <today>  (warn-only; strict lives in scheduled-strict-check.py)
  5. session-close-audit.py
  6. validate-receipt-graph.py --since <today> --warn-only

Hard total wall-clock budget: < 10s (per-validator timeout prevents a single
hang from stalling the sweep).
Timeout budget: 2+1+1+2+2+2 = 10s worst-case.

This script NEVER raises an unhandled exception (outer try/except + each
validator wrapped individually). It NEVER blocks or aborts a session.

Pure Python / stdlib only. No LLM, no network, no spend.

Usage:
  python3 Meta/sync/enforcement-sweep.py
  python3 Meta/sync/enforcement-sweep.py --verbose   # print stdout/stderr per validator
"""
from __future__ import annotations

import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from wulong._root import child_env, resolve_root

# Install-relative FLOOR only, reached when no root was handed down. This script
# runs as a child of an entry point, which passes the resolved root in the
# environment, so this tier fires only on direct manual invocation.
VAULT = Path(resolve_root(fallback=str(Path(__file__).resolve().parent.parent.parent),
                          tool="enforcement-sweep"))
SYNC = VAULT / "Meta" / "sync"
DOCTOR = VAULT / "Meta" / "doctor"
AGENT_MESSAGES = VAULT / "Meta" / "agent-messages.md"
REPORT_FILE = DOCTOR / "enforcement-sweep-latest.md"

# Per-validator timeouts sum to 10s worst-case (2+1+1+2+2+2).
# Measured runtimes: cerebrum-health ~0.03s, surface-manifest ~0.04s,
# notebook-count ~0.01s, validate-receipts ~0.03s, graph ~0.05s.
# Timeouts are hard safety caps, not expected durations.
_VALIDATORS: list[tuple[str, list[str], int]] = [
    (
        "cerebrum-health",
        ["python3", str(SYNC / "cerebrum-health.py")],
        2,
    ),
    (
        "validate-surface-manifest",
        ["python3", str(SYNC / "validate-surface-manifest.py")],
        1,
    ),
    (
        "validate-notebook-count",
        ["python3", str(SYNC / "validate-notebook-count.py")],
        1,
    ),
    (
        # warn-only: sweep is non-blocking-by-design (v1); strict pass lives in scheduled-strict-check.py (Rec 1)
        "validate-receipts (--since --warn-only)",
        ["python3", str(SYNC / "validate-receipts.py"), "--since",
         datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        2,
    ),
    (
        "session-close-audit",
        ["python3", str(SYNC / "session-close-audit.py")],
        2,
    ),
    (
        "validate-receipt-graph (--since --warn-only)",
        ["python3", str(SYNC / "validate-receipt-graph.py"), "--since",
         datetime.now(timezone.utc).strftime("%Y-%m-%d"), "--warn-only"],
        2,
    ),
]


class ValidatorResult(NamedTuple):
    name: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    error: str  # non-empty if subprocess call itself failed (timeout / FileNotFoundError)


def _run_validator(name: str, cmd: list[str], timeout: int) -> ValidatorResult:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(VAULT),
            # cwd alone is not enough: a validator that reads the environment
            # would take an inherited WULONG_ROOT over its own working directory.
            env=child_env(str(VAULT)),
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        passed = result.returncode == 0
        return ValidatorResult(
            name=name,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error="",
        )
    except subprocess.TimeoutExpired:
        return ValidatorResult(
            name=name,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr="",
            error=f"TIMEOUT after {timeout}s",
        )
    except FileNotFoundError:
        return ValidatorResult(
            name=name,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr="",
            error=f"Script not found: {cmd[1] if len(cmd) > 1 else cmd}",
        )
    except Exception:  # noqa: BLE001
        return ValidatorResult(
            name=name,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr="",
            error=traceback.format_exc(limit=3),
        )


def _write_report(results: list[ValidatorResult], ts: str, elapsed: float) -> None:
    any_fail = any(not r.passed for r in results)
    lines: list[str] = [
        f"# Enforcement Sweep — {ts}",
        "",
        f"Elapsed: {elapsed:.1f}s | Status: {'FAIL' if any_fail else 'PASS'}",
        "",
        "## Validator Results",
        "",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if r.error:
            detail = f"  ERROR: {r.error}"
        elif not r.passed:
            # Trim output to keep the report readable
            combined = (r.stdout + r.stderr).strip()
            detail = ("  " + "\n  ".join(combined.splitlines()[:20])) if combined else ""
        else:
            detail = ""
        lines.append(f"### {status}: {r.name}")
        if detail:
            lines.append(detail)
        lines.append("")

    DOCTOR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


def _post_failure_warning(results: list[ValidatorResult], ts: str) -> None:
    failed_names = [r.name for r in results if not r.passed]
    if not failed_names:
        return
    msg = (
        f"\n**[{ts}] enforcement-sweep → TO: Jarvis** ⚠️\n"
        f"Enforcement sweep FAILED. Validators: {', '.join(failed_names)}. "
        f"See `Meta/doctor/enforcement-sweep-latest.md` for details.\n"
    )
    try:
        with AGENT_MESSAGES.open("a", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        sys.stderr.write(f"[enforcement-sweep] could not write to agent-messages.md\n")


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    import time
    t0 = time.monotonic()

    results: list[ValidatorResult] = []
    for name, cmd, timeout in _VALIDATORS:
        r = _run_validator(name, cmd, timeout)
        results.append(r)
        if verbose:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {name} (exit={r.exit_code})")
            if r.error:
                print(f"    ERROR: {r.error}")
            elif not r.passed and (r.stdout or r.stderr):
                print((r.stdout + r.stderr).strip()[:400])

    elapsed = time.monotonic() - t0
    any_fail = any(not r.passed for r in results)

    _write_report(results, ts, elapsed)

    if any_fail:
        _post_failure_warning(results, ts)
        if verbose:
            print(f"\nSWEEP FAIL in {elapsed:.1f}s — report: {REPORT_FILE}")
    else:
        if verbose:
            print(f"\nSWEEP PASS in {elapsed:.1f}s — all clean.")

    # Never block/abort a session — always exit 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001 — outer safety net, must never propagate
        sys.stderr.write(f"[enforcement-sweep] unhandled error:\n{traceback.format_exc()}\n")
        sys.exit(0)  # still exit 0 — never block a session
