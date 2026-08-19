"""Receipt frontmatter reading. ONE implementation for the whole toolchain.

Eight scripts each carried their own copy of this line splitter and four of them
decide a governance verdict from what it returns. Three of the eight had quietly
drifted apart: one coerced a `[a, b]` value into a Python list, one lowercased
keys and stripped quotes, one returned None where the others returned an empty
dict. Changing how the gates read a receipt therefore meant making the same edit
eight times, correctly, or leaving the gates disagreeing about what a receipt
says.

This is a LINE SPLITTER, not a YAML parser, and that is deliberate. The gates
must not acquire a parsing dependency, and the consequences of the choice are
published rather than hidden: see "What the gate actually proves" in README.md
and docs/ARCHITECTURE.md. Fact 2 there cites the last-value-wins overwrite,
which is the unguarded assignment in `parse_frontmatter` below.

The module sits at the package root, NOT in `wulong/sync/`, for the same reason
`wulong/_root.py` does: a script executed by path gets its own directory as
`sys.path[0]`, so `from wulong._frontmatter import ...` resolves only through the
installed package. That is what makes it one implementation instead of one copy
per directory.

Two things stay OUT of here on purpose.

  * The three drifted callers keep named wrappers at their own sites, so their
    behaviour is visible where it is used rather than hidden behind a flag here.
  * The read window keeps its call site. `check_gate_precondition.py`,
    `automerge_gate.py` and `session-close-audit.py` read only the first 4096
    bytes of a receipt while the rest read the whole file, and that split is real
    and tested. These functions take TEXT and never open a file.

ponytail: stdlib only, no options argument, no policy hook, no caching. The
ceiling is a flat key to string map taken from the first `---` block. The upgrade
path is to edit these two function bodies once, which is the whole point of the
file existing.
"""
from __future__ import annotations

from typing import Optional


def split_frontmatter(text: str) -> tuple[Optional[str], str]:
    """Split TEXT into (frontmatter block, body).

    The block is what lies between the leading `---` and the first later line
    that is exactly `---`, with neither delimiter included. It is None, and the
    body is the whole input, when the text does not start with `---` or has no
    closing delimiter. A leading byte order mark defeats the startswith check, so
    a receipt saved with a BOM has no frontmatter at all.
    """
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    close: Optional[int] = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        return None, text
    return "\n".join(lines[1:close]), "\n".join(lines[close + 1:])


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the frontmatter of TEXT into a flat map of key to string.

    Returns an empty dict when frontmatter is absent or unterminated. Blank lines
    and lines whose first non-blank character is `#` are skipped. A line counts
    as a field only if it contains a colon, and the split is on the FIRST colon,
    so a value may contain more of them. Key and value are both stripped.

    On a duplicated key the LAST value wins, because the assignment below is
    unguarded. That is the behaviour fact 2 of "What the gate actually proves"
    reports, and it is why a `review_verdict: FAIL` line followed by a
    `review_verdict: PASS` line resolves to PASS.
    """
    block, _ = split_frontmatter(text)
    if block is None:
        return {}
    fields: dict[str, str] = {}
    for line in block.split("\n"):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return fields
