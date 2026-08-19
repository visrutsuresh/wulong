"""test_execution_surface.py - freezes what wulong can execute, and records the boundary honestly.

Two jobs, and they pull against each other on purpose.

1. The pinned inventory. An AST walk over every .py file in the package counts
   the execution primitives and reds if a count moves. The point is not that the
   frozen counts are safe. The point is that adding one more has to be a
   deliberate, visible act instead of a line in a diff nobody read.
2. The hostile-clone reproduction. It proves the removed D7 shell dispatch is
   gone, and proves in the same run that a script living in the scanned
   directory still executes and can still make wulong print PASS. wulong runs
   vault-resident scripts by design (see SECURITY.md). A test that asserted only
   the first half would read as an all-clear it has not earned.

AST, never regex. A regex over the source is defeated by passing the shell
keyword a name or a call rather than a bare literal.

ponytail: stdlib ast, shutil and subprocess. No new dependency, no fixture
library, no config file. Ceiling is a hand-rolled walk; upgrade path if this
ever needs call-graph reachability is bandit or semgrep in CI.
"""
import ast
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_PKG = _REPO / "wulong"

# Frozen 2026-08-19 by this same walk over all 58 package .py files.
#
#   shell        0  no call anywhere passes a shell keyword. Removing the D7
#                   plug-in dispatch took the last one (verify-change.py:568).
#   exec_module  3  recompute-doc-baseline.py:33, session-start-gate.py:56,
#                   vault-health-check.py:539
#   eval         3  hermes-append-notebook.py:233, judge-append-notebook.py:168,
#                   metis-append-notebook.py:268
#   exec         0
#   compile      0
#   os_system    0  the os module's `system` helper, which hands a string to a
#                   shell without ever passing the keyword the first key checks
#   os_popen     0  the os module's `popen` helper, same reason
#
# This is an INVENTORY, not a threat count. Of the three exec_module sites only
# vault-health-check.py:539 loads a module out of the scanned vault; the other
# two build their path from __file__ and the directory under scan cannot steer
# them. Do not read "3" as "3 problems".
#
# KNOWN BLIND SPOT, stated rather than papered over. This walk keys on the
# literal keyword name written at a call site, so a shell flag arriving through
# a `**kwargs` unpack is invisible to it: the AST records `arg is None` for an
# unpack and the value is a runtime dict this file cannot resolve. That case is
# NOT covered, and no assertion here should be read as covering it. Closing it
# needs call-graph or taint analysis (bandit, semgrep), not a bigger walk. The
# two os keys above were added for the adjacent gap, which IS resolvable
# syntactically: a dispatch that never passes the keyword at all.
_FROZEN = {
    "shell": 0,
    "exec_module": 3,
    "eval": 3,
    "exec": 0,
    "compile": 0,
    "os_system": 0,
    "os_popen": 0,
}

# Attribute or bare name -> the _FROZEN key it counts against. The bare-name
# arm covers the direct-import form, where the helper is pulled off the os
# module by name and no `os.` prefix survives in the tree to key on.
_OS_DISPATCH = {"system": "os_system", "popen": "os_popen"}

# Anti-vacuity floor. Without it, renaming or emptying a scanned directory makes
# every count above trivially true and this file passes forever over nothing.
_MIN_FILES = 58
_PAYLOAD_FILES = 1


def _scan(root: pathlib.Path) -> tuple[dict, dict, list]:
    counts = {key: 0 for key in _FROZEN}
    sites: dict[str, list[str]] = {key: [] for key in _FROZEN}
    files = sorted(root.rglob("*.py"))

    def hit(key: str, path: pathlib.Path, lineno: int) -> None:
        counts[key] += 1
        sites[key].append(f"{path.relative_to(_REPO)}:{lineno}")

    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    hit("shell", path, node.lineno)
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "exec_module":
                hit("exec_module", path, node.lineno)
            elif isinstance(func, ast.Attribute) and func.attr in _OS_DISPATCH:
                # Attribute form: the helper reached through the module object.
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    hit(_OS_DISPATCH[func.attr], path, node.lineno)
            elif isinstance(func, ast.Name) and func.id in ("eval", "exec", "compile"):
                hit(func.id, path, node.lineno)
            elif isinstance(func, ast.Name) and func.id in _OS_DISPATCH:
                # Bare-name form: imported straight off the module, so the
                # prefix is gone by the time the call is written.
                hit(_OS_DISPATCH[func.id], path, node.lineno)

    return counts, sites, files


def test_the_scan_is_not_vacuous() -> None:
    _, _, files = _scan(_PKG)
    payload = [p for p in files if "payload" in p.relative_to(_PKG).parts]
    assert len(files) >= _MIN_FILES, (
        f"scanned only {len(files)} .py files under {_PKG}, floor is {_MIN_FILES}. "
        "A directory was renamed, moved or emptied, so the inventory below is "
        "measuring less than it thinks."
    )
    assert len(payload) == _PAYLOAD_FILES, (
        f"scanned {len(payload)} .py files under wulong/payload/, expected "
        f"{_PAYLOAD_FILES}. The payload ships into a user's vault, so it has to "
        "be inside the walk, and a change to its file count is a deliberate act."
    )


def test_the_execution_primitive_inventory_is_frozen() -> None:
    counts, sites, files = _scan(_PKG)
    if counts != _FROZEN:
        moved = [
            f"  {key}: frozen at {_FROZEN[key]}, measured {counts[key]}\n"
            f"    sites: {', '.join(sites[key]) or 'none'}"
            for key in _FROZEN
            if counts[key] != _FROZEN[key]
        ]
        pytest.fail(
            "The execution surface moved across "
            f"{len(files)} package .py files:\n" + "\n".join(moved) + "\n\n"
            "Adding an execution primitive is allowed. Doing it silently is not. "
            "If the new site is intended, get it reviewed, then update _FROZEN "
            "and the comment above it in the same commit.\n"
            "This inventory covers exactly the keys listed above, keyed on what "
            "is written at the call site. A shell flag passed through a "
            "`**kwargs` unpack is not covered. See the blind-spot note by "
            "_FROZEN before reading a green run as a clean surface."
        )


# ---------------------------------------------------------------------------
# The hostile clone
# ---------------------------------------------------------------------------

_RECEIPT = (
    "---\n"
    "agent: coder\n"
    "task: fixture\n"
    "date: 2026-08-18\n"
    'time: "12:00"\n'
    "status: DONE\n"
    "change_id: hostile-clone-fixture\n"
    "---\n\n"
    "## Task\nfixture\n\n"
    "## Outcome\nfixture\n\n"
    "## Files written\n- Meta/receipts/coder-2026-08-18-1200-hostile.md\n"
)


def _env() -> dict:
    """A developer with WULONG_ROOT exported would point this run at a real vault."""
    env = os.environ.copy()
    env.pop("WULONG_ROOT", None)
    return env


@pytest.fixture()
def hostile(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """A cloned vault that tries to get its own code run by wulong."""
    root = tmp_path / "clone"
    shutil.copytree(_PKG / "sync", root / "Meta" / "sync")
    (root / "Meta" / "receipts").mkdir(parents=True)
    (root / "Meta" / "receipts" / "coder-2026-08-18-1200-hostile.md").write_text(
        _RECEIPT, encoding="utf-8"
    )

    d7_marker = tmp_path / "d7-dispatch-ran"
    vault_marker = tmp_path / "vault-script-ran"

    # The removed sink: a manifest in the scanned directory whose cmd was
    # interpolated into a string and handed to a shell.
    qa = root / "Meta" / "qa"
    qa.mkdir()
    (qa / "e2e-plugins.yaml").write_text(
        "plugins:\n"
        "  - name: hostile\n"
        "    trigger: ['Meta/receipts']\n"
        f"    cmd: touch {d7_marker}\n",
        encoding="utf-8",
    )

    # The by-design path, unchanged by the removal: D2 runs this file by name.
    (root / "Meta" / "sync" / "validate-receipts.py").write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(vault_marker)!r}).write_text('ran')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return root, d7_marker, vault_marker


def test_hostile_clone_reaches_no_shell_dispatch_but_still_runs_its_own_script(
    hostile: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    root, d7_marker, vault_marker = hostile
    proc = subprocess.run(
        [sys.executable, str(root / "Meta" / "sync" / "verify-change.py"),
         "--change-id", "hostile-clone-fixture", "--json"],
        capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 0, proc.stderr
    checks = {c["id"]: c for c in json.loads(proc.stdout)["checks"]}

    assert checks["D7"]["status"] == "NA"
    assert "removed in 0.4.0" in checks["D7"]["detail"]
    assert not d7_marker.exists(), (
        "the manifest in the scanned directory reached a shell: the D7 dispatch "
        "is back"
    )

    # The honest half. Do not delete these three asserts to make the file read
    # like a clean bill of health. wulong executes scripts resident in the
    # directory it is pointed at, the removal did not change that, and the
    # verdict it prints is only as trustworthy as that directory. If any of
    # these ever fails, the trust boundary in SECURITY.md has moved and the
    # document has to be rewritten before this test is relaxed.
    assert vault_marker.exists(), "vault-resident script execution stopped"
    assert vault_marker.read_text() == "ran"
    assert checks["D2"]["status"] == "PASS", (
        "D2 no longer trusts the scanned vault's own validator, which is a "
        "stronger position than SECURITY.md currently documents"
    )
