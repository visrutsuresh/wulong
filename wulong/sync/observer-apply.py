#!/usr/bin/env python3
"""
observer-apply.py — mechanical applier for contrarian-gated observer self-apply loop.

Execution order per ADR-006 DQ7 + Dependencies table:
  1. HALT sentinel (Meta/observer-apply/HALT) + per-observer paused flag check.
     Fail-closed: any exception during either check → treat as HALT present.
  2. Rate-limit read (Meta/observer-apply/rate-limit.jsonl) + enforce N=1/observer/UTC-day.
  3. Oscillation check against SSOT Meta/observer-proposals/ledger.jsonl:
       no-op guard   — last applied_value == proposed → reject(rejected_noop)
       ping-pong guard — applied → reverted within M=14 days → park(oscillating)
  4. Surface-manifest forbidden re-check.
  5. Allow-list re-check (Meta/observer-apply/self-apply-allowlist.yaml).
     Tolerate-absent → STOP with BLOCKED status (ar-director still working).
  6. patch_sha256 verify against target bytes (TOCTOU guard).
  7. Apply diff.
  8. Write receipt + change-log line + CEO-notify line to Meta/agent-messages.md
     (all via fcntl.flock).
  9. Append rate-limit ledger row.
  10. Refuse diff_kind == agent_def (must go to ar-director).

Also verifies that the contrarian PASS receipt exists on disk before applying.

Usage (called by Jarvis step 5ob):
    python3 Meta/sync/observer-apply.py --inbox-item Meta/observer-apply/inbox/<id>.json

Exit codes:
  0 = applied (happy path)
  1 = refused (validation failure; item not parked)
  2 = parked  (rate-limit or oscillation; item moved to parked/)
  3 = HALT    (sentinel present or per-observer pause)
  4 = BLOCKED (allow-list YAML absent — dependency not yet delivered)
  5 = filesystem / unexpected error
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)

HALT_SENTINEL = VAULT / "Meta" / "observer-apply" / "HALT"
INBOX_DIR = VAULT / "Meta" / "observer-apply" / "inbox"
PARKED_DIR = VAULT / "Meta" / "observer-apply" / "parked"
RATE_LIMIT_LEDGER = VAULT / "Meta" / "observer-apply" / "rate-limit.jsonl"
ALLOWLIST_PATH = VAULT / "Meta" / "observer-apply" / "self-apply-allowlist.yaml"
LEDGER_PATH = VAULT / "Meta" / "observer-proposals" / "ledger.jsonl"
SURFACE_MANIFEST = VAULT / "Meta" / "hermes" / "surface-manifest.yaml"
RECEIPTS_DIR = VAULT / "Meta" / "receipts"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"
AGENT_MESSAGES = VAULT / "Meta" / "agent-messages.md"

RATE_CAP_PER_DAY = 1   # OQ1 CEO-confirmed: N=1
OSCILLATION_WINDOW_DAYS = 14  # OQ1 CEO-confirmed: M=14


# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 — HALT + pause check (fail-closed)
# ──────────────────────────────────────────────────────────────────────────────

def check_halt(observer: str) -> str | None:
    """Return a reason string if halted/paused, None if clear. Fail-closed."""
    try:
        if HALT_SENTINEL.exists():
            content = HALT_SENTINEL.read_text(encoding="utf-8").strip()
            return f"HALT sentinel present: {content or '(no content)'}"
    except Exception as e:
        return f"HALT check exception (fail-closed): {e}"

    # Per-observer paused flag
    try:
        observer_key = "hermes" if observer == "hermes" else "metis"
        cfg_path = VAULT / "Meta" / observer_key / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if cfg.get("paused") is True:
                return f"observer {observer!r} paused flag set in config.json"
    except Exception as e:
        return f"per-observer pause check exception (fail-closed): {e}"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 — Rate-limit check
# ──────────────────────────────────────────────────────────────────────────────

def count_today_applies(observer: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    if not RATE_LIMIT_LEDGER.exists():
        return 0
    count = 0
    for line in RATE_LIMIT_LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("observer") == observer and row.get("ts_utc", "").startswith(today):
            count += 1
    return count


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 — Oscillation check
# ──────────────────────────────────────────────────────────────────────────────

def _load_ledger_rows() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    rows = []
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def oscillation_check(variable: str, proposed_value: str) -> str | None:
    """
    Returns None if clear, or a reason string for no-op / ping-pong detection.

    No-op guard: if last applied_value == proposed_value → rejected_noop.
    Ping-pong guard: if applied → reverted within M days → oscillating.
    """
    rows = _load_ledger_rows()
    var_rows = [r for r in rows if r.get("variable") == variable]
    if not var_rows:
        return None

    # No-op guard: find most recent row with verdict=applied
    applied_rows = [r for r in var_rows if r.get("verdict") == "applied"]
    if applied_rows:
        # Last applied row (assumes rows ordered by time, use ts_utc for sort)
        last_applied = sorted(
            applied_rows,
            key=lambda r: r.get("ts_utc", ""),
        )[-1]
        if str(last_applied.get("applied_value", "")).strip() == str(proposed_value).strip():
            return "rejected_noop: applied_value already equals proposed_value"

    # Ping-pong guard: applied followed by reverted within M days
    cutoff = datetime.now(timezone.utc) - timedelta(days=OSCILLATION_WINDOW_DAYS)
    recent = [
        r for r in var_rows
        if _parse_ts(r.get("ts_utc", "")) is not None
        and _parse_ts(r.get("ts_utc", "")) >= cutoff
    ]
    verdicts = [r.get("verdict") for r in sorted(recent, key=lambda r: r.get("ts_utc", ""))]
    # Look for applied → reverted (or rejected_noop right after applied) pattern
    for i, v in enumerate(verdicts):
        if v == "applied" and i + 1 < len(verdicts) and verdicts[i + 1] in ("reverted", "rejected_noop"):
            return f"oscillating: applied → {verdicts[i + 1]} within {OSCILLATION_WINDOW_DAYS}-day window"

    return None


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) if ts.endswith("Z") else datetime.fromisoformat(ts)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4 — Surface-manifest forbidden re-check
# ──────────────────────────────────────────────────────────────────────────────

def check_manifest_forbidden(variable: str) -> bool:
    """
    Return True if variable is forbidden in the surface-manifest. Fail-open on missing.

    Handles both dotted (scope.bare_name) and bare name conventions:
      - "ops.preflight_checklist_usage_target" matches forbidden entry "preflight_checklist_usage_target"
      - Also matches if the full dotted name is stored in forbidden
    Also checks the variable's declared scope if it contains a dot.
    """
    if not SURFACE_MANIFEST.exists():
        return False
    try:
        data = yaml.safe_load(SURFACE_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False

    # Derive scope and bare_name from dotted variable
    if "." in variable:
        scope_part, bare_name = variable.split(".", 1)
    else:
        scope_part, bare_name = None, variable

    for scope_key, scope_data in data.items():
        if not isinstance(scope_data, dict):
            continue
        for entry in scope_data.get("forbidden", []) or []:
            name = entry["name"] if isinstance(entry, dict) else entry
            # Match on full dotted name, bare name, or bare name within matching scope
            if name == variable:
                return True
            if name == bare_name:
                # Bare name match: confirm the scope key matches if a scope was inferred
                if scope_part is None or scope_part == scope_key:
                    return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# STEP 5 — Allow-list re-check
# ──────────────────────────────────────────────────────────────────────────────

def load_allowlist() -> dict | None:
    """
    Returns the parsed allowlist dict, or None if the file is absent.
    Raises on parse error (fail-closed).
    """
    if not ALLOWLIST_PATH.exists():
        return None
    return yaml.safe_load(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def check_allowlist(allowlist: dict, variable: str) -> bool:
    """Return True if variable is allowed. Expected YAML structure (ADR-006 DQ5):
       allow:
         - variable: "Meta/hermes/config.json: observe_threshold.*"
           ...
    Source-of-truth key is `allow` per Meta/observer-apply/self-apply-allowlist.yaml.
    """
    entries = allowlist.get("allow", []) or []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("variable", "")
        else:
            name = str(entry)
        # Support wildcard suffix (e.g. "observe_threshold.*")
        if name.endswith(".*"):
            prefix = name[:-2]
            if variable.startswith(prefix):
                return True
        elif name == variable:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# STEP 6 — patch_sha256 verify
# ──────────────────────────────────────────────────────────────────────────────

def verify_patch_sha256(patch_bytes: bytes, expected_sha256: str) -> bool:
    actual = hashlib.sha256(patch_bytes).hexdigest()
    return actual == expected_sha256


# ──────────────────────────────────────────────────────────────────────────────
# STEP 7 — Apply diff
# ──────────────────────────────────────────────────────────────────────────────

def apply_diff(item: dict) -> None:
    """
    Apply the diff described in item['diff']. Currently supports diff_kind:
      config_knob — JSON-path set operation (target_file must be JSON).
      manifest_entry — JSON-patch in unified diff text format (future).

    For config_knob: patch is expected to be a dict {"json_path": [...], "value": ...}
    OR a unified diff string. In v1 only config_knob JSON-path ops are used.
    """
    diff = item["diff"]
    diff_kind = diff.get("diff_kind", "")
    target_file = Path(diff["target_file"])

    if diff_kind == "agent_def":
        # Should have been caught at step 10, but be defensive
        raise ValueError("diff_kind=agent_def must not reach apply_diff — route to ar-director")

    patch = diff.get("patch", "")

    if diff_kind in ("config_knob", "notebook_param"):
        # patch is a JSON object with json_path (list) and value
        if isinstance(patch, str):
            op = json.loads(patch)
        else:
            op = patch
        data = json.loads(target_file.read_text(encoding="utf-8"))
        _json_path_set(data, op["json_path"], op["value"])
        target_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    elif diff_kind in ("manifest_entry", "checklist_line"):
        # patch is a unified diff string — apply via Python difflib patching
        import subprocess  # noqa: PLC0415
        result = subprocess.run(
            ["patch", "--no-backup-if-mismatch", "-p0", str(target_file)],
            input=patch.encode("utf-8"),
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"patch command failed: {result.stderr.decode()}")

    else:
        raise ValueError(f"Unsupported diff_kind: {diff_kind!r}")


def _json_path_set(obj: dict, path: list, value) -> None:
    """Set obj[path[0]][path[1]]... = value, creating dicts as needed."""
    for key in path[:-1]:
        if key not in obj or not isinstance(obj[key], dict):
            obj[key] = {}
        obj = obj[key]
    obj[path[-1]] = value


# ──────────────────────────────────────────────────────────────────────────────
# STEP 8 — Write receipt + change-log + CEO-notify (all via fcntl.flock)
# ──────────────────────────────────────────────────────────────────────────────

def write_receipt(item: dict, contrarian_receipt: str, status: str, outcome_note: str) -> Path:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d-%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", item.get("variable", "unknown").lower())[:40]
    receipt_name = f"observer-apply-{item.get('observer', 'unknown')}-{ts}-{slug}.md"
    receipt_path = RECEIPTS_DIR / receipt_name

    variable = item.get("variable", "")
    proposal_id = item.get("proposal_id", "")
    observer = item.get("observer", "")
    change_id = item.get("change_id", "adr-006-observer-apply-loop-2026-06-13")

    lines = [
        "---",
        f"agent: observer-apply",
        f"task: self-apply {variable}",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"time: {now.strftime('%H:%M')}",
        f"status: {status}",
        f"change_type: governance",
        f"change_id: {change_id}",
        f"gated_by: [{contrarian_receipt}]",
        f"trigger_kind: observation_threshold",
        f"trigger_ref: {item.get('observation_id', '')}",
        f"tags: [observer-apply, {observer}, self-apply, governance]",
        "---",
        "",
        "## Task",
        f"Mechanical self-apply for proposal `{proposal_id}`: set `{variable}`.",
        "",
        "## Outcome",
        outcome_note,
        "",
        "## Files written",
        f"- {receipt_path.relative_to(VAULT)}",
    ]
    receipt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return receipt_path


def flock_append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def write_change_log(variable: str, observer: str, status: str, proposal_id: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = (
        f"[{now}] observer-apply → {status.upper()} {observer}/{variable} "
        f"(proposal={proposal_id})\n"
    )
    flock_append(CHANGE_LOG, line)


def write_ceo_notify(
    variable: str,
    observer: str,
    old_val: str,
    new_val: str,
    action: str,
    reason: str,
    proposal_id: str,
    contrarian_receipt: str,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = (
        f"\n## [{now}] observer-apply → CEO notify\n"
        f"{observer} {action} {variable}: `{old_val}` → `{new_val}` (reason: {reason}). "
        f"Proposal: {proposal_id}. Contrarian receipt: {contrarian_receipt}.\n"
        "\n---\n"
    )
    flock_append(AGENT_MESSAGES, line)


# ──────────────────────────────────────────────────────────────────────────────
# STEP 9 — Append rate-limit ledger row
# ──────────────────────────────────────────────────────────────────────────────

def append_rate_limit_row(item: dict) -> None:
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "observer": item.get("observer", ""),
        "variable": item.get("variable", ""),
        "proposal_id": item.get("proposal_id", ""),
    }
    flock_append(RATE_LIMIT_LEDGER, json.dumps(row) + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Park helper
# ──────────────────────────────────────────────────────────────────────────────

def park_item(item_path: Path, reason: str, observer: str) -> None:
    PARKED_DIR.mkdir(parents=True, exist_ok=True)
    park_meta = {
        "reason": reason,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "observer": observer,
        "original_inbox_path": str(item_path),
    }
    dest = PARKED_DIR / item_path.name
    # Write the original item plus the park reason in a wrapper
    original = json.loads(item_path.read_text(encoding="utf-8"))
    original["_parked"] = park_meta
    dest.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    item_path.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Contrarian receipt verification
# ──────────────────────────────────────────────────────────────────────────────

def verify_contrarian_receipt(receipt_filename: str) -> bool:
    """Check that the named contrarian PASS receipt exists on disk."""
    receipt_path = RECEIPTS_DIR / receipt_filename
    if not receipt_path.exists():
        return False
    text = receipt_path.read_text(encoding="utf-8")
    return "review_verdict: PASS" in text and "agent: contrarian" in text


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Observer mechanical applier (ADR-006).")
    ap.add_argument(
        "--inbox-item",
        required=True,
        help="Absolute path to the inbox JSON item to process.",
    )
    args = ap.parse_args(argv)

    item_path = Path(args.inbox_item)
    if not item_path.exists():
        sys.stderr.write(f"[observer-apply] FATAL: inbox item not found: {item_path}\n")
        return 5

    try:
        item = json.loads(item_path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(f"[observer-apply] FATAL: cannot parse inbox item: {e}\n")
        return 5

    observer = item.get("observer", "")
    variable = item.get("variable", "")
    proposal_id = item.get("proposal_id", "")
    current_value = str(item.get("current_value", ""))
    proposed_value = str(item.get("proposed_value", ""))
    diff = item.get("diff", {})
    diff_kind = diff.get("diff_kind", "")
    contrarian_receipt_file = item.get("contrarian_receipt", "")

    # ── STEP 10 guard: refuse agent_def diffs immediately ─────────────────────
    if diff_kind == "agent_def":
        sys.stderr.write(
            f"[observer-apply] REFUSE: diff_kind=agent_def is not handled by observer-apply.py. "
            "Route to ar-director per NN#6.\n"
        )
        write_change_log(variable, observer, "refused_agent_def", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", "diff_kind=agent_def — must route to ar-director",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    # ── STEP 1: HALT + pause check (fail-closed) ───────────────────────────────
    halt_reason = check_halt(observer)
    if halt_reason:
        sys.stderr.write(f"[observer-apply] HALT: {halt_reason}\n")
        write_change_log(variable, observer, "halted", proposal_id)
        return 3

    # ── Contrarian PASS receipt verify ────────────────────────────────────────
    if not contrarian_receipt_file:
        sys.stderr.write(
            "[observer-apply] REFUSE: inbox item missing 'contrarian_receipt' field.\n"
        )
        write_change_log(variable, observer, "refused_no_contrarian_receipt", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", "no contrarian_receipt field in inbox item",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    if not verify_contrarian_receipt(contrarian_receipt_file):
        sys.stderr.write(
            f"[observer-apply] REFUSE: contrarian PASS receipt not found or not a PASS: "
            f"{contrarian_receipt_file}\n"
        )
        write_change_log(variable, observer, "refused_no_contrarian_pass", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", f"contrarian receipt missing or not PASS: {contrarian_receipt_file}",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    # ── STEP 2: Rate-limit ─────────────────────────────────────────────────────
    today_count = count_today_applies(observer)
    if today_count >= RATE_CAP_PER_DAY:
        reason = f"rate_limit: {today_count}/{RATE_CAP_PER_DAY} applies today for {observer!r}"
        sys.stderr.write(f"[observer-apply] PARK: {reason}\n")
        park_item(item_path, reason, observer)
        write_change_log(variable, observer, "parked_rate_limit", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "parked", reason, proposal_id, contrarian_receipt_file,
        )
        return 2

    # ── STEP 3: Oscillation check ──────────────────────────────────────────────
    osc_reason = oscillation_check(variable, proposed_value)
    if osc_reason:
        if "rejected_noop" in osc_reason:
            sys.stderr.write(f"[observer-apply] REFUSE: {osc_reason}\n")
            write_change_log(variable, observer, "refused_noop", proposal_id)
            write_ceo_notify(
                variable, observer, current_value, proposed_value,
                "refused", osc_reason, proposal_id, contrarian_receipt_file,
            )
            return 1
        else:
            sys.stderr.write(f"[observer-apply] PARK: {osc_reason}\n")
            park_item(item_path, osc_reason, observer)
            write_change_log(variable, observer, "parked_oscillating", proposal_id)
            write_ceo_notify(
                variable, observer, current_value, proposed_value,
                "parked", osc_reason, proposal_id, contrarian_receipt_file,
            )
            return 2

    # ── STEP 4: Surface-manifest forbidden re-check ────────────────────────────
    if check_manifest_forbidden(variable):
        sys.stderr.write(
            f"[observer-apply] REFUSE: variable {variable!r} is in surface-manifest forbidden list.\n"
        )
        write_change_log(variable, observer, "refused_forbidden", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", "variable is forbidden in surface-manifest",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    # ── STEP 5: Allow-list re-check ────────────────────────────────────────────
    allowlist = load_allowlist()
    if allowlist is None:
        sys.stderr.write(
            f"[observer-apply] BLOCKED: allow-list YAML not found at {ALLOWLIST_PATH}. "
            "Dependency on ar-director not yet delivered. Refusing apply.\n"
        )
        write_change_log(variable, observer, "blocked_allowlist_absent", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "blocked", f"self-apply-allowlist.yaml absent ({ALLOWLIST_PATH})",
            proposal_id, contrarian_receipt_file,
        )
        return 4

    if not check_allowlist(allowlist, variable):
        sys.stderr.write(
            f"[observer-apply] REFUSE: variable {variable!r} not in self-apply allow-list.\n"
        )
        write_change_log(variable, observer, "refused_not_allowlisted", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", "variable not in self-apply-allowlist.yaml",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    # ── STEP 6: patch_sha256 verify (TOCTOU guard) ────────────────────────────
    patch = diff.get("patch", "")
    patch_sha256 = diff.get("patch_sha256", "")
    if isinstance(patch, dict):
        patch_bytes = json.dumps(patch, sort_keys=True).encode("utf-8")
    else:
        patch_bytes = str(patch).encode("utf-8")

    if patch_sha256 and not verify_patch_sha256(patch_bytes, patch_sha256):
        actual = hashlib.sha256(patch_bytes).hexdigest()
        sys.stderr.write(
            f"[observer-apply] REFUSE: patch_sha256 mismatch. "
            f"Expected {patch_sha256!r}, got {actual!r}.\n"
        )
        write_change_log(variable, observer, "refused_sha256_mismatch", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "refused", f"patch_sha256 mismatch (expected {patch_sha256[:16]}…)",
            proposal_id, contrarian_receipt_file,
        )
        return 1

    # ── STEP 7: Apply diff ─────────────────────────────────────────────────────
    try:
        apply_diff(item)
    except Exception as e:
        sys.stderr.write(f"[observer-apply] ERROR applying diff: {e}\n")
        write_change_log(variable, observer, "error_apply_failed", proposal_id)
        write_ceo_notify(
            variable, observer, current_value, proposed_value,
            "error", f"apply_diff raised: {e}",
            proposal_id, contrarian_receipt_file,
        )
        return 5

    # ── STEP 8: Write receipt + change-log + CEO-notify ───────────────────────
    receipt_path = write_receipt(
        item,
        contrarian_receipt_file,
        status="DONE",
        outcome_note=(
            f"Applied {variable}: `{current_value}` → `{proposed_value}`. "
            f"Contrarian PASS: {contrarian_receipt_file}."
        ),
    )
    write_change_log(variable, observer, "applied", proposal_id)
    write_ceo_notify(
        variable, observer, current_value, proposed_value,
        "applied", "contrarian PASS + all guards clear",
        proposal_id, contrarian_receipt_file,
    )

    # ── STEP 9: Append rate-limit ledger row ──────────────────────────────────
    append_rate_limit_row(item)

    # Archive the inbox item (move out of inbox)
    archive_path = item_path.parent.parent / "parked" / f"applied-{item_path.name}"
    item_path.rename(archive_path) if not archive_path.exists() else item_path.unlink(missing_ok=True)

    print(f"[observer-apply] APPLIED {variable} ({observer}) → receipt {receipt_path.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"[observer-apply] FATAL: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(5)
