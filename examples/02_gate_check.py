"""Example 02: wulong gate check on a fresh skeleton.

Initialises a temp vault, then runs `wulong gate --change-id example-2026
--gate nn3`. With no contrarian receipt in the skeleton, the gate returns
REFUSE (exit 1). This is the correct, expected behaviour: no unreviewed
change gets through.

No network calls. No fixtures. Deterministic output committed at
tests/expected/ex02.txt.
"""
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SYNC = os.path.join(_REPO, "wulong", "sync")


def _run(script: str, args: list, *, env: dict = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(_SYNC, script)] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main() -> None:
    with tempfile.TemporaryDirectory() as _tmp:
        vault = os.path.realpath(_tmp)

        # Scaffold the skeleton (creates Meta/receipts/ among other dirs)
        r_init = _run("wulong-init.py", [vault])
        assert r_init.returncode == 0, f"init failed: {r_init.stderr}"

        receipts_dir = os.path.join(vault, "Meta", "receipts")

        # Gate check: no contrarian receipt exists, so this must REFUSE
        r_gate = _run(
            "check_gate_precondition.py",
            [
                "--change-id", "example-2026",
                "--gate", "nn3",
                "--receipts-dir", receipts_dir,
            ],
        )

        for line in r_gate.stdout.splitlines():
            print(line)

        assert r_gate.returncode == 1, (
            f"Expected gate to REFUSE (exit 1) but got exit {r_gate.returncode}"
        )

        print("example 02: PASS")


if __name__ == "__main__":
    main()
