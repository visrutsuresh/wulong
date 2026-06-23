"""wulong CLI — thin subprocess dispatcher. No business logic lives here.
ponytail: stdlib argparse + subprocess only; rich/click deferred to Phase F if needed.
"""
import argparse
import os
import sys

# Engine scripts live in wulong/sync/ beside this file.
_SYNC_DIR = os.path.join(os.path.dirname(__file__), "sync")

# Subcommand -> script basename (Phase D0: stubs only — scripts arrive in Phase C).
_DISPATCH: dict[str, str] = {
    "init":   "wulong-init.py",
    "doctor": "vault-health-check.py",
    "gate":   "check_gate_precondition.py",
    "pulse":  "session-pulse.py",
}

_NOT_YET = "(Phase D — not yet wired; scripts arrive in Phase C)"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wulong",
        description="Wulong multi-agent governance framework CLI.",
    )
    parser.add_argument("--version", action="version", version="wulong 0.1.0")
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    for name in _DISPATCH:
        sub.add_parser(name, help=f"Run {name} {_NOT_YET}")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)

    script = os.path.join(_SYNC_DIR, _DISPATCH[args.cmd])
    if not os.path.isfile(script):
        print(f"wulong {args.cmd}: not yet wired {_NOT_YET}")
        sys.exit(0)

    # Phase C: when the script lands, dispatch via subprocess so it runs in its own sys.path.
    import subprocess  # ponytail: deferred import — only reached post-Phase-C
    result = subprocess.run([sys.executable, script] + sys.argv[2:])
    sys.exit(result.returncode)
