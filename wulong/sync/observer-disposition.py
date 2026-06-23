#!/usr/bin/env python3
"""
observer-disposition.py — HUMAN-IN-THE-LOOP dispositioner for observer proposals
and Judge-warn false-positive adjudication.

Closes the observe→propose→disposition open loop (observer-audit-2026-06-10 #1).
The ledger at Meta/observer-proposals/ledger.jsonl is the SINGLE SOURCE OF TRUTH;
on recording a verdict this script ALSO moves the proposal file out of queued/
so folder state never diverges from the ledger.

This script does NOT auto-accept anything and grants no observer teeth — it only
RECORDS a verdict already decided by a human/contrarian. Judge may never
adjudicate its own warns (anti-sycophancy lock, judge.md); observers may never
adjudicate their own proposals.

Subcommands:
  list
      Show queued hermes/metis proposals + un-adjudicated judge warns
      (notebook entries scored below the warn floor with no judge_warn ledger row).

  record --observer hermes|metis --proposal <filename-in-queued>
         --verdict accepted|rejected|deferred --by <adjudicator>
         --rationale "..." [--observation-ref <pattern_id>] [--dry-run]
      Append a proposal-disposition row and move the file:
        accepted → hermes: archive/   metis: approved/
        rejected → rejected/ (both)
        deferred → deferred/ (both, created on demand)

  adjudicate-warn --change-id <id> --false-positive true|false
                  --by <adjudicator> --rationale "..." [--dry-run]
      Append a judge_warn adjudication row {kind, change_id, false_positive,
      adjudicated_by, rationale, timestamp}. Feeds the pre-registered <=15%
      FP flip criterion read by judge-flip-readiness.py.

Exit codes: 0 = success, 1 = validation failure, 2 = filesystem error.
Pure Python / stdlib only. No LLM, no network, no spend.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent.parent
LEDGER = VAULT / "Meta" / "observer-proposals" / "ledger.jsonl"
HERMES_BASE = VAULT / "Meta" / "hermes-proposals"
METIS_BASE = VAULT / "Meta" / "metis-proposals"
JUDGE_NOTEBOOK = VAULT / "Meta" / "judge" / "notebook.md"
JUDGE_CONFIG = VAULT / "Meta" / "judge" / "config.json"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"

# adjudicated_by must never be an observer adjudicating itself
FORBIDDEN_ADJUDICATORS = {"judge", "hermes", "metis"}

# Verdict → destination folder name, per existing observer vocabulary
DEST_MAP = {
    "hermes": {"accepted": "archive", "rejected": "rejected", "deferred": "deferred"},
    "metis": {"accepted": "approved", "rejected": "rejected", "deferred": "deferred"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_log() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_ledger() -> list[dict]:
    """Read ledger rows. Empty/missing ledger → []. Malformed lines are skipped with a warning."""
    if not LEDGER.exists():
        return []
    rows: list[dict] = []
    for i, line in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            sys.stderr.write(f"[observer-disposition] WARN: ledger line {i} unparseable, skipped\n")
    return rows


def append_ledger(row: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would append to ledger: {json.dumps(row)}")
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _log_change(msg: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would append change-log: {msg}")
        return
    try:
        import fcntl
        with open(CHANGE_LOG, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(f"[{_now_log()}] observer-disposition → {msg}\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass  # best-effort; ledger write already succeeded


def _queued_files(base: Path) -> list[Path]:
    q = base / "queued"
    if not q.exists():
        return []
    return sorted(
        p for p in q.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name.lower() != "readme.md"
    )


def _warn_floor() -> float:
    try:
        cfg = json.loads(JUDGE_CONFIG.read_text(encoding="utf-8"))
        return float(cfg.get("thresholds", {}).get("warn_score_floor", 0.65))
    except Exception:  # noqa: BLE001
        return 0.65


def _judge_warns() -> list[dict]:
    """Parse judge notebook for scored entries below the warn floor.

    Accepts BOTH 'Rule following score:' (real written format) and the
    underscore form, same defensive dual-format as judge-flip-readiness.py.
    """
    if not JUDGE_NOTEBOOK.exists():
        return []
    text = JUDGE_NOTEBOOK.read_text(encoding="utf-8", errors="replace")
    floor = _warn_floor()
    score_re = re.compile(r"(?:Rule following score|rule_following_score):\s*(.+)", re.IGNORECASE)
    band_re = re.compile(r"(?:Rule following band|rule_following_band):\s*(.+)", re.IGNORECASE)
    cid_re = re.compile(r"(?:Change id|change_id):\s*(.+)", re.IGNORECASE)

    warns: list[dict] = []
    seen: set[str] = set()
    for block in re.split(r"^## Observation", text, flags=re.MULTILINE)[1:]:
        sm = score_re.search(block)
        cm = cid_re.search(block)
        if not sm or not cm:
            continue
        raw = sm.group(1).strip().strip("\"'")
        try:
            score = float(raw)
        except ValueError:
            continue  # INSUFFICIENT_DATA etc.
        if score >= floor:
            continue
        change_id = cm.group(1).strip()
        if change_id in seen:
            continue
        seen.add(change_id)
        bm = band_re.search(block)
        warns.append({
            "change_id": change_id,
            "score": score,
            "band": bm.group(1).strip() if bm else "?",
        })
    return warns


def _adjudicated_change_ids(rows: list[dict]) -> set[str]:
    return {
        r.get("change_id", "")
        for r in rows
        if r.get("kind") == "judge_warn" and isinstance(r.get("false_positive"), bool)
    }


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(_args: argparse.Namespace) -> int:
    rows = load_ledger()

    print("# Queued proposals awaiting disposition\n")
    any_queued = False
    for observer, base in (("hermes", HERMES_BASE), ("metis", METIS_BASE)):
        files = _queued_files(base)
        print(f"## {observer} ({len(files)} queued)")
        for p in files:
            print(f"  - {p.name}")
        if files:
            any_queued = True
        print()
    if not any_queued:
        print("(no queued proposals — observers have not written any, or all are dispositioned)\n")

    adjudicated = _adjudicated_change_ids(rows)
    warns = _judge_warns()
    pending = [w for w in warns if w["change_id"] not in adjudicated]
    print(f"# Judge warns (score < {_warn_floor()}) — {len(pending)} un-adjudicated of {len(warns)} total")
    for w in pending:
        print(f"  - {w['change_id']}  score={w['score']:.2f}  band={w['band']}")
    if warns and not pending:
        print("  (all adjudicated)")
    print(f"\nLedger rows: {len(rows)} ({LEDGER.relative_to(VAULT)})")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    by = args.by.strip()
    if by.lower() in FORBIDDEN_ADJUDICATORS:
        sys.stderr.write(
            f"[observer-disposition] REJECT: adjudicated_by={by!r} — observers may not "
            "adjudicate proposals (anti-sycophancy lock). Use CEO/contrarian/jarvis.\n"
        )
        return 1

    base = HERMES_BASE if args.observer == "hermes" else METIS_BASE
    src = base / "queued" / args.proposal
    if not src.exists():
        sys.stderr.write(
            f"[observer-disposition] REJECT: proposal not found in queued/: {src}\n"
        )
        return 1

    dest_dir = base / DEST_MAP[args.observer][args.verdict]
    dest = dest_dir / args.proposal

    row = {
        "kind": "proposal",
        "observer": args.observer,
        "proposal_id": args.proposal,
        "observation_ref": args.observation_ref or None,
        "status": args.verdict,
        "verdict_rationale": args.rationale,
        "adjudicated_by": by,
        "timestamp": _now_iso(),
    }

    append_ledger(row, args.dry_run)
    if args.dry_run:
        print(f"[dry-run] would move {src.relative_to(VAULT)} → {dest.relative_to(VAULT)}")
    else:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        except OSError as e:
            sys.stderr.write(
                f"[observer-disposition] ERROR: ledger row written but file move FAILED: {e}\n"
                "Folder state diverges from ledger — move the file manually and verify.\n"
            )
            return 2
        print(f"[observer-disposition] {args.observer}/{args.proposal} → {args.verdict} "
              f"(moved to {dest.relative_to(VAULT)})")
    _log_change(
        f"PROPOSAL-VERDICT {args.observer}/{args.proposal} status={args.verdict} "
        f"by={by} — recorded in observer-proposals/ledger.jsonl",
        args.dry_run,
    )
    return 0


def cmd_adjudicate_warn(args: argparse.Namespace) -> int:
    by = args.by.strip()
    if by.lower() in FORBIDDEN_ADJUDICATORS:
        sys.stderr.write(
            f"[observer-disposition] REJECT: adjudicated_by={by!r} — Judge may never "
            "adjudicate its own warns (anti-sycophancy lock, judge.md). Use CEO/contrarian.\n"
        )
        return 1

    fp_raw = args.false_positive.strip().lower()
    if fp_raw not in ("true", "false"):
        sys.stderr.write("[observer-disposition] REJECT: --false-positive must be true|false\n")
        return 1

    row = {
        "kind": "judge_warn",
        "change_id": args.change_id.strip(),
        "false_positive": fp_raw == "true",
        "adjudicated_by": by,
        "rationale": args.rationale,
        "timestamp": _now_iso(),
    }
    append_ledger(row, args.dry_run)
    if not args.dry_run:
        print(f"[observer-disposition] judge_warn {args.change_id} → false_positive={fp_raw} (by {by})")
    _log_change(
        f"JUDGE-WARN-ADJUDICATION change_id={args.change_id} false_positive={fp_raw} by={by}",
        args.dry_run,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List queued proposals + un-adjudicated judge warns")

    rp = sub.add_parser("record", help="Record a proposal verdict (human-in-the-loop)")
    rp.add_argument("--observer", required=True, choices=["hermes", "metis"])
    rp.add_argument("--proposal", required=True, help="filename inside <observer>-proposals/queued/")
    rp.add_argument("--verdict", required=True, choices=["accepted", "rejected", "deferred"])
    rp.add_argument("--by", required=True, help="adjudicator (CEO, contrarian, jarvis — never an observer)")
    rp.add_argument("--rationale", required=True)
    rp.add_argument("--observation-ref", default="", help="originating notebook pattern_id (optional)")
    rp.add_argument("--dry-run", action="store_true")

    wp = sub.add_parser("adjudicate-warn", help="Record a judge-warn false-positive adjudication")
    wp.add_argument("--change-id", required=True)
    wp.add_argument("--false-positive", required=True, help="true|false")
    wp.add_argument("--by", required=True, help="adjudicator (CEO, contrarian — never judge)")
    wp.add_argument("--rationale", required=True)
    wp.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "record":
        return cmd_record(args)
    if args.cmd == "adjudicate-warn":
        return cmd_adjudicate_warn(args)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[observer-disposition] FATAL: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(2)
