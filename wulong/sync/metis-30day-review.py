#!/usr/bin/env python3
"""
metis-30day-review.py — the falsification script for the Metis two-agent evaluation gate.

Implements Step 5h of the v3.0.1 plan. Run automatically 30 days after first_spawn_iso
(cron added by deployer at Metis go-live). Can also be run manually at any time.

Metric A — divergent firing:
  Count Metis PROPOSE artifacts where (at fire time) times_observed < 3 OR confidence < high.
  These are proposals that would NOT have cleared Hermes thresholds (times_observed=3, high).
  PASS: count ≥ 3. FAIL: FM1 (threshold-inversion benefit) unsupported.
  Sources: Meta/metis-proposals/queued/ + Meta/metis-proposals/approved/

Metric B — divergent stream:
  Count Metis notebook observations with source_type: learning_output.
  PASS: count ≥ 10. FAIL: FM2 (cadence-decoupling benefit) unsupported.
  Source: Meta/metis/notebook.md

Decision table (mechanical, no discretion):
  PASS + PASS  → Lock two-agent design through v3.1.
  FAIL + FAIL  → Collapse: ar-director merges Metis into Hermes.
  One PASS, one FAIL → Analyst review: lower failing metric bar by 50% + extend 30 days,
                       OR escalate to CEO.

Exit codes:
  0 = evaluation complete (decision posted to agent-messages.md)
  1 = first_spawn_iso is null (Metis never spawned)
  2 = 30 days have not elapsed yet (early run — prints days remaining)
"""
from __future__ import annotations
import fcntl
import json
import re
import sys
from datetime import datetime, timezone
import os
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
NOTEBOOK = VAULT / "Meta" / "metis" / "notebook.md"
PROPOSALS_QUEUED = VAULT / "Meta" / "metis-proposals" / "queued"
PROPOSALS_APPROVED = VAULT / "Meta" / "metis-proposals" / "approved"
AGENT_MESSAGES = VAULT / "Meta" / "agent-messages.md"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"

# Hermes reference thresholds (what Metis's lower bar is measured against)
HERMES_TIMES_THRESHOLD = 3
HERMES_CONFIDENCE_THRESHOLD = "high"
CONFIDENCE_RANK = {"low": 0, "med": 1, "medium": 1, "high": 2}

# Pass criteria
METRIC_A_PASS_COUNT = 3
METRIC_B_PASS_COUNT = 10
EVALUATION_WINDOW_DAYS = 30


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def read_notebook_frontmatter() -> dict:
    if not NOTEBOOK.exists():
        return {}
    text = NOTEBOOK.read_text(encoding="utf-8")
    m = re.match(r"^---\n([\s\S]*?)\n---", text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def count_metric_b(notebook_text: str) -> int:
    """Count observations with Source type: learning_output."""
    return len(re.findall(r"^Source type:\s*learning_output\s*$", notebook_text, re.MULTILINE))


def parse_proposal_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n([\s\S]*?)\n---", text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
        return data
    except Exception:
        return {}


def count_metric_a() -> tuple[int, list[str]]:
    """Count Metis proposals where times_observed < 3 OR confidence < high (at fire time).
    Returns (count, list of cycle_ids that qualified).
    """
    qualifying: list[str] = []
    for directory in (PROPOSALS_QUEUED, PROPOSALS_APPROVED):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            fm = parse_proposal_frontmatter(path)
            if not fm:
                continue
            times_obs = fm.get("times_observed", 999)
            confidence = fm.get("confidence", "high")
            conf_rank = CONFIDENCE_RANK.get(str(confidence).lower(), 2)
            hermes_conf_rank = CONFIDENCE_RANK[HERMES_CONFIDENCE_THRESHOLD]
            if times_obs < HERMES_TIMES_THRESHOLD or conf_rank < hermes_conf_rank:
                cycle_id = fm.get("cycle_id", path.stem)
                qualifying.append(str(cycle_id))
    return len(qualifying), qualifying


def post_result(decision: str, metric_a: int, metric_b: int, qualifying_a: list[str]) -> None:
    now = now_str()
    body = (
        f"\n## [{now}] — From: metis-30day-review → TO: Jarvis\n"
        f"**Status**: ⏳ 30-day evaluation gate complete\n"
        f"**Metric A** (divergent firing, PASS≥{METRIC_A_PASS_COUNT}): {metric_a} proposals "
        f"{'PASS' if metric_a >= METRIC_A_PASS_COUNT else 'FAIL'}\n"
        f"  Qualifying cycle_ids: {', '.join(qualifying_a) if qualifying_a else '(none)'}\n"
        f"**Metric B** (learning_output observations, PASS≥{METRIC_B_PASS_COUNT}): {metric_b} "
        f"{'PASS' if metric_b >= METRIC_B_PASS_COUNT else 'FAIL'}\n"
        f"**Decision**: {decision}\n---\n"
    )
    with open(AGENT_MESSAGES, "a", encoding="utf-8") as f:
        f.write(body)
    with open(CHANGE_LOG, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(
                f"[{now}] metis-30day-review → EVALUATION complete "
                f"(MetricA={metric_a}, MetricB={metric_b}, decision={decision[:40]})\n"
            )
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def main() -> int:
    fm = read_notebook_frontmatter()
    first_spawn_iso = fm.get("first_spawn_iso")

    if not first_spawn_iso or first_spawn_iso == "null":
        print("[metis-30day-review] SKIP: first_spawn_iso is null — Metis has never been spawned.")
        print("Clock starts on Metis's first OBSERVE call (update-cursor with null first_spawn_iso).")
        return 1

    try:
        first_spawn_dt = datetime.fromisoformat(str(first_spawn_iso))
        if first_spawn_dt.tzinfo is None:
            first_spawn_dt = first_spawn_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"[metis-30day-review] ERROR: cannot parse first_spawn_iso: {first_spawn_iso!r}")
        return 1

    now_utc = datetime.now(timezone.utc)
    elapsed_days = (now_utc - first_spawn_dt).total_seconds() / 86400.0

    if elapsed_days < EVALUATION_WINDOW_DAYS:
        remaining = EVALUATION_WINDOW_DAYS - elapsed_days
        print(
            f"[metis-30day-review] EARLY: {elapsed_days:.1f} days elapsed since first spawn. "
            f"{remaining:.1f} days until 30-day gate."
        )
        return 2

    # ── Compute metrics ────────────────────────────────────────────────────────
    metric_a, qualifying_a = count_metric_a()

    if not NOTEBOOK.exists():
        metric_b = 0
    else:
        metric_b = count_metric_b(NOTEBOOK.read_text(encoding="utf-8"))

    a_pass = metric_a >= METRIC_A_PASS_COUNT
    b_pass = metric_b >= METRIC_B_PASS_COUNT

    if a_pass and b_pass:
        decision = (
            "BOTH PASS — Lock two-agent design through v3.1. Close 30-day evaluation."
        )
    elif not a_pass and not b_pass:
        decision = (
            "BOTH FAIL — Collapse to single-agent: AR Director merges Metis notebook into "
            "Hermes notebook and retires Metis agent. No discretion — mechanical decision."
        )
    elif a_pass and not b_pass:
        decision = (
            f"SPLIT: Metric A PASS, Metric B FAIL — Analyst review required. Options: "
            f"(1) lower Metric B bar to ≥{METRIC_B_PASS_COUNT // 2} and extend 30 days, OR "
            f"(2) escalate to CEO. Choose ONE, not both."
        )
    else:
        decision = (
            f"SPLIT: Metric A FAIL, Metric B PASS — Analyst review required. Options: "
            f"(1) lower Metric A bar to ≥{METRIC_A_PASS_COUNT // 2} and extend 30 days, OR "
            f"(2) escalate to CEO. Choose ONE, not both."
        )

    print(f"[metis-30day-review] Elapsed: {elapsed_days:.1f} days")
    print(f"[metis-30day-review] Metric A: {metric_a} (PASS≥{METRIC_A_PASS_COUNT}) → {'PASS' if a_pass else 'FAIL'}")
    print(f"[metis-30day-review] Metric B: {metric_b} (PASS≥{METRIC_B_PASS_COUNT}) → {'PASS' if b_pass else 'FAIL'}")
    print(f"[metis-30day-review] Decision: {decision}")

    post_result(decision, metric_a, metric_b, qualifying_a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
