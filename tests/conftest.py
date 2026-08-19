"""Session guard: a skipped test is a failure wherever zero skips is the standard.

docs/CONTRIBUTING.md has said "zero skips is the standard, a skip is treated as a
failure in CI" since 0.2.0, and nothing anywhere enforced it. tests/test_wheel_cli.py
skips all six of its tests when a wheel cannot be built, and those six are the only
tests that reproduce a `pip install wulong` user, so losing them left the suite
summarising green with the flagship coverage gone. That is the false-green class this
release exists to close.

Opt-in by environment variable, because a local run legitimately skips: a machine
without the `build` module, or a sandbox that denies stat() on dotfiles. CI sets the
variable, so a skip there is a red build and someone has to look at it.

xfail is NOT a skip and is not counted: pytest files it under "xfailed".

ponytail: one stdlib pytest hook, no plugin dependency and no new config file.
Ceiling is a boolean; upgrade path is an allow-list of permitted skip reasons if one
is ever genuinely legitimate in CI.
"""
import json
import os
import pathlib
import shutil

import pytest

REQUIRE_NO_SKIPS = "WULONG_REQUIRE_NO_SKIPS"


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get(REQUIRE_NO_SKIPS) != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if not skipped:
        return
    reporter.write_sep("=", f"{len(skipped)} SKIPPED with {REQUIRE_NO_SKIPS}=1", red=True)
    for report in skipped:
        reporter.write_line(f"  SKIPPED {report.nodeid}")
    reporter.write_line(
        "A skipped test proves nothing. Install what it needs, or delete the claim "
        "in docs/CONTRIBUTING.md that zero skips is the standard."
    )
    session.exitstatus = 1


# ---------------------------------------------------------------------------
# A vault where all nine doctor axes RUN.
#
# A bare fixture is not enough to test the verdict: on one, only B, G, H and I
# say anything at all, and every run comes back PARTIAL because four axes cannot
# run. Reaching GREEN, or the ADVISORY token that sits just under it, needs a
# tree that satisfies every axis's precondition. Reaching all nine TAGS needs
# more still: A, C, D, E and F are silent unless driven over their thresholds.
# ---------------------------------------------------------------------------

# Row format is the real one, taken from check-enforcement-rules.py's own
# self-check fixture: rule_id | statement | mechanism | action | source, with no
# outer pipes. A markdown table with leading pipes parses every rule_id as "".
_WARDENS_ONE_MISSING = """---
type: meta
---
```wardens
present-rule | A present rule. | Meta/sync/check-enforcement-rules.py | BLOCK | NN01
missing-rule | A missing rule. | Meta/sync/does-not-exist.py | WARN | NN02
```
"""

_WARDENS_ALL_PRESENT = _WARDENS_ONE_MISSING.replace(
    "missing-rule | A missing rule. | Meta/sync/does-not-exist.py | WARN | NN02\n", "")

# Parses, and declares rules, but not one of them is a path this axis can check.
_WARDENS_NO_MECHANISMS = _WARDENS_ONE_MISSING.replace(
    "Meta/sync/check-enforcement-rules.py | BLOCK", "LLM-GATE:contrarian | BLOCK").replace(
    "Meta/sync/does-not-exist.py | WARN", "GAP | N/A")

_SYNC = pathlib.Path(__file__).resolve().parent.parent / "wulong" / "sync"


@pytest.fixture
def all_axes_vault(tmp_path):
    """Factory returning a vault root on which doctor reports SKIPPED: 0.

    Keyword arguments each move exactly one axis off silent, so a test names the
    single thing it is about and inherits a clean tree for everything else.
    """
    counter = {"n": 0}

    def _build(*, strays=0, hook_fired=True, wardens=None,
               rulebook=True, noisy=False):
        if wardens is None:
            wardens = _WARDENS_ONE_MISSING if noisy else _WARDENS_ALL_PRESENT
        counter["n"] += 1
        vault = tmp_path / f"vault{counter['n']}"
        for d in ("00-Inbox", "01-Projects/demo", "02-Areas/allowed", "03-Resources",
                  "04-Archive", "05-People", "06-Meetings", "07-Daily", "MOC",
                  "Templates", "Meta/handoffs", "Meta/sync", ".claude", ".wulong"):
            (vault / d).mkdir(parents=True, exist_ok=True)
        (vault / "CLAUDE.md").write_text("# vault\n", encoding="utf-8")
        # E is silent only when no scanned folder is empty.
        for d in vault.rglob("*"):
            if d.is_dir() and not any(d.iterdir()):
                (d / "index.md").write_text("# index\n", encoding="utf-8")

        # B runs once an allow-list exists. C and D need thresholds that a
        # deliberately noisy tree can cross.
        (vault / "Meta/sync/vault-health-thresholds.json").write_text(json.dumps({
            "handoff_backlog_red": 0 if noisy else 1000,
            "stray_code_allow_list": ["02-Areas/allowed"],
        }), encoding="utf-8")

        # G needs a runnable drift-scan.py plus the reference map it reads from
        # its own parent's parent, which lands at Meta/ once it is copied here.
        shutil.copy2(_SYNC / "drift-scan.py", vault / "Meta/sync/drift-scan.py")
        drift_src = tmp_path / f"driftsrc{counter['n']}"
        drift_src.mkdir()
        (drift_src / "app.py").write_text(
            "import oldproject\n" if noisy else "import newproject\n", encoding="utf-8")
        (vault / "Meta/reference-map.md").write_text(
            "```refmap\n"
            "aliases:\n  - oldproject -> newproject\n"
            f"scan_repos:\n  - {drift_src}\n"
            "code_extensions:\n  - .py\n"
            "doc_extensions:\n  - .md\n"
            "excludes:\n  - .git\n"
            "```\n", encoding="utf-8")
        if noisy:
            # G compares against a baseline, so a stale ref only speaks when the
            # baseline it is measured against is lower.
            (vault / "Meta/sync/drift-baseline.json").write_text(
                json.dumps({"high_signal_count": 0}), encoding="utf-8")

        # H needs the validator and, separately, a rulebook for it to read.
        shutil.copy2(_SYNC / "check-enforcement-rules.py",
                     vault / "Meta/sync/check-enforcement-rules.py")
        if rulebook:
            (vault / "Meta/enforcement-rules.md").write_text(wardens, encoding="utf-8")

        # I needs the opt-in wiring; the log decides warning versus silence.
        (vault / ".claude/settings.json").write_text(json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}),
            encoding="utf-8")
        (vault / ".wulong/hook-events.jsonl").write_text(
            json.dumps({"ts": "2099-01-01T00:00:00", "hook": "stop-slop",
                        "outcome": "ok"}) + "\n" if hook_fired else "",
            encoding="utf-8")

        for i in range(strays):
            (vault / "01-Projects/demo" / f"stray{i}.py").write_text("# x\n", encoding="utf-8")

        if noisy:
            for i in range(12):                      # A, threshold 10
                (vault / "00-Inbox" / f"n{i}.md").write_text("x", encoding="utf-8")
            (vault / "Meta/handoffs/h.md").write_text("x", encoding="utf-8")  # C
            for i in range(25):                      # D, threshold 20
                (vault / "01-Projects/demo" / f"orphan{i}.md").write_text("x", encoding="utf-8")
            (vault / "01-Projects/lonely").mkdir()   # E
            (vault / "01-Projects/demo/link.md").write_text(
                "See [[NoSuchNote]].\n", encoding="utf-8")                    # F
        return vault

    return _build
