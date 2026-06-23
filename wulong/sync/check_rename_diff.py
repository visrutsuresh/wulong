#!/usr/bin/env python3
"""
check_rename_diff.py — Stage-2 allowlist guard for agent def persona renames.

Given a unified diff of a single agent definition file, HARD-FAILs if any
changed line is outside the allowed frontmatter keys {name, description, persona}.
Also HARD-FAILs if any changed line lies outside the YAML frontmatter block.

Usage:
  # Pipe a git diff:
  git diff HEAD -- .claude/agents/coder.md | python3 check_rename_diff.py

  # Pass a diff file:
  python3 check_rename_diff.py path/to/rename.diff

  # Test self-demo:
  python3 check_rename_diff.py --demo

Exit codes:
  0 — all changed lines are within the allowed frontmatter key set (PASS)
  1 — any changed line violates the allowlist (HARD-FAIL)
  2 — usage/parse error

change_id: v34-agent-full-persona-rename
"""
from __future__ import annotations

import re
import sys
from typing import Optional

_ALLOWED_KEYS: frozenset[str] = frozenset({"name", "description", "persona"})

# Matches a YAML frontmatter key: optional leading spaces, then identifier, then colon.
_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:")


def _extract_key(line: str) -> Optional[str]:
    """Return the frontmatter key from a changed line, or None if not a key-value pair."""
    m = _KEY_RE.match(line)
    return m.group(1).strip().lower() if m else None


def check_diff(diff_text: str) -> tuple[bool, list[str]]:
    """Inspect a unified diff for allowlist violations.

    Returns (pass: bool, violations: list[str]).
    A violation is any changed (+/-) content line that either:
    - lies outside the YAML frontmatter block (between --- delimiters), or
    - is inside the frontmatter but touches a key not in _ALLOWED_KEYS.

    Hunk headers (@@) and context lines (space-prefixed) are not checked.
    """
    violations: list[str] = []
    in_frontmatter = False
    frontmatter_seen = 0  # counts how many --- delimiter lines we've crossed

    for raw_line in diff_text.splitlines():
        # Hunk header: reset location context (but don't touch frontmatter state).
        if raw_line.startswith("@@"):
            continue
        # Diff metadata lines (index, ---/+++ file headers).
        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            continue
        if raw_line.startswith("diff ") or raw_line.startswith("index "):
            continue

        # Changed content lines start with + or -.
        if not (raw_line.startswith("+") or raw_line.startswith("-")):
            # Context line: track frontmatter delimiters.
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            if content.strip() == "---":
                frontmatter_seen += 1
                in_frontmatter = (frontmatter_seen == 1)
            continue

        # Changed content line (+/-).
        content = raw_line[1:]  # strip the +/-

        # Track frontmatter delimiters in changed lines too.
        if content.strip() == "---":
            frontmatter_seen += 1
            in_frontmatter = (frontmatter_seen == 1)
            continue

        if not in_frontmatter:
            violations.append(
                f"HARD-FAIL: changed line outside frontmatter block: {raw_line!r}"
            )
            continue

        key = _extract_key(content)
        if key is None:
            # Non-key line inside frontmatter (e.g. empty line, comment) — skip.
            continue
        if key not in _ALLOWED_KEYS:
            violations.append(
                f"HARD-FAIL: changed frontmatter key '{key}' not in "
                f"allowed set {sorted(_ALLOWED_KEYS)}: {raw_line!r}"
            )

    return len(violations) == 0, violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if "--demo" in args:
        return _demo()

    if args:
        try:
            with open(args[0], encoding="utf-8") as fh:
                diff_text = fh.read()
        except OSError as exc:
            print(f"error: cannot read diff file: {exc}", file=sys.stderr)
            return 2
    else:
        if sys.stdin.isatty():
            print("usage: check_rename_diff.py [diff_file | --demo]", file=sys.stderr)
            print("       or pipe a unified diff via stdin", file=sys.stderr)
            return 2
        diff_text = sys.stdin.read()

    passed, violations = check_diff(diff_text)
    if passed:
        print("PASS: all changed lines are within allowed frontmatter keys {name, description, persona}")
        return 0
    for v in violations:
        print(v)
    return 1


def _demo() -> int:
    """Self-test: run the two required demo cases and print results."""
    print("=== demo: PASS case (name/description only diff) ===")
    good_diff = """\
--- a/.claude/agents/coder.md
+++ b/.claude/agents/coder.md
@@ -1,4 +1,4 @@
 ---
-name: coder
+name: head-forger
-description: Python engineer
+description: The Head Forger (coder). Python engineer
 persona: The Head Forger
 ---
"""
    ok, errs = check_diff(good_diff)
    print("Result:", "PASS" if ok else "FAIL", errs or "")

    print()
    print("=== demo: HARD-FAIL case (--agent line in body changed) ===")
    bad_diff = """\
--- a/.claude/agents/coder.md
+++ b/.claude/agents/coder.md
@@ -1,4 +1,4 @@
 ---
-name: coder
+name: head-forger
 description: Python engineer
 ---

-Run: python3 spawn_gate.py --worker coder
+Run: python3 spawn_gate.py --worker head-forger
"""
    ok2, errs2 = check_diff(bad_diff)
    print("Result:", "PASS" if ok2 else "HARD-FAIL")
    for e in errs2:
        print(" ", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
