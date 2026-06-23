#!/usr/bin/env python3
"""
validate-notebook-count.py — read-only invariant check (stdlib only).

For each of:
  Meta/hermes/notebook.md
  Meta/metis/notebook.md

Parse frontmatter `observation_count`, count actual `## Observation ` headers
in the body, and assert they match.

Exit 0 if all notebooks pass.
Exit 1 if any notebook shows drift (with per-file report).

This is the exact check that would have auto-caught both of the 2026-05-29
bugs:
  - metis: observation_count=1 frontmatter vs 2 actual headers after first
    OBSERVE spawn
  - hermes: observation_count frozen at 2 vs 4 actual headers after coder
    backfilled without calling the recount helper

Usage:
  python3 validate-notebook-count.py            # check both notebooks
  python3 validate-notebook-count.py --verbose  # per-file detail even on PASS
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent.parent
NOTEBOOKS: list[Path] = [
    VAULT / "Meta" / "hermes" / "notebook.md",
    VAULT / "Meta" / "metis" / "notebook.md",
]

_FM_COUNT_RE = re.compile(r"^observation_count\s*:\s*(\d+)", re.MULTILINE)
_OBS_HEADER_RE = re.compile(r"^## Observation ", re.MULTILINE)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body). Frontmatter is between leading --- delimiters."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return text, ""
    fm = text[3:end].strip()
    body = text[end + 4:]
    return fm, body


def check_notebook(path: Path) -> tuple[bool, str]:
    """Return (ok, detail_line). ok=True means counts match (or file absent counts as ok=False)."""
    if not path.exists():
        return False, f"MISSING: {path}"

    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)

    m = _FM_COUNT_RE.search(fm)
    if m is None:
        # No observation_count in frontmatter — cannot validate; treat as WARN not FAIL
        return True, f"SKIP (no observation_count key): {path.relative_to(VAULT)}"

    expected = int(m.group(1))
    actual = len(_OBS_HEADER_RE.findall(body))

    if expected == actual:
        return True, f"PASS: {path.relative_to(VAULT)} — observation_count={expected} matches body count"

    return (
        False,
        (
            f"DRIFT: {path.relative_to(VAULT)} — "
            f"frontmatter observation_count={expected} but body has {actual} "
            f"'## Observation ' headers"
        ),
    )


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    results: list[tuple[bool, str]] = [check_notebook(p) for p in NOTEBOOKS]

    any_fail = any(not ok for ok, _ in results)

    for ok, detail in results:
        if not ok or verbose:
            print(detail)

    if any_fail:
        # Always print the full report on failure
        print("\nSUMMARY: observation_count drift detected — run the relevant")
        print("  Meta/sync/hermes-append-notebook.py  OR")
        print("  Meta/sync/metis-append-notebook.py")
        print("recount helper to reconcile before the next agent spawn.")
        return 1

    if verbose:
        print("\nSUMMARY: all notebooks consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
