"""Vault root resolution. ONE implementation for the whole toolchain.

Before this module every script that needed a vault root invented its own
precedence, and the two most common inventions were both wrong in a way that
silently targets the WRONG VAULT:

  * resolve from ``__file__``. In a wheel that is ``site-packages``, so the tool
    scans the install directory, finds nothing and reports success.
  * read the environment FIRST and treat an explicit flag as a fallback, so
    ``--root vaultB`` read, wrote and (in one case) deleted inside vaultA.

The precedence below is the only one, and it is the obvious one:

  1. an explicit path handed in by the caller (a flag or a positional)
  2. the WULONG_ROOT environment variable
  3. the floor, and which floor depends on who is asking:
       - an ENGINE SCRIPT passes ``fallback``, its install-relative directory.
         A script sitting at <vault>/Meta/sync/x.py KNOWS its vault, so that
         beats any guess. This is exactly the matrix these scripts already had.
       - an ENTRY POINT passes no fallback, because in a wheel its
         install-relative path is site-packages. It instead walks up from the
         CURRENT WORKING DIRECTORY looking for a vault marker, and if that finds
         nothing it raises with a clear error naming all three options.

The two floors are not interchangeable and the order matters. Walking up from
CWD ahead of a script's own install-relative directory would point an engine
script at whichever vault the operator happened to be standing in.

ponytail: stdlib os/pathlib only, no config file, no plugin hook. Ceiling is a
single marker filename set; upgrade path is a marker file that carries data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

ENV_VAR = "WULONG_ROOT"

# A directory holding either of these is a vault. CLAUDE.md is the policy file
# every vault has; .wulong is what `wulong init` writes and is the marker a
# vault can carry without committing a policy file.
MARKERS = ("CLAUDE.md", ".wulong")

_MAX_WALK = 40

_WARNED: set[str] = set()


class RootNotFound(RuntimeError):
    """No vault root could be resolved and no fallback was offered."""


def _walk_up_from_cwd() -> Optional[str]:
    try:
        current = Path.cwd().resolve()
    except OSError:
        return None
    for _ in range(_MAX_WALK):
        for marker in MARKERS:
            try:
                if (current / marker).exists():
                    return str(current)
            except OSError:
                pass
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _warn_once(message: str, key: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    print(message, file=sys.stderr)


def _warn_if_not_a_vault(root: str) -> None:
    """One WARN if the resolved root holds no Meta/ directory.

    A governance tool that reports clean because it had nothing to look at is a
    false green. Warning only: the exit code is unchanged, so no caller breaks.
    """
    if os.path.isdir(os.path.join(root, "Meta")):
        return
    _warn_once(
        f"WARN: no Meta/ directory under the resolved vault root {root!r}. "
        "Nothing will be scanned, so a clean result below means NOT CHECKED, "
        f"not verified. Pass --root, or set {ENV_VAR}.",
        f"nometa:{root}",
    )


def _not_found_message(tool: str) -> str:
    return (
        f"{tool}: cannot determine which vault to use. Three ways to say so:\n"
        f"  1. pass the path explicitly, for example --root /path/to/vault\n"
        f"  2. export {ENV_VAR}=/path/to/vault\n"
        f"  3. run this from inside the vault (any directory under one holding "
        f"{' or '.join(MARKERS)})\n"
        "Refusing to guess: guessing here scans, writes to or deletes from the "
        "wrong vault."
    )


def resolve_root(
    cli_root: Optional[str] = None,
    *,
    fallback: Optional[str] = None,
    tool: str = "wulong",
) -> str:
    """Resolve the vault root. See the module docstring for the precedence.

    ``fallback`` is the install-relative floor for engine scripts that run as
    children of an entry point. Entry points pass none and get an error instead,
    because an entry point that guesses is the wrong-vault bug itself.
    """
    if cli_root:
        root = str(cli_root)
    else:
        env = os.environ.get(ENV_VAR, "").strip()
        if env:
            root = env
        elif fallback:
            root = str(fallback)
        else:
            walked = _walk_up_from_cwd()
            if not walked:
                raise RootNotFound(_not_found_message(tool))
            root = walked
    _warn_if_not_a_vault(root)
    return root


def child_env(root: str, env: Optional[dict] = None) -> dict:
    """Environment for a subprocess, with the resolved root pinned into it.

    Every spawn in this toolchain goes through here. Ambient inheritance is not
    good enough: the parent may have resolved a root the environment does not
    name, and a child that re-resolves independently is how parent and child end
    up auditing two different vaults.
    """
    out = dict(os.environ if env is None else env)
    out[ENV_VAR] = str(root)
    return out


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        a = os.path.join(tmp, "vaultA")
        b = os.path.join(tmp, "vaultB")
        for v in (a, b):
            os.makedirs(os.path.join(v, "Meta"))
        os.environ[ENV_VAR] = a
        assert resolve_root(b) == b, "explicit path must beat the environment"
        assert resolve_root(None) == a, "environment is tier 2"
        del os.environ[ENV_VAR]
        assert resolve_root(None, fallback=b) == b, "the install-relative floor is tier 3"
        os.environ[ENV_VAR] = a
        assert resolve_root(None, fallback=b) == a, "the environment beats the floor"
        del os.environ[ENV_VAR]
        assert resolve_root(a, fallback=b) == a, "an explicit path beats the floor"
        os.environ[ENV_VAR] = ""
        assert resolve_root(a) == a, "an empty environment value must not win"
        del os.environ[ENV_VAR]
        assert child_env(a)[ENV_VAR] == a
    print("_root selftest: all assertions PASS")


if __name__ == "__main__":
    _selftest()
