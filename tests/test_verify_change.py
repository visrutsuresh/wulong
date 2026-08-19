"""test_verify_change.py - D3, D4, D7 and change_id locks on wulong/sync/verify-change.py.

verify-change.py resolves its root through wulong._root, which reads WULONG_ROOT
from the environment before falling back to its own install-relative directory.
Every child spawned here therefore has WULONG_ROOT stripped: a developer who
happens to have that variable exported would otherwise point this whole file at
their real vault. The fixture route is the copytree: the WHOLE wulong/sync/
directory is copied to <tmp>/Meta/sync/, which makes the fallback resolve to
<tmp>. Copying the whole directory (not just the one script) is what lets the
three siblings it shells out to (validate-receipts.py,
validate-receipt-graph.py, session-close-audit.py) resolve.

Assertions read the --json report and target D3 and D4 by check id. D6 always
FAILs in a bare fixture vault (no gate chain exists there), which is why the
overall verdict is not asserted.

ponytail: shutil.copytree + subprocess only. No source change, no monkeypatch.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SYNC = _REPO / "wulong" / "sync"


@pytest.fixture(scope="module")
def vault(tmp_path_factory) -> pathlib.Path:
    root = tmp_path_factory.mktemp("vault")
    shutil.copytree(_SYNC, root / "Meta" / "sync")
    (root / "Meta" / "receipts").mkdir(parents=True)
    return root


def _write_receipt(vault: pathlib.Path, name: str, change_id: str,
                   status: str, files: list[str]) -> None:
    listed = "\n".join(f"- {f}" for f in files)
    (vault / "Meta" / "receipts" / name).write_text(
        "---\n"
        "agent: coder\n"
        "task: fixture\n"
        "date: 2026-08-18\n"
        'time: "12:00"\n'
        f"status: {status}\n"
        f"change_id: {change_id}\n"
        "---\n\n"
        "## Task\nfixture\n\n"
        "## Outcome\nfixture\n\n"
        f"## Files written\n{listed}\n",
        encoding="utf-8",
    )


def _env() -> dict:
    """WULONG_ROOT exported in a developer shell would redirect every child here."""
    env = os.environ.copy()
    env.pop("WULONG_ROOT", None)
    return env


def _run(vault: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(vault / "Meta" / "sync" / "verify-change.py"), *args],
        capture_output=True, text=True, env=_env(),
    )


def _checks(vault: pathlib.Path, change_id: str) -> dict:
    proc = _run(vault, "--change-id", change_id, "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    return {c["id"]: c for c in report["checks"]}


def test_d4_passes_when_every_claimed_path_exists(vault: pathlib.Path) -> None:
    _write_receipt(vault, "coder-2026-08-18-1200-d4ok.md", "fixture-d4-ok", "DONE",
                   ["Meta/receipts/coder-2026-08-18-1200-d4ok.md"])
    checks = _checks(vault, "fixture-d4-ok")
    assert checks["D4"]["status"] == "PASS"
    assert checks["D3"]["status"] == "PASS"


def test_d4_catches_a_phantom_path(vault: pathlib.Path) -> None:
    _write_receipt(vault, "coder-2026-08-18-1200-d4phantom.md", "fixture-d4-phantom",
                   "DONE", ["Meta/receipts/never-written.md"])
    checks = _checks(vault, "fixture-d4-phantom")
    assert checks["D4"]["status"] == "FAIL"
    assert "never-written.md" in json.dumps(checks["D4"])


def test_d4_reds_on_brace_shorthand(vault: pathlib.Path) -> None:
    """Brace shorthand is split on the comma, so the first half is a phantom.

    This asserts CURRENT behaviour, which is a known false-positive class: a
    receipt listing real files as Meta/sync/{a.py,b.py} is flagged even though
    both files exist. The fix is to write paths out in full, not to loosen D4.
    """
    _write_receipt(vault, "coder-2026-08-18-1200-d4brace.md", "fixture-d4-brace",
                   "DONE", ["Meta/sync/{verify-change.py,session-pulse.py}"])
    checks = _checks(vault, "fixture-d4-brace")
    assert checks["D4"]["status"] == "FAIL"
    assert "{verify-change.py" in json.dumps(checks["D4"])
    assert (vault / "Meta" / "sync" / "verify-change.py").exists()
    assert (vault / "Meta" / "sync" / "session-pulse.py").exists()


def test_d3_rejects_a_non_done_terminal_status(vault: pathlib.Path) -> None:
    _write_receipt(vault, "coder-2026-08-18-1200-d3partial.md", "fixture-d3-partial",
                   "PARTIAL", ["Meta/receipts/coder-2026-08-18-1200-d3partial.md"])
    checks = _checks(vault, "fixture-d3-partial")
    assert checks["D3"]["status"] == "FAIL"
    assert "PARTIAL" in json.dumps(checks["D3"])
    assert checks["D4"]["status"] == "PASS"


def test_d7_is_na_and_says_why(vault: pathlib.Path) -> None:
    """D7's plug-in dispatch was removed, so the check is N/A on every run."""
    _write_receipt(vault, "coder-2026-08-18-1200-d7na.md", "fixture-d7-na", "DONE",
                   ["Meta/receipts/coder-2026-08-18-1200-d7na.md"])
    checks = _checks(vault, "fixture-d7-na")
    assert checks["D7"]["status"] == "NA"
    assert checks["D7"]["severity"] == "NA"
    assert "removed in 0.4.0" in checks["D7"]["detail"]
    assert "shell command string" in checks["D7"]["detail"]


def test_change_id_accepts_a_real_id(vault: pathlib.Path) -> None:
    """Dots, dashes and underscores all appear in real change_ids."""
    cid = "wulong-injection_fix.v4-2026-08-19"
    _write_receipt(vault, "coder-2026-08-18-1200-cidok.md", cid, "DONE",
                   ["Meta/receipts/coder-2026-08-18-1200-cidok.md"])
    checks = _checks(vault, cid)
    assert checks["D3"]["status"] == "PASS"


@pytest.mark.parametrize("bad", [
    "fixture-d4-ok; touch /tmp/wulong-pwned",
    "fixture d4 ok",
    "../../etc/passwd",
    "-strict",
    ".",
    "..",
    "",
    "x" * 201,
])
def test_change_id_rejects_a_non_token(vault: pathlib.Path, bad: str) -> None:
    """Hygiene, not a sandbox: change_id reaches an escaped regex and list argv."""
    proc = _run(vault, f"--change-id={bad}", "--json")
    assert proc.returncode == 2
    assert "[A-Za-z0-9._-]" in proc.stderr
    assert proc.stdout == ""
