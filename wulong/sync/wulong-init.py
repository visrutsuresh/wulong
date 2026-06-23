#!/usr/bin/env python3
"""wulong-init.py — scaffold a working wulong setup into a target directory.

Creates the vault skeleton (Meta/, .claude/, .wulong/) and copies .example overlay
files into place as real files. Skips any target file that already exists.

OVERLAY FILES (gitignored; bootstrapped from .example by this script):
  .env                       — env knobs (WULONG_ROOT, GITHUB_TOKEN, etc.)
  scrub-patterns.txt         — personal tokens for the scrub deny-list
  Meta/brain.md              — living world-state (personal project content)
  .wulong/projects.json      — per-project metrics config for compile-context.py

ENGINE FILES (tracked in git; generic, no personal data):
  Everything else — agent defs, sync scripts, Meta skeleton docs, playbooks.

WHEEL-VS-CLONE NOTE:
  This script locates .example templates via Path(__file__).parent.parent.parent
  (i.e., wulong/sync/ -> wulong/ -> repo root). This is correct when running
  from a git clone. A wheel install places the package in site-packages, where
  the repo root is not present. If you installed via pip without cloning, run
  init from inside the cloned repo instead. Future Phase F will package templates
  as importlib.resources data for full pip-install support.
  # ponytail: clone-only for now; upgrade path = importlib.resources in Phase F
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

# Engine root = the repo root (wulong/sync/ -> wulong/ -> root)
_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent


def _check_engine_root() -> bool:
    """Return True if engine root looks like a valid wulong clone (has .example files)."""
    return any(_ENGINE_ROOT.rglob("*.example"))


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

    if not _check_engine_root():
        print(
            f"ERROR: no .example template files found under {_ENGINE_ROOT}.\n"
            "wulong init must be run from inside a cloned wulong repo, not a\n"
            "wheel install. Clone the repo first:\n"
            "  git clone https://github.com/your-org/wulong\n"
            "  cd wulong && pip install -e . && wulong init",
            file=sys.stderr,
        )
        sys.exit(1)

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
        print("  Overlay files: none found.")

    print("Done. Set WULONG_ROOT to this path in your shell or .env.")


if __name__ == "__main__":
    main()
