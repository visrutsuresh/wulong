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

from wulong._binding import (
    ENV_LEGACY_UNTIL,
    FIELD_COUNT,
    FIELD_DIGEST,
    reads_pass,
    verdict_is_binding_pass,
)
from wulong._frontmatter import parse_frontmatter
from wulong._manifest import ManifestError, manifest_digest
from wulong._root import ENV_VAR, RootNotFound, resolve_root

# ---------------------------------------------------------------------------
# Paths (fail-closed defaults)
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Install-relative floor only. In a wheel this is site-packages, which is why it
# is a floor and not an answer: the real root arrives via --root or WULONG_ROOT.
_INSTALL_RELATIVE = os.path.dirname(os.path.dirname(_THIS_DIR))
_DEFAULT_RECEIPTS = os.path.join(_INSTALL_RELATIVE, "Meta", "receipts")

VALID_GATES = {"nn3", "nn4"}

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
    require_binding: Optional[bool] = None,
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
    require_binding:
        True refuses a PASS that carries no artifact manifest digest, False
        allows one, None takes the WULONG_REQUIRE_BINDING variable and then the
        migration default in wulong/_binding.py. Only the nn3 gate consults it;
        nn4 keys off `status` and is a separate change.

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

    # A plan-review PASS that exists and is REFUSED for carrying no artifact
    # binding is a different fact from no plan-review PASS existing at all, and
    # the REFUSE reason at the bottom has to say which one happened.
    unbound_pass: list[str] = []

    for fname in entries:
        fpath = os.path.join(receipts_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(4096)  # frontmatter is never more than a few hundred bytes
        except OSError:
            continue

        fields = parse_frontmatter(text)

        # Every check starts with: does this receipt belong to our change_id?
        if fields.get("change_id", "").strip() != change_id:
            continue

        if gate == "nn3":
            # ALLOW iff agent=contrarian, review_mode=plan, review_verdict=PASS
            is_plan_review = (
                fields.get("agent", "").strip() == "contrarian"
                and fields.get("review_mode", "").strip() == "plan"
            )
            if (
                is_plan_review
                and verdict_is_binding_pass(fields, label=fname, require=require_binding)
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
            if is_plan_review and reads_pass(fields):
                unbound_pass.append(fname)

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
    if gate == "nn3" and unbound_pass:
        reason = (
            f"contrarian plan-review receipt {unbound_pass} reads "
            "review_verdict=PASS but is not bound to an artifact, so it was "
            "refused under the binding requirement. Stamp it with `wulong gate "
            "--manifest --artifact PATH`. Coder spawn REFUSED"
        )
    elif gate == "nn3":
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
        "--root",
        default=None,
        metavar="PATH",
        help=f"Vault root. Wins over the {ENV_VAR} env var. "
             "Receipts are read from <root>/Meta/receipts unless --receipts-dir says otherwise.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout (exit code still indicates result)",
    )
    p.add_argument(
        "--require-binding",
        action="store_true",
        help="REFUSE an nn3 PASS that carries no artifact manifest digest. "
             "Default OFF during the migration window; a warning is printed "
             "instead. Becomes the default at 0.6.0.",
    )
    p.add_argument(
        "--legacy-unbound-until",
        default=None,
        metavar="YYYY-MM-DD",
        help="ADVISORY exemption: accept an unbound PASS on a receipt dated "
             "before this. It keys off the receipt's OWN self-reported date "
             "field, so it is a convenience for an old corpus, not a control.",
    )
    return p


# ---------------------------------------------------------------------------
# Artifact modes: --manifest writes the digest, --verify recomputes it
# ---------------------------------------------------------------------------

_ARTIFACT_FLAGS = ("--manifest", "--verify")


def _is_artifact_mode(argv: list[str]) -> bool:
    return any(
        tok in _ARTIFACT_FLAGS or tok.startswith("--verify=") for tok in argv
    )


def _build_artifact_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wulong gate",
        description=(
            "Artifact manifest modes. --manifest prints the digest to stamp into "
            "a receipt; --verify recomputes it from the bytes you name and "
            "compares. Neither reads a vault."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--manifest",
        action="store_true",
        help="Print the manifest digest and a paste-ready frontmatter block.",
    )
    mode.add_argument(
        "--verify",
        default=None,
        metavar="RECEIPT",
        help="Recompute the manifest and compare it to RECEIPT's recorded digest.",
    )
    p.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="PATH",
        help="An artifact to hash. Repeat once per artifact. YOU enumerate them: "
             "no mode reads a file list out of the receipt.",
    )
    # Accepted and unused. `wulong gate` normally gets a --root injected, and a
    # user may type one out of habit. Neither artifact mode resolves a vault.
    p.add_argument("--root", default=None, help=argparse.SUPPRESS)
    return p


def _gated_by_names(raw: str) -> list[str]:
    """Split a `[a.md, b.md]` inline list into its entries."""
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]


def _run_artifact_mode(argv: list[str]) -> int:
    args = _build_artifact_parser().parse_args(argv)
    if not args.artifact:
        print(
            "wulong gate: --artifact is required at least once. The CALLER "
            "enumerates the artifacts; a receipt's Files-written list is a "
            "fail-open prose parser and is never used as the enumeration source.",
            file=sys.stderr,
        )
        return 2
    try:
        digest = manifest_digest(args.artifact)
    except ManifestError as exc:
        print(f"[REFUSE] {exc}", file=sys.stderr)
        return 1

    count = len(args.artifact)
    if args.manifest:
        print(f"artifact_manifest_sha256: {digest}")
        print(f"artifact_count: {count}")
        print(f"artifact_paths: [{', '.join(args.artifact)}]")
        print(
            "\nartifact_paths is DIAGNOSTIC ONLY. No verifier resolves it, and "
            "no path is inside the digest.",
            file=sys.stderr,
        )
        return 0

    try:
        with open(args.verify, "r", encoding="utf-8", errors="ignore") as handle:
            fields = parse_frontmatter(handle.read())
    except OSError as exc:
        print(f"wulong gate: cannot read receipt: {exc}", file=sys.stderr)
        return 2

    # BA-2: the bytes come from --artifact and are recomputed. The receipt's own
    # artifact_paths field is deliberately NOT read here, because reading it
    # would make a recorded path load-bearing and falsify the published claim.
    recorded = fields.get(FIELD_DIGEST, "").strip()
    if not recorded:
        print(
            f"[UNBOUND] {args.verify}: no {FIELD_DIGEST} field, so there is "
            "nothing to verify against.",
            file=sys.stderr,
        )
        return 1

    recorded_count = fields.get(FIELD_COUNT, "").strip()
    if recorded != digest:
        print(
            f"[MISMATCH] {args.verify}: recorded {recorded}, recomputed {digest} "
            f"over {count} artifact(s) (receipt says {recorded_count or 'no count'})",
            file=sys.stderr,
        )
        return 1

    # BA-4: the manifest is authoritative for WHAT WAS HASHED, gated_by for graph
    # topology. They need not be co-extensive, so a predecessor outside the
    # manifest is REPORTED and never refused.
    supplied = {os.path.basename(a) for a in args.artifact}
    outside = [g for g in _gated_by_names(fields.get("gated_by", "")) if g not in supplied]
    for name in outside:
        print(
            f"[REPORT] {args.verify}: gated_by names '{name}', which is not in "
            "the manifest. gated_by is graph topology; the manifest is what was "
            "hashed. Not a failure."
        )
    print(f"[VERIFIED] {args.verify}: manifest digest matches over {count} artifact(s)")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if _is_artifact_mode(tokens):
        return _run_artifact_mode(tokens)

    args = _build_parser().parse_args(argv)
    if args.legacy_unbound_until:
        os.environ[ENV_LEGACY_UNTIL] = args.legacy_unbound_until

    receipts_dir = args.receipts_dir
    if receipts_dir is None:
        try:
            root = resolve_root(args.root, tool="wulong gate")
        except RootNotFound as exc:
            print(str(exc), file=sys.stderr)
            return 2
        receipts_dir = os.path.join(root, "Meta", "receipts")

    result = check_gate_precondition(
        change_id=args.change_id,
        gate=args.gate,
        receipts_dir=receipts_dir,
        require_binding=True if args.require_binding else None,
    )
    if not args.quiet:
        print(result)
    return 0 if result.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
