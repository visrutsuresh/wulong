"""test_session_pulse.py - exit-code behaviour lock on wulong/sync/session-pulse.py.

Change D flipped the default: a RED verdict now exits 1, and
--no-exit-nonzero-on-red restores the old log-only behaviour. --strict is
untouched and still governs what counts as failure and how it is labelled.

Two tests below (missing sibling, missing baseline) are NOT about the exit-code
default at all. Their subject is that a specific condition is not a failure, and
the fixture happens to also carry a RED verify-change verdict. They therefore
pass --no-exit-nonzero-on-red so that a returncode of 0 still means exactly what
it meant before the flip, plus they now assert their subject is absent from the
issue list. Sweeping them into the flip would have deleted what they test.

Same fixture route as test_verify_change.py: the WHOLE wulong/sync/ directory is
copied to <tmp>/Meta/sync/ so every sibling shell-out resolves. The fixture also
covers the no-baseline branch: doc-consistency-baseline.json is referenced by
session-pulse.py but does not exist in wulong/sync/, so the run prints a WARN and
treats the backlog as empty. In a bare fixture vault the doc-drift delta is 0,
so it is never the cause of a non-zero exit here.

ponytail: shutil.copytree + subprocess only.
"""
import pathlib
import shutil
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SYNC = _REPO / "wulong" / "sync"

# session-pulse.py hard-requires exactly these three siblings and exits 2 if any
# is missing. check-compliance.py is derived from the same directory but is
# deliberately EXCLUDED from that check.
_REQUIRED = ("verify-change.py", "check-doc-consistency.py", "session-close-audit.py")
_NOT_REQUIRED = "check-compliance.py"

_NO_SUCH_CHANGE = "no-such-change-id-fixture-2026"


def _vault(tmp_path: pathlib.Path) -> pathlib.Path:
    shutil.copytree(_SYNC, tmp_path / "Meta" / "sync")
    (tmp_path / "Meta" / "receipts").mkdir(parents=True)
    return tmp_path


def _pulse(vault: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    # --root is explicit so the fixture vault is the subject. Without it the
    # entry point walks up from CWD, which during a test run is this repo.
    return subprocess.run(
        [sys.executable, str(vault / "Meta" / "sync" / "session-pulse.py"),
         "--change-id", _NO_SUCH_CHANGE, "--root", str(vault), *args],
        capture_output=True, text=True,
    )


def _issues(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if "ACTION REQUIRED" in ln]
    return lines[0] if lines else ""


def test_red_exits_nonzero_by_default(tmp_path: pathlib.Path) -> None:
    """An unknown change_id is a RED verify-change verdict, and RED now exits 1."""
    result = _pulse(_vault(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RED" in result.stdout
    assert "ACTION REQUIRED" in result.stdout


def test_red_exits_zero_with_the_opt_out(tmp_path: pathlib.Path) -> None:
    """The old log-only behaviour is still reachable, by name, on purpose."""
    result = _pulse(_vault(tmp_path), "--no-exit-nonzero-on-red")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ACTION REQUIRED" in result.stdout, "the verdict is unchanged, only the exit code"


def test_the_exit_code_agrees_with_the_printed_verdict(tmp_path: pathlib.Path) -> None:
    """The bug the flip fixes: PULSE said ACTION REQUIRED and the shell saw success."""
    result = _pulse(_vault(tmp_path))
    printed_clear = "ALL CLEAR" in result.stdout
    assert printed_clear == (result.returncode == 0), result.stdout
    assert result.returncode == 1, result.stdout
    assert "log-only" not in result.stdout, (
        "a run that exits 1 must not label itself with the pre-0.4.0 default"
    )


def test_no_panel_string_advertises_the_switch_the_flip_replaced() -> None:
    """The panel is byte-identical in both exit modes, so its text cannot name a
    mode. It has to state the RULE, and after D4 the rule names
    --no-exit-nonzero-on-red. Shipping "run without --strict to suppress exit
    code" put the tool in direct contradiction with docs/USERGUIDE.md on the one
    point that changed."""
    text = (_SYNC / "session-pulse.py").read_text(encoding="utf-8")
    assert "run without --strict" not in text
    assert "[log-only]" not in text
    assert "--no-exit-nonzero-on-red" in text


def test_red_exits_one_under_strict(tmp_path: pathlib.Path) -> None:
    """check-compliance.py is removed so the exit 1 is attributable to the RED alone."""
    vault = _vault(tmp_path)
    (vault / "Meta" / "sync" / _NOT_REQUIRED).unlink()
    result = _pulse(vault, "--strict")
    assert result.returncode == 1
    assert "ACTION REQUIRED" in result.stdout
    issues = [ln for ln in result.stdout.splitlines() if "ACTION REQUIRED" in ln]
    assert issues and "verify-change RED" in issues[0]
    assert "doc drift" not in issues[0]


@pytest.mark.parametrize("script", _REQUIRED)
def test_missing_required_sibling_exits_two(tmp_path: pathlib.Path, script: str) -> None:
    vault = _vault(tmp_path)
    (vault / "Meta" / "sync" / script).unlink()
    result = _pulse(vault)
    assert result.returncode == 2
    assert "required script not found" in result.stdout
    assert script in result.stdout


def test_missing_check_compliance_is_not_an_infrastructure_error(tmp_path: pathlib.Path) -> None:
    """It is derived from the same directory but excluded from the required set."""
    vault = _vault(tmp_path)
    (vault / "Meta" / "sync" / _NOT_REQUIRED).unlink()
    result = _pulse(vault, "--no-exit-nonzero-on-red")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"({_NOT_REQUIRED} not found" in result.stdout
    assert "compliance" not in _issues(result.stdout), (
        "a missing check-compliance.py must not be reported as a compliance failure"
    )


def test_no_baseline_file_is_a_warning_not_a_failure(tmp_path: pathlib.Path) -> None:
    """doc-consistency-baseline.json ships nowhere, so every run takes this branch."""
    vault = _vault(tmp_path)
    assert not (vault / "Meta" / "sync" / "doc-consistency-baseline.json").exists()
    result = _pulse(vault, "--no-exit-nonzero-on-red")
    assert "baseline file not found" in result.stdout
    assert result.returncode == 0
    assert "doc drift" not in _issues(result.stdout), (
        "a missing baseline file is a warning, never counted as new drift"
    )
