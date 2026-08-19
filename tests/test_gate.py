"""test_gate.py - behaviour lock on wulong/sync/check_gate_precondition.py.

Covers the ALLOW path, every REFUSE path the script advertises, and the fact
that the two exit-code levels disagree: an unknown gate name is a REFUSE when
the function is called directly, but a usage error (exit 2) through the CLI,
because argparse rejects it against `choices` before the REFUSE branch runs.

ponytail: importlib + tmp_path only. No mocking framework, no fixtures package,
no conftest. Ceiling = the script is loaded by path because its siblings are
hyphenated and there is no wulong.sync package. Upgrade path: if wulong/sync/
ever gains an __init__.py, replace _load() with a plain import.
"""
import importlib.util
import pathlib
import re
import sys

import pytest

from wulong._manifest import manifest_digest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_GATE_PY = _REPO / "wulong" / "sync" / "check_gate_precondition.py"

# The trust-boundary disclosure is DUPLICATED across these two files. B1b greps
# both, because fixing one alone leaves the other lying.
_DISCLOSURE_DOCS = (_REPO / "README.md", _REPO / "docs" / "ARCHITECTURE.md")


def _load():
    spec = importlib.util.spec_from_file_location("check_gate_precondition", _GATE_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_gate_precondition"] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()


def _flat(text: str) -> str:
    """Collapse all whitespace runs to single spaces.

    Required: the disclosure sentences are hard-wrapped with a three-space
    continuation indent, so a literal substring match on raw text always fails.
    """
    return re.sub(r"\s+", " ", text)


def _receipt(directory: pathlib.Path, name: str, **fields: str) -> pathlib.Path:
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    path = directory / name
    path.write_text(f"---\n{lines}\n---\n\n## Task\nfixture\n", encoding="utf-8")
    return path


def _bind(directory: pathlib.Path, stem: str, body: str = "reviewed content\n") -> dict:
    """Write an artifact and return the frontmatter fields that bind it.

    The digest is COMPUTED over a real file rather than typed, so a fixture
    receipt can be handed to `wulong gate --verify` unchanged and it will pass.
    A hand-typed constant would look bound while binding nothing.
    """
    artifact = directory / f"{stem}.artifact"
    artifact.write_text(body, encoding="utf-8")
    return {
        "artifact_manifest_sha256": manifest_digest([str(artifact)]),
        "artifact_count": "1",
    }


def _pass_receipt(directory: pathlib.Path, name: str = "contrarian-plan-pass.md", **over: str):
    """A well-formed contrarian plan-review PASS, BOUND to a real artifact.

    The binding fields belong in the shared fixture rather than in the handful of
    tests that assert on them. Once `--require-binding` is the default, an
    unbound PASS is REFUSED, and every test built on an unbound fixture would
    quietly stop testing the trust boundary and start testing the migration
    default while still reporting green.
    """
    fields = {
        "agent": "contrarian",
        "change_id": "fixture-change-2026",
        "review_mode": "plan",
        "review_verdict": "PASS",
    }
    fields.update(_bind(directory, name))
    fields.update(over)
    return _receipt(directory, name, **fields)


@pytest.fixture
def receipts(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "Meta" / "receipts"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# ALLOW
# ---------------------------------------------------------------------------

def test_allow_on_wellformed_contrarian_plan_pass(receipts: pathlib.Path) -> None:
    _pass_receipt(receipts)
    result = gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts))
    assert result.verdict == "ALLOW"
    assert result.allowed is True
    assert result.matching_receipt == "contrarian-plan-pass.md"


def test_nn4_allows_tester_done(receipts: pathlib.Path) -> None:
    _receipt(receipts, "tester-smoke.md", agent="tester",
             change_id="fixture-change-2026", status="DONE")
    result = gate.check_gate_precondition("fixture-change-2026", "nn4", str(receipts))
    assert result.verdict == "ALLOW"
    assert result.matching_receipt == "tester-smoke.md"


# ---------------------------------------------------------------------------
# REFUSE
# ---------------------------------------------------------------------------

def test_refuse_when_no_receipt_exists(receipts: pathlib.Path) -> None:
    result = gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts))
    assert result.verdict == "REFUSE"
    assert "no contrarian receipt" in result.reason


def test_refuse_on_wrong_change_id(receipts: pathlib.Path) -> None:
    _pass_receipt(receipts, change_id="some-other-change-2026")
    assert gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts)).verdict == "REFUSE"


@pytest.mark.parametrize("field,value", [
    ("agent", "mastermind"),
    ("review_mode", "output"),
    ("review_verdict", "FAIL"),
])
def test_refuse_on_wrong_field_value(receipts: pathlib.Path, field: str, value: str) -> None:
    _pass_receipt(receipts, **{field: value})
    assert gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts)).verdict == "REFUSE"


def test_refuse_when_frontmatter_absent(receipts: pathlib.Path) -> None:
    (receipts / "no-frontmatter.md").write_text(
        "agent: contrarian\nchange_id: fixture-change-2026\n"
        "review_mode: plan\nreview_verdict: PASS\n",
        encoding="utf-8",
    )
    assert gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts)).verdict == "REFUSE"


def test_refuse_when_receipts_directory_missing(tmp_path: pathlib.Path) -> None:
    absent = tmp_path / "no-such-dir"
    result = gate.check_gate_precondition("fixture-change-2026", "nn3", str(absent))
    assert result.verdict == "REFUSE"
    assert "receipts directory not found" in result.reason


@pytest.mark.parametrize("blank", ["", "   "])
def test_refuse_on_blank_change_id(receipts: pathlib.Path, blank: str) -> None:
    _pass_receipt(receipts)
    result = gate.check_gate_precondition(blank, "nn3", str(receipts))
    assert result.verdict == "REFUSE"
    assert "empty or blank" in result.reason


def test_refuse_nn4_when_tester_status_is_not_done(receipts: pathlib.Path) -> None:
    _receipt(receipts, "tester-smoke.md", agent="tester",
             change_id="fixture-change-2026", status="PARTIAL")
    assert gate.check_gate_precondition("fixture-change-2026", "nn4", str(receipts)).verdict == "REFUSE"


def test_unopenable_entry_is_skipped_not_fatal(receipts: pathlib.Path) -> None:
    """A directory named *.md is listed but cannot be read; the loop swallows it.

    A directory is used rather than chmod 000 because chmod is a no-op as root,
    which would silently make this test vacuous in a root container.
    """
    (receipts / "a-directory.md").mkdir()
    assert gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts)).verdict == "REFUSE"
    _pass_receipt(receipts)
    assert gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts)).verdict == "ALLOW"


# ---------------------------------------------------------------------------
# Exit codes: the two levels disagree on the unknown-gate case
# ---------------------------------------------------------------------------

def test_unknown_gate_is_refuse_at_function_level(receipts: pathlib.Path) -> None:
    result = gate.check_gate_precondition("fixture-change-2026", "nn99", str(receipts))
    assert result.verdict == "REFUSE"
    assert "unknown gate" in result.reason


def test_unknown_gate_is_usage_error_at_cli_level(receipts: pathlib.Path) -> None:
    """Exit 2, NOT 1: argparse `choices` rejects it before the REFUSE branch."""
    with pytest.raises(SystemExit) as exc:
        gate.main(["--change-id", "fixture-change-2026", "--gate", "nn99",
                   "--receipts-dir", str(receipts), "--quiet"])
    assert exc.value.code == 2


def test_cli_returns_zero_on_allow(receipts: pathlib.Path) -> None:
    _pass_receipt(receipts)
    assert gate.main(["--change-id", "fixture-change-2026", "--gate", "nn3",
                      "--receipts-dir", str(receipts), "--quiet"]) == 0


def test_cli_returns_one_on_refuse(receipts: pathlib.Path) -> None:
    assert gate.main(["--change-id", "fixture-change-2026", "--gate", "nn3",
                      "--receipts-dir", str(receipts), "--quiet"]) == 1


# ---------------------------------------------------------------------------
# B1b: documented trust boundary
# ---------------------------------------------------------------------------

class TestDocumentedTrustBoundary:
    """Bidirectional locks on the two weaknesses the project publishes about itself.

    The disclosure is duplicated at README.md ("What the gate actually proves",
    facts 1 and 2) and docs/ARCHITECTURE.md ("What the gate actually proves",
    facts 1 and 2). It is NOT in SECURITY.md. Both files are grepped, because
    correcting one and not the other leaves a published document lying.

    Each test asserts BOTH halves: the current gate behaviour, and that the
    sentence disclosing that behaviour is still present in both documents.
    So it goes red if someone deletes the disclosure without fixing the gate,
    AND if someone fixes the gate without deleting the disclosure.

    DELETION CONTRACT, rewritten by E1b. These are assertions about a KNOWN
    LIMITATION, not about desired behaviour, so each one is deleted by the change
    that makes its own fact FALSE, and by nothing else.

    E1b is not that change for either of them. It binds a PASS to a digest of the
    artifact that was reviewed, which stops substitution, but the digest is
    UNKEYED: a process that can write into the receipts directory can also read
    the artifact and compute its digest, so fact 1 is exactly as true after E1b
    as before it. Fact 2 is untouched, because the reader is still a line
    splitter. The previous wording told a future reader to delete this whole
    class "when Change E strengthens the gate", which would have removed two
    disclosures that are still true and two tests that still hold.

    Fact 1 dies with keyed attestation, where a receipt carries a signature that
    a receipt-directory writer cannot forge. Fact 2 dies when the frontmatter
    reader rejects a duplicated key instead of taking the last one. Each deletion
    removes its own disclosure sentence from README.md and docs/ARCHITECTURE.md
    in the same change. Binding has its own class below and does not live here.

    Sentences are matched by TEXT after whitespace collapse, never by line
    number, so unrelated edits to either document cannot break them.
    """

    FACT_1 = "any process that can write into the receipts directory can mint a PASS"
    FACT_2 = "On a duplicated key the last value wins"

    def _assert_disclosed(self, sentence: str) -> None:
        for doc in _DISCLOSURE_DOCS:
            flat = _flat(doc.read_text(encoding="utf-8"))
            assert sentence in flat, (
                f"{doc.name} no longer discloses: {sentence!r}. "
                "If the gate was strengthened, delete this whole class too."
            )

    def test_any_writable_filename_can_mint_a_pass(self, receipts: pathlib.Path) -> None:
        """Filenames are never consulted; the scan branches only on frontmatter."""
        _pass_receipt(receipts, name="anyone-can-write-this.md")
        result = gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts))
        assert result.verdict == "ALLOW"
        assert result.matching_receipt == "anyone-can-write-this.md"
        # Still true with binding REQUIRED. The digest is unkeyed, so whoever
        # wrote the receipt could also read the artifact and compute it.
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts), require_binding=True
        ).verdict == "ALLOW"
        self._assert_disclosed(self.FACT_1)

    def test_duplicated_verdict_key_resolves_to_the_last_value(self, receipts: pathlib.Path) -> None:
        """FAIL followed by PASS resolves to PASS: the reader is a line splitter."""
        bound = _bind(receipts, "dup-key")
        (receipts / "dup-key.md").write_text(
            "---\nagent: contrarian\nchange_id: fixture-change-2026\n"
            "review_mode: plan\nreview_verdict: FAIL\nreview_verdict: PASS\n"
            f"artifact_manifest_sha256: {bound['artifact_manifest_sha256']}\n"
            "artifact_count: 1\n---\n",
            encoding="utf-8",
        )
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts)).verdict == "ALLOW"
        # This receipt is written INLINE rather than through _pass_receipt, so it
        # needs its own binding fields. Without them it would REFUSE once binding
        # is the default and this test would silently stop testing the overwrite.
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts), require_binding=True
        ).verdict == "ALLOW"
        self._assert_disclosed(self.FACT_2)


# ---------------------------------------------------------------------------
# E1b: artifact binding at the gate
# ---------------------------------------------------------------------------

class TestArtifactBinding:
    """What binding changes, and what it does NOT.

    The class above keeps facts 1 and 2 because both survive this change. This
    one covers the only thing that moves: an nn3 PASS with no manifest digest is
    REFUSED when binding is required, and allowed with a warning until then.
    """

    def test_unbound_pass_is_allowed_while_binding_is_off(self, receipts: pathlib.Path) -> None:
        _receipt(receipts, "unbound.md", agent="contrarian",
                 change_id="fixture-change-2026", review_mode="plan",
                 review_verdict="PASS")
        result = gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts), require_binding=False)
        assert result.verdict == "ALLOW"

    def test_unbound_pass_is_refused_when_binding_is_required(
        self, receipts: pathlib.Path
    ) -> None:
        _receipt(receipts, "unbound.md", agent="contrarian",
                 change_id="fixture-change-2026", review_mode="plan",
                 review_verdict="PASS")
        result = gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts), require_binding=True)
        assert result.verdict == "REFUSE"

    def test_bound_pass_is_allowed_either_way(self, receipts: pathlib.Path) -> None:
        _pass_receipt(receipts)
        for require in (True, False, None):
            assert gate.check_gate_precondition(
                "fixture-change-2026", "nn3", str(receipts),
                require_binding=require).verdict == "ALLOW"

    @pytest.mark.parametrize("digest,count", [
        ("banana", "1"),
        ("A" * 64, "1"),          # uppercase hex is not the pinned form
        ("0" * 63, "1"),          # one character short
        ("0" * 64, "0"),          # a manifest over nothing is refused at source
        ("0" * 64, "many"),
    ])
    def test_a_malformed_digest_is_not_a_weak_binding_it_is_none(
        self, receipts: pathlib.Path, digest: str, count: str
    ) -> None:
        _receipt(receipts, "malformed.md", agent="contrarian",
                 change_id="fixture-change-2026", review_mode="plan",
                 review_verdict="PASS", artifact_manifest_sha256=digest,
                 artifact_count=count)
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts),
            require_binding=True).verdict == "REFUSE"

    def test_nn4_is_untouched_by_binding(self, receipts: pathlib.Path) -> None:
        """The carve, asserted. nn4 reads `status` and never reads a verdict."""
        _receipt(receipts, "tester-smoke.md", agent="tester",
                 change_id="fixture-change-2026", status="DONE")
        for require in (True, False):
            assert gate.check_gate_precondition(
                "fixture-change-2026", "nn4", str(receipts),
                require_binding=require).verdict == "ALLOW"

    def test_the_legacy_exemption_keys_off_the_receipts_own_date(
        self, receipts: pathlib.Path, monkeypatch
    ) -> None:
        """ADVISORY, and the test says why: the receipt supplies the date itself."""
        _receipt(receipts, "old.md", agent="contrarian",
                 change_id="fixture-change-2026", review_mode="plan",
                 review_verdict="PASS", date="2025-01-01")
        monkeypatch.setenv("WULONG_LEGACY_UNBOUND_UNTIL", "2026-01-01")
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts),
            require_binding=True).verdict == "ALLOW"
        monkeypatch.setenv("WULONG_LEGACY_UNBOUND_UNTIL", "2024-01-01")
        assert gate.check_gate_precondition(
            "fixture-change-2026", "nn3", str(receipts),
            require_binding=True).verdict == "REFUSE"
