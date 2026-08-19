"""test_frontmatter.py - behaviour lock on the hand-rolled frontmatter reader.

`parse_frontmatter` in `wulong/_frontmatter.py` is a line splitter, not a YAML
parser. These tests pin what it does on the malformed inputs a real receipt
directory produces, including the two silent-REFUSE cliffs: a byte-order mark,
and frontmatter longer than the 4096-byte read window that three callers use.

Until E1a the same splitter existed eight times over, and three of the eight had
drifted. The last section here pins the unification itself: exactly one function
under `wulong/` still scans for the delimiter, and the three drifted callers keep
named wrappers whose extra behaviour is asserted rather than described.

ponytail: importlib + tmp_path only. The loader is duplicated from test_gate.py
rather than hoisted into a conftest, because one 6-line duplication is cheaper
than a new file. Upgrade path: hoist if a third module needs it.
"""
import ast
import importlib.util
import pathlib
import sys

import pytest

from wulong._frontmatter import parse_frontmatter, split_frontmatter

_REPO = pathlib.Path(__file__).resolve().parent.parent
_PKG = _REPO / "wulong"
_SYNC = _PKG / "sync"
_GATE_PY = _SYNC / "check_gate_precondition.py"

# check_gate_precondition.py reads only fh.read(4096) per receipt.
_READ_LIMIT = 4096


def _load(stem: str):
    safe = stem.replace("-", "_") + "_fm"
    spec = importlib.util.spec_from_file_location(safe, _SYNC / (stem + ".py"))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load("check_gate_precondition")
parse = parse_frontmatter


def test_no_leading_delimiter_yields_nothing() -> None:
    assert parse("agent: contrarian\n---\nbody\n") == {}


def test_unterminated_block_yields_nothing() -> None:
    assert parse("---\nagent: contrarian\nstatus: DONE\n") == {}


def test_crlf_line_endings_still_parse() -> None:
    """Split is on \\n and each line is stripped, so the stray \\r falls off."""
    assert parse("---\r\nagent: contrarian\r\n---\r\nbody") == {"agent": "contrarian"}


def test_value_containing_a_colon_is_kept_whole() -> None:
    """partition(':') splits on the FIRST colon, so a time value survives."""
    assert parse('---\ntime: "19:00"\n---\n')["time"] == '"19:00"'


def test_leading_and_trailing_whitespace_is_stripped_from_key_and_value() -> None:
    assert parse("---\n   agent :   contrarian   \n---\n") == {"agent": "contrarian"}


def test_empty_value_is_kept_as_empty_string_not_dropped() -> None:
    """The key exists with '', so callers must test the value, not membership."""
    assert parse("---\nagent:\n---\n") == {"agent": ""}


def test_comment_lines_are_ignored() -> None:
    assert parse("---\n# a note\nagent: contrarian\n---\n") == {"agent": "contrarian"}


def test_duplicated_key_resolves_to_the_last_value() -> None:
    assert parse("---\nreview_verdict: FAIL\nreview_verdict: PASS\n---\n")["review_verdict"] == "PASS"


def test_byte_order_mark_defeats_the_parser_entirely() -> None:
    """A BOM before '---' makes startswith fail, so every field is lost."""
    assert parse("﻿---\nagent: contrarian\n---\n") == {}


def test_keys_keep_the_case_they_were_written_in() -> None:
    """Only query-receipts lowers keys, and it does so in its own wrapper."""
    assert parse("---\nAgent: Contrarian\n---\n") == {"Agent": "Contrarian"}


def test_a_bracketed_value_stays_a_string() -> None:
    """Only judge-score turns this into a list, and it does so in its own wrapper."""
    assert parse("---\ngated_by: [a.md, b.md]\n---\n")["gated_by"] == "[a.md, b.md]"


def test_split_returns_the_block_without_its_delimiters_and_the_body() -> None:
    block, body = split_frontmatter("---\nagent: coder\n---\nline one\nline two\n")
    assert block == "agent: coder"
    assert body == "line one\nline two\n"


def test_split_reports_absence_as_none_not_as_an_empty_block() -> None:
    """An empty but PRESENT block is '' and must not be confused with absent."""
    assert split_frontmatter("no frontmatter here") == (None, "no frontmatter here")
    assert split_frontmatter("---\nunterminated\n")[0] is None
    assert split_frontmatter("---\n---\nbody\n") == ("", "body\n")


# ---------------------------------------------------------------------------
# The 4096-byte read window
# ---------------------------------------------------------------------------

def _receipt_text(frontmatter_bytes: int) -> str:
    """Build a valid nn3 receipt whose closing '---' ENDS at frontmatter_bytes."""
    head = (
        "---\n"
        "agent: contrarian\n"
        "change_id: fixture-change-2026\n"
        "review_mode: plan\n"
        "review_verdict: PASS\n"
    )
    pad_len = frontmatter_bytes - len(head) - len("---\n")
    assert pad_len >= 3, "target size too small to pad"
    text = head + "# " + "p" * (pad_len - 3) + "\n" + "---\n\n## Task\nfixture\n"
    assert text.index("---\n", 3) + 4 == frontmatter_bytes
    return text


@pytest.mark.parametrize("size,expected", [
    (_READ_LIMIT - 6, "ALLOW"),
    (_READ_LIMIT + 6, "REFUSE"),
])
def test_frontmatter_past_the_read_window_silently_refuses(
    tmp_path: pathlib.Path, size: int, expected: str
) -> None:
    """Identical valid fields, only the byte offset of the closing '---' differs.

    Past 4096 bytes the closing delimiter is never read, parse_frontmatter
    returns {}, and the gate REFUSEs with the generic no-receipt-found reason
    rather than anything naming the real cause.
    """
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "padded.md").write_text(_receipt_text(size), encoding="utf-8")
    result = gate.check_gate_precondition("fixture-change-2026", "nn3", str(receipts))
    assert result.verdict == expected
    # The full text parses fine; only the truncated read fails.
    assert parse((receipts / "padded.md").read_text(encoding="utf-8"))["review_verdict"] == "PASS"


@pytest.mark.parametrize("size,expected", [
    (_READ_LIMIT - 6, {"review_verdict": "PASS"}),
    (_READ_LIMIT + 6, {}),
])
def test_the_read_window_belongs_to_the_caller_not_the_shared_reader(
    tmp_path: pathlib.Path, size: int, expected: dict
) -> None:
    """session-close-audit caps its own read at 4096; the shared reader does not.

    E1a moved the parse into wulong/_frontmatter.py and deliberately left every
    read where it was, because three tools cap and five do not. If the cap ever
    migrates into the shared reader, the five whole-file callers start losing
    fields and this goes red.
    """
    audit = _load("session-close-audit")
    receipt = tmp_path / "padded.md"
    receipt.write_text(_receipt_text(size), encoding="utf-8")
    got = audit._parse_receipt_frontmatter(str(receipt))
    assert {k: v for k, v in got.items() if k == "review_verdict"} == expected
    assert parse(receipt.read_text(encoding="utf-8"))["review_verdict"] == "PASS"


# ---------------------------------------------------------------------------
# E1a: one scanner, three named wrappers
# ---------------------------------------------------------------------------
#
# The scan root is pinned to _REPO/"wulong", the same pin tests/test_execution
# _surface.py:33 uses. Meta/sync/ and build/lib/ each hold a full second copy of
# these scripts, so an unscoped walk would count every parser two or three times
# and go red on day one. Deleting those copies is denied, so the pin is the
# honest scope: what SHIPS is what is under wulong/.

def _delimiter_scanners() -> list[tuple[str, str]]:
    """Functions that split text into lines AND compare against the '---' literal.

    Structural, not name-based, so a scanner cannot hide behind a rename.
    """
    found = []
    for py in sorted(_PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            consts = {n.value for n in ast.walk(node)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            splits = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "split" and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "\n"
                for n in ast.walk(node)
            )
            if "---" in consts and splits:
                found.append((str(py.relative_to(_REPO)), node.name))
    return sorted(found)


# Every entry is here because it is TRUE today, and each carries the reason it is
# not the shared reader. A fourth entry means someone hand-rolled a ninth copy.
_EXPECTED_SCANNERS = sorted([
    ("wulong/_frontmatter.py", "split_frontmatter"),
    # Body extractors, not field parsers: they return the text AFTER the block and
    # read no keys. Out of E1a's named scope of eight field parsers. Folding them
    # into split_frontmatter is a separate change with its own before/after.
    ("wulong/sync/judge-score.py", "_parse_body"),
    ("wulong/sync/verify-change.py", "_parse_body"),
])

_MIN_PACKAGE_FILES = 59


def test_exactly_one_delimiter_scanner_ships_under_the_package() -> None:
    files = [p for p in _PKG.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) >= _MIN_PACKAGE_FILES, (
        f"walked only {len(files)} .py files under {_PKG}, floor is "
        f"{_MIN_PACKAGE_FILES}. A vacuous walk would pass this test by finding "
        "nothing at all."
    )
    found = _delimiter_scanners()
    assert found == _EXPECTED_SCANNERS, (
        "the set of functions that scan for the frontmatter delimiter changed.\n"
        f"found:    {found}\n"
        f"expected: {_EXPECTED_SCANNERS}"
    )


def test_the_gate_reads_receipts_through_the_shared_module() -> None:
    """The point of E1a: the gate has no parser of its own to drift."""
    assert gate.parse_frontmatter is parse_frontmatter
    assert not hasattr(gate, "_parse_frontmatter")


def test_judge_score_wrapper_adds_list_coercion_and_nothing_else() -> None:
    judge = _load("judge-score")
    fields = judge._parse_frontmatter("---\ngated_by: [a.md, b.md]\nagent: coder\n---\n")
    assert fields["gated_by"] == ["a.md", "b.md"]
    assert fields["agent"] == "coder"


def test_query_receipts_wrapper_lowers_keys_and_strips_one_quote_layer() -> None:
    query = _load("query-receipts")
    fields, body = query._parse_frontmatter("---\nAgent: \"Coder\"\n---\nbody\n")
    assert fields == {"agent": "Coder"}
    assert body == "body\n"
    assert query._parse_frontmatter("no frontmatter") == ({}, "no frontmatter")


def test_validate_receipts_wrapper_returns_none_where_the_others_return_empty() -> None:
    """None means "no frontmatter at all", which is the violation it reports.

    An empty dict cannot carry that distinction: a '---' block that parsed to
    nothing is a different defect from a receipt with no block.
    """
    validate = _load("validate-receipts")
    assert validate.parse_frontmatter("no frontmatter")[0] is None
    assert validate.parse_frontmatter("---\nunterminated\n")[0] is None
    assert validate.parse_frontmatter("---\n---\nbody\n")[0] == {}
    assert validate.parse_frontmatter("---\nagent: coder\n---\nbody\n") == (
        {"agent": "coder"}, "body\n"
    )
