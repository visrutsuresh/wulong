#!/usr/bin/env python3
"""
changelog-append.py — flock-safe change-log append helper (ADR-008 R5, P3 prereq).

CLI:
    python3 Meta/sync/changelog-append.py "<full change-log line>"
    python3 Meta/sync/changelog-append.py --demo

All spawned workers and the spawn wrapper MUST call this script (or the
flock_append() function imported from it) instead of the Edit tool for
change-log appends once parallel heads ship (P3). The Edit tool holds no
cross-process lock; with 8 concurrent workers the last-write-wins race can
silently drop or interleave lines.

ponytail: stdlib fcntl + argparse; no new deps; single-purpose.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

_VAULT = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
CHANGE_LOG = _VAULT / "Meta" / "change-log.md"


def flock_append(path: Path, line: str) -> None:
    """Append *line* to *path* under an exclusive flock.

    Line must already end with '\\n' if a newline is desired; this function
    writes exactly what it receives.  Opens in append mode so concurrent
    writers never clobber each other even when the lock is briefly contested.
    """
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _demo() -> None:
    """Self-check: two rapid appends both land in a temp file (no lost line)."""
    import tempfile
    import threading

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    lines_written: list[str] = []
    errors: list[Exception] = []

    def _write(n: int) -> None:
        line = f"line-{n}\n"
        lines_written.append(line)
        try:
            flock_append(tmp_path, line)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_write, args=(1,))
    t2 = threading.Thread(target=_write, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    content = tmp_path.read_text(encoding="utf-8")
    tmp_path.unlink()

    assert not errors, f"flock_append raised: {errors}"
    written = set(content.splitlines(keepends=True))
    expected = set(lines_written)
    assert written == expected, (
        f"lost or duplicate lines — written={written!r} expected={expected!r}"
    )
    print("demo PASS: both rapid appends landed, no lost line")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="flock-safe change-log append helper",
    )
    parser.add_argument(
        "line",
        nargs="?",
        help="The full change-log line to append (must include trailing newline or one is added)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the two-rapid-appends self-check and exit",
    )
    parser.add_argument(
        "--path",
        default=None,
        help=f"Path to append to (default: {CHANGE_LOG})",
    )
    args = parser.parse_args()

    if args.demo:
        _demo()
        return 0

    if not args.line:
        parser.error("A change-log line is required (or --demo for self-check)")

    target = Path(args.path) if args.path else CHANGE_LOG
    line = args.line if args.line.endswith("\n") else args.line + "\n"
    flock_append(target, line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
