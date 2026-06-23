#!/usr/bin/env python3
"""wulong-init.py — scaffold a working wulong setup into a target directory.

Creates the vault skeleton (Meta/, .claude/) and copies .example overlay files
into place as real files. Skips any target file that already exists.

Phase D: copies whatever .example files exist in the engine root.
Phase E: completes the full overlay file set (brain.md, user-profile.md, .env, etc).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

# Engine root = the installed package's grandparent (wulong/sync/ -> wulong/ -> root)
_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent


def _scaffold_dirs(target: Path) -> list[str]:
    """Create the canonical vault skeleton directories."""
    dirs = [
        ".claude/agents",
        ".claude/hooks",
        ".claude/skills",
        "Meta/receipts",
        "Meta/handoffs",
        "Meta/sync",
        "Meta/playbooks",
        "Meta/knowledge-base",
        "Meta/context",
        ".wulong",
    ]
    created = []
    for d in dirs:
        p = target / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created


def _copy_examples(target: Path) -> tuple[list[str], list[str]]:
    """Copy .example files from engine root to target, stripping .example suffix.

    Returns (copied, skipped).
    """
    copied: list[str] = []
    skipped: list[str] = []
    for example in sorted(_ENGINE_ROOT.rglob("*.example")):
        rel = example.relative_to(_ENGINE_ROOT)
        dest = target / str(rel).removesuffix(".example")
        if dest.exists():
            skipped.append(str(rel))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(example, dest)
            copied.append(str(rel) + " -> " + str(dest.relative_to(target)))
    return copied, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wulong init",
        description="Scaffold a wulong vault skeleton into a target directory.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Directory to scaffold into (default: current directory).",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        target.mkdir(parents=True)

    print(f"Initialising wulong vault at: {target}")

    created_dirs = _scaffold_dirs(target)
    if created_dirs:
        print(f"  Created {len(created_dirs)} directories:")
        for d in created_dirs:
            print(f"    {d}/")
    else:
        print("  Directories: all present, nothing created.")

    copied, skipped = _copy_examples(target)
    if copied:
        print(f"  Copied {len(copied)} overlay file(s):")
        for f in copied:
            print(f"    {f}")
    if skipped:
        print(f"  Skipped {len(skipped)} file(s) (already exist):")
        for f in skipped:
            print(f"    {f}")
    if not copied and not skipped:
        print("  Overlay files: none found (Phase E adds the full set).")

    print("Done. Set WULONG_ROOT to this path in your shell or .env.")


if __name__ == "__main__":
    main()
