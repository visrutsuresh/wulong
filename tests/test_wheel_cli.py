"""test_wheel_cli.py - all four subcommands, from a wheel, outside any checkout.

This is the only test that reproduces what a `pip install wulong` user has. The
source tree hides the bug it exists to catch: run from the repo, every script
finds the repo's own CLAUDE.md by walking up from __file__, so a broken resolver
looks fine. Install the wheel into a clean venv, cd somewhere with no vault
anywhere above it, and that crutch is gone.

The bar for each subcommand is the same: a CLEAR error naming the three ways to
say which vault you mean. Not a traceback. Not a different vault. Not a silent
pass over an empty site-packages directory, which is the false green that
motivated the whole change.

ponytail: subprocess + venv + build, all stdlib or already a test dependency.
"""
import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _have_build() -> bool:
    try:
        import build  # noqa: F401
        import setuptools  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _have_build(), reason="build/setuptools unavailable; cannot build a wheel"
)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """Build the wheel, install it into a clean venv, return (python, cwd).

    cwd is a directory with no vault marker anywhere above it. That is the
    property under test, so it is asserted rather than assumed.
    """
    base = tmp_path_factory.mktemp("wheelcli")
    dist = base / "dist"

    def _must(cmd, what):
        """Run a build step, and SKIP rather than ERROR when the box cannot.

        A sandbox that denies the network or a temp-directory write cannot build
        or install anything, and that is not a finding about this code. CI does
        both, which is where the skip must never fire; docs/CONTRIBUTING.md sets
        zero skips as the standard there.
        """
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip(f"cannot {what} in this environment: "
                        f"{(result.stderr or result.stdout).strip()[:200]}")
        return result

    _must([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(_REPO)],
          "build a wheel")
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1, wheels

    venv = base / "venv"
    _must([sys.executable, "-m", "venv", str(venv)], "create a clean venv")
    python = venv / "bin" / "python"
    # No Windows venv-layout branch: the package is POSIX only, 11 scripts
    # hard-import fcntl, so this test cannot reach a Windows interpreter at all.
    _must([str(python), "-m", "pip", "install", "-q", str(wheels[0])],
          "install the wheel")

    nowhere = base / "nowhere" / "deeper"
    nowhere.mkdir(parents=True)
    probe = nowhere.resolve()
    for _ in range(60):
        assert not (probe / "CLAUDE.md").exists() and not (probe / ".wulong").exists(), (
            f"the fixture directory is inside a vault at {probe}; the test would pass "
            "for the wrong reason"
        )
        if probe.parent == probe:
            break
        probe = probe.parent

    return python, nowhere


def _run(installed, *args):
    python, cwd = installed
    env = {k: v for k, v in os.environ.items() if k != "WULONG_ROOT"}
    return subprocess.run([str(python), "-m", "wulong.cli", *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


_ROOTLESS = {
    "doctor": [],
    "gate": ["--change-id", "x", "--gate", "nn3"],
    "pulse": ["--change-id", "x"],
}


@pytest.mark.parametrize("cmd", sorted(_ROOTLESS))
def test_subcommand_names_all_three_options_instead_of_guessing(installed, cmd):
    result = _run(installed, cmd, *_ROOTLESS[cmd])

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 2, (
        f"expected the refuse-to-guess exit, got {result.returncode}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "--root" in result.stderr, result.stderr
    assert "WULONG_ROOT" in result.stderr, result.stderr
    assert "inside the vault" in result.stderr, result.stderr
    # The false green this replaces: a clean verdict over an empty install dir.
    assert "GREEN" not in result.stdout, result.stdout
    assert "site-packages" not in result.stdout, result.stdout


def test_init_scaffolds_and_doctor_then_reports_partial(installed):
    """init is the one subcommand with no root to resolve: it CREATES the vault.

    Its positional target is the answer, so there is nothing to refuse. The
    round trip is the real assertion: init a vault, then point doctor at it with
    each of the three routes and get the same honest PARTIAL verdict from all
    three.
    """
    python, cwd = installed
    vault = cwd / "myvault"

    r_init = _run(installed, "init", str(vault))
    assert r_init.returncode == 0, r_init.stderr
    assert (vault / "Meta" / "receipts").is_dir()

    # route 1: the flag
    by_flag = _run(installed, "doctor", "--root", str(vault))
    # route 2: the environment
    env = {k: v for k, v in os.environ.items() if k != "WULONG_ROOT"}
    env["WULONG_ROOT"] = str(vault)
    by_env = subprocess.run([str(python), "-m", "wulong.cli", "doctor"],
                            capture_output=True, text=True, cwd=str(cwd), env=env)
    # route 3: standing inside it
    env.pop("WULONG_ROOT")
    by_cwd = subprocess.run([str(python), "-m", "wulong.cli", "doctor"],
                            capture_output=True, text=True, cwd=str(vault), env=env)

    for name, result in (("flag", by_flag), ("env", by_env), ("cwd", by_cwd)):
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
        assert "PARTIAL" in result.stdout, f"{name}: {result.stdout}"
        assert "GREEN" not in result.stdout, f"{name}: {result.stdout}"
        assert "FAILED: 0" in result.stdout, f"{name}: {result.stdout}"

    strict = _run(installed, "doctor", "--root", str(vault), "--require-all-axes")
    assert strict.returncode == 1, strict.stdout


def test_gate_and_pulse_run_against_a_real_vault_from_the_wheel(installed):
    """Not just a clean error: the three that take --root must also WORK."""
    python, cwd = installed
    vault = cwd / "gatevault"
    assert _run(installed, "init", str(vault)).returncode == 0

    gate = _run(installed, "gate", "--change-id", "nope-2026", "--gate", "nn3",
                "--root", str(vault))
    assert gate.returncode == 1, gate.stdout + gate.stderr
    assert "REFUSE" in gate.stdout
    assert "site-packages" not in gate.stdout

    pulse = _run(installed, "pulse", "--change-id", "nope-2026", "--root", str(vault),
                 "--no-exit-nonzero-on-red")
    assert "Traceback" not in pulse.stderr, pulse.stderr
    assert pulse.returncode in (0, 1), pulse.stdout + pulse.stderr
    assert f"root:       {vault}" in pulse.stdout, pulse.stdout
    assert "site-packages" not in pulse.stdout, pulse.stdout


def test_pulse_exits_nonzero_on_red_from_the_wheel(installed):
    """The D4 default, asserted where users meet it rather than in the tree."""
    python, cwd = installed
    vault = cwd / "redvault"
    assert _run(installed, "init", str(vault)).returncode == 0

    red = _run(installed, "pulse", "--change-id", "nope-2026", "--root", str(vault))
    assert "ACTION REQUIRED" in red.stdout, red.stdout
    assert red.returncode == 1, red.stdout
