"""M7: vault-fresh's OK/WARN label and exit code must track the doctor's actual
verdict, not just its exit code. Reverting `_summarise` back to
`"OK" if rc == 0 else "WARN"` and `run_full`'s clean check back to `if rc != 0:`
leaves the full suite green, because doctor exits 0 for both PARTIAL and
ADVISORY by design, and nothing else touches this file end to end.

ponytail: reuses the existing all_axes_vault fixture, no new fixture.
"""
import importlib.util
import pathlib
import sys

import pytest

_SYNC = pathlib.Path(__file__).resolve().parent.parent / "wulong" / "sync"


def _load_vault_fresh(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("wulong_vault_fresh", _SYNC / "vault-fresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("WULONG_ROOT", raising=False)
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / ".vault-fresh-last")
    monkeypatch.setattr(mod, "_SCRIPTS",
                         [("vault-health", [sys.executable, str(_SYNC / "vault-health-check.py")])])
    return mod


@pytest.mark.parametrize("kwargs,ok", [
    ({}, True),                       # GREEN
    ({"hook_fired": False}, False),   # ADVISORY (WARNING)
    ({"strays": 1}, False),           # ADVISORY (YELLOW)
    ({"strays": 3}, False),           # RED
])
def test_run_full_tracks_the_doctor_verdict_not_just_its_exit_code(
        kwargs, ok, all_axes_vault, monkeypatch, tmp_path, capsys):
    vault_fresh = _load_vault_fresh(monkeypatch, tmp_path)
    monkeypatch.chdir(all_axes_vault(**kwargs))
    rc = vault_fresh.run_full()
    out = capsys.readouterr().out
    assert rc == (0 if ok else 1)
    if ok:
        assert "[OK]" in out and "ALL OK" in out
    else:
        assert "[WARN]" in out and "WARN (see above)" in out


def test_run_full_warns_on_partial_too(monkeypatch, tmp_path, capsys):
    (tmp_path / "CLAUDE.md").write_text("# vault\n", encoding="utf-8")
    vault_fresh = _load_vault_fresh(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    rc = vault_fresh.run_full()
    out = capsys.readouterr().out
    assert rc == 1
    assert "[WARN]" in out and "WARN (see above)" in out
