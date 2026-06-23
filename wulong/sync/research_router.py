#!/usr/bin/env python3
"""
research_router.py — Risk-class router for the research-proposal feed (v32-p7).

Pure function: route_risk_class(risk_class, target) -> gate

Gate values: "CEO-ALWAYS" | "CONTRARIAN-GATED" | "LIGHTWEIGHT"

Design mirrors the ADR-006 observer self-apply safety pattern:
- Default-deny: unknown/missing risk_class -> CEO-ALWAYS (fail-closed).
- Re-derive from target: a low self-declared risk_class is overridden to
  CEO-ALWAYS when the target path points at protected surfaces (CLAUDE.md,
  agent defs, trading repos, stage.json, *.plist, spawn/kill-switch paths).
  This mirrors ADR-006's "applier re-checks forbidden independently" principle.

Usage:
    from research_router import route_risk_class
    gate = route_risk_class("gated-code", "Meta/sync/foo.py")  # -> "CONTRARIAN-GATED"
    gate = route_risk_class("note", "CLAUDE.md")               # -> "CEO-ALWAYS" (re-derived)
"""
from __future__ import annotations

import os
import re

# ── Gate constants ────────────────────────────────────────────────────────────

CEO_ALWAYS = "CEO-ALWAYS"
CONTRARIAN_GATED = "CONTRARIAN-GATED"
LIGHTWEIGHT = "LIGHTWEIGHT"

# ── Self-declared class -> gate table ────────────────────────────────────────

# ponytail: flat dict lookup; no class hierarchy needed for this routing table.
_CLASS_GATE: dict[str, str] = {
    # CEO-always classes
    "core": CEO_ALWAYS,
    "nn": CEO_ALWAYS,
    "kill-switch": CEO_ALWAYS,
    "trading-logic": CEO_ALWAYS,
    "live": CEO_ALWAYS,
    "capital": CEO_ALWAYS,
    # Contrarian-gated classes
    "gated-code": CONTRARIAN_GATED,
    "vault-tooling": CONTRARIAN_GATED,
    "doc": CONTRARIAN_GATED,
    "governance-non-core": CONTRARIAN_GATED,
    # Lightweight classes
    "note": LIGHTWEIGHT,
    "research-capture": LIGHTWEIGHT,
}

# ── Protected target patterns — force CEO-ALWAYS regardless of declared class ─
# Mirrors ADR-006 FORBIDDEN_SELF_APPLY: the applier re-checks forbidden
# independently, fail-closed. Here the router re-derives risk from target.

_CEO_TARGET_PATTERNS: list[re.Pattern[str]] = [
    # CLAUDE.md — the NNs and project instructions
    re.compile(r"(?i)\bCLAUDE\.md\b"),
    # Agent definitions / roster / managed-settings
    re.compile(r"(?i)\.claude[/\\]agents[/\\]"),
    re.compile(r"(?i)managed.settings\.json"),
    # Trading repos (under GitHub/) — any path inside them.
    # ponytail: base pattern; extend by setting WULONG_TRADING_REPOS env var as
    # pipe-separated names, e.g. "my_trader|my_bot". Upgrade path = .wulong/projects.json
    re.compile(
        r"(?i)(?:GitHub|Documents/GitHub)[/\\](?:"
        + "|".join(
            ["my_trader", "wulong.strat"]
            + [re.escape(r) for r in os.environ.get("WULONG_TRADING_REPOS", "").split("|") if r]
        )
        + r")[/\\]"
    ),
    # stage.json — live-mode flag (CLAUDE.md sensitive tripwire)
    re.compile(r"(?i)\bstage\.json\b"),
    # launchd plists — cron/deploy
    re.compile(r"(?i)\.plist\b"),
    # Spawn gate / contrarian gate machinery
    re.compile(r"(?i)\bspawn_gate\b"),
    re.compile(r"(?i)\bcheck_gate_precondition\b"),
    # Kill-switch / halt paths
    re.compile(r"(?i)\bkill.switch\b"),
    re.compile(r"(?i)\bhalt\b.*\bpath\b"),
    re.compile(r"(?i)\bwulong.gate\b"),
    re.compile(r"(?i)\bwulong.stop\b"),
    # self-apply tooling (mirrors ADR-006 FORBIDDEN_SELF_APPLY)
    re.compile(r"(?i)\bself.apply.allowlist\b"),
    re.compile(r"(?i)\bobserver.apply\.py\b"),
]


def _target_forces_ceo(target: str) -> bool:
    """Return True if target path matches any protected surface."""
    if not target or not target.strip():
        return False
    return any(p.search(target) for p in _CEO_TARGET_PATTERNS)


def route_risk_class(risk_class: str | None, target: str | None = None) -> str:
    """
    Return the required gate for a research proposal.

    Args:
        risk_class: Self-declared risk class string (may be None/empty).
        target:     The proposal's target path/area (used for re-derivation).

    Returns:
        One of "CEO-ALWAYS", "CONTRARIAN-GATED", "LIGHTWEIGHT".
    """
    # Step 1: re-derive from target FIRST — independent of self-declared class.
    # Mirrors ADR-006: "applier re-checks forbidden independently (fail-closed)".
    if _target_forces_ceo(target or ""):
        return CEO_ALWAYS

    # Step 2: look up self-declared class. Fail-closed: unknown/missing -> CEO.
    if not risk_class or not risk_class.strip():
        return CEO_ALWAYS  # ponytail: fail-closed, no separate unknown bucket needed

    return _CLASS_GATE.get(risk_class.strip().lower(), CEO_ALWAYS)
