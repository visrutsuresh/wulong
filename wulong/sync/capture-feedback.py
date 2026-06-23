#!/usr/bin/env python3
"""
capture-feedback.py — UserPromptSubmit hook target.

Called by Claude Code BEFORE Jarvis (or any agent) sees the user's message.
Receives prompt context via env / stdin and writes one file per turn to:
    Meta/feedback/raw/<date>/<timestamp>--<session>--<turn>.md

Intent class is pre-classified via simple regex/keyword pass — cheap, deterministic,
no LLM call. Heavier LLM classification batched daily by classify-feedback.py (v3.1).

This script is part of Cerebrum v3.0 Phase 1. Replaces the dead fswatch/launchd
watcher with a Claude Code hook that fires exactly when the user submits a prompt.

Schema (per CLAUDE.md NN #7-compatible):
---
session_id: <claude-code-session-id>
turn: <int>
timestamp_utc: <iso8601>
intent_class: course-correction | preference-signal | decision-ask | factual-query | venting | other
sentiment: positive | negative | neutral | frustrated
references: [paths cited or implied]
agent_active: <agent-name or "jarvis">
conflict_with_rule: <rule-id or null>
---
<verbatim user message>

Exit codes:
  0 = success (file written) OR no-op (hook called with no prompt)
  1 = unrecoverable error (logged to stderr; Claude Code shows hook error)
"""
from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
FEEDBACK_RAW = VAULT / "Meta" / "feedback" / "raw"
DAILY_FILE_CAP = 500  # hard ceiling per locked-pref guardrail

# ────────────────────────────────────────────────────────────────────────────────
# Intent classification — regex-based, cheap, deterministic
# Heavier classification happens daily via classify-feedback.py
# ────────────────────────────────────────────────────────────────────────────────
INTENT_PATTERNS = [
    # ORDER MATTERS — first match wins
    ("course-correction", [
        r"\b(no|nope|don'?t|stop|wrong|actually|instead|rather)\b",
        r"\b(redo|re-?run|undo|revert|rollback)\b",
        r"\b(that'?s? (?:wrong|bad|shit|terrible|horrible|ugly|off|stale|broken|outdated))\b",
        r"\b(use (?:this|these) (?:colour|color|hex|value) instead)\b",
    ]),
    ("preference-signal", [
        r"\b(i (?:prefer|like|want|need|love|hate))\b",
        r"\b(make (?:it|this|that) (?:more|less))\b",
        r"\b(should be (?:more|less|deeper|brighter|tighter|cleaner))\b",
        r"\b(could be (?:more|less|harmonious|distinct|subtle))\b",
    ]),
    ("decision-ask", [
        r"\?\s*$",
        r"\b(should i|which|what (?:if|about)|how (?:do|should|would|about))\b",
    ]),
    ("factual-query", [
        r"\b(what is|what are|where is|where are|who is)\b",
        r"\b(can you (?:explain|tell|show))\b",
    ]),
    ("venting", [
        r"(😭|😂|🤣|😡|😤|💀)",
        r"\b(omg|wtf|holy|jesus|christ|fuck)\b",
        r"\b(gonna cry|so frustrated)\b",
    ]),
]

SENTIMENT_NEGATIVE = re.compile(
    r"\b(bad|shit|terrible|horrible|wrong|ugly|stale|broken|hate|no|don'?t|fail|stuck)\b",
    re.IGNORECASE,
)
SENTIMENT_POSITIVE = re.compile(
    r"\b(beautiful|love|nice|great|good|perfect|works|ship it|elegant|clean)\b",
    re.IGNORECASE,
)
SENTIMENT_FRUSTRATED = re.compile(
    r"(😭|😂|🤣|💀|gonna cry|hahaha|wtf|holy|jesus|so frustrated|whyyy+|broken)",
    re.IGNORECASE,
)


def classify_intent(text: str) -> str:
    low = text.lower()
    for label, patterns in INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, low, re.IGNORECASE):
                return label
    return "other"


def classify_sentiment(text: str) -> str:
    if SENTIMENT_FRUSTRATED.search(text):
        return "frustrated"
    neg = bool(SENTIMENT_NEGATIVE.search(text))
    pos = bool(SENTIMENT_POSITIVE.search(text))
    if neg and not pos:
        return "negative"
    if pos and not neg:
        return "positive"
    return "neutral"


def extract_references(text: str) -> list[str]:
    """Scan for file paths, hex codes, URLs, agent names."""
    refs = []
    # absolute paths
    refs.extend(re.findall(r"/Users/[\w/.\-]+", text))
    # vault-relative paths (Meta/..., 01-Projects/..., etc.)
    refs.extend(re.findall(r"\b(?:Meta|01-Projects|02-Areas|03-Resources|04-Archive|05-People|06-Meetings|07-Daily|\.claude)[\w/.\-]+", text))
    # hex colors
    refs.extend(re.findall(r"#[0-9a-fA-F]{6}\b", text))
    # URLs
    refs.extend(re.findall(r"https?://[^\s)>]+", text))
    return list(dict.fromkeys(refs))[:10]  # dedup + cap


def get_session_id() -> str:
    """Try a few env vars Claude Code may set; fall back to date-based pseudo-id."""
    for k in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "SESSION_ID"):
        v = os.environ.get(k)
        if v:
            return v
    # Fallback: use date hour to group same-day same-hour turns
    return datetime.now(timezone.utc).strftime("session-%Y%m%dT%H")


def get_turn_number(session_id: str, date_dir: Path) -> int:
    """Count existing files matching this session id today; return next ordinal."""
    if not date_dir.exists():
        return 1
    existing = list(date_dir.glob(f"*--{session_id}--*.md"))
    return len(existing) + 1


def get_active_agent() -> str:
    """Detect which agent is active in this session."""
    return os.environ.get("CLAUDE_AGENT") or os.environ.get("CC_AGENT") or "jarvis"


def get_prompt_text() -> str | None:
    """Pull prompt from Claude Code hook env (preferred) or stdin (fallback)."""
    # Claude Code passes prompt content via env or stdin depending on version.
    # Try a few known locations:
    for k in ("CLAUDE_USER_PROMPT", "USER_PROMPT", "PROMPT_TEXT"):
        v = os.environ.get(k)
        if v:
            return v
    # stdin fallback
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            # Some Claude Code hooks pass JSON like {"prompt": "..."}
            try:
                obj = json.loads(data)
                if isinstance(obj, dict):
                    for k in ("prompt", "user_prompt", "userPrompt", "message", "text"):
                        if k in obj and obj[k]:
                            return str(obj[k])
                if isinstance(obj, str):
                    return obj
            except json.JSONDecodeError:
                pass
            return data
    return None


def main() -> int:
    prompt = get_prompt_text()
    if not prompt:
        # No prompt content — hook called outside its normal context; no-op.
        return 0

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y-%m-%dT%H-%M-%SZ")

    session_id = get_session_id()
    date_dir = FEEDBACK_RAW / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    # Volume guardrail
    today_count = len(list(date_dir.glob("*.md")))
    if today_count >= DAILY_FILE_CAP:
        # silently drop — guardrail per locked-pref
        sys.stderr.write(f"[capture-feedback] DAILY_FILE_CAP {DAILY_FILE_CAP} reached for {date_str}; dropping.\n")
        return 0

    turn = get_turn_number(session_id, date_dir)
    intent = classify_intent(prompt)
    sentiment = classify_sentiment(prompt)
    refs = extract_references(prompt)
    agent = get_active_agent()

    out_path = date_dir / f"{ts_str}--{session_id}--turn{turn:03d}.md"
    front = [
        "---",
        f"session_id: {session_id}",
        f"turn: {turn}",
        f"timestamp_utc: {now.isoformat()}",
        f"intent_class: {intent}",
        f"sentiment: {sentiment}",
        f"references: {json.dumps(refs)}",
        f"agent_active: {agent}",
        "conflict_with_rule: null",
        "---",
        "",
    ]
    out_path.write_text("\n".join(front) + prompt.rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[capture-feedback] ERROR: {e}\n")
        sys.exit(1)
