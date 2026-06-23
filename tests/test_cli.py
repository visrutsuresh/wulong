"""test_cli.py — one test per CLI subcommand: init, doctor, gate, pulse.

Each test asserts the subcommand exits as expected and prints recognisable
output. No network calls. No fixtures outside this repo.
ponytail: subprocess + tmp_path only; no mocking framework.
"""
import os
import subprocess
import sys
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_CLI_PY = _REPO / "wulong" / "cli.py"
_SYNC = _REPO / "wulong" / "sync"


def _wulong(*args, env=None) -> subprocess.CompletedProcess:
    # Invoke cli.py directly so __main__ guard fires regardless of install state.
    return subprocess.run(
        [sys.executable, str(_CLI_PY)] + list(args),
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        env=env,
    )


def test_init(tmp_path: pathlib.Path) -> None:
    """wulong init exits 0 and creates Meta/receipts/."""
    result = _wulong("init", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "Meta" / "receipts").is_dir()
    assert "Done." in result.stdout


def test_doctor(tmp_path: pathlib.Path) -> None:
    """wulong doctor exits 0 on a fresh vault and reports GREEN."""
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(tmp_path)
    # doctor reads vault-structure.md if present; skip absence gracefully
    result = _wulong("doctor", env=env)
    assert result.returncode == 0, result.stderr
    assert "GREEN" in result.stdout or "all checks passed" in result.stdout


def test_gate(tmp_path: pathlib.Path) -> None:
    """wulong gate exits 1 (REFUSE) when no contrarian receipt exists."""
    receipts = tmp_path / "Meta" / "receipts"
    receipts.mkdir(parents=True)
    result = _wulong(
        "gate",
        "--change-id", "test-change-2026",
        "--gate", "nn3",
        "--receipts-dir", str(receipts),
    )
    assert result.returncode == 1
    assert "REFUSE" in result.stdout


def test_pulse() -> None:
    """wulong pulse with a nonexistent change-id exits cleanly (0 or 1, no crash)."""
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(_REPO)
    result = _wulong("pulse", "--change-id", "nonexistent-test-2026", env=env)
    # pulse reports RED/GREEN but must not crash
    assert result.returncode in (0, 1), f"unexpected exit: {result.returncode}\n{result.stderr}"
    assert "Traceback" not in result.stderr
