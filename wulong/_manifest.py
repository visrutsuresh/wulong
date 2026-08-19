"""Artifact manifest: what a review receipt was actually looking at.

A `review_verdict: PASS` names a `change_id` and nothing else, so the same PASS
authorises anything that carries that label. This module produces the digest
that closes the gap, and the honest description of what it closes is narrow.

It stops SUBSTITUTION: reviewing plan A then shipping plan B, reusing one
change_id's PASS for a different artifact, and editing an artifact after the
PASS was written. It does NOT stop a rogue writer, because the hash is UNKEYED:
anyone who can write a receipt can read the artifact and compute its digest.

Three limits that stay open, disclosed together rather than one at a time:

  1. The digest is unkeyed, so it is an attestation, not a signature.
  2. Mode bits and empty directories are outside it, so a `chmod +x` is
     invisible to a digest that matches.
  3. The digest binds the MULTISET of contents. No path is hashed, so a rename
     is invisible, and so is swapping which name holds which content inside the
     bound set.

CONTENT ONLY. No path enters the digest. That is what makes the digest a
function of bytes alone: no anchor to choose, no normalisation form, no locale,
no separator. Paths belong in the receipt as diagnostics for a human, and no
verifier resolves them.

The serialisation is pinned, because a digest whose recipe is ambiguous is not a
digest:

  * sha256 of each artifact's RAW BYTES, lowercase hex, no text mode and no
    normalisation.
  * Those digests sorted as ASCII bytes, one per line, EVERY line terminated by
    a newline including the last.
  * sha256 of that byte string is the manifest digest.

Sorting fixed-width lowercase hex as ASCII gives the same order in every locale,
which is why argument order cannot move the answer.

Two artifacts with identical bytes emit TWO identical lines. There is no dedupe,
deliberately: the digest binds the count as well as the contents, so dropping a
duplicate would let a two-file review claim to cover one file.

Every refusal is a named error and never a fallback. N=0, a repeated path, a
directory, a missing path, a symlink and an unreadable file all raise
ManifestError. A manifest over nothing must not exist, because it would be a
receipt attesting to the empty string.

ponytail: hashlib plus os.path, no dependency, no config, no plugin point. The
ceiling is a flat list of regular files hashed whole. The upgrade path, if
keyed attestation ever ships, is to sign THIS digest rather than to change it.
"""
from __future__ import annotations

import hashlib
import os
from typing import Iterable


class ManifestError(ValueError):
    """A refusal. Every one names the offending artifact."""


# One MiB. Large enough that a normal receipt or source file is a single read,
# small enough that a stray multi-gigabyte artifact does not become resident.
_READ_CHUNK = 1 << 20


def artifact_digest(path: str) -> str:
    """Return the lowercase sha256 hex of the raw bytes at PATH.

    The order of the checks is load-bearing. `os.path.islink` is lstat-based, so
    a broken symlink is refused AS a symlink rather than reported as missing,
    and the target of a live symlink is never opened. `os.path.isfile` is last
    because it also excludes a FIFO, which `open` would block on forever.
    """
    if os.path.islink(path):
        raise ManifestError(
            f"symlink refused, not followed and target not hashed: {path}"
        )
    if not os.path.exists(path):
        raise ManifestError(f"missing artifact: {path}")
    if os.path.isdir(path):
        raise ManifestError(f"directory refused, an artifact is a file: {path}")
    if not os.path.isfile(path):
        raise ManifestError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ManifestError(f"unreadable artifact: {path} ({exc})") from exc
    return digest.hexdigest()


def artifact_digests(paths: Iterable[str]) -> list[str]:
    """Validate PATHS and return their digests sorted as ASCII bytes.

    Duplicates in the RESULT are kept, because two artifacts may legitimately
    hold the same bytes. Duplicates in the INPUT are refused, because passing
    one path twice is a caller error rather than a second artifact.
    """
    supplied = list(paths)
    if not supplied:
        raise ManifestError(
            "no artifacts supplied: a manifest over nothing is refused, "
            "since it would attest to the digest of the empty string"
        )
    # ponytail: sameness is decided on the absolute path, so two spellings that
    # differ only through a symlinked PARENT directory are not caught here. They
    # then hash identical bytes and produce two identical lines, which is the
    # no-dedupe rule, so the digest still binds the count honestly. Upgrade path
    # if that ever matters: key on os.stat st_dev plus st_ino.
    seen: dict[str, str] = {}
    for path in supplied:
        key = os.path.abspath(path)
        if key in seen:
            raise ManifestError(
                f"artifact supplied twice: {path} (already given as {seen[key]})"
            )
        seen[key] = path
    return sorted(artifact_digest(path) for path in supplied)


def manifest_bytes(paths: Iterable[str]) -> bytes:
    """Return the exact bytes that get hashed. Exposed so a reviewer can see them."""
    return "".join(digest + "\n" for digest in artifact_digests(paths)).encode("ascii")


def manifest_digest(paths: Iterable[str]) -> str:
    """Return the lowercase sha256 hex over the manifest of PATHS."""
    return hashlib.sha256(manifest_bytes(paths)).hexdigest()


def verify_manifest(paths: Iterable[str], expected: str) -> bool:
    """Recompute the manifest over PATHS and compare it to EXPECTED.

    Strict: EXPECTED must already be the pinned lowercase hex. An uppercase
    value names the same number but fails here, and the shape validator in
    validate-receipts.py is what explains why.
    """
    return manifest_digest(paths) == expected


def _demo() -> None:
    """Runnable check: the pinned recipe, order independence, and one refusal."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        made = []
        for name, body in (("a", b"alpha\n"), ("b", b"beta\n"), ("c", b"gamma\n")):
            target = os.path.join(tmp, name)
            with open(target, "wb") as handle:
                handle.write(body)
            made.append(target)
        pinned = "5ffb95d3ac75fb5d8dec10a38d0e6477096cdf373f4767f61ae8fc4b1aa5a612"
        assert manifest_digest(made) == pinned, manifest_digest(made)
        assert manifest_digest(list(reversed(made))) == pinned
        try:
            manifest_digest([])
        except ManifestError:
            pass
        else:  # pragma: no cover
            raise AssertionError("N=0 must refuse")
        print(f"manifest _demo OK: {pinned}")


if __name__ == "__main__":
    _demo()
