"""test_init_payload.py - what `wulong init` installs, and what it refuses to destroy.

Two things are under test and they pull in opposite directions.

INSTALL: after Change C, init must lay down the agent payload, not just the four
overlay templates. `pip install wulong` used to hand a stranger 53 scripts and ten
empty folders; the tests here fail if it goes back to that.

REFUSE: the never-clobber contract used to rest entirely on `Path.exists()`, and
`Path.exists()` delegates to `os.path.exists()`, which swallows EVERY OSError and
returns False. A filesystem that cannot answer therefore read as "not there" and
init OVERWROTE the user's env file (which holds WULONG_ROOT and GITHUB_TOKEN) and
their scrub deny-list. That is the most damaging write in the product, so it gets
a test that fails on any environment, root or not, rather than one that depends on
chmod semantics.

ponytail: stdlib importlib + tmp_path. The init script is loaded by path because
its filename is hyphenated and therefore not importable as a module.
"""
import errno
import importlib.util
import os
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_INIT_PY = _REPO / "wulong" / "sync" / "wulong-init.py"
_PAYLOAD = _REPO / "wulong" / "payload"

# Change C1 moved these here from the repo root. One constant per fact, so a
# future move is a one-line edit rather than a scavenger hunt.
_PAYLOAD_AGENTS = _PAYLOAD / ".claude" / "agents"
_PAYLOAD_HOOKS = _PAYLOAD / ".claude" / "hooks"
_PAYLOAD_SKILLS = _PAYLOAD / ".claude" / "skills"
_PAYLOAD_CLAUDE_MD = _PAYLOAD / "CLAUDE.md"


def _load_init():
    spec = importlib.util.spec_from_file_location("wulong_init", _INIT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init = _load_init()


def _stat_works_on_dotfiles(tmp_path: pathlib.Path) -> bool:
    """True if this filesystem can stat a file whose name begins with a dot.

    The developer sandbox this repo is built in denies stat() on every path
    matching `.env`, which is exactly the file the clobber bug destroys. Probing
    is what keeps that environment quirk from being reported as a code failure.
    """
    probe_dir = tmp_path / "stat-probe"
    probe_dir.mkdir()
    probe = probe_dir / ("." + "env")
    probe.write_text("probe\n", encoding="utf-8")
    try:
        os.lstat(probe)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# The payload is installed
# ---------------------------------------------------------------------------

def test_payload_files_are_the_agents_hook_skills_and_policy() -> None:
    """The exact payload set, so a silent drop fails instead of shipping quietly."""
    files = init.payload_files()
    agents = [f for f in files if f.parts[:2] == (".claude", "agents")]
    assert len(agents) == 65, f"expected 65 agent definitions, found {len(agents)}"
    assert pathlib.PurePath(".claude/hooks/stop-slop-hook.py") in files
    assert pathlib.PurePath(".claude/skills/ponytail/SKILL.md") in files
    assert pathlib.PurePath(".claude/skills/explain-in-plain-english/SKILL.md") in files
    assert pathlib.PurePath("CLAUDE.md") in files
    assert len(files) == 69, f"expected 65 + 1 + 2 + 1 = 69 payload files, found {len(files)}"


def test_init_installs_the_payload(tmp_path: pathlib.Path) -> None:
    copied, skipped = init._install_payload(tmp_path, force=False)
    assert skipped == []
    assert len(copied) == 69
    assert len(list((tmp_path / ".claude" / "agents").glob("*.md"))) == 65
    assert (tmp_path / ".claude" / "hooks" / "stop-slop-hook.py").is_file()
    assert (tmp_path / ".claude" / "skills" / "ponytail" / "SKILL.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()


def test_init_does_not_copy_the_sync_scripts(tmp_path: pathlib.Path) -> None:
    """One copy of the engine, in site-packages.

    A second copy in the vault goes stale on `pip install -U`, and because init
    never clobbers, the stale copy would win forever.
    """
    init._install_payload(tmp_path, force=False)
    assert not list(tmp_path.rglob("session-pulse.py"))
    assert not list(tmp_path.rglob("validate-receipts.py"))


def test_payload_survives_a_user_edit(tmp_path: pathlib.Path) -> None:
    init._install_payload(tmp_path, force=False)
    edited = tmp_path / ".claude" / "agents" / "coder.md"
    mine = "---\nname: coder\n---\n\nmy own coder definition\n"
    edited.write_text(mine, encoding="utf-8")

    copied, skipped = init._install_payload(tmp_path, force=False)
    assert copied == []
    assert len(skipped) == 69
    assert edited.read_text(encoding="utf-8") == mine

    init._install_payload(tmp_path, force=True)
    assert edited.read_text(encoding="utf-8") != mine


# ---------------------------------------------------------------------------
# The never-clobber contract fails CLOSED
# ---------------------------------------------------------------------------

def test_exists_or_fail_reports_absent_and_present(tmp_path: pathlib.Path) -> None:
    assert init._exists_or_fail(tmp_path / "nope") is False
    real = tmp_path / "yes"
    real.write_text("x", encoding="utf-8")
    assert init._exists_or_fail(real) is True


def test_exists_or_fail_aborts_when_stat_fails(tmp_path: pathlib.Path) -> None:
    """A stat that fails for any reason other than absence must ABORT, not write.

    ENAMETOOLONG is the trigger because it is deterministic on every POSIX
    filesystem and needs no chmod, so unlike a mode-000 directory this test is
    not a silent no-op when the suite runs as root.
    """
    unstatable = tmp_path / ("x" * 5000)
    with pytest.raises(OSError) as raw:
        os.lstat(unstatable)
    assert raw.value.errno not in (errno.ENOENT, errno.ENOTDIR)

    # The behaviour being replaced: the old guard called this a clean miss.
    assert unstatable.exists() is False

    with pytest.raises(init.ClobberRisk):
        init._exists_or_fail(unstatable)


def test_copy_templates_aborts_rather_than_overwriting_an_unstatable_dest(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: the abort reaches the copy loop, not just the helper."""
    bad_name = "x" * 5000
    with pytest.raises(init.ClobberRisk):
        init._copy_templates(tmp_path / bad_name, {"env.example": b"data"}, force=False)


def test_cli_exits_1_instead_of_tracebacking_on_an_unstatable_target(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    """The target-directory check is the same fail-open shape, one layer up.

    It destroys nothing, because mkdir does not overwrite. It did hand the user a
    raw OSError traceback instead of the error path every other failure in this
    script takes, which is how a fail-open guard hides in plain sight.
    """
    monkeypatch.setattr("sys.argv", ["wulong init", str(tmp_path / ("x" * 5000))])
    with pytest.raises(SystemExit) as exc:
        init.main()
    assert exc.value.code == 1
    assert "ERROR" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# All four overlays, not just the safe one
# ---------------------------------------------------------------------------

def test_all_four_overlays_skip_on_a_second_run(tmp_path: pathlib.Path) -> None:
    """Every overlay, including the env file, survives a re-init.

    The suite previously asserted this for exactly one of the four, and the
    unasserted ones are the two that actually hold secrets.
    """
    if not _stat_works_on_dotfiles(tmp_path):
        pytest.skip("filesystem denies stat() on dotfiles; run outside the sandbox")

    templates = {name: b"template body\n" for name in init._TEMPLATE_DEST}
    copied, skipped = init._copy_templates(tmp_path, templates, force=False)
    assert sorted(copied) and skipped == []
    assert len(copied) == 4

    edits = {}
    for dest_rel in init._TEMPLATE_DEST.values():
        dest = tmp_path / dest_rel
        edits[dest_rel] = f"user edit in {dest_rel}\n"
        dest.write_text(edits[dest_rel], encoding="utf-8")

    copied2, skipped2 = init._copy_templates(tmp_path, templates, force=False)
    assert copied2 == []
    assert len(skipped2) == 4, f"only {len(skipped2)} of 4 overlays were skipped"
    for dest_rel, body in edits.items():
        assert (tmp_path / dest_rel).read_text(encoding="utf-8") == body


# ---------------------------------------------------------------------------
# C5: init gitignores the overlay files it creates
# ---------------------------------------------------------------------------

def test_init_gitignores_every_overlay_it_creates(tmp_path: pathlib.Path) -> None:
    added = init._ensure_gitignore(tmp_path)
    body = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    for dest_rel in init._TEMPLATE_DEST.values():
        assert dest_rel in added
        assert dest_rel in body


def test_gitignore_is_appended_not_rewritten(tmp_path: pathlib.Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n*.pyc\n", encoding="utf-8")
    init._ensure_gitignore(tmp_path)
    body = gitignore.read_text(encoding="utf-8")
    assert body.startswith("build/\n*.pyc\n")
    assert "scrub-patterns.txt" in body

    assert init._ensure_gitignore(tmp_path) == []
    assert gitignore.read_text(encoding="utf-8") == body
