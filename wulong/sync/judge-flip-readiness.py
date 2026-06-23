#!/usr/bin/env python3
"""
judge-flip-readiness.py — observability reporter for Judge block-mode flip readiness.

Reads Meta/judge/config.json (block_enabled_after, thresholds) and the Judge's
current scores from Meta/judge/notebook.md and Meta/feedback/taste-model.md.

Computes:
  - Days until block_enabled_after date
  - Current N (number of scored change_ids in notebook)
  - Current agreement rate (CLEAN-band entries / total scored)
  - Current false-positive rate (if derivable from notebook)
  - PASS / NOT-YET verdict per threshold

If a metric is not yet available, marks it UNKNOWN — never guesses.

Writes to Meta/doctor/judge-flip-readiness.md.
Always exits 0.

Pure Python / stdlib only. No LLM, no network, no spend.

Usage:
  python3 Meta/sync/judge-flip-readiness.py
  python3 Meta/sync/judge-flip-readiness.py --verbose
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent.parent
DOCTOR = VAULT / "Meta" / "doctor"
CONFIG_PATH = VAULT / "Meta" / "judge" / "config.json"
NOTEBOOK_PATH = VAULT / "Meta" / "judge" / "notebook.md"
TASTE_MODEL_PATH = VAULT / "Meta" / "feedback" / "taste-model.md"
LEDGER_PATH = VAULT / "Meta" / "observer-proposals" / "ledger.jsonl"


def _load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _parse_notebook_scores() -> dict:
    """
    Parse notebook.md to count scored change_ids and agreement.

    Returns dict with:
      n_scored: int — number of ## Observation blocks with a numeric rule_following_score
      n_clean: int  — those with rule_following_band: CLEAN
      n_insufficient: int — INSUFFICIENT_DATA entries (not counted toward agreement)
    """
    if not NOTEBOOK_PATH.exists():
        return {"n_scored": 0, "n_clean": 0, "n_insufficient": 0, "available": False}

    try:
        text = NOTEBOOK_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"n_scored": 0, "n_clean": 0, "n_insufficient": 0, "available": False}

    # Each observation starts with "## Observation"
    # Extract rule_following_score and rule_following_band per block
    blocks = re.split(r"^## Observation", text, flags=re.MULTILINE)

    n_scored = 0
    n_clean = 0
    n_insufficient = 0

    # Entries write "Rule following score:" / "Rule following band:" (spaces,
    # capital R). Accept the underscore form too so a future writer-side
    # change can't silently zero the count again.
    score_re = re.compile(r"(?:Rule following score|rule_following_score):\s*(.+)", re.IGNORECASE)
    band_re = re.compile(r"(?:Rule following band|rule_following_band):\s*(.+)", re.IGNORECASE)

    for block in blocks[1:]:  # skip header before first Observation
        score_match = score_re.search(block)
        band_match = band_re.search(block)
        if not score_match:
            continue
        score_val = score_match.group(1).strip().strip('"\'')
        if score_val.upper() == "INSUFFICIENT_DATA":
            n_insufficient += 1
            continue
        # Numeric score — count as scored
        try:
            float(score_val)
        except ValueError:
            continue
        n_scored += 1
        if band_match:
            band = band_match.group(1).strip().strip('"\'').upper()
            if band == "CLEAN":
                n_clean += 1

    return {
        "n_scored": n_scored,
        "n_clean": n_clean,
        "n_insufficient": n_insufficient,
        "available": True,
    }


def _false_positive_rollup() -> dict:
    """
    FP rate from ADJUDICATED judge_warn rows in the disposition ledger ONLY.

    rate = count(false_positive==true) / count(false_positive in {true, false})
    Null/un-adjudicated warns are excluded from BOTH numerator and denominator.
    Judge never adjudicates its own warns (anti-sycophancy lock) — rows are
    written by observer-disposition.py adjudicate-warn (CEO/contrarian).
    Until warns are adjudicated, the criterion is honestly UNKNOWN.

    Returns {n_adjudicated, n_fp, rate (float|None)}. Latest row per change_id wins.
    """
    if not LEDGER_PATH.exists():
        return {"n_adjudicated": 0, "n_fp": 0, "rate": None}
    latest: dict[str, bool] = {}
    try:
        for line in LEDGER_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "judge_warn":
                continue
            fp = row.get("false_positive")
            if isinstance(fp, bool):  # null/unadjudicated excluded entirely
                latest[row.get("change_id", "")] = fp
    except OSError:
        return {"n_adjudicated": 0, "n_fp": 0, "rate": None}

    n_adj = len(latest)
    n_fp = sum(1 for v in latest.values() if v)
    return {
        "n_adjudicated": n_adj,
        "n_fp": n_fp,
        "rate": (n_fp / n_adj) if n_adj > 0 else None,
    }


def _write_report(
    config: dict | None,
    notebook: dict,
    today: date,
) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Judge Flip Readiness",
        "",
        f"Generated: {ts}",
        "",
        "---",
        "",
    ]

    if config is None:
        lines += [
            "**ERROR: Meta/judge/config.json not found or unreadable.**",
            "Cannot compute readiness without config. All thresholds: UNKNOWN.",
            "",
        ]
        DOCTOR.mkdir(parents=True, exist_ok=True)
        out_path = DOCTOR / "judge-flip-readiness.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    block_after_str = config.get("block_enabled_after", "UNKNOWN")
    thresholds = config.get("thresholds", {})
    warn_floor = thresholds.get("warn_score_floor", None)

    # Pre-registered re-eval criteria from config's _block_note
    # (hard-coded from the config comment: N>=20, agreement>=80%, FP<=15%)
    REQUIRED_N = 20
    REQUIRED_AGREEMENT = 0.80
    REQUIRED_FP_MAX = 0.15

    # Days until flip date
    if block_after_str != "UNKNOWN":
        try:
            flip_date = date.fromisoformat(block_after_str)
            days_until = (flip_date - today).days
            days_str = f"{days_until} days" if days_until >= 0 else f"{abs(days_until)} days PAST"
        except ValueError:
            days_str = "UNKNOWN (parse error)"
            flip_date = None
    else:
        days_str = "UNKNOWN"
        flip_date = None

    lines += [
        "## Flip Configuration",
        "",
        f"- block_enabled_after: {block_after_str}",
        f"- Days until flip date: {days_str}",
        f"- block_enabled (current): {config.get('block_enabled', 'UNKNOWN')}",
        f"- mode (current): {config.get('mode', 'UNKNOWN')}",
        "",
        "---",
        "",
        "## Pre-registered Flip Criteria",
        "",
        "*(Source: config.json _block_note — N>=20 scored change_ids, >=80% agreement, <=15% false-positive)*",
        "",
    ]

    # Criterion 1: N >= 20
    if not notebook["available"]:
        n_str = "UNKNOWN (notebook.md not readable)"
        n_verdict = "UNKNOWN"
    else:
        n_scored = notebook["n_scored"]
        n_str = str(n_scored)
        n_verdict = "PASS" if n_scored >= REQUIRED_N else f"NOT-YET ({n_scored}/{REQUIRED_N})"

    # Criterion 2: agreement >= 80%
    # agreement = CLEAN-band / total_scored (numeric-only, not INSUFFICIENT_DATA)
    if not notebook["available"] or notebook["n_scored"] == 0:
        agreement_str = "UNKNOWN (no scored entries in notebook)"
        agreement_verdict = "UNKNOWN"
    else:
        n_clean = notebook["n_clean"]
        n_scored = notebook["n_scored"]
        agreement = n_clean / n_scored
        agreement_str = f"{agreement:.0%} ({n_clean} CLEAN / {n_scored} scored)"
        agreement_verdict = "PASS" if agreement >= REQUIRED_AGREEMENT else f"NOT-YET ({agreement:.0%} < {REQUIRED_AGREEMENT:.0%})"

    # Criterion 3: FP <= 15%
    # Counted from ADJUDICATED judge_warn ledger rows only (observer-disposition.py).
    # Honest UNKNOWN until at least one warn is adjudicated — never 0, never fabricated.
    fp = _false_positive_rollup()
    if fp["rate"] is None:
        fp_str = "UNKNOWN (0 judge warns adjudicated in observer-proposals/ledger.jsonl)"
        fp_verdict = "UNKNOWN"
    else:
        fp_str = f"{fp['rate']:.0%} ({fp['n_fp']} FP / {fp['n_adjudicated']} adjudicated)"
        fp_verdict = (
            "PASS" if fp["rate"] <= REQUIRED_FP_MAX
            else f"NOT-YET ({fp['rate']:.0%} > {REQUIRED_FP_MAX:.0%})"
        )

    lines += [
        f"| Criterion | Required | Current | Verdict |",
        f"|---|---|---|---|",
        f"| N (scored change_ids) | ≥{REQUIRED_N} | {n_str} | {n_verdict} |",
        f"| Agreement (CLEAN rate) | ≥{REQUIRED_AGREEMENT:.0%} | {agreement_str} | {agreement_verdict} |",
        f"| False-positive rate | ≤{REQUIRED_FP_MAX:.0%} | {fp_str} | {fp_verdict} |",
        "",
        "---",
        "",
        "## Overall Readiness",
        "",
    ]

    all_verdicts = [n_verdict, agreement_verdict, fp_verdict]
    has_unknown = any(v == "UNKNOWN" for v in all_verdicts)
    has_not_yet = any(v.startswith("NOT-YET") for v in all_verdicts)

    if has_unknown:
        overall = "UNKNOWN — one or more criteria cannot be evaluated yet"
    elif has_not_yet:
        fails = [v for v in all_verdicts if v.startswith("NOT-YET")]
        overall = f"NOT-YET — {len(fails)} criterion/criteria below threshold"
    else:
        overall = "PASS — all criteria met (manual NN#10-gated change still required to flip)"

    lines += [
        f"**{overall}**",
        "",
        (
            "> Reminder: even if all criteria PASS, the actual flip (block_enabled: true) "
            "requires a SEPARATE, dated, NN#10-gated change_id on or after 2026-06-21. "
            "This script cannot trigger the flip — it only reports readiness."
        ),
    ]

    DOCTOR.mkdir(parents=True, exist_ok=True)
    out_path = DOCTOR / "judge-flip-readiness.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    today = datetime.now(timezone.utc).date()
    config = _load_config()
    notebook = _parse_notebook_scores()

    if verbose:
        print(f"Config loaded: {config is not None}")
        print(f"Notebook: n_scored={notebook.get('n_scored')} n_clean={notebook.get('n_clean')}")

    out_path = _write_report(config, notebook, today)

    if verbose:
        print(f"Report written: {out_path}")

    return 0  # always exit 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"[judge-flip-readiness] unhandled error:\n{traceback.format_exc()}\n")
        sys.exit(0)
