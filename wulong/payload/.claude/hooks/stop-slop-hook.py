#!/usr/bin/env python3
"""
stop-slop-hook.py -- Stop hook that blocks delivery if the last assistant
message contains an em dash (U+2014).

Wired for NN#21 pre-delivery em-dash enforcement.

Contract:
  - Reads Stop event JSON from stdin (fields: hook_event_name, transcript_path).
  - On a payload naming a different hook_event_name: exits 0, does nothing, and
    records the event it actually received (fail-OPEN, and visible in the log).
  - Reads the transcript file; extracts the last assistant text turn.
  - Runs slop-scrub logic (single codepoint scan, no subprocess).
  - On confirmed em-dash match: exits 0 + prints {"decision":"block","reason":"..."}
    so Claude Code feeds the reason back to the model and re-generates.
  - On stop_hook_active=true: exits 0 immediately (prevents infinite loops).
  - On ANY error: exits 0 without printing JSON (fail-OPEN).
  - Appends one JSON line per invocation to .wulong/hook-events.jsonl, so a hook
    that fails open leaves a record and a hook that never fires leaves none.

Stop hook block cap: default 8 iterations (CLAUDE_CODE_STOP_HOOK_BLOCK_CAP).

ponytail: stdlib only, inline scan (no subprocess to slop-scrub), no classes.

Real transcript format (JSONL -- one JSON object per line):
  {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."}]}, ...}
  Each record is independent; parse line by line, skip bad lines.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

EM_DASH = "—"

# The Claude Code event this hook is written for. wulong-init.py holds the same
# string and tests/test_hook_wiring.py reads THIS one by AST and asserts they
# match, so a settings file can never name an event the script does not parse.
#
# main() also ENFORCES it at runtime against the incoming hook_event_name. It has
# to: transcript_path is a common field across Claude Code payloads, not a
# Stop-only one, so a mis-wired settings entry pointing another event here would
# otherwise scan and block on that event with nothing to distinguish it.
HOOK_EVENT = "Stop"

HOOK_NAME = "stop-slop"

# Durable, append-only, one line of JSON per invocation.
#
# WHY IT EXISTS: every non-blocking path in main() is a bare `return`, so a hook
# that fails open emits nothing at all, and hook stderr is transient anyway. With
# no record, "the hook is fine" and "the hook has been crashing for a week" look
# identical.
#
# HEARTBEAT: a record is written on EVERY invocation, including the ordinary
# allow. That is what separates the two silences. No file, or a file with no
# records, means the hook has NEVER FIRED: wrong path, or killed by the timeout.
# Records whose outcome is "allow" mean it fired and had nothing to do. Without
# the heartbeat those two are the same empty log. A wrong event is neither: it
# fires, and it records the event name it was handed.
#
# WHAT IS NEVER RECORDED: message text. The reason string is built for the model
# and carries line and column numbers only, and the log stores the HIT COUNT, not
# the prose. A delivery gate that quietly wrote every blocked message to disk
# would be a worse leak than the one it prevents.
HOOK_LOG_REL = ".wulong/hook-events.jsonl"


def _vault_root() -> Path:
    """WULONG_ROOT, else this file's own vault.

    Install-relative resolution is CORRECT for this file and wrong for an engine
    script, and the difference is where the file lives after installation. The
    engine stays in site-packages, so `__file__` there names the install
    directory rather than anyone's vault. This hook is PAYLOAD: `wulong init`
    copies it to <vault>/.claude/hooks/, and Claude Code runs it from there, so
    parent.parent.parent is that vault and nothing else.
    """
    env = os.environ.get("WULONG_ROOT", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent


def _log(outcome: str, **fields) -> None:
    """Append one record. Never raises, and never creates a directory.

    The `.wulong/` directory must already exist, which `wulong init` guarantees.
    Requiring it rather than creating it is the guard against a mis-resolved
    root: a wrong answer then writes nothing instead of scattering a stray
    `.wulong/` somewhere on the disk.
    """
    try:
        log_dir = _vault_root() / HOOK_LOG_REL
        if not log_dir.parent.is_dir():
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hook": HOOK_NAME,
            "event": HOOK_EVENT,
            "outcome": outcome,
        }
        record.update(fields)
        with log_dir.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 -- logging must never wedge the session
        pass


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
    except Exception as exc:  # noqa: BLE001
        _log("failopen", stage="parse_event", error=type(exc).__name__)
        return  # fail-open: malformed input

    # The wrong-event guard. An absent name is treated as ours, because older
    # runners and hand-fed payloads omit the field and fail-open is the rule
    # everywhere else in this file. A name that is present and different is a
    # mis-wiring: do nothing, and log the OBSERVED event rather than this file's
    # own constant, so the log shows the mis-wiring as itself.
    observed = event.get("hook_event_name", "")
    if observed and observed != HOOK_EVENT:
        _log("allow", reason="wrong_event", event=observed)
        return

    # Prevent infinite loop: if the model is already in a rewrite triggered
    # by this hook, let it stop.
    if event.get("stop_hook_active"):
        _log("allow", reason="stop_hook_active")
        return

    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        _log("allow", reason="no_transcript")
        return  # no transcript available -- fail-open

    try:
        records = _read_jsonl(transcript_path)
    except Exception as exc:  # noqa: BLE001
        _log("failopen", stage="read_transcript", error=type(exc).__name__)
        return  # fail-open: cannot read transcript

    if not records:
        _log("allow", reason="no_records")
        return  # empty or unparseable -- fail-open

    text = _last_assistant_text(records)
    if not text:
        _log("allow", reason="no_assistant_text")
        return  # nothing to scan -- fail-open

    hits = _scan_em_dash(text)
    if not hits:
        _log("allow", reason="clean")
        return  # clean -- allow delivery

    # Em dash found: block and feed reason back to Claude.
    locations = ", ".join(f"line {ln} col {col}" for ln, col in hits[:5])
    reason = (
        f"Em dash (U+2014) found at: {locations}. "
        "Replace with: a comma, a colon, brackets, or a full stop. "
        "Do NOT use the em dash character in user-facing prose (NN#12f). "
        "Rewrite the affected sentence(s) and try again."
    )
    _log("block", hits=len(hits))
    # STDOUT, as JSON, because that is the Stop protocol's block channel: the
    # runner reads the decision off stdout and feeds `reason` back to the model.
    # A PreToolUse deny is a different channel (exit 2 plus stderr); writing this
    # to stderr, or exiting non-zero, would surface a scary error and block
    # nothing. Exit stays 0.
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

        def _run(transcript_path: str, stop_hook_active: bool = False,
                 hook_event_name: str = "") -> dict | None:
            """Run the hook, return parsed JSON output or None."""
            import subprocess
            event = {"transcript_path": transcript_path}
            if stop_hook_active:
                event["stop_hook_active"] = True
            if hook_event_name:
                event["hook_event_name"] = hook_event_name
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

        # T8: another event's payload, carrying a transcript_path that WOULD
        # block under the Stop event -> no block. transcript_path is a common
        # field, so this is the only thing that separates the two.
        p8 = _make_jsonl("Wrong event \u2014 em dash")
        out8 = _run(p8, hook_event_name="PreToolUse")
        os.unlink(p8)
        if out8 is None:
            print("T8 PASS: another event's payload -> no block")
            passed += 1
        else:
            print(f"T8 FAIL: expected no block, got {out8}")
            failed += 1

        print(f"\nSelf-check: {passed} PASS, {failed} FAIL")
        sys.exit(0 if failed == 0 else 1)

    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- outer safety net; must never wedge
        _log("failopen", stage="main", error=type(exc).__name__)
        pass  # fail-open
