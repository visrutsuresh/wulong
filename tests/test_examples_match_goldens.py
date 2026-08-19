"""The examples are executable claims, and the suite has to check them.

CI ran every example and diffed its output against a committed golden. pytest did
not, and no test referenced `tests/expected/` at all. That split is a false green
with a measurable cost: unwiring `_ensure_gitignore` from `wulong-init.py` leaves
the FULL suite passing, and only the CI golden goes red. Under that mutation
`wulong init` writes the secrets overlay into the target with no `.gitignore`
created, so it is committable. A security-relevant call site guarded by a CI step
alone is guarded by whatever anyone happens to notice in a build log.

This file folds the CI step into the suite. CI keeps its own copy: two runners
that disagree is information, and one of them living only in a workflow file is
exactly what this closes.

ponytail: subprocess plus `difflib`, both stdlib, no fixture framework and no new
config. Ceiling is that it asserts on stdout only; the upgrade path, if an example
ever needs to assert on stderr or on files left behind, is to widen the golden
format rather than to add a second runner.
"""
import difflib
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"
_EXPECTED = _REPO / "tests" / "expected"


def _golden_for(example: pathlib.Path) -> pathlib.Path:
    """examples/01_init_and_doctor.py -> tests/expected/ex01.txt"""
    return _EXPECTED / f"ex{example.name.split('_')[0]}.txt"


def _examples() -> list[pathlib.Path]:
    return sorted(p for p in _EXAMPLES.glob("*.py") if p.name[0].isdigit())


@pytest.mark.parametrize("example", _examples(), ids=lambda p: p.stem)
def test_the_example_still_prints_its_golden(example: pathlib.Path) -> None:
    golden = _golden_for(example)
    assert golden.is_file(), f"{example.name} has no golden at {golden}"
    result = subprocess.run([sys.executable, str(example)],
                            capture_output=True, text=True, cwd=str(_REPO))
    assert result.returncode == 0, f"{example.name} exited {result.returncode}:\n{result.stderr}"
    diff = list(difflib.unified_diff(
        golden.read_text(encoding="utf-8").splitlines(keepends=True),
        result.stdout.splitlines(keepends=True),
        fromfile=str(golden.relative_to(_REPO)), tofile=f"{example.name} stdout",
    ))
    assert diff == [], "".join(diff)


def test_every_golden_belongs_to_an_example_and_every_example_has_one() -> None:
    """A new example with no golden, or a golden whose example was renamed away,
    is the same silent nothing as an axis that is defined and never registered.
    """
    examples = _examples()
    assert examples, "no examples found, so the test above guards nothing"
    assert {_golden_for(e) for e in examples} == set(_EXPECTED.glob("ex*.txt"))
