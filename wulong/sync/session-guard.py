#!/usr/bin/env python3
"""
session-guard.py — Multi-session conflict prevention for the wulong vault.

Usage:
  python3 session-guard.py check                  # Check for concurrent sessions, warn if found
  python3 session-guard.py register FOCUS         # Register this session (call on start)
  python3 session-guard.py release                # Remove this session (call on end)
  python3 session-guard.py safe-write FILE TMP    # Atomic write: rename TMP → FILE after lock check
  python3 session-guard.py status                 # Print all active sessions

AGENTS: Call `python3 Meta/sync/session-guard.py check` before writing:
  - Meta/brain.md
  - Meta/agent-messages.md
  - Meta/approval-queue.md
  - Any 01-Projects/*/State.md
If a conflict is detected, write to Meta/sync/conflict-queue.md instead.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY_FILE = os.path.join(VAULT_ROOT, "Meta", "session-registry.json")
CONFLICT_QUEUE = os.path.join(VAULT_ROOT, "Meta", "sync", "conflict-queue.md")
LEDGER_FILE = os.path.join(VAULT_ROOT, "Meta", "doctor", "observe-pass-ledger.jsonl")
VIOLATIONS_FILE = os.path.join(VAULT_ROOT, "Meta", "doctor", "enforcement-violations.md")
HERMES_NOTEBOOK = os.path.join(VAULT_ROOT, "Meta", "hermes", "notebook.md")
METIS_NOTEBOOK = os.path.join(VAULT_ROOT, "Meta", "metis", "notebook.md")
STALE_MINUTES = 120  # sessions older than 2 hours are considered dead even if PID looks alive


def _read_registry():
    if not os.path.exists(REGISTRY_FILE):
        return {"sessions": []}
    try:
        with open(REGISTRY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"sessions": []}


def _write_registry(data):
    tmp = REGISTRY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.rename(tmp, REGISTRY_FILE)


def _is_pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _observe_seen(session_start_iso):
    """
    Return True if BOTH hermes and metis notebooks were modified after this session's start.

    Timezone-safe: both sides converted to epoch floats before comparison.
    session_start_iso must be a UTC ISO string (e.g. "2026-05-31T06:16:57.942963+00:00").
    os.path.getmtime() returns a UTC epoch float — no TZ conversion needed.
    """
    try:
        start_epoch = datetime.fromisoformat(session_start_iso).timestamp()
    except (ValueError, TypeError):
        return False

    try:
        hermes_mtime = os.path.getmtime(HERMES_NOTEBOOK)
    except OSError:
        return False

    try:
        metis_mtime = os.path.getmtime(METIS_NOTEBOOK)
    except OSError:
        return False

    return hermes_mtime > start_epoch and metis_mtime > start_epoch


def _ledger_has_release_for(session_id):
    """Return True if the ledger already contains a release or prune record for session_id."""
    if not os.path.exists(LEDGER_FILE):
        return False
    try:
        with open(LEDGER_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("session_id") == session_id and rec.get("event") in ("release", "prune"):
                    return True
    except IOError:
        pass
    return False


def _ledger_append(record):
    """Append one JSON record (dict) as a line to the ledger. Atomic via rename."""
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    tmp = LEDGER_FILE + ".tmp"
    # Read existing content, append, write back atomically
    existing = b""
    if os.path.exists(LEDGER_FILE):
        with open(LEDGER_FILE, "rb") as f:
            existing = f.read()
    with open(tmp, "wb") as f:
        f.write(existing)
        f.write(line.encode())
    os.rename(tmp, LEDGER_FILE)


def _violations_append(line):
    """Append a line to enforcement-violations.md (no atomic rename — append-only)."""
    os.makedirs(os.path.dirname(VIOLATIONS_FILE), exist_ok=True)
    with open(VIOLATIONS_FILE, "a") as f:
        f.write(line + "\n")


def _log_missed_observe(session_id, start_iso):
    """
    Append a MISSED_OBSERVE_PASS entry to violations file.
    Only called when idempotency check has already confirmed no prior release/prune record.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    _violations_append(
        f"- **MISSED_OBSERVE_PASS** | session_id: `{session_id}` | started: {start_iso} | detected: {now_iso}"
    )


def _session_id(entry):
    """Derive a stable session identifier from a registry entry (pid + started)."""
    return f"{entry.get('pid', 'unknown')}:{entry.get('started', 'unknown')}"


def _prune_dead_sessions(registry):
    live = []
    for s in registry.get("sessions", []):
        pid_alive = _is_pid_alive(s.get("pid", -1))
        if pid_alive:
            live.append(s)
        else:
            # Dead session — log to ledger if jarvis and not already logged
            started_str = s.get("started", "")
            if s.get("focus") == "jarvis":
                sid = _session_id(s)
                if not _ledger_has_release_for(sid):
                    seen = _observe_seen(started_str)
                    _ledger_append({
                        "event": "prune",
                        "session_id": sid,
                        "observe_seen": seen,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    if not seen:
                        _log_missed_observe(sid, started_str)
    registry["sessions"] = live
    return registry


def cmd_check():
    registry = _read_registry()
    registry = _prune_dead_sessions(registry)
    _write_registry(registry)

    my_pid = os.getpid()
    my_ppid = os.getppid()
    others = [s for s in registry["sessions"] if s.get("pid") not in (my_pid, my_ppid)]

    if not others:
        print("OK: No concurrent sessions detected.")
        return 0
    else:
        print("WARNING: Concurrent session(s) detected:")
        for s in others:
            print(f"  PID {s.get('pid')} | focus: {s.get('focus','?')} | started: {s.get('started','?')} | last_file: {s.get('last_file','?')}")
        print()
        print("ACTION: Write to Meta/sync/conflict-queue.md instead of the shared file.")
        print("        Or confirm with user before proceeding.")
        return 1


def cmd_register(focus):
    registry = _read_registry()
    registry = _prune_dead_sessions(registry)

    my_pid = os.getppid()  # parent PID = the Claude Code process
    now_iso = datetime.now(timezone.utc).isoformat()
    # Store session_id when available (additive; backward-compatible with readers using .get())
    claude_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    entry = {
        "pid": my_pid,
        "started": now_iso,
        "focus": focus,
        "last_file": "",
    }
    if claude_session_id:
        entry["session_id"] = claude_session_id
    existing_pids = {s["pid"] for s in registry["sessions"]}
    if my_pid not in existing_pids:
        registry["sessions"].append(entry)
        _write_registry(registry)
        print(f"Registered session: PID {my_pid}, focus: {focus}")
        # Append ledger registration record for jarvis sessions
        if focus == "jarvis":
            sid = _session_id(entry)
            _ledger_append({
                "event": "register",
                "session_id": sid,
                "start_iso": now_iso,
                "ts": now_iso,
            })
    else:
        print(f"Session PID {my_pid} already registered.")
    return 0


def cmd_release():
    registry = _read_registry()
    my_pid = os.getppid()

    # Find the entry before removing it so we can log observe status
    released_entry = None
    for s in registry["sessions"]:
        if s.get("pid") == my_pid:
            released_entry = s
            break

    before = len(registry["sessions"])
    registry["sessions"] = [s for s in registry["sessions"] if s.get("pid") != my_pid]
    after = len(registry["sessions"])
    _write_registry(registry)

    if before != after:
        print(f"Released session PID {my_pid}.")
        # Log observe status for jarvis sessions
        if released_entry and released_entry.get("focus") == "jarvis":
            sid = _session_id(released_entry)
            if not _ledger_has_release_for(sid):
                started_str = released_entry.get("started", "")
                seen = _observe_seen(started_str)
                _ledger_append({
                    "event": "release",
                    "session_id": sid,
                    "observe_seen": seen,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                if not seen:
                    _log_missed_observe(sid, started_str)
    else:
        print(f"No session found for PID {my_pid} (already released or never registered).")
    return 0


def cmd_safe_write(target_file, tmp_file):
    """Rename tmp_file → target_file atomically, after checking for conflicts."""
    result = cmd_check()
    if result != 0:
        print(f"Aborting safe-write to {target_file} due to concurrent session.")
        print(f"Your content is preserved at: {tmp_file}")
        return 1
    if not os.path.exists(tmp_file):
        print(f"Error: tmp file not found: {tmp_file}")
        return 2
    os.rename(tmp_file, target_file)

    # Update last_file in registry for this session
    registry = _read_registry()
    my_pid = os.getppid()
    for s in registry["sessions"]:
        if s.get("pid") == my_pid:
            s["last_file"] = target_file
    _write_registry(registry)

    print(f"Safe-write complete: {target_file}")
    return 0


def cmd_status():
    registry = _read_registry()
    registry = _prune_dead_sessions(registry)
    _write_registry(registry)

    sessions = registry.get("sessions", [])
    if not sessions:
        print("No active sessions in registry.")
    else:
        print(f"Active sessions ({len(sessions)}):")
        for s in sessions:
            print(f"  PID {s.get('pid')} | focus: {s.get('focus','?')} | started: {s.get('started','?')} | last_file: {s.get('last_file','?')}")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    if cmd == "check":
        return cmd_check()
    elif cmd == "register":
        focus = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        return cmd_register(focus)
    elif cmd == "release":
        return cmd_release()
    elif cmd == "safe-write":
        if len(sys.argv) < 4:
            print("Usage: session-guard.py safe-write TARGET_FILE TMP_FILE")
            return 1
        return cmd_safe_write(sys.argv[2], sys.argv[3])
    elif cmd == "status":
        return cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
