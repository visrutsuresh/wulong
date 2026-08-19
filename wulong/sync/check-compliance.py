#!/usr/bin/env python3
"""
check-compliance.py — Registry-driven compliance enforcement layer.

RESPONSIBILITY SPLIT (verbatim — do not change without updating rule-registry.yaml):
  enforcement-sweep.py  = broad always-exits-0 runner; owned by doctor;
                          surfaces all validator FAILs; NEVER modified here.
  check-compliance.py   = registry-driven severity+baseline-delta layer that
                          CAN exit non-zero under --strict on NEW block-severity
                          violations. It calls enforcement-sweep.py as a
                          subprocess and reads its latest report; it does NOT
                          replace or subsume enforcement-sweep.py.

How it works:
  1. Reads Meta/compliance/rule-registry.yaml to get severity per rule.
  2. Calls enforcement-sweep.py as a subprocess (to refresh the latest report),
     then reads Meta/doctor/enforcement-sweep-latest.md. Maps each FAIL
     validator to its registry rule_id via the validator_name field.
  3. MECH-003 has validator_name: null — checked by calling verify-change.py
     directly for each --change-id supplied.
  4. Applies BASELINE-DELTA: baseline = Meta/compliance/enforcement-baseline.json,
     keyed {rule_id}:{validator_name}:{composite_key}. NEW violations = current
     FAILs not in baseline. severity:warn rules NEVER block (advisory only).
  5. Checks the BLOCK-SET FINGERPRINT: SHA-256 of sorted block rule_id list vs
     Meta/compliance/block-set.lock. Mismatch → [BLOCK-SET DRIFT] + exit non-zero.

Usage:
  python3 Meta/sync/check-compliance.py [--change-id <id>] [--strict] [--update-lock]

  --change-id   change_id to pass to verify-change.py for MECH-003 (repeatable)
  --strict      exit non-zero on NEW block-severity violations or block-set drift
  --update-lock rewrite block-set.lock (REQUIRES --change-id; rejected without it)

Exit codes:
  0  — No new block-severity violations (known backlog shown DIM, not an alarm).
  1  — Under --strict: new block-severity violation found, OR block-set drift.
  2  — Usage / infrastructure error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from wulong._root import child_env, resolve_root

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
# Install-relative FLOOR only, reached when no root was handed down. This script
# runs as a child of an entry point, which passes the resolved root in the
# environment, so this tier fires only on direct manual invocation.
VAULT = Path(resolve_root(fallback=str(SCRIPT_DIR.parent.parent), tool="check-compliance"))
META_DIR = VAULT / "Meta"

RULE_REGISTRY   = META_DIR / "compliance" / "rule-registry.yaml"
BASELINE_FILE   = META_DIR / "compliance" / "enforcement-baseline.json"
BLOCK_SET_LOCK  = META_DIR / "compliance" / "block-set.lock"
SWEEP_REPORT    = META_DIR / "doctor" / "enforcement-sweep-latest.md"

ENFORCEMENT_SWEEP = SCRIPT_DIR / "enforcement-sweep.py"
VERIFY_CHANGE     = SCRIPT_DIR / "verify-change.py"

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
        description="Registry-driven compliance enforcement layer"
    )
    p.add_argument(
        "--change-id",
        action="append",
        dest="change_ids",
        metavar="ID",
        default=[],
        help="change_id to verify via verify-change.py for MECH-003 (repeatable)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on NEW block-severity violations or block-set drift",
    )
    p.add_argument(
        "--update-lock",
        action="store_true",
        help="Rewrite block-set.lock (REQUIRES --change-id; rejected without it)",
    )
    return p.parse_args()

# ---------------------------------------------------------------------------
# Minimal YAML parser (stdlib only — parses only what rule-registry.yaml uses)
# ---------------------------------------------------------------------------

def _parse_registry(path: Path) -> list[dict]:
    """
    Parse rule-registry.yaml into a list of rule dicts.
    Only handles the structure we wrote — no full YAML parser needed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Rule registry not found: {path}")

    text = path.read_text(encoding="utf-8")
    rules: list[dict] = []
    in_rules = False
    current: dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if line.strip() == "rules:":
            in_rules = True
            continue

        if not in_rules:
            continue

        # New rule block
        if re.match(r"^  - id:", line):
            if current is not None:
                rules.append(current)
            current = {}
            current["id"] = line.split(":", 1)[1].strip()
            continue

        if current is None:
            continue

        # Multi-line block scalar (>-) — just grab the value prefix
        m = re.match(r"^    (\w+):\s*(.+)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val in (">-", "|", ">", "|2"):
                current[key] = ""
            elif val.lower() == "null":
                current[key] = None
            else:
                current[key] = val
            continue

        # Continuation of a block scalar (indent 6+)
        m2 = re.match(r"^      (.+)$", line)
        if m2 and current:
            # Append to the last set key (find which one was set last)
            # We only need validator_name and severity precisely, so skip multi-line appending
            pass

    if current is not None:
        rules.append(current)

    return rules

# ---------------------------------------------------------------------------
# Load baseline
# ---------------------------------------------------------------------------

def _load_baseline() -> tuple[dict[str, str], int]:
    """
    Returns (baseline_entries: {composite_key: rule_id}, total_count).
    composite_key = "{rule_id}:{validator_name}:{stable_key}"
    """
    if not BASELINE_FILE.exists():
        return {}, 0
    with open(BASELINE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", {})
    count = data.get("count", len(entries))
    return entries, count

# ---------------------------------------------------------------------------
# Parse enforcement-sweep-latest.md
# ---------------------------------------------------------------------------

def _parse_sweep_report(path: Path) -> list[str]:
    """
    Parse the sweep report to return a list of FAIL validator names.
    Format: "### FAIL: <name>"
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    fails: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^### FAIL:\s+(.+)$", line.strip())
        if m:
            fails.append(m.group(1).strip())
    return fails

# ---------------------------------------------------------------------------
# Composite key (stable — excludes line numbers, matches session-pulse pattern)
# ---------------------------------------------------------------------------

def _composite_key(rule_id: str, validator_name: str, detail: str) -> str:
    """
    Build a stable composite key EXCLUDING line numbers.
    Matches the proven pattern from session-pulse.py:126-132.
    Key: {rule_id}:{validator_name}:{normalised-detail}
    """
    norm = re.sub(r"\s+", " ", detail).strip().lower()
    # Strip any leading line-number patterns (e.g. "line 42: ") — exclude from key
    norm = re.sub(r"\bline\s+\d+[:\s]", "", norm).strip()
    return f"{rule_id}:{validator_name}:{norm}"

# ---------------------------------------------------------------------------
# Run enforcement sweep (refresh the report)
# ---------------------------------------------------------------------------

def _run_sweep() -> bool:
    """Run enforcement-sweep.py as subprocess. Always exits 0 itself."""
    if not ENFORCEMENT_SWEEP.exists():
        print(YELLOW("WARN") + f": enforcement-sweep.py not found at {ENFORCEMENT_SWEEP}")
        return False
    proc = subprocess.run(
        [sys.executable, str(ENFORCEMENT_SWEEP)],
        env=child_env(str(VAULT)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    # enforcement-sweep always exits 0; if somehow it didn't, log it
    if proc.returncode != 0:
        print(YELLOW("WARN") + f": enforcement-sweep exited {proc.returncode} (unusual)")
    return True

# ---------------------------------------------------------------------------
# Run verify-change for MECH-003
# ---------------------------------------------------------------------------

def _run_verify_change(change_ids: list[str]) -> tuple[bool, list[str]]:
    """
    Run verify-change.py for each change_id.
    Returns (all_green: bool, issues: list of problem descriptions).
    """
    if not VERIFY_CHANGE.exists():
        return False, [f"verify-change.py not found at {VERIFY_CHANGE}"]
    if not change_ids:
        return True, []

    issues: list[str] = []
    all_green = True

    for cid in change_ids:
        proc = subprocess.run(
            [sys.executable, str(VERIFY_CHANGE), "--change-id", cid],
            capture_output=True,
            text=True,
            timeout=30,
            env=child_env(str(VAULT)),
        )
        output = proc.stdout + proc.stderr
        if "RED" in output or proc.returncode != 0:
            all_green = False
            issues.append(f"change_id={cid}: RED verdict")
        elif "GREEN" not in output:
            all_green = False
            issues.append(f"change_id={cid}: UNKNOWN verdict")

    return all_green, issues

# ---------------------------------------------------------------------------
# Block-set fingerprint
# ---------------------------------------------------------------------------

def _block_set_hash(block_rule_ids: list[str]) -> str:
    """SHA-256 of newline-joined sorted block rule_id list."""
    payload = "\n".join(sorted(block_rule_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def _load_lock() -> dict | None:
    """Load block-set.lock. Returns None if absent."""
    if not BLOCK_SET_LOCK.exists():
        return None
    try:
        return json.loads(BLOCK_SET_LOCK.read_text(encoding="utf-8"))
    except Exception:
        return None

def _write_lock(block_rule_ids: list[str], change_id: str) -> None:
    """Write block-set.lock."""
    BLOCK_SET_LOCK.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hash": _block_set_hash(block_rule_ids),
        "block_rule_ids": sorted(block_rule_ids),
        "updated_by_change_id": change_id,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    BLOCK_SET_LOCK.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

def _check_block_set(block_rule_ids: list[str], update_lock: bool, change_ids: list[str]) -> tuple[bool, str]:
    """
    Check (or update) the block-set fingerprint.
    Returns (drift_found: bool, message: str).
    """
    current_hash = _block_set_hash(block_rule_ids)
    lock = _load_lock()

    if update_lock:
        # Requires --change-id (enforced in main)
        cid = change_ids[0] if change_ids else "UNKNOWN"
        _write_lock(block_rule_ids, cid)
        return False, f"block-set.lock updated (hash={current_hash[:12]}…, change_id={cid})"

    if lock is None:
        # Bootstrap: write and continue
        _write_lock(block_rule_ids, "bootstrap")
        return False, f"block-set.lock bootstrapped (hash={current_hash[:12]}…)"

    locked_hash = lock.get("hash", "")
    locked_ids = set(lock.get("block_rule_ids", []))
    current_ids = set(block_rule_ids)

    if current_hash == locked_hash:
        return False, f"fingerprint OK (hash={current_hash[:12]}…)"

    added   = sorted(current_ids - locked_ids)
    removed = sorted(locked_ids - current_ids)
    parts = []
    if added:
        parts.append(f"added={added}")
    if removed:
        parts.append(f"removed={removed}")
    drift_msg = "[BLOCK-SET DRIFT] " + "; ".join(parts)
    drift_msg += f"\n  current_hash={current_hash[:12]}… locked_hash={locked_hash[:12]}…"
    drift_msg += "\n  To resolve: edit registry + run check-compliance.py --update-lock --change-id <stamped-id>"
    return True, drift_msg

# ---------------------------------------------------------------------------
# Main compliance check
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    change_ids: list[str] = args.change_ids or []
    strict: bool = args.strict
    update_lock: bool = args.update_lock

    if update_lock and not change_ids:
        print(RED("ERROR") + ": --update-lock requires --change-id <stamped id> (rejected without it)")
        return 2

    # Load rule registry
    try:
        rules = _parse_registry(RULE_REGISTRY)
    except FileNotFoundError as e:
        print(RED("ERROR") + f": {e}")
        return 2

    # Build maps: validator_name → rule, and collect block rules
    validator_to_rule: dict[str, dict] = {}
    block_rule_ids: list[str] = []
    warn_rule_ids: list[str] = []
    mech003_rule: dict | None = None

    for rule in rules:
        severity = rule.get("severity", "warn")
        rule_id  = rule.get("id", "UNKNOWN")
        vname    = rule.get("validator_name")
        kind     = rule.get("kind", "judgment")

        if severity == "block":
            block_rule_ids.append(rule_id)

        if kind == "judgment":
            warn_rule_ids.append(rule_id)
            continue

        if vname is None and rule_id == "MECH-003":
            mech003_rule = rule
            continue

        if vname:
            validator_to_rule[vname] = rule

    # ── Step 1: Run enforcement sweep (refresh report) ──────────────────────
    print(BOLD("── compliance check ──────────────────────────────────────────────────"))
    print("  refreshing enforcement sweep…")
    _run_sweep()

    # ── Step 2: Parse sweep FAILs and map to rules ───────────────────────────
    sweep_fails = _parse_sweep_report(SWEEP_REPORT)

    # Load baseline
    baseline_entries, baseline_count = _load_baseline()

    block_new: list[str] = []     # NEW block-severity violations (keys not in baseline)
    warn_new:  list[str] = []     # NEW warn-severity violations (advisory)
    block_known: list[str] = []   # known block-severity violations (in baseline)
    warn_known:  list[str] = []   # known warn violations (advisory, in baseline)
    unregistered_fails: list[str] = []

    for vname in sweep_fails:
        rule = validator_to_rule.get(vname)
        if rule is None:
            unregistered_fails.append(vname)
            continue

        rule_id  = rule.get("id", "UNKNOWN")
        severity = rule.get("severity", "warn")
        key = _composite_key(rule_id, vname, f"sweep-fail:{vname}")

        if severity == "block":
            if key in baseline_entries:
                block_known.append(f"{rule_id} ({vname})")
            else:
                block_new.append(f"{rule_id} ({vname})")
        else:
            if key in baseline_entries:
                warn_known.append(f"{rule_id} ({vname})")
            else:
                warn_new.append(f"{rule_id} ({vname})")

    # ── Step 3: MECH-003 via verify-change ─────────────────────────────────
    mech003_ok = True
    mech003_issues: list[str] = []
    if change_ids:
        mech003_ok, mech003_issues = _run_verify_change(change_ids)

    # ── Step 4: Block-set fingerprint ──────────────────────────────────────
    drift_found, drift_msg = _check_block_set(block_rule_ids, update_lock, change_ids)

    # ── Print results ──────────────────────────────────────────────────────
    print()

    # Block-severity violations
    if block_new:
        print(f"  {RED('BLOCK-SEVERITY — NEW violations (not in baseline):')}")
        for v in block_new:
            print(f"    {RED('•')} {v}")
        if strict:
            print(f"  {RED('[HARD-BLOCK under --strict]')}")
        else:
            print(f"  {YELLOW('[log-only — pass --strict to block]')}")
    elif block_known:
        print(f"  {GREEN('GREEN')} — no NEW block-severity violations")
        print(f"  {DIM(f'known-backlog (block): {len(block_known)} rule(s) — Phase-3 burndown, not an alarm')}")
    else:
        print(f"  {GREEN('GREEN')} — no block-severity violations")

    # Warn-severity violations (advisory only, never block)
    if warn_new or warn_known:
        label = f"warn-severity: {len(warn_new)} new, {len(warn_known)} known (advisory — never blocks)"
        print(f"  {DIM(label)}")

    # Known backlog summary (MECH-001/002 sweep-level)
    if baseline_count > 0:
        print(f"  {DIM(f'enforcement-baseline: {baseline_count} known entries (pre-existing backlog — Phase-3 burndown)')}")

    # Unregistered fails (extension seam — advisory)
    if unregistered_fails:
        print(f"  {DIM('unregistered sweep validators (no rule entry — advisory only):')}")
        for v in unregistered_fails:
            print(f"    {DIM(f'  • {v}')}")

    # MECH-003 result
    print()
    if change_ids:
        if mech003_ok:
            print(f"  MECH-003 (verify-change D1-D4 gate): {GREEN('PASS')}")
        else:
            print(f"  MECH-003 (verify-change D1-D4 gate): {RED('FAIL')}")
            for issue in mech003_issues:
                print(f"    {RED('•')} {issue}")
    else:
        print(f"  MECH-003 (verify-change): {DIM('skipped — no --change-id supplied')}")

    # Block-set fingerprint
    print()
    if drift_found:
        print(f"  {RED(drift_msg)}")
    else:
        print(f"  block-set fingerprint: {GREEN(drift_msg)}")

    # Judgment rules (WARN-only reminder)
    print()
    if warn_rule_ids:
        print(f"  {DIM(f'judgment rules ({len(warn_rule_ids)}): advisory only — cannot be machine-verified')}")

    print(BOLD("──────────────────────────────────────────────────────────────────────"))

    # ── Exit code ─────────────────────────────────────────────────────────
    has_new_block = bool(block_new)
    has_mech003_fail = not mech003_ok and bool(change_ids)
    # MECH-003: a RED verify-change is a new block violation (not in baseline by definition)
    if has_mech003_fail:
        has_new_block = True

    # STRICT-FREE verdict, printed unconditionally and parsed by session-pulse.py.
    # The exit code below cannot carry this: it returns 1 for a new block
    # violation only under --strict, so in the default mode a caller reading the
    # exit code sees "clean" while this line says RED. One of the two has to be
    # honest in every mode, and it is this one.
    if drift_found:
        verdict, why = "RED", "block-set drift"
    elif has_new_block:
        verdict, why = "RED", "new block-severity violation"
    else:
        verdict, why = "GREEN", "no new block-severity violation, no drift"
    print(f"COMPLIANCE-VERDICT: {verdict} ({why})")

    if drift_found:
        return 1  # always non-zero on drift (regardless of --strict)

    if strict and has_new_block:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
