"""test_hook_wiring.py - the opt-in Stop hook: is it wired, does it fire, does it say so.

Wiring a hook is the one change whose whole failure mode is SILENCE. A wrong
event, a stale path, a timeout under the real runtime and a settings file that
was never written all produce the same result: nothing happens, no error, no
log. Every test here exists to turn one of those silences into a red.

Four things are pinned.

1. SHAPE. The settings file wulong writes is compared field by field against the
   one form with production evidence: interpreter plus absolute path, no matcher
   on an event that carries no tool, an explicit timeout.
2. AGREEMENT. wulong-init.py names the event it wires and the hook names the
   event it parses. The hook's constant is read by AST, never by regex over the
   docstring, because a docstring stays green while the parsing underneath moves.
3. BEHAVIOUR. The real script is run as a subprocess on a real payload. A Stop
   payload must block on stdout; a payload for any other event must produce a
   detectable no-op. A constant that says "Stop" proves nothing on its own.
4. VISIBILITY. The fail-open path emits no output by construction, so it is
   proved through the durable log instead, and the heartbeat is proved by
   separating never-fired (no records) from nothing-to-do (allow records).

ponytail: stdlib ast, json, subprocess, tmp_path. No fixture library, no new
dependency. Ceiling is a hand-rolled AST read of one module-level constant;
upgrade path if this ever needs more is importing the hook as a module, which
its hyphenated filename currently forbids.
"""
import ast
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_INIT_PY = _REPO / "wulong" / "sync" / "wulong-init.py"
_HOOK_PY = _REPO / "wulong" / "payload" / ".claude" / "hooks" / "stop-slop-hook.py"

# The codepoint under test, written as an escape rather than a literal. A test
# for an em-dash detector needs the byte, and a literal here is indistinguishable
# at a glance from an en dash or a hyphen, which is the bug it exists to catch.
EM_DASH = "\u2014"


def _load_init():
    spec = importlib.util.spec_from_file_location("wulong_init_hooks", _INIT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init = _load_init()


def _module_constant(path: pathlib.Path, name: str):
    """Read a module-level string assignment by AST.

    AST, never regex: a regex over the source finds the string in a docstring, a
    comment or a dead branch and reports agreement that does not exist.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                assert isinstance(node.value, ast.Constant), f"{name} is not a literal"
                return node.value.value
    raise AssertionError(f"{path.name} has no module-level {name}")


def _vault(tmp_path: pathlib.Path) -> pathlib.Path:
    """A target with the payload installed and .wulong/ present, as init leaves it."""
    init._install_payload(tmp_path, force=False)
    (tmp_path / ".wulong").mkdir(exist_ok=True)
    return tmp_path


def _run_hook(vault: pathlib.Path, event: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WULONG_ROOT"] = str(vault)
    return subprocess.run(
        [sys.executable, str(vault / init.HOOK_SCRIPT_REL)],
        input=json.dumps(event), capture_output=True, text=True, env=env,
    )


def _log_records(vault: pathlib.Path) -> list[dict]:
    log = vault / ".wulong" / "hook-events.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _transcript(vault: pathlib.Path, text: str) -> str:
    path = vault / "transcript.jsonl"
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }) + "\n", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1. The settings file: which file, what shape
# ---------------------------------------------------------------------------

def test_the_settings_file_is_the_project_level_one() -> None:
    """Not the user-level file (out of the target), not the local one (gitignored)."""
    assert init.HOOK_SETTINGS_REL == ".claude/settings.json"
    assert "settings.local.json" not in init.HOOK_SETTINGS_REL
    assert not init.HOOK_SETTINGS_REL.startswith(("/", "~"))


def test_settings_shape_is_exactly_the_form_with_production_evidence(tmp_path) -> None:
    settings = init.hook_settings(tmp_path)
    assert list(settings) == ["hooks"]
    assert list(settings["hooks"]) == [init.HOOK_EVENT]
    groups = settings["hooks"][init.HOOK_EVENT]
    assert len(groups) == 1
    # No matcher: the Stop event carries no tool name, so a matcher key would be
    # decoration that reads as filtering the script does not do.
    assert "matcher" not in groups[0]
    entries = groups[0]["hooks"]
    assert len(entries) == 1
    assert entries[0]["type"] == "command"
    assert entries[0]["timeout"] == init.HOOK_TIMEOUT_SECONDS == 10


def test_the_command_names_an_interpreter_so_the_exec_bit_is_irrelevant(tmp_path) -> None:
    """The hook is mode 0644 in the repo and in the payload, init copies bytes not
    modes, and wheels do not reliably preserve the exec bit on package data. A
    bare-path command would depend on all three. This is why no test in this file
    asserts the file is executable: under this command form that assertion would
    be theatre.
    """
    command = init.hook_command(tmp_path)
    interpreter, _, path = command.partition(" ")
    assert interpreter == init.HOOK_INTERPRETER == "python3"
    assert pathlib.PurePath(path).is_absolute()
    assert path.endswith("/.claude/hooks/stop-slop-hook.py")
    assert not os.access(_HOOK_PY, os.X_OK), (
        "the hook became executable; either the command form changed to a bare "
        "path, or a stray chmod landed. The wheel will not preserve this bit."
    )


def test_the_wired_command_points_at_a_file_that_exists(tmp_path) -> None:
    vault = _vault(tmp_path)
    init._wire_hooks(vault, force=False)
    settings = json.loads((vault / init.HOOK_SETTINGS_REL).read_text(encoding="utf-8"))
    command = settings["hooks"][init.HOOK_EVENT][0]["hooks"][0]["command"]
    assert pathlib.Path(command.split(" ", 1)[1]).is_file()


# ---------------------------------------------------------------------------
# 2. Event agreement, and the tripwire for the error that has happened three times
# ---------------------------------------------------------------------------

def test_the_wired_event_is_the_event_the_hook_parses() -> None:
    assert _module_constant(_HOOK_PY, "HOOK_EVENT") == init.HOOK_EVENT == "Stop"


def test_no_settings_file_names_sessionstart(tmp_path) -> None:
    """SessionStart has been the wrong answer three times in this project's drafts.
    It fires at session start, where there is no assistant turn to scan, so it is
    the wrong event that looks most right.
    """
    vault = _vault(tmp_path)
    init._wire_hooks(vault, force=False)
    written = (vault / init.HOOK_SETTINGS_REL).read_text(encoding="utf-8")
    assert "SessionStart" not in written
    for path in _REPO.rglob("settings*.json"):
        if any(part in (".venv", ".git", "build", "dist") for part in path.parts):
            continue
        assert "SessionStart" not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# 3. Behaviour: the payload for the wired event works, another event no-ops
# ---------------------------------------------------------------------------

def test_the_wired_event_payload_produces_the_designed_effect(tmp_path) -> None:
    vault = _vault(tmp_path)
    result = _run_hook(vault, {
        "hook_event_name": init.HOOK_EVENT,
        "transcript_path": _transcript(vault, "a " + EM_DASH + " b"),
    })
    assert result.returncode == 0
    # Stop's block channel is stdout as JSON. A PreToolUse deny is exit 2 plus
    # stderr; using that channel here would print an error and block nothing.
    assert result.stderr == ""
    assert json.loads(result.stdout)["decision"] == "block"


def test_a_wrong_event_payload_produces_a_detectable_no_op(tmp_path) -> None:
    """The payload carries a transcript_path that WOULD block under Stop.

    That is the whole test. `transcript_path` is a common field across Claude
    Code payloads, not a Stop-only one, so an absent transcript proves nothing
    about the event: this version of the test failed against the hook until the
    hook actually read `hook_event_name`. The log records the event RECEIVED,
    not this hook's own constant, so a mis-wiring reads as itself.
    """
    vault = _vault(tmp_path)
    result = _run_hook(vault, {
        "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {},
        "transcript_path": _transcript(vault, "a " + EM_DASH + " b"),
    })
    assert result.returncode == 0
    assert result.stdout == ""
    records = _log_records(vault)
    assert [r["outcome"] for r in records] == ["allow"]
    assert records[0]["reason"] == "wrong_event"
    assert records[0]["event"] == "PreToolUse"


def test_a_payload_with_no_event_name_is_still_scanned(tmp_path) -> None:
    """Absent is not wrong. Hand-fed payloads and any runner that omits the
    field must still be gated, so the guard fires only on a name that is present
    AND different. Refusing an absent name would disable the gate silently,
    which is the failure mode this whole file exists to prevent.
    """
    vault = _vault(tmp_path)
    result = _run_hook(vault, {"transcript_path": _transcript(vault, "a " + EM_DASH + " b")})
    assert json.loads(result.stdout)["decision"] == "block"


def test_a_clean_turn_is_allowed(tmp_path) -> None:
    vault = _vault(tmp_path)
    result = _run_hook(vault, {"transcript_path": _transcript(vault, "no long dash here")})
    assert result.stdout == ""
    assert [r["reason"] for r in _log_records(vault)] == ["clean"]


# ---------------------------------------------------------------------------
# 4. The fail-open path, the heartbeat, and what the log refuses to record
# ---------------------------------------------------------------------------

def test_the_fail_open_path_leaves_a_durable_record(tmp_path) -> None:
    """Fail-open emits nothing by construction, so without the log a hook that
    has been crashing for a week is indistinguishable from one that is fine.
    """
    vault = _vault(tmp_path)
    result = _run_hook(vault, {"transcript_path": str(vault / "does-not-exist.jsonl")})
    assert result.returncode == 0
    assert result.stdout == ""
    records = _log_records(vault)
    assert len(records) == 1
    assert records[0]["outcome"] == "failopen"
    assert records[0]["stage"] == "read_transcript"
    assert records[0]["error"] == "FileNotFoundError"


def test_the_heartbeat_separates_never_fired_from_nothing_to_do(tmp_path) -> None:
    """The two silences a hook can be in. Without a record on the ordinary allow
    they are the same empty log, and a stale command path ships green forever.
    """
    vault = _vault(tmp_path)
    assert _log_records(vault) == [], "never fired: no records at all"

    _run_hook(vault, {"transcript_path": _transcript(vault, "nothing to do here")})
    records = _log_records(vault)
    assert len(records) == 1
    assert records[0]["outcome"] == "allow"
    assert records[0]["event"] == init.HOOK_EVENT
    assert records[0]["hook"] == "stop-slop"
    assert records[0]["ts"].endswith("+00:00")


def test_the_log_appends_and_never_records_message_text(tmp_path) -> None:
    """A delivery gate that wrote every blocked message to disk would be a worse
    leak than the one it prevents.
    """
    vault = _vault(tmp_path)
    secret = "my private sentence " + EM_DASH + " with a long dash"
    _run_hook(vault, {"transcript_path": _transcript(vault, secret)})
    _run_hook(vault, {"transcript_path": _transcript(vault, secret)})
    raw = (vault / ".wulong" / "hook-events.jsonl").read_text(encoding="utf-8")
    assert len(_log_records(vault)) == 2, "append-only, not truncate-and-write"
    assert "my private sentence" not in raw
    assert _log_records(vault)[0]["hits"] == 1


def test_no_wulong_directory_means_no_log_and_no_stray_directory(tmp_path) -> None:
    """A mis-resolved root must write nothing rather than scatter a .wulong/."""
    vault = tmp_path / "novault"
    vault.mkdir()
    init._install_payload(vault, force=False)
    result = _run_hook(vault, {"transcript_path": _transcript(vault, "x " + EM_DASH + " y")})
    assert json.loads(result.stdout)["decision"] == "block", "blocking is unaffected"
    assert not (vault / ".wulong").exists()


# ---------------------------------------------------------------------------
# 5. Opt-in, and the collision that would otherwise be silent
# ---------------------------------------------------------------------------

def test_init_writes_no_settings_file_by_default(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_INIT_PY), str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / init.HOOK_SETTINGS_REL).exists()
    assert "not wired" in proc.stdout


def test_with_hooks_wires_it_and_says_so(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_INIT_PY), str(tmp_path), "--with-hooks"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / init.HOOK_SETTINGS_REL).is_file()
    assert "Wired the delivery gate" in proc.stdout


def test_an_existing_settings_file_is_named_and_the_hook_is_not_wired(tmp_path) -> None:
    """The silent failure this exists to stop. init never clobbers, so a user who
    already has this file (anyone with Claude Code permissions configured) would
    otherwise type the flag, see a bare count, and get no hook.
    """
    settings = tmp_path / init.HOOK_SETTINGS_REL
    settings.parent.mkdir(parents=True)
    mine = '{"permissions": {"allow": ["Bash(ls:*)"]}}\n'
    settings.write_text(mine, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(_INIT_PY), str(tmp_path), "--with-hooks"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert settings.read_text(encoding="utf-8") == mine, "the user's file survives"
    assert "HOOK NOT WIRED" in proc.stdout
    assert str(settings) in proc.stdout, "the collision names the file, not a count"
    assert init.HOOK_EVENT in proc.stdout, "and prints what to paste"
    assert "--force" in proc.stdout


def test_force_overwrites_the_settings_file(tmp_path) -> None:
    settings = tmp_path / init.HOOK_SETTINGS_REL
    settings.parent.mkdir(parents=True)
    settings.write_text("{}\n", encoding="utf-8")
    _vault(tmp_path)
    status, _ = init._wire_hooks(tmp_path, force=True)
    assert status == "written"
    assert init.HOOK_EVENT in settings.read_text(encoding="utf-8")


def test_no_hook_script_means_no_settings_file(tmp_path) -> None:
    """Never point a settings file at a path that is not there."""
    status, detail = init._wire_hooks(tmp_path, force=False)
    assert status == "no-script"
    assert detail == init.HOOK_SCRIPT_REL
    assert not (tmp_path / init.HOOK_SETTINGS_REL).exists()


def test_the_opt_out_procedure_is_documented_where_the_flag_is() -> None:
    """R8: init never clobbers, so re-running it cannot remove the wiring. The
    only removal is by hand, and it belongs next to the flag that created it.
    """
    source = _INIT_PY.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", source)
    assert "--with-hooks" in source
    assert "To remove it" in source
    assert flat.count("To remove it") >= 2, "the docstring and the --help text"


# ---------------------------------------------------------------------------
# 6. The doctor axis is REGISTERED, and the published count matches the code
# ---------------------------------------------------------------------------

def _load_doctor():
    path = _REPO / "wulong" / "sync" / "vault-health-check.py"
    spec = importlib.util.spec_from_file_location("wulong_doctor_hooks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hook_axis_is_wired_into_the_runner(tmp_path) -> None:
    """Defining check_i_hook_health is not the same as running it.

    Deleting its line from run_checks left every other test in this file green,
    because they all call the axis function directly. An axis that is never
    invoked is the same silent nothing as a hook that is never wired.
    """
    doctor = _load_doctor()
    (tmp_path / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    report = doctor.run_checks(tmp_path)
    assert any("[I]" in line for line in report.lines), report.lines
    assert report.passed + report.skipped + report.failed == 9


def test_the_published_axis_count_equals_the_axis_count_the_code_runs(tmp_path) -> None:
    """Parse the number out of the CLI description, then measure it from a run."""
    doctor = _load_doctor()
    (tmp_path / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    report = doctor.run_checks(tmp_path)
    measured = report.passed + report.skipped + report.failed

    source = (_REPO / "wulong" / "sync" / "vault-health-check.py").read_text(encoding="utf-8")
    published = re.search(r"vault health scanner \((\d+) axes, A to ([A-Z])\)", source)
    assert published is not None, "the CLI description no longer states an axis count"
    assert int(published.group(1)) == measured
    assert published.group(2) == chr(ord("A") + measured - 1)


# Number words, because the docs write "nine axes" as often as "9 axes" and a
# digits-only sweep returns zero hits on the prose form. That is the exact hole
# this test exists to close: the stale count that shipped said "Eight axes:".
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# A sentence carrying one of these is describing a count that WAS true and says
# so. Both live instances are deliberate: the user guide's "used to skip 3 of
# the 8 axes it then had", and the CHANGELOG's Corrected item, which quotes the
# superseded number on purpose. Anything else must state the current count.
_SUPERSEDED_MARKERS = ("used to", "superseded")


def _axis_count_claims(name: str, text: str) -> list[tuple[str, int, str]]:
    """Every live "N axes" claim in a doc, as (doc, N, sentence).

    Plural only. "at least one axis could not run" and "a skipped axis" are
    correct English about a single axis and are not count claims, and matching
    the singular turns both into false reds.
    """
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        if any(m in sentence.lower() for m in _SUPERSEDED_MARKERS):
            continue
        for match in re.finditer(r"(\S+)\s+axes\b", sentence):
            token = match.group(1)
            if token.startswith(('"', "'")):
                # A QUOTED number is a quotation of text published elsewhere, not
                # a claim this sentence is making. The CHANGELOG quotes the
                # superseded "3 of the 8 axes" on purpose, and the entry
                # describing this very defect quotes "Eight axes:" to name it.
                continue
            token = token.strip("`*_,.:;()")
            value = int(token) if token.isdigit() else _NUMBER_WORDS.get(token.lower())
            if value is not None:
                out.append((name, value, sentence[:160]))
    return out


def test_no_shipped_doc_publishes_a_stale_axis_count(tmp_path) -> None:
    """The in-code description is not the only place the count is published.

    The count moved from eight to nine and `docs/USERGUIDE.md` kept saying
    "Eight axes:" over a list of eight, nine lines above a table saying "all nine
    axes ran". Nothing caught it, because the axis-count test above reads only
    the CLI description string inside `vault-health-check.py`.

    SCOPE, and why it is not everything: released CHANGELOG entries are a record
    of what was published and are not edited in place, which is that file's own
    stated policy and is asserted here, so only its Unreleased section is live.
    `docs/CLAIMS-AUDIT.md` is a was/now table whose middle column is a historical
    record of claims that were false, so its stale numbers are the point of it.

    ponytail: one regex over sentences, no parser. Ceiling = it checks the NUMBER
    a doc publishes, not the LENGTH of any list beside it, so a tenth axis added
    to the list without touching the number would pass here. Upgrade path = count
    the comma-separated items in the "N axes:" enumeration, once a second doc
    enumerates them and the shape is worth pinning.
    """
    doctor = _load_doctor()
    (tmp_path / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    report = doctor.run_checks(tmp_path)
    measured = report.passed + report.skipped + report.failed

    changelog = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Released entries are a record of what was published and are not " \
           "edited in place." in re.sub(r"\s+", " ", changelog), \
        "the released-entry freeze policy this test relies on is gone"
    start = changelog.index("## [Unreleased]")
    unreleased = changelog[start:changelog.index("\n## [", start + 5)]

    claims = _axis_count_claims("CHANGELOG.md (Unreleased)", unreleased)
    for doc in (_REPO / "README.md", _REPO / "SECURITY.md",
                _REPO / "docs" / "USERGUIDE.md", _REPO / "docs" / "ARCHITECTURE.md",
                _REPO / "wulong" / "payload" / "CLAUDE.md"):
        if doc.exists():
            claims += _axis_count_claims(doc.name, doc.read_text(encoding="utf-8"))

    assert claims, "the axis count is published nowhere, so this test guards nothing"
    stale = [c for c in claims if c[1] != measured]
    assert stale == [], stale


def test_a_declined_opt_in_skips_the_axis_rather_than_failing_it(tmp_path) -> None:
    """A red cross over a deliberate decline is how a check trains people to
    ignore it. A skip is neither a pass nor a failure and does not move the exit
    code, which is the correct severity for a choice the user made.
    """
    doctor = _load_doctor()
    (tmp_path / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    lines = [ln for ln in doctor.run_checks(tmp_path).lines if "[I]" in ln]
    assert len(lines) == 1
    assert lines[0].startswith("SKIP ")
    assert "--with-hooks" in lines[0], "a skip must say what it needs"


def test_a_wired_but_never_fired_hook_warns_instead_of_skipping(tmp_path) -> None:
    doctor = _load_doctor()
    vault = _vault(tmp_path)
    (vault / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    init._wire_hooks(vault, force=False)
    lines = [ln for ln in doctor.run_checks(vault).lines if "[I]" in ln]
    assert len(lines) == 1
    assert lines[0].startswith("WARNING ")
    assert "NEVER FIRED" in lines[0]


def test_the_axis_reports_the_fail_open_count(tmp_path) -> None:
    doctor = _load_doctor()
    vault = _vault(tmp_path)
    (vault / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    init._wire_hooks(vault, force=False)
    _run_hook(vault, {"transcript_path": str(vault / "gone.jsonl")})
    lines = [ln for ln in doctor.run_checks(vault).lines if "[I]" in ln]
    assert len(lines) == 1
    assert "failed open 1 time(s)" in lines[0]
    assert "FileNotFoundError" in lines[0]


def test_the_axis_names_the_event_a_mis_wiring_actually_sent(tmp_path) -> None:
    """A wrong event is NOT the silent state. It fires, it logs, and the axis has
    to say which event arrived, otherwise a healthy-looking log sits over a gate
    that never runs.
    """
    doctor = _load_doctor()
    vault = _vault(tmp_path)
    (vault / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    init._wire_hooks(vault, force=False)
    _run_hook(vault, {
        "hook_event_name": "PreToolUse",
        "transcript_path": _transcript(vault, "a " + EM_DASH + " b"),
    })
    lines = [ln for ln in doctor.run_checks(vault).lines if "[I]" in ln]
    assert len(lines) == 1
    assert lines[0].startswith("WARNING ")
    assert "PreToolUse x1" in lines[0]


def test_a_healthy_fired_hook_is_silent(tmp_path) -> None:
    doctor = _load_doctor()
    vault = _vault(tmp_path)
    (vault / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    init._wire_hooks(vault, force=False)
    _run_hook(vault, {"transcript_path": _transcript(vault, "all clean here")})
    assert [ln for ln in doctor.run_checks(vault).lines if "[I]" in ln] == []


# ---------------------------------------------------------------------------
# 7. The vocabulary rule, both directions
# ---------------------------------------------------------------------------

# Direction A: a doc that ELEVATES a hook into wulong's enforcement chain. False:
# wulong ships no pre-commit, no CI job and no scheduler, so nothing runs a hook
# for you. Every phrase here is an assertion, so none of them can appear inside a
# sentence that denies the claim.
_ELEVATION = (
    "enforced by a hook", "enforced by the hook", "enforced by hooks",
    "hook enforces", "hooks enforce", "hook enforcement", "hook-enforced",
)

# Direction B: a doc that DEMOTES the one hook that blocks. Also false: once
# wired, the Stop hook emits a block decision on stdout and the model rewrites.
_DEMOTION = (
    "advisory", "non-blocking", "does not block", "do not block",
    "cannot block", "never blocks", "purely informational",
)


def _shipped_docs() -> list[pathlib.Path]:
    docs = [_REPO / "README.md", _REPO / "SECURITY.md", _REPO / "CHANGELOG.md"]
    docs += sorted((_REPO / "docs").glob("*.md"))
    docs.append(_REPO / "wulong" / "payload" / "CLAUDE.md")
    return [d for d in docs if d.exists()]


def hook_vocabulary_violations(name: str, text: str) -> list[str]:
    """Every sentence mentioning a hook that also makes a false claim about one.

    Sentence-scoped, because both vocabularies are legitimate elsewhere in these
    documents: the gate IS enforcement, and plenty of things that are not hooks
    are advisory.
    """
    flat = re.sub(r"\s+", " ", text).lower()
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", flat):
        if "hook" not in sentence:
            continue
        for phrase in _ELEVATION + _DEMOTION:
            if phrase in sentence:
                out.append(f"{name}: [{phrase}] {sentence[:160]}")
    return out


def test_no_shipped_doc_makes_a_false_claim_about_a_hook() -> None:
    found = []
    for doc in _shipped_docs():
        found += hook_vocabulary_violations(doc.name, doc.read_text(encoding="utf-8"))
    assert found == [], found


@pytest.mark.parametrize("sentence", [
    "The em dash rule is enforced by a hook that wulong runs for you.",
    "Delivery is blocked because hooks enforce NN#21 automatically.",
    "The Stop hook is advisory and changes nothing on its own.",
    "This hook is non-blocking, so it can only warn.",
])
def test_the_vocabulary_check_reds_in_both_directions(sentence: str) -> None:
    """A one-directional grep would have missed the live risk. The failure this
    change had to avoid was calling a hook that blocks advisory, which no search
    for the word enforcement can see.
    """
    assert hook_vocabulary_violations("fixture.md", sentence) != []


def test_the_two_true_claims_are_both_published() -> None:
    """One claim per level, because one sentence cannot carry both truths.
    Project level: nothing wulong ships runs a hook. Session level: once wired,
    this hook blocks.
    """
    readme = re.sub(r"\s+", " ", (_REPO / "README.md").read_text(encoding="utf-8"))
    assert "wulong ships nothing that runs them for you" in readme
    assert "blocks your own delivery" in readme


# ---------------------------------------------------------------------------
# 7. Every one of the nine axes is REGISTERED, and every one can still speak
# ---------------------------------------------------------------------------

def test_every_one_of_the_nine_axes_appears_in_the_output(all_axes_vault) -> None:
    """The sum above cannot tell a deleted axis from a substituted one.

    Delete check_b's call from run_checks and duplicate check_a's, and the sum
    stays 9 while axis B is gone. Only a per-tag assertion sees that, and only
    on a fixture that FORCES every axis to emit: on a bare tree A, C, D, E and F
    are silent-green and print nothing at all, so nine tags need eleven inbox
    items, a crossed handoff threshold, twenty-one orphans, an empty folder, a
    broken wikilink, a stale reference below its baseline, a missing enforcer
    and an unfired hook, all at once.

    ponytail ceiling, stated rather than chased: this catches a REGISTRATION
    mutation, not a SEVERITY one. Change check_e to emit YELLOW instead of RED
    and the tag is still there and the sum is still 9. The selftest at the foot
    of vault-health-check.py pins RED for A, B, E and F; D, G, H and I are
    unpinned. Upgrade path = assert the prefix alongside the tag, once the
    severity of each axis is a decision worth freezing rather than a default.
    """
    doctor = _load_doctor()
    report = doctor.run_checks(all_axes_vault(noisy=True, strays=1, hook_fired=False))

    tags = {m for line in report.lines for m in re.findall(r"\[([A-I])\]", line)}
    assert tags == set("ABCDEFGHI"), f"axes that never spoke: {set('ABCDEFGHI') - tags}"
    assert report.passed + report.skipped + report.failed == 9
    assert report.skipped == 0, "the forcing fixture must leave nothing unable to run"


def test_the_verdict_tokens_the_code_prints_are_the_ones_the_docs_publish() -> None:
    """The count guard above matches `N axes` claims and is blind to this.

    Two live sites enumerate the verdict tokens in a table: the module docstring
    of vault-health-check.py and the table in docs/USERGUIDE.md. Adding a fourth
    token left both of them saying three, and nothing anywhere went red for it.

    SCOPE: CHANGELOG released entries and docs/CLAIMS-AUDIT.md are historical
    records, exempt by the same freeze policy the axis-count guard asserts.

    ponytail: substring checks against the two table shapes, no markdown parser.
    Ceiling = it catches a MISSING row, not a wrong Meaning or a wrong Exit
    column. Upgrade path = parse the table and compare the exit column against a
    real run, once a token's exit code is in question.
    """
    path = _REPO / "wulong" / "sync" / "vault-health-check.py"
    source = path.read_text(encoding="utf-8")
    tokens = set(re.findall(r'print\(f?"(\w+) vault-health', source))
    assert tokens >= {"RED", "PARTIAL", "GREEN"}, tokens

    own_table = ast.get_docstring(ast.parse(source))
    for token in sorted(tokens):
        assert f"-> {token}," in own_table, \
            f"vault-health-check.py's own verdict table does not list {token}"

    guide = (_REPO / "docs" / "USERGUIDE.md").read_text(encoding="utf-8")
    for token in sorted(tokens):
        assert f"| `{token}` |" in guide, \
            f"docs/USERGUIDE.md's verdict table does not list {token}"


def test_axis_h_never_reports_an_empty_inventory_as_clean(tmp_path, all_axes_vault) -> None:
    """check() returns present=[] missing=[] for BOTH "no rulebook at all" and
    "a rulebook that declares no mechanical enforcer", and the old `if missing:`
    read both as nothing-to-report. That is a silent green over an axis that
    inventoried nothing, which is not the same as one that found nothing wrong.

    LATENT, not live. It needs a vault carrying Meta/sync/check-enforcement-rules.py,
    and `wulong init` copies no engine scripts, so on everything the installer
    produces the axis SKIPs at the script gate instead. It is reachable on a
    vault that HAS the engine scripts and then loses or empties its rulebook.

    check_h keeps resolving the validator VAULT-side, unchanged. Nothing here
    asks it to look in the package, and ex01.txt does not move.
    """
    doctor = _load_doctor()
    from conftest import _WARDENS_NO_MECHANISMS

    absent = doctor.check_h_warden_validator(all_axes_vault(rulebook=False))
    assert absent and absent[0].startswith(doctor.SKIP_PREFIX), absent
    assert "enforcement-rules.md" in absent[0], absent

    empty = doctor.check_h_warden_validator(
        all_axes_vault(wardens=_WARDENS_NO_MECHANISMS))
    assert empty, "a rulebook with zero mechanical rows must not be silent"
    assert empty[0].startswith(doctor.ADVISORY_PREFIXES), empty
    assert not empty[0].startswith(doctor.SKIP_PREFIX), \
        "the rulebook is present, so this is not a skip"

    # No validator script at all is still the original skip, naming the script.
    (tmp_path / "CLAUDE.md").write_text("# vault", encoding="utf-8")
    no_script = doctor.check_h_warden_validator(tmp_path)
    assert no_script[0].startswith(doctor.SKIP_PREFIX), no_script
    assert "check-enforcement-rules.py" in no_script[0], no_script
