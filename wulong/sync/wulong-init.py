#!/usr/bin/env python3
"""wulong-init.py — scaffold a working wulong setup into a target directory.

Creates the vault skeleton (Meta/, .claude/, .wulong/), installs the agent
payload, and copies .example overlay files into place as real files.

PAYLOAD (shipped inside the wheel at wulong/payload/; installed by this script):
  .claude/agents/*.md        the agent definitions
  .claude/hooks/*.py         the delivery-gate hook
  .claude/skills/*/SKILL.md  the skills agent definitions cite by literal path
  CLAUDE.md                  the governance policy the agents read

OPT-IN WIRING (written ONLY with --with-hooks; off by default):
  .claude/settings.json      wires the Stop hook above into Claude Code

  Off by default because what it installs is code that runs on your machine
  every time a turn ends. What wulong contributes here is the wiring itself:
  the right event, a path that survives `pip install -U`, and a timeout above
  the hook's real runtime. Getting any of those wrong produces a silent
  nothing, not an error.

  Hooks are not part of wulong's enforcement chain. The binding gate is
  `wulong gate` and the test suite, and wulong ships nothing that runs them
  for you: no pre-commit, no CI job, no scheduler. Wiring those is your step.
  The Stop hook itself is not passive once wired: it BLOCKS your own delivery
  on an em dash and feeds a rewrite reason back to the model.

  To remove it: delete .claude/settings.json, or delete its "Stop" entry.
  Re-running init cannot remove it, because init never clobbers.

OVERLAY FILES (personal data; bootstrapped from .example by this script):
  .env                       — env knobs (WULONG_ROOT, GITHUB_TOKEN, etc.)
  scrub-patterns.txt         — personal tokens for the scrub deny-list
  Meta/brain.md              — living world-state (personal project content)
  .wulong/projects.json      — per-project metrics config for compile-context.py

NOT COPIED: the 53 engine scripts in wulong/sync/. A second copy in the target
would go stale on `pip install -U`, and because this script never clobbers, the
stale copy would win forever. One copy, in site-packages, reached via the CLI.

NEVER CLOBBERS. A destination that already exists is skipped, so a user edit
survives re-running init. `--force` overwrites. The existence check FAILS CLOSED:
if the filesystem cannot answer whether the destination exists, init aborts
rather than writing, because the write would silently destroy a real file.

TEMPLATE RESOLUTION (two paths, both correct):
  Wheel install: templates are packaged inside wulong.templates (importlib.resources).
  Editable/clone: templates are found by walking up from this file to the repo root
    and globbing *.example. The repo-root walk is the fallback when the package
    data path is empty or unavailable.
  # ponytail: two-path design; ceiling = importlib.resources for wheel,
  #   repo-root glob for editable. Upgrade path = none needed (this is the final form).

PAYLOAD RESOLUTION (one path, deliberately): the payload lives INSIDE the package
at wulong/payload/, so a plain __file__-relative path resolves it identically for
a wheel install and an editable clone. No importlib.resources branch is needed.
  # ponytail: rung 5, one line. Ceiling = payload must be an unpacked directory,
  #   which it already must be, since the CLI runs these scripts by path.
"""
import argparse
import errno
import importlib.resources
import json
import os
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

# The payload ships inside the package, one level up from wulong/sync/.
_PAYLOAD_ROOT = Path(__file__).resolve().parent.parent / "payload"

# --- Opt-in hook wiring (--with-hooks) -------------------------------------
#
# WHICH FILE, and why the other two candidates are wrong here.
# Claude Code reads three settings files, in ascending precedence:
#   ~/.claude/settings.json          user level, every project on the machine
#   <target>/.claude/settings.json   project level, committed, shared
#   <target>/.claude/settings.local.json  project local, personal, gitignored
# We write the PROJECT level file.
#   * The user-level file is out of this tool's blast radius. init writes nothing
#     outside `target`, and a command pointing into ONE vault, fired in every
#     unrelated project on the machine, is the wrong-vault class wulong/_root.py
#     exists to prevent. It is also the most-edited config a user owns.
#   * The project-LOCAL file is the personal override layer and is gitignored by
#     convention. What wulong ships here is the wiring knowledge, so burying it
#     where a teammate's clone cannot see it delivers the vault and the hook
#     script with no wiring at all. It also has HIGHER precedence, so writing
#     there would silently override the user's own project settings instead of
#     sitting beside them.
#   * The project-level file is also the one wulong/sync/cerebrum-health.py
#     already reads, so choosing it makes an existing check meaningful.
HOOK_SETTINGS_REL = ".claude/settings.json"
HOOK_SCRIPT_REL = ".claude/hooks/stop-slop-hook.py"

# The event the shipped hook is written for. The hook itself declares this as a
# module-level HOOK_EVENT constant; tests/test_hook_wiring.py reads that constant
# by AST and asserts it equals this one, so the two cannot drift.
# Wiring the wrong event used to produce a silent nothing. The hook now reads
# the incoming event name, refuses one it does not handle, and records the name
# it was handed, so a mis-wiring here surfaces at `wulong doctor` axis I.
# ponytail: rung 5, one constant plus a test, rather than an AST read at runtime.
#   Ceiling = the two constants must agree; upgrade path = the test that pins it.
HOOK_EVENT = "Stop"

# INVOCATION FORM: interpreter plus absolute path.
#   * Interpreter, so the executable bit is irrelevant. The hook is mode 0644 in
#     the repo and in the payload, init copies bytes (not modes), and wheels do
#     not reliably preserve the exec bit on package data. A bare-path command
#     would depend on all three going right.
#   * Absolute, because that is the only form with production evidence in this
#     project. A path that does not resolve makes the hook silently never fire,
#     which is precisely the failure this change exists to make visible.
#     Cost of absolute: the committed file names one machine's path. Moving or
#     re-cloning the vault leaves a stale command, and `wulong doctor` axis I
#     reports it as wired-but-never-fired. Repoint it by editing this one string.
HOOK_INTERPRETER = "python3"

# NO MATCHER KEY. A matcher selects a tool name, and the Stop event carries no
# tool, so a matcher here would be decoration that reads as filtering. The hook
# does its own filtering (last assistant turn only).
# TIMEOUT 10 SECONDS. The hook reads one transcript file and scans codepoints;
# it spawns nothing on the delivery path. A timeout under the real runtime kills
# the hook reporting nothing, which is a silent nothing with no record at all.
HOOK_TIMEOUT_SECONDS = 10


class ClobberRisk(RuntimeError):
    """Raised when init cannot prove a destination is absent before writing."""


def _exists_or_fail(dest: Path) -> bool:
    """Return True/False for destination existence, or raise rather than guess.

    Path.exists() delegates to os.path.exists(), which swallows every OSError and
    returns False. That turns "I could not stat this file" into "this file is not
    there", and the caller then WRITES -- destroying a user's .env or a hand-edited
    agent definition. os.lstat is called directly so only ENOENT and ENOTDIR mean
    absent; anything else (EACCES, EIO, ELOOP, ENAMETOOLONG) aborts the run.
    """
    try:
        os.lstat(dest)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise ClobberRisk(
            f"cannot determine whether {dest} already exists ({exc.strerror}); "
            f"refusing to write, because overwriting it would destroy your data. "
            f"Fix the permissions or remove the path, then re-run."
        ) from exc
    return True


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


# The only suffixes the package-data globs in pyproject.toml put in the wheel.
# Init must install exactly what ships and nothing else, or a file present in a
# clone but absent from the wheel would make `pip install` and `pip install -e`
# produce different vaults. tests/test_wheel_payload.py asserts the two sets match.
_PAYLOAD_SUFFIXES = frozenset({".md", ".py"})


def payload_files() -> list[Path]:
    """Every shipped payload file, as paths relative to the payload root."""
    if not _PAYLOAD_ROOT.is_dir():
        return []
    return sorted(
        p.relative_to(_PAYLOAD_ROOT)
        for p in _PAYLOAD_ROOT.rglob("*")
        if p.is_file()
        and p.suffix in _PAYLOAD_SUFFIXES
        and "__pycache__" not in p.parts
    )


def hook_command(target: Path) -> str:
    """The exact `command` string written into the settings file."""
    return f"{HOOK_INTERPRETER} {(target / HOOK_SCRIPT_REL).as_posix()}"


def hook_settings(target: Path) -> dict:
    """The settings object `--with-hooks` writes. One event, one command."""
    return {
        "hooks": {
            HOOK_EVENT: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_command(target),
                            "timeout": HOOK_TIMEOUT_SECONDS,
                        }
                    ]
                }
            ]
        }
    }


def _wire_hooks(target: Path, force: bool) -> tuple[str, str]:
    """Write the opt-in settings file. Returns (status, detail).

    status is one of:
      written   the settings file was created and the hook is wired
      exists    a settings file was already there and was NOT touched
      no-script the hook script is not present, so nothing was wired

    NEVER CLOBBERS, like every other write in this file. The difference is that a
    skipped payload file leaves the user with the shipped default, whereas a
    skipped settings file leaves the hook UNWIRED while the flag they typed says
    otherwise. That is silent, and the common case: anyone who has configured
    Claude Code permissions already has this file. So the collision is reported
    by NAME with the exact JSON to paste, never folded into a count.
    """
    if not _exists_or_fail(target / HOOK_SCRIPT_REL):
        return "no-script", HOOK_SCRIPT_REL
    dest = target / HOOK_SETTINGS_REL
    if _exists_or_fail(dest) and not force:
        return "exists", str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(hook_settings(target), indent=2) + "\n", encoding="utf-8")
    return "written", str(dest)


def _report_hook_wiring(status: str, detail: str, target: Path) -> None:
    """Say plainly whether the hook is wired, and what to do when it is not."""
    if status == "written":
        print(f"  Wired the delivery gate: {detail}")
        print(f"    event {HOOK_EVENT}, timeout {HOOK_TIMEOUT_SECONDS}s, "
              f"command: {hook_command(target)}")
        print("    It BLOCKS your own delivery on an em dash and feeds a rewrite "
              "reason back to the model.")
        print("    To remove it: delete that file, or delete its "
              f"\"{HOOK_EVENT}\" entry.")
        return
    if status == "exists":
        print(f"  HOOK NOT WIRED. {detail} already exists and init never overwrites it.")
        print("    Your --with-hooks flag had no effect on that file. To finish by "
              f"hand, merge this into its \"hooks\" object:")
        for line in json.dumps(hook_settings(target)["hooks"], indent=2).splitlines():
            print(f"      {line}")
        print("    Or re-run with --force to overwrite the whole settings file.")
        return
    print(f"  HOOK NOT WIRED. {detail} is not present in this installation, "
          "so there is nothing to point a settings file at.")


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


def _install_payload(target: Path, force: bool) -> tuple[list[str], list[str]]:
    """Copy the agent payload into target. Returns (copied, skipped)."""
    copied: list[str] = []
    skipped: list[str] = []
    for rel in payload_files():
        dest = target / rel
        if _exists_or_fail(dest) and not force:
            skipped.append(str(rel))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((_PAYLOAD_ROOT / rel).read_bytes())
        copied.append(str(rel))
    return copied, skipped


def _copy_templates(
    target: Path, templates: dict[str, bytes], force: bool
) -> tuple[list[str], list[str]]:
    """Write template data to target, using the dest map. Skip if dest exists.

    Returns (copied_descriptions, skipped_descriptions).
    """
    copied: list[str] = []
    skipped: list[str] = []
    for name, content in sorted(templates.items()):
        dest_rel = _TEMPLATE_DEST.get(name, name.removesuffix(".example"))
        dest = target / dest_rel
        if _exists_or_fail(dest) and not force:
            skipped.append(f"{name} -> {dest_rel}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            copied.append(f"{name} -> {dest_rel}")
    return copied, skipped


def _ensure_gitignore(target: Path) -> list[str]:
    """Add a .gitignore entry for every overlay file init creates.

    Not cosmetic. `.env` holds GITHUB_TOKEN and `scrub-patterns.txt` is a list of
    the exact strings the user never wants published, so committing either is the
    leak the scrub tooling exists to prevent. Appends only; an existing .gitignore
    is never rewritten, and entries already present are not duplicated.
    """
    wanted = sorted(set(_TEMPLATE_DEST.values()))
    gitignore = target / ".gitignore"
    existing: list[str] = []
    if _exists_or_fail(gitignore):
        existing = gitignore.read_text(encoding="utf-8").splitlines()
    present = {line.strip().lstrip("/") for line in existing}
    missing = [w for w in wanted if w not in present]
    if not missing:
        return []
    block = ["", "# wulong overlay files (personal data; never commit)"] + missing
    prefix = "" if (not existing or existing[-1] == "") else "\n"
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(prefix + "\n".join(block) + "\n")
    return missing


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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing payload and overlay files instead of skipping them.",
    )
    parser.add_argument(
        "--with-hooks",
        action="store_true",
        help=(
            f"Also write {HOOK_SETTINGS_REL} wiring the {HOOK_EVENT} hook. Off by "
            "default: this installs code that runs on a stranger's machine every "
            "time a turn ends. To remove it later, delete that file or delete its "
            f"\"{HOOK_EVENT}\" entry."
        ),
    )
    args = parser.parse_args()

    # The last Path.exists() in this file used to sit here, and it had the same
    # fail-open shape as the one _exists_or_fail replaced: an unstatable target
    # read as absent, then mkdir raised and the user got a traceback instead of
    # the designed error path. Nothing is destroyed by that (mkdir does not
    # overwrite), but a tool whose contract is "abort rather than guess" must
    # abort here too.
    target = Path(args.target).resolve()
    try:
        if not _exists_or_fail(target):
            target.mkdir(parents=True)
    except ClobberRisk as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: cannot create {target}: {exc.strerror}", file=sys.stderr)
        sys.exit(1)

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

    try:
        payload_copied, payload_skipped = _install_payload(target, args.force)
        copied, skipped = _copy_templates(target, templates, args.force)
        ignored = _ensure_gitignore(target)
        hook_status, hook_detail = (
            _wire_hooks(target, args.force) if args.with_hooks else ("declined", "")
        )
    except ClobberRisk as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if payload_copied:
        print(f"  Installed {len(payload_copied)} payload file(s).")
    if payload_skipped:
        print(f"  Kept {len(payload_skipped)} existing payload file(s) (use --force to overwrite).")
    if not payload_copied and not payload_skipped:
        print("  Payload: not found in this installation.")

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

    if ignored:
        print(f"  Added {len(ignored)} overlay entr(y/ies) to .gitignore.")

    if hook_status == "declined":
        print(f"  Hooks: not wired (pass --with-hooks to write {HOOK_SETTINGS_REL}).")
    else:
        _report_hook_wiring(hook_status, hook_detail, target)

    print("Done. Set WULONG_ROOT to this path in your shell or .env.")


if __name__ == "__main__":
    main()
