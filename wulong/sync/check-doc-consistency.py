#!/usr/bin/env python3
"""
check-doc-consistency.py

Run-on-demand checker: extracts key structural facts from
Meta/company-facts.md (canonical) and verifies each source-of-truth
doc agrees with the canonical values.

Exit codes:
  0  — all facts AGREE and disk self-check passes
  1  — one or more DISAGREE, or disk self-check FAILS

Usage:
  python3 Meta/sync/check-doc-consistency.py
  python3 Meta/sync/check-doc-consistency.py --facts-file /path/to/alt-facts.md
"""

import re
import sys
import glob
import argparse
from pathlib import Path

from wulong._root import resolve_root

# Install-relative FLOOR only, reached when no root was handed down. This script
# runs as a child of an entry point, which passes the resolved root in the
# environment, so this tier fires only on direct manual invocation.
VAULT = Path(resolve_root(fallback=str(Path(__file__).resolve().parent.parent.parent),
                          tool="check-doc-consistency"))
META = VAULT / "Meta"
AGENTS_GLOB = str(VAULT / ".claude" / "agents" / "*.md")

CANONICAL_FILE = META / "company-facts.md"

SOURCE_DOCS = [
    META / "brain.md",
    META / "master-map.md",
    META / "company-registry.md",
    META / "agents-roster.md",
    META / "company-structure.md",
]

# ── colour helpers (stripped when not a tty) ──────────────────────────────────

def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

RED    = lambda t: _c("31;1", t)
YELLOW = lambda t: _c("33;1", t)
GREEN  = lambda t: _c("32;1", t)
BOLD   = lambda t: _c("1",    t)
DIM    = lambda t: _c("2",    t)


# ── canonical parser ──────────────────────────────────────────────────────────

def parse_facts_block(facts_path: Path) -> dict[str, str]:
    """
    Extract the ```facts ... ``` fenced block from company-facts.md
    and return a dict of {key: value} string pairs.
    Raises SystemExit if the block is missing or malformed.
    """
    text = facts_path.read_text(encoding="utf-8")
    m = re.search(r"```facts\n(.*?)```", text, re.DOTALL)
    if not m:
        print(RED("HARD FAIL") + f": no ```facts``` block found in {facts_path}")
        sys.exit(1)

    facts: dict[str, str] = {}
    for raw_line in m.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        facts[key.strip()] = val.strip()

    if not facts:
        print(RED("HARD FAIL") + f": ```facts``` block is empty in {facts_path}")
        sys.exit(1)

    return facts


# ── disk self-check ───────────────────────────────────────────────────────────

def disk_agent_count() -> int:
    return len(glob.glob(AGENTS_GLOB))


def self_check(canonical_count: int) -> bool:
    """
    Verify canonical agent_count matches the actual files on disk.
    Returns True if they agree, False (and prints a loud error) if not.
    """
    disk_count = disk_agent_count()
    if disk_count == canonical_count:
        print(GREEN("PASS") + f" [self-check] agent_count on disk: {disk_count} == canonical {canonical_count}")
        return True
    else:
        print(
            RED("HARD FAIL") + " [self-check] agent_count MISMATCH: "
            f"disk has {disk_count} files but canonical says {canonical_count}. "
            "The canonical file itself has drifted from reality — update company-facts.md first."
        )
        return False


# ── per-doc checkers ──────────────────────────────────────────────────────────

# Each checker returns a list of (fact_key, status, file_path, line_no, snippet) tuples.
# status is one of: "AGREE", "DISAGREE", "UNMATCHED"

Finding = tuple[str, str, str, int | None, str]


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _find_line(lines: list[str], pattern: str) -> tuple[int | None, str]:
    """Return (1-based line number, line text) for the first match, or (None, '')."""
    rx = re.compile(pattern, re.IGNORECASE)
    for i, line in enumerate(lines, 1):
        if rx.search(line):
            return i, line.strip()
    return None, ""


# ── individual fact checkers ──────────────────────────────────────────────────

def check_agent_count(facts: dict, lines: list[str], doc_path: Path) -> list[Finding]:
    """
    Look for a TOTAL/DECLARED agent count — lines that are clearly stating the company's
    agent headcount, not incidental mentions of the word "agents".

    We require at least one of these anchors on the same line:
      - "total", "active", "unique" adjacent to "agents"
      - "agents" in a table-cell or key:value context (e.g. "| 49 |", "agents: 49")
      - an arrow pattern like "47→49 agents"
      - "N agents, 9 departments" (the company-summary formula)
      - "agent_count:"
    """
    canonical = int(facts.get("agent_count", 0))
    results: list[Finding] = []

    patterns = [
        # "Total agents: 49" / "Total unique agents | | | 56"
        r"total\s+(?:unique\s+)?agents?[^\d]*(\d+)",
        # "56 unique agents"
        r"\b(\d+)\s+unique\s+agents?\b",
        # table cell "| Total unique agents | ... | **49** |" — number in bold or plain after Total unique agents
        r"\*\*(\d+)\*\*\s+\(see Summary",
        # "47→49 agents" or "47->49 agents"
        r"\d+[→>-]+(\d+)\s+agents?\b",
        # "N agents, 9 departments" — the company-summary formula
        r"\b(\d+)\s+agents?,\s*9\s+departments?",
        # "agents: 49" / "agent_count: 56"  — colon or whitespace must immediately precede the number
        r"\bagent[_\s]count\s*[:\s]\s*(\d+)",
        # "Total agents | 49" — require "Total" before agents to avoid matching per-dept count rows
        r"\bTotal\s+(?:unique\s+)?agents?\s*\|\s*(\d+)\b",
        # "Total active agents: 47"
        r"\b(?:total\s+)?active\s+agents?[^\d]*(\d+)",
    ]
    combined = re.compile("|".join(patterns), re.IGNORECASE)

    # Dated "X→Y" / "X->Y" headcount-transition records (e.g. "agent count 56→57",
    # "Total 56→57. UPDATE ...", "57→58") log a HIRE event, not the current headcount.
    # The PRE-arrow number on such a line is historical and MUST NOT be read as the
    # live count — otherwise every hire bullet false-DISAGREEs forever. We only suppress
    # the match when the grabbed number is the LEFT operand of an arrow transition; a
    # genuine non-transition drift line (e.g. "agent_count: 57") has no arrow and still fires.
    TRANSITION = re.compile(r"(\d+)\s*(?:->|→)\s*(\d+)")

    found_any = False
    for i, line in enumerate(lines, 1):
        m = combined.search(line)
        if not m:
            continue
        num_str = next((g for g in m.groups() if g and g.isdigit()), None)
        if num_str is None:
            continue
        num = int(num_str)
        # Skip if this exact number is the PRE-arrow operand of an "X→Y" transition
        # record on this line (historical hire log, not a live-count declaration).
        if any(int(pre) == num for pre, _post in TRANSITION.findall(line)):
            continue
        found_any = True
        if num == canonical:
            results.append(("agent_count", "AGREE", str(doc_path), i, line.strip()))
        else:
            results.append(("agent_count", "DISAGREE", str(doc_path), i,
                            f"says {num}, canonical is {canonical} — {line.strip()}"))

    if not found_any:
        results.append(("agent_count", "UNMATCHED", str(doc_path), None,
                        "no declared agent-count figure found in this doc"))
    return results


def check_active_projects_count(facts: dict, lines: list[str], doc_path: Path) -> list[Finding]:
    """
    Look for a DECLARED project count — not incidental comparisons or preference statements.

    We require anchors that indicate the line is stating "this is how many projects we have":
      - "Trading projects | N" (table cell)
      - "N trading projects" as a direct declaration (not followed by comparison words)
      - "active_projects_count: N"
      - "Active roster (N)" — prose footer form (e.g. brain.md quick-facts)

    We EXCLUDE lines containing comparison operators (>, <, ≥, vs, >) that indicate
    priority-ordering rather than a headcount declaration.
    """
    canonical = int(facts.get("active_projects_count", 0))
    results: list[Finding] = []

    EXCLUSION = re.compile(r"\bvs\b|priority|prefer|more than|greater than|>", re.IGNORECASE)

    patterns = [
        # table cell: "Trading projects | 5 (project_a, ...)"
        r"\btrading\s+projects?\s*\|\s*(\d+)",
        # "active_projects_count: 6"
        r"\bactive[_\s]projects[_\s]count[^\d]*(\d+)",
        # "5 trading projects" as a standalone count claim (not in a comparison)
        r"\b(\d+)\s+trading\s+projects?\b(?!\s*[→>])",
        # "Active roster (6):" — prose quick-facts form used in brain.md footer
        r"\bActive\s+roster\s*\((\d+)\)",
    ]
    combined = re.compile("|".join(patterns), re.IGNORECASE)

    found_any = False
    for i, line in enumerate(lines, 1):
        if EXCLUSION.search(line):
            continue
        m = combined.search(line)
        if not m:
            continue
        num_str = next((g for g in m.groups() if g and g.isdigit()), None)
        if num_str is None:
            continue
        num = int(num_str)
        found_any = True
        if num == canonical:
            results.append(("active_projects_count", "AGREE", str(doc_path), i, line.strip()))
        else:
            results.append(("active_projects_count", "DISAGREE", str(doc_path), i,
                            f"says {num} projects, canonical is {canonical} — {line.strip()}"))

    if not found_any:
        results.append(("active_projects_count", "UNMATCHED", str(doc_path), None,
                        "no declared project-count found in this doc"))
    return results


def check_dormant_listed_as_active(facts: dict, lines: list[str], doc_path: Path) -> list[Finding]:
    """
    The dormant projects listed in company-facts.md dormant_projects field
    must NOT appear in a row/section that describes the CURRENT live portfolio.

    A DISAGREE fires only when ALL of these hold on the same line:
      1. The dormant project name appears.
      2. A "live status" keyword appears (WR|PnL|STAGE|MONITORING|RUNNING|LIVE|PAPER|Phase N).
      3. No "historical/past" suppressor appears that indicates we are recording what was,
         not what is (e.g. "was", "renamed", "archived", "ARCHIVED", "dissolved",
         "→", "previously", "PARK", "no-edge proven", "confirmed terminal",
         "FORMALLY CLOSED", "SUPERSEDES", "RETIRED").

    This prevents false positives from history-log sentences like:
      "old_project WR collapsed — PARK-FOR-FIX" (PARK is a suppressor)
      "old_project renamed → new_project" (→ is a suppressor)
    """
    dormant_raw = facts.get("dormant_projects", "")
    dormant = [p.strip() for p in dormant_raw.split(",") if p.strip()]

    LIVE_SIGNALS = re.compile(
        r"\b(WR|PnL|STAGE\s*\d|MONITORING|RUNNING|LIVE|PAPER|Phase\s*\d|go.live)\b",
        re.IGNORECASE,
    )

    # Lines mentioning these words/patterns are recording history, not current state.
    # NOTE: bare "dormant" / "off-roadmap" are intentionally NOT suppressors here.
    # A roster-classification line asserting a dormant project has live-signal keywords
    # (e.g. "old_project: dormant, WR=45%") should still be caught as a DISAGREE.
    # Only genuinely past-tense / archival phrasing suppresses (was, renamed, archived, →, etc.).
    HISTORICAL = re.compile(
        r"\b(was|renamed|archived|ARCHIVED|dissolved|previously|PARK|no.edge|KILL|"
        r"FORMALLY\s+CLOSED|SUPERSEDES|RETIRED|dead|"
        r"INCONCLUSIVE|TERMINAL|DEFERRED|DEFER|README|stubs?|contradictions?|"
        r"synced|sign.off|cleanup)\b"
        r"|[→]",  # arrow = rename/transition record
        re.IGNORECASE,
    )

    results: list[Finding] = []
    for proj in dormant:
        proj_rx = re.compile(re.escape(proj), re.IGNORECASE)
        found_any = False
        for i, line in enumerate(lines, 1):
            if not proj_rx.search(line):
                continue
            if not LIVE_SIGNALS.search(line):
                continue
            if HISTORICAL.search(line):
                continue  # historical record — not a live-status claim
            found_any = True
            results.append((
                f"dormant_not_active:{proj}",
                "DISAGREE",
                str(doc_path),
                i,
                f"dormant project '{proj}' appears described as active — {line.strip()}",
            ))
        if not found_any:
            results.append((
                f"dormant_not_active:{proj}",
                "AGREE",
                str(doc_path),
                None,
                f"'{proj}' not listed as active in this doc",
            ))
    return results


# map: doc path → list of checker functions to apply
CHECKERS = [
    check_agent_count,
    check_active_projects_count,
    check_dormant_listed_as_active,
]


# ── runner ────────────────────────────────────────────────────────────────────

def run_checks(facts: dict, docs: list[Path]) -> list[Finding]:
    all_findings: list[Finding] = []
    for doc in docs:
        lines = _lines(doc)
        if not lines:
            print(YELLOW("WARN") + f": {doc.name} not found or empty — skipping")
            continue
        for checker in CHECKERS:
            all_findings.extend(checker(facts, lines, doc))
    return all_findings


def print_report(findings: list[Finding]) -> tuple[int, int, int]:
    """Print a human-readable report. Returns (agree, disagree, unmatched) counts."""
    by_doc: dict[str, list[Finding]] = {}
    for f in findings:
        doc = Path(f[2]).name
        by_doc.setdefault(doc, []).append(f)

    agree_total = disagree_total = unmatched_total = 0

    for doc_name, items in by_doc.items():
        print(f"\n{BOLD(doc_name)}")
        print("─" * (len(doc_name) + 2))
        for fact_key, status, _doc, lineno, snippet in items:
            loc = f"line {lineno}" if lineno else "—"
            if status == "AGREE":
                agree_total += 1
                print(f"  {GREEN('AGREE')}  [{fact_key}]  {DIM(loc)}  {DIM(snippet[:100])}")
            elif status == "DISAGREE":
                disagree_total += 1
                print(f"  {RED('DISAGREE')}  [{fact_key}]  {loc}")
                print(f"           {snippet[:200]}")
            else:  # UNMATCHED
                unmatched_total += 1
                print(f"  {YELLOW('WARN')} (UNMATCHED)  [{fact_key}]  {snippet[:120]}")

    return agree_total, disagree_total, unmatched_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Check doc consistency against company-facts.md")
    parser.add_argument(
        "--facts-file",
        default=str(CANONICAL_FILE),
        help=f"Path to the canonical facts file (default: {CANONICAL_FILE})",
    )
    args = parser.parse_args()

    facts_path = Path(args.facts_file)
    if not facts_path.exists():
        print(RED("HARD FAIL") + f": canonical facts file not found: {facts_path}")
        sys.exit(1)

    print(BOLD("=== check-doc-consistency ==="))
    print(f"Canonical: {facts_path}")
    print(f"Vault:     {VAULT}\n")

    # 1. Parse canonical
    facts = parse_facts_block(facts_path)
    print(f"Parsed {len(facts)} canonical facts: {', '.join(facts.keys())}\n")

    # 2. Disk self-check
    canonical_agent_count = int(facts.get("agent_count", -1))
    self_check_ok = self_check(canonical_agent_count)

    # 3. Run doc checks
    print(f"\n{BOLD('Checking source-of-truth docs...')}")
    findings = run_checks(facts, SOURCE_DOCS)

    # 4. Report
    print(f"\n{BOLD('─── Results ───')}")
    agree, disagree, unmatched = print_report(findings)

    print(f"\n{BOLD('─── Summary ───')}")
    print(f"  {GREEN(f'AGREE:      {agree}')}")
    print(f"  {RED(f'DISAGREE:   {disagree}')}"   if disagree else f"  DISAGREE:   {disagree}")
    print(f"  {YELLOW(f'UNMATCHED:  {unmatched} (warnings — not failures)')}")
    print(f"  Disk self-check: {'PASS' if self_check_ok else RED('FAIL')}")

    # 5. Exit
    if disagree > 0 or not self_check_ok:
        print(f"\n{RED('RESULT: FAIL')} — {disagree} disagreement(s); self-check {'PASS' if self_check_ok else 'FAIL'}")
        sys.exit(1)
    else:
        print(f"\n{GREEN('RESULT: PASS')} — zero disagreements; disk self-check PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
