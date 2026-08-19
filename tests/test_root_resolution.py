"""test_root_resolution.py - the precedence matrix for wulong/_root.py.

Every cell of the matrix is asserted twice, once for the ENTRY-POINT floor and
once for the CHILD floor, because the two differ in exactly one tier and that
difference is the whole design:

    tier              entry point (no fallback)      engine script (fallback)
    1 explicit path   wins                           wins
    2 WULONG_ROOT     wins over everything below     wins over everything below
    3 floor           walk up from CWD for a marker  the install-relative path
    4 nothing left    RootNotFound, naming all three (unreachable, tier 3 always
                                                      answers)

Getting tier 3 the wrong way round is not cosmetic. An engine script lives at
<vault>/Meta/sync/, so its own location IS its vault, while a CWD walk would
point it at whichever vault the operator happened to be standing in. An entry
point installed into site-packages has the opposite problem: its own location is
never a vault, so it must ask the working directory or refuse.

ponytail: stdlib tmp_path + monkeypatch. No fixtures beyond the two temp vaults.
"""
import os
import pathlib
import subprocess
import sys

import pytest

from wulong._root import ENV_VAR, RootNotFound, child_env, resolve_root

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SYNC = _REPO / "wulong" / "sync"
_CLI_PY = _REPO / "wulong" / "cli.py"


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    """Two marked vaults, and a CWD that is NOT inside either of them."""
    a, b, elsewhere = tmp_path / "vaultA", tmp_path / "vaultB", tmp_path / "elsewhere"
    for v in (a, b):
        (v / "Meta").mkdir(parents=True)
        (v / "CLAUDE.md").write_text("# marker", encoding="utf-8")
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv(ENV_VAR, raising=False)
    return a, b, elsewhere


# ---------------------------------------------------------------------------
# Tier 1 and 2: identical for both floors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("floor", ["entry-point", "child"])
def test_explicit_path_only(vaults, floor):
    a, b, _ = vaults
    kw = {} if floor == "entry-point" else {"fallback": str(b)}
    assert resolve_root(str(a), **kw) == str(a)


@pytest.mark.parametrize("floor", ["entry-point", "child"])
def test_environment_only(vaults, floor, monkeypatch):
    a, b, _ = vaults
    monkeypatch.setenv(ENV_VAR, str(a))
    kw = {} if floor == "entry-point" else {"fallback": str(b)}
    assert resolve_root(None, **kw) == str(a)


@pytest.mark.parametrize("floor", ["entry-point", "child"])
def test_both_the_flag_wins(vaults, floor, monkeypatch):
    """The data-loss cell. A tool told --root B must never touch vault A."""
    a, b, _ = vaults
    monkeypatch.setenv(ENV_VAR, str(a))
    kw = {} if floor == "entry-point" else {"fallback": str(a)}
    assert resolve_root(str(b), **kw) == str(b)


@pytest.mark.parametrize("floor", ["entry-point", "child"])
def test_an_empty_environment_value_is_not_a_value(vaults, floor):
    a, b, _ = vaults
    os.environ[ENV_VAR] = "   "
    try:
        if floor == "child":
            assert resolve_root(None, fallback=str(b)) == str(b)
        else:
            with pytest.raises(RootNotFound):
                resolve_root(None)
    finally:
        del os.environ[ENV_VAR]


# ---------------------------------------------------------------------------
# Tier 3: where the two floors part company
# ---------------------------------------------------------------------------

def test_entry_point_neither_walks_up_from_cwd(vaults, monkeypatch):
    a, _, _ = vaults
    inside = a / "Meta" / "deep" / "deeper"
    inside.mkdir(parents=True)
    monkeypatch.chdir(inside)
    assert resolve_root(None) == str(a.resolve())


def test_entry_point_neither_and_nowhere_raises(vaults):
    """Refusing to guess IS the feature. Name all three ways instead."""
    with pytest.raises(RootNotFound) as exc:
        resolve_root(None, tool="wulong doctor")
    message = str(exc.value)
    assert "--root" in message
    assert ENV_VAR in message
    assert "inside the vault" in message
    assert "wulong doctor" in message


def test_child_neither_uses_the_install_relative_floor(vaults):
    _, b, _ = vaults
    assert resolve_root(None, fallback=str(b)) == str(b)


def test_child_floor_beats_a_cwd_marker(vaults, monkeypatch):
    """The floor outranks CWD for a child, and this is deliberate.

    A script at <vault>/Meta/sync/ knows its vault. If the CWD walk ran first,
    running that script from inside some OTHER vault would silently retarget it.
    """
    a, b, _ = vaults
    monkeypatch.chdir(a)
    assert resolve_root(None, fallback=str(b)) == str(b)


def test_environment_beats_the_child_floor(vaults, monkeypatch):
    a, b, _ = vaults
    monkeypatch.setenv(ENV_VAR, str(a))
    assert resolve_root(None, fallback=str(b)) == str(a)


# ---------------------------------------------------------------------------
# child_env: how the answer travels
# ---------------------------------------------------------------------------

def test_child_env_pins_the_root(vaults, monkeypatch):
    a, b, _ = vaults
    monkeypatch.setenv(ENV_VAR, str(a))
    assert child_env(str(b))[ENV_VAR] == str(b), (
        "the resolved root must overwrite an inherited one, or a parent that "
        "resolved via --root hands its children the wrong vault"
    )


# ---------------------------------------------------------------------------
# The same matrix, through the real scripts
# ---------------------------------------------------------------------------

_ENTRY_POINTS = ["doctor", "gate", "pulse"]


def _cli(*args, cwd, env):
    return subprocess.run([sys.executable, str(_CLI_PY), *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


@pytest.mark.parametrize("cmd", _ENTRY_POINTS)
def test_every_subcommand_refuses_to_guess(cmd, vaults):
    """No flag, no env, no marker above CWD: named error, exit 2, no traceback."""
    _, _, elsewhere = vaults
    env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
    args = {"doctor": [], "gate": ["--change-id", "x", "--gate", "nn3"],
            "pulse": ["--change-id", "x"]}[cmd]
    result = _cli(cmd, *args, cwd=elsewhere, env=env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--root" in result.stderr and ENV_VAR in result.stderr
    assert "Traceback" not in result.stderr


def test_pulse_hands_the_root_to_every_child(tmp_path):
    """`pulse --root B` must put ALL children on B, not on site-packages.

    Proven by content, not by trust: every child is a real subprocess, and the
    audit child prints the root it used.
    """
    import shutil
    vault_b = tmp_path / "vaultB"
    shutil.copytree(_SYNC, vault_b / "Meta" / "sync")
    (vault_b / "Meta" / "receipts").mkdir(parents=True)
    (vault_b / "CLAUDE.md").write_text("# marker", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
    result = _cli("pulse", "--change-id", "no-such-change-2026",
                  "--root", str(vault_b), "--no-exit-nonzero-on-red",
                  cwd=_REPO, env=env)

    combined = result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert f"root:       {vault_b}" in result.stdout
    assert "site-packages" not in combined, combined

    # Each child names a path it derived from ITS OWN resolved root. Every one of
    # them must sit under vault B. Before Change D the parent was on B and three
    # of the four were on the engine's install directory.
    # check-compliance.py, reached through step 4:
    assert f"{vault_b}/Meta/compliance" in combined, combined
    # the doc-consistency baseline, which is per-vault DATA:
    assert f"{vault_b}/Meta/sync/doc-consistency-baseline.json" in combined, combined
    # nothing may resolve a VAULT path inside the package tree:
    assert f"{_SYNC}/doc-consistency-baseline.json" not in combined, combined


# The four constants a child derives from its own root, one per child. Asserted
# by importing the module with the environment set, which is the exact route the
# parent uses to hand the root down.
_CHILD_ROOT_ATTR = {
    "verify-change": "VAULT_ROOT",
    "check-doc-consistency": "VAULT",
    "check-compliance": "VAULT",
    "enforcement-sweep": "VAULT",
    "session-guard": "VAULT_ROOT",
    "session-start-gate": "VAULT_ROOT",
}


@pytest.mark.parametrize("stem", sorted(_CHILD_ROOT_ATTR))
def test_a_child_takes_the_root_from_the_environment(stem, tmp_path, monkeypatch):
    """The pass-down route, asserted at the constant each child actually uses."""
    import importlib.util
    vault = tmp_path / "v"
    (vault / "Meta").mkdir(parents=True)
    monkeypatch.setenv(ENV_VAR, str(vault))

    safe = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe, _SYNC / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(safe, None)

    resolved = pathlib.Path(str(getattr(mod, _CHILD_ROOT_ATTR[stem])))
    assert resolved == vault, f"{stem} resolved {resolved}, not the handed-down root"


@pytest.mark.parametrize("stem", sorted(_CHILD_ROOT_ATTR))
def test_a_child_falls_back_to_its_own_location(stem, tmp_path, monkeypatch):
    """No root handed down: the install-relative floor, NOT a CWD guess.

    tmp_path is the CWD here and holds a marker. An entry point would take it.
    A child must not: its own directory is the better answer.
    """
    import importlib.util
    (tmp_path / "CLAUDE.md").write_text("# marker", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_VAR, raising=False)

    safe = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe, _SYNC / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(safe, None)

    resolved = pathlib.Path(str(getattr(mod, _CHILD_ROOT_ATTR[stem])))
    assert resolved != tmp_path, f"{stem} took the CWD marker over its own location"
    assert resolved == _REPO, resolved
