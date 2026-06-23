#!/usr/bin/env python3
"""
query-receipts.py — Read-only query interface for Meta/receipts/.

Filters receipts by date, agent, change type, file path, tag, trigger kind,
and status. All filters are optional and combinable (AND semantics between
different filter types; OR semantics for repeated --agent and --tag flags).

Usage examples:
  python3 Meta/sync/query-receipts.py --since 2026-05-25 --count
  python3 Meta/sync/query-receipts.py --agent jarvis --status DONE
  python3 Meta/sync/query-receipts.py --agent jarvis --agent coder --count
  python3 Meta/sync/query-receipts.py --file-path hermes.md
  python3 Meta/sync/query-receipts.py --tag cerebrum --full
  python3 Meta/sync/query-receipts.py --change-type feature --since 2026-05-29
  python3 Meta/sync/query-receipts.py --git-log --since 2026-05-29

Constraints:
  - Read-only: no writes, no deletions, no file modifications.
  - Stdlib only: no external dependencies.
  - Exit 0 even when 0 matches.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date, datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECEIPTS_DIR = os.path.join(VAULT, "Meta", "receipts")

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

CHANGE_TYPE_VALUES = {"feature", "fix", "governance", "docs", "housekeeping"}
TRIGGER_KIND_VALUES = {
    "user_request", "contrarian_fail", "scheduled", "observation_threshold",
    "upstream_handoff", "self_initiated", "system_event",
}
STATUS_VALUES = {"DONE", "FAIL", "BLOCKED", "PARTIAL"}

# Regex to detect "Files"-shaped section headings (case-insensitive).
# Matches: ## Files, ## Files written, ## Files changed, ## Files (Jarvis domain), etc.
FILES_HEADING_RE = re.compile(r"^## Files\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Frontmatter parser (minimal — only what we need for filtering)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter. Returns (fields, body).

    fields is empty dict if frontmatter absent or unparseable.
    body is the full content when frontmatter is absent.
    """
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    close_idx: Optional[int] = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break

    if close_idx is None:
        return {}, content

    fields: dict[str, str] = {}
    for line in lines[1:close_idx]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip().lower()] = val.strip().strip("\"'")

    body = "\n".join(lines[close_idx + 1:])
    return fields, body


def _parse_tags(raw: str) -> list[str]:
    """Parse a YAML inline list like '[a, b, c]' or a bare string into a list."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [t.strip().strip("\"'") for t in inner.split(",") if t.strip()]
    # bare string — treat as single tag
    return [raw] if raw else []


# ---------------------------------------------------------------------------
# Receipt loader
# ---------------------------------------------------------------------------

class Receipt:
    """Parsed receipt record."""

    __slots__ = (
        "path", "filename", "agent", "task", "date_str", "time_str",
        "status", "change_type", "tags", "trigger_kind", "body",
        "date_obj",
    )

    def __init__(
        self,
        path: str,
        filename: str,
        agent: str,
        task: str,
        date_str: str,
        time_str: str,
        status: str,
        change_type: str,
        tags: list[str],
        trigger_kind: str,
        body: str,
    ) -> None:
        self.path = path
        self.filename = filename
        self.agent = agent
        self.task = task
        self.date_str = date_str
        self.time_str = time_str
        self.status = status
        self.change_type = change_type
        self.tags = tags
        self.trigger_kind = trigger_kind
        self.body = body
        self.date_obj: Optional[date] = None
        try:
            self.date_obj = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            pass


def _load_receipt(filepath: str) -> Optional[Receipt]:
    """Load and parse one receipt file. Returns None on read error."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None

    fields, body = _parse_frontmatter(content)

    # Resolve legacy aliases for agent/task/date/time
    agent = fields.get("agent", "")
    task = fields.get("task") or fields.get("task-id") or fields.get("task_id", "")
    date_str = fields.get("date", "")
    time_str = fields.get("time", "")

    # timestamp alias: '2026-05-18 06:00' or '2026-05-18-0600'
    if (not date_str or not time_str) and fields.get("timestamp"):
        ts = fields["timestamp"]
        parts = re.split(r"[T\s]", ts)
        if len(parts) >= 2:
            date_str = date_str or parts[0]
            time_str = time_str or parts[1].replace(":", "")

    # created alias
    if (not date_str or not time_str) and fields.get("created"):
        created = fields["created"]
        parts = re.split(r"[T\s]", created)
        if len(parts) >= 2:
            date_str = date_str or parts[0]
            time_str = time_str or parts[1].replace(":", "")
        elif re.match(r"\d{4}-\d{2}-\d{2}-\d{4}", created):
            segs = created.split("-")
            date_str = date_str or "-".join(segs[:3])
            time_str = time_str or segs[3]

    # Fallback: extract date from filename
    if not date_str:
        fname = os.path.basename(filepath)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if m:
            date_str = m.group(1)

    # Fallback: extract agent from filename
    if not agent:
        fname = os.path.basename(filepath)
        m = re.match(r"^([a-z][a-z0-9_-]+)-\d{4}-\d{2}-\d{2}", fname)
        if m:
            agent = m.group(1)

    # Normalise status
    raw_status = fields.get("status", "")
    _STATUS_NORM = {
        "complete": "DONE", "Complete": "DONE", "COMPLETE": "DONE",
        "dispatched": "DONE", "DISPATCHED": "DONE",
        "SMOKE_FAIL": "FAIL",
        "CONTRARIAN GATE OPEN": "PARTIAL",
    }
    status = _STATUS_NORM.get(raw_status, raw_status).upper()

    change_type = fields.get("change_type", "")
    tags = _parse_tags(fields.get("tags", ""))
    trigger_kind = fields.get("trigger_kind", "")

    return Receipt(
        path=filepath,
        filename=os.path.basename(filepath),
        agent=agent,
        task=task,
        date_str=date_str,
        time_str=time_str,
        status=status,
        change_type=change_type,
        tags=tags,
        trigger_kind=trigger_kind,
        body=body,
    )


def load_all_receipts(receipts_dir: str) -> list[Receipt]:
    """Load all .md receipt files from receipts_dir (non-recursive)."""
    receipts: list[Receipt] = []
    if not os.path.isdir(receipts_dir):
        return receipts
    for entry in sorted(os.listdir(receipts_dir)):
        if not entry.endswith(".md"):
            continue
        full_path = os.path.join(receipts_dir, entry)
        if not os.path.isfile(full_path):
            continue
        receipt = _load_receipt(full_path)
        if receipt is not None:
            receipts.append(receipt)
    return receipts


# ---------------------------------------------------------------------------
# File-path search
# ---------------------------------------------------------------------------

def _file_path_matches(receipt: Receipt, pattern: str) -> bool:
    """Return True if pattern appears in the Files-shaped section of the receipt body.

    Searches within the first Files-shaped heading's section. Falls back to
    searching the whole body if no Files-shaped heading exists.
    """
    body = receipt.body
    lines = body.split("\n")

    # Find lines belonging to a Files-shaped section
    files_section_lines: list[str] = []
    in_files_section = False
    found_any_files_heading = False

    for line in lines:
        if FILES_HEADING_RE.match(line):
            in_files_section = True
            found_any_files_heading = True
            continue
        if in_files_section:
            # Stop at next heading (any level)
            if re.match(r"^#+\s", line):
                in_files_section = False
            else:
                files_section_lines.append(line)

    search_text: str
    if found_any_files_heading:
        search_text = "\n".join(files_section_lines)
    else:
        search_text = body

    return pattern.lower() in search_text.lower()


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_receipts(
    receipts: list[Receipt],
    since: Optional[date] = None,
    agents: Optional[list[str]] = None,          # OR semantics
    change_type: Optional[str] = None,
    file_path: Optional[str] = None,
    tags: Optional[list[str]] = None,             # OR semantics
    trigger_kind: Optional[str] = None,
    status: Optional[str] = None,
) -> list[Receipt]:
    """Apply all active filters and return matching receipts."""
    results: list[Receipt] = []

    for r in receipts:
        # --since
        if since is not None:
            if r.date_obj is None or r.date_obj < since:
                continue

        # --agent (OR across multiple values)
        if agents:
            if not any(r.agent == a for a in agents):
                continue

        # --change-type
        if change_type is not None:
            if r.change_type.lower() != change_type.lower():
                continue

        # --file-path (substring match, case-insensitive)
        if file_path is not None:
            if not _file_path_matches(r, file_path):
                continue

        # --tag (OR across multiple values)
        if tags:
            r_tags_lower = [t.lower() for t in r.tags]
            if not any(t.lower() in r_tags_lower for t in tags):
                continue

        # --trigger-kind
        if trigger_kind is not None:
            if r.trigger_kind.lower() != trigger_kind.lower():
                continue

        # --status
        if status is not None:
            if r.status.upper() != status.upper():
                continue

        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def _sort_key(r: Receipt) -> tuple:
    """Sort key: date descending, then time descending."""
    date_part = r.date_obj or date(1970, 1, 1)
    # time_str may be '1900', '19:00', etc.
    time_norm = r.time_str.replace(":", "").replace(" ", "")[:4]
    try:
        time_int = int(time_norm) if time_norm.isdigit() else 0
    except ValueError:
        time_int = 0
    # Negate for descending sort
    return (
        -date_part.toordinal(),
        -time_int,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _one_line(r: Receipt) -> str:
    ct = r.change_type if r.change_type else "?"
    task_display = r.task[:60] if r.task else "(no task)"
    return f"{r.date_str} {r.time_str:>4} {r.agent:<20} [{ct:<11}] {task_display} — {r.status}"


def _full_body(r: Receipt) -> str:
    """Return filename header + full body text."""
    sep = "=" * 72
    return f"{sep}\n{r.filename}\n{sep}\n{r.body}"


# ---------------------------------------------------------------------------
# git log helper
# ---------------------------------------------------------------------------

def _run_git_log(since: Optional[date], vault: str) -> str:
    """Invoke git log in vault root and return output string."""
    cmd = [
        "git", "-C", vault,
        "log",
        "--pretty=format:%h %ad %s",
        "--date=iso",
    ]
    if since is not None:
        cmd.extend([f"--since={since.isoformat()}"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return f"[git log error: {result.stderr.strip()}]"
        return result.stdout.strip()
    except FileNotFoundError:
        return "[git not found — vault may not be a git repo yet]"
    except subprocess.TimeoutExpired:
        return "[git log timed out]"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Query Meta/receipts/ with optional filters. Read-only."
    )
    p.add_argument(
        "--since",
        metavar="ISO_DATE",
        help="Match receipts with date >= YYYY-MM-DD",
    )
    p.add_argument(
        "--agent",
        action="append",
        dest="agents",
        metavar="NAME",
        help="Filter by agent name (repeatable — OR semantics)",
    )
    p.add_argument(
        "--change-type",
        metavar="ENUM",
        help=f"Filter by change_type: {' | '.join(sorted(CHANGE_TYPE_VALUES))}",
    )
    p.add_argument(
        "--file-path",
        metavar="PATTERN",
        help="Substring match within the Files section (or full body if no Files heading)",
    )
    p.add_argument(
        "--tag",
        action="append",
        dest="tags",
        metavar="NAME",
        help="Filter by tag (repeatable — OR semantics)",
    )
    p.add_argument(
        "--trigger-kind",
        metavar="ENUM",
        help=f"Filter by trigger_kind: {' | '.join(sorted(TRIGGER_KIND_VALUES))}",
    )
    p.add_argument(
        "--status",
        metavar="ENUM",
        help=f"Filter by status: {' | '.join(sorted(STATUS_VALUES))}",
    )

    # Output modes (mutually exclusive)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Dump full body of each matching receipt",
    )
    mode.add_argument(
        "--count",
        action="store_true",
        help="Print match count only",
    )

    p.add_argument(
        "--git-log",
        action="store_true",
        help="Append git log output (--since scoped) after receipt summaries",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Parse --since
    since_date: Optional[date] = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got '{args.since}'", file=sys.stderr)
            return 2

    # Validate enums
    if args.change_type and args.change_type.lower() not in CHANGE_TYPE_VALUES:
        print(
            f"ERROR: --change-type '{args.change_type}' not in {sorted(CHANGE_TYPE_VALUES)}",
            file=sys.stderr,
        )
        return 2
    if args.trigger_kind and args.trigger_kind.lower() not in TRIGGER_KIND_VALUES:
        print(
            f"ERROR: --trigger-kind '{args.trigger_kind}' not in {sorted(TRIGGER_KIND_VALUES)}",
            file=sys.stderr,
        )
        return 2
    if args.status and args.status.upper() not in STATUS_VALUES:
        print(
            f"ERROR: --status '{args.status}' not in {sorted(STATUS_VALUES)}",
            file=sys.stderr,
        )
        return 2

    # Load and filter
    all_receipts = load_all_receipts(RECEIPTS_DIR)
    matches = filter_receipts(
        all_receipts,
        since=since_date,
        agents=args.agents,
        change_type=args.change_type,
        file_path=args.file_path,
        tags=args.tags,
        trigger_kind=args.trigger_kind,
        status=args.status,
    )

    # Sort: date+time descending
    matches.sort(key=_sort_key)

    # Output
    if args.count:
        print(len(matches))
    elif args.full:
        for r in matches:
            print(_full_body(r))
    else:
        for r in matches:
            print(_one_line(r))

    # --git-log (appended after receipt output)
    if args.git_log:
        git_output = _run_git_log(since_date, VAULT)
        print("\n--- git log ---")
        print(git_output if git_output else "(no commits in range)")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
