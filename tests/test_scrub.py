"""test_scrub.py - the deny-list scan, widened to every tracked file, plus proof it fires.

Two halves.

1. The live half drives check (c) of scripts/pre-publish-assert.sh, which is the
   scan that gates a push. Before 0.3.0 this file passed only examples/ (two
   files) to scripts/scrub.sh, so a personal literal anywhere else in the tree
   failed nothing.

2. The fixture half proves the scan is capable of failing at all. Before 0.3.0
   both scanners passed each deny-list line to grep with its inline trailing
   comment still attached, and every live pattern carries one, so every pattern
   was a regex matching nothing and half 1 could not have gone red no matter
   what the tree contained. These tests also pin the tag split: [allow-author]
   exempts the commit-author check only, [allow-public] exempts the file scan
   only, and a tag left in a comment exempts nothing and warns.

ponytail: stdlib plus subprocess into the real shell scripts. No conftest, no
helper package, no mocking. Ceiling = both scripts resolve their repo root from
BASH_SOURCE, so copying them into a tmp tree is the whole injection mechanism
and no source change is needed to test them. Upgrade path: if either script ever
grows a --patterns flag, drop _fixture_tree and pass the flag.
"""
import pathlib
import shutil
import subprocess

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SCRUB = _REPO / "scripts" / "scrub.sh"
_ASSERT = _REPO / "scripts" / "pre-publish-assert.sh"
_PATTERNS = _REPO / "scrub-patterns.txt"

_SECRET = "ZZ-NOT-A-REAL-TOKEN"
_PUBLIC = "zz-public-handle"


def _skip_without_patterns() -> None:
    """An absent deny-list is a skip with a stated reason, never a silent pass."""
    if not _PATTERNS.exists():
        pytest.skip(
            "scrub-patterns.txt is absent, so there is no deny-list to scan against "
            "and a green result here would mean nothing. Create it with: "
            "cp scrub-patterns.txt.example scrub-patterns.txt"
        )


def _fixture_tree(tmp_path: pathlib.Path, patterns: str, files: dict) -> pathlib.Path:
    """A tree the real scripts treat as their own repo root."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(_SCRUB, root / "scripts" / "scrub.sh")
    shutil.copy(_ASSERT, root / "scripts" / "pre-publish-assert.sh")
    (root / "scrub-patterns.txt").write_text(patterns)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def _run(argv: list) -> tuple:
    result = subprocess.run(argv, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _scrub(root: pathlib.Path) -> tuple:
    return _run(["bash", str(root / "scripts" / "scrub.sh"), str(root)])


def test_publish_gate_scan_is_clean_on_every_tracked_file() -> None:
    """Check (c) of the publish gate is clean across all git-tracked files.

    Only the (c) markers are asserted. Check (a) depends on the configured
    remote and (b) on local commit history, so a fork or a clean clone can
    legitimately differ there without that telling us anything about the scan.
    """
    _skip_without_patterns()
    # Check (c) iterates git ls-files. Outside a work tree that list is empty
    # and (c) prints PASS after scanning nothing, which would be a green result
    # that means nothing at all.
    tracked = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files"], capture_output=True, text=True
    ).stdout
    if not tracked.strip():
        pytest.skip("not a git work tree, so check (c) has no file list to scan")
    _, out = _run(["bash", str(_ASSERT)])
    assert "SCRUB HIT (c):" not in out, f"deny-list hit in a tracked file:\n{out}"
    assert "PASS (c):" in out, f"check (c) did not report a result:\n{out}"


def test_file_scan_fires_on_an_untagged_pattern(tmp_path: pathlib.Path) -> None:
    """The whole point of B5: an untagged deny-list value in a file is caught."""
    root = _fixture_tree(
        tmp_path,
        f"{_SECRET}\n",
        {"docs/handbook.md": f"deploy key is {_SECRET} here\n"},
    )
    code, out = _scrub(root)
    assert code == 1, f"scan did not fire:\n{out}"
    assert "docs/handbook.md" in out


def test_file_scan_strips_the_inline_trailing_comment(tmp_path: pathlib.Path) -> None:
    """The defect itself: a commented pattern used to be a regex matching nothing.

    Every live pattern carries a trailing comment, so without this the scan is
    inert against the entire deny-list.
    """
    root = _fixture_tree(
        tmp_path,
        f"{_SECRET}      # the thing we must never publish\n",
        {"docs/handbook.md": f"deploy key is {_SECRET} here\n"},
    )
    code, out = _scrub(root)
    assert code == 1, f"comment was not stripped, so the pattern matched nothing:\n{out}"
    assert "docs/handbook.md" in out


def test_allow_public_exempts_the_file_scan(tmp_path: pathlib.Path) -> None:
    """A value that is public by construction passes, which is what makes the gate usable."""
    root = _fixture_tree(
        tmp_path,
        f"[allow-public] {_PUBLIC}      # published in every repo URL\n",
        {"README.md": f"git clone https://github.com/{_PUBLIC}/thing\n"},
    )
    code, out = _scrub(root)
    assert code == 0, f"[allow-public] did not exempt the file scan:\n{out}"


def test_allow_author_does_not_exempt_the_file_scan(tmp_path: pathlib.Path) -> None:
    """The tag split, in the direction that matters.

    [allow-author] means "I commit under this name". Widening it to the file
    scan would silently strip file-scan coverage from every user who followed
    that documented instruction, so it must not.
    """
    root = _fixture_tree(
        tmp_path,
        f"[allow-author] {_SECRET}      # a name I commit under\n",
        {"docs/handbook.md": f"leaked {_SECRET} in prose\n"},
    )
    code, out = _scrub(root)
    assert code == 1, f"[allow-author] wrongly exempted the file scan:\n{out}"
    assert "docs/handbook.md" in out


def test_tag_left_in_a_comment_warns_and_exempts_nothing(tmp_path: pathlib.Path) -> None:
    """Migration guard for a pre-0.3.0 deny-list.

    The tag used to live in the comment, which is now stripped. That line must
    fail loudly rather than quietly keeping or losing its exemption.
    """
    root = _fixture_tree(
        tmp_path,
        f"{_SECRET}      # my own handle [allow-public]\n",
        {"docs/handbook.md": f"leaked {_SECRET} in prose\n"},
    )
    code, out = _scrub(root)
    assert code == 1, f"a comment-position tag still exempted the scan:\n{out}"
    assert "WARN" in out and "front of the line" in out, f"no migration warning:\n{out}"


def test_publish_gate_applies_the_same_tag_rules(tmp_path: pathlib.Path) -> None:
    """pre-publish-assert.sh check (c) is a second, separate implementation.

    Fixing scrub.sh alone would leave the script that actually gates a push
    inert, so the same three cases are asserted against it directly. Only the
    (c) markers are read: check (a) fails in any fixture because there is no
    origin remote, which says nothing about the scan.
    """
    root = _fixture_tree(
        tmp_path,
        f"[allow-public] {_PUBLIC}   # public by construction\n"
        f"[allow-author] {_SECRET}   # a name I commit under\n"
        "zz-unused-value            # stale tag position [allow-public]\n",
        {
            "README.md": f"git clone https://github.com/{_PUBLIC}/thing\n",
            "docs/handbook.md": f"leaked {_SECRET} in prose\n",
        },
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "add", "README.md", "docs/handbook.md", "scripts"],
        check=True,
        capture_output=True,
    )
    _, out = _run(["bash", str(root / "scripts" / "pre-publish-assert.sh")])
    assert "SCRUB HIT (c): docs/handbook.md" in out, (
        f"check (c) did not enforce an [allow-author] line:\n{out}"
    )
    assert "SCRUB HIT (c): README.md" not in out, (
        f"check (c) did not honour [allow-public]:\n{out}"
    )
    assert "WARN" in out and "front of the line" in out, (
        f"no migration warning for a comment-position tag:\n{out}"
    )
