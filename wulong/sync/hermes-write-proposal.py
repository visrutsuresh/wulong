#!/usr/bin/env python3
"""
hermes-write-proposal.py — the ONLY sanctioned write path for Hermes in PROPOSE mode.

Validates a proposal payload against Hermes's hard constraints:
- Single proposed_variable (not a list)
- Single proposed_value (no multiple blocks)
- scope ∈ config.allowed_scopes
- ≥config.min_evidence_citations evidence paths
- ≤config.max_proposals_per_day already written today
- --variable must be in hermes_owns for --scope per surface-manifest.yaml
- --surface-category must match manifest classification for the variable

If valid, writes a proposal artifact to Meta/hermes-proposals/queued/<date>.md
and posts a ⏳ jarvis line to Meta/agent-messages.md requesting review.

Flag aliases (old flags emit DEPRECATED warning to stderr; both accepted):
  --current-value      ← canonical (replaces --baseline-value)
  --baseline-value     ← DEPRECATED alias for --current-value
  --success-criterion  ← canonical (replaces --hypothesis)
  --hypothesis         ← DEPRECATED alias for --success-criterion
  --rollback-condition ← canonical (replaces --falsification)
  --falsification      ← DEPRECATED alias for --rollback-condition

New required flags (no aliases):
  --predicted-score-direction  choices=[up, down, flat]
  --surface-category           choices=[strategy, learned_param]

Usage (called by Hermes via Bash):
    python3 hermes-write-proposal.py \\
        --scope my-project \\
        --variable <name> \\
        --current-value <observed> \\
        --proposed-value <new> \\
        --success-criterion "<one-sentence>" \\
        --rollback-condition "<one-sentence>" \\
        --predicted-score-direction up|down|flat \\
        --surface-category strategy|learned_param \\
        --evidence "<path1>,<path2>,<path3>" \\
        --domains-touched "my-project" \\
        --authority-conflicts "" \\
        --confidence high \\
        --rationale-file /tmp/rationale.md

Exit codes:
  0 = success (proposal written)
  1 = validation failure
  2 = filesystem error
  3 = daily cap reached
"""
from __future__ import annotations
import argparse
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-untyped]

import os
_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
CONFIG = VAULT / "Meta" / "hermes" / "config.json"
QUEUED_DIR = VAULT / "Meta" / "hermes-proposals" / "queued"
AGENT_MESSAGES = VAULT / "Meta" / "agent-messages.md"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"
SURFACE_MANIFEST = VAULT / "Meta" / "hermes" / "surface-manifest.yaml"
ALLOWLIST_PATH = VAULT / "Meta" / "observer-apply" / "self-apply-allowlist.yaml"
INBOX_DIR = VAULT / "Meta" / "observer-apply" / "inbox"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def load_manifest() -> dict:
    """Load and return the surface-manifest. Fail-closed: exit 1 on missing or malformed."""
    if not SURFACE_MANIFEST.exists():
        sys.stderr.write(
            f"[hermes-proposal] REJECT: surface-manifest.yaml missing at {SURFACE_MANIFEST}. "
            "Bootstrap: AR Director must create it before any proposal can be written.\n"
        )
        sys.exit(1)
    try:
        data = yaml.safe_load(SURFACE_MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: surface-manifest.yaml malformed: {e}\n"
        )
        sys.exit(1)
    if not isinstance(data, dict):
        sys.stderr.write(
            "[hermes-proposal] REJECT: surface-manifest.yaml must be a mapping at top level.\n"
        )
        sys.exit(1)
    return data


def count_today_proposals() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    if not QUEUED_DIR.exists():
        return 0
    return len(list(QUEUED_DIR.glob(f"{today}-*.md")))


def _add_aliased(ap: argparse.ArgumentParser, canonical: str, alias: str, **kwargs) -> None:
    """Add a canonical flag plus a DEPRECATED alias that shares the same dest."""
    dest = canonical.lstrip("-").replace("-", "_")
    ap.add_argument(canonical, dest=dest, **kwargs)
    ap.add_argument(alias, dest=dest, **kwargs)


class _DeprecationAction(argparse.Action):
    """Emit a DEPRECATED warning when an old flag alias is used."""

    def __init__(self, option_strings, dest, canonical_flag: str, **kwargs):
        self._canonical_flag = canonical_flag
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if option_string and option_string != self._canonical_flag:
            sys.stderr.write(
                f"[hermes-proposal] DEPRECATED: {option_string!r} is deprecated. "
                f"Use {self._canonical_flag!r} instead.\n"
            )
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Hermes proposal writer — see module docstring for usage."
    )
    ap.add_argument("--scope", required=True)
    ap.add_argument("--variable", required=True)

    # Canonical: --current-value  /  DEPRECATED alias: --baseline-value
    ap.add_argument(
        "--current-value",
        dest="current_value",
        action=_DeprecationAction,
        canonical_flag="--current-value",
        default=None,
    )
    ap.add_argument(
        "--baseline-value",
        dest="current_value",
        action=_DeprecationAction,
        canonical_flag="--current-value",
        default=None,
    )

    ap.add_argument("--proposed-value", required=True)

    # Canonical: --success-criterion  /  DEPRECATED alias: --hypothesis
    ap.add_argument(
        "--success-criterion",
        dest="success_criterion",
        action=_DeprecationAction,
        canonical_flag="--success-criterion",
        default=None,
    )
    ap.add_argument(
        "--hypothesis",
        dest="success_criterion",
        action=_DeprecationAction,
        canonical_flag="--success-criterion",
        default=None,
    )

    # Canonical: --rollback-condition  /  DEPRECATED alias: --falsification
    ap.add_argument(
        "--rollback-condition",
        dest="rollback_condition",
        action=_DeprecationAction,
        canonical_flag="--rollback-condition",
        default=None,
    )
    ap.add_argument(
        "--falsification",
        dest="rollback_condition",
        action=_DeprecationAction,
        canonical_flag="--rollback-condition",
        default=None,
    )

    # New required flags — no aliases
    ap.add_argument(
        "--predicted-score-direction",
        choices=["up", "down", "flat"],
        required=True,
    )
    ap.add_argument(
        "--surface-category",
        choices=["strategy", "learned_param"],
        required=True,
    )

    ap.add_argument("--evidence", required=True, help="comma-separated paths")
    ap.add_argument("--domains-touched", default="")
    ap.add_argument("--authority-conflicts", default="")
    ap.add_argument("--confidence", choices=["low", "medium", "high"], required=True)
    ap.add_argument(
        "--rationale-file", required=True, help="path to file with rationale body (≤500 words)"
    )
    return ap


def validate_manifest_ownership(manifest: dict, scope: str, variable: str, surface_category: str) -> int:
    """
    Validate that:
      1. The variable is in hermes_owns for the given scope (not metis_owns or forbidden).
      2. The declared surface_category matches the manifest classification.
    Returns 0 on pass, 1 on failure (writes to stderr).
    """
    scope_data = manifest.get(scope)
    if scope_data is None:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: scope {scope!r} not found in surface-manifest.yaml. "
            "Add it via AR Director before proposing.\n"
        )
        return 1

    def _names(lst: list) -> set:
        return {entry["name"] if isinstance(entry, dict) else entry for entry in (lst or [])}

    hermes_owns = _names(scope_data.get("hermes_owns", []))
    metis_owns = _names(scope_data.get("metis_owns", []))
    forbidden = _names(scope_data.get("forbidden", []))

    if variable in forbidden:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: variable {variable!r} is in the forbidden list for "
            f"scope {scope!r}. It may not be proposed by any agent.\n"
        )
        return 1

    if variable in metis_owns:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: variable {variable!r} is owned by Metis (metis_owns) "
            f"for scope {scope!r}. Use metis-write-proposal.py instead.\n"
        )
        return 1

    if variable not in hermes_owns:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: variable {variable!r} is not listed in hermes_owns for "
            f"scope {scope!r}. Add it to the manifest via AR Director, or use metis-write-proposal.py "
            "if it is a learned parameter.\n"
        )
        return 1

    # surface_category must be "strategy" for hermes_owns variables
    if surface_category != "strategy":
        sys.stderr.write(
            f"[hermes-proposal] REJECT: variable {variable!r} is in hermes_owns which maps to "
            f"surface_category='strategy', but caller passed --surface-category={surface_category!r}. "
            "Fix the surface_category or move the variable to metis_owns.\n"
        )
        return 1

    return 0


def _load_allowlist_for_variable(variable: str) -> bool:
    """
    Return True if variable is in the self-apply allow-list (ADR-006 DQ5).
    Tolerate-absent: returns False if the YAML is missing (ar-director still working).
    """
    if not ALLOWLIST_PATH.exists():
        return False
    try:
        data = yaml.safe_load(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    entries = data.get("allow", []) or []
    for entry in entries:
        name = entry.get("variable", "") if isinstance(entry, dict) else str(entry)
        if name.endswith(".*"):
            if variable.startswith(name[:-2]):
                return True
        elif name == variable:
            return True
    return False


def _emit_inbox_item(
    cycle_id: str,
    args,
    evidence: list[str],
    now: datetime,
    times_observed: int,
    distinct_days: int,
    confidence: str,
) -> Path | None:
    """
    Emit an evidence bundle + concrete diff as an ADR-006 observer-apply/v1 inbox item.
    Called only when variable is allow-listed AND the promotion threshold was cleared.
    Returns the written path, or None if allow-list absent (tolerate-absent).
    """
    if not _load_allowlist_for_variable(args.variable):
        return None

    import hashlib  # noqa: PLC0415
    patch_op = {
        "json_path": args.variable.split(":")[1].strip().split(".") if ":" in args.variable
        else [args.variable],
        "value": args.proposed_value,
    }
    patch_bytes = json.dumps(patch_op, sort_keys=True).encode("utf-8")
    patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()

    inbox_item: dict = {
        "schema": "observer-apply/v1",
        "proposal_id": cycle_id,
        "observer": "hermes",
        "observation_id": cycle_id,
        "scope": args.scope,
        "variable": args.variable,
        "current_value": args.current_value,
        "proposed_value": args.proposed_value,
        "evidence": {
            "times_observed": times_observed,
            "distinct_days": distinct_days,
            "confidence": confidence,
            "cited_receipts": evidence,
            "success_criterion": args.success_criterion,
            "rollback_condition": args.rollback_condition,
        },
        "diff": {
            "target_file": str(VAULT / args.variable.split(":")[0].strip()) if ":" in args.variable
            else str(VAULT / "Meta" / "hermes" / "config.json"),
            "diff_kind": "config_knob",
            "applier": "observer-apply.py",
            "patch": patch_op,
            "patch_sha256": patch_sha256,
        },
        "blast_radius": f"Single observer-config knob for scope={args.scope}. "
            "Touches no gate, no review depth, no trading code.",
        "rollback": f"Revert to current_value={args.current_value!r}.",
        "rate_limit_token": f"{now.date().isoformat()}-hermes-1",
        "contrarian_receipt": "",
    }

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y-%m-%d-%H%M")
    inbox_path = INBOX_DIR / f"{cycle_id}.json"
    inbox_path.write_text(json.dumps(inbox_item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return inbox_path


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    # Post-parse required checks for the aliased flags (argparse won't enforce required=True on
    # conflicting dest — we enforce manually)
    if args.current_value is None:
        sys.stderr.write("[hermes-proposal] error: --current-value (or --baseline-value) is required\n")
        return 1
    if args.success_criterion is None:
        sys.stderr.write("[hermes-proposal] error: --success-criterion (or --hypothesis) is required\n")
        return 1
    if args.rollback_condition is None:
        sys.stderr.write("[hermes-proposal] error: --rollback-condition (or --falsification) is required\n")
        return 1

    cfg = load_config()
    manifest = load_manifest()  # fail-closed — exits 1 if missing/malformed

    # ── Validate single-variable rule ───────────────────────────────────────────
    if "," in args.variable:
        sys.stderr.write("[hermes-proposal] REJECT: proposed_variable must be a single name\n")
        return 1
    if ";" in args.proposed_value or "\n" in args.proposed_value:
        sys.stderr.write("[hermes-proposal] REJECT: proposed_value must be a single literal\n")
        return 1

    # ── Validate scope lock ────────────────────────────────────────────────────
    if args.scope not in cfg.get("allowed_scopes", []):
        sys.stderr.write(
            f"[hermes-proposal] REJECT: scope {args.scope!r} not in allowed_scopes "
            f"{cfg.get('allowed_scopes')}\n"
        )
        return 1

    # ── Validate manifest ownership + surface_category ────────────────────────
    rc = validate_manifest_ownership(manifest, args.scope, args.variable, args.surface_category)
    if rc != 0:
        return rc

    # ── Validate evidence floor ────────────────────────────────────────────────
    evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
    if len(evidence) < cfg.get("min_evidence_citations", 3):
        sys.stderr.write(
            f"[hermes-proposal] REJECT: only {len(evidence)} evidence citations; "
            f"need ≥{cfg.get('min_evidence_citations', 3)}\n"
        )
        return 1

    # ── Daily proposal cap (null/None/0 = unlimited per CEO directive 2026-05-29) ──
    today_count = count_today_proposals()
    max_today = cfg.get("max_proposals_per_day")
    if max_today and max_today > 0 and today_count >= max_today:
        sys.stderr.write(
            f"[hermes-proposal] REJECT: daily cap {max_today} reached ({today_count} already today)\n"
        )
        return 3

    # ── Validate rationale file exists ─────────────────────────────────────────
    rat_path = Path(args.rationale_file)
    if not rat_path.exists():
        sys.stderr.write(f"[hermes-proposal] REJECT: rationale file missing: {rat_path}\n")
        return 1
    rationale = rat_path.read_text(encoding="utf-8").strip()
    if len(rationale.split()) > 500:
        sys.stderr.write(
            "[hermes-proposal] WARN: rationale > 500 words; truncating logically allowed "
            "but reviewer may flag.\n"
        )

    # ── Compose artifact ───────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d-%H%M")
    cycle_id = f"hermes-{timestamp}-{today_count + 1:02d}"
    domains = [d.strip() for d in args.domains_touched.split(",") if d.strip()] or [args.scope]
    conflicts = [c.strip() for c in args.authority_conflicts.split(",") if c.strip()]

    QUEUED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = QUEUED_DIR / f"{timestamp}-{args.variable}.md"

    front = [
        "---",
        f"cycle_id: {cycle_id}",
        f"scope: {args.scope}",
        f"proposed_variable: {args.variable}",
        f"surface_category: {args.surface_category}",
        f"current_value: {json.dumps(args.current_value)}",
        f"proposed_value: {json.dumps(args.proposed_value)}",
        f"evidence: {json.dumps(evidence)}",
        f"success_criterion: {json.dumps(args.success_criterion)}",
        f"rollback_condition: {json.dumps(args.rollback_condition)}",
        f"predicted_score_direction: {args.predicted_score_direction}",
        f"domains_touched: {json.dumps(domains)}",
        f"authority_conflicts: {json.dumps(conflicts)}",
        f"confidence: {args.confidence}",
        f"created_at: {now.isoformat()}",
        "---",
        "",
        rationale,
        "",
    ]
    out_path.write_text("\n".join(front), encoding="utf-8")

    # ── Notify Jarvis (per-agent dir — no agent-messages.md) ──────────────────
    # Background observers write PROPOSE to their own dir only — never
    # agent-messages.md — to avoid concurrent-write contention; Jarvis collates
    # at session-start.
    notify_path = QUEUED_DIR / f"{cycle_id}-notify.md"
    notify_path.write_text(
        f"## [{now.strftime('%Y-%m-%d %H:%M')}] — From: hermes → TO: Jarvis\n"
        f"**Status**: ⏳ pending CEO review\n"
        f"**Subject**: Proposal {cycle_id} — change `{args.variable}` in scope `{args.scope}`\n"
        f"**Action requested**: Review {out_path.relative_to(VAULT)}, route to contrarian gate; if PASS, "
        f"surface to CEO for ship-approval; if approved, ar-director translates per NN #6.\n",
        encoding="utf-8",
    )

    # ── change-log ─────────────────────────────────────────────────────────────
    with open(CHANGE_LOG, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(
                f"[{now.strftime('%Y-%m-%d %H:%M')}] hermes → PROPOSAL {cycle_id} "
                f"queued at {out_path.relative_to(VAULT)} (scope={args.scope}, var={args.variable}, "
                f"confidence={args.confidence}, evidence_n={len(evidence)}, "
                f"surface_category={args.surface_category}, "
                f"predicted_score_direction={args.predicted_score_direction})\n"
            )
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # ── ADR-006: emit inbox item if allow-listed (additive, does not affect queue artifact) ──
    # Gate: variable must be in self-apply-allowlist.yaml AND promotion threshold cleared
    # (threshold check: times_observed and distinct_days are not tracked in this script's
    # args — the observer passes them via the evidence list. We gate here purely on
    # allow-list membership; threshold enforcement is the observer's responsibility upstream.)
    inbox_path = _emit_inbox_item(
        cycle_id=cycle_id,
        args=args,
        evidence=evidence,
        now=now,
        times_observed=len(evidence),
        distinct_days=1,
        confidence=args.confidence,
    )
    if inbox_path is not None:
        print(f"[hermes-proposal] INBOX item emitted at {inbox_path} (allow-listed for self-apply)")

    print(f"[hermes-proposal] WROTE {out_path}")
    print(f"[hermes-proposal] queued notify for Jarvis at {notify_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[hermes-proposal] FATAL: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(2)
