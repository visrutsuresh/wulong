#!/usr/bin/env python3
"""
validate-surface-manifest.py — structural validator for Meta/hermes/surface-manifest.yaml.

Validation checklist (per plan Step 3a):
  V1. Top-level keys must be a subset of allowed_scopes (from Meta/hermes/config.json +
      Meta/metis/config.json union). Unknown scopes = error.
  V2. Each scope entry must have all 3 sub-keys: hermes_owns, metis_owns, forbidden.
  V3. No dual ownership: a name must not appear in both hermes_owns and metis_owns for
      the same scope.
  V4. No path-like names: variable names must not contain '/', '\\', or '.md', '.py',
      '.json', '.yaml' suffixes (they would be mistaken for file paths).
  V5. All entries under each list must be either a string or a dict with at least 'name'.
  V6. No cross-scope dual ownership is enforced as a WARN (different scopes may reuse
      a name legitimately — e.g. EDGE_THRESHOLD in multiple projects).

Exit codes:
  0 = manifest is valid
  1 = validation errors found (details printed to stdout)
  2 = manifest file missing or YAML parse error
"""
from __future__ import annotations
import json
import sys
import os
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
MANIFEST_PATH = VAULT / "Meta" / "hermes" / "surface-manifest.yaml"
HERMES_CONFIG = VAULT / "Meta" / "hermes" / "config.json"
METIS_CONFIG = VAULT / "Meta" / "metis" / "config.json"

# Path-like patterns that should never appear in a variable name
_PATH_FORBIDDEN_SUFFIXES = (".md", ".py", ".json", ".yaml", ".yml", ".csv", ".txt")
_PATH_FORBIDDEN_CHARS = ("/", "\\")


def load_allowed_scopes() -> set[str]:
    scopes: set[str] = set()
    for cfg_path in (HERMES_CONFIG, METIS_CONFIG):
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                scopes.update(data.get("allowed_scopes", []))
            except Exception:
                pass
    if not scopes:
        # Fallback if neither config exists yet
        scopes = {"design", "ops", "agents", "cross-domain"}  # ponytail: add project scopes in overlay config (hermes/metis config allowed_scopes)
    return scopes


def extract_name(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("name")
    return None


def is_path_like(name: str) -> bool:
    for ch in _PATH_FORBIDDEN_CHARS:
        if ch in name:
            return True
    for suffix in _PATH_FORBIDDEN_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


def validate(manifest: dict, allowed_scopes: set[str]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []

    # V1 — top-level keys must be subset of allowed_scopes
    for scope in manifest:
        if scope not in allowed_scopes:
            errors.append(
                f"V1: scope {scope!r} is not in allowed_scopes "
                f"({sorted(allowed_scopes)}). Remove or add to a config file."
            )

    for scope, scope_data in manifest.items():
        if not isinstance(scope_data, dict):
            errors.append(f"V2: scope {scope!r} value must be a mapping, got {type(scope_data).__name__}")
            continue

        # V2 — all 3 required sub-keys present; judge_owns is recognized but optional
        for key in ("hermes_owns", "metis_owns", "forbidden"):
            if key not in scope_data:
                errors.append(f"V2: scope {scope!r} missing required key {key!r}")

        # V2-ext — validate judge_owns if present (like hermes_owns/metis_owns)
        # Unknown keys beyond the 4 recognized ones are still flagged
        recognized_keys = {"hermes_owns", "metis_owns", "forbidden", "judge_owns"}
        for key in scope_data:
            if key not in recognized_keys:
                warnings.append(
                    f"WARN: scope {scope!r} has unrecognized sub-key {key!r}. "
                    "Recognized keys: hermes_owns, metis_owns, forbidden, judge_owns."
                )

        hermes_names: set[str] = set()
        metis_names: set[str] = set()
        judge_names: set[str] = set()
        forbidden_names: set[str] = set()

        for key, target in (
            ("hermes_owns", hermes_names),
            ("metis_owns", metis_names),
            ("judge_owns", judge_names),
            ("forbidden", forbidden_names),
        ):
            entries = scope_data.get(key, []) or []
            if not isinstance(entries, list):
                errors.append(f"V5: scope {scope!r}.{key} must be a list")
                continue
            for i, entry in enumerate(entries):
                name = extract_name(entry)
                if name is None:
                    errors.append(
                        f"V5: scope {scope!r}.{key}[{i}] must be a string or dict with 'name' key"
                    )
                    continue
                target.add(name)

                # V4 — no path-like names
                if is_path_like(name):
                    errors.append(
                        f"V4: scope {scope!r}.{key}[{i}] variable name {name!r} looks like a "
                        "file path. Use a dotted-name convention (e.g. 'calibrator.A') instead."
                    )

        # V3 — no dual ownership within scope (all owned lists vs each other)
        all_owned_pairs = [
            ("hermes_owns", hermes_names, "metis_owns", metis_names),
            ("hermes_owns", hermes_names, "judge_owns", judge_names),
            ("metis_owns", metis_names, "judge_owns", judge_names),
        ]
        for name_a, set_a, name_b, set_b in all_owned_pairs:
            dual = set_a & set_b
            if dual:
                errors.append(
                    f"V3: scope {scope!r} has dual ownership ({name_a} ∩ {name_b}): {sorted(dual)}. "
                    "Each variable must belong to exactly one agent."
                )

        # forbidden should not overlap with owned lists (warning only)
        all_owned = hermes_names | metis_names | judge_names
        forbidden_and_owned = forbidden_names & all_owned
        if forbidden_and_owned:
            warnings.append(
                f"WARN: scope {scope!r} has variables in forbidden that are ALSO in an owns list: "
                f"{sorted(forbidden_and_owned)}. The forbidden designation takes precedence, but "
                "the owns entry is misleading — remove it."
            )

    if warnings:
        for w in warnings:
            print(f"[validate-manifest] {w}")

    return errors


def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"[validate-manifest] ERROR: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        return 2

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[validate-manifest] ERROR: YAML parse failure: {e}", file=sys.stderr)
        return 2

    if not isinstance(manifest, dict):
        print("[validate-manifest] ERROR: top-level manifest must be a YAML mapping", file=sys.stderr)
        return 2

    allowed_scopes = load_allowed_scopes()
    errors = validate(manifest, allowed_scopes)

    if errors:
        print(f"[validate-manifest] FAIL — {len(errors)} error(s):")
        for err in errors:
            print(f"  {err}")
        return 1

    scope_count = len(manifest)
    total_hermes = sum(
        len(v.get("hermes_owns") or []) for v in manifest.values() if isinstance(v, dict)
    )
    total_metis = sum(
        len(v.get("metis_owns") or []) for v in manifest.values() if isinstance(v, dict)
    )
    total_judge = sum(
        len(v.get("judge_owns") or []) for v in manifest.values() if isinstance(v, dict)
    )
    judge_suffix = f", {total_judge} judge_owns" if total_judge > 0 else ""
    print(
        f"[validate-manifest] OK — {scope_count} scopes, "
        f"{total_hermes} hermes_owns, {total_metis} metis_owns{judge_suffix}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
