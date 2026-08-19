"""test_doc_claims.py - the shipped numbers must survive contact with the disk.

Every test here parses the number (and where the doc states one, the path) OUT
OF THE DOCUMENT, then measures the same thing from disk and asserts equality.
Nothing is hardcoded, so editing a doc without changing the code goes red, and
changing the code without editing the doc goes red too.

Sentences are matched after whitespace collapse because the docs are hard
wrapped, and are located by TEXT, never by line number.

NOTE FOR CHANGE C: a test in this file going red during Change C is a C-OWNED
fix, not a Change B regression. C moves the agent payload and reconciles the
doubled sync scripts, which moves both the agent-tracking claim and the
WULONG_ROOT figure. C's definition of done includes leaving this file green.

ponytail: stdlib ast, re, pathlib. No parser dependency, no doc fixtures.
"""
import ast
import importlib.util
import pathlib
import re
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent

_README = _REPO / "README.md"
_ARCHITECTURE = _REPO / "docs" / "ARCHITECTURE.md"
_USERGUIDE = _REPO / "docs" / "USERGUIDE.md"
_PYPROJECT = _REPO / "pyproject.toml"

_SYNC_DIR = _REPO / "wulong" / "sync"

# THE agents directory. Single module-level constant on purpose: Change C1 moved
# the payload here from the repo root, and this one line plus the doc sentences
# were the whole edit. Each test asserts this constant equals the path parsed out
# of the doc, then measures at the parsed path, so updating one without the other
# goes red.
_AGENTS_DIR = _REPO / "wulong" / "payload" / ".claude" / "agents"


def _flat(path: pathlib.Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _search(path: pathlib.Path, pattern: str) -> re.Match:
    match = re.search(pattern, _flat(path))
    assert match is not None, f"{path.name} no longer states the claim: {pattern!r}"
    return match


# ---------------------------------------------------------------------------
# 1. fcntl top-level import count vs the POSIX-classifier justification
# ---------------------------------------------------------------------------

def _top_level_fcntl_imports(directory: pathlib.Path) -> int:
    """Count MODULE-TOP-LEVEL `import fcntl` only.

    Imports nested inside a function do not make the script POSIX-only at import
    time, so they must not be counted. Walking tree.body rather than ast.walk is
    what draws that line.
    """
    total = 0
    for py in sorted(directory.glob("*.py")):
        for node in ast.parse(py.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import) and any(a.name == "fcntl" for a in node.names):
                total += 1
            elif isinstance(node, ast.ImportFrom) and node.module == "fcntl":
                total += 1
    return total


def test_pyproject_fcntl_count_matches_disk() -> None:
    match = _search(_PYPROJECT, r"POSIX only: (\d+) scripts in ([\w./-]+) hard-import fcntl")
    claimed, claimed_path = int(match.group(1)), match.group(2)
    assert (_REPO / claimed_path).resolve() == _SYNC_DIR.resolve()
    assert _top_level_fcntl_imports(_REPO / claimed_path) == claimed


# ---------------------------------------------------------------------------
# 2. "23 of the 53 governance scripts read WULONG_ROOT"
# ---------------------------------------------------------------------------
#
# The verb in both docs is READ, so the predicate must be an actual environment
# read, not "the string appears somewhere in the file". Substring membership
# over-counts and drifts: it returned 24 when the docs were first corrected and
# returns 26 today, because C0 added comments naming WULONG_ROOT to
# session-pulse.py and session-start-gate.py, neither of which reads it. The
# oldest and worst passenger is wulong-init.py, which names WULONG_ROOT in its
# module docstring and prints it in a closing hint and never reads it, and which
# is the script where a user would most expect the variable to apply. The count
# below is deliberately NOT pinned to a membership number, because that number
# moves whenever anyone writes a comment.


_ROOT_MODULE = _REPO / "wulong" / "_root.py"


def _aliases_for_the_variable(module: ast.Module) -> set[str]:
    """Module-level names bound to the literal "WULONG_ROOT".

    wulong/_root.py names the variable once, as ENV_VAR, and uses the name
    everywhere else. A detector that only matched the bare literal would report
    the one module that definitively reads WULONG_ROOT as not reading it, and
    the whole count would silently follow.
    """
    aliases = set()
    for node in module.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and node.value.value == "WULONG_ROOT":
            aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return aliases


def _names_the_variable(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return node.value == "WULONG_ROOT"
    return isinstance(node, ast.Name) and node.id in aliases


def _direct_env_read(tree: ast.AST, aliases: set[str] = frozenset()) -> bool:
    """os.environ.get(VAR), os.getenv(VAR) or os.environ[VAR]."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else None
            if name in {"get", "getenv"} and node.args:
                if _names_the_variable(node.args[0], aliases):
                    return True
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            # Load only. `env["WULONG_ROOT"] = value` WRITES the variable for a
            # child process, which is the opposite of reading it, and counting
            # child_env() as a reader would inflate the published number.
            if _names_the_variable(node.slice, aliases):
                return True
    return False


def _shared_resolver_names() -> set[str]:
    """Names exported by wulong/_root.py whose BODY reads WULONG_ROOT.

    Measured, not hardcoded. If someone renames resolve_root or adds a second
    reader, this follows; if _root.py ever stops reading the variable, every
    importer stops counting, which is the honest answer.
    """
    tree = ast.parse(_ROOT_MODULE.read_text(encoding="utf-8"))
    aliases = _aliases_for_the_variable(tree)
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")   # private helpers are not API
            and _direct_env_read(node, aliases)}


def _reads_wulong_root(path: pathlib.Path) -> bool:
    """True if the module reads WULONG_ROOT, directly OR through the shared resolver.

    Change D moved the precedence into wulong/_root.py, so a purely syntactic
    "does this file touch os.environ" test would have reported the count falling
    from 23 to 16 while MORE scripts than ever honoured the variable. The verb in
    the docs is READ, and a script that calls resolve_root() reads it, one level
    down. Following the import is what keeps the published number true.

    Writers do not count: child_env() sets the variable for a subprocess.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if _direct_env_read(tree, _aliases_for_the_variable(tree)):
        return True
    readers = _shared_resolver_names()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "wulong._root":
            if any(alias.name in readers for alias in node.names):
                return True
    return False


def test_the_shared_resolver_is_what_reads_the_variable() -> None:
    """Guards the indirection above: if _root.py stops reading, this says so."""
    assert _shared_resolver_names() == {"resolve_root"}, _shared_resolver_names()


@pytest.mark.parametrize("doc", [_README, _USERGUIDE], ids=lambda p: p.name)
def test_wulong_root_reader_count_matches_disk(doc: pathlib.Path) -> None:
    match = _search(doc, r"(\d+) of the (\d+) governance scripts read")
    claimed_readers, claimed_total = int(match.group(1)), int(match.group(2))
    scripts = sorted(_SYNC_DIR.glob("*.py"))
    readers = [p for p in scripts if _reads_wulong_root(p)]
    assert len(scripts) == claimed_total
    assert len(readers) == claimed_readers, sorted(p.name for p in readers)


def test_wulong_init_names_wulong_root_without_reading_it() -> None:
    """Pins the one script that separates "contains" from "reads".

    If this ever goes green the other way, the 23/24 distinction has collapsed
    and every doc sentence built on it needs re-measuring.
    """
    init = _SYNC_DIR / "wulong-init.py"
    text = init.read_text(encoding="utf-8")
    assert "WULONG_ROOT" in text
    assert not _reads_wulong_root(init)


@pytest.mark.parametrize("doc", [_README, _USERGUIDE], ids=lambda p: p.name)
def test_wulong_root_breakdown_adds_up(doc: pathlib.Path) -> None:
    """The dependent sentence must move with the headline figure.

    Both docs follow the count with "Of the other N, M derive ...". Editing only
    the headline leaves a published document claiming more scripts than exist.
    """
    readers = int(_search(doc, r"(\d+) of the (\d+) governance scripts read").group(1))
    total = int(_search(doc, r"(\d+) of the (\d+) governance scripts read").group(2))
    others = int(_search(doc, r"Of the other (\d+), (\d+) derive").group(1))
    assert readers + others == total


def _touches_its_own_file_location(path: pathlib.Path) -> bool:
    """`__file__` appears anywhere in the module source.

    Textual on purpose, and the published verb was changed to match it. The old
    verb was "resolve the vault root by walking up from their own file location",
    which is false for at least three of the 20: slop-scrub.py carries __file__
    inside a self-invocation demo string, and wulong-init.py resolves its own
    engine and payload directories rather than any vault. Only the sum was ever
    asserted, so the sub-split shipped false through two audits.
    """
    return "__file__" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", [_README, _USERGUIDE], ids=lambda p: p.name)
def test_the_sub_split_of_the_non_readers_matches_disk(doc: pathlib.Path) -> None:
    """N of the non-readers derive paths from __file__; the rest never touch it."""
    claimed_others, claimed_deriving = (
        int(g) for g in _search(doc, r"Of the other (\d+), (\d+) derive").groups()
    )
    others = [p for p in sorted(_SYNC_DIR.glob("*.py")) if not _reads_wulong_root(p)]
    assert len(others) == claimed_others

    deriving = [p for p in others if _touches_its_own_file_location(p)]
    assert len(deriving) == claimed_deriving, sorted(p.name for p in deriving)

    # The doc names the remainder one by one, so measure that too.
    named = _search(
        doc,
        r"never touch `__file__` at all \(`([^`]+)` takes its paths as arguments, "
        r"`([^`]+)` has no path logic\)",
    ).groups()
    assert sorted(p.name for p in others if p not in deriving) == sorted(named)


# ---------------------------------------------------------------------------
# 3. "65 agent definitions are tracked in wulong/payload/.claude/agents/"
# ---------------------------------------------------------------------------
#
# Measured from disk, not from `git ls-files`. Change C1 made the wheel the thing
# that matters: what a user gets is what the BUILD carries, and the built-wheel
# count is asserted separately in tests/test_wheel_payload.py. Counting tracked
# files here as well would only restate a git fact that the shipped artifact does
# not depend on, and would go red during the window between moving the payload
# and staging it.

@pytest.mark.parametrize("doc", [_README, _ARCHITECTURE], ids=lambda p: p.name)
def test_agent_definition_count_matches_disk(doc: pathlib.Path) -> None:
    match = _search(doc, r"(\d+) agent definitions are tracked in `([^`]+)`")
    claimed, claimed_path = int(match.group(1)), match.group(2)
    assert (_REPO / claimed_path).resolve() == _AGENTS_DIR.resolve()
    assert len(list((_REPO / claimed_path).glob("*.md"))) == claimed


# ---------------------------------------------------------------------------
# 4. "the name: frontmatter field matches the filename stem"
# ---------------------------------------------------------------------------

# Each doc words the claim differently, so each is grepped for its own sentence.
_NAME_CLAIM = {
    _README: "The `name:` frontmatter field matches the filename stem so Claude Code "
             "routing works without configuration.",
    _ARCHITECTURE: "The `name:` field matches the filename stem",
}


@pytest.mark.parametrize("doc", list(_NAME_CLAIM), ids=lambda p: p.name)
def test_name_stem_claim_is_still_published(doc: pathlib.Path) -> None:
    assert _NAME_CLAIM[doc] in _flat(doc)


def _frontmatter_name(path: pathlib.Path) -> str | None:
    lines = path.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("name:"):
            return line.partition(":")[2].strip()
    return None


def test_every_agent_name_field_matches_its_filename_stem() -> None:
    """Claude Code routes spawn tokens by this match, so a drift is a live break."""
    agents = sorted(_AGENTS_DIR.glob("*.md"))
    assert agents, f"no agent definitions found at {_AGENTS_DIR}"
    mismatched = {p.name: _frontmatter_name(p) for p in agents
                  if _frontmatter_name(p) != p.stem}
    assert not mismatched, f"name: does not match filename stem: {mismatched}"


# ---------------------------------------------------------------------------
# 5. "--root ... overrides WULONG_ROOT env var" must be true of the CODE
# ---------------------------------------------------------------------------
#
# This is the data-loss guard. Before C0 every _resolve_root returned the
# environment value FIRST, so a script invoked with --root vaultB read from,
# wrote to and (in cerebrum-search --smoke) DELETED the index of vaultA. The
# help string promised an override the code did not honour. Nothing in the suite
# noticed, because no test had ever executed the resolver. This one does.

_ROOT_FLAG_SCRIPTS = [
    "cerebrum-search",
    "query-receipts",
    "session-close-audit",
    "trace-change-chain",
    "validate-receipts",
    "validate-receipt-graph",
]


def _load_sync_module(stem: str):
    """Import a hyphenated sync script by path, the way test_imports.py does."""
    safe = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe, _SYNC_DIR / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(safe, None)
    return mod


@pytest.mark.parametrize("stem", _ROOT_FLAG_SCRIPTS)
def test_explicit_root_flag_beats_the_environment(stem, tmp_path, monkeypatch):
    """Explicit --root wins, env is the fallback, install-relative is the floor."""
    vault_a = tmp_path / "vaultA"
    vault_b = tmp_path / "vaultB"
    (vault_a / "Meta").mkdir(parents=True)
    (vault_b / "Meta").mkdir(parents=True)

    mod = _load_sync_module(stem)
    monkeypatch.setenv("WULONG_ROOT", str(vault_a))
    assert mod._resolve_root(str(vault_b)) == str(vault_b), (
        f"{stem}: --root was ignored while WULONG_ROOT was set. This is the "
        "wrong-target read/write/delete bug C0 fixed, not a cosmetic help string."
    )
    assert mod._resolve_root(None) == str(vault_a)
    monkeypatch.delenv("WULONG_ROOT")
    assert mod._resolve_root(None) not in (str(vault_a), str(vault_b))


@pytest.mark.parametrize("stem", _ROOT_FLAG_SCRIPTS)
def test_the_root_help_string_still_promises_the_override(stem):
    """If the promise is ever dropped, the test above stops guarding anything."""
    text = (_SYNC_DIR / f"{stem}.py").read_text(encoding="utf-8")
    assert "overrides WULONG_ROOT env var" in text or "Wins over the WULONG_ROOT" in text


# ---------------------------------------------------------------------------
# 6. "What the gate actually proves": the two line citations must stay true
# ---------------------------------------------------------------------------
#
# Both documents carry exactly two line citations, and until E1a NEITHER was
# covered by a test. One of them was simply WRONG: `:71-77` was published as the
# location of the last-value-wins overwrite in check_gate_precondition.py, and
# those lines are the loop header and the comment skip. The overwrite was at :81.
# A wrong citation survived two audits because grep of tests/ for it returned
# nothing.
#
# These tests do not check that a range "looks right". They assert that named
# code IS inside it and that named neighbouring code is NOT, so the citation
# fails both when it drifts off the top and when it is widened to hide a drift.

_GATE_SCRIPT = _SYNC_DIR / "check_gate_precondition.py"
_FM_MODULE = _REPO / "wulong" / "_frontmatter.py"

_FRAMING = (
    "Six facts about it, each checkable in source. A bare line citation is to "
    "that file; where a fact lives in another file, that file is named:"
)


def _cited(path: pathlib.Path, spec: str) -> str:
    lo, hi = (int(n) for n in spec.split("-"))
    return "\n".join(path.read_text(encoding="utf-8").split("\n")[lo - 1:hi])


def _assert_range(path: pathlib.Path, spec: str, first, last, inside, outside) -> None:
    whole = path.read_text(encoding="utf-8")
    cited = _cited(path, spec)
    edges = cited.split("\n")
    # The edges give the test a ONE-LINE bite in both directions. Without them a
    # citation 24 lines long keeps its anchors through a small shift, and only a
    # drift big enough to push code out of the range is caught.
    assert edges[0] == first, (
        f"{path.name}:{spec} now begins at {edges[0]!r}, not {first!r}. The "
        "citation has drifted off the top of what it points at."
    )
    assert edges[-1] == last, (
        f"{path.name}:{spec} now ends at {edges[-1]!r}, not {last!r}. The "
        "citation has drifted off the bottom of what it points at."
    )
    for probe in inside:
        assert probe in cited, (
            f"{path.name}:{spec} no longer contains {probe!r}. The published "
            "citation has drifted off the code it points at."
        )
    for probe in outside:
        assert probe in whole, f"{path.name} no longer contains {probe!r} anywhere"
        assert probe not in cited, (
            f"{path.name}:{spec} has widened far enough to swallow {probe!r}. A "
            "range wide enough to always contain the anchors proves nothing."
        )


@pytest.mark.parametrize("doc", [_README, _ARCHITECTURE], ids=lambda p: p.name)
def test_the_framing_sentence_survived_the_parser_moving_out(doc: pathlib.Path) -> None:
    """The old wording promised all six facts were checkable "in that file".

    E1a moved the frontmatter reader to wulong/_frontmatter.py, which made that
    promise false for fact 2 the moment the file changed. Repairing the citation
    alone would have left the framing lying about where to look.
    """
    flat = _flat(doc)
    assert _FRAMING in flat
    assert "each checkable in that file" not in flat


@pytest.mark.parametrize("doc", [_README, _ARCHITECTURE], ids=lambda p: p.name)
def test_fact_one_cites_the_range_that_actually_mints_a_pass(doc: pathlib.Path) -> None:
    spec = _search(doc, r"can mint a PASS \(`:(\d+-\d+)`\)").group(1)
    _assert_range(
        _GATE_SCRIPT, spec,
        first="    # Scan receipts",
        last="                )",
        inside=(
            "os.listdir(receipts_dir)",
            "fh.read(4096)",
            "parse_frontmatter(text)",
            'fields.get("change_id", "").strip() != change_id',
            'reason="tester DONE receipt found for this change_id"',
            # The binding branch lands INSIDE the range fact 1 cites, so the
            # citation was re-pointed and both edges moved with it. This probe
            # is what proves the branch is inside rather than merely nearby.
            "verdict_is_binding_pass(fields, label=fname, require=require_binding)",
        ),
        outside=(
            "def check_gate_precondition(",
            "no contrarian receipt with agent=contrarian",
            'VALID_GATES = {"nn3", "nn4"}',
        ),
    )


@pytest.mark.parametrize("doc", [_README, _ARCHITECTURE], ids=lambda p: p.name)
def test_fact_two_cites_the_file_and_range_holding_the_overwrite(doc: pathlib.Path) -> None:
    path, spec = _search(
        doc, r"the last value wins \(`([\w./-]+):(\d+-\d+)`\)"
    ).groups()
    assert (_REPO / path).resolve() == _FM_MODULE.resolve()
    _assert_range(
        _FM_MODULE, spec,
        first="def parse_frontmatter(text: str) -> dict[str, str]:",
        last="    return fields",
        inside=(
            "fields[key.strip()] = val.strip()",
            'key, _, val = line.partition(":")',
        ),
        outside=(
            "def split_frontmatter(",
            "return None, text",
        ),
    )


# ---------------------------------------------------------------------------
# 7. Windows posture: the prose and the classifier cannot disagree
# ---------------------------------------------------------------------------

_OS_CLASSIFIER = re.compile(r'"Operating System :: ([^"]+)"')


def _os_classifiers() -> list:
    return _OS_CLASSIFIER.findall(_PYPROJECT.read_text(encoding="utf-8"))


def test_the_platform_classifier_is_one_posix_entry() -> None:
    """One entry, so "the classifier" in the tests below is unambiguous."""
    assert _os_classifiers() == ["POSIX"], _os_classifiers()


@pytest.mark.parametrize("doc", [_README, _USERGUIDE], ids=lambda p: p.name)
def test_the_docs_name_the_same_platform_as_the_classifier(doc: pathlib.Path) -> None:
    """Publishing a platform in prose and another in metadata is the drift here."""
    claimed = _search(doc, r"wulong is (\w+) only\.").group(1)
    assert [claimed] == _os_classifiers()


def _fcntl_importing_files(directory: pathlib.Path, top_level_only: bool) -> set:
    names = set()
    for py in sorted(directory.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        nodes = tree.body if top_level_only else list(ast.walk(tree))
        for node in nodes:
            if isinstance(node, ast.Import) and any(a.name == "fcntl" for a in node.names):
                names.add(py.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "fcntl":
                names.add(py.name)
    return names


@pytest.mark.parametrize("doc", [_README, _USERGUIDE], ids=lambda p: p.name)
def test_both_fcntl_counts_and_the_named_deferred_site_match_disk(doc: pathlib.Path) -> None:
    """Two numbers and one filename, each measured rather than restated.

    pyproject.toml counts TOP-LEVEL imports, because only those make a script
    fail at import. The docs also count the deferred one, because its failure is
    worse: it happens at call time, after the ledger write it was locking.
    """
    deep, scanned_path = _search(
        doc, r"(\d+) scripts in `([\w./-]+)` import `fcntl`"
    ).groups()
    top = int(_search(doc, r"(\d+) of them at module top level").group(1))
    scanned = _REPO / scanned_path
    assert scanned.resolve() == _SYNC_DIR.resolve()

    any_depth = _fcntl_importing_files(scanned, top_level_only=False)
    top_level = _fcntl_importing_files(scanned, top_level_only=True)
    assert len(any_depth) == int(deep), sorted(any_depth)
    assert len(top_level) == top, sorted(top_level)
    assert _top_level_fcntl_imports(scanned) == top

    ordinal, named = _search(
        doc, r"The (\d+)th, `([\w./-]+)`, defers its import"
    ).groups()
    assert int(ordinal) == int(deep)
    assert any_depth - top_level == {pathlib.Path(named).name}, sorted(any_depth - top_level)


def test_the_deferred_fcntl_import_is_guarded_by_a_handler_that_cannot_catch_it() -> None:
    """The docs claim the guard misses the failure. Both halves are checked here.

    Half one: the import really does sit inside try/except OSError. Half two: an
    absent module raises ImportError, which is not an OSError, so the guard is
    not a guard on Windows.
    """
    tree = ast.parse((_SYNC_DIR / "observer-disposition.py").read_text(encoding="utf-8"))
    guarded = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(isinstance(n, ast.Import) and any(a.name == "fcntl" for a in n.names)
                for n in ast.walk(node))
        and any(isinstance(h.type, ast.Name) and h.type.id == "OSError"
                for h in node.handlers)
    ]
    assert guarded, "the deferred fcntl import is no longer inside try/except OSError"
    assert not issubclass(ImportError, OSError)
