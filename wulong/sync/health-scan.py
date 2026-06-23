#!/usr/bin/env python3
"""
health-scan.py — Pure-Python deterministic health checks. No LLM calls.

Runs from the existing 15-min compile-context launchd chain (or standalone).

Checks performed:
  1. Cron freshness — WULONG_LAUNCHD_LOG_DIR/*.log mtimes (>2h stale = RED)
  2. VPS health — Meta/live-state/<project>.md (missing or >30min stale = RED)
  3. Agent KB staleness — Meta/knowledge-base/*.md mtime >7d when expected active = YELLOW
  4. Broken handoffs — Meta/handoffs/*.md (excl. archive/) >48h old = YELLOW
  5. Stale ⏳ messages — Meta/agent-messages.md ⏳ entries >24h old = YELLOW
  6. Dashboard data integrity — curl dashboard pages; compare numeric data to local state = RED on mismatch

Output:
  Writes RED/YELLOW findings to Meta/inbox/doctor-pending-YYYY-MM-DD.md (replace mode —
  each scan overwrites the file with the current set of findings; no intra-day history).
  To avoid invalidating banner read markers on unchanged findings, the file is only
  rewritten when the new findings differ from existing content.
"""

import glob
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
META = os.path.join(VAULT_ROOT, "Meta")
LIVE_STATE_DIR = os.path.join(META, "live-state")
KB_DIR = os.path.join(META, "knowledge-base")
HANDOFFS_DIR = os.path.join(META, "handoffs")
INBOX_DIR = os.path.join(META, "inbox")
AGENT_MESSAGES = os.path.join(META, "agent-messages.md")
LAUNCHD_LOG_DIR = os.environ.get("WULONG_LAUNCHD_LOG_DIR", os.path.expanduser("~/Library/Logs/com.wulong"))  # ponytail: env knob; set WULONG_LAUNCHD_LOG_DIR in overlay

DASHBOARD_BASE = os.environ.get("WULONG_DASHBOARD_BASE", "")  # ponytail: env knob; set in overlay to enable dashboard check
# ponytail: project list from overlay config; empty default = no dashboard project checks
_wulong_root = os.environ.get("WULONG_ROOT", VAULT_ROOT)
_projects_cfg = os.path.join(_wulong_root, ".wulong", "projects.json")
try:
    import json as _json
    DASHBOARD_PROJECTS = _json.loads(open(_projects_cfg).read()).get("projects", [])
except Exception:
    DASHBOARD_PROJECTS = []  # ponytail: safe empty default; add .wulong/projects.json in overlay

# Active projects whose KB should be fresh (<=7d) when active
ACTIVE_KB_AGENTS = {"jarvis", "company-orchestrator", "ar-director", "doctor", "keepers",
                    "mastermind", "coder", "deployer", "contrarian", "tester"}


def now():
    return datetime.now(timezone.utc)


def file_age_seconds(path):
    try:
        return (now().timestamp() - os.path.getmtime(path))
    except FileNotFoundError:
        return None


def fmt_age(seconds):
    if seconds is None:
        return "missing"
    h = seconds / 3600.0
    if h < 1:
        return f"{seconds/60:.0f}min"
    if h < 48:
        return f"{h:.1f}h"
    return f"{h/24:.1f}d"


# ---------------------------------------------------------------------------
# Check 1: Cron freshness
# ---------------------------------------------------------------------------
def check_cron_freshness():
    findings = []
    if not os.path.isdir(LAUNCHD_LOG_DIR):
        findings.append(("RED", "cron-freshness", f"launchd log dir missing: {LAUNCHD_LOG_DIR}"))
        return findings
    logs = glob.glob(os.path.join(LAUNCHD_LOG_DIR, "*.log"))
    if not logs:
        findings.append(("YELLOW", "cron-freshness", "no *.log files in launchd log dir"))
        return findings
    for log in logs:
        age = file_age_seconds(log)
        if age is not None and age > 2 * 3600:
            findings.append((
                "RED",
                "cron-freshness",
                f"{os.path.basename(log)} stale ({fmt_age(age)} since last write, >2h threshold)",
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 2: VPS health (live-state files)
# ---------------------------------------------------------------------------
def check_vps_health():
    findings = []
    # ponytail: live-state file list from overlay config; empty default = no VPS checks
    _root = os.environ.get("WULONG_ROOT", VAULT_ROOT)
    _cfg = os.path.join(_root, ".wulong", "projects.json")
    try:
        import json as _j
        _plist = _j.loads(open(_cfg).read()).get("projects", [])
        expected = [f"{p}.md" for p in _plist]
    except Exception:
        expected = []
    for name in expected:
        path = os.path.join(LIVE_STATE_DIR, name)
        age = file_age_seconds(path)
        if age is None:
            findings.append(("RED", "vps-health", f"live-state file missing: {name}"))
        elif age > 30 * 60:
            findings.append((
                "RED",
                "vps-health",
                f"{name} stale ({fmt_age(age)} since last sync, >30min threshold)",
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 3: Agent KB staleness
# ---------------------------------------------------------------------------
def check_kb_staleness():
    findings = []
    for agent in ACTIVE_KB_AGENTS:
        path = os.path.join(KB_DIR, f"{agent}.md")
        age = file_age_seconds(path)
        if age is None:
            continue  # KB may not exist for all agents
        if age > 7 * 86400:
            findings.append((
                "YELLOW",
                "kb-staleness",
                f"{agent} KB not updated in {fmt_age(age)} (active agent)",
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 4: Broken handoffs
# ---------------------------------------------------------------------------
def check_broken_handoffs():
    findings = []
    if not os.path.isdir(HANDOFFS_DIR):
        return findings
    for path in glob.glob(os.path.join(HANDOFFS_DIR, "*.md")):
        if os.path.basename(path) == "README.md":
            continue
        # Skip archive/
        if os.sep + "archive" + os.sep in path:
            continue
        age = file_age_seconds(path)
        if age is not None and age > 48 * 3600:
            findings.append((
                "YELLOW",
                "broken-handoff",
                f"{os.path.basename(path)} unarchived after {fmt_age(age)} (>48h)",
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 5: Stale pending messages
# ---------------------------------------------------------------------------
def check_stale_messages():
    findings = []
    try:
        with open(AGENT_MESSAGES) as f:
            content = f.read()
    except FileNotFoundError:
        return findings

    # Split on --- block delimiters
    blocks = re.split(r"\n---\n", content)
    threshold = now() - timedelta(hours=24)
    for block in blocks:
        if "⏳" not in block:
            continue
        # Find timestamp at start, e.g. "## [2026-05-20 14:00]"
        m = re.search(r"\[(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})\]", block)
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
            ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < threshold:
            # Extract subject for clarity
            sub_m = re.search(r"\*\*Subject\*\*:\s*(.+)", block)
            subject = sub_m.group(1).strip()[:80] if sub_m else "(no subject)"
            age_h = (now() - ts).total_seconds() / 3600.0
            findings.append((
                "YELLOW",
                "stale-message",
                f"⏳ pending {age_h:.0f}h: [{m.group(0)}] {subject}",
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 6: Dashboard data integrity
# ---------------------------------------------------------------------------
def _fetch_url(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "health-scan/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


def _extract_live_state_metrics(path):
    """Pull WR% and PnL$ from a live-state markdown file. Returns dict with optional
    'wr' (float, percent) and 'pnl' (float, USD). Missing keys = data absent."""
    metrics = {}
    try:
        with open(path) as f:
            content = f.read()
    except FileNotFoundError:
        return metrics
    # Win rate: "**Win rate (last 200):** 71.4%" — note markdown bold markers
    # after the colon. Allow optional `**` and whitespace before the value.
    m = re.search(r"Win rate[^\n]*?:\s*\**\s*([+-]?\d+(?:\.\d+)?)\s*%", content)
    if m:
        metrics["wr"] = float(m.group(1))
    # PnL: "**Paper PnL:** $-41.19"
    m = re.search(r"Paper PnL:\s*\**\s*\$([+-]?[\d,]+(?:\.\d+)?)", content)
    if m:
        metrics["pnl"] = float(m.group(1).replace(",", ""))
    return metrics


def check_dashboard_integrity():
    """Compare live-state numerics against the canonical summary.json the dashboard SPA
    consumes. The HTML shells are client-rendered (Chart.js etc.) and never contain the
    numbers — we must read the underlying data file instead.

    Behaviour:
      - HTML shell: HTTP-reachability check only (200 OK). HTTP error → RED.
      - summary.json: required; if missing/parse-fail → RED.
      - Per project, if both sides expose a comparable field (wr, pnl), drift beyond
        tolerance → RED. If the dashboard has no entry for a project that live-state
        DOES have data for → YELLOW (honest gap, not a sync break).
    """
    findings = []

    # 1) HTML shell reachability — keeps the original "is the site up" signal.
    status, body = _fetch_url(DASHBOARD_BASE)
    if status is None:
        findings.append(("RED", "dashboard", f"home page unreachable: {body}"))
        return findings
    if status != 200:
        findings.append(("RED", "dashboard", f"home page HTTP {status}"))

    # 2) Canonical data file the SPA loads.
    summary_url = f"{DASHBOARD_BASE}summary.json"
    status, body = _fetch_url(summary_url)
    if status is None:
        findings.append(("RED", "dashboard", f"summary.json unreachable: {body}"))
        return findings
    if status != 200:
        findings.append(("RED", "dashboard", f"summary.json HTTP {status}"))
        return findings
    try:
        summary = json.loads(body)
    except json.JSONDecodeError as e:
        findings.append(("RED", "dashboard", f"summary.json parse error: {e}"))
        return findings

    dash_projects = summary.get("projects", {}) or {}

    # 3) Per-project comparison.
    WR_TOL = 0.5   # percent
    PNL_TOL = 1.0  # USD
    for proj in DASHBOARD_PROJECTS:
        ls_path = os.path.join(LIVE_STATE_DIR, f"{proj.replace('_','-')}.md")
        local = _extract_live_state_metrics(ls_path)
        dash = dash_projects.get(proj)

        if dash is None:
            if local:
                findings.append((
                    "YELLOW",
                    "dashboard",
                    f"{proj} missing from summary.json (live-state has data) — honest gap",
                ))
            continue

        # WR comparison — only if both sides have a non-null value.
        dash_wr = dash.get("win_rate")
        if "wr" in local and dash_wr is not None:
            if abs(float(dash_wr) - local["wr"]) > WR_TOL:
                findings.append((
                    "RED",
                    "dashboard",
                    f"{proj} WR drift: dashboard {dash_wr}% vs live-state {local['wr']}%",
                ))
        elif "wr" in local and dash_wr is None:
            findings.append((
                "YELLOW",
                "dashboard",
                f"{proj} summary.json win_rate is null but live-state has {local['wr']}%",
            ))

        # PnL comparison.
        dash_pnl = dash.get("pnl")
        if "pnl" in local and dash_pnl is not None:
            if abs(float(dash_pnl) - local["pnl"]) > PNL_TOL:
                findings.append((
                    "RED",
                    "dashboard",
                    f"{proj} PnL drift: dashboard ${dash_pnl} vs live-state ${local['pnl']}",
                ))
        elif "pnl" in local and dash_pnl is None:
            findings.append((
                "YELLOW",
                "dashboard",
                f"{proj} summary.json pnl is null but live-state has ${local['pnl']}",
            ))

    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_findings(all_findings):
    if not all_findings:
        print("[health-scan] no findings — system clean")
        return None
    os.makedirs(INBOX_DIR, exist_ok=True)
    date_str = now().strftime("%Y-%m-%d")
    out_path = os.path.join(INBOX_DIR, f"doctor-pending-{date_str}.md")
    ts = now().strftime("%H:%M UTC")
    reds = [f for f in all_findings if f[0] == "RED"]
    yellows = [f for f in all_findings if f[0] == "YELLOW"]

    # Build findings body (excludes scan timestamp so unchanged findings = unchanged content)
    findings_lines = []
    if reds:
        findings_lines.append("### RED")
        for sev, kind, msg in reds:
            findings_lines.append(f"- [{kind}] {msg}")
        findings_lines.append("")
    if yellows:
        findings_lines.append("### YELLOW")
        for sev, kind, msg in yellows:
            findings_lines.append(f"- [{kind}] {msg}")
        findings_lines.append("")
    findings_body = "\n".join(findings_lines)

    # Compare against existing file body (skip rewrite if findings unchanged → preserve read markers)
    existing_body = None
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                existing = f.read()
            m = re.search(r"<!-- FINDINGS_START -->\n([\s\S]*?)<!-- FINDINGS_END -->", existing)
            if m:
                existing_body = m.group(1)
        except OSError:
            pass

    if existing_body is not None and existing_body.strip() == findings_body.strip():
        print(f"[health-scan] findings unchanged ({len(reds)} RED + {len(yellows)} YELLOW) — skipping rewrite")
        return out_path

    # Replace file contents with current scan only
    header = (
        f"# Doctor Pending — {date_str}\n\n"
        f"<!-- Rewritten by Meta/sync/health-scan.py on each scan. Read by Jarvis at session start. -->\n"
        f"<!-- Last scan: {ts} -->\n\n"
        f"<!-- FINDINGS_START -->\n"
    )
    footer = "<!-- FINDINGS_END -->\n"
    with open(out_path, "w") as f:
        f.write(header + findings_body + ("\n" if not findings_body.endswith("\n") else "") + footer)
    print(f"[health-scan] wrote {len(reds)} RED + {len(yellows)} YELLOW to {out_path}")
    return out_path


def main():
    print(f"[{now().strftime('%Y-%m-%d %H:%M:%S UTC')}] health-scan.py starting...")
    findings = []
    for fn in (check_cron_freshness, check_vps_health, check_kb_staleness,
               check_broken_handoffs, check_stale_messages, check_dashboard_integrity):
        try:
            findings.extend(fn())
        except Exception as e:
            findings.append(("YELLOW", "health-scan", f"check {fn.__name__} raised: {e}"))
    write_findings(findings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
