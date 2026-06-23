#!/usr/bin/env python3
"""
post-write-trigger.py — PostToolUse hook target.

Called by Claude Code AFTER any tool call. Checks if the tool was a write to
Cerebrum (Write/Edit/NotebookEdit/MultiEdit targeting Meta/*) and, if so,
triggers compile-context.py immediately.

Replaces the dead fswatch/launchd watcher. No background daemon, no polling,
no silent failure — fires exactly when Claude Code modifies Cerebrum.

Debounce: 3s min-gap via /tmp/post-write-trigger.last to prevent thrash when
agents batch-write many files.

Exit codes:
  0 = success (compile-context fired OR no-op because not a Cerebrum write)
  1 = unrecoverable error
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
COMPILE_CONTEXT = VAULT / "Meta" / "sync" / "compile-context.py"
DEBOUNCE_FILE = Path("/tmp/post-write-trigger.last")
DEBOUNCE_SECONDS = 3.0
PAUSE_SENTINEL = VAULT / "Meta" / "sync" / ".watcher-paused"

WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}

# Paths that should NEVER trigger recompile (would loop)
EXCLUDED_PREFIXES = (
    str(VAULT / "Meta" / "context"),         # compile-context's OWN output
    str(VAULT / "Meta" / "feedback" / "raw"), # high-frequency raw writes
    str(VAULT / "Meta" / "agent-bus"),        # bus message store
    str(VAULT / "Meta" / "hermes" / "notebook.archive.md"),  # archive churn
    str(VAULT / "Meta" / "sync"),             # sync internals (logs, etc.)
)


def get_tool_info() -> tuple[str | None, str | None]:
    """Extract (tool_name, target_path) from Claude Code hook env/stdin.
    Returns (None, None) if not extractable.
    """
    tool = os.environ.get("CLAUDE_TOOL") or os.environ.get("CC_TOOL")
    target = (
        os.environ.get("CLAUDE_TOOL_TARGET")
        or os.environ.get("CLAUDE_TOOL_FILE_PATH")
        or os.environ.get("CC_FILE_PATH")
    )

    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            try:
                obj = json.loads(data)
                if isinstance(obj, dict):
                    tool = tool or obj.get("tool") or obj.get("toolName") or obj.get("tool_name")
                    ti = obj.get("toolInput") or obj.get("tool_input") or {}
                    if isinstance(ti, dict):
                        target = target or ti.get("file_path") or ti.get("filePath") or ti.get("path") or ti.get("notebook_path")
            except json.JSONDecodeError:
                pass

    return tool, target


def is_cerebrum_write(tool: str | None, target: str | None) -> bool:
    if not tool or tool not in WRITE_TOOLS:
        return False
    if not target:
        return False
    target_str = str(target)
    if not target_str.startswith(str(VAULT / "Meta")):
        return False
    for excluded in EXCLUDED_PREFIXES:
        if target_str.startswith(excluded):
            return False
    return True


def debounce_ok() -> bool:
    try:
        last = DEBOUNCE_FILE.stat().st_mtime
        if (time.time() - last) < DEBOUNCE_SECONDS:
            return False
    except FileNotFoundError:
        pass
    return True


def mark_fired() -> None:
    try:
        DEBOUNCE_FILE.write_text(str(time.time()))
    except OSError:
        pass


def main() -> int:
    if PAUSE_SENTINEL.exists():
        return 0  # killswitch active

    tool, target = get_tool_info()
    if not is_cerebrum_write(tool, target):
        return 0  # not a Cerebrum write — no-op

    if not debounce_ok():
        return 0  # debounced — recent fire already covers this

    if not COMPILE_CONTEXT.exists():
        sys.stderr.write(f"[post-write-trigger] compile-context.py missing at {COMPILE_CONTEXT}\n")
        return 1

    # Fire compile-context.py — fire-and-forget with reasonable timeout
    try:
        subprocess.run(
            ["python3", str(COMPILE_CONTEXT)],
            cwd=str(VAULT),
            timeout=30,
            check=False,  # don't fail the hook if compile-context has issues
            capture_output=True,
        )
        mark_fired()
    except subprocess.TimeoutExpired:
        sys.stderr.write("[post-write-trigger] compile-context timed out (30s)\n")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[post-write-trigger] compile-context error: {e}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[post-write-trigger] ERROR: {e}\n")
        sys.exit(1)
