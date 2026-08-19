"""test_binding.py - behaviour lock on wulong/_binding.py and every site it routes.

Three things are pinned here and nothing else belongs in this file.

  1. The predicate. An unbound PASS passes with a warning today and is refused
     once binding is required, and a malformed digest is no binding at all
     rather than a weak one.
  2. The MIGRATION MECHANISM. The flip date is a constant in code, one test goes
     red once that date passes while the default is still off, and a second test
     proves the first one is executable by moving the clock forward rather than
     by waiting for it. CI runs with WULONG_REQUIRE_NO_SKIPS=1 and conftest turns
     any skip into a session failure, so neither can be neutered with a skip.
  3. Every ROUTED SITE, one test each. Routing four scripts through one predicate
     is worth nothing if a site was missed, and a grep does not prove a call is
     reached. Each site is exercised through the function that owns it.

Deliberately NOT here: the nn4 and deploy sites. They key off `status`, never off
`review_verdict`, and belong to wulong-e1b-nn4-binding. The one nn4 assertion in
tests/test_gate.py exists to pin that carve, not to cover it.

ponytail: importlib by path for the hyphenated scripts, monkeypatch for the two
environment variables. No mocking framework and no fixtures package.
"""
import datetime
import importlib.util
import inspect
import pathlib
import sys

import pytest

from wulong import _binding
from wulong._binding import (
    BINDING_REQUIRED_FROM,
    DEFAULT_REQUIRE_BINDING,
    binding_default_is_current,
    binding_ok,
    is_bound,
    require_binding,
    verdict_is_binding_pass,
)
from wulong._manifest import manifest_digest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SYNC = _REPO / "wulong" / "sync"

_BOUND = {"artifact_manifest_sha256": "a" * 64, "artifact_count": "2"}


def _load(stem: str):
    path = _SYNC / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _quiet_budget():
    """The warning budget is per process, and the whole suite is one process."""
    _binding.reset_warnings()
    yield
    _binding.reset_warnings()


def _fields(**over) -> dict:
    base = {"agent": "contrarian", "review_mode": "plan", "review_verdict": "PASS"}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------

def test_a_bound_pass_passes_either_way() -> None:
    fields = _fields(**_BOUND)
    assert verdict_is_binding_pass(fields, require=True) is True
    assert verdict_is_binding_pass(fields, require=False) is True


def test_an_unbound_pass_passes_only_while_binding_is_off() -> None:
    fields = _fields()
    assert verdict_is_binding_pass(fields, require=False) is True
    assert verdict_is_binding_pass(fields, require=True) is False


def test_a_fail_stays_a_fail_and_binding_cannot_rescue_it() -> None:
    assert verdict_is_binding_pass(_fields(review_verdict="FAIL", **_BOUND),
                                   require=False) is False
    assert verdict_is_binding_pass(_fields(review_verdict="FAIL", **_BOUND),
                                   require=True) is False


def test_the_verdict_comparison_is_byte_identical_to_the_one_it_replaced() -> None:
    """The whole zero-difference claim rests on this. With binding off the new
    predicate must answer exactly what `== "PASS"` answered."""
    for value in ("PASS", "FAIL", "pass", "Pass", " PASS ", "", "PASSED", "SOFT FAIL"):
        old = value.strip() == "PASS"
        new = verdict_is_binding_pass({"review_verdict": value}, require=False)
        assert new is old, value


@pytest.mark.parametrize("digest,count", [
    ("banana", "2"),
    ("A" * 64, "2"),
    ("a" * 63, "2"),
    ("a" * 65, "2"),
    ("", "2"),
    ("a" * 64, ""),
    ("a" * 64, "0"),
    ("a" * 64, "-1"),
    ("a" * 64, "two"),
])
def test_a_malformed_binding_is_no_binding(digest: str, count: str) -> None:
    assert is_bound({"artifact_manifest_sha256": digest, "artifact_count": count}) is False


def test_a_real_manifest_digest_is_accepted(tmp_path: pathlib.Path) -> None:
    artifact = tmp_path / "plan.md"
    artifact.write_text("plan v3\n", encoding="utf-8")
    fields = _fields(artifact_manifest_sha256=manifest_digest([str(artifact)]),
                     artifact_count="1")
    assert verdict_is_binding_pass(fields, require=True) is True


def test_the_environment_variable_is_the_transport_for_scripts(monkeypatch) -> None:
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    assert require_binding() is True
    assert verdict_is_binding_pass(_fields()) is False
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "0")
    assert require_binding() is False
    monkeypatch.delenv("WULONG_REQUIRE_BINDING")
    assert require_binding() is DEFAULT_REQUIRE_BINDING


def test_an_explicit_flag_outranks_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    assert require_binding(False) is False
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "0")
    assert require_binding(True) is True


def test_the_legacy_exemption_is_advisory_and_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("WULONG_LEGACY_UNBOUND_UNTIL", "2026-01-01")
    assert binding_ok(_fields(date="2025-06-01"), require=True) is True
    assert binding_ok(_fields(date="2026-06-01"), require=True) is False
    # Absent, unparseable and a garbage cutoff all mean NOT exempt.
    assert binding_ok(_fields(), require=True) is False
    assert binding_ok(_fields(date="not a date"), require=True) is False
    monkeypatch.setenv("WULONG_LEGACY_UNBOUND_UNTIL", "banana")
    assert binding_ok(_fields(date="2025-06-01"), require=True) is False


def test_the_warning_budget_is_bounded(capsys) -> None:
    """Loud, and not so loud that everyone learns to ignore it."""
    for i in range(20):
        verdict_is_binding_pass(_fields(), label=f"r{i}.md", require=False)
    lines = [l for l in capsys.readouterr().err.split("\n") if l.strip()]
    assert len(lines) == _binding._WARN_LIMIT + 1
    assert "suppressed" in lines[-1]


# ---------------------------------------------------------------------------
# The migration mechanism
# ---------------------------------------------------------------------------

def test_the_binding_default_has_not_expired() -> None:
    """RED once BINDING_REQUIRED_FROM passes while the default is still off.

    This is the whole mechanism. A date in a changelog is a promise no code
    reads; this is a constant a test reads.

    The one hole, stated rather than hidden: if nobody ever commits again, CI
    never runs and this never fires. A date constant compels a maintainer who is
    still working. It cannot compel one who has stopped.
    """
    assert binding_default_is_current(datetime.date.today()), (
        f"BINDING_REQUIRED_FROM ({BINDING_REQUIRED_FROM.isoformat()}) has passed "
        "and DEFAULT_REQUIRE_BINDING is still False. Set it True, update the "
        "CHANGELOG entry, and delete this test."
    )


def test_that_tripwire_is_executable_with_the_clock_moved_forward() -> None:
    """Proves the test above can fail, without waiting until the date arrives."""
    day = datetime.timedelta(days=1)
    assert binding_default_is_current(BINDING_REQUIRED_FROM - day) is True
    assert binding_default_is_current(BINDING_REQUIRED_FROM) is DEFAULT_REQUIRE_BINDING
    assert binding_default_is_current(BINDING_REQUIRED_FROM + day) is DEFAULT_REQUIRE_BINDING


def test_the_tripwire_reads_the_same_symbol_the_gate_reads(monkeypatch) -> None:
    """Not a copy of the rule. Flip the symbol and both answers move together."""
    monkeypatch.delenv("WULONG_REQUIRE_BINDING", raising=False)
    assert require_binding() is DEFAULT_REQUIRE_BINDING
    monkeypatch.setattr(_binding, "DEFAULT_REQUIRE_BINDING", True)
    assert _binding.require_binding() is True
    assert _binding.binding_default_is_current(BINDING_REQUIRED_FROM + datetime.timedelta(days=999)) is True


def test_the_changelog_publishes_the_same_flip_date() -> None:
    """The constant and the published promise cannot drift apart silently."""
    changelog = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert BINDING_REQUIRED_FROM.isoformat() in changelog


# ---------------------------------------------------------------------------
# Routed site 1: check_gate_precondition.py nn3
#   covered by tests/test_gate.py::TestArtifactBinding
# Routed site 2: automerge_gate.py, plan and output
# ---------------------------------------------------------------------------

def _write_receipt(directory: pathlib.Path, name: str, **fields: str) -> None:
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    (directory / name).write_text(f"---\n{lines}\n---\n\nbody\n", encoding="utf-8")


@pytest.fixture
def merge_corpus(tmp_path: pathlib.Path):
    def build(**extra: str):
        d = tmp_path / ("r" + str(len(list(tmp_path.iterdir()))))
        d.mkdir()
        for mode in ("plan", "output"):
            _write_receipt(d, f"contrarian-{mode}.md", agent="contrarian",
                           change_id="c1", review_mode=mode,
                           review_verdict="PASS", **extra)
        _write_receipt(d, "tester.md", agent="tester", change_id="c1", status="DONE")
        return str(d)
    return build


def test_automerge_gate_routes_both_verdict_sites(merge_corpus, monkeypatch) -> None:
    merge = _load("automerge_gate")
    unbound, bound = merge_corpus(), merge_corpus(**_BOUND)
    monkeypatch.delenv("WULONG_REQUIRE_BINDING", raising=False)
    assert merge.can_auto_merge("c1", unbound)[0] is True
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    allowed, reason = merge.can_auto_merge("c1", unbound)
    assert allowed is False and "plan-review PASS" in reason
    assert merge.can_auto_merge("c1", bound)[0] is True


def test_automerge_gate_output_site_is_routed_independently(
    merge_corpus, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Bind the plan review only. The OUTPUT site must still refuse."""
    d = pathlib.Path(merge_corpus())
    _write_receipt(d, "contrarian-plan.md", agent="contrarian", change_id="c1",
                   review_mode="plan", review_verdict="PASS", **_BOUND)
    merge = _load("automerge_gate")
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    allowed, reason = merge.can_auto_merge("c1", str(d))
    assert allowed is False and "output-review PASS" in reason


# ---------------------------------------------------------------------------
# Routed site 3: validate-receipt-graph.py, three functions, five sites:
#   _check_nn3 (1), _check_nn10 (2), _is_complete (2)
# ---------------------------------------------------------------------------

def _node(agent: str, **over) -> dict:
    node = {
        "fname": "", "path": "", "agent": agent, "status": "DONE",
        "change_id": "c1", "change_type": "feature", "session_id": "",
        "review_mode": "", "review_verdict": "", "gated_by": [], "date": None,
        "artifact_manifest_sha256": "", "artifact_count": "",
    }
    node.update(over)
    return node


def _graph_index(**binding) -> dict:
    plan = _node("contrarian", fname="plan.md", review_mode="plan",
                 review_verdict="PASS", **binding)
    out = _node("contrarian", fname="out.md", review_mode="output",
                review_verdict="PASS", gated_by=["coder.md"], **binding)
    coder = _node("coder", fname="coder.md", gated_by=["plan.md"])
    return {"plan.md": plan, "coder.md": coder, "out.md": out}


@pytest.mark.parametrize("check,expect_clean", [("_check_nn3", True), ("_check_nn10", True)])
def test_graph_validator_accepts_a_bound_pass(check: str, expect_clean: bool, monkeypatch) -> None:
    graph = _load("validate-receipt-graph")
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    index = _graph_index(**_BOUND)
    viols = getattr(graph, check)("c1", list(index), index)
    assert (viols == []) is expect_clean


@pytest.mark.parametrize("check", ["_check_nn3", "_check_nn10"])
def test_graph_validator_refuses_an_unbound_pass(check: str, monkeypatch) -> None:
    graph = _load("validate-receipt-graph")
    index = _graph_index()
    monkeypatch.delenv("WULONG_REQUIRE_BINDING", raising=False)
    assert getattr(graph, check)("c1", list(index), index) == []
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    assert getattr(graph, check)("c1", list(index), index) != []


def test_graph_completeness_site_is_routed(monkeypatch) -> None:
    """_is_complete carries two of the five sites, one per branch."""
    graph = _load("validate-receipt-graph")
    with_coder = _graph_index()
    without_coder = {k: v for k, v in _graph_index().items() if k != "coder.md"}
    monkeypatch.delenv("WULONG_REQUIRE_BINDING", raising=False)
    assert graph._is_complete("c1", list(with_coder), with_coder) is True
    assert graph._is_complete("c1", list(without_coder), without_coder) is True
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    assert graph._is_complete("c1", list(with_coder), with_coder) is False
    assert graph._is_complete("c1", list(without_coder), without_coder) is False


def test_the_graph_index_carries_the_binding_fields(tmp_path: pathlib.Path) -> None:
    """A routed comparison reads the index, so a missing key makes every PASS unbound."""
    graph = _load("validate-receipt-graph")
    _write_receipt(tmp_path, "r.md", agent="contrarian", change_id="c1",
                   review_mode="plan", review_verdict="PASS", **_BOUND)
    node = graph._load_receipt(str(tmp_path / "r.md"))
    assert node["artifact_manifest_sha256"] == _BOUND["artifact_manifest_sha256"]
    assert node["artifact_count"] == _BOUND["artifact_count"]


# ---------------------------------------------------------------------------
# Routed site 4: judge-score.py, one chokepoint feeding six comparisons
# ---------------------------------------------------------------------------

def test_judge_score_verdict_reader_is_routed(monkeypatch) -> None:
    judge = _load("judge-score")
    unbound = {"fname": "c.md", "fields": _fields()}
    bound = {"fname": "c.md", "fields": _fields(**_BOUND)}
    monkeypatch.delenv("WULONG_REQUIRE_BINDING", raising=False)
    assert judge._get_review_verdict(unbound) == "PASS"
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    assert judge._get_review_verdict(unbound) == "FAIL"
    assert judge._get_review_verdict(bound) == "PASS"
    assert judge._get_review_verdict(None) is None


def test_judge_score_finders_inherit_the_refusal(monkeypatch) -> None:
    """The two finder sites read their verdict from the chokepoint above."""
    judge = _load("judge-score")
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    members = [
        {"fname": "p-fail.md", "fields": _fields(review_verdict="FAIL", **_BOUND)},
        {"fname": "p-pass.md", "fields": _fields()},
    ]
    # The unbound PASS must NOT be preferred over the bound FAIL.
    assert judge._find_contrarian_plan_review(members)["fname"] == "p-fail.md"
    members[1]["fields"] = _fields(**_BOUND)
    assert judge._find_contrarian_plan_review(members)["fname"] == "p-pass.md"


def test_judge_score_tester_status_is_not_routed() -> None:
    """The carve, and it spans TWO functions rather than sitting inside one.

    `_build_comprehensiveness_skeleton` and `score_change` each read
    `tester_status` directly. An earlier version of this docstring cited two
    line numbers as though they were one function; both numbers were wrong and
    so was the shape. Naming the functions and asserting them from source is a
    citation that cannot drift, which is what tests/test_doc_claims.py section 6
    exists to enforce for the two published ones.
    """
    judge = _load("judge-score")
    tester = {"fname": "t.md", "fields": {"agent": "tester", "status": "DONE"}}
    assert judge._get_tester_status(tester) == "DONE"
    owners = ["_build_comprehensiveness_skeleton", "score_change"]
    sources = [inspect.getsource(getattr(judge, fn)) for fn in owners]
    assert all("tester_status" in src for src in sources)
    assert sources[0] != sources[1]


# ---------------------------------------------------------------------------
# BA-4: manifest versus gated_by authority, tested in BOTH directions
# ---------------------------------------------------------------------------

def _verify(receipt: pathlib.Path, artifacts: list[str]):
    gate = _load("check_gate_precondition")
    argv = ["--verify", str(receipt)]
    for a in artifacts:
        argv += ["--artifact", a]
    return gate.main(argv)


@pytest.fixture
def stamped(tmp_path: pathlib.Path):
    def build(gated_by: str, artifacts: list[str]) -> pathlib.Path:
        receipt = tmp_path / "receipt.md"
        receipt.write_text(
            "---\nagent: contrarian\nchange_id: c1\nreview_mode: plan\n"
            "review_verdict: PASS\n"
            f"gated_by: [{gated_by}]\n"
            f"artifact_manifest_sha256: {manifest_digest(artifacts)}\n"
            f"artifact_count: {len(artifacts)}\n---\n\nbody\n",
            encoding="utf-8",
        )
        return receipt
    return build


def test_a_gated_by_predecessor_outside_the_manifest_is_reported_not_refused(
    stamped, tmp_path: pathlib.Path, capsys
) -> None:
    hashed = tmp_path / "plan.md"
    hashed.write_text("plan v3\n", encoding="utf-8")
    receipt = stamped("plan.md, an-earlier-receipt.md", [str(hashed)])
    assert _verify(receipt, [str(hashed)]) == 0
    out = capsys.readouterr().out
    assert "[REPORT]" in out and "an-earlier-receipt.md" in out
    assert "[VERIFIED]" in out


def test_a_manifest_entry_outside_gated_by_is_not_refused_either(
    stamped, tmp_path: pathlib.Path, capsys
) -> None:
    """The other direction. They are not required to be co-extensive."""
    one = tmp_path / "plan.md"
    two = tmp_path / "delta.md"
    one.write_text("plan v3\n", encoding="utf-8")
    two.write_text("delta\n", encoding="utf-8")
    receipt = stamped("plan.md", [str(one), str(two)])
    assert _verify(receipt, [str(one), str(two)]) == 0
    assert "[REPORT]" not in capsys.readouterr().out


def test_the_manifest_wins_where_both_cover_the_same_file(
    stamped, tmp_path: pathlib.Path
) -> None:
    """gated_by naming the file does not rescue a digest that no longer matches."""
    hashed = tmp_path / "plan.md"
    hashed.write_text("plan v3\n", encoding="utf-8")
    receipt = stamped("plan.md", [str(hashed)])
    assert _verify(receipt, [str(hashed)]) == 0
    hashed.write_text("plan v4\n", encoding="utf-8")
    assert _verify(receipt, [str(hashed)]) == 1


# ---------------------------------------------------------------------------
# BA-2: --verify takes its bytes from --artifact and never from a recorded path
# ---------------------------------------------------------------------------

def test_verify_recomputes_from_supplied_bytes_after_the_artifact_moves(
    stamped, tmp_path: pathlib.Path
) -> None:
    original = tmp_path / "plan.md"
    original.write_text("plan v3\n", encoding="utf-8")
    receipt = stamped("plan.md", [str(original)])
    moved = tmp_path / "moved" / "elsewhere.md"
    moved.parent.mkdir()
    original.rename(moved)
    assert not original.exists()
    assert _verify(receipt, [str(moved)]) == 0


def test_verify_refuses_when_the_caller_names_nothing(stamped, tmp_path: pathlib.Path) -> None:
    hashed = tmp_path / "plan.md"
    hashed.write_text("plan v3\n", encoding="utf-8")
    assert _verify(stamped("plan.md", [str(hashed)]), []) == 2


def test_verify_refuses_an_unbound_receipt(tmp_path: pathlib.Path) -> None:
    receipt = tmp_path / "unbound.md"
    receipt.write_text("---\nagent: contrarian\nreview_verdict: PASS\n---\n",
                       encoding="utf-8")
    artifact = tmp_path / "plan.md"
    artifact.write_text("plan v3\n", encoding="utf-8")
    assert _verify(receipt, [str(artifact)]) == 1


# ---------------------------------------------------------------------------
# The shape validator in validate-receipts.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fields,code", [
    ({"artifact_manifest_sha256": "banana", "artifact_count": "1"},
     "VT_W_ARTIFACT_DIGEST_INVALID"),
    ({"artifact_manifest_sha256": "A" * 64, "artifact_count": "1"},
     "VT_W_ARTIFACT_DIGEST_INVALID"),
    ({"artifact_manifest_sha256": "a" * 64, "artifact_count": "0"},
     "VT_W_ARTIFACT_COUNT_INVALID"),
    ({"artifact_manifest_sha256": "a" * 64, "artifact_count": "lots"},
     "VT_W_ARTIFACT_COUNT_INVALID"),
    ({"artifact_manifest_sha256": "a" * 64},
     "VT_W_ARTIFACT_BINDING_INCOMPLETE"),
    ({"artifact_count": "3"},
     "VT_W_ARTIFACT_BINDING_INCOMPLETE"),
    ({"artifact_paths": "a.md, b.md"},
     "VT_W_ARTIFACT_PATHS_NOT_LIST"),
])
def test_a_malformed_binding_field_warns(fields: dict, code: str) -> None:
    """Every other graph field was already shape-validated. These are the new ones."""
    validate = _load("validate-receipts")
    viols: list = []
    validate._shape_validate_binding_fields(fields, "r.md", viols)
    assert code in [v["code"] for v in viols], viols
    assert all(v["is_warn"] for v in viols)


def test_a_well_formed_binding_warns_about_nothing() -> None:
    validate = _load("validate-receipts")
    viols: list = []
    validate._shape_validate_binding_fields(
        {"artifact_manifest_sha256": "a" * 64, "artifact_count": "2",
         "artifact_paths": "[a.md, b.md]"}, "r.md", viols)
    assert viols == []


def test_the_shape_validator_is_reached_from_the_graph_field_check() -> None:
    """Wiring, not just the leaf: a receipt with a bad digest must WARN end to end."""
    validate = _load("validate-receipts")
    viols: list = []
    validate._check_graph_fields(
        {"agent": "contrarian", "review_mode": "plan", "review_verdict": "PASS",
         "artifact_manifest_sha256": "banana", "artifact_count": "1"},
        "/tmp/r.md", viols)
    assert "VT_W_ARTIFACT_DIGEST_INVALID" in [v["code"] for v in viols]


# ---------------------------------------------------------------------------
# The refusal reason has to name the TRUE cause
# ---------------------------------------------------------------------------
#
# Widening a condition without widening the reason is how a gate ends up telling
# the reader that a receipt carries review_verdict=FAIL when it carries PASS.
# Measured on the 6,635-receipt vault corpus on 2026-08-19, before this fix and
# with the requirement ON, that happened 308 times in _check_nn3 and 981 times in
# _check_nn10. Every verdict was correct; only the explanation was false.
#
# The NN#3 case needs a FAIL from ANOTHER receipt in the ancestor set, because
# the ancestor walk is not change_id scoped and that is what steered the message.

_FALSE = "review_verdict=FAIL"
_TRUE = "reads review_verdict=PASS but is not bound"


def _two_plan_reviews() -> dict:
    """One unbound PASS and one FAIL, both reachable from the same coder."""
    return {
        "pass.md": _node("contrarian", fname="pass.md", review_mode="plan",
                         review_verdict="PASS"),
        "fail.md": _node("contrarian", fname="fail.md", review_mode="plan",
                         review_verdict="FAIL", **_BOUND),
        "coder.md": _node("coder", fname="coder.md",
                          gated_by=["pass.md", "fail.md"]),
    }


def test_nn3_does_not_report_an_unbound_pass_as_a_fail_verdict(monkeypatch) -> None:
    graph = _load("validate-receipt-graph")
    index = _two_plan_reviews()
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    viols = graph._check_nn3("c1", list(index), index)
    assert len(viols) == 1
    detail = viols[0]["detail"]
    assert _TRUE in detail and "pass.md" in detail
    assert not detail.endswith(f"{_FALSE} — gate not satisfied")
    # The code assignment is unchanged: this is a message fix, not a reclassify.
    assert viols[0]["code"] == "NN3_VIOLATION"


def test_nn3_still_says_fail_when_the_verdict_really_is_fail(monkeypatch) -> None:
    """The other half of the branch. Without this, naming the unbound case could
    silently swallow the genuine FAIL message and nothing would notice."""
    graph = _load("validate-receipt-graph")
    index = _two_plan_reviews()
    del index["pass.md"]
    index["coder.md"]["gated_by"] = ["fail.md"]
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    viols = graph._check_nn3("c1", list(index), index)
    assert len(viols) == 1
    assert viols[0]["detail"].endswith(f"{_FALSE} — gate not satisfied")
    assert _TRUE not in viols[0]["detail"]


@pytest.mark.parametrize("mode", ["plan", "output"])
def test_nn10_does_not_claim_no_pass_exists_when_one_does(mode: str, monkeypatch) -> None:
    graph = _load("validate-receipt-graph")
    index = _graph_index()
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    viols = [v for v in graph._check_nn10("c1", list(index), index)
             if f"{mode}-review" in v["detail"]]
    assert len(viols) == 1
    detail = viols[0]["detail"]
    assert _TRUE in detail
    assert "but none with review_verdict=PASS" not in detail


@pytest.mark.parametrize("mode", ["plan", "output"])
def test_nn10_keeps_the_absent_wording_when_the_verdict_is_absent(mode, monkeypatch) -> None:
    graph = _load("validate-receipt-graph")
    index = _graph_index()
    for node in index.values():
        if node["review_mode"] == mode:
            node["review_verdict"] = ""
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    viols = [v for v in graph._check_nn10("c1", list(index), index)
             if f"{mode}-review" in v["detail"]]
    assert len(viols) == 1
    assert "but none with review_verdict=PASS" in viols[0]["detail"]
    assert _TRUE not in viols[0]["detail"]


def test_the_nn3_oracle_says_which_refusal_happened(tmp_path: pathlib.Path, monkeypatch) -> None:
    gate = _load("check_gate_precondition")
    empty, unbound = tmp_path / "empty", tmp_path / "unbound"
    empty.mkdir()
    unbound.mkdir()
    _write_receipt(unbound, "plan.md", agent="contrarian", change_id="c1",
                   review_mode="plan", review_verdict="PASS")
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")

    absent = gate.check_gate_precondition("c1", "nn3", str(empty))
    found = gate.check_gate_precondition("c1", "nn3", str(unbound))
    assert absent.verdict == "REFUSE" and found.verdict == "REFUSE"
    assert "no contrarian receipt with agent=contrarian" in absent.reason
    assert _TRUE in found.reason and "plan.md" in found.reason
    assert "no contrarian receipt" not in found.reason


def test_automerge_says_which_refusal_happened_on_both_arms(merge_corpus, monkeypatch) -> None:
    merge = _load("automerge_gate")
    unbound = merge_corpus()
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    _, plan_reason = merge.can_auto_merge("c1", unbound)
    assert _TRUE in plan_reason and "no contrarian plan-review PASS receipt found" not in plan_reason

    bound_plan = pathlib.Path(merge_corpus())
    _write_receipt(bound_plan, "contrarian-plan.md", agent="contrarian", change_id="c1",
                   review_mode="plan", review_verdict="PASS", **_BOUND)
    _, output_reason = merge.can_auto_merge("c1", str(bound_plan))
    assert _TRUE in output_reason and "output-review" in output_reason


def test_automerge_keeps_the_absent_wording_when_nothing_is_there(tmp_path, monkeypatch) -> None:
    merge = _load("automerge_gate")
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    _, reason = merge.can_auto_merge("c1", str(tmp_path))
    assert reason == "REFUSE: no contrarian plan-review PASS receipt found for change_id"


@pytest.mark.parametrize("bound,expected", [
    (False, "plan-review PASS not bound to an artifact (-0.25)"),
    (True, "plan-review verdict FAIL (-0.25)"),
])
def test_judge_score_deduction_label_names_the_true_cause(
    bound: bool, expected: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    """`_get_review_verdict` reports an unbound PASS as FAIL so callers inherit
    the refusal. The label must not inherit the wrong noun with it."""
    judge = _load("judge-score")
    monkeypatch.setattr(judge, "RECEIPTS", tmp_path)
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    verdict = "FAIL" if bound else "PASS"
    extra = _BOUND if bound else {}
    _write_receipt(tmp_path, "contrarian-plan.md", agent="contrarian", change_id="c1",
                   review_mode="plan", review_verdict=verdict, **extra)
    result = judge.score_change("c1")
    deductions = result.get("deductions") or result["rule_following_preview"]["deductions"]
    assert expected in deductions


@pytest.fixture
def judge_corpus(tmp_path: pathlib.Path):
    """A BOUND plan PASS plus one output review the caller specifies.

    Binding the plan review is what isolates the output label. An unbound plan
    PASS fires the plan deduction as well, and the assertion below could then be
    satisfied by the wrong branch.
    """
    def build(**output_fields: str) -> pathlib.Path:
        _write_receipt(tmp_path, "contrarian-plan.md", agent="contrarian",
                       change_id="c1", review_mode="plan",
                       review_verdict="PASS", **_BOUND)
        _write_receipt(tmp_path, "contrarian-output.md", agent="contrarian",
                       change_id="c1", review_mode="output", **output_fields)
        return tmp_path
    return build


@pytest.mark.parametrize("bound,expected", [
    (False, "output-review PASS not bound to an artifact (-0.20)"),
    (True, "output-review verdict FAIL (-0.20)"),
])
def test_judge_score_output_deduction_label_names_the_true_cause(
    bound: bool, expected: str, judge_corpus, monkeypatch
) -> None:
    """The output arm of the pair above. Both labels are live, both can inherit
    the wrong noun from `_get_review_verdict`, and only the plan one was pinned."""
    judge = _load("judge-score")
    verdict = "FAIL" if bound else "PASS"
    extra = _BOUND if bound else {}
    monkeypatch.setattr(judge, "RECEIPTS",
                        judge_corpus(review_verdict=verdict, **extra))
    monkeypatch.setenv("WULONG_REQUIRE_BINDING", "1")
    result = judge.score_change("c1")
    deductions = result.get("deductions") or result["rule_following_preview"]["deductions"]
    assert expected in deductions
    assert not any("plan-review" in d for d in deductions)
