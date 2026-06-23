#!/usr/bin/env python3
"""
check_gate_precondition.py — Pre-spawn existence oracle for ADR-007 inheritable gates.

The ONLY correct pre-spawn gate check. Scans Meta/receipts/ RIGHT NOW for receipts
that satisfy a gate criterion for a given change_id.

Distinct from validate-receipt-graph.py's _check_nn3/_check_nn4 (which are post-hoc
detective traversals anchored on already-existing coder receipts and vacuously ALLOW
pre-spawn because no coder receipt exists yet).

Gates:
  nn3 — contrarian PASS before coder spawn:
        ALLOW iff ≥1 receipt with agent=contrarian, change_id=X,
        review_mode=plan, review_verdict=PASS.
  nn4 — tester DONE before closing a deploy:
        ALLOW iff ≥1 receipt with agent=tester, change_id=X, status=DONE.

Fail-closed: missing or malformed field values → REFUSE.

Usage (CLI):
  python3 check_gate_precondition.py --change-id X --gate nn3
  python3 check_gate_precondition.py --change-id X --gate nn4

Exit codes:
  0 — ALLOW
  1 — REFUSE
  2 — usage error

change_id: adr-007-inheritable-gates-keepers-pilot
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Paths (fail-closed defaults)
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_VAULT = os.path.dirname(os.path.dirname(_THIS_DIR))
_DEFAULT_RECEIPTS = os.path.join(_VAULT, "Meta", "receipts")

VALID_GATES = {"nn3", "nn4"}

# ---------------------------------------------------------------------------
# Frontmatter parser (minimal, stdlib-only)
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML-like frontmatter between the first pair of '---' delimiters.

    Returns an empty dict if the file does not start with '---' or has no
    closing delimiter. Intentionally does not import yaml — stdlib only.
    """
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


# ---------------------------------------------------------------------------
# Gate result type
# ---------------------------------------------------------------------------

class GateResult:
    """Result of a gate precondition check."""

    __slots__ = ("verdict", "reason", "change_id", "gate", "matching_receipt")

    def __init__(
        self,
        verdict: str,
        reason: str,
        change_id: str,
        gate: str,
        matching_receipt: Optional[str] = None,
    ) -> None:
        self.verdict = verdict                   # "ALLOW" or "REFUSE"
        self.reason = reason                     # Human-readable explanation
        self.change_id = change_id
        self.gate = gate
        self.matching_receipt = matching_receipt  # filename that satisfied the gate (ALLOW only)

    def __str__(self) -> str:
        base = f"[{self.verdict}] gate={self.gate} change_id={self.change_id} — {self.reason}"
        if self.matching_receipt:
            base += f" (matched: {self.matching_receipt})"
        return base

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def check_gate_precondition(
    change_id: str,
    gate: str,
    receipts_dir: Optional[str] = None,
) -> GateResult:
    """Pre-spawn existence oracle. Scans receipts_dir RIGHT NOW.

    Parameters
    ----------
    change_id:
        The change identifier to check (e.g. "adr-007-inheritable-gates-keepers-pilot").
    gate:
        "nn3" or "nn4". Any other value returns REFUSE immediately.
    receipts_dir:
        Path to scan. Defaults to Meta/receipts/ relative to this file's vault root.
        Inject a tempdir in tests.

    Returns
    -------
    GateResult with verdict ALLOW or REFUSE, plus a reason string.
    """
    if not change_id or not change_id.strip():
        return GateResult(
            verdict="REFUSE",
            reason="change_id is empty or blank — cannot check gate",
            change_id=change_id,
            gate=gate,
        )

    if gate not in VALID_GATES:
        return GateResult(
            verdict="REFUSE",
            reason=f"unknown gate '{gate}' — valid gates: {sorted(VALID_GATES)}",
            change_id=change_id,
            gate=gate,
        )

    if receipts_dir is None:
        receipts_dir = _DEFAULT_RECEIPTS

    if not os.path.isdir(receipts_dir):
        return GateResult(
            verdict="REFUSE",
            reason=f"receipts directory not found: {receipts_dir}",
            change_id=change_id,
            gate=gate,
        )

    # Scan receipts
    try:
        entries = [e for e in os.listdir(receipts_dir) if e.endswith(".md")]
    except OSError as exc:
        return GateResult(
            verdict="REFUSE",
            reason=f"cannot list receipts directory: {exc}",
            change_id=change_id,
            gate=gate,
        )

    for fname in entries:
        fpath = os.path.join(receipts_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(4096)  # frontmatter is never more than a few hundred bytes
        except OSError:
            continue

        fields = _parse_frontmatter(text)

        # Every check starts with: does this receipt belong to our change_id?
        if fields.get("change_id", "").strip() != change_id:
            continue

        if gate == "nn3":
            # ALLOW iff agent=contrarian, review_mode=plan, review_verdict=PASS
            if (
                fields.get("agent", "").strip() == "contrarian"
                and fields.get("review_mode", "").strip() == "plan"
                and fields.get("review_verdict", "").strip() == "PASS"
            ):
                return GateResult(
                    verdict="ALLOW",
                    reason=(
                        "contrarian plan-review PASS receipt found for this change_id"
                    ),
                    change_id=change_id,
                    gate=gate,
                    matching_receipt=fname,
                )

        elif gate == "nn4":
            # ALLOW iff agent=tester, status=DONE
            if (
                fields.get("agent", "").strip() == "tester"
                and fields.get("status", "").strip() == "DONE"
            ):
                return GateResult(
                    verdict="ALLOW",
                    reason="tester DONE receipt found for this change_id",
                    change_id=change_id,
                    gate=gate,
                    matching_receipt=fname,
                )

    # No satisfying receipt found
    if gate == "nn3":
        reason = (
            "no contrarian receipt with agent=contrarian, review_mode=plan, "
            "review_verdict=PASS found for change_id — coder spawn REFUSED"
        )
    else:
        reason = (
            "no tester receipt with agent=tester, status=DONE found for "
            "change_id — deploy-close REFUSED"
        )

    return GateResult(
        verdict="REFUSE",
        reason=reason,
        change_id=change_id,
        gate=gate,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Pre-spawn existence oracle for ADR-007 inheritable gates. "
            "Returns ALLOW (exit 0) or REFUSE (exit 1)."
        )
    )
    p.add_argument(
        "--change-id",
        required=True,
        metavar="X",
        help="The change_id to check (e.g. adr-007-inheritable-gates-keepers-pilot)",
    )
    p.add_argument(
        "--gate",
        required=True,
        choices=sorted(VALID_GATES),
        help="Gate to check: nn3 (contrarian PASS before coder) or nn4 (tester DONE before deploy-close)",
    )
    p.add_argument(
        "--receipts-dir",
        default=None,
        metavar="PATH",
        help="Override path to receipts directory (default: Meta/receipts/ in vault)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout (exit code still indicates result)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = check_gate_precondition(
        change_id=args.change_id,
        gate=args.gate,
        receipts_dir=args.receipts_dir,
    )
    if not args.quiet:
        print(result)
    return 0 if result.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
