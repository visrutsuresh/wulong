"""test_wheel_payload.py - the payload must survive the BUILD, not just the tree.

Inspecting the source tree would reproduce the exact bug Change C fixes. The
0.1.0 wheel shipped 67 entries and zero agent definitions while all 65 sat in the
tree the whole time, so "the files are there" proves nothing. Every assertion
below therefore reads a freshly built wheel.

Two packaging traps are pinned here because both shipped silently once:
  1. Explicit package-data keys REPLACE the setuptools defaults, so a missing key
     drops files with no warning.
  2. `payload/**/*.md` looks recursive and is, but glob never descends into a
     directory whose name starts with a dot unless the dot is written literally.
     That pattern shipped exactly ONE file (payload/CLAUDE.md) and no agents.

ponytail: stdlib zipfile + subprocess. The build is skipped, not faked, when the
toolchain is unavailable, because a fake build proves nothing about a real one.
"""
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_PAYLOAD = _REPO / "wulong" / "payload"

_EXPECTED_AGENTS = 65
_EXPECTED_HOOKS = 1
_EXPECTED_SKILLS = 2
_EXPECTED_CLAUDE_MD = 1
_EXPECTED_TOTAL = _EXPECTED_AGENTS + _EXPECTED_HOOKS + _EXPECTED_SKILLS + _EXPECTED_CLAUDE_MD


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> list[str]:
    """Build a wheel from a clean copy of the package and return its entry names."""
    try:
        import build  # noqa: F401
        import setuptools  # noqa: F401
    except ImportError:
        pytest.skip("build/setuptools unavailable; cannot build a wheel to inspect")

    work = tmp_path_factory.mktemp("wheelsrc")
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "AUTHORS"):
        src = _REPO / name
        if src.exists():
            shutil.copy2(src, work / name)
    shutil.copytree(
        _REPO / "wulong",
        work / "wulong",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    out = tmp_path_factory.mktemp("wheelout")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out), "."],
        cwd=str(work), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as zf:
        return zf.namelist()


def _under(names: list[str], prefix: str, suffix: str = "") -> list[str]:
    return [n for n in names if n.startswith(prefix) and n.endswith(suffix)]


def test_wheel_carries_every_agent_definition(wheel_names: list[str]) -> None:
    found = _under(wheel_names, "wulong/payload/.claude/agents/", ".md")
    assert len(found) == _EXPECTED_AGENTS, f"wheel carries {len(found)} agent definitions"


def test_wheel_carries_the_hook(wheel_names: list[str]) -> None:
    found = _under(wheel_names, "wulong/payload/.claude/hooks/", ".py")
    assert len(found) == _EXPECTED_HOOKS, found


def test_wheel_carries_both_skills(wheel_names: list[str]) -> None:
    """NOTICE reproduces an MIT licence naming the ponytail SKILL.md by path.

    If the skills do not ship, that legal file makes a statement about a file
    that exists nowhere in the distribution.
    """
    found = _under(wheel_names, "wulong/payload/.claude/skills/", "SKILL.md")
    assert len(found) == _EXPECTED_SKILLS, found
    assert any(n.endswith("ponytail/SKILL.md") for n in found)
    assert any(n.endswith("explain-in-plain-english/SKILL.md") for n in found)


def test_wheel_carries_the_governance_policy(wheel_names: list[str]) -> None:
    found = [n for n in wheel_names if n == "wulong/payload/CLAUDE.md"]
    assert len(found) == _EXPECTED_CLAUDE_MD, wheel_names[:5]


def test_wheel_payload_total_is_exact(wheel_names: list[str]) -> None:
    found = _under(wheel_names, "wulong/payload/")
    assert len(found) == _EXPECTED_TOTAL, sorted(found)


def test_wheel_still_carries_the_engine_scripts(wheel_names: list[str]) -> None:
    """The payload must be additive. Adding a package-data key must not drop one."""
    found = _under(wheel_names, "wulong/sync/", ".py")
    assert len(found) == len(list((_REPO / "wulong" / "sync").glob("*.py")))


def test_init_installs_exactly_what_the_wheel_ships(wheel_names: list[str]) -> None:
    """A clone and a pip install must produce the same vault.

    init walks the payload directory on disk; the wheel is filtered by the
    package-data globs. Anything in one and not the other is a real divergence.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wulong_init", _REPO / "wulong" / "sync" / "wulong-init.py"
    )
    init = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init)

    from_disk = {p.as_posix() for p in init.payload_files()}
    from_wheel = {
        n[len("wulong/payload/"):] for n in _under(wheel_names, "wulong/payload/")
    }
    assert from_disk == from_wheel, {
        "clone only": sorted(from_disk - from_wheel),
        "wheel only": sorted(from_wheel - from_disk),
    }
