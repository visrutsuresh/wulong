#!/usr/bin/env python3
"""
session-pulse.py — Single session-close entry point.

Extends the bare verify-change.py call that lives in jarvis.md step-0.
Delegates D1-D4 gate to verify-change.py (exit semantics preserved),
then adds doc-consistency delta check, audit summary, and compliance check.

Usage:
  python3 Meta/sync/session-pulse.py --change-id <id> [--change-id <id2> ...]
                                     [--root PATH] [--strict]
                                     [--no-exit-nonzero-on-red]

Exit codes:
  0  All gates clear, or RED with --no-exit-nonzero-on-red.
  1  RED verdict: verify-change RED, new doc drift, or a new block-severity
     compliance violation. This is the DEFAULT now. --exit-nonzero-on-red is on
     unless you turn it off, because a session-close gate that always exits 0 is
     not a gate. --strict is unchanged and still governs what counts as failure
     and how it is labelled, not just the exit code.
  2  Usage or infrastructure error.

Root resolution: --root, then WULONG_ROOT, then a marker above the working
directory. The resolved root is handed to EVERY child process, both as a flag
where the child has one and in the child environment, so the parent and its four
children can never audit two different vaults.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import pathlib
from pathlib import Path

from wulong._root import ENV_VAR, RootNotFound, child_env, resolve_root

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
# No install-relative vault constant lives here on purpose. This is an ENTRY
# POINT, and in a wheel the install-relative path is site-packages, so guessing
# it is the wrong-vault bug itself. The root is resolved in main() and handed to
# every child, because this script fans out to four of them and used to pass the
# root to exactly one.

VERIFY_CHANGE    = SCRIPT_DIR / "verify-change.py"
CHECK_DOC        = SCRIPT_DIR / "check-doc-consistency.py"
SESSION_AUDIT    = SCRIPT_DIR / "session-close-audit.py"
CHECK_COMPLIANCE = SCRIPT_DIR / "check-compliance.py"
# NOT SCRIPT_DIR. The four constants above are CODE, shipped with the package
# and correctly resolved next to this file. The baseline is per-vault DATA, and
# reading it from the install directory meant `pulse --root B` compared vault B's
# drift against whatever baseline happened to sit beside the installed engine.
# Set from the resolved root in main().
BASELINE_FILE    = SCRIPT_DIR / "doc-consistency-baseline.json"


def _baseline_path(root: str) -> pathlib.Path:
    return pathlib.Path(root) / "Meta" / "sync" / "doc-consistency-baseline.json"

# Machine-readable, strict-free compliance verdict emitted by check-compliance.py.
COMPLIANCE_VERDICT_PREFIX = "COMPLIANCE-VERDICT:"


# ---------------------------------------------------------------------------
# Colour helpers (stripped when not a tty)
# ---------------------------------------------------------------------------

def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

RED    = lambda t: _c("31;1", t)
YELLOW = lambda t: _c("33;1", t)
GREEN  = lambda t: _c("32;1", t)
BOLD   = lambda t: _c("1",    t)
DIM    = lambda t: _c("2",    t)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Session-close pulse: verify-change + doc-consistency + audit"
    )
    p.add_argument(
        "--change-id",
        action="append",
        dest="change_ids",
        metavar="ID",
        help="change_id to verify (repeatable)",
    )
    p.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help=f"Vault root. Wins over the {ENV_VAR} env var. Handed to every child.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat RED as a HARD-BLOCK: changes what counts as failure in the "
             "children and how it is labelled, not only the exit code",
    )
    p.add_argument(
        "--exit-nonzero-on-red",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exit 1 when the pulse verdict is ACTION REQUIRED (default: on). "
             "Governs ONLY the exit code. Use --no-exit-nonzero-on-red for the "
             "old log-only behaviour.",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Step 1: verify-change.py delegation
# ---------------------------------------------------------------------------

def run_verify_change(change_ids: list[str], strict: bool, root: str) -> tuple[bool, list[dict]]:
    """
    Run verify-change.py for each change_id.
    Returns (all_green: bool, results: list of {id, rc, output} dicts).
    Preserves exit semantics: --strict propagated → non-zero if RED.
    """
    results = []
    all_ok = True

    for cid in change_ids:
        cmd = [sys.executable, str(VERIFY_CHANGE), "--change-id", cid]
        if strict:
            cmd.append("--strict")

        proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env(root))
        output = proc.stdout + proc.stderr
        rc = proc.returncode

        # Determine verdict label from output
        if "GREEN" in output:
            verdict = "GREEN"
        elif "RED" in output:
            verdict = "RED"
            if strict:
                all_ok = False
        else:
            verdict = "UNKNOWN"
            if strict:
                all_ok = False

        results.append({"id": cid, "rc": rc, "verdict": verdict, "output": output.strip()})

    return all_ok, results


# ---------------------------------------------------------------------------
# Step 2: doc-consistency delta
# ---------------------------------------------------------------------------

def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[^m]*m", "", text)


def _composite_key(check_type: str, doc_basename: str, snippet: str) -> str:
    """
    Build a stable composite key EXCLUDING line numbers.
    Key: (check_type, doc_basename, normalised-snippet-text).
    """
    norm = re.sub(r"\s+", " ", snippet).strip().lower()
    return f"{check_type}||{doc_basename}||{norm}"


def _parse_disagrees(output: str) -> list[str]:
    """
    Parse DISAGREE entries from check-doc-consistency.py text output.
    Returns a list of composite keys (line-number-free).
    """
    lines = output.splitlines()
    keys = []
    current_doc: str | None = None
    i = 0

    while i < len(lines):
        raw = lines[i]
        clean = _strip_ansi(raw)

        # Detect doc header: non-indented, non-separator, non-summary line
        if (not clean.startswith(" ")
                and clean.strip()
                and not clean.startswith("─")
                and not clean.startswith("=")
                and not clean.startswith("-")
                and "RESULT:" not in clean
                and "Summary" not in clean
                and "Results" not in clean
                and "Checking" not in clean
                and "Canonical" not in clean
                and "Vault" not in clean
                and "Parsed" not in clean
                and "===" not in clean
                and "PASS" not in clean
                and "WARN" not in clean):
            if i + 1 < len(lines):
                next_clean = _strip_ansi(lines[i + 1])
                if next_clean.startswith("─"):
                    current_doc = clean.strip()

        # DISAGREE line
        if "DISAGREE" in clean:
            m = re.match(r"\s+DISAGREE\s+\[([^\]]+)\]", clean)
            if m:
                check_type = m.group(1).strip()
                snippet = ""
                if i + 1 < len(lines):
                    next_clean = _strip_ansi(lines[i + 1])
                    snippet = next_clean.strip()
                    i += 1
                doc = current_doc or "unknown"
                keys.append(_composite_key(check_type, doc, snippet))

        i += 1

    return keys


def _load_baseline() -> tuple[set[str], int]:
    """
    Load doc-consistency-baseline.json.
    Returns (baseline_keys: set, declared_count: int).
    """
    if not BASELINE_FILE.exists():
        print(YELLOW("WARN") + f": baseline file not found at {BASELINE_FILE}")
        print("         Run _gen_baseline.py to create it. Treating entire backlog as NEW drift.")
        return set(), 0

    with open(BASELINE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    return set(data.get("keys", [])), data.get("count", 0)


def run_doc_consistency(strict: bool, root: str) -> tuple[bool, dict]:
    """
    Run check-doc-consistency.py, compute delta vs baseline.
    Returns (ok: bool, info: dict with counts + new_keys list).
    """
    proc = subprocess.run(
        [sys.executable, str(CHECK_DOC)],
        capture_output=True, text=True, env=child_env(root)
    )
    output = proc.stdout + proc.stderr

    current_keys = _parse_disagrees(output)
    baseline_keys, baseline_count = _load_baseline()

    current_set = set(current_keys)
    delta = current_set - baseline_keys  # genuinely NEW disagreements not in baseline

    ok = len(delta) == 0 or not strict

    return ok, {
        "current_count": len(current_keys),
        "baseline_count": baseline_count,
        "delta": sorted(delta),
        "raw_output": output.strip(),
    }


# ---------------------------------------------------------------------------
# Step 3: session-close-audit
# ---------------------------------------------------------------------------

def run_session_audit(root: str) -> str:
    """Run session-close-audit.py --dry-run and return its summary line.

    The root arrives from main(), which resolved it once. It goes down both as
    --root and in the environment: the flag because this child has one and a flag
    beats the environment, the environment because everything this child spawns
    in turn must land on the same vault.
    """
    proc = subprocess.run(
        [sys.executable, str(SESSION_AUDIT), "--dry-run", "--root", str(root)],
        capture_output=True, text=True, env=child_env(root)
    )
    output = (proc.stdout + proc.stderr).strip()
    # Return last non-empty line as the summary
    lines = [l for l in output.splitlines() if l.strip()]
    return lines[-1] if lines else "(no output)"


# ---------------------------------------------------------------------------
# Step 4: compliance (registry-driven, baseline-gated)
# ---------------------------------------------------------------------------

def run_compliance(change_ids: list[str], strict: bool, root: str) -> tuple[bool, str, list[str]]:
    """
    Run check-compliance.py for each change_id.
    Returns (ok: bool, summary: str, output_lines: list[str]).
    ok=True means no NEW block-severity violations and no block-set drift.
    Reuses the sweep report that enforcement-sweep.py already wrote
    (check-compliance.py runs the sweep internally; no double-invocation risk).
    """
    if not CHECK_COMPLIANCE.exists():
        return True, "(check-compliance.py not found — skipping)", []

    cmd = [sys.executable, str(CHECK_COMPLIANCE)]
    for cid in change_ids:
        cmd += ["--change-id", cid]
    if strict:
        cmd.append("--strict")

    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env(root))
    output = proc.stdout + proc.stderr
    rc = proc.returncode
    lines = [l for l in output.splitlines() if l.strip()]

    # rc is STRICT-TAINTED: check-compliance.py returns 1 for a new block
    # violation only under --strict, so `rc == 0` silently means "clean" in the
    # default mode even when there IS a new violation. The verdict line below is
    # printed unconditionally by check-compliance.py for exactly this reason.
    ok = (rc == 0)
    for ln in lines:
        clean = _strip_ansi(ln).strip()
        if clean.startswith(COMPLIANCE_VERDICT_PREFIX):
            ok = clean.split(":", 1)[1].strip().startswith("GREEN")
            break

    # Build a one-line summary from the output
    if ok:
        # Find the GREEN line or fingerprint OK line
        for ln in lines:
            clean = _strip_ansi(ln)
            if "GREEN" in clean or "fingerprint OK" in clean or "no new" in clean.lower():
                summary = clean.strip()
                break
        else:
            summary = f"PASS (exit 0)"
    else:
        # Find the actionable line
        for ln in lines:
            clean = _strip_ansi(ln)
            if "BLOCK-SET DRIFT" in clean or "BLOCK-SEVERITY" in clean or "HARD-BLOCK" in clean:
                summary = clean.strip()
                break
        else:
            summary = f"FAIL (exit {rc})"

    return ok, summary, lines


# ---------------------------------------------------------------------------
# COMPANY PULSE printer
# ---------------------------------------------------------------------------

def print_pulse(
    change_results: list[dict],
    doc_info: dict,
    audit_summary: str,
    compliance_ok: bool,
    compliance_summary: str,
    compliance_lines: list[str],
    strict: bool,
) -> bool:
    """Print the one-screen COMPANY PULSE summary and RETURN the verdict.

    Returning it is the point. This function computes the only strict-free
    verdict in the file, and while it was declared `-> None` main() could not
    reach it, so main() keyed off the shadowed strict-tainted flags instead and
    the printed verdict and the exit code could disagree.
    """
    width = 72
    bar = "─" * width

    print()
    print(BOLD("╔" + "═" * (width - 2) + "╗"))
    print(BOLD("║") + BOLD("  COMPANY PULSE — session close").center(width - 2) + BOLD("║"))
    print(BOLD("╚" + "═" * (width - 2) + "╝"))
    print()

    # ── 1. verify-change verdicts ──────────────────────────────────────────
    print(BOLD("1. verify-change (D1–D4 gate)"))
    print(bar)
    for r in change_results:
        if r["verdict"] == "GREEN":
            label = GREEN("GREEN")
        elif r["verdict"] == "RED":
            # The old label here said log-only. Since 0.4.0 a RED fails the
            # pulse and the process exits 1 unless --no-exit-nonzero-on-red is
            # passed, so that label described a default that no longer exists.
            label = RED("RED") + (" [HARD-BLOCK]" if strict else " [ACTION REQUIRED]")
        else:
            label = YELLOW("UNKNOWN")
        print(f"  change-id: {BOLD(r['id'])}")
        print(f"  verdict:   {label}  (verify-change exit {r['rc']})")
        # Surface first 6 lines of verify output for context
        for line in r["output"].splitlines()[:6]:
            clean = _strip_ansi(line)
            if clean.strip():
                print(f"  {DIM(clean)}")
        print()

    # ── 2. doc-consistency delta ────────────────────────────────────────────
    print(BOLD("2. doc-consistency delta"))
    print(bar)
    delta = doc_info["delta"]
    baseline_count = doc_info["baseline_count"]
    current_count = doc_info["current_count"]

    if not delta:
        print(f"  {GREEN('GREEN')} — no new drift this session")
        print(f"  {DIM(f'known-backlog: {baseline_count}  (Phase-3-owned — not an alarm)')}")
    else:
        print(f"  {RED('RED')} — {len(delta)} NEW disagreement(s) not in baseline:")
        for key in delta:
            print(f"    • {key}")
        if strict:
            print(f"  {RED('[HARD-BLOCK under --strict]')}")
        else:
            # True in BOTH exit modes, which it has to be: the two modes print
            # byte-identical output, so this text must describe the RULE and the
            # rule's default, not whichever mode happens to be running.
            print(f"  {YELLOW('[not a hard block. Exits 1 unless --no-exit-nonzero-on-red]')}")
    print(f"  {DIM(f'(current: {current_count}  baseline: {baseline_count})')}")
    print()

    # ── 3. audit summary ────────────────────────────────────────────────────
    print(BOLD("3. session-close-audit"))
    print(bar)
    print(f"  {DIM(audit_summary)}")
    print()

    # ── 4. compliance (registry-driven, baseline-gated) ─────────────────────
    print(BOLD("4. compliance (registry-driven)"))
    print(bar)
    for ln in compliance_lines:
        clean = _strip_ansi(ln)
        if not clean.strip():
            continue
        # Colour-neutral re-print for tty; skip separator lines from check-compliance
        if "──" in clean:
            continue
        if "BLOCK-SET DRIFT" in clean or ("HARD-BLOCK" in clean and not compliance_ok):
            print(f"  {RED(clean)}")
        elif "GREEN" in clean or "fingerprint OK" in clean or "PASS" in clean:
            print(f"  {GREEN(clean)}")
        elif "WARN" in clean or "advisory" in clean.lower() or "known-backlog" in clean.lower():
            print(f"  {DIM(clean)}")
        else:
            print(f"  {clean}")
    if not compliance_lines:
        print(f"  {DIM(compliance_summary)}")
    print()

    # ── overall ─────────────────────────────────────────────────────────────
    print(bar)
    vc_ok = all(r["verdict"] == "GREEN" for r in change_results)
    doc_ok = not delta
    all_clear = vc_ok and doc_ok and compliance_ok
    if all_clear:
        print(BOLD(GREEN("  PULSE: ALL CLEAR")) + "  — gate chain satisfied, no new drift, compliance GREEN")
    else:
        issues = []
        if not vc_ok:
            issues.append("verify-change RED")
        if not doc_ok:
            issues.append(f"doc drift ({len(delta)} new)")
        if not compliance_ok:
            issues.append("compliance block violation or drift")
        print(BOLD(RED("  PULSE: ACTION REQUIRED")) + f"  — {', '.join(issues)}")
    print(bar)
    print()
    return all_clear


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    change_ids = args.change_ids or []
    strict = args.strict

    try:
        root = resolve_root(args.root, tool="wulong pulse")
    except RootNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    global BASELINE_FILE
    BASELINE_FILE = _baseline_path(root)

    if not change_ids:
        print(RED("ERROR") + ": at least one --change-id required")
        return 2

    # Validate required scripts exist
    for script in (VERIFY_CHANGE, CHECK_DOC, SESSION_AUDIT):
        if not script.exists():
            print(RED("ERROR") + f": required script not found: {script}")
            return 2

    print(BOLD("=== session-pulse: starting close checks ==="))
    print(f"change-ids: {', '.join(change_ids)}")
    print(f"root:       {root}")
    print(f"strict:     {strict}")
    print()

    # Step 1: verify-change (D1-D4)
    # NAMED _strict: these two are the strict-TAINTED returns. They are correct
    # for the --strict exit branch and wrong for anything else, and the old
    # unsuffixed names shadowed the strict-free verdict computed in print_pulse.
    vc_ok_strict, vc_results = run_verify_change(change_ids, strict, root)

    # Step 2: doc-consistency delta
    doc_ok_strict, doc_info = run_doc_consistency(strict, root)

    # Step 3: audit
    # Note: session-close-audit.py runs inside enforcement-sweep.py (called by check-compliance.py).
    # We call it here directly so section 3 has a standalone summary; check-compliance reuses
    # the sweep report rather than re-invoking session-close-audit independently.
    audit_summary = run_session_audit(root)

    # Step 4: compliance (registry-driven, baseline-gated)
    compliance_ok, compliance_summary, compliance_lines = run_compliance(change_ids, strict, root)

    # Print pulse, and take the verdict it computed rather than recomputing it
    all_clear = print_pulse(
        vc_results, doc_info, audit_summary, compliance_ok,
        compliance_summary, compliance_lines, strict,
    )

    # --strict is unchanged: same inputs, same branch, same meaning.
    if strict and (not vc_ok_strict or not doc_ok_strict or not compliance_ok):
        return 1

    # The default. A RED verdict exits non-zero unless explicitly told not to.
    if args.exit_nonzero_on_red and not all_clear:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
