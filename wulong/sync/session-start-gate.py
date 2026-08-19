#!/usr/bin/env python3
"""
session-start-gate.py — UserPromptSubmit hook: auto-register + obligation check.

Runs on EVERY user prompt (via .claude/settings.json UserPromptSubmit hook).
Identity key: CLAUDE_CODE_SESSION_ID env var (confirmed present in hook env).

Behaviour:
  - Read-only steady state: session_id already registered → zero writes, check obligations.
  - First-turn only: auto-register (prune dead + append new entry + ledger record).
  - Obligations: OBSERVE (hermes+metis mtime > session start) + pending *to-jarvis* handoffs.
  - Silent (zero stdout) when all met. Prints ⛔ block (≤400 chars / ≤8 lines) when unmet.
  - Exit 0 ALWAYS — never blocks the user's prompt.

Reuses helpers from session-guard.py directly (import by path).
"""

from __future__ import annotations

import glob
import importlib.util
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from wulong._root import resolve_root

# ─── paths ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_GUARD_PATH = _HERE / "session-guard.py"

# THE SPLIT THIS COMMENT USED TO DOCUMENT IS CLOSED. session-guard.py honoured
# WULONG_ROOT while this file resolved install-relative, so with the variable
# exported a register through the guard landed under the environment vault while
# this gate read the install-relative one and reported no active sessions. Both
# files now go through the same resolver, so both land on the same registry.
# The reason it was deferred (a one-file patch would move the reader count the
# docs and tests assert against) died with the shared resolver: the count is
# re-measured from disk and the detector follows the import.
VAULT_ROOT = Path(resolve_root(fallback=str(_HERE.parent.parent),
                               tool="session-start-gate"))
REGISTRY_FILE = str(VAULT_ROOT / "Meta" / "session-registry.json")
LEDGER_FILE = str(VAULT_ROOT / "Meta" / "doctor" / "observe-pass-ledger.jsonl")
HERMES_NOTEBOOK = str(VAULT_ROOT / "Meta" / "hermes" / "notebook.md")
METIS_NOTEBOOK = str(VAULT_ROOT / "Meta" / "metis" / "notebook.md")
HANDOFFS_DIR = str(VAULT_ROOT / "Meta" / "handoffs")

# ─── load session-guard helpers ───────────────────────────────────────────────

def _load_guard():
    spec = importlib.util.spec_from_file_location("session_guard_gate", str(_GUARD_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Redirect all path constants so tests can patch this module's attrs instead
    mod.REGISTRY_FILE = REGISTRY_FILE
    mod.LEDGER_FILE = LEDGER_FILE
    mod.HERMES_NOTEBOOK = HERMES_NOTEBOOK
    mod.METIS_NOTEBOOK = METIS_NOTEBOOK
    return mod


_guard = _load_guard()

# Expose guard helpers as module-level names so tests can patch them on THIS module
_read_registry = _guard._read_registry
_write_registry = _guard._write_registry
_is_pid_alive = _guard._is_pid_alive
_observe_seen = _guard._observe_seen
_ledger_append = _guard._ledger_append

# STALE_MINUTES imported from guard — single source of truth
STALE_MINUTES = _guard.STALE_MINUTES

# ─── in-memory prune (no write on read path) ──────────────────────────────────

def _prune_to_live(registry: dict) -> list:
    """Return only the live sessions from registry, in memory. Zero writes."""
    live = []
    for s in registry.get("sessions", []):
        if _is_pid_alive(s.get("pid", -1)):
            live.append(s)
    return live

# ─── handoff discovery ────────────────────────────────────────────────────────

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{4})")


def _filename_epoch(path: str) -> float | None:
    """Parse YYYY-MM-DD-HHMM from filename and return epoch float, or None."""
    name = os.path.basename(path)
    m = _TS_RE.search(name)
    if not m:
        return None
    ts_str = m.group(1)
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d-%H%M").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _pending_handoffs(session_start_epoch: float) -> list[str]:
    """
    Return sorted-desc (by mtime) list of *to-jarvis*.md handoff basenames
    that are pending for this session: filename-epoch OR mtime >= session_start_epoch,
    excluding anything inside archive/.
    """
    pattern = os.path.join(HANDOFFS_DIR, "*to-jarvis*.md")
    archive_prefix = os.path.join(HANDOFFS_DIR, "archive")
    pending = []
    for path in glob.glob(pattern):
        # Exclude anything under archive/
        if path.startswith(archive_prefix):
            continue
        # Determine effective epoch: filename-parsed first, fallback to mtime
        fname_epoch = _filename_epoch(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        effective_epoch = fname_epoch if fname_epoch is not None else mtime
        if effective_epoch >= session_start_epoch:
            pending.append((mtime, path))
    # Sort descending by mtime (newest first)
    pending.sort(key=lambda x: x[0], reverse=True)
    return [os.path.basename(p) for _, p in pending]

# ─── first-registration write ─────────────────────────────────────────────────

def _register_new_session(session_id: str, live_sessions: list) -> str:
    """
    Write the pruned live list + new entry atomically.
    Returns the started ISO string for the new session.
    Also appends a ledger 'register' event.
    Only called when session_id is NOT already in live_sessions.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    new_entry = {
        "session_id": session_id,
        "pid": os.getppid(),
        "started": now_iso,
        "focus": "jarvis",
        "last_file": "",
    }
    registry = {"sessions": live_sessions + [new_entry]}
    _write_registry(registry)
    _ledger_append({
        "event": "register",
        "session_id": session_id,
        "start_iso": now_iso,
        "ts": now_iso,
    })
    return now_iso

# ─── output formatting ────────────────────────────────────────────────────────

_MAX_CHARS = 400
_MAX_LINES = 8
_MAX_HANDOFFS_SHOWN = 5


def _build_block(observe_unmet: bool, pending: list[str]) -> str:
    lines = ["⛔ SESSION-START OBLIGATIONS UNMET"]
    if observe_unmet:
        lines.append("• OBSERVE: hermes+metis notebooks not updated since session start")
    overflow = len(pending) - _MAX_HANDOFFS_SHOWN
    shown = pending[:_MAX_HANDOFFS_SHOWN]
    for fname in shown:
        lines.append(f"• HANDOFF: Meta/handoffs/{fname}")
    if overflow > 0:
        lines.append(f"• … and {overflow} more handoffs")
    lines.append("→ Fix: spawn hermes+metis OBSERVE (def 5a/5a-bis); read+archive each named handoff.")

    # Hard cap at 8 lines first (before char cap to preserve line integrity)
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]

    block = "\n".join(lines)

    # Hard cap at 400 chars
    if len(block) > _MAX_CHARS:
        block = block[:_MAX_CHARS - len(" … (truncated)")] + " … (truncated)"

    return block

# ─── main ─────────────────────────────────────────────────────────────────────

def run() -> None:
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print("⛔ SESSION-START GATE: CLAUDE_CODE_SESSION_ID not set — cannot register session.")
        sys.exit(0)

    # 1. Read registry once (pure read)
    registry = _read_registry()

    # 2. Prune dead/stale IN MEMORY only — no write on read path
    live = _prune_to_live(registry)

    # 3. Check if session_id already registered
    live_ids = {s.get("session_id") for s in live}
    if session_id in live_ids:
        # Steady-state: zero writes — find this session's started for obligation check
        started_iso = next(
            (s.get("started", "") for s in live if s.get("session_id") == session_id),
            "",
        )
    else:
        # First registration: persist pruned list + new entry
        started_iso = _register_new_session(session_id, live)

    # 4. Compute session-start epoch from started_iso
    try:
        session_start_epoch = datetime.fromisoformat(started_iso).timestamp()
    except (ValueError, TypeError):
        # Cannot determine epoch — treat OBSERVE as unmet, handoffs unknown
        session_start_epoch = datetime.now(timezone.utc).timestamp()

    # 5. Check obligations
    observe_ok = _observe_seen(started_iso)
    pending = _pending_handoffs(session_start_epoch)

    # 6. Output
    if observe_ok and not pending:
        # Silent — zero bytes stdout
        return

    print(_build_block(not observe_ok, pending))


if __name__ == "__main__":
    run()
