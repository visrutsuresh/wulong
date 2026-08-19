"""test_manifest.py - behaviour lock on wulong/_manifest.py.

The digest is the whole product here, so the recipe is pinned to exact hex
constants rather than to a property. A property test would pass against any
self-consistent serialisation, including a different one from the published
rule, and the published rule is what a reviewer recomputes by hand.

Four pinned cases, each closing a specific way the recipe could drift:
  * three artifacts, the ordinary case;
  * the same three in reverse argument order, which must not move the digest;
  * one changed byte in any one artifact, which must move it;
  * two DISTINCT artifacts holding identical bytes, which must emit two lines.
    That fourth constant is the one a dedupe implementation goes red against.

ponytail: tmp_path plus a direct import. No fixtures package, no helper module.
"""
import hashlib
import os
import pathlib

import pytest

from wulong._manifest import (
    ManifestError,
    artifact_digest,
    artifact_digests,
    manifest_bytes,
    manifest_digest,
    verify_manifest,
)

# Pinned by hand from the published recipe: sha256 of each artifact's raw bytes,
# lowercase hex, sorted as ASCII, one per line, every line newline-terminated,
# then sha256 of that byte string.
_D_ALPHA = "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
_D_BETA = "f2c82decdd7181cf98945929a62598db7e6b477e11f6e0eb0ae97020eff151ad"
_D_GAMMA = "ae9a6306a205417afddd14316cc1d0d5e04a98f1be10865dce643925ee070ce2"

_THREE = "5ffb95d3ac75fb5d8dec10a38d0e6477096cdf373f4767f61ae8fc4b1aa5a612"
_IDENTICAL_PAIR = "864b56fca60546ca83313d9c72fdaa9571d1fa11d42bbf94721a1112f5fecbcc"
_DEDUPED_PAIR = "b5454524c38a374446a799681b63e59f2fae89c731d101aed291dd750f96a3cb"


def _write(directory: pathlib.Path, name: str, body: bytes) -> str:
    path = directory / name
    path.write_bytes(body)
    return str(path)


@pytest.fixture
def three(tmp_path: pathlib.Path) -> list[str]:
    return [
        _write(tmp_path, "a.txt", b"alpha\n"),
        _write(tmp_path, "b.txt", b"beta\n"),
        _write(tmp_path, "c.txt", b"gamma\n"),
    ]


# ---------------------------------------------------------------------------
# The pinned recipe
# ---------------------------------------------------------------------------

def test_each_artifact_digest_is_lowercase_sha256_of_raw_bytes(three: list[str]) -> None:
    assert artifact_digest(three[0]) == _D_ALPHA
    assert artifact_digest(three[1]) == _D_BETA
    assert artifact_digest(three[2]) == _D_GAMMA
    assert artifact_digest(three[0]) == hashlib.sha256(b"alpha\n").hexdigest()


def test_three_artifacts_hash_to_the_pinned_constant(three: list[str]) -> None:
    assert manifest_digest(three) == _THREE


def test_the_hashed_bytes_are_sorted_hex_each_newline_terminated(three: list[str]) -> None:
    """Including the LAST line. A missing final newline moves the digest."""
    blob = manifest_bytes(three)
    assert blob == b"".join(
        (h + "\n").encode("ascii") for h in sorted((_D_ALPHA, _D_BETA, _D_GAMMA))
    )
    assert blob.endswith(b"\n")
    assert hashlib.sha256(blob).hexdigest() == _THREE
    assert hashlib.sha256(blob.rstrip(b"\n")).hexdigest() != _THREE


def test_argument_order_does_not_change_the_digest(three: list[str]) -> None:
    assert manifest_digest(list(reversed(three))) == _THREE
    assert manifest_digest([three[1], three[2], three[0]]) == _THREE


@pytest.mark.parametrize("index", [0, 1, 2])
def test_one_changed_byte_in_any_artifact_changes_the_digest(
    three: list[str], index: int
) -> None:
    original = pathlib.Path(three[index]).read_bytes()
    pathlib.Path(three[index]).write_bytes(original[:-1] + b"X")
    assert manifest_digest(three) != _THREE


def test_no_paths_are_hashed_so_a_rename_is_invisible(
    three: list[str], tmp_path: pathlib.Path
) -> None:
    """The disclosed limit, asserted rather than only written down."""
    renamed = [str(tmp_path / f"renamed-{i}.dat") for i in range(3)]
    for old, new in zip(three, renamed):
        os.rename(old, new)
    assert manifest_digest(renamed) == _THREE


def test_swapping_which_name_holds_which_content_is_also_invisible(
    tmp_path: pathlib.Path
) -> None:
    """The multiset limit. Wider than a rename, and disclosed beside it."""
    first = [
        _write(tmp_path, "one.txt", b"alpha\n"),
        _write(tmp_path, "two.txt", b"beta\n"),
        _write(tmp_path, "three.txt", b"gamma\n"),
    ]
    assert manifest_digest(first) == _THREE
    pathlib.Path(first[0]).write_bytes(b"beta\n")
    pathlib.Path(first[1]).write_bytes(b"alpha\n")
    assert manifest_digest(first) == _THREE


# ---------------------------------------------------------------------------
# No dedupe: the digest binds the multiset, therefore the count
# ---------------------------------------------------------------------------

def test_two_distinct_artifacts_with_identical_bytes_emit_two_lines(
    tmp_path: pathlib.Path
) -> None:
    pair = [
        _write(tmp_path, "b.txt", b"beta\n"),
        _write(tmp_path, "b-copy.txt", b"beta\n"),
    ]
    assert artifact_digests(pair) == [_D_BETA, _D_BETA]
    assert manifest_bytes(pair) == (_D_BETA + "\n" + _D_BETA + "\n").encode("ascii")
    assert manifest_digest(pair) == _IDENTICAL_PAIR


def test_a_dedupe_implementation_would_go_red(tmp_path: pathlib.Path) -> None:
    """Pins the two constants apart, so collapsing the pair is a visible failure."""
    single = [_write(tmp_path, "b.txt", b"beta\n")]
    assert manifest_digest(single) == _DEDUPED_PAIR
    assert _DEDUPED_PAIR != _IDENTICAL_PAIR


# ---------------------------------------------------------------------------
# Every refusal, each with a named error
# ---------------------------------------------------------------------------

def test_zero_artifacts_refuses(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ManifestError, match="no artifacts supplied"):
        manifest_digest([])


def test_the_empty_manifest_is_never_the_digest_of_the_empty_string() -> None:
    """The exact value a fallback would have produced, named so it cannot ship."""
    with pytest.raises(ManifestError):
        manifest_digest([])
    assert hashlib.sha256(b"").hexdigest() == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_the_same_path_twice_refuses(three: list[str]) -> None:
    with pytest.raises(ManifestError, match="supplied twice"):
        manifest_digest([three[0], three[1], three[0]])


def test_two_spellings_of_the_same_path_refuse(tmp_path: pathlib.Path) -> None:
    target = _write(tmp_path, "a.txt", b"alpha\n")
    indirect = str(tmp_path / "." / "a.txt")
    with pytest.raises(ManifestError, match="supplied twice"):
        manifest_digest([target, indirect])


def test_a_directory_refuses(tmp_path: pathlib.Path) -> None:
    (tmp_path / "adir").mkdir()
    with pytest.raises(ManifestError, match="directory refused"):
        manifest_digest([str(tmp_path / "adir")])


def test_a_missing_path_refuses(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ManifestError, match="missing artifact"):
        manifest_digest([str(tmp_path / "nope.txt")])


def test_a_symlink_refuses_and_its_target_is_not_hashed(tmp_path: pathlib.Path) -> None:
    target = _write(tmp_path, "real.txt", b"alpha\n")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(ManifestError, match="symlink refused"):
        manifest_digest([str(link)])


def test_a_broken_symlink_refuses_as_a_symlink_not_as_missing(
    tmp_path: pathlib.Path
) -> None:
    link = tmp_path / "dangling.txt"
    link.symlink_to(tmp_path / "gone.txt")
    with pytest.raises(ManifestError, match="symlink refused"):
        manifest_digest([str(link)])


def test_an_unreadable_file_refuses(tmp_path: pathlib.Path) -> None:
    """chmod 000 is a no-op as root, so the mode is verified before trusting it."""
    target = pathlib.Path(_write(tmp_path, "secret.txt", b"alpha\n"))
    os.chmod(target, 0o000)
    try:
        if os.access(target, os.R_OK):
            pytest.fail(
                "chmod 000 left the file readable, which happens as root. This "
                "assertion exists so the test cannot pass vacuously."
            )
        with pytest.raises(ManifestError, match="unreadable artifact"):
            manifest_digest([str(target)])
    finally:
        os.chmod(target, 0o644)


def test_a_fifo_refuses_rather_than_blocking_on_open(tmp_path: pathlib.Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(ManifestError, match="not a regular file"):
        manifest_digest([str(fifo)])


# ---------------------------------------------------------------------------
# verify_manifest
# ---------------------------------------------------------------------------

def test_verify_matches_the_pinned_constant(three: list[str]) -> None:
    assert verify_manifest(three, _THREE) is True
    assert verify_manifest(three, _THREE.upper()) is False
    assert verify_manifest(three[:2], _THREE) is False
