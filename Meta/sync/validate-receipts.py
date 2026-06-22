#!/usr/bin/env python3
"""
validate-receipts.py — Log-only schema validator for Meta/receipts/.

Checks every receipt in Meta/receipts/ against the canonical Cerebrum schema.

Canonical schema:
  Filename : <agent>-<YYYY-MM-DD>-<HHMM>-<task-slug>.md
  Frontmatter: agent, task, date, time, status (required)
  Status values: DONE | FAIL | BLOCKED | PARTIAL
  Body sections: ## Task, ## Outcome, ## Files written
  Legacy fields: task-id, task_id, timestamp, complete → DEPRECATED (not MISSING)

v3.0.2 additions (all OPTIONAL — warn-not-fail in non-strict mode):
  Frontmatter: change_type, tags, trigger_kind, trigger_ref, session_id
  Body: ## Rationale (200-500 char target), ## Linked artifacts (bulleted list)

v3.0.2 receipt-graph additions (all OPTIONAL — warn-not-fail):
  change_id   : free-form string — the logical change this receipt belongs to
  gated_by    : YAML inline list of predecessor receipt filenames (causal edges)
  review_mode : plan | output — which NN#10 review gate (contrarian-only)
  review_verdict: PASS | FAIL — machine-readable gate outcome (contrarian-only)

Usage:
  python3 validate-receipts.py                  # scan all of Meta/receipts/
  python3 validate-receipts.py --since 2026-05-20
  python3 validate-receipts.py --dry-run        # print to stdout, don't write report
  python3 validate-receipts.py --strict         # exit non-zero if any violation found
  python3 validate-receipts.py --file <path>    # validate a single receipt, print result
  python3 validate-receipts.py --root /path/to/vault

Root resolution order:
  1. WULONG_ROOT environment variable
  2. --root CLI argument
  3. Repo root inferred from this script's location (../../.. from Meta/sync/)
"""

import argparse
import os
import re
import sys
from datetime import datetime, date
from typing import Optional


def _resolve_root(cli_root: Optional[str] = None) -> str:
    env = os.environ.get("WULONG_ROOT", "").strip()
    if env:
        return env
    if cli_root:
        return cli_root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

FILENAME_PRIMARY_RE = re.compile(
    r"^(?P<agent>[a-z][a-z0-9_-]+)-(?P<date>\d{4}-\d{2}-\d{2})-(?P<hhmm>\d{4})-(?P<slug>[a-zA-Z0-9][a-zA-Z0-9.-]{0,59})\.md$"
)

FILENAME_LEGACY_RE = re.compile(
    r"^(?P<agent>[a-z][a-z0-9_-]+)-(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-zA-Z0-9][a-zA-Z0-9_A-Z-]{0,80})\.md$"
)

REQUIRED_FIELDS = {"agent", "task", "date", "time", "status"}

LEGACY_FIELD_MAP = {
    "task-id": "task",
    "task_id": "task",
    "timestamp": "date+time",
    "complete": "status",
    "created": "date+time",
}

CANONICAL_STATUSES = {"DONE", "FAIL", "BLOCKED", "PARTIAL"}

STATUS_NORMALISATION: dict[str, str] = {
    "complete": "DONE",
    "Complete": "DONE",
    "COMPLETE": "DONE",
    "dispatched": "DONE",
    "DISPATCHED": "DONE",
    "SMOKE_FAIL": "FAIL",
    "CONTRARIAN GATE OPEN": "PARTIAL",
}

REQUIRED_SECTIONS = ["## Task", "## Outcome", "## Files written"]
PARTIAL_EXTRA_SECTION = "## Next Step"

# ---------------------------------------------------------------------------
# Violation type codes
# ---------------------------------------------------------------------------

VT_FILENAME = "FILENAME_PATTERN_MISMATCH"
VT_PARSE    = "FRONTMATTER_PARSE_ERROR"
VT_MISSING  = "MISSING_FIELD"
VT_STATUS   = "INVALID_STATUS"
VT_SECTION  = "MISSING_SECTION"
VT_DEPR     = "DEPRECATED_FIELD"
VT_R3       = "AGENT_FILENAME_MISMATCH"
VT_R4       = "DATE_FILENAME_MISMATCH"
VT_R5       = "TIME_FILENAME_MISMATCH"
VT_R8       = "MISSING_NEXT_STEP_FOR_PARTIAL"

VT_W_MISSING_RECOMMENDED = "MISSING_RECOMMENDED_FIELD_V302"
VT_W_INVALID_ENUM        = "INVALID_ENUM_V302"
VT_W_TAGS_NOT_LIST       = "TAGS_NOT_LIST_V302"
VT_W_RATIONALE_EMPTY     = "RATIONALE_EMPTY_V302"
VT_W_RATIONALE_LENGTH    = "RATIONALE_LENGTH_V302"
VT_W_ARTIFACTS_NOT_LIST  = "LINKED_ARTIFACTS_NOT_LIST_V302"

VT_W_GATED_BY_NOT_LIST           = "GATED_BY_NOT_LIST"
VT_W_VERDICT_ON_NON_CONTRARIAN   = "VT_W_VERDICT_ON_NON_CONTRARIAN"
VT_W_VERDICT_INVALID             = "VT_W_VERDICT_INVALID"
VT_W_REVIEW_MODE_INVALID         = "VT_W_REVIEW_MODE_INVALID"
VT_W_VERDICT_INCOMPLETE          = "VT_W_VERDICT_INCOMPLETE"

V302_CUTOFF = date(2026, 5, 29)

REVIEW_MODE_ENUM    = {"plan", "output"}
REVIEW_VERDICT_ENUM = {"PASS", "FAIL"}

CHANGE_TYPE_ENUM = {"feature", "fix", "governance", "docs", "housekeeping"}
TRIGGER_KIND_ENUM = {
    "user_request", "contrarian_fail", "scheduled", "observation_threshold",
    "upstream_handoff", "self_initiated", "system_event",
}

RATIONALE_MIN_CHARS = 200
RATIONALE_MAX_CHARS = 500


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> tuple[Optional[dict[str, str]], str]:
    """Parse YAML frontmatter from receipt content.

    Returns (fields_dict, body_text). fields_dict is None if frontmatter is absent
    or unparseable. Body text excludes the frontmatter block.
    """
    if not content.startswith("---"):
        return None, content

    lines = content.split("\n")
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break

    if close_idx is None:
        return None, content

    frontmatter_lines = lines[1:close_idx]
    body = "\n".join(lines[close_idx + 1:])

    fields: dict[str, str] = {}
    for line in frontmatter_lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()

    return fields, body


# ---------------------------------------------------------------------------
# Per-file validation
# ---------------------------------------------------------------------------

def validate_receipt(filepath: str) -> list[dict]:
    """Validate a single receipt file against the canonical schema.

    Returns a list of violation dicts with keys: code, detail.
    Empty list means the file is clean.
    """
    violations: list[dict] = []
    fname = os.path.basename(filepath)

    primary_match = FILENAME_PRIMARY_RE.match(fname)
    legacy_match = FILENAME_LEGACY_RE.match(fname) if not primary_match else None
    has_hhmm = primary_match is not None

    if not primary_match and not legacy_match:
        violations.append({
            "code": VT_FILENAME,
            "detail": f"'{fname}' does not match <agent>-<YYYY-MM-DD>-<HHMM>-<slug>.md or legacy variant",
        })
        fn_agent: Optional[str] = None
        fn_date: Optional[str] = None
        fn_hhmm: Optional[str] = None
    elif primary_match:
        fn_agent = primary_match.group("agent")
        fn_date = primary_match.group("date")
        fn_hhmm = primary_match.group("hhmm")
    else:
        violations.append({
            "code": VT_FILENAME,
            "detail": f"'{fname}' uses legacy pattern (no HHMM component) — soft warning",
        })
        fn_agent = legacy_match.group("agent")  # type: ignore[union-attr]
        fn_date = legacy_match.group("date")    # type: ignore[union-attr]
        fn_hhmm = None

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError as exc:
        violations.append({"code": VT_PARSE, "detail": f"Cannot read file: {exc}"})
        return violations

    fields, body = parse_frontmatter(content)

    if fields is None:
        violations.append({
            "code": VT_PARSE,
            "detail": "Frontmatter block absent or closing '---' not found",
        })
        _check_body_sections(body if isinstance(body, str) else content, None, violations)
        return violations

    deprecated_present: set[str] = set()
    for depr_field in LEGACY_FIELD_MAP:
        if depr_field in fields:
            violations.append({
                "code": VT_DEPR,
                "detail": f"'{depr_field}' is a legacy alias — migrate to '{LEGACY_FIELD_MAP[depr_field]}'",
            })
            deprecated_present.add(depr_field)

    effective = dict(fields)
    if "task-id" in effective and "task" not in effective:
        effective["task"] = effective.pop("task-id")
    if "task_id" in effective and "task" not in effective:
        effective["task"] = effective.pop("task_id")
    if "timestamp" in effective and ("date" not in effective or "time" not in effective):
        ts_val = effective.get("timestamp", "")
        ts_parts = ts_val.split()
        if len(ts_parts) >= 2:
            if "date" not in effective:
                effective["date"] = ts_parts[0]
            if "time" not in effective:
                effective["time"] = ts_parts[1].replace(":", "")
    if "created" in effective and ("date" not in effective or "time" not in effective):
        created_val = effective.get("created", "")
        created_parts = re.split(r"[T\s]", created_val)
        if len(created_parts) >= 2:
            if "date" not in effective:
                effective["date"] = created_parts[0]
            if "time" not in effective:
                effective["time"] = created_parts[1].replace(":", "")
        elif re.match(r"\d{4}-\d{2}-\d{2}-\d{4}", created_val):
            segments = created_val.split("-")
            if "date" not in effective:
                effective["date"] = "-".join(segments[:3])
            if "time" not in effective:
                effective["time"] = segments[3]

    for req_field in REQUIRED_FIELDS:
        if req_field not in effective:
            violations.append({
                "code": VT_MISSING,
                "detail": f"Required field '{req_field}' absent (not resolvable via legacy aliases)",
            })

    raw_status = effective.get("status", "")
    if raw_status:
        normalised = STATUS_NORMALISATION.get(raw_status, raw_status)
        if normalised not in CANONICAL_STATUSES:
            violations.append({
                "code": VT_STATUS,
                "detail": f"status='{raw_status}' not in {{DONE, FAIL, BLOCKED, PARTIAL}} and has no known normalisation",
            })
        effective["_normalised_status"] = normalised

    fm_agent = effective.get("agent", "")
    if fn_agent and fm_agent and fm_agent != fn_agent:
        violations.append({
            "code": VT_R3,
            "detail": f"frontmatter agent='{fm_agent}' does not match filename prefix '{fn_agent}'",
        })

    fm_date = effective.get("date", "")
    if fn_date and fm_date and fm_date != fn_date:
        fm_date_only = fm_date.split()[0] if " " in fm_date else fm_date
        if fm_date_only != fn_date:
            violations.append({
                "code": VT_R4,
                "detail": f"frontmatter date='{fm_date}' does not match filename date '{fn_date}'",
            })

    fm_time = effective.get("time", "")
    if fn_hhmm and fm_time:
        fm_time_norm = fm_time.strip('"\'').replace(":", "").replace(" ", "").split(".")[0]
        if fm_time_norm != fn_hhmm:
            violations.append({
                "code": VT_R5,
                "detail": f"frontmatter time='{fm_time}' does not match filename HHMM '{fn_hhmm}'",
            })

    normalised_status = effective.get("_normalised_status", STATUS_NORMALISATION.get(raw_status, raw_status))
    _check_body_sections(body, normalised_status, violations)

    fm_date_raw = effective.get("date", "")
    receipt_date: Optional[date] = None
    try:
        date_str = fm_date_raw.split()[0] if " " in fm_date_raw else fm_date_raw
        receipt_date = date.fromisoformat(date_str)
    except (ValueError, AttributeError):
        pass

    if receipt_date is not None and receipt_date >= V302_CUTOFF:
        _check_v302_fields(fields, body, filepath, violations)

    return violations


def _check_v302_fields(
    fields: dict[str, str],
    body: str,
    filepath: str,
    violations: list[dict],
) -> None:
    """Check v3.0.2 optional recommended fields for receipts dated 2026-05-29+."""
    path_label = os.path.basename(filepath)

    if "change_type" not in fields:
        _warn_v302(violations, VT_W_MISSING_RECOMMENDED,
                   f"receipt {path_label} dated 2026-05-29+ missing recommended field 'change_type'",
                   path_label)
    else:
        val = fields["change_type"].strip()
        if val not in CHANGE_TYPE_ENUM:
            violations.append({
                "code": VT_W_INVALID_ENUM,
                "detail": f"change_type='{val}' not in {sorted(CHANGE_TYPE_ENUM)}",
                "is_warn": True,
            })

    if "tags" in fields:
        tags_raw = fields["tags"].strip()
        if not (tags_raw.startswith("[") and tags_raw.endswith("]")):
            violations.append({
                "code": VT_W_TAGS_NOT_LIST,
                "detail": f"tags value '{tags_raw[:60]}' is not a YAML inline list (expected [a, b, c])",
                "is_warn": True,
            })

    if "trigger_kind" not in fields:
        _warn_v302(violations, VT_W_MISSING_RECOMMENDED,
                   f"receipt {path_label} dated 2026-05-29+ missing recommended field 'trigger_kind'",
                   path_label)
    else:
        val = fields["trigger_kind"].strip()
        if val not in TRIGGER_KIND_ENUM:
            violations.append({
                "code": VT_W_INVALID_ENUM,
                "detail": f"trigger_kind='{val}' not in {sorted(TRIGGER_KIND_ENUM)}",
                "is_warn": True,
            })

    rationale_text = _extract_section_body(body, "## Rationale")
    if rationale_text is None:
        _warn_v302(violations, VT_W_MISSING_RECOMMENDED,
                   f"receipt {path_label} dated 2026-05-29+ missing recommended section '## Rationale'",
                   path_label)
    else:
        stripped = rationale_text.strip()
        if not stripped:
            violations.append({
                "code": VT_W_RATIONALE_EMPTY,
                "detail": "## Rationale section is present but empty",
                "is_warn": True,
            })
        else:
            char_count = len(stripped)
            if char_count < RATIONALE_MIN_CHARS or char_count > RATIONALE_MAX_CHARS:
                violations.append({
                    "code": VT_W_RATIONALE_LENGTH,
                    "detail": (
                        f"## Rationale is {char_count} chars "
                        f"(target {RATIONALE_MIN_CHARS}-{RATIONALE_MAX_CHARS}) — warn only"
                    ),
                    "is_warn": True,
                })

    artifacts_text = _extract_section_body(body, "## Linked artifacts")
    if artifacts_text is not None:
        stripped = artifacts_text.strip()
        bullet_lines = [l for l in stripped.splitlines() if l.strip().startswith("-")]
        if not bullet_lines:
            violations.append({
                "code": VT_W_ARTIFACTS_NOT_LIST,
                "detail": "## Linked artifacts section is present but contains no bullet items",
                "is_warn": True,
            })

    _check_graph_fields(fields, filepath, violations)


def _warn_v302(violations: list[dict], code: str, detail: str, path_label: str) -> None:
    print(f"[validate-receipts] WARN: {detail}", file=sys.stderr)
    violations.append({"code": code, "detail": detail, "is_warn": True})


def _check_graph_fields(
    fields: dict[str, str],
    filepath: str,
    violations: list[dict],
) -> None:
    """Validate receipt-graph edge fields (change_id, gated_by, review_mode, review_verdict)."""
    label = os.path.basename(filepath)
    agent_val = fields.get("agent", "").strip()
    is_contrarian = agent_val == "contrarian"

    if "gated_by" in fields:
        raw = fields["gated_by"].strip()
        if not (raw.startswith("[") and raw.endswith("]")):
            violations.append({
                "code": VT_W_GATED_BY_NOT_LIST,
                "detail": f"gated_by='{raw[:80]}' is not a YAML inline list (expected [a, b, c])",
                "is_warn": True,
            })

    has_review_mode    = "review_mode" in fields
    has_review_verdict = "review_verdict" in fields

    if (has_review_mode or has_review_verdict) and not is_contrarian:
        violations.append({
            "code": VT_W_VERDICT_ON_NON_CONTRARIAN,
            "detail": (
                f"review_mode/review_verdict present on agent='{agent_val}' "
                f"in {label} — these fields are valid only on agent:contrarian"
            ),
            "is_warn": True,
        })
        _shape_validate_review_fields(fields, label, violations)
        return

    _shape_validate_review_fields(fields, label, violations)

    if is_contrarian and (has_review_mode ^ has_review_verdict):
        missing = "review_verdict" if has_review_mode else "review_mode"
        present = "review_mode" if has_review_mode else "review_verdict"
        violations.append({
            "code": VT_W_VERDICT_INCOMPLETE,
            "detail": (
                f"{label}: contrarian receipt has '{present}' but missing '{missing}' "
                f"— both must be present together or both omitted"
            ),
            "is_warn": True,
        })


def _shape_validate_review_fields(
    fields: dict[str, str],
    label: str,
    violations: list[dict],
) -> None:
    if "review_mode" in fields:
        val = fields["review_mode"].strip()
        if val not in REVIEW_MODE_ENUM:
            violations.append({
                "code": VT_W_REVIEW_MODE_INVALID,
                "detail": f"review_mode='{val}' not in {{plan, output}} in {label}",
                "is_warn": True,
            })

    if "review_verdict" in fields:
        val = fields["review_verdict"].strip()
        if val not in REVIEW_VERDICT_ENUM:
            violations.append({
                "code": VT_W_VERDICT_INVALID,
                "detail": f"review_verdict='{val}' not in {{PASS, FAIL}} in {label}",
                "is_warn": True,
            })


def _extract_section_body(body: str, heading: str) -> Optional[str]:
    """Return the text between `heading` and the next same-level heading, or None if absent."""
    heading_lower = heading.lower().rstrip()
    level = len(heading_lower) - len(heading_lower.lstrip("#"))
    next_heading_re = re.compile(r"^#{" + str(level) + r"}[^#]", re.MULTILINE)

    lines = body.split("\n")
    start_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.lower().strip() == heading_lower.strip():
            start_idx = i
            break

    if start_idx is None:
        return None

    section_lines: list[str] = []
    for line in lines[start_idx + 1:]:
        if next_heading_re.match(line):
            break
        section_lines.append(line)

    return "\n".join(section_lines)


def _check_body_sections(body: str, normalised_status: Optional[str], violations: list[dict]) -> None:
    body_lower = body.lower()

    for section in REQUIRED_SECTIONS:
        if section.lower() not in body_lower:
            violations.append({
                "code": VT_SECTION,
                "detail": f"Required section '{section}' not found in body",
            })

    if normalised_status == "PARTIAL" and PARTIAL_EXTRA_SECTION.lower() not in body_lower:
        violations.append({
            "code": VT_R8,
            "detail": f"status=PARTIAL but '{PARTIAL_EXTRA_SECTION}' section is absent",
        })


# ---------------------------------------------------------------------------
# Full-directory scan
# ---------------------------------------------------------------------------

def scan_receipts_dir(
    receipts_dir: str,
    since: Optional[date] = None,
) -> dict[str, list[dict]]:
    """Scan all .md files in receipts_dir (non-recursive)."""
    results: dict[str, list[dict]] = {}

    if not os.path.isdir(receipts_dir):
        return results

    for entry in sorted(os.listdir(receipts_dir)):
        if not entry.endswith(".md"):
            continue
        full_path = os.path.join(receipts_dir, entry)
        if not os.path.isfile(full_path):
            continue

        if since is not None:
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})", entry)
            if date_m:
                try:
                    file_date = date.fromisoformat(date_m.group(1))
                    if file_date < since:
                        continue
                except ValueError:
                    pass
            else:
                mtime = date.fromtimestamp(os.path.getmtime(full_path))
                if mtime < since:
                    continue

        violations = validate_receipt(full_path)
        results[entry] = violations

    return results


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def build_report_section(
    results: dict[str, list[dict]],
    run_ts: str,
) -> str:
    total = len(results)
    all_violations = [v for vlist in results.values() for v in vlist]
    hard_violations = [v for v in all_violations if not v.get("is_warn")]
    warn_violations = [v for v in all_violations if v.get("is_warn")]
    files_with_violations = sum(1 for vlist in results.values() if vlist)

    breakdown: dict[str, int] = {}
    for v in all_violations:
        breakdown[v["code"]] = breakdown.get(v["code"], 0) + 1

    lines: list[str] = []
    lines.append(
        f"\n## Validation run {run_ts} ({total} receipts scanned, "
        f"{len(hard_violations)} violations, {len(warn_violations)} warnings)\n"
    )

    if len(all_violations) == 0:
        lines[-1] = lines[-1].rstrip() + " — clean\n"
        return "".join(lines)

    lines.append(f"- Total receipts scanned: {total}\n")
    lines.append(f"- Receipts with at least one issue: {files_with_violations}\n")
    lines.append(f"- Hard violations: {len(hard_violations)}\n")
    lines.append(f"- Warnings (v3.0.2 optional fields): {len(warn_violations)}\n")
    if breakdown:
        lines.append("- Breakdown by type:\n")
        for code, count in sorted(breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  - {code}: {count}\n")

    lines.append("\n### Issues by file\n\n")
    for fname, file_violations in sorted(results.items()):
        if not file_violations:
            continue
        lines.append(f"**{fname}**\n")
        for v in file_violations:
            prefix = "WARN" if v.get("is_warn") else "VIOLATION"
            lines.append(f"- [{prefix}] {v['code']}: {v['detail']}\n")
        lines.append("\n")

    return "".join(lines)


_HEADER = (
    "# Receipt Schema Violations\n\n"
    "Auto-generated by `Meta/sync/validate-receipts.py`. "
    "Violations are LOGGED, not blocking (v1).\n"
    "Rotated: last 5 runs kept.\n"
)
_MAX_RUNS = 5
_RUN_DELIMITER = "\n## Validation run "


def write_report(section: str, report_path: str) -> None:
    """Write the validation section to the log file, keeping only the last 5 runs."""
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    existing_runs: list[str] = []
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            raw = f.read()
        parts = raw.split(_RUN_DELIMITER)
        existing_runs = parts[1:]

    new_run = section.lstrip("\n")
    if new_run.startswith("## Validation run "):
        new_run = new_run[len("## Validation run "):]

    existing_runs.append(new_run)
    kept_runs = existing_runs[-_MAX_RUNS:]

    content = _HEADER + _RUN_DELIMITER.join([""] + kept_runs)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)


def print_summary(results: dict[str, list[dict]]) -> None:
    total = len(results)
    files_with_issues = sum(1 for v in results.values() if v)
    all_violations = [v for vlist in results.values() for v in vlist]
    hard_violations = [v for v in all_violations if not v.get("is_warn")]
    warn_violations = [v for v in all_violations if v.get("is_warn")]

    print(
        f"[validate-receipts] {total} receipts scanned, "
        f"{files_with_issues} with issues, "
        f"{len(hard_violations)} violations, "
        f"{len(warn_violations)} warnings (v3.0.2 optional fields)."
    )

    if all_violations:
        breakdown: dict[str, int] = {}
        for v in all_violations:
            breakdown[v["code"]] = breakdown.get(v["code"], 0) + 1
        for code, count in sorted(breakdown.items(), key=lambda x: -x[1]):
            print(f"  {code}: {count}")


def validate_single_file(filepath: str) -> int:
    if not os.path.isfile(filepath):
        print(f"ERROR: not a file: {filepath}", file=sys.stderr)
        return 1
    violations = validate_receipt(filepath)
    fname = os.path.basename(filepath)
    if not violations:
        print(f"CLEAN: {fname}")
        return 0
    print(f"VIOLATIONS ({len(violations)}): {fname}")
    for v in violations:
        print(f"  - {v['code']}: {v['detail']}")
    return len(violations)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate Meta/receipts/ against canonical schema."
    )
    p.add_argument("--since", type=str, default=None, help="Only validate receipts on or after this date (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true", help="Print report to stdout instead of writing to doctor/")
    p.add_argument("--strict", action="store_true", help="Exit non-zero if any violation found")
    p.add_argument("--file", type=str, default=None, metavar="PATH", help="Validate a single receipt file")
    p.add_argument("--root", type=str, default=None, help="Vault root path (overrides WULONG_ROOT env var)")
    return p.parse_args(argv)


def main(args: Optional[list[str]] = None) -> int:
    parsed = parse_args(args)
    root = _resolve_root(parsed.root)
    receipts_dir = os.path.join(root, "Meta", "receipts")
    report_path  = os.path.join(root, "Meta", "doctor", "receipt-schema-violations.log")

    if parsed.file:
        count = validate_single_file(parsed.file)
        return 1 if (parsed.strict and count > 0) else 0

    since_date: Optional[date] = None
    if parsed.since:
        try:
            since_date = date.fromisoformat(parsed.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got '{parsed.since}'", file=sys.stderr)
            return 2

    results = scan_receipts_dir(receipts_dir, since=since_date)
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
    section = build_report_section(results, run_ts)

    if parsed.dry_run:
        print(section)
    else:
        write_report(section, report_path)

    print_summary(results)

    all_violations = [v for vlist in results.values() for v in vlist]
    hard_violations = [v for v in all_violations if not v.get("is_warn")]
    if parsed.strict and all_violations:
        return 1
    if hard_violations:
        return 0  # log-only in v1
    return 0


if __name__ == "__main__":
    sys.exit(main())
