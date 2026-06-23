#!/usr/bin/env python3
"""
fix-mermaid-newlines.py — Replace literal \\n with <br/> inside mermaid fences.

Operates ONLY on content between ```mermaid ... ``` fences.
Never touches anything outside a fence.

Usage:
    python3 Meta/sync/fix-mermaid-newlines.py            # dry-run (default)
    python3 Meta/sync/fix-mermaid-newlines.py --apply    # write files

ponytail: stdlib re + pathlib + argparse; no new deps.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent.parent
EXCLUDE_DIRS = {".git", "Private"}
# Machine audit-trail dirs: receipts and handoffs may contain prose that mentions
# ```mermaid as inline code examples, producing false-positive fence matches.
# ponytail: path-prefix exclude; upgrade to per-file fence-type check if needed.
EXCLUDE_PATH_PREFIXES = {"Meta/receipts", "Meta/handoffs", "Meta/playbooks"}

# Anchored on the language token; DOTALL so .* spans lines; non-greedy.
_FENCE_RE = re.compile(r"(```mermaid\b)(.*?)(```)", re.DOTALL)

BEFORE = "\\n"   # 2-char literal: backslash + n
AFTER  = "<br/>"


def _transform_fence(match: re.Match) -> tuple[str, int]:
    """Return (replacement_text, count_of_substitutions)."""
    open_tag, body, close_tag = match.group(1), match.group(2), match.group(3)
    new_body, n = body.replace(BEFORE, AFTER), body.count(BEFORE)
    return open_tag + new_body + close_tag, n


def process_file(path: Path, apply: bool) -> tuple[int, list[tuple[str, str]]]:
    """Return (total_replacements, [(before_label, after_label), ...]) for up to 3 samples."""
    text = path.read_text(encoding="utf-8")
    total = 0
    samples: list[tuple[str, str]] = []

    def _replacer(m: re.Match) -> str:
        nonlocal total
        replacement, n = _transform_fence(m)
        total += n
        if n and len(samples) < 3:
            # Capture the first substituted label for the sample display.
            body = m.group(2)
            for chunk in body.split(BEFORE):
                orig_label = chunk.split("\n")[-1].strip()[:60] if chunk else ""
                if orig_label:
                    samples.append((orig_label + BEFORE, orig_label + AFTER))
                    break
        return replacement

    new_text = _FENCE_RE.sub(_replacer, text)

    if apply and total > 0:
        path.write_text(new_text, encoding="utf-8")

    return total, samples


def iter_md_files(vault: Path) -> list[Path]:
    results = []
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault)
        parts = rel.parts
        if parts and parts[0] in EXCLUDE_DIRS:
            continue
        rel_str = str(rel)
        if any(rel_str.startswith(prefix) for prefix in EXCLUDE_PATH_PREFIXES):
            continue
        results.append(p)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write files (default: dry-run)")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"fix-mermaid-newlines — {mode}\n")

    files = iter_md_files(VAULT)
    grand_total = 0
    changed_files: list[tuple[Path, int, list[tuple[str, str]]]] = []

    for path in sorted(files):
        count, samples = process_file(path, apply=args.apply)
        if count:
            grand_total += count
            changed_files.append((path, count, samples))

    # Report
    for path, count, samples in changed_files:
        rel = path.relative_to(VAULT)
        print(f"  {rel}: {count} replacement(s)")
        for before, after in samples[:3]:
            print(f"      before: ...{before!r}")
            print(f"      after:  ...{after!r}")

    print(f"\nTotal: {grand_total} replacements across {len(changed_files)} file(s)")
    if not args.apply:
        print("(dry-run — nothing written; pass --apply to write)")


if __name__ == "__main__":
    main()
