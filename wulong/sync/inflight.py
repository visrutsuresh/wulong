#!/usr/bin/env python3
"""
inflight.py — flock-safe in-flight ledger manager (v33-1-amnesia-fix).

Manages Meta/state/in-flight.md: the always-current per-change_id session
continuity ledger.  Reuses the same fcntl.LOCK_EX discipline as changelog-append.py.

CLI:
  set --change-id X --phase P [--next N] [--blocker B]
      Upsert an ACTIVE WORK row (replace existing for X, else append).
  decide "<text>" [--rationale R] [--change-id X]
      Append a DECISIONS entry with UTC ts.
  done --change-id X
      Remove X's ACTIVE WORK row (DECISIONS history stays).
  show
      Print the current ledger to stdout.
  --audit [--days N] [--stale-days S]
      Read-only drift report: forgot-to-log + stale-open rows.
  --demo
      Self-check: set/show/done roundtrip + concurrent-set no-loss.

ponytail: stdlib only (fcntl, argparse, pathlib, subprocess). No new deps.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(os.path.abspath(__file__)).parent
_VAULT = _HERE.parent.parent
LEDGER = _VAULT / "Meta" / "state" / "in-flight.md"
RECEIPTS_DIR = _VAULT / "Meta" / "receipts"
TRACE_SCRIPT = _HERE / "trace-change-chain.py"

# ---------------------------------------------------------------------------
# Section markers (must match Artifact 1 header text exactly)
# ---------------------------------------------------------------------------

_HDR_ACTIVE = "## ACTIVE WORK"
_HDR_DECISIONS = "## DECISIONS"
_HDR_OPEN = "## OPEN QUESTIONS"

_TABLE_HEADER = "| change_id | phase | next-action | blocker | updated |"
_TABLE_SEP    = "|-----------|-------|-------------|---------|---------|"

# ---------------------------------------------------------------------------
# Low-level flock helpers
# ---------------------------------------------------------------------------

def _flock_read(path: Path) -> str:
    """Read *path* under a shared lock. Returns '' if file absent."""
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return f.read()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _flock_write(path: Path, content: str) -> None:
    """Write *content* to *path* under an exclusive lock (open for write, truncate)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(content)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Ledger parsing
# ---------------------------------------------------------------------------

def _split_sections(text: str) -> tuple[str, list[str], str, list[str], str, list[str]]:
    """Split ledger into (pre_active, active_lines, pre_decisions, decision_lines,
    pre_open, open_lines).  Tolerates missing sections."""
    lines = text.splitlines(keepends=True)

    def _find(marker: str) -> int:
        for i, l in enumerate(lines):
            if l.strip() == marker:
                return i
        return -1

    ia = _find(_HDR_ACTIVE)
    id_ = _find(_HDR_DECISIONS)
    io = _find(_HDR_OPEN)

    def _section_lines(start_idx: int, end_idx: int) -> list[str]:
        if start_idx < 0:
            return []
        end = end_idx if end_idx > start_idx else len(lines)
        return lines[start_idx + 1 : end]

    active_lines    = _section_lines(ia, min(x for x in [id_, io, len(lines)] if x > ia) if ia >= 0 else -1)
    decision_lines  = _section_lines(id_, io if io > id_ else len(lines)) if id_ >= 0 else []
    open_lines      = _section_lines(io, len(lines)) if io >= 0 else []

    return lines, ia, id_, io, active_lines, decision_lines, open_lines


def _parse_active_rows(active_lines: list[str]) -> dict[str, dict]:
    """Return {change_id: {phase, next, blocker, updated}} from table rows."""
    rows: dict[str, dict] = {}
    for line in active_lines:
        s = line.strip()
        if not s.startswith("|") or s.startswith("|---") or s == _TABLE_HEADER.strip():
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 5:
            continue
        cid = cols[0]
        if cid and cid != "change_id":
            rows[cid] = {
                "phase":   cols[1],
                "next":    cols[2],
                "blocker": cols[3],
                "updated": cols[4],
            }
    return rows


def _render_active_block(rows: dict[str, dict]) -> list[str]:
    """Render the ACTIVE WORK section lines (no trailing blank line)."""
    out = [_TABLE_HEADER + "\n", _TABLE_SEP + "\n"]
    for cid, r in rows.items():
        out.append(f"| {cid} | {r['phase']} | {r['next']} | {r['blocker']} | {r['updated']} |\n")
    return out


# ---------------------------------------------------------------------------
# Public API (each fn reads then writes atomically via flock)
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_ledger() -> str:
    return _flock_read(LEDGER)


def cmd_set(change_id: str, phase: str, next_action: str = "", blocker: str = "") -> None:
    """Upsert an ACTIVE WORK row for *change_id*."""
    # Full read-modify-write under exclusive lock
    path = LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            text = f.read()
            updated = _now_utc()
            text = _upsert_active_row(text, change_id, phase, next_action, blocker, updated)
            f.seek(0)
            f.truncate()
            f.write(text)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _upsert_active_row(text: str, change_id: str, phase: str,
                        next_action: str, blocker: str, updated: str) -> str:
    """Pure function: return new ledger text with the row upserted."""
    if not text.strip():
        # Bootstrap a blank ledger
        text = (
            "<!-- machine-updated via Meta/sync/inflight.py — do NOT hand-edit -->\n\n"
            f"{_HDR_ACTIVE}\n\n"
            f"{_TABLE_HEADER}\n{_TABLE_SEP}\n\n"
            f"{_HDR_DECISIONS}\n\n"
            f"{_HDR_OPEN}\n\n"
        )

    lines = text.splitlines(keepends=True)

    # Find ACTIVE WORK table boundaries
    active_start = next((i for i, l in enumerate(lines) if l.strip() == _HDR_ACTIVE), None)
    if active_start is None:
        # Append section at end
        text += f"\n{_HDR_ACTIVE}\n\n{_TABLE_HEADER}\n{_TABLE_SEP}\n\n"
        lines = text.splitlines(keepends=True)
        active_start = next(i for i, l in enumerate(lines) if l.strip() == _HDR_ACTIVE)

    # Find the next section after ACTIVE WORK
    next_section = next(
        (i for i, l in enumerate(lines)
         if i > active_start and l.startswith("## ")),
        len(lines),
    )

    active_block = lines[active_start + 1 : next_section]
    _, decision_lines, _, _, _, _, _ = (None, None, None, None, None, None, None)

    # Parse existing rows
    rows: dict[str, list[int]] = {}  # change_id -> [line_idx] within active_block
    for idx, line in enumerate(active_block):
        s = line.strip()
        if not s.startswith("|") or s.startswith("|---") or s == _TABLE_HEADER.strip():
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if cols and cols[0] and cols[0] != "change_id":
            rows[cols[0]] = idx

    new_row_line = f"| {change_id} | {phase} | {next_action} | {blocker} | {updated} |\n"

    if change_id in rows:
        active_block[rows[change_id]] = new_row_line
    else:
        # Append after separator line
        sep_idx = next((i for i, l in enumerate(active_block) if l.strip().startswith("|---")), None)
        if sep_idx is not None:
            active_block.insert(sep_idx + 1, new_row_line)
        else:
            active_block += [_TABLE_HEADER + "\n", _TABLE_SEP + "\n", new_row_line]

    result = (
        lines[:active_start + 1]
        + active_block
        + lines[next_section:]
    )
    return "".join(result)


def cmd_done(change_id: str) -> None:
    """Remove *change_id* row from ACTIVE WORK. DECISIONS history stays."""
    path = LEDGER
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            text = f.read()
            text = _remove_active_row(text, change_id)
            f.seek(0)
            f.truncate()
            f.write(text)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _remove_active_row(text: str, change_id: str) -> str:
    """Pure function: return new ledger text with *change_id* row removed."""
    lines = text.splitlines(keepends=True)
    new_lines = []
    for line in lines:
        s = line.strip()
        if s.startswith("|") and not s.startswith("|---") and s != _TABLE_HEADER.strip():
            cols = [c.strip() for c in s.strip("|").split("|")]
            if cols and cols[0] == change_id:
                continue  # drop this row
        new_lines.append(line)
    return "".join(new_lines)


def cmd_decide(text: str, rationale: str = "", change_id: str = "") -> None:
    """Append a DECISIONS entry."""
    ts = _now_utc()
    parts = [f"- **{ts}**"]
    if change_id:
        parts.append(f"| change_id: {change_id}")
    parts.append(f"| {text}")
    if rationale:
        parts.append(f"Rationale: {rationale}")
    entry = " ".join(parts) + "\n"

    path = LEDGER
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            content = f.read()
            content = _append_decision(content, entry)
            f.seek(0)
            f.truncate()
            f.write(content)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _append_decision(text: str, entry: str) -> str:
    """Append *entry* right after the DECISIONS header line."""
    lines = text.splitlines(keepends=True)
    dec_idx = next((i for i, l in enumerate(lines) if l.strip() == _HDR_DECISIONS), None)
    if dec_idx is None:
        return text + f"\n{_HDR_DECISIONS}\n\n" + entry
    # Insert after the blank line(s) following the header, before next section
    insert_at = dec_idx + 1
    # Skip blank lines right after header
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    lines.insert(insert_at, entry)
    return "".join(lines)


def cmd_show() -> None:
    """Print the current ledger to stdout."""
    print(_load_ledger() or "(ledger is empty or absent)")


# ---------------------------------------------------------------------------
# --audit: read-only drift report
# ---------------------------------------------------------------------------

def cmd_audit(days: int = 7, stale_days: int = 3) -> None:
    """Print forgot-to-log and stale-open drift classes. Never writes."""
    text = _load_ledger()
    lines = text.splitlines(keepends=True)

    # Parse ACTIVE WORK rows
    active_start = next((i for i, l in enumerate(lines) if l.strip() == _HDR_ACTIVE), -1)
    next_section = next(
        (i for i, l in enumerate(lines) if i > active_start and l.startswith("## ")),
        len(lines),
    ) if active_start >= 0 else len(lines)
    active_block = lines[active_start + 1 : next_section] if active_start >= 0 else []

    active_rows: dict[str, str] = {}  # change_id -> updated ts
    for line in active_block:
        s = line.strip()
        if not s.startswith("|") or s.startswith("|---") or s == _TABLE_HEADER.strip():
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if cols and cols[0] and cols[0] != "change_id":
            active_rows[cols[0]] = cols[4] if len(cols) > 4 else ""

    # Parse DECISIONS for known change_ids (ever recorded = not forgotten)
    dec_start = next((i for i, l in enumerate(lines) if l.strip() == _HDR_DECISIONS), -1)
    decision_text = "".join(lines[dec_start:]) if dec_start >= 0 else ""
    known_via_decide = set(re.findall(r"change_id:\s*(\S+)", decision_text))
    # Also: any change_id ever in ACTIVE WORK that was `done` leaves no row but may be in DECISIONS
    all_ledger_known = set(active_rows.keys()) | known_via_decide

    # Scan receipts for change_ids dated within last N days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    receipt_ids: set[str] = set()
    if RECEIPTS_DIR.exists():
        for rf in RECEIPTS_DIR.iterdir():
            if not rf.suffix == ".md":
                continue
            # Extract date from filename (YYYY-MM-DD)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", rf.name)
            if not m:
                continue
            try:
                fdate = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if fdate < cutoff:
                continue
            try:
                content = rf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cid_m = re.search(r"^change_id\s*:\s*(\S+)", content, re.MULTILINE)
            if cid_m:
                receipt_ids.add(cid_m.group(1))

    forgot = receipt_ids - all_ledger_known

    # Stale-open: active rows with updated ts older than stale_days
    stale_threshold = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale = []
    for cid, updated in active_rows.items():
        try:
            ts = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if ts < stale_threshold:
                stale.append((cid, updated))
        except ValueError:
            stale.append((cid, updated + " (unparseable)"))

    print(f"=== inflight --audit (last {days}d, stale>{stale_days}d) ===\n")

    print(f"[1] forgot-to-log ({len(forgot)} found):")
    if forgot:
        for cid in sorted(forgot):
            print(f"  {cid}")
            # Shell out to trace-change-chain for chain summary
            try:
                result = subprocess.run(
                    [sys.executable, str(TRACE_SCRIPT), "--change-id", cid],
                    capture_output=True, text=True, timeout=15,
                )
                chain = (result.stdout or result.stderr or "").strip()
                if chain:
                    for cl in chain.splitlines()[:6]:
                        print(f"    {cl}")
            except Exception as exc:
                print(f"    (trace-change-chain unavailable: {exc})")
    else:
        print("  none")

    print(f"\n[2] stale-open ({len(stale)} found):")
    if stale:
        for cid, updated in stale:
            print(f"  {cid}  (last updated {updated})")
    else:
        print("  none")

    print("\n(audit is read-only — no ledger writes)")


# ---------------------------------------------------------------------------
# --demo: self-check
# ---------------------------------------------------------------------------

def _demo() -> None:
    import tempfile
    import threading

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    tmp_path.unlink()  # let cmd_set create it

    # Monkey-patch LEDGER for this demo
    global LEDGER
    _orig = LEDGER
    LEDGER = tmp_path

    try:
        # 1. set
        cmd_set("demo-change-A", "testing", "next step", "none")
        text = _load_ledger()
        assert "demo-change-A" in text, "set: row not found"
        assert "testing" in text, "set: phase not found"

        # 2. upsert (update existing)
        cmd_set("demo-change-A", "updated-phase", "next-2", "")
        text = _load_ledger()
        assert "updated-phase" in text, "upsert: phase not updated"

        # 3. decide
        cmd_decide("test decision", rationale="for demo", change_id="demo-change-A")
        text = _load_ledger()
        assert "test decision" in text, "decide: entry not found"

        # 4. show (just ensure no crash)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_show()
        assert "demo-change-A" in buf.getvalue(), "show: change_id not in output"

        # 5. done
        cmd_done("demo-change-A")
        text = _load_ledger()
        active_section = text.split(_HDR_DECISIONS)[0]
        assert "demo-change-A" not in active_section, "done: row still present in ACTIVE WORK"
        # Decision entry persists
        assert "test decision" in text, "done: decision entry was removed"

        # 6. Concurrent set: two threads set different change_ids — both must land
        errors: list[str] = []
        def _concurrent_set(n: int) -> None:
            try:
                cmd_set(f"concurrent-{n}", f"phase-{n}", "", "")
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=_concurrent_set, args=(1,))
        t2 = threading.Thread(target=_concurrent_set, args=(2,))
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not errors, f"concurrent set raised: {errors}"
        text = _load_ledger()
        assert "concurrent-1" in text, "concurrent: row 1 lost"
        assert "concurrent-2" in text, "concurrent: row 2 lost"

        print("demo PASS: set/upsert/decide/show/done roundtrip + concurrent no-loss")
    finally:
        LEDGER = _orig
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="inflight.py — in-flight ledger manager",
    )
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("set", help="Upsert an ACTIVE WORK row")
    s.add_argument("--change-id", required=True)
    s.add_argument("--phase", required=True)
    s.add_argument("--next", default="", dest="next_action")
    s.add_argument("--blocker", default="")

    d = sub.add_parser("decide", help="Append a DECISIONS entry")
    d.add_argument("text")
    d.add_argument("--rationale", default="")
    d.add_argument("--change-id", default="")

    dn = sub.add_parser("done", help="Remove an ACTIVE WORK row")
    dn.add_argument("--change-id", required=True)

    sub.add_parser("show", help="Print the current ledger")

    p.add_argument("--audit", action="store_true", help="Read-only drift report")
    p.add_argument("--days", type=int, default=7, help="Receipt look-back window (default 7)")
    p.add_argument("--stale-days", type=int, default=3, help="Stale-open threshold in days (default 3)")
    p.add_argument("--demo", action="store_true", help="Run self-check and exit")

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.demo:
        _demo()
        return 0

    if args.audit:
        cmd_audit(days=args.days, stale_days=args.stale_days)
        return 0

    if args.cmd == "set":
        cmd_set(args.change_id, args.phase, args.next_action, args.blocker)
    elif args.cmd == "decide":
        cmd_decide(args.text, args.rationale, args.change_id)
    elif args.cmd == "done":
        if not LEDGER.exists():
            print(f"error: ledger not found at {LEDGER}", file=sys.stderr)
            return 1
        cmd_done(args.change_id)
    elif args.cmd == "show":
        cmd_show()
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
