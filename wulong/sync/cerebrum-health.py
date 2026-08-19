#!/usr/bin/env python3
"""
cerebrum-health.py — one-glance health check for the Cerebrum v3.0 plumbing.

Run anytime to verify the hooks, agent-bus, Hermes infrastructure, and
synthesis scripts are all alive and behaving. Plain-English output, no
JSON dumps, no surprises.

Usage:
    python3 Meta/sync/cerebrum-health.py
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")


def green(s: str) -> str: return f"\033[32m{s}\033[0m"
def red(s: str) -> str: return f"\033[31m{s}\033[0m"
def yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def dim(s: str) -> str: return f"\033[2m{s}\033[0m"


def mtime_str(p: Path) -> str:
    if not p.exists():
        return "(missing)"
    age = NOW - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    if age < timedelta(minutes=2):
        return f"{int(age.total_seconds())}s ago"
    if age < timedelta(hours=1):
        return f"{int(age.total_seconds() / 60)}m ago"
    if age < timedelta(days=1):
        return f"{int(age.total_seconds() / 3600)}h ago"
    return f"{int(age.days)}d ago"


def check_hooks() -> None:
    print("─" * 70)
    print(" HOOKS — does Claude Code write to Cerebrum when you do anything?")
    print("─" * 70)

    # This block used to print three red crosses at a CORRECTLY installed vault,
    # on both paths through the opt-in. It demanded UserPromptSubmit AND
    # PostToolUse in a settings file wulong writes neither of, so a user who
    # accepted the Stop-hook wiring still got a cross, and one who declined got a
    # cross for a deliberate choice. A health check that reds on a correct
    # install is one people learn to scroll past.
    # Now: report per event, and report the two vault-local scripts as ABSENT
    # rather than WRONG, because `wulong init` does not install them and never
    # claimed to.
    settings = VAULT / ".claude" / "settings.json"
    wired: list[str] = []
    if settings.exists():
        try:
            wired = sorted(json.loads(settings.read_text()).get("hooks", {}))
        except (OSError, ValueError):
            wired = []
    if wired:
        print(f"  settings.json hooks wired:       {green('✓')} {dim(', '.join(wired))}")
    elif settings.exists():
        print(f"  settings.json hooks wired:       {yellow('none')} "
              f"{dim('(file exists, no hooks object)')}")
    else:
        print(f"  settings.json hooks wired:       {yellow('none')} "
              f"{dim('(optional; wulong init --with-hooks writes the Stop hook)')}")

    capture = VAULT / "Meta" / "sync" / "capture-feedback.py"
    trigger = VAULT / "Meta" / "sync" / "post-write-trigger.py"
    for label, path in (("capture-feedback.py", capture), ("post-write-trigger.py", trigger)):
        mark = green("✓") if path.exists() else yellow("absent")
        note = "" if path.exists() else dim("(vault-local; not installed by wulong init)")
        print(f"  {label + ' present:':<32} {mark} {note}")

    # Today's feedback files
    raw_today = VAULT / "Meta" / "feedback" / "raw" / TODAY
    if raw_today.exists():
        files = list(raw_today.glob("*.md"))
        if files:
            latest = max(files, key=lambda p: p.stat().st_mtime)
            print(f"  feedback files captured today:   {green(str(len(files)))} ({dim(f'latest: {mtime_str(latest)}')})")
        else:
            print(f"  feedback files captured today:   {yellow('0')} {dim('(no user messages today yet, or hook not firing — restart Claude Code if this persists)')}")
    else:
        print(f"  feedback files captured today:   {yellow('0')} {dim('(no raw dir for today — hook may not have fired since midnight UTC)')}")

    # Context file freshness
    ctx = VAULT / "Meta" / "context" / "jarvis.md"
    if ctx.exists():
        print(f"  Meta/context/jarvis.md last update: {green(mtime_str(ctx))}")
    else:
        print(f"  Meta/context/jarvis.md:          {red('missing')}")


def check_dead_watcher() -> None:
    print()
    print("─" * 70)
    print(" DEAD WATCHER — is the old fswatch/launchd entry truly gone?")
    print("─" * 70)

    # launchctl
    try:
        result = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5
        )
        _label = os.environ.get("WULONG_LAUNCHD_LABEL", "com.wulong.watch-meta")  # ponytail: env knob; set in overlay
        loaded = _label in result.stdout
        print(f"  launchd {_label}:   {red('STILL LOADED') if loaded else green('unloaded ✓')}")
    except Exception as e:
        print(f"  launchctl check failed:          {yellow(str(e))}")

    archive = VAULT / "Meta" / "sync" / "archive" / "dead-watcher-2026-05-29"
    if archive.exists():
        files = list(archive.glob("*"))
        print(f"  archived files:                  {green(str(len(files)))} in {archive.name}/")


def check_hermes() -> None:
    print()
    print("─" * 70)
    print(" HERMES — your sideline coach")
    print("─" * 70)

    cfg = VAULT / "Meta" / "hermes" / "config.json"
    notebook = VAULT / "Meta" / "hermes" / "notebook.md"
    agent_def = VAULT / ".claude" / "agents" / "hermes.md"
    proposals_queued = VAULT / "Meta" / "hermes-proposals" / "queued"

    print(f"  config.json:                     {green('✓') if cfg.exists() else red('✗')}")
    print(f"  notebook.md:                     {green('✓') if notebook.exists() else red('✗')} ({dim(mtime_str(notebook))})")
    print(f"  .claude/agents/hermes.md:        {green('✓') if agent_def.exists() else red('✗')}")

    if cfg.exists():
        import json
        c = json.loads(cfg.read_text())
        scopes = c.get("allowed_scopes", [])
        print(f"  allowed_scopes:                  {green(', '.join(scopes)) if len(scopes) > 1 else yellow(', '.join(scopes))}")
        print(f"  spawn_time_invocation:           {green('on') if c.get('spawn_time_invocation') else red('off')}")
        print(f"  max_invocations_per_day:         {c.get('max_invocations_per_day')}")

    if proposals_queued.exists():
        queued = list(proposals_queued.glob("*.md"))
        print(f"  proposals queued for review:     {green(str(len(queued))) if queued else dim('none yet')}")


def check_agent_bus() -> None:
    print()
    print("─" * 70)
    print(" AGENT-BUS — can agents talk to each other?")
    print("─" * 70)

    server = VAULT / ".claude" / "mcp-servers" / "agent-bus" / "server.py"
    venv_py = VAULT / ".claude" / "mcp-servers" / "agent-bus" / ".venv" / "bin" / "python"
    db = VAULT / "Meta" / "agent-bus" / "bus.sqlite"
    mcp_json = VAULT / ".mcp.json"

    print(f"  server.py exists:                {green('✓') if server.exists() else red('✗')}")
    print(f"  venv python exists:              {green('✓') if venv_py.exists() else red('✗')}")
    print(f"  bus.sqlite exists:               {green('✓') if db.exists() else red('✗')}")
    print(f"  .mcp.json has agent-bus entry:   {green('✓') if mcp_json.exists() and 'agent-bus' in mcp_json.read_text() else red('✗')}")

    if db.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db)
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            halt = conn.execute("SELECT scope, active, reason FROM halt_state WHERE active=1").fetchall()
            conn.close()
            print(f"  total messages on bus:           {green(str(msg_count))}")
            if halt:
                for scope, active, reason in halt:
                    print(f"  {red('⚠ ACTIVE HALT')}:                 scope={scope} reason={reason}")
            else:
                print(f"  active halts:                    {green('none')}")
        except Exception as e:
            print(f"  bus DB query failed:             {red(str(e))}")


def check_synthesis() -> None:
    print()
    print("─" * 70)
    print(" SYNTHESIS — are lessons getting promoted to rules?")
    print("─" * 70)

    synth = VAULT / "Meta" / "sync" / "synthesize-lessons.py"
    active = VAULT / "Meta" / "memory" / "jarvis" / "active.md"
    last_marker = VAULT / "Meta" / "sync" / ".last-lesson-synth"

    print(f"  synthesize-lessons.py exists:    {green('✓') if synth.exists() else red('✗')}")
    print(f"  active.md exists:                {green('✓') if active.exists() else red('✗')} ({dim(mtime_str(active))})")

    if active.exists():
        rule_count = active.read_text().count("\n### R")
        print(f"  rules in active.md:              {green(str(rule_count))}")

    if last_marker.exists():
        print(f"  last synthesis run:              {green(mtime_str(last_marker))}")
    else:
        print(f"  last synthesis run:              {dim('never (will run on first end-of-session trigger)')}")

    # Count lessons across all agents
    total_lessons = 0
    for lessons_md in (VAULT / "Meta" / "memory").glob("*/lessons.md"):
        # crude count of "## " headings
        total_lessons += sum(1 for ln in lessons_md.read_text().split("\n") if ln.startswith("## "))
    print(f"  un-promoted lessons across agents: {green(str(total_lessons))} {dim('(synthesize-lessons.py reads these)')}")


def check_change_log() -> None:
    print()
    print("─" * 70)
    print(" CHANGE-LOG — the event stream all agents read")
    print("─" * 70)

    cl = VAULT / "Meta" / "change-log.md"
    if not cl.exists():
        print(f"  change-log.md:                   {red('missing')}")
        return

    print(f"  change-log.md last update:       {green(mtime_str(cl))}")
    lines = cl.read_text().split("\n")
    print(f"  total entries:                   {green(str(len([L for L in lines if L.startswith('[')])))}")

    # Today's activity
    today_short = NOW.strftime("[%Y-%m-%d")
    today_entries = [L for L in lines if L.startswith(today_short)]
    print(f"  entries today:                   {green(str(len(today_entries)))}")
    if today_entries:
        print(f"  latest entry:                    {dim(today_entries[-1][:90])}...")


def main() -> int:
    print()
    print(f"  Cerebrum v3.0 health check  {dim(NOW.strftime('%Y-%m-%d %H:%M:%S UTC'))}")
    check_hooks()
    check_dead_watcher()
    check_hermes()
    check_agent_bus()
    check_synthesis()
    check_change_log()
    print()
    print(dim("  Run anytime: python3 Meta/sync/cerebrum-health.py"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
