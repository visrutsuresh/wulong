#!/usr/bin/env python3
"""
trace-change-chain.py — Read-only DAG walker for receipt causal chains.

Walks `gated_by` edges and same-change_id membership to print the receipt
chain / DAG for a given change. Ordering is by edges, NEVER by timestamp.

Usage:
  python3 trace-change-chain.py --change-id <slug>
  python3 trace-change-chain.py --receipt <path-or-filename>

Output:
  One line per node in topological order, with predecessor edges, agent,
  review_mode/review_verdict (if present), and status.

Exit code:
  0 always (read-only; cycle/dangling edge → warning, not crash).
"""

import argparse
import os
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

VAULT    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECEIPTS = os.path.join(VAULT, "Meta", "receipts")

# ---------------------------------------------------------------------------
# Frontmatter parsing (minimal, inline — no import from validate-receipts)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML frontmatter into a flat dict. Returns {} if absent/broken."""
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
    """Parse a YAML inline list string into a list of filenames.

    Accepts '[a, b, c]' or '[a,b,c]'. Returns [] on malformed input.
    """
    raw = raw.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return []
    inner = raw[1:-1]
    items = [s.strip().strip("'\"") for s in inner.split(",")]
    return [x for x in items if x]


# ---------------------------------------------------------------------------
# Receipt index builder
# ---------------------------------------------------------------------------

def _load_receipt(path: str) -> Optional[dict]:
    """Load a receipt file and return a node dict. Returns None on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None
    fields = _parse_frontmatter(content)
    fname  = os.path.basename(path)
    gated_by_raw = fields.get("gated_by", "")
    gated_by     = _parse_gated_by(gated_by_raw) if gated_by_raw else []
    return {
        "fname":          fname,
        "path":           path,
        "agent":          fields.get("agent", ""),
        "status":         fields.get("status", ""),
        "change_id":      fields.get("change_id", ""),
        "session_id":     fields.get("session_id", ""),
        "review_mode":    fields.get("review_mode", ""),
        "review_verdict": fields.get("review_verdict", ""),
        "gated_by":       gated_by,
    }


def _build_index(receipts_dir: str) -> dict[str, dict]:
    """Load all receipts into a dict keyed by filename."""
    index: dict[str, dict] = {}
    if not os.path.isdir(receipts_dir):
        return index
    for entry in os.listdir(receipts_dir):
        if not entry.endswith(".md"):
            continue
        path = os.path.join(receipts_dir, entry)
        if not os.path.isfile(path):
            continue
        node = _load_receipt(path)
        if node is not None:
            index[entry] = node
    return index


# ---------------------------------------------------------------------------
# Chain / DAG collection
# ---------------------------------------------------------------------------

def _collect_chain(
    seed_fnames: list[str],
    index: dict[str, dict],
    change_id: Optional[str],
) -> tuple[set[str], list[str]]:
    """Collect all receipt filenames reachable from seed_fnames via gated_by edges.

    Also includes all receipts sharing the same change_id (same-change membership).
    Returns (chain_set, warnings_list).
    """
    chain: set[str] = set()
    warnings: list[str] = []

    # BFS via gated_by forward+backward edges
    queue = list(seed_fnames)
    while queue:
        fname = queue.pop(0)
        if fname in chain:
            continue
        chain.add(fname)
        if fname not in index:
            if fname not in ("~", ""):
                warnings.append(f"DANGLING EDGE: '{fname}' not found in receipts/")
            continue
        # Walk backward (predecessors declared in gated_by)
        for pred in index[fname]["gated_by"]:
            if pred not in chain:
                queue.append(pred)

    # Walk forward: any receipt that lists a member of chain in its gated_by
    for fname2, node in index.items():
        if fname2 in chain:
            continue
        for pred in node["gated_by"]:
            if pred in chain:
                queue.append(fname2)
                break
    while queue:
        fname = queue.pop(0)
        if fname in chain:
            continue
        chain.add(fname)
        if fname not in index:
            if fname not in ("~", ""):
                warnings.append(f"DANGLING EDGE: '{fname}' not found in receipts/")
            continue
        for fname2, node in index.items():
            if fname2 in chain:
                continue
            for pred in node["gated_by"]:
                if pred in chain:
                    queue.append(fname2)
                    break

    # Same change_id membership
    if change_id:
        for fname2, node in index.items():
            if fname2 not in chain and node["change_id"] == change_id:
                chain.add(fname2)
                # Also pull their predecessors
                for pred in node["gated_by"]:
                    if pred not in chain:
                        if pred not in index and pred not in ("~", ""):
                            warnings.append(f"DANGLING EDGE: '{pred}' not found in receipts/")
                        queue.append(pred)
        while queue:
            fname = queue.pop(0)
            if fname in chain:
                continue
            chain.add(fname)

    return chain, warnings


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------

def _topological_sort(
    chain: set[str],
    index: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """Sort chain members by gated_by edge direction (predecessors first).

    Returns (sorted_list, cycle_warnings).
    Nodes with dangling predecessors (not in chain) are treated as roots.
    """
    # Build in-degree within chain
    in_degree: dict[str, int] = {f: 0 for f in chain}
    edges: dict[str, list[str]] = {f: [] for f in chain}  # pred → successors

    for fname in chain:
        if fname not in index:
            continue
        for pred in index[fname]["gated_by"]:
            if pred in chain:
                in_degree[fname] += 1
                edges[pred].append(fname)

    queue = [f for f in chain if in_degree[f] == 0]
    queue.sort()  # stable ordering within same tier
    sorted_list: list[str] = []

    while queue:
        node = queue.pop(0)
        sorted_list.append(node)
        for succ in sorted(edges.get(node, [])):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    cycles: list[str] = []
    if len(sorted_list) < len(chain):
        remaining = chain - set(sorted_list)
        cycles.append(f"CYCLE DETECTED among: {sorted(remaining)}")
        sorted_list.extend(sorted(remaining))

    return sorted_list, cycles


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_node(fname: str, index: dict[str, dict], chain: set[str]) -> str:
    """Render a single node as a readable line."""
    if fname not in index:
        return f"  [MISSING] {fname}"

    node = index[fname]
    parts = [f"  {fname}"]

    # Agent
    if node["agent"]:
        parts.append(f"  agent={node['agent']}")

    # Status
    if node["status"]:
        parts.append(f"  status={node['status']}")

    # review_mode / review_verdict
    if node["review_mode"] or node["review_verdict"]:
        rv = node["review_mode"] or "?"
        vd = node["review_verdict"] or "UNKNOWN"
        parts.append(f"  review={rv}/{vd}")

    # change_id
    if node["change_id"]:
        parts.append(f"  change_id={node['change_id']}")

    # Predecessor edges (within chain only)
    preds_in_chain = [p for p in node["gated_by"] if p in chain]
    preds_dangling  = [p for p in node["gated_by"] if p not in chain and p not in ("~", "")]
    if preds_in_chain:
        parts.append(f"  ← {', '.join(preds_in_chain)}")
    if preds_dangling:
        parts.append(f"  ← [DANGLING: {', '.join(preds_dangling)}]")

    return "".join(parts)


def _print_chain(
    sorted_list: list[str],
    index: dict[str, dict],
    chain: set[str],
    change_id: Optional[str],
    warnings: list[str],
    cycles: list[str],
) -> None:
    """Print the full chain report to stdout."""
    label = f"change_id: {change_id}" if change_id else f"{len(chain)} receipt(s)"
    print(f"\n=== Receipt chain: {label} ({len(sorted_list)} nodes) ===\n")

    for fname in sorted_list:
        print(_render_node(fname, index, chain))

    print()

    if cycles:
        for c in cycles:
            print(f"WARNING: {c}", file=sys.stderr)
    if warnings:
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)

    if not cycles and not warnings:
        print("(no cycles or dangling edges)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trace receipt causal chain via gated_by edges + change_id membership."
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--change-id",
        metavar="SLUG",
        help="Collect all receipts sharing this change_id slug.",
    )
    group.add_argument(
        "--receipt",
        metavar="PATH",
        help="Start traversal from this receipt file (path or filename).",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    index = _build_index(RECEIPTS)

    change_id: Optional[str] = None
    seed_fnames: list[str] = []

    if args.change_id:
        change_id = args.change_id
        seed_fnames = [f for f, n in index.items() if n["change_id"] == change_id]
        if not seed_fnames:
            print(
                f"No receipts found with change_id='{change_id}'. "
                f"Check that receipts carry this field in their frontmatter.",
                file=sys.stderr,
            )
            return 0

    else:
        # --receipt: resolve to filename
        receipt_arg = args.receipt
        fname = os.path.basename(receipt_arg)
        if fname not in index:
            # Try as absolute/relative path
            if os.path.isfile(receipt_arg):
                node = _load_receipt(receipt_arg)
                if node:
                    index[fname] = node
            if fname not in index:
                print(f"Receipt not found: {receipt_arg}", file=sys.stderr)
                return 0
        seed_fnames = [fname]
        change_id = index[fname].get("change_id") or None

    chain, warnings = _collect_chain(seed_fnames, index, change_id)
    sorted_list, cycles = _topological_sort(chain, index)
    _print_chain(sorted_list, index, chain, change_id, warnings, cycles)

    return 0


if __name__ == "__main__":
    sys.exit(main())
