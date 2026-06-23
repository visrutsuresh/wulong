#!/usr/bin/env python3
"""
judge-append-notebook.py — the ONLY sanctioned write path for the Judge's notebook.

The Judge is a sideline observer (like Hermes/Metis). It can run Bash, and Bash
runs THIS script to append scored observations to its notebook and update its cursor.
This script validates input + target path — it rejects any other write attempt.

Usage (called by Judge via Bash):
    python3 judge-append-notebook.py append-observation \\
        --change-id <change_id> \\
        --trigger <event-name> \\
        --rule-following-score <float 0.0-1.0 or "INSUFFICIENT_DATA"> \\
        --rule-following-band <CLEAN|MINOR_DRIFT|SIGNIFICANT_DRIFT|POOR|INSUFFICIENT_DATA> \\
        --comprehensiveness-rollup <float 0.0-1.0 or "null"> \\
        --evidence "<path1>,<path2>"

    python3 judge-append-notebook.py reinforce-observation \\
        --pattern-id <slug-matching-existing-obs> \\
        [--evidence "<path1>,<path2>"] \\
        [--note "<short note up to 200 chars>"]

    python3 judge-append-notebook.py update-status \\
        --pattern-id <slug> \\
        --new-status "<status string>"

    python3 judge-append-notebook.py update-cursor \\
        --last-event-id <event-id>

    python3 judge-append-notebook.py digest-overflow

Exit codes:
  0 = success
  1 = validation failure
  2 = filesystem error
"""
from __future__ import annotations
import argparse
import fcntl
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
NOTEBOOK = VAULT / "Meta" / "judge" / "notebook.md"
NOTEBOOK_ARCHIVE = VAULT / "Meta" / "judge" / "notebook.archive.md"
CONFIG = VAULT / "Meta" / "judge" / "config.json"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"

ALLOWED_TARGETS = (NOTEBOOK, NOTEBOOK_ARCHIVE)

VALID_BANDS = {"CLEAN", "MINOR_DRIFT", "SIGNIFICANT_DRIFT", "POOR", "INSUFFICIENT_DATA"}


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


def _recount_observations(text: str) -> int:
    return len(re.findall(r"^## Observation ", text, re.MULTILINE))


def _rewrite_observation_count(count: int) -> None:
    text = NOTEBOOK.read_text(encoding="utf-8")
    text = re.sub(
        r"^observation_count:\s*\d+",
        f"observation_count: {count}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    NOTEBOOK.write_text(text, encoding="utf-8")


def _validate_score(score_str: str) -> bool:
    if score_str == "INSUFFICIENT_DATA":
        return True
    try:
        v = float(score_str)
        return 0.0 <= v <= 1.0
    except ValueError:
        return False


def append_observation(
    change_id: str,
    trigger: str,
    rule_following_score: str,
    rule_following_band: str,
    comprehensiveness_rollup: str,
    evidence: list[str],
) -> int:
    if not change_id or len(change_id) > 120:
        sys.stderr.write("[judge-notebook] change_id must be 1-120 chars\n")
        return 1
    if not _validate_score(rule_following_score):
        sys.stderr.write(f"[judge-notebook] invalid rule_following_score: {rule_following_score!r} (must be 0.0-1.0 or INSUFFICIENT_DATA)\n")
        return 1
    if rule_following_band not in VALID_BANDS:
        sys.stderr.write(f"[judge-notebook] invalid band: {rule_following_band!r} (must be one of {sorted(VALID_BANDS)})\n")
        return 1
    # comprehensiveness_rollup: float string 0-1, or "null"
    if comprehensiveness_rollup != "null":
        try:
            v = float(comprehensiveness_rollup)
            if not (0.0 <= v <= 1.0):
                raise ValueError
        except ValueError:
            sys.stderr.write(f"[judge-notebook] invalid comprehensiveness_rollup: {comprehensiveness_rollup!r} (must be 0.0-1.0 or 'null')\n")
            return 1

    pattern_id = slugify(f"{change_id}-judge-observation")
    block = (
        f"\n## Observation {now_str()} — trigger: {trigger}\n"
        f"Change id: {change_id}\n"
        f"Pattern id: {pattern_id}\n"
        f"Rule following score: {rule_following_score}\n"
        f"Rule following band: {rule_following_band}\n"
        f"Comprehensiveness rollup: {comprehensiveness_rollup}\n"
        f"Evidence cited: {evidence!r}\n"
        f"Times observed: 1\n"
        f"First observed: {now_str()}\n"
        f"Last observed: {now_str()}\n"
        f"Status: observation accepted\n"
    )
    with open(NOTEBOOK, "a", encoding="utf-8") as f:
        f.write(block)
    text_after = NOTEBOOK.read_text(encoding="utf-8")
    _rewrite_observation_count(_recount_observations(text_after))
    log_event(f"append-observation: {pattern_id} (change_id={change_id}, score={rule_following_score}, band={rule_following_band})")
    return 0


def reinforce_observation(
    pattern_id: str,
    evidence: list[str] | None = None,
    note: str = "",
) -> int:
    text = NOTEBOOK.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Times observed:\s*)(\d+)(.*?Last observed:\s*)([^\n]+)",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        sys.stderr.write(f"[judge-notebook] pattern_id not found: {pattern_id}\n")
        return 1
    new_count = int(m.group(2)) + 1
    new_last = now_str()
    new_text = pat.sub(lambda mm: f"{mm.group(1)}{new_count}{mm.group(3)}{new_last}", text)

    if evidence:
        ev_pat = re.compile(
            rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Evidence cited:\s*)([^\n]+)",
            re.DOTALL,
        )
        ev_m = ev_pat.search(new_text)
        if ev_m:
            try:
                existing = eval(ev_m.group(2).strip())  # noqa: S307
                if not isinstance(existing, list):
                    existing = [ev_m.group(2).strip()]
            except Exception:
                existing = [ev_m.group(2).strip()]
            merged = existing + [e for e in evidence if e not in existing]
            new_text = ev_pat.sub(lambda mm: f"{mm.group(1)}{merged!r}", new_text)

    if note:
        note = note[:200]
        note_line = f"Reinforcement note ({new_last}): {note}"
        lo_pat = re.compile(
            rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Last observed:\s*[^\n]+)(\n)",
            re.DOTALL,
        )
        new_text = lo_pat.sub(lambda mm: f"{mm.group(1)}{mm.group(2)}{note_line}\n", new_text, count=1)

    NOTEBOOK.write_text(new_text, encoding="utf-8")
    log_event(f"reinforce-observation: {pattern_id} → count={new_count}")
    return 0


def update_status(pattern_id: str, new_status: str) -> int:
    text = NOTEBOOK.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Status:\s*)([^\n]+)",
        re.DOTALL,
    )
    if not pat.search(text):
        sys.stderr.write(f"[judge-notebook] pattern_id not found: {pattern_id}\n")
        return 1
    new_text = pat.sub(lambda mm: f"{mm.group(1)}{new_status}", text)
    NOTEBOOK.write_text(new_text, encoding="utf-8")
    log_event(f"update-status: {pattern_id} → {new_status}")
    return 0


def update_cursor(last_event_id: str) -> int:
    """Update notebook frontmatter last_event_id + last_invocation.

    Also mirrors cursor into config.json for easy machine reading.
    """
    if not last_event_id or len(last_event_id) > 200:
        sys.stderr.write("[judge-notebook] invalid last_event_id\n")
        return 1
    now = now_str()
    # Update notebook frontmatter
    text = NOTEBOOK.read_text(encoding="utf-8")
    text = re.sub(
        r"^last_event_id:\s*\S+",
        f"last_event_id: {last_event_id}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^last_invocation:\s*\S+",
        f"last_invocation: {now}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    # Handle null case (initial notebook has "null" as the value)
    text = re.sub(
        r"^last_event_id: null",
        f"last_event_id: {last_event_id}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^last_invocation: null",
        f"last_invocation: {now}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    NOTEBOOK.write_text(text, encoding="utf-8")

    # Mirror cursor fields into config.json
    if CONFIG.exists():
        import json
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            if "cursor" in cfg:
                cfg["cursor"]["last_event_id"] = last_event_id
                cfg["cursor"]["last_invocation"] = now
                CONFIG.write_text(
                    json.dumps(cfg, indent=2) + "\n",
                    encoding="utf-8",
                )
        except Exception:
            pass  # config mirror is best-effort; notebook update already succeeded

    log_event(f"update-cursor: last_event_id={last_event_id}")
    return 0


def digest_overflow() -> int:
    """Archive oldest 50 observations when notebook exceeds 200 entries."""
    text = NOTEBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"(## Observation [\s\S]*?)(?=## Observation|\Z)", text)
    if len(blocks) <= 50:
        return 0
    to_archive = blocks[:50]
    NOTEBOOK_ARCHIVE.touch(exist_ok=True)
    with open(NOTEBOOK_ARCHIVE, "a", encoding="utf-8") as f:
        f.write(f"\n<!-- digest from {now_str()} — 50 oldest observations archived -->\n")
        for b in to_archive:
            f.write(b.rstrip() + "\n\n")
    header_match = re.search(
        r"^---[\s\S]*?---\s*<!--[\s\S]*?-->\s*## Observations\s*",
        text,
    )
    header = header_match.group(0) if header_match else text.split("## Observation")[0]
    remaining = "## Observation" + "".join(blocks[50:]).split("## Observation", 1)[-1]
    digest = (
        f"\n## Digest {now_str()} (50 archived → notebook.archive.md)\n"
        f"50 oldest observations folded into archive; review notebook.archive.md if needed.\n"
    )
    new_body = header + digest + remaining
    NOTEBOOK.write_text(new_body, encoding="utf-8")
    _rewrite_observation_count(_recount_observations(new_body))
    log_event("digest-overflow: archived 50")
    return 0


def log_event(msg: str) -> None:
    try:
        with open(CHANGE_LOG, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(f"[{now_str()}] judge → NOTEBOOK {msg}\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("append-observation")
    p1.add_argument("--change-id", required=True)
    p1.add_argument("--trigger", required=True)
    p1.add_argument("--rule-following-score", required=True)
    p1.add_argument("--rule-following-band", required=True)
    p1.add_argument("--comprehensiveness-rollup", default="null")
    p1.add_argument("--evidence", default="")

    p2 = sub.add_parser("reinforce-observation")
    p2.add_argument("--pattern-id", required=True)
    p2.add_argument("--evidence", default="")
    p2.add_argument("--note", default="")

    p3 = sub.add_parser("update-status")
    p3.add_argument("--pattern-id", required=True)
    p3.add_argument("--new-status", required=True)

    p4 = sub.add_parser("update-cursor")
    p4.add_argument("--last-event-id", required=True)

    sub.add_parser("digest-overflow")

    args = ap.parse_args()

    if args.cmd == "append-observation":
        evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
        return append_observation(
            args.change_id,
            args.trigger,
            args.rule_following_score,
            args.rule_following_band,
            args.comprehensiveness_rollup,
            evidence,
        )
    if args.cmd == "reinforce-observation":
        evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
        return reinforce_observation(
            args.pattern_id,
            evidence=evidence or None,
            note=args.note,
        )
    if args.cmd == "update-status":
        return update_status(args.pattern_id, args.new_status)
    if args.cmd == "update-cursor":
        return update_cursor(args.last_event_id)
    if args.cmd == "digest-overflow":
        return digest_overflow()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[judge-notebook] FATAL: {e}\n")
        sys.exit(2)
