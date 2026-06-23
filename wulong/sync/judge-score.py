#!/usr/bin/env python3
"""
judge-score.py — objective RULE-FOLLOWING scorer for a single change_id.

Implements EXACTLY the formula pinned in Meta/plans/phase2-judge-2026-06-07.md (C2).
Emits: rule_following_score, rule_following_band, comprehensiveness_checklist skeleton.

Usage:
    python3 judge-score.py --change-id <id> [--json] [--strict]

Exit codes:
  0  scored OK (or INSUFFICIENT_DATA — still exit 0)
  1  internal error / receipts dir not found
  2  usage error

RULE-FOLLOWING formula (pinned — do not change without a new NN#10-gated change_id):
  score = 1.0
  if contrarian plan-review receipt absent: return 0.0  # GATE MISSING
  if contrarian output-review absent and not exempt: score -= 0.25
  if tester receipt absent and not exempt:            score -= 0.20
  if plan_verdict == FAIL:   score -= 0.25
  if output_verdict == FAIL: score -= 0.20
  if tester present but status != DONE: score -= 0.15
  score -= min(plan_fixer_loops, 3) * 0.05  # cap 0.15
  score -= min(output_fixer_loops, 3) * 0.05 # cap 0.15
  if compliance == RED: score = min(score, 0.40)
  score = max(score, 0.0)

Bands: 0.85-1.0 CLEAN / 0.65-0.84 MINOR DRIFT / 0.40-0.64 SIGNIFICANT DRIFT / 0-0.39 POOR.

Fail-closed:
  - missing review_verdict → treat as FAIL
  - tester status != DONE → treat as FAIL (score -= 0.15 applied)
  - verify-change.py no GREEN/RED in stdout → RED

Small-sample floor (N=5):
  - Counts previously scored change_ids from judge notebook observation_count.
  - Below 5, emits {"status":"INSUFFICIENT_DATA","scored_change_ids":k,"required":5} instead
    of a numeric score.
  - NOTE: for the self-test in this session, score_count=0 so INSUFFICIENT_DATA will be
    emitted alongside the comprehensiveness skeleton. This is correct behavior.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
META = VAULT / "Meta"
RECEIPTS = META / "receipts"
JUDGE_NOTEBOOK = META / "judge" / "notebook.md"
JUDGE_CONFIG = META / "judge" / "config.json"
VERIFY_CHANGE = META / "sync" / "verify-change.py"
TASTE_MODEL = META / "feedback" / "taste-model.md"
CHANGE_LOG = META / "change-log.md"

# Bands (ordered — check from top)
BANDS = [
    (0.85, 1.01, "CLEAN"),
    (0.65, 0.85, "MINOR DRIFT"),
    (0.40, 0.65, "SIGNIFICANT DRIFT"),
    (0.00, 0.40, "POOR"),
]


# ---------------------------------------------------------------------------
# Frontmatter parser (minimal, self-contained — no yaml dep)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    lines = content.split("\n")
    close = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        return {}
    fields: dict = {}
    for line in lines[1:close]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Handle YAML inline list: [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                fields[key] = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
            else:
                fields[key] = val
    return fields


def _parse_body(content: str) -> str:
    if not content.startswith("---"):
        return content
    lines = content.split("\n")
    close = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            close = i
            break
    if close is None:
        return content
    return "\n".join(lines[close + 1:])


# ---------------------------------------------------------------------------
# Frontmatter field coercion guard
# ---------------------------------------------------------------------------

def _str_field(fields: dict, key: str, default: str = "") -> str:
    """Read a frontmatter field safely, returning a string regardless of YAML type.

    A list-typed value (e.g. change_id: [a, b]) joins with ',' so callers that
    only need a string still get a non-empty sentinel; callers that need membership
    semantics should use _change_id_matches() instead.

    Missing/unexpected type → WARN to stderr and return default (never raises).
    # ponytail: stdlib str() ceiling; no dep needed; upgrade path = full yaml if more types emerge
    """
    val = fields.get(key)
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        sys.stderr.write(
            f"[judge-score] WARN: field '{key}' is a list — expected string. "
            f"Values: {val}\n"
        )
        return ",".join(str(v).strip() for v in val)
    sys.stderr.write(
        f"[judge-score] WARN: field '{key}' has unexpected type {type(val).__name__} — "
        f"using default {default!r}\n"
    )
    return default


def _change_id_matches(fields: dict, target: str) -> bool:
    """Return True if target equals the receipt's change_id (string) or is a member
    of it (list).  Never raises; a missing or malformed field returns False.
    """
    val = fields.get("change_id")
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip() == target
    if isinstance(val, list):
        return target in (v.strip() for v in val if isinstance(v, str))
    return False


# ---------------------------------------------------------------------------
# Receipt corpus loader
# ---------------------------------------------------------------------------

def _load_receipts_for_change(change_id: str) -> list[dict]:
    """Load all receipts in Meta/receipts/ with matching change_id, sorted by fname."""
    if not RECEIPTS.is_dir():
        return []
    members = []
    for entry in sorted(RECEIPTS.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".md"):
            continue
        try:
            content = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fields = _parse_frontmatter(content)
        if _change_id_matches(fields, change_id):
            members.append({
                "fname": entry.name,
                "path": str(entry),
                "fields": fields,
                "body": _parse_body(content),
            })
    return members


# ---------------------------------------------------------------------------
# Receipt classification helpers
# ---------------------------------------------------------------------------

def _find_contrarian_plan_review(members: list[dict]) -> Optional[dict]:
    """Return the contrarian plan-review receipt, preferring PASS over FAIL.

    When multiple plan-review receipts exist (e.g. v1-FAIL then v2-PASS), the binding
    gate semantic is the final verdict — return PASS if any PASS exists.
    """
    candidates = [
        m for m in members
        if _str_field(m["fields"], "agent") == "contrarian"
        and _str_field(m["fields"], "review_mode") == "plan"
    ]
    if not candidates:
        return None
    for m in candidates:
        if _get_review_verdict(m) == "PASS":
            return m
    return candidates[0]


def _find_contrarian_output_review(members: list[dict]) -> Optional[dict]:
    """Return the contrarian output-review receipt, preferring PASS over FAIL.

    When multiple output-review receipts exist, the binding gate semantic is the final
    verdict — return PASS if any PASS exists.
    """
    candidates = [
        m for m in members
        if _str_field(m["fields"], "agent") == "contrarian"
        and _str_field(m["fields"], "review_mode") == "output"
    ]
    if not candidates:
        return None
    for m in candidates:
        if _get_review_verdict(m) == "PASS":
            return m
    return candidates[0]


def _find_tester(members: list[dict]) -> Optional[dict]:
    """Return the tester receipt, or None."""
    for m in members:
        if _str_field(m["fields"], "agent") == "tester":
            return m
    return None


def _is_tester_exempt(members: list[dict]) -> bool:
    """Return True if the jarvis orchestration receipt declares tester_exempt: true.

    Only the jarvis receipt for this change_id may grant the exemption — no other agent.
    Absence of marker → False (fail-closed, tester required).
    """
    for m in members:
        f = m["fields"]
        if _str_field(f, "agent") == "jarvis":
            val = f.get("tester_exempt", "")
            if str(val).strip().lower() == "true":
                return True
    return False


def _get_review_verdict(receipt: Optional[dict]) -> Optional[str]:
    """Return PASS / FAIL from receipt, or None (absent = FAIL, per fail-closed rule)."""
    if receipt is None:
        return None
    v = _str_field(receipt["fields"], "review_verdict").upper()
    if v in ("PASS", "FAIL"):
        return v
    # present but missing/unrecognized → FAIL (fail-closed)
    return "FAIL"


def _get_tester_status(receipt: Optional[dict]) -> Optional[str]:
    """Return status string from tester receipt, or None."""
    if receipt is None:
        return None
    return _str_field(receipt["fields"], "status").upper()


def _count_fixer_receipts(members: list[dict], fixer_type: str) -> int:
    """Count plan-fixer or output-fixer receipts in the change corpus.

    fixer_type: 'plan-fixer' or 'output-fixer'
    """
    count = 0
    for m in members:
        if _str_field(m["fields"], "agent") == fixer_type:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Compliance check via verify-change.py
# ---------------------------------------------------------------------------

_VERDICT_RE = re.compile(r"^VERDICT:\s*(GREEN|RED)\b", re.MULTILINE)


def _run_compliance(change_id: str) -> str:
    """Run verify-change.py --change-id <id> and extract the verdict from stdout.

    Anchors on the authoritative 'VERDICT: GREEN|RED' line so that the word
    'GREEN' or 'RED' appearing elsewhere in the output (detail lines, notes)
    cannot mis-read the compliance result.  No VERDICT line → fail-closed RED.
    """
    if not VERIFY_CHANGE.exists():
        sys.stderr.write(f"[judge-score] verify-change.py not found at {VERIFY_CHANGE} → compliance=RED\n")
        return "RED"
    try:
        result = subprocess.run(
            [sys.executable, str(VERIFY_CHANGE), "--change-id", change_id],
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = result.stdout
        m = _VERDICT_RE.search(stdout)
        if m:
            return m.group(1)  # "GREEN" or "RED"
        # No VERDICT line in output → fail-closed = RED
        sys.stderr.write("[judge-score] verify-change stdout had no VERDICT line → RED\n")
        return "RED"
    except subprocess.TimeoutExpired:
        sys.stderr.write("[judge-score] verify-change timed out → RED\n")
        return "RED"
    except Exception as e:
        sys.stderr.write(f"[judge-score] verify-change error: {e} → RED\n")
        return "RED"


# ---------------------------------------------------------------------------
# Small-sample floor: count previously scored change_ids
# ---------------------------------------------------------------------------

def _count_scored_change_ids() -> int:
    """Count the observation_count in the judge notebook frontmatter."""
    if not JUDGE_NOTEBOOK.exists():
        return 0
    try:
        text = JUDGE_NOTEBOOK.read_text(encoding="utf-8")
        m = re.search(r"^observation_count:\s*(\d+)", text, re.MULTILINE)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


def _load_config() -> dict:
    if not JUDGE_CONFIG.exists():
        return {}
    try:
        return json.loads(JUDGE_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# COMPREHENSIVENESS checklist skeleton builder
# ---------------------------------------------------------------------------

def _build_comprehensiveness_skeleton(
    change_id: str,
    members: list[dict],
    plan_receipt: Optional[dict],
    output_receipt: Optional[dict],
    tester_receipt: Optional[dict],
    tester_exempt: bool = False,
) -> list[dict]:
    """
    Pre-populate C-1 (deliverables on disk) and C-2 (gate receipts exist + PASS/DONE).
    C-3 and C-4 are left as satisfied=null for the Judge agent to fill in-session.

    C-1: try to resolve named deliverables from the plan. Since judge-score.py is pure-Python
    with no LLM, we check whether named paths in the plan receipt body exist on disk.
    If no plan receipt is available, C-1 = null (Judge fills).
    """
    checklist = []

    # ── C-1: named plan deliverables exist on disk ─────────────────────────
    c1 = {
        "id": "C-1",
        "description": "Every named plan deliverable exists on disk",
        "required_evidence": "Disk path for each deliverable named in the plan",
        "satisfied": None,
        "cited_artifact": None,
    }
    # Attempt: scan the plan receipt body for ## Files written paths
    plan_body = plan_receipt["body"] if plan_receipt else ""
    deliverable_paths: list[str] = []
    files_section_match = re.search(
        r"^##\s+Files\s+written\s*\n(.*?)(?=^##\s|\Z)",
        plan_body,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if files_section_match:
        for raw in files_section_match.group(1).splitlines():
            line = raw.strip().lstrip("-*•").strip().strip("`").rstrip(".,;:)")
            for part in line.split(","):
                part = part.strip().strip("`").rstrip(".,;:)")
                if "/" in part and " " not in part:
                    # Looks like a path
                    deliverable_paths.append(part)

    if not deliverable_paths:
        # Fall back: no deliverables parseable from body → Judge fills
        c1["satisfied"] = None
        c1["cited_artifact"] = "Cannot resolve from plan receipt body; Judge fills in-session"
    else:
        all_exist = True
        missing = []
        for p in deliverable_paths:
            # Try both absolute and relative-to-vault
            resolved = Path(p) if Path(p).is_absolute() else VAULT / p
            if not resolved.exists():
                all_exist = False
                missing.append(p)
        c1["satisfied"] = all_exist
        if all_exist:
            c1["cited_artifact"] = f"All {len(deliverable_paths)} deliverable path(s) verified on disk"
        else:
            c1["cited_artifact"] = f"MISSING on disk: {missing}"

    checklist.append(c1)

    # ── C-2: required gate receipts exist + PASS/DONE ──────────────────────
    plan_verdict = _get_review_verdict(plan_receipt)
    output_verdict = _get_review_verdict(output_receipt)
    tester_status = _get_tester_status(tester_receipt)

    # tester_exempt: True  → "N/A (exempt)" which satisfies the gate (treat as True)
    tester_gate_value: bool
    tester_gate_label: str
    if tester_exempt and tester_receipt is None:
        tester_gate_value = True   # exempt counts as satisfied
        tester_gate_label = "tester DONE receipt (N/A — exempt)"
    else:
        tester_gate_value = tester_receipt is not None and tester_status == "DONE"
        tester_gate_label = "tester DONE receipt"

    gate_items = {
        "plan-review PASS receipt": (plan_receipt is not None and plan_verdict == "PASS"),
        "output-review PASS receipt": (output_receipt is not None and output_verdict == "PASS"),
        tester_gate_label: tester_gate_value,
    }
    all_gates = all(gate_items.values())
    missing_gates = [k for k, v in gate_items.items() if not v]

    c2_artifacts = []
    if plan_receipt:
        c2_artifacts.append(plan_receipt["fname"])
    if output_receipt:
        c2_artifacts.append(output_receipt["fname"])
    if tester_receipt:
        c2_artifacts.append(tester_receipt["fname"])

    c2 = {
        "id": "C-2",
        "description": "Every required gate ran: plan-review PASS + output-review PASS + tester DONE",
        "required_evidence": "Receipt filenames for plan-review, output-review, tester",
        "satisfied": all_gates,
        "cited_artifact": c2_artifacts if c2_artifacts else None,
        "_detail": {k: v for k, v in gate_items.items()} if not all_gates else None,
        "_missing": missing_gates if missing_gates else None,
    }
    # Clean up nulls in detail
    if c2["_detail"] is None:
        del c2["_detail"]
    if c2["_missing"] is None:
        del c2["_missing"]

    checklist.append(c2)

    # ── C-3: no out-of-scope items (Judge fills) ────────────────────────────
    checklist.append({
        "id": "C-3",
        "description": "No out-of-scope items silently added or dropped",
        "required_evidence": "Receipt or change-log citation confirming scope was honored",
        "satisfied": None,
        "cited_artifact": None,
        "_note": "Judge fills in-session by reading change-log + receipt bodies vs plan scope section",
    })

    # ── C-4: stated follow-ups/caveats carried forward (Judge fills) ────────
    checklist.append({
        "id": "C-4",
        "description": "Stated follow-ups and caveats carried forward to task-board or change-log",
        "required_evidence": "Citation in task-board/change-log/receipt noting the caveat was forwarded",
        "satisfied": None,
        "cited_artifact": None,
        "_note": "Judge fills in-session by comparing plan caveats vs receipts/task-board",
    })

    return checklist


def _comprehensiveness_rollup(checklist: list[dict]) -> Optional[float]:
    """Rollup = satisfied/4. Returns None if any are null (incomplete)."""
    satisfied_count = 0
    for item in checklist:
        s = item.get("satisfied")
        if s is None:
            return None  # incomplete, cannot compute
        if s is True:
            satisfied_count += 1
    return satisfied_count / 4.0


# ---------------------------------------------------------------------------
# Band classifier
# ---------------------------------------------------------------------------

def _classify_band(score: float) -> str:
    for lo, hi, label in BANDS:
        if lo <= score < hi:
            return label
    return "POOR"


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_change(change_id: str) -> dict:
    cfg = _load_config()
    small_sample_n = cfg.get("small_sample_floor_N", 5)

    # Load receipt corpus
    members = _load_receipts_for_change(change_id)

    # Identify key receipts
    plan_receipt = _find_contrarian_plan_review(members)
    output_receipt = _find_contrarian_output_review(members)
    tester_receipt = _find_tester(members)
    tester_exempt = _is_tester_exempt(members)

    # ── RULE-FOLLOWING formula (pinned) ────────────────────────────────────

    # Gate: plan-review receipt absent → 0.0, GATE MISSING
    if plan_receipt is None:
        result = {
            "change_id": change_id,
            "status": "GATE_MISSING",
            "rule_following_score": 0.0,
            "rule_following_band": "POOR",
            # Adjudicated later via observer-disposition.py adjudicate-warn.
            # Judge NEVER sets this itself (anti-sycophancy lock, judge.md).
            "false_positive": None,
            "reason": "GATE MISSING: contrarian plan-review receipt absent for this change_id",
            "receipts_found": [m["fname"] for m in members],
        }
        return result

    score = 1.0
    deductions: list[str] = []

    # output-review absent (not exempt for now — Judge determines exemption in-session)
    plan_verdict = _get_review_verdict(plan_receipt)
    output_verdict = _get_review_verdict(output_receipt)
    tester_status = _get_tester_status(tester_receipt)

    if output_receipt is None:
        score -= 0.25
        deductions.append("output-review absent (-0.25)")

    if tester_receipt is None and not tester_exempt:
        score -= 0.20
        deductions.append("tester absent (-0.20)")
    elif tester_receipt is None and tester_exempt:
        deductions.append("tester absent — EXEMPT (tester_exempt: true in jarvis receipt)")

    # Gate verdicts
    if plan_verdict == "FAIL":
        score -= 0.25
        deductions.append("plan-review verdict FAIL (-0.25)")

    if output_receipt is not None and output_verdict == "FAIL":
        score -= 0.20
        deductions.append("output-review verdict FAIL (-0.20)")

    # Tester present but not DONE
    if tester_receipt is not None and tester_status != "DONE":
        score -= 0.15
        deductions.append(f"tester present but status={tester_status} (-0.15)")

    # Fix-loop counts
    plan_fixer_loops = _count_fixer_receipts(members, "plan-fixer")
    output_fixer_loops = _count_fixer_receipts(members, "output-fixer")

    pf_penalty = min(plan_fixer_loops, 3) * 0.05
    of_penalty = min(output_fixer_loops, 3) * 0.05
    if pf_penalty > 0:
        score -= pf_penalty
        deductions.append(f"plan-fixer loops={plan_fixer_loops} (-{pf_penalty:.2f})")
    if of_penalty > 0:
        score -= of_penalty
        deductions.append(f"output-fixer loops={output_fixer_loops} (-{of_penalty:.2f})")

    # Compliance
    compliance = _run_compliance(change_id)
    if compliance == "RED":
        if score > 0.40:
            deductions.append(f"compliance=RED → hard cap at 0.40 (was {score:.2f})")
            score = min(score, 0.40)
        else:
            deductions.append(f"compliance=RED (score already <= 0.40)")

    # Floor at 0.0
    score = max(score, 0.0)

    # Band
    band = _classify_band(score)

    # ── Small-sample floor ─────────────────────────────────────────────────
    scored_count = _count_scored_change_ids()
    if scored_count < small_sample_n:
        # Emit INSUFFICIENT_DATA for numeric score; still emit comprehensiveness skeleton
        checklist = _build_comprehensiveness_skeleton(
            change_id, members, plan_receipt, output_receipt, tester_receipt, tester_exempt
        )
        rollup = _comprehensiveness_rollup(checklist)
        warn_threshold = cfg.get("comprehensiveness_warn_threshold", 0.75)

        result = {
            "change_id": change_id,
            "status": "INSUFFICIENT_DATA",
            # Adjudicated later via observer-disposition.py adjudicate-warn.
            # Judge NEVER sets this itself (anti-sycophancy lock, judge.md).
            "false_positive": None,
            "scored_change_ids": scored_count,
            "required": small_sample_n,
            "_note": (
                f"Below N={small_sample_n} scored change_ids — numeric RULE-FOLLOWING score WITHHELD. "
                "Observe without scoring per anti-overfitting guard. "
                f"Preview (not emitted as score): {score:.2f} ({band}). "
                "Emit score after N >= 5."
            ),
            "rule_following_preview": {
                "score": round(score, 4),
                "band": band,
                "deductions": deductions,
                "plan_verdict": plan_verdict,
                "output_verdict": output_verdict,
                "tester_status": tester_status,
                "plan_fixer_loops": plan_fixer_loops,
                "output_fixer_loops": output_fixer_loops,
                "compliance": compliance,
            },
            "comprehensiveness_checklist": checklist,
            "comprehensiveness_rollup": rollup,
            "comprehensiveness_warn": (
                rollup is not None and rollup < warn_threshold
            ),
        }
        return result

    # ── Full scored result ─────────────────────────────────────────────────
    checklist = _build_comprehensiveness_skeleton(
        change_id, members, plan_receipt, output_receipt, tester_receipt
    )
    rollup = _comprehensiveness_rollup(checklist)
    warn_threshold = cfg.get("comprehensiveness_warn_threshold", 0.75)
    warn_score_floor = cfg.get("thresholds", {}).get("warn_score_floor", 0.65)

    result = {
        "change_id": change_id,
        "status": "SCORED",
        "rule_following_score": round(score, 4),
        "rule_following_band": band,
        "warn": score < warn_score_floor,
        # Adjudicated later via observer-disposition.py adjudicate-warn.
        # Judge NEVER sets this itself (anti-sycophancy lock, judge.md).
        "false_positive": None,
        "deductions": deductions,
        "inputs": {
            "plan_verdict": plan_verdict,
            "output_verdict": output_verdict,
            "tester_status": tester_status,
            "plan_fixer_loops": plan_fixer_loops,
            "output_fixer_loops": output_fixer_loops,
            "compliance": compliance,
            "receipts_found": [m["fname"] for m in members],
        },
        "comprehensiveness_checklist": checklist,
        "comprehensiveness_rollup": rollup,
        "comprehensiveness_warn": (
            rollup is not None and rollup < warn_threshold
        ),
    }
    return result


# ---------------------------------------------------------------------------
# Taste-model write path (FIX #3 — the ONLY sanctioned writer of taste-model.md)
# ---------------------------------------------------------------------------
#
# IMPORTANT: this section does NOT touch the pinned RULE-FOLLOWING formula above,
# nor any config (block_enabled stays in config.json and is never read/written here).
# It only APPENDS a learned pattern into the EXISTING taste-model.md scaffold,
# preserving the frontmatter and bumping pattern_count + last_updated.
# Mirrors the structure / idempotency of judge-append-notebook.py.

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


def _log_change(msg: str) -> None:
    """Append a NN#7 change-log line. Best-effort (write already succeeded)."""
    try:
        with open(CHANGE_LOG, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(f"[{_now_str()}] judge → TASTE-MODEL {msg}\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def _confidence_for(sample_count: int, cfg: dict) -> str:
    """N<5 → low, 5<=N<10 → med, N>=10 → high (matches taste-model.md scaffold doc)."""
    med = cfg.get("taste_model_med_n", 5)
    high = cfg.get("taste_model_high_n", 10)
    if sample_count >= high:
        return "high"
    if sample_count >= med:
        return "med"
    return "low"


def append_pattern(
    pattern_id: str,
    dimension: str,
    description: str,
    signal: str,
    evidence: list[str],
) -> int:
    """Append (or reinforce) a learned pattern into the EXISTING taste-model.md scaffold.

    - Preserves frontmatter; bumps pattern_count + sets last_updated.
    - Appends a YAML pattern block below the existing scaffold (after ## Patterns).
    - Idempotent: if pattern_id already exists, REINFORCE it (sample_count++,
      merge evidence, refresh last_observed, recompute confidence) instead of dup.
    """
    if dimension not in ("A", "B", "both"):
        sys.stderr.write(f"[judge-score] invalid dimension: {dimension!r} (must be A | B | both)\n")
        return 1
    if not description.strip():
        sys.stderr.write("[judge-score] --description is required and must be non-empty\n")
        return 1
    if not TASTE_MODEL.exists():
        sys.stderr.write(f"[judge-score] taste-model scaffold not found at {TASTE_MODEL} — refusing to create from scratch\n")
        return 2

    pid = _slugify(pattern_id)
    if not pid:
        sys.stderr.write("[judge-score] --pattern-id slugifies to empty\n")
        return 1

    cfg = _load_config()
    text = TASTE_MODEL.read_text(encoding="utf-8")
    today = _today_str()

    # ── idempotency: reinforce if this pattern_id already exists ────────────
    existing = re.search(
        rf"^pattern_id:\s*{re.escape(pid)}\s*$([\s\S]*?)(?=^pattern_id:|\Z)",
        text,
        re.MULTILINE,
    )
    if existing:
        block = existing.group(0)
        # bump sample_count
        sc_m = re.search(r"^sample_count:\s*(\d+)", block, re.MULTILINE)
        new_count = (int(sc_m.group(1)) + 1) if sc_m else 1
        new_block = re.sub(r"^sample_count:\s*\d+", f"sample_count: {new_count}", block, count=1, flags=re.MULTILINE)
        # refresh last_observed
        new_block = re.sub(r"^last_observed:\s*\S+", f"last_observed: {today}", new_block, count=1, flags=re.MULTILINE)
        # recompute confidence
        new_block = re.sub(
            r"^confidence:\s*\S+",
            f"confidence: {_confidence_for(new_count, cfg)}",
            new_block, count=1, flags=re.MULTILINE,
        )
        # merge evidence (append new paths under evidence_cited)
        if evidence:
            ev_m = re.search(r"^evidence_cited:\s*\n((?:\s*-\s+.*\n?)*)", new_block, re.MULTILINE)
            if ev_m:
                existing_paths = set(re.findall(r"-\s+(\S+)", ev_m.group(1)))
                added = [e for e in evidence if e not in existing_paths]
                if added:
                    ins = "".join(f"  - {e}\n" for e in added)
                    new_block = new_block[:ev_m.end(1)] + ins + new_block[ev_m.end(1):]
        text = text.replace(block, new_block, 1)
        TASTE_MODEL.write_text(text, encoding="utf-8")
        # bump frontmatter last_updated only (pattern_count unchanged on reinforce)
        _bump_frontmatter(today, delta=0)
        _log_change(f"reinforce-pattern: {pid} → sample_count={new_count}")
        print(f"[judge-score] reinforced pattern '{pid}' → sample_count={new_count}, confidence={_confidence_for(new_count, cfg)}")
        return 0

    # ── new pattern: append YAML block under ## Patterns ───────────────────
    block = (
        f"\npattern_id: {pid}\n"
        f"dimension: {dimension}\n"
        f"description: {description.strip()}\n"
        f"signal: {signal.strip()}\n"
        f"confidence: {_confidence_for(1, cfg)}\n"
        f"sample_count: 1\n"
        f"evidence_cited:\n"
        + ("".join(f"  - {e}\n" for e in evidence) if evidence else "  - (none cited)\n")
        + f"first_observed: {today}\n"
        f"last_observed: {today}\n"
        f"status: observing\n"
    )
    text = text.rstrip() + "\n" + block
    TASTE_MODEL.write_text(text, encoding="utf-8")
    _bump_frontmatter(today, delta=1)
    _log_change(f"append-pattern: {pid} (dimension={dimension})")
    print(f"[judge-score] appended new pattern '{pid}' (dimension={dimension}, confidence=low, sample_count=1)")
    return 0


def _bump_frontmatter(today: str, delta: int) -> None:
    """Set last_updated=today and add `delta` to pattern_count in the frontmatter.

    Preserves everything else (owner, schema_version, anti-sycophancy lock, _doc).
    """
    text = TASTE_MODEL.read_text(encoding="utf-8")
    # last_updated (handles 'null' or a date)
    text = re.sub(
        r"^last_updated:\s*\S.*$",
        f"last_updated: {today}",
        text, count=1, flags=re.MULTILINE,
    )
    if delta:
        pc_m = re.search(r"^pattern_count:\s*(\d+)", text, re.MULTILINE)
        if pc_m:
            new_pc = int(pc_m.group(1)) + delta
            text = re.sub(r"^pattern_count:\s*\d+", f"pattern_count: {new_pc}", text, count=1, flags=re.MULTILINE)
    TASTE_MODEL.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Score a change_id against the pinned RULE-FOLLOWING formula, "
        "or append a learned pattern into taste-model.md (append-pattern subcommand)."
    )
    sub = ap.add_subparsers(dest="cmd")

    # append-pattern subcommand (the ONLY sanctioned write path to taste-model.md)
    pp = sub.add_parser(
        "append-pattern",
        help="Append (or reinforce) a learned pattern into Meta/feedback/taste-model.md",
    )
    pp.add_argument("--pattern-id", required=True, help="Stable slug for the pattern")
    pp.add_argument("--dimension", required=True, choices=["A", "B", "both"],
                    help="A=RULE-FOLLOWING, B=COMPREHENSIVENESS, both")
    pp.add_argument("--description", required=True, help="One-sentence pattern description")
    pp.add_argument("--signal", default="", help="Observable signal(s) for this pattern")
    pp.add_argument("--evidence", default="", help="Comma-separated artifact paths")

    # default (no subcommand): score a change_id — preserves `--change-id` CLI
    ap.add_argument("--change-id", help="The change_id to score (default mode)")
    ap.add_argument("--json", action="store_true", dest="as_json", help="Emit raw JSON")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if WARN (rule_following_score < warn_score_floor or comprehensiveness_warn)",
    )
    args = ap.parse_args()

    # ── append-pattern path ────────────────────────────────────────────────
    if args.cmd == "append-pattern":
        evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
        return append_pattern(
            args.pattern_id, args.dimension, args.description, args.signal, evidence
        )

    # ── default scoring path ───────────────────────────────────────────────
    if not args.change_id:
        ap.error("the following argument is required: --change-id (or use the append-pattern subcommand)")

    if not RECEIPTS.is_dir():
        sys.stderr.write(f"[judge-score] ERROR: receipts dir not found: {RECEIPTS}\n")
        return 1

    result = score_change(args.change_id)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable summary
        cid = result["change_id"]
        status = result["status"]
        print(f"\n[judge-score] change_id: {cid}")
        print(f"  status: {status}")

        if status == "GATE_MISSING":
            print(f"  RULE-FOLLOWING: 0.00 (POOR) — {result['reason']}")
        elif status == "INSUFFICIENT_DATA":
            print(f"  RULE-FOLLOWING: WITHHELD (N={result['scored_change_ids']} < {result['required']})")
            print(f"  Preview (not official): {result['rule_following_preview']['score']:.2f} ({result['rule_following_preview']['band']})")
            if result['rule_following_preview']['deductions']:
                print(f"  Deductions: {'; '.join(result['rule_following_preview']['deductions'])}")
        else:
            score = result["rule_following_score"]
            band = result["rule_following_band"]
            warn = result.get("warn", False)
            print(f"  RULE-FOLLOWING: {score:.4f} ({band}){' [WARN]' if warn else ''}")
            if result.get("deductions"):
                print(f"  Deductions: {'; '.join(result['deductions'])}")

        # Comprehensiveness
        print(f"\n  COMPREHENSIVENESS checklist:")
        for item in result.get("comprehensiveness_checklist", []):
            sat = item.get("satisfied")
            sat_str = "PASS" if sat is True else ("FAIL" if sat is False else "null (Judge fills)")
            artifact = item.get("cited_artifact")
            print(f"    {item['id']}: {sat_str}")
            if artifact:
                print(f"      cited: {artifact}")
            if "_missing" in item:
                print(f"      missing: {item['_missing']}")
        rollup = result.get("comprehensiveness_rollup")
        if rollup is not None:
            print(f"  Rollup: {rollup:.2f}/1.00{' [WARN: < 0.75]' if result.get('comprehensiveness_warn') else ''}")
        else:
            print("  Rollup: null (incomplete — Judge fills C-3/C-4 in-session)")
        print()

    if args.strict:
        warn_flag = result.get("warn", False) or result.get("comprehensiveness_warn", False)
        if warn_flag or result["status"] in ("GATE_MISSING",):
            return 1
    return 0


def _demo() -> None:
    """Assert-based self-check for list-typed change_id guard (ponytail: no test framework).

    Proves three properties:
      (a) A list change_id receipt does NOT FATAL the loader.
      (b) A member target matches and is credited (returned in members list).
      (c) A non-member target does NOT match.
    """
    # Build a synthetic receipt with a list change_id
    poison_fields: dict = {
        "agent": "tester",
        "status": "DONE",
        "change_id": ["change-a-2099-01-01", "change-b-2099-01-01"],
    }

    # (a) _change_id_matches must not raise on a list value
    try:
        result_a = _change_id_matches(poison_fields, "change-a-2099-01-01")
    except Exception as exc:
        raise AssertionError(f"(a) FATAL on list change_id: {exc}") from exc

    # (b) member target matches
    assert result_a is True, "(b) member target should match"

    # (c) non-member target does not match
    result_c = _change_id_matches(poison_fields, "change-z-2099-01-01")
    assert result_c is False, "(c) non-member target should not match"

    # (d) plain string change_id still works
    string_fields: dict = {"change_id": "change-a-2099-01-01"}
    assert _change_id_matches(string_fields, "change-a-2099-01-01") is True, "(d) string match broken"
    assert _change_id_matches(string_fields, "change-z-2099-01-01") is False, "(d) string non-match broken"

    # (e) _str_field returns string for list value without raising
    s = _str_field({"myfield": ["x", "y"]}, "myfield")
    assert isinstance(s, str), f"(e) _str_field should return str, got {type(s)}"

    print("[judge-score] SELF-CHECK PASS: list-typed change_id guard (a/b/c/d/e)")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) == 2 and _sys.argv[1] == "--self-check":
        try:
            _demo()
        except AssertionError as _e:
            _sys.stderr.write(f"[judge-score] SELF-CHECK FAIL: {_e}\n")
            _sys.exit(1)
        _sys.exit(0)
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[judge-score] FATAL: {e}\n")
        sys.exit(1)
