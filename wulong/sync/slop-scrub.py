#!/usr/bin/env python3
"""
slop-scrub.py — em-dash pre-delivery scrubber (NN#21 enforcement).

Scans text for U+2014 EM DASH only. No banned-phrase detection.

Usage:
  python3 slop-scrub.py [file]   # file path as argv[1]
  echo "..." | python3 slop-scrub.py  # stdin

Exit codes:
  0 — clean (no em dash found, or any error — fail-OPEN)
  1 — em dash(es) found (confirmed match)

A bug must fail toward delivery, never wedge a session.

ponytail: single-codepoint scan, stdlib only, no abstractions.
"""
from __future__ import annotations

import sys

EM_DASH = "—"


def scan(text: str) -> list[tuple[int, int]]:
    """Return list of (line_number, col) for every U+2014 in text. 1-indexed."""
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for col, ch in enumerate(line, 1):
            if ch == EM_DASH:
                hits.append((ln, col))
    return hits


def main() -> int:
    try:
        if len(sys.argv) > 1:
            try:
                text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
            except OSError as e:
                sys.stderr.write(f"[slop-scrub] cannot read {sys.argv[1]}: {e}\n")
                return 0  # fail-open on missing file
        else:
            text = sys.stdin.read()

        hits = scan(text)
        if not hits:
            return 0

        print(f"[slop-scrub] {len(hits)} em-dash(es) found (U+2014):")
        for ln, col in hits:
            print(f"  line {ln}, col {col}")
        return 1

    except Exception as e:  # noqa: BLE001 — fail-open, never wedge
        sys.stderr.write(f"[slop-scrub] error (fail-open): {e}\n")
        return 0


def _demo() -> None:
    """Runnable self-check — assert-based, no framework."""
    import subprocess, os, tempfile

    py = sys.executable
    this = __file__

    def run(input_text: str | None = None, file_content: str | None = None) -> int:
        if file_content is not None:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(file_content)
                fname = f.name
            try:
                r = subprocess.run([py, this, fname], capture_output=True, text=True)
                return r.returncode
            finally:
                os.unlink(fname)
        else:
            r = subprocess.run([py, this], input=input_text, capture_output=True, text=True)
            return r.returncode

    # Confirmed match — must exit 1
    assert run("hello—world") == 1, "em-dash via stdin must exit 1"

    # Clean string — must exit 0
    assert run("hello world, no dash here") == 0, "clean text must exit 0"

    # Crashing input path — must fail open (exit 0)
    r = subprocess.run([py, this, "/tmp/slop-scrub-nonexistent-99999.txt"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "missing file must exit 0 (fail-open)"

    print("[slop-scrub] self-check PASS (3/3)")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--demo":
        _demo()
    else:
        sys.exit(main())
