#!/usr/bin/env python3
"""
agent_identity.py — Fail-closed slug->machine_id resolver for v34 persona rename.

Loads Meta/agent-aliases.json once and exposes resolve_to_machine_id(token)
which maps ANY of {machine_id, name_slug, persona_display} to the canonical
machine_id. Missing file / unknown token / malformed JSON -> raises RuntimeError.

ponytail: stdlib json only, no new deps. Single responsibility: identity bridge.
Upgrade path: swap JSON file for DB if >64 agents warrant indexed lookup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_VAULT_ROOT = _THIS_DIR.parent.parent
_ALIASES_PATH = _VAULT_ROOT / "Meta" / "agent-aliases.json"

# Hard-coded fallback gated slugs: protects the gate if aliases.json fails to load.
# Only the SINGLE gated worker (coder) and its Stage-2 slug need to be here.
# ponytail: hard-code rather than infer — fail-closed requires no dynamic lookup.
FALLBACK_GATED_SLUGS: frozenset[str] = frozenset({"coder", "head-forger"})

# Module-level cache so the file is read at most once per process.
_CACHE: dict[str, str] | None = None


def _load() -> dict[str, str]:
    """Load and cache the alias map.

    Returns a flat dict mapping every token (machine_id, name_slug,
    persona_display lowercased) to its machine_id.

    Raises RuntimeError on any load failure (fail-closed).
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    aliases_path = str(os.environ.get("AGENT_ALIASES_PATH", _ALIASES_PATH))
    try:
        with open(aliases_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(f"agent-aliases.json not found at {aliases_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"agent-aliases.json is malformed: {exc}") from exc

    agents = data.get("agents")
    if not isinstance(agents, list):
        raise RuntimeError("agent-aliases.json missing 'agents' list")

    mapping: dict[str, str] = {}
    for entry in agents:
        mid = entry.get("machine_id", "").strip().lower()
        slug = entry.get("name_slug", "").strip().lower()
        display = entry.get("persona_display", "").strip().lower()
        if not mid:
            raise RuntimeError(f"agent-aliases.json entry missing machine_id: {entry!r}")
        mapping[mid] = mid
        if slug:
            mapping[slug] = mid
        if display:
            mapping[display] = mid

    _CACHE = mapping
    return _CACHE


def resolve_to_machine_id(token: str) -> str:
    """Resolve a token (slug, machine_id, or persona_display) to its machine_id.

    Raises RuntimeError if aliases.json fails to load OR if token is unknown.
    Callers in the gate path treat RuntimeError as fail-closed (deny).
    """
    if not token or not token.strip():
        raise RuntimeError("resolve_to_machine_id: empty token")
    key = token.strip().lower()
    mapping = _load()
    if key not in mapping:
        raise RuntimeError(f"unknown agent token: {token!r}")
    return mapping[key]


def clear_cache() -> None:
    """Reset the in-process cache (test helper, not for production use)."""
    global _CACHE
    _CACHE = None
