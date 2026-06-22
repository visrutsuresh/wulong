#!/usr/bin/env python3
"""
stop-slop-hook.py -- Stop hook that blocks delivery if the last assistant
message contains an em dash (U+2014).

Wired for NN#21 pre-delivery em-dash enforcement.

Contract:
  - Reads Stop event JSON from stdin (field: transcript_path).
  - Reads the transcript file; extracts the last assistant text turn.
  - Runs slop-scrub logic (single codepoint scan, no subprocess).
  - On confirmed em-dash match: exits 0 + prints {"decision":"block","reason":"..."}
    so Claude Code feeds the reason back to the model and re-generates.
  - On stop_hook_active=true: exits 0 immediately (prevents infinite loops).
  - On ANY error: exits 0 without printing JSON (fail-OPEN).

Stop hook block cap: default 8 iterations (CLAUDE_CODE_STOP_HOOK_BLOCK_CAP).

ponytail: stdlib only, inline scan (no subprocess to slop-scrub), no classes.

Real transcript format (JSONL -- one JSON object per line):
  {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."}]}, ...}
  Each record is independent; parse line by line, skip bad lines.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EM_DASH = "—"


def _read_jsonl(path: str) -> list:
    """
    Parse a JSONL transcript: one JSON object per line.
    Lines that fail to parse are skipped (fail-open per line).
    Falls back gracefully if the file is a single JSON object instead.
    """
    text = Path(path).read_text(encoding="utf-8")
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass  # skip unparseable lines
    if records:
        return records
    # Fallback: entire file is a single JSON object (legacy/fixture shape).
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            return obj.get("messages", [])
    except Exception:  # noqa: BLE001
        pass
    return []


def _last_assistant_text(records: list) -> str:
    """
    Extract concatenated text from the LAST assistant turn.

    Handles three shapes (most-specific first):
    1. Real JSONL: top-level "type"=="assistant", text in message.content list.
    2. Legacy nested: top-level "role"=="assistant", content in record directly.
    3. Wrapped: record["message"]["role"]=="assistant", content in record["message"].
    """
    for record in reversed(records):
        if not isinstance(record, dict):
            continue

        # Shape 1: real JSONL (type=="assistant")
        if record.get("type") == "assistant":
            msg = record.get("message", {})
            content = msg.get("content", "")
            text = _extract_content_text(content)
            if text:
                return text

        # Shape 2: top-level role=="assistant" (fixture/legacy)
        if record.get("role") == "assistant":
            content = record.get("content", "")
            text = _extract_content_text(content)
            if text:
                return text

        # Shape 3: nested message.role=="assistant"
        msg = record.get("message", {})
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content", "")
            text = _extract_content_text(content)
            if text:
                return text

    return ""


def _extract_content_text(content) -> str:
    """
    Extract plain text from a content value.
    content may be:
      - a plain string
      - a list of content blocks: {"type":"text","text":"..."} or {"type":"thinking",...}
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _scan_em_dash(text: str) -> list[tuple[int, int]]:
    """Return (line, col) 1-indexed for every U+2014 in text."""
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for col, ch in enumerate(line, 1):
            if ch == EM_DASH:
                hits.append((ln, col))
    return hits


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001
        return  # fail-open: malformed input

    # Prevent infinite loop: if the model is already in a rewrite triggered
    # by this hook, let it stop.
    if event.get("stop_hook_active"):
        return

    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        return  # no transcript available -- fail-open

    try:
        records = _read_jsonl(transcript_path)
    except Exception:  # noqa: BLE001
        return  # fail-open: cannot read transcript

    if not records:
        return  # empty or unparseable -- fail-open

    text = _last_assistant_text(records)
    if not text:
        return  # nothing to scan -- fail-open

    hits = _scan_em_dash(text)
    if not hits:
        return  # clean -- allow delivery

    # Em dash found: block and feed reason back to Claude.
    locations = ", ".join(f"line {ln} col {col}" for ln, col in hits[:5])
    reason = (
        f"Em dash (U+2014) found at: {locations}. "
        "Replace with: a comma, a colon, brackets, or a full stop. "
        "Do NOT use the em dash character in user-facing prose (NN#12f). "
        "Rewrite the affected sentence(s) and try again."
    )
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    # Self-check fixtures using REAL JSONL shapes.
    # Run: python3 stop-slop-hook.py --selfcheck
    if len(sys.argv) > 1 and sys.argv[1] == "--selfcheck":
        import os
        import tempfile

        def _make_jsonl(text_content: str) -> str:
            """Write a minimal REAL JSONL transcript to a temp file, return path."""
            record = {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text_content}],
                },
            }
            f = tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            )
            f.write(json.dumps(record) + "\n")
            f.close()
            return f.name

        def _run(transcript_path: str, stop_hook_active: bool = False) -> dict | None:
            """Run the hook, return parsed JSON output or None."""
            import subprocess
            event = {"transcript_path": transcript_path}
            if stop_hook_active:
                event["stop_hook_active"] = True
            result = subprocess.run(
                [sys.executable, __file__],
                input=json.dumps(event),
                capture_output=True,
                text=True,
            )
            out = result.stdout.strip()
            return json.loads(out) if out else None

        passed = 0
        failed = 0

        # T1: REAL JSONL with em dash -> must block
        p1 = _make_jsonl("Hello — world")
        out1 = _run(p1)
        os.unlink(p1)
        if out1 and out1.get("decision") == "block":
            print("T1 PASS: em dash in real JSONL -> block")
            passed += 1
        else:
            print(f"T1 FAIL: expected block, got {out1}")
            failed += 1

        # T2: REAL JSONL without em dash -> no block
        p2 = _make_jsonl("Hello world, no em dash here.")
        out2 = _run(p2)
        os.unlink(p2)
        if out2 is None:
            print("T2 PASS: clean real JSONL -> no block")
            passed += 1
        else:
            print(f"T2 FAIL: expected no block, got {out2}")
            failed += 1

        # T3: REAL JSONL with em dash but stop_hook_active -> no block (infinite loop guard)
        p3 = _make_jsonl("Hello — world")
        out3 = _run(p3, stop_hook_active=True)
        os.unlink(p3)
        if out3 is None:
            print("T3 PASS: stop_hook_active -> no block")
            passed += 1
        else:
            print(f"T3 FAIL: expected no block, got {out3}")
            failed += 1

        # T4: missing transcript path -> no block (fail-open)
        out4 = _run("/nonexistent/path/that/does/not/exist.jsonl")
        if out4 is None:
            print("T4 PASS: missing transcript -> fail-open (no block)")
            passed += 1
        else:
            print(f"T4 FAIL: expected no block, got {out4}")
            failed += 1

        # T5: multi-record JSONL; em dash only in last assistant msg -> block
        p5 = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        p5.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Earlier clean msg"}]},
        }) + "\n")
        p5.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "user message"},
        }) + "\n")
        p5.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Last msg — has em dash"}]},
        }) + "\n")
        p5.close()
        out5 = _run(p5.name)
        os.unlink(p5.name)
        if out5 and out5.get("decision") == "block":
            print("T5 PASS: em dash in last of multi-record JSONL -> block")
            passed += 1
        else:
            print(f"T5 FAIL: expected block, got {out5}")
            failed += 1

        # T6: bad line mixed in -> must not crash; em dash in last msg -> block
        p6 = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        p6.write("THIS IS NOT JSON\n")
        p6.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello — world"}]},
        }) + "\n")
        p6.close()
        out6 = _run(p6.name)
        os.unlink(p6.name)
        if out6 and out6.get("decision") == "block":
            print("T6 PASS: bad line skipped; em dash detected -> block")
            passed += 1
        else:
            print(f"T6 FAIL: expected block, got {out6}")
            failed += 1

        # T7: legacy fixture shape (top-level role) -> backward compat
        p7 = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        p7.write(json.dumps({"role": "assistant", "content": "Legacy — shape"}) + "\n")
        p7.close()
        out7 = _run(p7.name)
        os.unlink(p7.name)
        if out7 and out7.get("decision") == "block":
            print("T7 PASS: legacy top-level-role shape -> block")
            passed += 1
        else:
            print(f"T7 FAIL: expected block, got {out7}")
            failed += 1

        print(f"\nSelf-check: {passed} PASS, {failed} FAIL")
        sys.exit(0 if failed == 0 else 1)

    try:
        main()
    except Exception:  # noqa: BLE001 -- outer safety net; must never wedge
        pass  # fail-open
