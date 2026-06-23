"""test_imports.py — mechanical guard: every shipped wulong/sync/ module imports cleanly.

Uses importlib (real import, not py_compile) to catch missing deps at CI time.
Add any new module to MODULES as part of the same commit that adds the script.
"""
import importlib.util
import pathlib
import sys

import pytest

SYNC_DIR = pathlib.Path(__file__).resolve().parent.parent / "wulong" / "sync"

# Explicit list of all 53 shipped modules. Update when a script is added or removed.
MODULES = [
    "agent_identity",
    "apply-model-tiers",
    "automerge_gate",
    "capture-feedback",
    "cerebrum-health",
    "cerebrum-search",
    "changelog-append",
    "check_gate_precondition",
    "check_rename_diff",
    "check-compliance",
    "check-doc-consistency",
    "check-enforcement-rules",
    "compile-context",
    "drift-scan",
    "enforcement-sweep",
    "fix-mermaid-newlines",
    "health-scan",
    "hermes-append-notebook",
    "hermes-write-proposal",
    "inflight",
    "judge-append-notebook",
    "judge-flip-readiness",
    "judge-score",
    "metis-30day-review",
    "metis-append-notebook",
    "metis-write-proposal",
    "observer-accept-rate",
    "observer-apply",
    "observer-disposition",
    "post-write-trigger",
    "query-receipts",
    "recompute-doc-baseline",
    "research_router",
    "research-propose",
    "scheduled-strict-check",
    "seed-canaries",
    "session-close-audit",
    "session-guard",
    "session-pulse",
    "session-start-gate",
    "slop-scrub",
    "spawn_gate",
    "synthesize-lessons",
    "trace-change-chain",
    "update-agent-kb",
    "validate-notebook-count",
    "validate-receipt-graph",
    "validate-receipts",
    "validate-surface-manifest",
    "vault-fresh",
    "vault-health-check",
    "verify-change",
    "wulong-init",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    """Assert each sync/ module can be imported without error."""
    pyfile = SYNC_DIR / f"{module_name}.py"
    assert pyfile.exists(), f"Missing script: {pyfile}"

    # Use a fresh module name to avoid caching conflicts between test runs
    safe_name = module_name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe_name, pyfile)
    assert spec is not None, f"Could not build spec for {module_name}"
    mod = importlib.util.module_from_spec(spec)
    # Add to sys.modules before exec so sibling imports resolve correctly
    sys.modules[safe_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except SystemExit:
        pass  # scripts that run on import (argparse main at bottom) may exit
    except ImportError as exc:
        pytest.fail(f"{module_name} raised ImportError: {exc}")
    finally:
        sys.modules.pop(safe_name, None)


def test_module_list_matches_disk() -> None:
    """Guard: MODULES list must match *.py files on disk exactly (no phantom or missing)."""
    on_disk = {p.stem for p in SYNC_DIR.glob("*.py")}
    in_list = set(MODULES)
    # Normalise hyphenated names (disk stems == module names, both use hyphens)
    missing_from_list = on_disk - in_list
    extra_in_list = in_list - on_disk
    errors = []
    if missing_from_list:
        errors.append(f"Scripts on disk not in MODULES list: {sorted(missing_from_list)}")
    if extra_in_list:
        errors.append(f"Names in MODULES list not on disk: {sorted(extra_in_list)}")
    assert not errors, "\n".join(errors)
