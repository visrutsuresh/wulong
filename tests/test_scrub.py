"""test_scrub.py — scrub dry-run over examples/ exits 0.

Verifies that examples/ contains no patterns from scrub-patterns.txt.
This is the mechanical anti-theater check: a real example that prints
personal data would fail here before it reaches CI.
ponytail: one subprocess call; no extra deps.
"""
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SCRUB = _REPO / "scripts" / "scrub.sh"
_EXAMPLES = _REPO / "examples"
_PATTERNS = _REPO / "scrub-patterns.txt"


def test_examples_scrub_clean() -> None:
    """scrub.sh exits 0 on examples/ — no sensitive patterns present."""
    if not _PATTERNS.exists():
        import pytest
        pytest.skip("scrub-patterns.txt not present (overlay not initialised)")

    result = subprocess.run(
        ["bash", str(_SCRUB), str(_EXAMPLES)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scrub.sh found sensitive patterns in examples/:\n{result.stdout}\n{result.stderr}"
    )
