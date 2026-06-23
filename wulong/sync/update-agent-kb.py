#!/usr/bin/env python3
"""
update-agent-kb.py — Append an action to an agent's knowledge base Action History.

Usage:
  python3 Meta/sync/update-agent-kb.py \\
    --agent [name] \\
    --action "[what was done]" \\
    --outcome "[result]" \\
    --changed "[files changed]"

What it does:
  1. Reads Meta/knowledge-base/[agent].md
  2. Prepends new entry to Action History section
     Format: YYYY-MM-DD HH:MM | action | outcome | what changed
  3. Trims to last 30 entries
  4. Updates last_updated frontmatter timestamp
  5. Writes updated file atomically (.tmp → rename)
  6. Runs compile-context.py to trigger real-time recompile
  7. If action is significant (deploy/model-change/trade/batch-file/health-change),
     sends Telegram notification via send_message.py
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_DIR = os.path.join(VAULT_ROOT, "Meta", "knowledge-base")
SYNC_DIR = os.path.join(VAULT_ROOT, "Meta", "sync")
TELEGRAM_SCRIPT = os.path.join(VAULT_ROOT, "Meta", "telegram_bot", "send_message.py")
COMPILE_SCRIPT = os.path.join(SYNC_DIR, "compile-context.py")

# Actions that trigger a Telegram notification
SIGNIFICANT_ACTIONS = {
    "deploy",
    "vps-deploy",
    "model-change",
    "feature-implementation",
    "health-audit",
    "optimization-cycle",
    "daily-pdf-report",
    "go-live",
    "bug-fix",
    "batch-file",
}


def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_atomic(path, content):
    """Write content to path atomically via .tmp rename."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, path)


def update_frontmatter_timestamp(content, now_str):
    """Update or insert last_updated in YAML frontmatter."""
    # Try to update existing last_updated line
    updated = re.sub(
        r"^(last_updated:\s*).*$",
        f"last_updated: {now_str}",
        content,
        flags=re.MULTILINE,
    )
    if "last_updated:" not in updated:
        # Insert before closing --- of frontmatter
        updated = re.sub(
            r"^(---\n(?:.*\n)*?)(---)",
            lambda m: m.group(1) + f"last_updated: {now_str}\n" + m.group(2),
            updated,
            count=1,
        )
    return updated


def get_action_history_lines(content):
    """Extract the lines inside the Action History section."""
    m = re.search(
        r"## 4\. Action History\s*\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL,
    )
    if not m:
        return None, None, None
    section_text = m.group(1)
    start = m.start(1)
    end = m.end(1)
    return section_text, start, end


def trim_history_entries(section_text, max_entries=30):
    """
    Keep only the last max_entries history lines.
    History lines look like: YYYY-MM-DD ... | ... | ... | ...
    Comment lines (<!-- ... -->) and blank lines are preserved at start.
    """
    lines = section_text.split("\n")
    # Separate comment/blank preamble from data lines
    preamble = []
    data_lines = []
    in_preamble = True
    for line in lines:
        stripped = line.strip()
        if in_preamble and (
            stripped == ""
            or stripped.startswith("<!--")
            or stripped.endswith("-->")
        ):
            preamble.append(line)
        else:
            in_preamble = False
            data_lines.append(line)

    # Trim data lines to max_entries (keep the first max_entries since we prepend newest first)
    if len(data_lines) > max_entries:
        data_lines = data_lines[:max_entries]

    return "\n".join(preamble + data_lines)


def is_significant(action):
    """Check if the action string matches a significant action keyword."""
    action_lower = action.lower()
    return any(sig in action_lower for sig in SIGNIFICANT_ACTIONS)


def send_telegram_notification(agent, action, outcome):
    """Send a Telegram notification for significant actions."""
    if not os.path.exists(TELEGRAM_SCRIPT):
        print(f"  [telegram] send_message.py not found — skipping notification", file=sys.stderr)
        return
    message = f"[{agent.upper()}] {action} — {outcome}"
    try:
        result = subprocess.run(
            [sys.executable, TELEGRAM_SCRIPT, message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            print(f"  [telegram] notification sent: {message}")
        else:
            print(f"  [telegram] failed: {result.stderr.strip()}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"  [telegram] timeout — skipping", file=sys.stderr)
    except Exception as e:
        print(f"  [telegram] error: {e}", file=sys.stderr)


def run_compile_context():
    """Trigger compile-context.py to rebuild agent context files."""
    if not os.path.exists(COMPILE_SCRIPT):
        print(f"  [compile] compile-context.py not found — skipping", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [sys.executable, COMPILE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"  [compile] context files rebuilt")
        else:
            print(f"  [compile] failed: {result.stderr.strip()[:200]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"  [compile] timeout — skipping", file=sys.stderr)
    except Exception as e:
        print(f"  [compile] error: {e}", file=sys.stderr)


MEMORY_DIR = os.path.join(VAULT_ROOT, "Meta", "memory")


def append_lesson(agent, lesson_text, now_str):
    """Append a timestamped lesson to Meta/memory/<agent>/lessons.md (create dir if missing)."""
    agent_dir = os.path.join(MEMORY_DIR, agent)
    os.makedirs(agent_dir, exist_ok=True)
    lessons_path = os.path.join(agent_dir, "lessons.md")
    # Ensure file exists with a header preamble
    if not os.path.exists(lessons_path):
        with open(lessons_path, "w") as f:
            f.write(f"<!-- {agent} lesson buffer — append-only. Promoted to active.md when frequency ≥3. -->\n")
    block = f"\n## {now_str}\n{lesson_text.strip()}\n"
    with open(lessons_path, "a") as f:
        f.write(block)
    print(f"  [lesson] appended to {os.path.relpath(lessons_path, VAULT_ROOT)}")


def main():
    parser = argparse.ArgumentParser(description="Update agent knowledge base action history")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. jarvis, coder, analyst)")
    parser.add_argument("--action", required=False, default=None, help="What the agent did")
    parser.add_argument("--outcome", required=False, default=None, help="Result of the action")
    parser.add_argument("--changed", required=False, default=None, help="Files changed (comma-separated or 'none')")
    parser.add_argument("--lesson", required=False, default=None, help="One-line lesson to append to Meta/memory/<agent>/lessons.md")
    args = parser.parse_args()

    # Build timestamp (used by both lesson and action flow)
    now_utc = datetime.now(timezone.utc)
    now_str_early = now_utc.strftime("%Y-%m-%d %H:%M")

    # Lesson-only mode: --lesson provided without action flow
    if args.lesson and not (args.action or args.outcome or args.changed):
        append_lesson(args.agent, args.lesson, now_str_early)
        return 0

    # If --lesson is provided alongside action flow, append lesson first then continue
    if args.lesson:
        append_lesson(args.agent, args.lesson, now_str_early)

    # Otherwise, require action/outcome/changed for the legacy flow
    if not (args.action and args.outcome and args.changed):
        print("ERROR: --action, --outcome, --changed are required unless using --lesson alone", file=sys.stderr)
        sys.exit(2)

    kb_path = os.path.join(KB_DIR, f"{args.agent}.md")

    # Read existing KB
    content = read_file(kb_path)
    if content is None:
        print(f"ERROR: KB file not found: {kb_path}", file=sys.stderr)
        print(f"  Create it first at Meta/knowledge-base/{args.agent}.md", file=sys.stderr)
        sys.exit(1)

    # Build timestamp
    now_str = now_str_early

    # Build new history entry (prepend = newest first)
    new_entry = f"{now_str} | {args.action} | {args.outcome} | {args.changed}"

    # Find action history section
    section_text, start, end = get_action_history_lines(content)
    if section_text is None:
        print(f"ERROR: '## 4. Action History' section not found in {kb_path}", file=sys.stderr)
        sys.exit(1)

    # Strip leading comment/blank preamble from section, prepend new entry
    lines = section_text.split("\n")
    preamble = []
    data_lines = []
    in_preamble = True
    for line in lines:
        stripped = line.strip()
        if in_preamble and (
            stripped == ""
            or stripped.startswith("<!--")
            or stripped.endswith("-->")
        ):
            preamble.append(line)
        else:
            in_preamble = False
            data_lines.append(line)

    # Prepend new entry to data lines
    data_lines = [new_entry] + [l for l in data_lines if l.strip()]

    # Trim to 30 entries
    if len(data_lines) > 30:
        data_lines = data_lines[:30]

    new_section = "\n".join(preamble + data_lines)

    # Rebuild content
    new_content = content[:start] + new_section + content[end:]

    # Update last_updated frontmatter
    new_content = update_frontmatter_timestamp(new_content, now_str)

    # Atomic write
    write_atomic(kb_path, new_content)
    print(f"[update-agent-kb] {args.agent} KB updated: {new_entry}")

    # Trigger compile-context.py
    run_compile_context()

    # Send Telegram notification if significant
    if is_significant(args.action):
        send_telegram_notification(args.agent, args.action, args.outcome)

    return 0


if __name__ == "__main__":
    sys.exit(main())
