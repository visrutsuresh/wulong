#!/usr/bin/env python3
"""
automerge_gate.py — Autonomous-loop auto-merge authorization gate.

can_auto_merge(change_id) -> (bool, reason)

Returns True ONLY if ALL three conditions hold for change_id:
  1. contrarian receipt: review_mode=plan, review_verdict=PASS
  2. contrarian receipt: review_mode=output, review_verdict=PASS
  3. tester receipt: status=DONE

Fail-closed: missing/malformed → (False, reason).
Reuses check_gate_precondition._parse_frontmatter (stdlib, no yaml).

change_id: v34-rails
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

# Reuse the parser from check_gate_precondition (same vault sync package).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
from check_gate_precondition import _parse_frontmatter

_VAULT = os.path.dirname(os.path.dirname(_THIS_DIR))
_DEFAULT_RECEIPTS = os.path.join(_VAULT, "Meta", "receipts")


def can_auto_merge(
    change_id: str,
    receipts_dir: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (True, reason) only when ALL 3 gate conditions are satisfied.

    Conditions (ALL required):
      - contrarian plan-review PASS (review_mode=plan, review_verdict=PASS)
      - contrarian output-review PASS (review_mode=output, review_verdict=PASS)
      - tester DONE (status=DONE)

    Fail-closed: any error / missing / malformed → (False, reason).
    """
    if not change_id or not change_id.strip():
        return False, "REFUSE: change_id is empty — fail-closed"

    if receipts_dir is None:
        receipts_dir = _DEFAULT_RECEIPTS

    if not os.path.isdir(receipts_dir):
        return False, f"REFUSE: receipts directory not found: {receipts_dir}"

    try:
        entries = [e for e in os.listdir(receipts_dir) if e.endswith(".md")]
    except OSError as exc:
        return False, f"REFUSE: cannot list receipts directory: {exc}"

    found_plan_pass = False
    found_output_pass = False
    found_tester_done = False

    for fname in entries:
        fpath = os.path.join(receipts_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(4096)
        except OSError:
            continue

        fields = _parse_frontmatter(text)

        if fields.get("change_id", "").strip() != change_id:
            continue

        agent = fields.get("agent", "").strip()
        status = fields.get("status", "").strip()
        review_mode = fields.get("review_mode", "").strip()
        review_verdict = fields.get("review_verdict", "").strip()

        if (
            agent == "contrarian"
            and review_mode == "plan"
            and review_verdict == "PASS"
        ):
            found_plan_pass = True

        if (
            agent == "contrarian"
            and review_mode == "output"
            and review_verdict == "PASS"
        ):
            found_output_pass = True

        if agent == "tester" and status == "DONE":
            found_tester_done = True

    if not found_plan_pass:
        return False, "REFUSE: no contrarian plan-review PASS receipt found for change_id"
    if not found_output_pass:
        return (
            False,
            "REFUSE: no contrarian output-review PASS receipt found for change_id"
            " — output review is required before auto-merge",
        )
    if not found_tester_done:
        return False, "REFUSE: no tester DONE receipt found for change_id"

    return True, "ALLOW: plan-review PASS + output-review PASS + tester DONE all satisfied"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _demo() -> None:
    """Self-check with no real receipts — verify fail-closed behaviour."""
    import tempfile

    cases: list[tuple[str, str, list[tuple[str, str]], bool]] = [
        (
            "empty change_id",
            "",
            [],
            False,
        ),
        (
            "no receipts at all",
            "test-change",
            [],
            False,
        ),
        (
            "plan-PASS only (missing output-review)",
            "test-change",
            [
                "agent: contrarian\nchange_id: test-change\nreview_mode: plan\nreview_verdict: PASS\nstatus: DONE\n",
                "agent: tester\nchange_id: test-change\nstatus: DONE\n",
            ],
            False,  # ponytail: output-review is the exact gap the contrarian flagged
        ),
        (
            "all three present",
            "test-change",
            [
                "agent: contrarian\nchange_id: test-change\nreview_mode: plan\nreview_verdict: PASS\nstatus: DONE\n",
                "agent: contrarian\nchange_id: test-change\nreview_mode: output\nreview_verdict: PASS\nstatus: DONE\n",
                "agent: tester\nchange_id: test-change\nstatus: DONE\n",
            ],
            True,
        ),
    ]

    all_pass = True
    for label, cid, receipt_bodies, expect_allow in cases:
        with tempfile.TemporaryDirectory() as td:
            for i, body in enumerate(receipt_bodies):
                path = os.path.join(td, f"receipt-{i}.md")
                with open(path, "w") as f:
                    f.write(f"---\n{body}---\n")
            allow, reason = can_auto_merge(cid, receipts_dir=td)
        ok = allow == expect_allow
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{status}] {label}: {'ALLOW' if allow else 'REFUSE'}")
        if not ok:
            print(f"         expected={'ALLOW' if expect_allow else 'REFUSE'} reason={reason}")

    print()
    print("--demo:", "ALL PASS" if all_pass else "FAILURES DETECTED")
    sys.exit(0 if all_pass else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous-loop auto-merge gate: ALLOW only if all 3 gate conditions satisfied."
    )
    parser.add_argument("--change-id", help="The change_id to evaluate")
    parser.add_argument("--receipts-dir", default=None, help="Override receipts directory path")
    parser.add_argument("--demo", action="store_true", help="Run self-check demo and exit")
    args = parser.parse_args()

    if args.demo:
        _demo()

    if not args.change_id:
        parser.error("--change-id is required (or use --demo)")

    allow, reason = can_auto_merge(args.change_id, receipts_dir=args.receipts_dir)
    print(f"{'ALLOW' if allow else 'REFUSE'}: {reason}")
    sys.exit(0 if allow else 1)


if __name__ == "__main__":
    main()
