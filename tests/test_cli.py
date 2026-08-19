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


def _wulong(*args, env=None, cwd=None) -> subprocess.CompletedProcess:
    # Invoke cli.py directly so __main__ guard fires regardless of install state.
    return subprocess.run(
        [sys.executable, str(_CLI_PY)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or str(_REPO),
        env=env,
    )


def test_init(tmp_path: pathlib.Path) -> None:
    """wulong init exits 0 and creates Meta/receipts/."""
    result = _wulong("init", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "Meta" / "receipts").is_dir()
    assert "Done." in result.stdout


def test_init_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Running init twice into one directory: no error, no duplication, no clobber.

    The overlay asserted here is scrub-patterns.txt rather than the env file,
    because a sandbox that denies stat() on a dotfile makes this end-to-end run
    abort by design after Change C. All FOUR overlays are asserted at the
    function level in tests/test_init_payload.py, which can tell the two apart.
    """
    assert _wulong("init", str(tmp_path)).returncode == 0

    edited = tmp_path / "scrub-patterns.txt"
    assert edited.is_file()
    user_content = "# user edit that must survive a re-init\nexample-pattern\n"
    edited.write_text(user_content, encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    second = _wulong("init", str(tmp_path))
    if second.returncode == 1 and "cannot determine whether" in second.stderr:
        pytest.skip("filesystem denies stat() on a dotfile; init aborted by design")
    assert second.returncode == 0, second.stderr
    assert "all present, nothing created" in second.stdout
    skipped = second.stdout.partition("Skipped")[2]
    assert "scrub-patterns.txt.example -> scrub-patterns.txt" in skipped

    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == before
    assert edited.read_text(encoding="utf-8") == user_content


def test_doctor(tmp_path: pathlib.Path) -> None:
    """doctor honours WULONG_ROOT, and an empty root is PARTIAL, not GREEN.

    Before Change D this test passed for the wrong reason: doctor ignored
    WULONG_ROOT entirely and walked up from its own __file__, so it silently
    scanned the wulong repo and reported the repo's health under the name of an
    empty temp directory. Now that the variable is honoured, the subject really
    is a bare empty directory, which is why the expected verdict changed: four
    axes cannot run there, and an axis that never ran is not an axis that passed.

    The assertions below name no count on purpose, so adding an axis moves the
    verdict without touching them. That is also why this docstring is corrected
    rather than left stale: nothing here would have gone red for it.
    """
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(tmp_path)
    result = _wulong("doctor", env=env)

    assert result.returncode == 0, result.stderr
    assert "PARTIAL" in result.stdout, result.stdout
    assert "GREEN" not in result.stdout, "a skipped axis must not print the all-clear token"
    assert "FAILED: 0" in result.stdout
    # The vault it reports on must be the one we named, not the wulong repo. The
    # no-Meta WARN is the only line that carries the RESOLVED root, so it is the
    # only thing that can prove which vault ran. The `or "SKIP [B]"` alternative
    # this replaced was true of any empty directory, which made the assertion
    # vacuous, and it is why `doctor --require-all-axes VAULT` shipped scanning a
    # different vault. The order matrix below is the real guard.
    assert str(tmp_path) in result.stderr, result.stderr


def test_doctor_reports_three_counts_and_names_every_skip(tmp_path: pathlib.Path) -> None:
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(tmp_path)
    out = _wulong("doctor", env=env).stdout

    counts = [ln for ln in out.splitlines() if ln.startswith("PASSED:")]
    assert len(counts) == 1, out
    assert "PASSED:" in counts[0] and "SKIPPED:" in counts[0] and "FAILED:" in counts[0]

    skips = [ln for ln in out.splitlines() if ln.startswith("SKIP [")]
    assert skips, out
    for line in skips:
        assert "needs" in line, f"a skip must say what it needs: {line}"


def test_doctor_require_all_axes_exits_nonzero_on_a_skip(tmp_path: pathlib.Path) -> None:
    """Opt-in completeness for CI, default off so the quickstart still passes."""
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(tmp_path)
    assert _wulong("doctor", env=env).returncode == 0
    strict = _wulong("doctor", "--require-all-axes", env=env)
    assert strict.returncode == 1, strict.stdout


def test_doctor_exits_nonzero_on_a_real_failure(tmp_path: pathlib.Path) -> None:
    """A FAILED axis is exit 1, and it stays 1 while other axes are skipped."""
    inbox = tmp_path / "00-Inbox"
    inbox.mkdir()
    for i in range(12):  # threshold is 10
        (inbox / f"n{i}.md").write_text("x", encoding="utf-8")

    env = os.environ.copy()
    env["WULONG_ROOT"] = str(tmp_path)
    result = _wulong("doctor", env=env)

    assert result.returncode == 1, result.stdout
    assert "FAILED: 1" in result.stdout
    assert "RED vault-health" in result.stdout
    assert "PARTIAL" not in result.stdout, "a failure outranks a skip in the verdict"


# ---------------------------------------------------------------------------
# The argument-order matrix.
#
# `wulong doctor --require-all-axes VAULT` scanned a DIFFERENT vault than the one
# named, silently, and with the same tokens in the other order it scanned the
# right one. The CLI tested only the FIRST passthrough token for a positional, so
# any leading flag defeated the check and a resolved `--root` was prepended in
# front of the user's own path; the engine reads `--root` first. Every order of
# every root-naming form is asserted here because that is the only shape of test
# that could have caught it.
# ---------------------------------------------------------------------------

# Two vaults doctor cannot confuse: one fails axis A, the other fails axis E.
_NAMED_FINGERPRINT = "inbox_backlog"
_OTHER_FINGERPRINT = "empty_folder"


def _two_vaults(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    named, other = tmp_path / "named", tmp_path / "other"
    for v in (named, other):
        (v / "Meta").mkdir(parents=True)
        (v / "00-Inbox").mkdir()
    for i in range(12):  # threshold is 10
        (named / "00-Inbox" / f"n{i}.md").write_text("x", encoding="utf-8")
    return named, other


_ROOT_FORMS = ["{named}", "--root|{named}", "--root={named}"]
_FLAG = "--require-all-axes"


def _orders() -> list[list[str]]:
    """Every root-naming form, alone and on either side of --require-all-axes."""
    out = []
    for form in _ROOT_FORMS:
        tokens = form.split("|")
        out += [tokens, tokens + [_FLAG], [_FLAG] + tokens]
    return out


@pytest.mark.parametrize("order", _orders(), ids=lambda o: " ".join(o))
def test_the_vault_the_user_named_is_the_vault_that_is_scanned(
    tmp_path: pathlib.Path, order: list[str]
) -> None:
    """No argument order may reach a vault other than the one on the command line."""
    named, other = _two_vaults(tmp_path)
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(other)  # a competing answer in every other tier
    args = [tok.replace("{named}", str(named)) for tok in order]

    # cwd is the OTHER vault too, so the CWD walk would also find the wrong one.
    out = _wulong("doctor", *args, env=env, cwd=str(other)).stdout

    assert _NAMED_FINGERPRINT in out, out
    assert _OTHER_FINGERPRINT not in out, f"scanned the WRONG vault: {out}"


def test_the_root_flag_still_outranks_the_legacy_positional(tmp_path: pathlib.Path) -> None:
    """Both forms at once: --root wins, which is what its help string promises."""
    named, other = _two_vaults(tmp_path)
    out = _wulong("doctor", "--root", str(other), str(named), cwd=str(tmp_path)).stdout
    assert _OTHER_FINGERPRINT in out, out
    assert _NAMED_FINGERPRINT not in out, out


def test_naming_no_vault_still_falls_through_to_the_environment(
    tmp_path: pathlib.Path,
) -> None:
    """The other half of the fix: nothing named means the lower tiers still run."""
    named, other = _two_vaults(tmp_path)
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(other)
    out = _wulong("doctor", _FLAG, env=env, cwd=str(tmp_path)).stdout
    assert _OTHER_FINGERPRINT in out, out
    assert _NAMED_FINGERPRINT not in out, out


def test_doctor_without_any_root_refuses_to_guess(tmp_path: pathlib.Path) -> None:
    """No flag, no env, no marker above CWD: a named error, not a wrong vault."""
    env = os.environ.copy()
    env.pop("WULONG_ROOT", None)
    result = subprocess.run(
        [sys.executable, str(_CLI_PY), "doctor"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert result.returncode == 2, result.stdout
    assert "--root" in result.stderr
    assert "WULONG_ROOT" in result.stderr
    assert "Traceback" not in result.stderr


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


# ---------------------------------------------------------------------------
# The verdict on a run where every axis RAN.
#
# Change D gave a skipped axis its own token and left the other half standing: a
# YELLOW or WARNING axis lands in the `passed` bucket, so a run reporting two
# warnings still ended in "GREEN vault-health: all checks passed", exit 0. The
# reproduction needed a vault where all nine axes can run, which no test had.
#
# The exit code deliberately does NOT move. A pristine `wulong init --with-hooks`
# raises WARNING [I] on every run until the hook fires for the first time, so a
# RED there would exit 1 on the shipped quickstart. This is a reporting fix.
# ---------------------------------------------------------------------------

def _doctor_on(vault: pathlib.Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["WULONG_ROOT"] = str(vault)
    return _wulong("doctor", env=env)


def test_a_warning_only_run_does_not_print_the_all_clear(all_axes_vault) -> None:
    result = _doctor_on(all_axes_vault(hook_fired=False))
    assert "WARNING [I]" in result.stdout, result.stdout
    assert "PASSED: 9  SKIPPED: 0  FAILED: 0" in result.stdout, result.stdout
    assert "GREEN" not in result.stdout, "a warned axis must not print the all-clear token"
    assert "ADVISORY vault-health" in result.stdout, result.stdout
    assert result.returncode == 0, "a warning is advisory and does not move the exit code"


def test_a_yellow_only_run_does_not_print_the_all_clear(all_axes_vault) -> None:
    """YELLOW is the arm a predicate written against the literal WARNING misses.

    One site emits it, check_b at one or two strays, and it lands in the same
    `passed` bucket a WARNING does.
    """
    result = _doctor_on(all_axes_vault(strays=1))
    assert "YELLOW [B]" in result.stdout, result.stdout
    assert "WARNING" not in result.stdout, "this arm must isolate YELLOW"
    assert "PASSED: 9  SKIPPED: 0  FAILED: 0" in result.stdout, result.stdout
    assert "GREEN" not in result.stdout, "a YELLOW axis must not print the all-clear token"
    assert "ADVISORY vault-health" in result.stdout, result.stdout
    assert result.returncode == 0


def test_a_run_where_every_axis_was_silent_still_reaches_green(all_axes_vault) -> None:
    """The other half of the claim: the fix must not make GREEN unreachable."""
    result = _doctor_on(all_axes_vault())
    assert "PASSED: 9  SKIPPED: 0  FAILED: 0" in result.stdout, result.stdout
    assert "GREEN vault-health: all checks passed" in result.stdout, result.stdout
    assert "ADVISORY" not in result.stdout
    assert result.returncode == 0


def test_a_skip_outranks_an_advisory_and_the_skip_list_still_prints(tmp_path) -> None:
    """Wire the hook into a bare vault and the run both skips three axes and
    warns on the fourth. PARTIAL is the stronger statement, so it wins, and the
    indented list naming each skip has to survive the new branch.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}',
        encoding="utf-8")
    (tmp_path / ".wulong").mkdir()
    (tmp_path / ".wulong" / "hook-events.jsonl").write_text("", encoding="utf-8")

    result = _doctor_on(tmp_path)
    assert "WARNING [I]" in result.stdout, result.stdout
    assert "PARTIAL vault-health" in result.stdout, result.stdout
    assert "ADVISORY" not in result.stdout, "PARTIAL outranks ADVISORY"
    assert "GREEN" not in result.stdout
    assert [l for l in result.stdout.splitlines() if l.startswith("  SKIP [")], \
        "the indented skip list must still print under the verdict"
    assert result.returncode == 0


def test_a_failure_outranks_an_advisory(all_axes_vault) -> None:
    vault = all_axes_vault(hook_fired=False)
    for i in range(12):  # A, threshold 10
        (vault / "00-Inbox" / f"n{i}.md").write_text("x", encoding="utf-8")

    result = _doctor_on(vault)
    assert "WARNING [I]" in result.stdout, result.stdout
    assert "RED vault-health" in result.stdout, result.stdout
    assert "ADVISORY" not in result.stdout, "the advisory token must not print on the RED path"
    assert result.returncode == 1


def test_a_pristine_with_hooks_install_is_partial_and_exits_zero(tmp_path) -> None:
    """The shipped quickstart, pinned. No golden covers --with-hooks.

    This is the run that settles the severity question: it carries three skips
    AND one warning, on a vault the installer just built and nobody has touched.
    Reddening a warning would exit 1 here, on a correct install, forever.
    """
    vault = tmp_path / "v"
    assert _wulong("init", str(vault), "--with-hooks").returncode == 0

    result = _doctor_on(vault)
    assert "PASSED: 6  SKIPPED: 3  FAILED: 0" in result.stdout, result.stdout
    assert "WARNING [I] hook_health" in result.stdout, result.stdout
    assert "PARTIAL vault-health" in result.stdout, result.stdout
    assert "GREEN" not in result.stdout
    assert [l for l in result.stdout.splitlines() if l.startswith("  SKIP [")], result.stdout
    assert result.returncode == 0
