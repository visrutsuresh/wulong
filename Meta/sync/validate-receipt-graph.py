#!/usr/bin/env python3
"""
validate-receipt-graph.py — Gate rule checker for receipt causal graphs.

Enforces NN#3, NN#4, NN#10 per change_id using gated_by edge traversal.
Keys off review_verdict (PASS|FAIL), NEVER off status (DONE|FAIL).

Gate rules (per change_id, new-era receipts only):
  NN#3: coder receipt with change_type in {feature,fix} REQUIRES a contrarian
        receipt reachable via gated_by with review_mode=plan AND review_verdict=PASS.
        If contrarian present but verdict absent/UNKNOWN → NN3_COVERAGE_GAP (fail-closed).
  NN#4: if a deployer receipt exists in the change_id, REQUIRES a tester receipt
        (status=DONE) reachable forward from the deployer via edges.
  NN#10: change_id REQUIRES both contrarian plan+PASS AND output+PASS.
         Sub-codes: PLAN_GATE_MISSING / OUTPUT_GATE_MISSING / VERDICT_UNKNOWN.
  Fix-loop: loop-N nodes additive. COMPLETE when the LATEST coder reachable in
            the DAG has a contrarian output-review with review_verdict=PASS reachable
            from it (directly or via tester chain).

Usage:
  python3 validate-receipt-graph.py [--since YYYY-MM-DD] [--change-id X] [--warn-only] [--strict]
  python3 validate-receipt-graph.py --root /path/to/vault

Root resolution order:
  1. WULONG_ROOT environment variable
  2. --root CLI argument
  3. Repo root inferred from this script's location (../../.. from Meta/sync/)
"""

import argparse
import os
import re
import sys
from collections import defaultdict, deque
from datetime import date
from typing import Optional


def _resolve_root(cli_root: Optional[str] = None) -> str:
    env = os.environ.get("WULONG_ROOT", "").strip()
    if env:
        return env
    if cli_root:
        return cli_root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Only receipts from this date onward carry graph fields (new-era)
GRAPH_ERA_CUTOFF = date(2026, 5, 30)

CODER_CHANGE_TYPES = {"feature", "fix"}

# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> dict[str, str]:
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
    fields: dict[str, str] = {}
    for line in lines[1:close]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return fields


def _parse_gated_by(raw: str) -> list[str]:
    raw = raw.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return []
    inner = raw[1:-1]
    return [s.strip().strip("'\"") for s in inner.split(",") if s.strip().strip("'\"")]


def _parse_date(val: str) -> Optional[date]:
    try:
        parts = val.strip().split()
        if not parts:
            return None
        return date.fromisoformat(parts[0])
    except (ValueError, AttributeError):
        return None

# ---------------------------------------------------------------------------
# Receipt loader
# ---------------------------------------------------------------------------

def _load_receipt(path: str) -> Optional[dict]:
    fname = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None

    fields = _parse_frontmatter(content)
    fm_date = _parse_date(fields.get("date", ""))

    gated_by_raw = fields.get("gated_by", "")
    gated_by = _parse_gated_by(gated_by_raw) if gated_by_raw else []

    return {
        "fname":          fname,
        "path":           path,
        "agent":          fields.get("agent", "").strip(),
        "status":         fields.get("status", "").strip(),
        "change_id":      fields.get("change_id", "").strip(),
        "change_type":    fields.get("change_type", "").strip(),
        "session_id":     fields.get("session_id", "").strip(),
        "review_mode":    fields.get("review_mode", "").strip(),
        "review_verdict": fields.get("review_verdict", "").strip(),
        "gated_by":       gated_by,
        "date":           fm_date,
    }


def _load_all(receipts_dir: str, since: Optional[date]) -> dict[str, dict]:
    """Load all receipts. --since filters which change_ids are validated, not
    which individual receipts are loaded (chain completeness requires loading all)."""
    if not os.path.isdir(receipts_dir):
        return {}

    all_entries = [
        e for e in os.listdir(receipts_dir)
        if e.endswith(".md") and os.path.isfile(os.path.join(receipts_dir, e))
    ]

    if since is None:
        index: dict[str, dict] = {}
        for entry in all_entries:
            node = _load_receipt(os.path.join(receipts_dir, entry))
            if node is not None:
                index[entry] = node
        return index

    # Pass 1: load receipts with filename-date >= since
    partial: dict[str, dict] = {}
    for entry in all_entries:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", entry)
        if m:
            try:
                fdate = date.fromisoformat(m.group(1))
                if fdate < since:
                    continue
            except ValueError:
                pass
        node = _load_receipt(os.path.join(receipts_dir, entry))
        if node is not None:
            partial[entry] = node

    active_change_ids: set[str] = {
        n["change_id"] for n in partial.values() if n["change_id"]
    }

    if not active_change_ids:
        return partial

    # Pass 2: load ALL receipts that share an active change_id (chain completeness)
    index = dict(partial)
    for entry in all_entries:
        if entry in index:
            continue
        node = _load_receipt(os.path.join(receipts_dir, entry))
        if node is not None and node["change_id"] in active_change_ids:
            index[entry] = node

    return index

# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _reachable_ancestors(fname: str, index: dict[str, dict]) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([fname])
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        if cur not in index:
            continue
        for pred in index[cur]["gated_by"]:
            if pred not in visited:
                queue.append(pred)
    return visited


def _reachable_descendants(fname: str, index: dict[str, dict],
                           change_id: str) -> set[str]:
    rev: dict[str, list[str]] = defaultdict(list)
    for f, n in index.items():
        if n.get("change_id") != change_id:
            continue
        for pred in n["gated_by"]:
            rev[pred].append(f)

    visited: set[str] = set()
    queue: deque[str] = deque([fname])
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        for succ in rev.get(cur, []):
            if succ not in visited:
                queue.append(succ)
    return visited


def _build_change_id_groups(index: dict[str, dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for fname, node in index.items():
        cid = node.get("change_id", "")
        if cid:
            groups[cid].append(fname)
    return dict(groups)

# ---------------------------------------------------------------------------
# Gate checks per change_id
# ---------------------------------------------------------------------------

Violation = dict


def _check_nn3(
    change_id: str,
    members: list[str],
    index: dict[str, dict],
) -> list[Violation]:
    """NN#3: every coder receipt with change_type in {feature,fix} must have a
    reachable contrarian plan-PASS ancestor."""
    viols: list[Violation] = []

    triggering_coders = [
        f for f in members
        if f in index
        and index[f]["agent"] == "coder"
        and index[f]["change_type"] in CODER_CHANGE_TYPES
    ]

    for coder_fname in triggering_coders:
        ancestors = _reachable_ancestors(coder_fname, index)

        contrarian_plan_receipts = [
            a for a in ancestors
            if a in index
            and index[a]["agent"] == "contrarian"
            and index[a]["review_mode"] == "plan"
        ]

        if not contrarian_plan_receipts:
            viols.append({
                "code": "NN3_VIOLATION",
                "detail": (
                    f"coder receipt '{coder_fname}' (change_type={index[coder_fname]['change_type']}) "
                    f"has no reachable contrarian plan-review ancestor via gated_by"
                ),
                "change_id": change_id,
                "receipt": coder_fname,
            })
            continue

        passing = [
            a for a in contrarian_plan_receipts
            if index[a]["review_verdict"] == "PASS"
        ]
        if passing:
            continue

        verdicts = [index[a]["review_verdict"] for a in contrarian_plan_receipts]
        if any(v == "FAIL" for v in verdicts):
            viols.append({
                "code": "NN3_VIOLATION",
                "detail": (
                    f"coder receipt '{coder_fname}': reachable contrarian plan-review has "
                    f"review_verdict=FAIL — gate not satisfied"
                ),
                "change_id": change_id,
                "receipt": coder_fname,
            })
        else:
            viols.append({
                "code": "NN3_COVERAGE_GAP",
                "detail": (
                    f"coder receipt '{coder_fname}': contrarian plan-review reachable but "
                    f"review_verdict absent/UNKNOWN on {contrarian_plan_receipts} — "
                    f"fail-closed: NOT a pass"
                ),
                "change_id": change_id,
                "receipt": coder_fname,
            })

    return viols


def _check_nn4(
    change_id: str,
    members: list[str],
    index: dict[str, dict],
) -> list[Violation]:
    """NN#4: every deployer receipt must have a tester receipt (status=DONE)
    reachable forward from it via edges."""
    viols: list[Violation] = []

    deployer_receipts = [
        f for f in members
        if f in index and index[f]["agent"] == "deployer"
    ]

    for dep_fname in deployer_receipts:
        descendants = _reachable_descendants(dep_fname, index, change_id)
        tester_found = any(
            d for d in descendants
            if d in index and index[d]["agent"] == "tester"
            and index[d]["status"] == "DONE"
        )

        if not tester_found:
            viols.append({
                "code": "NN4_VIOLATION",
                "detail": (
                    f"deployer receipt '{dep_fname}' has no tester receipt (status=DONE) "
                    f"reachable forward via gated_by edges"
                ),
                "change_id": change_id,
                "receipt": dep_fname,
            })

    return viols


def _check_nn10(
    change_id: str,
    members: list[str],
    index: dict[str, dict],
) -> list[Violation]:
    """NN#10: change_id requires both contrarian plan+PASS and output+PASS."""
    viols: list[Violation] = []

    contrarian_members = [
        f for f in members
        if f in index and index[f]["agent"] == "contrarian"
    ]

    plan_passes = [
        f for f in contrarian_members
        if index[f]["review_mode"] == "plan" and index[f]["review_verdict"] == "PASS"
    ]
    output_passes = [
        f for f in contrarian_members
        if index[f]["review_mode"] == "output" and index[f]["review_verdict"] == "PASS"
    ]
    plan_reviews = [f for f in contrarian_members if index[f]["review_mode"] == "plan"]
    output_reviews = [f for f in contrarian_members if index[f]["review_mode"] == "output"]

    if not plan_passes:
        if plan_reviews:
            sub = "VERDICT_UNKNOWN" if not any(
                index[f]["review_verdict"] for f in plan_reviews
            ) else "PLAN_GATE_MISSING"
            viols.append({
                "code": "NN10_INCOMPLETE",
                "sub": sub,
                "detail": (
                    f"change_id='{change_id}' has contrarian plan-review(s) but none with "
                    f"review_verdict=PASS — sub={sub}"
                ),
                "change_id": change_id,
                "receipt": change_id,
            })
        else:
            viols.append({
                "code": "NN10_INCOMPLETE",
                "sub": "PLAN_GATE_MISSING",
                "detail": f"change_id='{change_id}' has no contrarian plan-review at all",
                "change_id": change_id,
                "receipt": change_id,
            })

    if not output_passes:
        if output_reviews:
            sub = "VERDICT_UNKNOWN" if not any(
                index[f]["review_verdict"] for f in output_reviews
            ) else "OUTPUT_GATE_MISSING"
            viols.append({
                "code": "NN10_INCOMPLETE",
                "sub": sub,
                "detail": (
                    f"change_id='{change_id}' has contrarian output-review(s) but none with "
                    f"review_verdict=PASS — sub={sub}"
                ),
                "change_id": change_id,
                "receipt": change_id,
            })
        else:
            viols.append({
                "code": "NN10_INCOMPLETE",
                "sub": "OUTPUT_GATE_MISSING",
                "detail": f"change_id='{change_id}' has no contrarian output-review at all",
                "change_id": change_id,
                "receipt": change_id,
            })

    return viols


def _is_complete(
    change_id: str,
    members: list[str],
    index: dict[str, dict],
) -> bool:
    """A change_id is COMPLETE when the latest coder node has a contrarian
    output+PASS reachable forward from it."""
    coder_members = [
        f for f in members
        if f in index and index[f]["agent"] == "coder"
    ]
    if not coder_members:
        output_passes = [
            f for f in members
            if f in index
            and index[f]["agent"] == "contrarian"
            and index[f]["review_mode"] == "output"
            and index[f]["review_verdict"] == "PASS"
        ]
        return bool(output_passes)

    coder_set = set(coder_members)
    latest_coders = []
    for c in coder_members:
        descendants = _reachable_descendants(c, index, change_id)
        if not any(d in coder_set and d != c for d in descendants):
            latest_coders.append(c)

    if not latest_coders:
        latest_coders = coder_members

    for latest_coder in latest_coders:
        descendants = _reachable_descendants(latest_coder, index, change_id)
        has_output_pass = any(
            d for d in descendants
            if d in index
            and index[d]["agent"] == "contrarian"
            and index[d]["review_mode"] == "output"
            and index[d]["review_verdict"] == "PASS"
        )
        if has_output_pass:
            return True

    return False

# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def run_validation(
    index: dict[str, dict],
    since: Optional[date],
) -> tuple[list[Violation], dict[str, str]]:
    all_violations: list[Violation] = []
    verdicts: dict[str, str] = {}

    groups = _build_change_id_groups(index)

    for change_id, members in sorted(groups.items()):
        cid_viols: list[Violation] = []

        cid_viols.extend(_check_nn3(change_id, members, index))
        cid_viols.extend(_check_nn4(change_id, members, index))
        cid_viols.extend(_check_nn10(change_id, members, index))

        all_violations.extend(cid_viols)

        if not cid_viols and _is_complete(change_id, members, index):
            verdicts[change_id] = "COMPLETE"
        elif cid_viols:
            verdicts[change_id] = "VIOLATION"
        else:
            verdicts[change_id] = "IN_PROGRESS"

    return all_violations, verdicts

# ---------------------------------------------------------------------------
# Coverage metric
# ---------------------------------------------------------------------------

def _coverage(
    index: dict[str, dict],
    verdicts: dict[str, str],
) -> tuple[int, int, float]:
    groups = _build_change_id_groups(index)
    triggering: set[str] = set()
    for cid, members in groups.items():
        for f in members:
            if f in index and index[f]["agent"] == "coder" \
                    and index[f]["change_type"] in CODER_CHANGE_TYPES:
                triggering.add(cid)
                break

    complete = sum(1 for cid in triggering if verdicts.get(cid) == "COMPLETE")
    total = len(triggering)
    frac = (complete / total) if total > 0 else 1.0
    return complete, total, frac

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _print_report(
    violations: list[Violation],
    verdicts: dict[str, str],
    complete: int,
    total: int,
    frac: float,
    since: Optional[date],
) -> None:
    since_str = f" (since {since})" if since else ""
    print(f"[validate-receipt-graph]{since_str}")
    print(f"  change_ids scanned: {len(verdicts)}")
    print(f"  violations: {len(violations)}")
    print(f"  coverage: {complete}/{total} triggering change_ids COMPLETE "
          f"({frac*100:.0f}%)")
    print()

    if verdicts:
        print("  Per-change_id verdicts:")
        for cid, v in sorted(verdicts.items()):
            symbol = "OK" if v == "COMPLETE" else ("!!" if v == "VIOLATION" else "~")
            print(f"    [{symbol}] {cid}: {v}")
        print()

    if violations:
        print("  Violations:")
        for viol in violations:
            sub = viol.get("sub", "")
            sub_str = f"[{sub}] " if sub else ""
            print(f"    [{viol['code']}] {sub_str}change_id={viol['change_id']}")
            print(f"      {viol['detail']}")
        print()
    else:
        print("  No violations found.")
        print()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate receipt causal graph: enforce NN#3/NN#4/NN#10."
    )
    p.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                   help="Only include receipts on or after this date (filter at load time).")
    p.add_argument("--change-id", metavar="CHANGE_ID", default=None,
                   help="Scope validation to a single change_id.")
    p.add_argument("--warn-only", action="store_true", default=True,
                   help="Print violations but always exit 0 (default).")
    p.add_argument("--strict", action="store_true", default=False,
                   help="Exit 1 if any violation found.")
    p.add_argument("--root", type=str, default=None,
                   help="Vault root path (overrides WULONG_ROOT env var).")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    root = _resolve_root(args.root)
    receipts_dir = os.path.join(root, "Meta", "receipts")

    since: Optional[date] = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got '{args.since}'",
                  file=sys.stderr)
            return 2

    index = _load_all(receipts_dir, since=since)

    if args.change_id:
        index = {
            fname: node for fname, node in index.items()
            if node.get("change_id") == args.change_id
        }

    violations, verdicts = run_validation(index, since)
    complete, total, frac = _coverage(index, verdicts)

    _print_report(violations, verdicts, complete, total, frac, since)

    if args.strict and violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
