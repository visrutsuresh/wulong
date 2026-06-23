"""wulong CLI — thin subprocess dispatcher. No business logic lives here.
ponytail: stdlib argparse + subprocess only; rich/click deferred if ever needed.
"""
import argparse
import os
import subprocess
import sys

# Engine scripts live alongside this file in wulong/sync/.
_SYNC_DIR = os.path.join(os.path.dirname(__file__), "sync")

# Subcommand -> (script basename, usage hint)
_COMMANDS: dict[str, tuple[str, str]] = {
    "init":   ("wulong-init.py",          "wulong init [target]"),
    "doctor": ("vault-health-check.py",   "wulong doctor [vault-path]"),
    "gate":   ("check_gate_precondition.py", "wulong gate --change-id X --gate {nn3,nn4}"),
    "pulse":  ("session-pulse.py",        "wulong pulse [--change-id ID] [--strict]"),
}

_HELP: dict[str, str] = {
    "init":   "Scaffold a wulong vault skeleton into a target directory.",
    "doctor": "Run vault health scan (set WULONG_ROOT to your vault path).",
    "gate":   "Check NN#3/NN#4 gate preconditions for a change_id.",
    "pulse":  "Session-close pulse: verify-change + doc-consistency + audit.",
}


def _print_subcommand_help(cmd: str) -> None:
    usage, desc = _COMMANDS[cmd][1], _HELP[cmd]
    print(f"usage: {usage}\n\n{desc}\n\nRun without --help to pass args to the engine script.")


def _dispatch(cmd: str, passthrough: list[str]) -> None:
    script = os.path.join(_SYNC_DIR, _COMMANDS[cmd][0])
    if not os.path.isfile(script):
        print(f"wulong {cmd}: engine script not found: {script}", file=sys.stderr)
        sys.exit(1)
    result = subprocess.run([sys.executable, script] + passthrough)
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
    parser.add_argument("--version", action="version", version="wulong 0.1.0")

    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")
    for name, help_text in _HELP.items():
        sub.add_parser(name, help=help_text)

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
