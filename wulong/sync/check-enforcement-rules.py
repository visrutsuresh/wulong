#!/usr/bin/env python3
"""
check-enforcement-rules.py — Warden rulebook validator.

Parses the ```wardens fenced block in Meta/enforcement-rules.md.
For each MECHANICAL row (mechanism is a filesystem path), confirms the
path exists on disk relative to the vault root.

IMPORTANT: this validator confirms the mechanism file EXISTS, not that
it still enforces its rule. A file could be emptied or gutted; the
validator cannot detect that. The summary footer states this caveat.

Exit code: always 0 (WARN-only — must never block session close).

Usage:
  python3 Meta/sync/check-enforcement-rules.py [vault_root]
  python3 Meta/sync/check-enforcement-rules.py --self-check

Public API (importable):
  check(repo_root=None) -> dict
    Returns {present: list, missing: list, llm_gate: list, gap: list, rows: list}
    so vault-health-check.py can call without subprocess.
"""

import re
import sys
import pathlib
import tempfile

# ponytail: stdlib only; no yaml/toml needed for pipe-separated block parsing

_WARDENS_RE = re.compile(r"```wardens\n(.*?)```", re.DOTALL)
_RULES_PATH = pathlib.Path(__file__).parent.parent / "enforcement-rules.md"


def _find_vault_root(start: pathlib.Path) -> pathlib.Path:
    current = start.resolve()
    for _ in range(20):
        if (current / "CLAUDE.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(f"CLAUDE.md not found walking up from {start}")


def _parse_wardens(text: str) -> list[dict]:
    """Parse the wardens block into a list of row dicts."""
    m = _WARDENS_RE.search(text)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        rows.append({
            "rule_id":   parts[0] if len(parts) > 0 else "",
            "statement": parts[1] if len(parts) > 1 else "",
            "mechanism": parts[2] if len(parts) > 2 else "",
            "action":    parts[3] if len(parts) > 3 else "",
            "source":    parts[4] if len(parts) > 4 else "",
        })
    return rows


def _classify(mechanism: str) -> str:
    """Return 'path', 'llm_gate', or 'gap' for a mechanism string."""
    m = mechanism.strip()
    if m.upper() == "GAP":
        return "gap"
    if m.upper().startswith("LLM-GATE:"):
        return "llm_gate"
    # ponytail: filesystem path heuristic — contains "/" and no LLM-GATE: prefix
    if "/" in m:
        return "path"
    return "gap"


def check(repo_root: pathlib.Path | None = None) -> dict:
    """
    Validate the wardens block in enforcement-rules.md.

    Returns:
      present   — list of (rule_id, path) for paths that exist
      missing   — list of (rule_id, path) for paths not on disk
      llm_gate  — list of (rule_id, mechanism) for LLM-GATE rows
      gap       — list of (rule_id,) for GAP rows
      rows      — raw row dicts
    """
    if repo_root is None:
        repo_root = _find_vault_root(pathlib.Path(__file__).parent)

    rules_file = repo_root / "Meta" / "enforcement-rules.md"
    if not rules_file.exists():
        return {"present": [], "missing": [], "llm_gate": [], "gap": [], "rows": []}

    text = rules_file.read_text(encoding="utf-8", errors="replace")
    rows = _parse_wardens(text)

    present = []
    missing = []
    llm_gate = []
    gap = []

    for row in rows:
        kind = _classify(row["mechanism"])
        rid = row["rule_id"]
        mech = row["mechanism"]

        if kind == "path":
            full = repo_root / mech.lstrip("/")
            if full.exists():
                present.append((rid, mech))
            else:
                missing.append((rid, mech))
        elif kind == "llm_gate":
            llm_gate.append((rid, mech))
        else:
            gap.append((rid,))

    return {
        "present": present,
        "missing": missing,
        "llm_gate": llm_gate,
        "gap": gap,
        "rows": rows,
    }


def _print_report(result: dict) -> None:
    present = result["present"]
    missing = result["missing"]
    llm_gate = result["llm_gate"]
    gap = result["gap"]
    rows = result["rows"]

    for rid, path in present:
        print(f"OK       [{rid}] {path}")
    for rid, path in missing:
        print(f"MISSING  [{rid}] {path}")
    for rid, mech in llm_gate:
        print(f"INFO     [{rid}] {mech} (LLM-gate — not path-validated)")
    for (rid,) in gap:
        print(f"INFO     [{rid}] GAP — no mechanical enforcer yet")

    N = len(present) + len(missing)
    M = len(present)
    K = len(missing)
    L = len(llm_gate)
    G = len(gap)
    print(
        f"\nENFORCEMENT-RULES: {N} mechanical wardens, {M} present, {K} MISSING;"
        f" {L} LLM-gate, {G} gap"
    )
    print(
        "NOTE: presence of the enforcer file is confirmed, NOT that it still"
        " enforces its rule. A gutted or renamed mechanism is a separate audit step."
    )


def _self_check() -> None:
    """
    Hermetic self-test. Seeds a temp dir, asserts bucketing is correct.
    Exits 0 on PASS, 1 on FAIL.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "CLAUDE.md").write_text("# fake")
        (root / "Meta").mkdir()

        # Create the one "present" mechanism file
        (root / "Meta").mkdir(exist_ok=True)
        present_path = "Meta/sync/real-hook.py"
        (root / "Meta" / "sync").mkdir(parents=True, exist_ok=True)
        (root / "Meta" / "sync" / "real-hook.py").write_text("# hook")

        # Write a mini wardens block
        wardens_md = f"""\
---
type: meta
---
```wardens
present-rule | A present rule. | {present_path} | BLOCK | NN01
missing-rule | A missing rule. | Meta/sync/ghost.py | WARN | NN02
llm-rule | An LLM-gated rule. | LLM-GATE:contrarian | BLOCK | NN03
gap-rule | A gap rule. | GAP | N/A | NN04
```
"""
        (root / "Meta" / "enforcement-rules.md").write_text(wardens_md)

        result = check(repo_root=root)

        assert len(result["present"]) == 1, \
            f"FAIL: expected 1 present, got {result['present']}"
        assert result["present"][0][0] == "present-rule", \
            f"FAIL: present rule_id wrong: {result['present']}"

        assert len(result["missing"]) == 1, \
            f"FAIL: expected 1 missing, got {result['missing']}"
        assert result["missing"][0][0] == "missing-rule", \
            f"FAIL: missing rule_id wrong: {result['missing']}"

        assert len(result["llm_gate"]) == 1, \
            f"FAIL: expected 1 llm_gate, got {result['llm_gate']}"
        assert result["llm_gate"][0][0] == "llm-rule", \
            f"FAIL: llm_gate rule_id wrong: {result['llm_gate']}"

        assert len(result["gap"]) == 1, \
            f"FAIL: expected 1 gap, got {result['gap']}"
        assert result["gap"][0][0] == "gap-rule", \
            f"FAIL: gap rule_id wrong: {result['gap']}"

    print("SELF-CHECK PASS")
    sys.exit(0)


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        repo_root = pathlib.Path(sys.argv[1])
    else:
        repo_root = _find_vault_root(pathlib.Path(__file__).parent)

    result = check(repo_root=repo_root)
    _print_report(result)
    sys.exit(0)  # always 0 — WARN-only
