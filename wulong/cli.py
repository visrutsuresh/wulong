"""wulong CLI — thin subprocess dispatcher. No business logic lives here.
ponytail: stdlib argparse + subprocess only; rich/click deferred if ever needed.
"""
import argparse
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from wulong._root import ENV_VAR, RootNotFound, child_env, resolve_root

# Engine scripts live alongside this file in wulong/sync/.
_SYNC_DIR = os.path.join(os.path.dirname(__file__), "sync")

# Subcommand -> (script basename, usage hint)
_COMMANDS: dict[str, tuple[str, str]] = {
    "init":   ("wulong-init.py",          "wulong init [target] [--force] [--with-hooks]"),
    "doctor": ("vault-health-check.py",   "wulong doctor [--root PATH] [--require-all-axes]"),
    "gate":   ("check_gate_precondition.py",
               "wulong gate --change-id X --gate {nn3,nn4} [--root PATH]\n"
               "       wulong gate --verify RECEIPT --artifact PATH [--artifact PATH ...]\n"
               "       wulong gate --manifest --artifact PATH [--artifact PATH ...]"),
    "pulse":  ("session-pulse.py",        "wulong pulse [--change-id ID] [--root PATH] [--strict]"),
}

_HELP: dict[str, str] = {
    "init":   "Scaffold a wulong vault skeleton into a target directory. "
              "--with-hooks also writes .claude/settings.json wiring the Stop "
              "hook; remove it by deleting that file or its \"Stop\" entry.",
    "doctor": f"Run vault health scan (--root PATH, or set {ENV_VAR}, or run inside the vault).",
    "gate":   "Check NN#3/NN#4 gate preconditions for a change_id, or hash and "
              "verify the artifacts a receipt is bound to.",
    "pulse":  "Session-close pulse: verify-change + doc-consistency + audit.",
}


def _print_subcommand_help(cmd: str) -> None:
    usage, desc = _COMMANDS[cmd][1], _HELP[cmd]
    print(f"usage: {usage}\n\n{desc}\n\nRun without --help to pass args to the engine script.")


# init SCAFFOLDS a vault rather than scanning one, so its positional target is
# the answer to "which directory" and there is no root to resolve.
_NEEDS_ROOT = ("doctor", "gate", "pulse")

# `wulong gate --verify` and `--manifest` hash bytes the caller names and read no
# vault at all. Resolving a root for them would make offline verification fail
# outside a vault, which is the one place it most needs to work.
_ROOTLESS_GATE_FLAGS = ("--manifest", "--verify")


def _is_rootless(cmd: str, passthrough: list[str]) -> bool:
    return cmd == "gate" and any(
        tok in _ROOTLESS_GATE_FLAGS or tok.startswith("--verify=")
        for tok in passthrough
    )


# Flags on `doctor` whose NEXT token is that flag's value, not a positional vault
# path. --root is caught before the scan below ever runs; it is listed so that a
# future value-taking flag added without an entry here is a visible omission
# rather than a silent misread of that flag's argument as a vault.
_DOCTOR_VALUE_FLAGS = ("--root",)


def _explicit_root(cmd: str, passthrough: list[str]) -> str | None:
    """The vault the user named on the command line, or None if they named none.

    Scans the WHOLE passthrough. Testing only passthrough[0] meant any leading
    flag defeated positional detection, so `doctor --require-all-axes VAULT` got
    a resolved --root prepended and the engine's `args.root or args.vault` then
    scanned the INJECTED vault instead of the named one, with no warning.

    Still deliberately narrow on the positional: only `doctor` has one. A blanket
    "any bare word is a path" test would read the value of --change-id as a vault
    and skip injection on every pulse call.
    """
    for i, tok in enumerate(passthrough):
        if tok.startswith("--root="):
            return tok[len("--root="):]
        if tok == "--root":
            # A dangling --root has no value. Returning "" still counts as
            # user-named, so nothing is injected over it and argparse in the
            # engine reports the missing argument instead.
            return passthrough[i + 1] if i + 1 < len(passthrough) else ""
    if cmd != "doctor":
        return None
    skip_next = False
    for tok in passthrough:
        if skip_next:
            skip_next = False
        elif tok in _DOCTOR_VALUE_FLAGS:
            skip_next = True
        elif not tok.startswith("-"):
            return tok
    return None


def _dispatch(cmd: str, passthrough: list[str]) -> None:
    script = os.path.join(_SYNC_DIR, _COMMANDS[cmd][0])
    if not os.path.isfile(script):
        print(f"wulong {cmd}: engine script not found: {script}", file=sys.stderr)
        sys.exit(1)

    # Resolve ONCE here and hand the answer down, both as a flag the engine
    # parses and in the child environment so every grandchild inherits the same
    # vault. Letting each engine re-resolve is what put `wulong pulse --root B`
    # on vault B and three of its four children on site-packages.
    # A vault the USER named is never resolved over. Injecting a --root in front
    # of a positional the user wrote is itself a wrong-vault bug, because the
    # engine reads --root first.
    env = None
    if cmd in _NEEDS_ROOT and not _is_rootless(cmd, passthrough):
        root = _explicit_root(cmd, passthrough)
        if root is None:
            try:
                root = resolve_root(tool=f"wulong {cmd}")
            except RootNotFound as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(2)
            passthrough = ["--root", root] + passthrough
        if root:
            # Absolute, because the child may hand this variable to a grandchild
            # that does not share our working directory. The flag the user wrote
            # is left exactly as written; both name the same place from here.
            env = child_env(os.path.abspath(root))

    result = subprocess.run([sys.executable, script] + passthrough, env=env)
    sys.exit(result.returncode)


def main() -> None:
    # Manual first-arg dispatch so --help and all flags pass through cleanly.
    if len(sys.argv) >= 2 and sys.argv[1] in _COMMANDS:
        cmd = sys.argv[1]
        passthrough = sys.argv[2:]
        if passthrough in (["-h"], ["--help"]):
            _print_subcommand_help(cmd)
            sys.exit(0)
        _dispatch(cmd, passthrough)
        return  # unreachable (dispatch calls sys.exit)

    parser = argparse.ArgumentParser(
        prog="wulong",
        description="Wulong multi-agent governance framework CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'wulong COMMAND --help' for per-command usage.",
    )
    try:
        installed_version = _pkg_version("wulong")
    except PackageNotFoundError:  # run from a source tree without installing
        installed_version = "unknown (not installed)"
    parser.add_argument("--version", action="version", version=f"wulong {installed_version}")

    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")
    for name, help_text in _HELP.items():
        sub.add_parser(name, help=help_text)

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
