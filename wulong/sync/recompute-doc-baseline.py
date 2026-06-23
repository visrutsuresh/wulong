#!/usr/bin/env python3
"""
recompute-doc-baseline.py — thin wrapper for the janitor's doc-reconciliation job.

Re-runs check-doc-consistency.py, parses the remaining DISAGREE entries using
session-pulse.py's EXISTING `_parse_disagrees` (imported, NOT reimplemented),
and writes the canonical set of composite keys + count back to
doc-consistency-baseline.json.

This is the single named step called by Meta/playbooks/janitor/doc-reconciliation.md.
It is a small sync utility — it contains NO parsing logic of its own; it reuses the
SOLE authoritative parser to avoid a second drifting parser (per Phase 3.1 plan).

Usage:
    python3 Meta/sync/recompute-doc-baseline.py            # write the recomputed baseline
    python3 Meta/sync/recompute-doc-baseline.py --dry-run  # print what would be written, no write
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# Import the SOLE authoritative parser + constants from session-pulse.py.
# Do NOT reimplement _parse_disagrees here.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import importlib.util

_spec = importlib.util.spec_from_file_location("session_pulse", SCRIPT_DIR / "session-pulse.py")
_sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sp)

_parse_disagrees = _sp._parse_disagrees          # reused, not reimplemented
CHECK_DOC = _sp.CHECK_DOC
BASELINE_FILE = _sp.BASELINE_FILE


def recompute() -> tuple[list[str], str]:
    """Run the checker, parse remaining DISAGREEs, return (sorted_keys, raw_output)."""
    proc = subprocess.run(
        [sys.executable, str(CHECK_DOC)],
        capture_output=True, text=True
    )
    output = proc.stdout + proc.stderr
    keys = sorted(set(_parse_disagrees(output)))
    return keys, output


def main() -> int:
    ap = argparse.ArgumentParser(description="Recompute doc-consistency baseline keys + count.")
    ap.add_argument("--dry-run", action="store_true", help="print result, do not write the baseline file")
    args = ap.parse_args()

    keys, _ = recompute()

    # Preserve any existing top-level metadata (e.g. "note"); refresh keys + count.
    data: dict = {}
    if BASELINE_FILE.exists():
        try:
            with open(BASELINE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    data["keys"] = keys
    data["count"] = len(keys)

    if args.dry_run:
        print(f"[dry-run] would write {len(keys)} key(s) to {BASELINE_FILE}")
        for k in keys:
            print(f"  {k}")
        return 0

    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(keys)} DISAGREE key(s) to {BASELINE_FILE} (count={len(keys)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
