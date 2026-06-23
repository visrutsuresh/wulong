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

TEMPLATE RESOLUTION (two paths, both correct):
  Wheel install: templates are packaged inside wulong.templates (importlib.resources).
  Editable/clone: templates are found by walking up from this file to the repo root
    and globbing *.example. The repo-root walk is the fallback when the package
    data path is empty or unavailable.
  # ponytail: two-path design; ceiling = importlib.resources for wheel,
  #   repo-root glob for editable. Upgrade path = none needed (this is the final form).
"""
import argparse
import importlib.resources
import shutil
import sys
from pathlib import Path

# Destination mapping for the flat template files (template name -> relative dest path).
# Keys are filenames inside wulong/templates/; values are dest paths relative to target.
_TEMPLATE_DEST: dict[str, str] = {
    "env.example": ".env",
    "scrub-patterns.txt.example": "scrub-patterns.txt",
    "brain.md.example": "Meta/brain.md",
    "projects.json.example": ".wulong/projects.json",
}

# Engine root for the editable/clone fallback: wulong/sync/ -> wulong/ -> repo root
_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent


def _templates_from_package() -> dict[str, bytes]:
    """Load templates from the installed package (wheel path).

    Returns a dict of {template_filename: file_bytes}. Empty if unavailable.
    """
    result: dict[str, bytes] = {}
    try:
        pkg = importlib.resources.files("wulong.templates")
        for name in _TEMPLATE_DEST:
            resource = pkg / name
            try:
                result[name] = resource.read_bytes()
            except (FileNotFoundError, TypeError):
                pass
    except (ModuleNotFoundError, AttributeError):
        pass
    return result


def _templates_from_repo() -> dict[str, bytes]:
    """Load templates from the repo root (editable/clone path).

    The flat template name is derived from the source .example file:
      .env.example         -> env.example
      scrub-patterns.txt.example -> scrub-patterns.txt.example
      Meta/brain.md.example -> brain.md.example
      .wulong/projects.json.example -> projects.json.example

    Returns a dict of {template_name: file_bytes} for templates found.
    """
    # Original repo paths for each flat template name
    _REPO_SOURCES: dict[str, Path] = {
        "env.example": _ENGINE_ROOT / ".env.example",
        "scrub-patterns.txt.example": _ENGINE_ROOT / "scrub-patterns.txt.example",
        "brain.md.example": _ENGINE_ROOT / "Meta" / "brain.md.example",
        "projects.json.example": _ENGINE_ROOT / ".wulong" / "projects.json.example",
    }
    result: dict[str, bytes] = {}
    for name, src in _REPO_SOURCES.items():
        if src.exists():
            result[name] = src.read_bytes()
    return result


def _resolve_templates() -> dict[str, bytes]:
    """Return templates from wheel package if available, else from repo root.

    Logs which path was used for transparency.
    """
    pkg_templates = _templates_from_package()
    if len(pkg_templates) == len(_TEMPLATE_DEST):
        return pkg_templates

    # Fallback: editable/clone install
    repo_templates = _templates_from_repo()
    return {**pkg_templates, **repo_templates}


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


def _copy_templates(target: Path, templates: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Write template data to target, using the dest map. Skip if dest exists.

    Returns (copied_descriptions, skipped_descriptions).
    """
    copied: list[str] = []
    skipped: list[str] = []
    for name, content in sorted(templates.items()):
        dest_rel = _TEMPLATE_DEST.get(name, name.removesuffix(".example"))
        dest = target / dest_rel
        if dest.exists():
            skipped.append(f"{name} -> {dest_rel}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            copied.append(f"{name} -> {dest_rel}")
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

    templates = _resolve_templates()
    if not templates:
        print(
            "ERROR: no overlay templates found.\n"
            "This should not happen with a correctly installed package.\n"
            "Try: pip install --force-reinstall wulong  or  pip install -e /path/to/wulong/clone",
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

    copied, skipped = _copy_templates(target, templates)
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
