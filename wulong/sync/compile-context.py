#!/usr/bin/env python3
"""
compile-context.py — Generate per-team agent context files from vault + live-state data.

Reads:
  Meta/brain.md
  Meta/live-state/*.md
  Meta/agent-messages.md
  Meta/approval-queue.md
  Meta/Sessions/ (most recent log)
  Meta/doctor/health-history.md
  Meta/knowledge-base/*.md (agent KB action histories — injected into context files)
  Meta/company-registry.md (Recent User Decisions block)

Writes:
  Meta/context/trading.md          — includes last 5 history entries from mastermind/analyst/coder/deployer (was phantom-troupe.md)
  Meta/context/keepers.md         — includes last 5 entries from sorter/scribe/connector/librarian
  Meta/context/jarvis.md          — includes last 3 entries from ALL agent KBs
  Meta/context/doctor.md

Run every 15 minutes (2 min after vps-sync):
  2-59/15 * * * * python3 /path/to/Meta/sync/compile-context.py
"""

import glob
import os
import re
import sys
from datetime import datetime, timezone
import urllib.request
import json as _json

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
META = os.path.join(VAULT_ROOT, "Meta")
CONTEXT_DIR = os.path.join(META, "context")
LIVE_STATE_DIR = os.path.join(META, "live-state")
KB_DIR = os.path.join(META, "knowledge-base")
INBOX_DIR = os.path.join(META, "inbox")
MEMORY_DIR = os.path.join(META, "memory")
PROPOSALS_DIR = os.path.join(META, "proposals")
REGISTRY_FILE = os.path.join(META, "session-registry.json")
HERMES_NOTEBOOK = os.path.join(META, "hermes", "notebook.md")
METIS_NOTEBOOK = os.path.join(META, "metis", "notebook.md")

# Task-enabled coordinators get their own per-agent context file
TASK_ENABLED_COORDINATORS = ["jarvis", "company-orchestrator", "ar-director", "doctor", "keepers"]


def get_observe_pass_banner():
    """
    Return an instructional banner string if there is a live jarvis session whose OBSERVE pass
    has not yet run (both notebook mtimes not yet > session start epoch).
    Returns empty string when no banner is needed (either no live session or OBSERVE already done).

    Detection is state-based (mtime epoch comparison), never time-based — safe for mid-session
    cron re-runs.  Both sides compared as UTC epoch floats; os.path.getmtime() is already UTC.
    """
    if not os.path.exists(REGISTRY_FILE):
        return ""
    try:
        with open(REGISTRY_FILE, "r") as f:
            registry = _json.load(f)
    except (IOError, ValueError):
        return ""

    for s in registry.get("sessions", []):
        if s.get("focus") != "jarvis":
            continue
        started_str = s.get("started", "")
        try:
            start_epoch = datetime.fromisoformat(started_str).timestamp()
        except (ValueError, TypeError):
            continue

        try:
            hermes_mtime = os.path.getmtime(HERMES_NOTEBOOK)
            metis_mtime = os.path.getmtime(METIS_NOTEBOOK)
        except OSError:
            # Notebooks don't exist yet — OBSERVE definitely not done
            return (
                "Session-start checklist: spawn hermes (OBSERVE) then metis "
                "— not yet done this session (5a/5a-bis)."
            )

        if hermes_mtime > start_epoch and metis_mtime > start_epoch:
            # OBSERVE pass confirmed for this session — no banner
            return ""
        else:
            return (
                "Session-start checklist: spawn hermes (OBSERVE) then metis "
                "— not yet done this session (5a/5a-bis)."
            )

    # No live jarvis session found
    return ""


def get_doctor_inbox_status():
    """
    Return (count, paths) of unread doctor-pending-*.md files in Meta/inbox/.
    'Unread' = file mtime newer than Meta/inbox/.last-read marker (or marker missing).
    Counts only files containing a '### RED' header.
    """
    if not os.path.isdir(INBOX_DIR):
        return 0, []
    marker = os.path.join(INBOX_DIR, ".last-read")
    try:
        last_read = os.path.getmtime(marker)
    except FileNotFoundError:
        last_read = 0
    pending = []
    for path in glob.glob(os.path.join(INBOX_DIR, "doctor-pending-*.md")):
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            continue
        if mtime <= last_read:
            continue
        # Only count files with RED findings
        try:
            with open(path) as f:
                if "### RED" in f.read():
                    pending.append(path)
        except OSError:
            pass
    return len(pending), pending


def get_proposals_count():
    """Return count of pending proposals in Meta/proposals/ (excluding README and archive)."""
    if not os.path.isdir(PROPOSALS_DIR):
        return 0
    count = 0
    for path in glob.glob(os.path.join(PROPOSALS_DIR, "*.md")):
        if os.path.basename(path) == "README.md":
            continue
        count += 1
    return count


def read_active_memory(agent):
    """Read Meta/memory/<agent>/active.md (return empty string if missing)."""
    path = os.path.join(MEMORY_DIR, agent, "active.md")
    return read_file(path)


def read_recent_lessons(agent, n=5):
    """Return the last n lesson blocks from Meta/memory/<agent>/lessons.md."""
    path = os.path.join(MEMORY_DIR, agent, "lessons.md")
    content = read_file(path)
    if not content:
        return []
    # Lesson blocks start with '## YYYY-MM-DD HH:MM'
    blocks = re.split(r"\n(?=## \d{4}-\d{2}-\d{2})", content)
    blocks = [b.strip() for b in blocks if b.strip().startswith("## ") and re.match(r"## \d{4}-\d{2}-\d{2}", b.strip())]
    return blocks[-n:]


def build_per_agent_context(agent, team_context_content):
    """Build Meta/context/<agent>.md = active.md + last 5 lessons + team context summary."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    active = read_active_memory(agent)
    lessons = read_recent_lessons(agent, 5)

    out = [
        f"<!-- Generated: {now_str} — do not edit manually -->",
        f"",
        f"# {agent} Per-Agent Context — {now_str}",
        f"",
        f"This file is your personalized persona + recent lesson buffer.",
        f"Read AFTER the team context file (Meta/context/jarvis.md, etc.).",
        f"",
        f"---",
        f"",
        f"## Active Persona (from Meta/memory/{agent}/active.md)",
        f"",
    ]
    if active.strip():
        out.append(active.strip())
    else:
        out.append("_No distilled rules yet — synthesize-lessons.py will populate this once frequency ≥3 patterns emerge._")
    out += [
        f"",
        f"---",
        f"",
        f"## Last 5 Lessons (from Meta/memory/{agent}/lessons.md)",
        f"",
    ]
    if lessons:
        for block in lessons:
            out.append(block)
            out.append("")
    else:
        out.append("_No lessons recorded yet. Lessons accumulate via `update-agent-kb.py --lesson`._")
        out.append("")
    out += [
        f"---",
        f"",
        f"## Team Context Snapshot",
        f"",
        f"_See Meta/context/<team>.md for full team state. Excerpt for orientation:_",
        f"",
        (team_context_content[:1500] if team_context_content else "_no team context available_"),
    ]
    return "\n".join(out)

# ---------------------------------------------------------------------------
# HIGH_IMPACT_FILES — changes to these files trigger immediate forced recompile
# when an agent calls compile-context.py immediately after writing one of these.
# The watch-meta.sh file watcher also picks these up within ~1 second.
# ---------------------------------------------------------------------------
HIGH_IMPACT_FILES = [
    os.path.join(META, "company-registry.md"),
    os.path.join(META, "master-map.md"),
    os.path.join(META, "brain.md"),
    os.path.join(META, "agents-roster.md"),
    os.path.join(META, "company-structure.md"),
    os.path.join(META, "task-board.md"),
    os.path.join(META, "agent-messages.md"),
    os.path.join(META, "approval-queue.md"),
    # All agent KB files (dynamically resolved at import time)
]

def get_high_impact_files():
    """Return the full list including dynamically found KB files."""
    files = set(HIGH_IMPACT_FILES)
    for kb_file in glob.glob(os.path.join(KB_DIR, "*.md")):
        files.add(kb_file)
    return files


def was_recently_modified(filepath, within_seconds=120):
    """Check if a file was modified within the last N seconds."""
    try:
        mtime = os.path.getmtime(filepath)
        now = datetime.now().timestamp()
        return (now - mtime) <= within_seconds
    except FileNotFoundError:
        return False


def check_high_impact_changes():
    """
    Check if any high-impact file was recently modified.
    Returns list of recently modified high-impact files.
    Used to decide whether to print a RECOMPILE notice.
    """
    recently_changed = []
    for fpath in get_high_impact_files():
        if was_recently_modified(fpath, within_seconds=120):
            recently_changed.append(os.path.relpath(fpath, VAULT_ROOT))
    return recently_changed

DOCTOR_AGENTS = {"doctor"}

TRADING_AGENTS = {
    "mastermind", "analyst", "data-scientist", "researcher", "crypto",
    "contrarian", "coder", "deployer", "dashboard-architect", "writer",
    "accountant", "lawyer",
    # phantom-troupe removed 2026-05-21 — coordinator dissolved 2026-05-15, work distributed across Architecture+R&D/Tech/Delivery+QA
}
KEEPER_AGENTS = {
    "architect", "scribe", "sorter", "seeker", "connector",
    "librarian", "transcriber", "postman", "keepers",
}


def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return default


def extract_section(content, header):
    """Extract text under a markdown ## Header until the next ##."""
    pattern = rf"#{{{2}}}[^#][^#]*{re.escape(header)}.*?\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def get_live_state_files():
    files = {}
    for path in glob.glob(os.path.join(LIVE_STATE_DIR, "*.md")):
        name = os.path.basename(path).replace(".md", "").replace("-", "_")
        files[name] = read_file(path)
    return files


def get_latest_session():
    sessions_dir = os.path.join(META, "Sessions")
    if not os.path.isdir(sessions_dir):
        return ""
    logs = sorted(glob.glob(os.path.join(sessions_dir, "*.md")))
    return read_file(logs[-1]) if logs else ""


def get_pending_messages(agent_messages_content, recipients):
    """Extract ⏳ pending messages addressed to a set of agents."""
    blocks = re.split(r"\n---\n", agent_messages_content)
    pending = []
    for block in blocks:
        if "⏳" not in block:
            continue
        if any(f"TO: {r}" in block or f"→ {r}" in block for r in recipients):
            clean = block.strip()
            if clean:
                pending.append(clean)
    return pending


def get_pending_approvals(approval_content):
    lines = []
    in_table = False
    for line in approval_content.splitlines():
        if "|" in line and "APQ-" in line:
            if "pending" in line.lower() or "⏳" in line:
                lines.append(line.strip())
    return lines


def extract_brain_section(brain_content, section_name):
    return extract_section(brain_content, section_name)


def inbox_count():
    inbox_dir = os.path.join(VAULT_ROOT, "00-Inbox")
    try:
        return len([f for f in os.listdir(inbox_dir) if f.endswith(".md")])
    except FileNotFoundError:
        return 0


def get_kb_history(agent_name, n=5):
    """Extract the last n action history entries from an agent's KB file."""
    kb_path = os.path.join(KB_DIR, f"{agent_name}.md")
    content = read_file(kb_path)
    if not content:
        return []

    # Find the Action History section
    m = re.search(
        r"## 4\. Action History\s*\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL,
    )
    if not m:
        return []

    section_text = m.group(1)
    entries = []
    for line in section_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.endswith("-->"):
            continue
        # Data line: YYYY-MM-DD HH:MM | action | outcome | changed
        if "|" in stripped and re.match(r"\d{4}-\d{2}-\d{2}", stripped):
            entries.append(stripped)
        if len(entries) >= n:
            break
    return entries


def build_cross_agent_activity(agents_n_pairs):
    """
    Build a 'Cross-Agent Recent Activity' section.
    agents_n_pairs: list of (agent_name, n_entries)
    Returns a markdown section string.
    """
    sections = ["## Cross-Agent Recent Activity", ""]
    any_found = False
    for agent_name, n in agents_n_pairs:
        entries = get_kb_history(agent_name, n)
        if entries:
            any_found = True
            sections.append(f"**{agent_name}** (last {n}):")
            for entry in entries:
                sections.append(f"  - {entry}")
            sections.append("")
    if not any_found:
        sections.append("_No KB history available yet — agents will populate this as they run._")
        sections.append("")
    return "\n".join(sections)


def write_context(filename, content):
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    target = os.path.join(CONTEXT_DIR, filename)
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, target)


def get_registry_decisions(vault_root):
    """Read the 'Recent User Decisions (CEO → Agents)' section from company-registry.md."""
    path = os.path.join(vault_root, "Meta", "company-registry.md")
    content = read_file(path)
    if not content:
        return ""
    return extract_section(content, "Recent User Decisions")


# ---------------------------------------------------------------------------
# Per-project situation assessment (derived from live-state data + brain.md)
# ---------------------------------------------------------------------------

# ponytail: WR baselines, go-live dates, critical path, and experiment status from overlay config
# Add .wulong/projects.json in your overlay with keys: wr_baselines, go_live_dates, critical_path, next_experiment
try:
    _cfg_data_proj = _json.loads(open(_projects_cfg).read())
    _ALLTIME_WR_BASELINE = _cfg_data_proj.get("wr_baselines", {})
    _GO_LIVE_DATES = _cfg_data_proj.get("go_live_dates", {})
    _CRITICAL_PATH = _cfg_data_proj.get("critical_path", {})
    _NEXT_EXPERIMENT = _cfg_data_proj.get("next_experiment", {})
except Exception:
    _ALLTIME_WR_BASELINE = {}
    _GO_LIVE_DATES = {}
    _CRITICAL_PATH = {}
    _NEXT_EXPERIMENT = {}  # ponytail: safe empty defaults; populate in .wulong/projects.json overlay



def _parse_wr(state_content):
    """Extract win rate (last 200) as a float from live-state content. Returns None if not found."""
    # Match e.g. **Win rate (last 200):** 49.5%  or  **Win rate:** 54.1%
    # The bold marker closing **: sits right before the colon, so we match Win rate ... **: value%
    m = re.search(r"Win rate[^:]*:\**\s*([\d.]+)%", state_content)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _parse_pnl(state_content):
    """Extract PnL as float (positive or negative) from live-state content."""
    m = re.search(r"\*\*Paper PnL\*\*:\s*\$([+-]?[\d,]+\.?\d*)", state_content)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _parse_process(state_content):
    """Extract process status string from live-state content."""
    m = re.search(r"\*\*Process\*\*:\s*([^\n]+)", state_content)
    return m.group(1).strip() if m else ""


def build_situation_assessment(pname, state_content, today_str):
    """
    Generate a ## Situation Assessment block for a single project.
    Derived purely from live-state data and the static tables above.
    """
    lines = ["## Situation Assessment", ""]

    last200_wr = _parse_wr(state_content)
    alltime_wr = _ALLTIME_WR_BASELINE.get(pname)
    pnl = _parse_pnl(state_content)
    process = _parse_process(state_content)

    # 1. WR trend
    if last200_wr is None:
        wr_trend = "N/A — no WR data in live-state file"
    elif alltime_wr is None:
        wr_trend = f"Last-200 WR: {last200_wr:.1f}% — no reliable all-time baseline yet (early stage)"
    else:
        diff = last200_wr - alltime_wr
        if diff < -3:
            wr_trend = (
                f"Last-200 WR {last200_wr:.1f}% vs all-time {alltime_wr:.1f}% "
                f"({diff:+.1f}pp) — POSSIBLE DRIFT: investigate recent bet segments"
            )
        elif diff > 3:
            wr_trend = (
                f"Last-200 WR {last200_wr:.1f}% vs all-time {alltime_wr:.1f}% "
                f"({diff:+.1f}pp) — POSSIBLE POSITIVE MOMENTUM: confirm sample is large enough"
            )
        else:
            wr_trend = (
                f"Last-200 WR {last200_wr:.1f}% vs all-time {alltime_wr:.1f}% "
                f"({diff:+.1f}pp) — stable, within ±3pp band"
            )
    lines.append(f"- **WR trend:** {wr_trend}")

    # 2. Anomaly flag
    anomalies = []
    if pnl is not None and pnl < 0:
        anomalies.append(f"PnL is negative (${pnl:+.2f})")
    if last200_wr is not None and last200_wr < 48.0:
        anomalies.append(f"WR {last200_wr:.1f}% below 48% alert threshold")
    proc_lower = process.lower()
    if "stale" in proc_lower or ("not running" in proc_lower):
        anomalies.append(f"Process appears stale or not running: {process}")
    if not anomalies:
        anomaly_str = "None detected"
    else:
        anomaly_str = "; ".join(anomalies)
    lines.append(f"- **Anomaly flag:** {anomaly_str}")

    # 3. Experiment status
    lines.append(f"- **Experiment status:** {_NEXT_EXPERIMENT.get(pname, 'See brain.md')}")

    # 4. Critical path
    lines.append(f"- **Critical path:** {_CRITICAL_PATH.get(pname, 'See brain.md for P-items')}")

    # 5. Risk flag
    risk_parts = []
    go_live = _GO_LIVE_DATES.get(pname)
    if go_live:
        try:
            today = datetime.strptime(today_str, "%Y-%m-%d")
            target = datetime.strptime(go_live, "%Y-%m-%d")
            days_left = (target - today).days
            if days_left >= 0:
                risk_parts.append(f"{days_left} days until go-live ({go_live})")
            else:
                risk_parts.append(f"Go-live date {go_live} has passed — confirm live mode is active")
        except ValueError:
            risk_parts.append(f"Go-live: {go_live}")
    _proj_risk = _CRITICAL_PATH.get(pname, "")
    if _proj_risk:
        risk_parts.append(_proj_risk)
    # ponytail: project-specific risk flags belong in .wulong/projects.json overlay risk_flags key
    if not risk_parts:
        risk_parts.append("None identified")
    lines.append(f"- **Risk flag:** {'; '.join(risk_parts)}")

    lines.append("")
    return "\n".join(lines)


def build_phantom_troupe(brain, live_states, agent_messages, approval_queue):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pending_msgs = get_pending_messages(agent_messages, TRADING_AGENTS)
    pending_apq = get_pending_approvals(approval_queue)

    open_threads = extract_brain_section(brain, "Open Threads")
    recent_decisions = extract_brain_section(brain, "Recent Decisions")
    trading_status = extract_brain_section(brain, "Trading Systems Status")

    project_order = [os.path.splitext(os.path.basename(p))[0] for p in sorted(os.listdir(LIVE_STATE_DIR)) if p.endswith(".md")] if os.path.isdir(LIVE_STATE_DIR) else []

    sections = [
        f"<!-- Generated: {now} — do not edit manually -->",
        f"",
        f"# Trading Context — {now}",
        f"",
        f"This file is your pre-compiled knowledge base. Read it before any trading task.",
        f"Do NOT read brain.md directly for current metrics — this file has fresher data.",
        f"Note: Previously named phantom-troupe.md. Phantom Troupe dissolved 2026-05-15 — work distributed across Architecture+R&D/Tech/Delivery+QA.",
        f"",
        f"---",
        f"",
        f"## Live Project State",
        f"",
    ]

    for pname in project_order:
        state = live_states.get(pname, "")
        if state:
            # Strip frontmatter and generation comment
            clean = re.sub(r"^---.*?---\s*", "", state, flags=re.DOTALL)
            clean = re.sub(r"<!-- Generated.*?-->\s*", "", clean)
            sections.append(clean.strip())
            sections.append("")
            # Inject situation assessment after each project's raw metrics block
            sections.append(build_situation_assessment(pname, state, today_str))
        else:
            sections.append(f"### {pname}\n⚠️ No live-state file found — run Meta/sync/vps-sync.py\n")
            sections.append(build_situation_assessment(pname, "", today_str))

    if pending_apq:
        sections += [
            f"---",
            f"",
            f"## Pending Approvals (APQ)",
            f"",
        ]
        sections += [f"- {line}" for line in pending_apq]
        sections.append("")

    if pending_msgs:
        sections += [
            f"---",
            f"",
            f"## Pending Agent Messages",
            f"",
        ]
        for msg in pending_msgs[:5]:
            sections.append(msg[:300] + ("..." if len(msg) > 300 else ""))
            sections.append("")

    if open_threads:
        sections += [
            f"---",
            f"",
            f"## Open Threads (from brain.md)",
            f"",
            open_threads[:1000],
            f"",
        ]

    if recent_decisions:
        sections += [
            f"---",
            f"",
            f"## Recent Decisions",
            f"",
            recent_decisions[:800],
            f"",
        ]

    # Inject Recent User Decisions from company-registry.md
    registry_decisions = get_registry_decisions(VAULT_ROOT)
    if registry_decisions:
        sections += [
            f"---",
            f"",
            f"## Recent User Decisions (CEO → Agents)",
            f"",
            registry_decisions[:1200],
            f"",
        ]

    # Inject cross-agent activity from KB files (last 5 entries per agent)
    cross_agent_agents = [
        ("mastermind", 5),
        ("analyst", 5),
        ("coder", 5),
        ("deployer", 5),
    ]
    cross_section = build_cross_agent_activity(cross_agent_agents)
    sections += [
        f"---",
        f"",
        cross_section,
    ]

    return "\n".join(sections)


def build_keepers(brain, agent_messages, latest_session):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pending_msgs = get_pending_messages(agent_messages, KEEPER_AGENTS)
    inbox_n = inbox_count()

    session_what = extract_brain_section(latest_session, "What happened this session")
    session_open = extract_brain_section(latest_session, "Open threads")
    session_files = extract_brain_section(latest_session, "Files changed")

    sections = [
        f"<!-- Generated: {now} — do not edit manually -->",
        f"",
        f"# Keepers Context — {now}",
        f"",
        f"This file is your pre-compiled vault state. Read it before any vault task.",
        f"",
        f"---",
        f"",
        f"## Vault Health Snapshot",
        f"",
        f"**Inbox items:** {inbox_n} files in 00-Inbox/",
        f"",
    ]

    if session_files:
        sections += [
            f"**Files changed last session:**",
            session_files[:500],
            f"",
        ]

    if session_what:
        sections += [
            f"---",
            f"",
            f"## Last Session Summary",
            f"",
            session_what[:600],
            f"",
        ]

    if session_open:
        sections += [
            f"---",
            f"",
            f"## Open Threads from Last Session",
            f"",
            session_open[:400],
            f"",
        ]

    if pending_msgs:
        sections += [
            f"---",
            f"",
            f"## Pending Messages for Keepers",
            f"",
        ]
        for msg in pending_msgs[:5]:
            sections.append(msg[:300] + ("..." if len(msg) > 300 else ""))
            sections.append("")

    # Inject cross-agent activity from KB files (last 5 entries per keeper sub-agent)
    cross_agent_agents = [
        ("sorter", 5),
        ("scribe", 5),
        ("connector", 5),
        ("librarian", 5),
    ]
    cross_section = build_cross_agent_activity(cross_agent_agents)
    sections += [
        f"---",
        f"",
        cross_section,
    ]

    return "\n".join(sections)


def build_cron_health():
    """
    Build a CRON HEALTH section by inspecting recent mtimes of expected outputs.
    Returns a tuple (section_str, has_overdue: bool).
    """
    now_ts = datetime.now().timestamp()
    # (label, path_relative_to_META, expected_max_age_minutes)
    jobs = [
        ("compile-context", os.path.join(CONTEXT_DIR, "jarvis.md"), 20),
        ("vps-sync (my_trader)", os.path.join(LIVE_STATE_DIR, "my_trader.md"), 30),
        ("vps-sync (weather)", os.path.join(LIVE_STATE_DIR, "my_trader.md"), 30),
        ("doctor health-check", os.path.join(META, "doctor", "health-history.md"), 60 * 24),
    ]
    rows = []
    overdue = []
    for label, path, max_age_min in jobs:
        try:
            mtime = os.path.getmtime(path)
            age_min = (now_ts - mtime) / 60.0
            if age_min <= max_age_min:
                status = "GREEN"
            else:
                status = "RED"
                overdue.append(f"{label} ({age_min:.0f} min overdue)")
            rows.append(f"| {label} | {age_min:.0f} min ago | {status} |")
        except FileNotFoundError:
            rows.append(f"| {label} | never | RED |")
            overdue.append(f"{label} (file missing)")

    lines = []
    if overdue:
        lines.append(f"**CRON OVERDUE WARNING:** {'; '.join(overdue)}")
        lines.append("")
    lines.append("## Cron Health")
    lines.append("")
    lines.append("| Job | Last fired | Status |")
    lines.append("|-----|-----------|--------|")
    lines.extend(rows)
    lines.append("")
    return "\n".join(lines), bool(overdue)


def build_jarvis(brain, live_states, agent_messages, approval_queue, latest_session):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pending_apq = get_pending_approvals(approval_queue)
    open_threads = extract_brain_section(brain, "Open Threads")
    life_focus = extract_brain_section(brain, "Current Focus")
    recent_decisions = extract_brain_section(brain, "Recent Decisions")

    session_what = extract_brain_section(latest_session, "What happened this session")

    project_order = [os.path.splitext(os.path.basename(p))[0] for p in sorted(os.listdir(LIVE_STATE_DIR)) if p.endswith(".md")] if os.path.isdir(LIVE_STATE_DIR) else []

    cron_section, cron_overdue = build_cron_health()

    sections = [
        f"<!-- Generated: {now} — do not edit manually -->",
        f"",
        f"# Jarvis Context — {now}",
        f"",
        f"This file is your pre-compiled chief-of-staff briefing. Read it first.",
        f"",
        f"---",
        f"",
    ]

    # In-flight ledger block — read FIRST (v33-1-amnesia-fix)
    try:
        _inflight_path = os.path.join(VAULT_ROOT, "Meta", "state", "in-flight.md")
        with open(_inflight_path, "r", encoding="utf-8") as _f:
            _inflight_text = _f.read()
        # Extract ACTIVE WORK and OPEN QUESTIONS sections
        _active_block = ""
        _open_block = ""
        _lines = _inflight_text.splitlines()
        _cur = None
        _buf: list[str] = []
        for _line in _lines:
            if _line.strip() == "## ACTIVE WORK":
                _cur = "active"; _buf = []
            elif _line.strip() == "## DECISIONS":
                if _cur == "active":
                    _active_block = "\n".join(_buf).strip()
                _cur = "decisions"; _buf = []
            elif _line.strip() == "## OPEN QUESTIONS":
                if _cur == "decisions":
                    pass
                _cur = "open"; _buf = []
            elif _cur is not None:
                _buf.append(_line)
        if _cur == "open":
            _open_block = "\n".join(_buf).strip()
        sections += [
            f"## In-Flight Work",
            f"",
            _active_block if _active_block else "(no active work rows)",
            f"",
            f"**Open questions:** {_open_block if _open_block else 'none'}",
            f"",
            f"---",
            f"",
        ]
    except Exception as _e:
        import warnings as _w
        _w.warn(f"[compile-context] in-flight ledger unavailable: {_e}")
        sections += [
            f"## In-Flight Work",
            f"",
            f"(in-flight ledger unavailable)",
            f"",
            f"---",
            f"",
        ]

    # OBSERVE-pass checklist banner — instructional, clears once both notebooks touched
    observe_banner = get_observe_pass_banner()
    if observe_banner:
        sections += [
            f"# Session-start checklist",
            f"",
            f"{observe_banner}",
            f"",
            f"---",
            f"",
        ]

    # Doctor inbox banner — RED findings from health-scan.py
    inbox_count_red, inbox_paths = get_doctor_inbox_status()
    if inbox_count_red > 0:
        sections += [
            f"# 🚨 Doctor inbox: {inbox_count_red} pending items",
            f"",
            f"Unread RED findings in Meta/inbox/. Surface to user and offer to dispatch fixes via Task().",
            f"Files:",
        ]
        for p in inbox_paths:
            sections.append(f"  - {os.path.relpath(p, VAULT_ROOT)}")
        sections += [f"", f"---", f""]

    # AR Director proposals banner
    prop_count = get_proposals_count()
    if prop_count > 0:
        sections += [
            f"# 📋 AR Director review queue: {prop_count} items",
            f"",
            f"Pending proposals in Meta/proposals/. Offer to dispatch AR Director via Task() to process.",
            f"",
            f"---",
            f"",
        ]

    if cron_overdue:
        sections += [
            f"# ⚠️ CRON OVERDUE — INVESTIGATE BEFORE TRUSTING METRICS BELOW",
            f"",
        ]
    sections += [
        cron_section,
        f"---",
        f"",
    ]

    if life_focus:
        sections += [
            f"## Current Life Focus",
            f"",
            life_focus[:600],
            f"",
            f"---",
            f"",
        ]

    if open_threads:
        sections += [
            f"## Open Threads",
            f"",
            open_threads[:800],
            f"",
            f"---",
            f"",
        ]

    sections += [
        f"## Trading Systems Summary",
        f"",
    ]

    for pname in project_order:
        state = live_states.get(pname, "")
        if state:
            wr_m = re.search(r"\*\*Win rate[^*]*\*\*:\s*([^\n]+)", state)
            pnl_m = re.search(r"\*\*Paper PnL\*\*:\s*([^\n]+)", state)
            commit_m = re.search(r"\*\*Last commit\*\*:\s*([^\n]+)", state)
            proc_m = re.search(r"\*\*Process\*\*:\s*([^\n]+)", state)
            wr = wr_m.group(1) if wr_m else "N/A"
            pnl = pnl_m.group(1) if pnl_m else "N/A"
            commit = commit_m.group(1)[:60] if commit_m else "N/A"
            proc = proc_m.group(1) if proc_m else "N/A"
            sections.append(f"**{pname}** — WR: {wr} | PnL: {pnl} | Process: {proc}")
        else:
            sections.append(f"**{pname}** — ⚠️ No sync data")

    sections.append("")
    sections.append("---")
    sections.append("")

    if pending_apq:
        sections += [
            f"## Pending Approvals — CEO Action Required",
            f"",
        ]
        sections += [f"- {line}" for line in pending_apq]
        sections.append("")
        sections.append("---")
        sections.append("")

    if session_what:
        sections += [
            f"## Last Session",
            f"",
            session_what[:400],
            f"",
            f"---",
            f"",
        ]

    if recent_decisions:
        sections += [
            f"## Recent Decisions",
            f"",
            recent_decisions[:600],
            f"",
        ]

    # Inject cross-agent activity from ALL agent KBs (last 3 entries each)
    all_agents = [
        ("jarvis", 3),
        # ("phantom-troupe", 3) removed 2026-05-21 — coordinator dissolved 2026-05-15
        ("mastermind", 3),
        ("analyst", 3),
        ("coder", 3),
        ("deployer", 3),
        ("keepers", 3),
        ("sorter", 3),
        ("scribe", 3),
        ("connector", 3),
        ("librarian", 3),
        ("company-orchestrator", 3),
        ("financial-manager", 3),
        ("doctor", 3),
    ]
    cross_section = build_cross_agent_activity(all_agents)
    sections += [
        f"---",
        f"",
        cross_section,
    ]

    return "\n".join(sections)


def get_health_history(vault_root):
    path = os.path.join(vault_root, "Meta", "doctor", "health-history.md")
    return read_file(path)


def parse_health_history_rows(history_content, n=10):
    """Extract the last N data rows from the health-history table (skip headers, separators, comments, placeholders)."""
    rows = []
    for line in history_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("#"):
            continue
        if "|" not in stripped:
            continue
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) < 4:
            continue
        date_col = parts[0]
        if "----" in date_col or date_col in ("Date (SGT)", "---", "—", ""):
            continue
        if date_col.startswith("—") or "No runs" in date_col:
            continue
        rows.append(stripped)
    return rows[-n:]


def build_doctor(live_states, agent_messages, health_history):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pending_msgs = get_pending_messages(agent_messages, DOCTOR_AGENTS)
    history_rows = parse_health_history_rows(health_history, n=10)

    project_order = [os.path.splitext(os.path.basename(p))[0] for p in sorted(os.listdir(LIVE_STATE_DIR)) if p.endswith(".md")] if os.path.isdir(LIVE_STATE_DIR) else []

    sections = [
        f"<!-- Generated: {now} — do not edit manually -->",
        f"",
        f"# Doctor Context — {now}",
        f"",
        f"This file is your pre-compiled health briefing. Read it before running any audit.",
        f"",
        f"---",
        f"",
        f"## Health Trend (last 10 runs)",
        f"",
        f"| Date (SGT) | Score | Band | Top Issue |",
        f"|------------|-------|------|-----------|",
    ]

    if history_rows:
        sections += history_rows
    else:
        sections.append("| — | — | — | No runs yet |")

    sections += [
        f"",
        f"---",
        f"",
        f"## Current System Snapshot",
        f"",
    ]

    for pname in project_order:
        state = live_states.get(pname, "")
        if state:
            wr_m = re.search(r"\*\*Win rate[^*]*\*\*:\s*([^\n]+)", state)
            pnl_m = re.search(r"\*\*Paper PnL\*\*:\s*([^\n]+)", state)
            proc_m = re.search(r"\*\*Process\*\*:\s*([^\n]+)", state)
            wr = wr_m.group(1).strip() if wr_m else "N/A"
            pnl = pnl_m.group(1).strip() if pnl_m else "N/A"
            proc = proc_m.group(1).strip() if proc_m else "N/A"
            sections.append(f"**{pname}:** {proc} | WR: {wr} | PnL: {pnl}")
        else:
            sections.append(f"**{pname}:** ⚠️ No sync data — run Meta/sync/vps-sync.py")

    sections.append("")

    if pending_msgs:
        sections += [
            f"---",
            f"",
            f"## Pending Messages for Doctor",
            f"",
        ]
        for msg in pending_msgs[:5]:
            sections.append(msg[:300] + ("..." if len(msg) > 300 else ""))
            sections.append("")
    else:
        sections += [
            f"---",
            f"",
            f"## Pending Messages for Doctor",
            f"",
            f"None.",
            f"",
        ]

    last_row = history_rows[-1] if history_rows else None
    sections += [
        f"---",
        f"",
        f"## Last Health Check",
        f"",
    ]
    if last_row:
        parts = [p.strip() for p in last_row.strip("|").split("|")]
        if len(parts) >= 4:
            sections.append(f"Score: {parts[1]} — {parts[2]} — {parts[3]}")
        else:
            sections.append(last_row)
    else:
        sections.append("No prior health check found. This is the first run.")

    sections.append("")

    return "\n".join(sections)


# ponytail: GitHub project map from overlay config; empty default = no GitHub fetch
_wulong_root = os.environ.get("WULONG_ROOT", VAULT_ROOT)
_projects_cfg = os.path.join(_wulong_root, ".wulong", "projects.json")
try:
    _cfg_data = _json.loads(open(_projects_cfg).read())
    _GITHUB_PROJECTS = _cfg_data.get("github_projects", {})
except Exception:
    _GITHUB_PROJECTS = {}  # ponytail: safe empty default; add .wulong/projects.json in overlay


def _json_to_live_state_md(data: dict) -> str:
    """Convert a live-state.json dict to the .md format expected by compile-context."""
    project   = data.get("project", "unknown")
    pushed_at = data.get("pushed_at", "")
    git_hash  = data.get("git_hash", "")
    git_msg   = data.get("git_message", "")
    git_ts    = data.get("git_timestamp", "")
    process   = data.get("process_status", "cron")
    total     = data.get("bets_total", 0)
    settled   = data.get("bets_settled", 0)
    wr        = data.get("win_rate_last200")
    pnl       = data.get("pnl_total")
    last_bet  = data.get("last_bet_time", "")

    wr_str  = f"{wr}%" if wr is not None else "N/A"
    pnl_str = (f"$+{pnl:.2f}" if pnl is not None and pnl >= 0 else f"$-{abs(pnl):.2f}") if pnl is not None else "N/A"

    lines = [
        f"---",
        f"project: {project}",
        f"synced: {pushed_at}",
        f"---",
        f"",
        f"<!-- Generated by compile-context.py (GitHub HTTPS fetch) — do not edit manually -->",
        f"",
        f"## Live State — {project}",
        f"",
        f"**Process:** CRON ({process})",
        f"**Last commit:** `{git_hash}` — \"{git_msg}\" — {git_ts}",
        f"**Bets (all-time):** {total} ({settled} settled)",
        f"**Win rate (last 200):** {wr_str}",
        f"**Paper PnL:** {pnl_str}",
        f"**Last bet placed:** {last_bet}",
        f"",
        f"## Last Sync",
        f"",
        f"Successfully synced at {pushed_at}.",
        f"",
    ]
    return "\n".join(lines)


def fetch_live_states_from_github() -> None:
    """
    Fetch live-state.json from GitHub raw URLs for each project and write to
    Meta/live-state/ in the existing .md format.  Falls back silently on any
    error — never crashes compile-context if GitHub is unreachable.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    os.makedirs(LIVE_STATE_DIR, exist_ok=True)

    for file_slug, repo in _GITHUB_PROJECTS.items():
        url = f"https://raw.githubusercontent.com/{repo}/main/live-state.json"
        try:
            req = urllib.request.Request(url)
            if token:
                req.add_header("Authorization", f"token {token}")
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
            data = _json.loads(raw)
            md   = _json_to_live_state_md(data)
            target = os.path.join(LIVE_STATE_DIR, f"{file_slug}.md")
            tmp    = target + ".tmp"
            with open(tmp, "w") as f:
                f.write(md)
            os.rename(tmp, target)
        except Exception:
            pass


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] compile-context.py starting...")

    # Check if any high-impact files were recently changed — print notice for agents
    recently_changed = check_high_impact_changes()
    if recently_changed:
        print(f"  HIGH_IMPACT changes detected (last 120s): {', '.join(recently_changed)}")
        print(f"  Forced recompile triggered by high-impact file change.")

    fetch_live_states_from_github()

    brain = read_file(os.path.join(META, "brain.md"))
    agent_messages = read_file(os.path.join(META, "agent-messages.md"))
    approval_queue = read_file(os.path.join(META, "approval-queue.md"))
    live_states = get_live_state_files()
    latest_session = get_latest_session()
    health_history = get_health_history(VAULT_ROOT)

    phantom = build_phantom_troupe(brain, live_states, agent_messages, approval_queue)
    write_context("trading.md", phantom)
    print("  trading.md written (was phantom-troupe.md)")

    keepers = build_keepers(brain, agent_messages, latest_session)
    write_context("keepers.md", keepers)
    print("  keepers.md written")

    jarvis = build_jarvis(brain, live_states, agent_messages, approval_queue, latest_session)
    write_context("jarvis.md", jarvis)
    print("  jarvis.md written")

    doctor = build_doctor(live_states, agent_messages, health_history)
    write_context("doctor.md", doctor)
    print("  doctor.md written")

    # Per-agent context files for Task-enabled coordinators
    # Map each coordinator to its primary team-context excerpt
    team_excerpt_map = {
        "jarvis": jarvis,
        "company-orchestrator": phantom,  # trading.md
        "ar-director": jarvis,            # AR Director needs Jarvis's overview
        "doctor": doctor,
        "keepers": keepers,
    }
    for agent in TASK_ENABLED_COORDINATORS:
        try:
            per_agent = build_per_agent_context(agent, team_excerpt_map.get(agent, ""))
            write_context(f"{agent}.md" if agent in ("jarvis", "keepers", "doctor") else f"{agent}.md",
                          per_agent) if False else None
            # The team file for jarvis/keepers/doctor already lives at Meta/context/<agent>.md.
            # To avoid clobbering team files with per-agent slim files, write per-agent variants
            # under a distinct name: <agent>-agent.md  for jarvis/keepers/doctor; otherwise <agent>.md.
            if agent in ("jarvis", "keepers", "doctor"):
                write_context(f"{agent}-agent.md", per_agent)
                print(f"  {agent}-agent.md written (per-agent context)")
            else:
                write_context(f"{agent}.md", per_agent)
                print(f"  {agent}.md written (per-agent context)")
        except Exception as e:
            print(f"  [per-agent] {agent} failed: {e}", file=sys.stderr)

    print(f"Done. Context files: {CONTEXT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
