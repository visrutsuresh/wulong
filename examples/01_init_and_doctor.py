"""Example 01: wulong init + doctor.

Runs `wulong init` into a temp directory, then `wulong doctor` on that
skeleton. Asserts both exit 0 and prints a deterministic summary.

This example has no network calls and no fixtures beyond what ships in the
repo. The expected output is committed at tests/expected/ex01.txt.
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
        # Resolve symlinks so the replace covers /private/var/... on macOS too
        vault = os.path.realpath(_tmp)

        # --- init ---
        r_init = _run("wulong-init.py", [vault])
        assert r_init.returncode == 0, f"wulong init failed:\n{r_init.stderr}"

        # Normalize: strip the vault path so output is portable
        init_lines = r_init.stdout.replace(vault, "<vault>").splitlines()
        for line in init_lines:
            print(line)

        # --- doctor ---
        env = os.environ.copy()
        env["WULONG_ROOT"] = vault
        r_doc = _run("vault-health-check.py", [], env=env)
        assert r_doc.returncode == 0, f"wulong doctor failed:\n{r_doc.stderr}"

        for line in r_doc.stdout.splitlines():
            print(line)

        print("example 01: PASS")


if __name__ == "__main__":
    main()
