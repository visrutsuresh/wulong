#!/usr/bin/env python3
"""
synthesize-lessons.py — frequency-based auto-promotion of lessons → active rules.

Per Cerebrum v3.0 Phase 1.3 + locked preferences B8 (auto-promote at ≥3) and B9
(flag conflicts before override).

Reads every Meta/memory/<agent>/lessons.md. Clusters lesson bodies via TF-IDF +
cosine similarity. Clusters with size ≥3 spanning ≥2 distinct calendar days
become candidate rules. Non-conflicting candidates auto-promote to
Meta/memory/jarvis/active.md tagged `auto-promoted, unreviewed`. Conflicting
candidates go to Meta/memory/jarvis/conflict-queue.md AND post a ⏳ jarvis line
to Meta/agent-messages.md so the conflict surfaces in the next session.

NOT cron-triggered. Invoked by Jarvis end-of-session protocol when
.last-lesson-synth mtime > 7 days OR total un-promoted lesson count > 50.
Also invokable manually via `python3 synthesize-lessons.py [--dry-run]`.

Algorithm guardrails:
- MIN_CLUSTER_SIZE = 3
- MIN_DAY_SPAN = 2 (prevents 3 lessons in one stressed day from auto-promoting)
- COSINE_THRESHOLD = 0.72 (same-meaning grouping)
- CONFLICT_THRESHOLD = 0.55 + polarity-flip check
- Auto-promoted rules tagged `auto-promoted, unreviewed` until Visrut thumbs them up

Exit codes:
  0 = success (clusters processed, promotions/conflicts written)
  1 = error
"""
from __future__ import annotations
import argparse
import fcntl
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
import os
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
MEMORY_DIR = VAULT / "Meta" / "memory"
ACTIVE_MD = MEMORY_DIR / "jarvis" / "active.md"
CONFLICT_QUEUE = MEMORY_DIR / "jarvis" / "conflict-queue.md"
AGENT_MESSAGES = VAULT / "Meta" / "agent-messages.md"
LAST_SYNTH_MARKER = VAULT / "Meta" / "sync" / ".last-lesson-synth"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"

# Tunables
MIN_CLUSTER_SIZE = 3
MIN_DAY_SPAN = 2
COSINE_THRESHOLD = 0.72
CONFLICT_COSINE_THRESHOLD = 0.55

# Lesson block regex (## YYYY-MM-DD HH:MM or ## YYYY-MM-DD)
LESSON_BLOCK_RE = re.compile(
    r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\s*$",
    re.MULTILINE,
)

NEGATION_RE = re.compile(
    r"\b(never|don'?t|do not|stop|avoid|no longer|must not|cannot|isn'?t|shouldn'?t)\b",
    re.IGNORECASE,
)

# ── Parsing ─────────────────────────────────────────────────────────────────────


def parse_lessons_file(path: Path) -> list[dict]:
    """Parse Meta/memory/<agent>/lessons.md into a list of {agent, date, body}.

    Block format:
        ## YYYY-MM-DD HH:MM (or just ## YYYY-MM-DD)
        free-text body lines until next ## or EOF
    """
    if not path.exists():
        return []
    agent = path.parent.name
    text = path.read_text(encoding="utf-8")
    matches = list(LESSON_BLOCK_RE.finditer(text))
    lessons: list[dict] = []
    for i, m in enumerate(matches):
        date_str = m.group("date")
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if not body or body.startswith("<!--"):
            continue
        lessons.append({
            "agent": agent,
            "date": date,
            "body": body,
            "header_pos": m.start(),
            "block_end": body_end,
            "source_path": str(path),
        })
    return lessons


def collect_all_lessons() -> list[dict]:
    lessons: list[dict] = []
    for lessons_md in MEMORY_DIR.glob("*/lessons.md"):
        lessons.extend(parse_lessons_file(lessons_md))
    return lessons


# ── Canonicalisation ────────────────────────────────────────────────────────────

STOP_PATTERNS = [
    r"\b(the|a|an|and|or|but|to|of|in|on|at|for|with|by|from|as)\b",
    r"[#*_`\[\]()]",
    r"https?://\S+",
    r"\s+",
]


def canonicalize(text: str) -> str:
    s = text.lower()
    for p in STOP_PATTERNS[:-1]:
        s = re.sub(p, " ", s)
    s = re.sub(STOP_PATTERNS[-1], " ", s).strip()
    # crude lemmatisation: strip common suffixes
    tokens = []
    for tok in s.split():
        for suf in ("ies", "ied", "ing", "tion", "tions", "ers", "ed", "es", "ly", "s"):
            if len(tok) > len(suf) + 3 and tok.endswith(suf):
                tok = tok[: -len(suf)]
                break
        tokens.append(tok)
    return " ".join(tokens)


# ── Clustering ──────────────────────────────────────────────────────────────────


def cluster_lessons(lessons: list[dict], threshold: float = COSINE_THRESHOLD) -> list[list[int]]:
    """Greedy single-link clustering via TF-IDF + cosine.
    Returns list of clusters; each cluster is a list of lesson indices.
    """
    if len(lessons) < 2:
        return [[i] for i in range(len(lessons))]
    docs = [canonicalize(L["body"]) for L in lessons]
    vec = TfidfVectorizer(min_df=1, ngram_range=(1, 2))
    try:
        X = vec.fit_transform(docs)
    except ValueError:
        return [[i] for i in range(len(lessons))]
    sim = cosine_similarity(X)
    n = len(lessons)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


# ── Conflict detection vs active.md ─────────────────────────────────────────────

ACTIVE_RULE_RE = re.compile(
    r"^###\s+R(?P<n>\d+)\s*(?:[—\-:]\s*(?P<title>.+))?$",
    re.MULTILINE,
)


def parse_active_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    matches = list(ACTIVE_RULE_RE.finditer(text))
    rules: list[dict] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        rules.append({
            "id": f"R{m.group('n')}",
            "title": (m.group("title") or "").strip(),
            "body": text[body_start:body_end].strip(),
        })
    return rules


def has_polarity_flip(a: str, b: str) -> bool:
    a_neg = bool(NEGATION_RE.search(a))
    b_neg = bool(NEGATION_RE.search(b))
    return a_neg != b_neg  # one has negation, other doesn't → potential flip


def detect_conflict(candidate_body: str, active_rules: list[dict]) -> dict | None:
    if not active_rules:
        return None
    candidate_canon = canonicalize(candidate_body)
    rule_canons = [canonicalize(r["body"]) for r in active_rules]
    vec = TfidfVectorizer(min_df=1, ngram_range=(1, 2))
    try:
        X = vec.fit_transform([candidate_canon] + rule_canons)
    except ValueError:
        return None
    sim = cosine_similarity(X[0:1], X[1:]).flatten()
    for i, score in enumerate(sim):
        if score >= CONFLICT_COSINE_THRESHOLD and has_polarity_flip(
            candidate_body, active_rules[i]["body"]
        ):
            return {"rule": active_rules[i], "similarity": float(score)}
    return None


# ── Candidate selection ────────────────────────────────────────────────────────


def select_candidates(lessons: list[dict], clusters: list[list[int]]) -> list[dict]:
    candidates: list[dict] = []
    for cluster in clusters:
        if len(cluster) < MIN_CLUSTER_SIZE:
            continue
        members = [lessons[i] for i in cluster]
        dates = {m["date"] for m in members}
        if len(dates) < MIN_DAY_SPAN:
            continue
        # canonical = longest body (most context)
        canonical = max(members, key=lambda m: len(m["body"]))
        candidates.append({
            "canonical_body": canonical["body"],
            "members": members,
            "count": len(members),
            "agents": sorted({m["agent"] for m in members}),
            "date_range": (min(dates), max(dates)),
        })
    return candidates


# ── Writers ────────────────────────────────────────────────────────────────────


def write_promotion(candidate: dict, dry_run: bool = False) -> str:
    """Append a new ### R<n> block to active.md. Returns the rule id."""
    text = ACTIVE_MD.read_text(encoding="utf-8") if ACTIVE_MD.exists() else ""
    existing_ids = ACTIVE_RULE_RE.findall(text)
    max_n = max((int(rid[0]) for rid in existing_ids), default=0)
    new_id = f"R{max_n + 1}"
    today = datetime.now(timezone.utc).date().isoformat()
    promoter_note = (
        f"\n\n### {new_id} — auto-promoted {today}\n"
        f"<!-- auto-promoted, unreviewed — {candidate['count']} sources "
        f"({', '.join(candidate['agents'])}) "
        f"span {candidate['date_range'][0]}→{candidate['date_range'][1]} -->\n\n"
        f"{candidate['canonical_body']}\n"
    )
    if not dry_run:
        with open(ACTIVE_MD, "a", encoding="utf-8") as f:
            f.write(promoter_note)
    return new_id


def write_conflict(candidate: dict, conflict: dict, dry_run: bool = False) -> str:
    if not CONFLICT_QUEUE.exists() and not dry_run:
        CONFLICT_QUEUE.write_text(
            "<!-- conflict-queue: auto-promoted candidates that conflict with existing active.md rules. -->\n"
            "<!-- Written by Meta/sync/synthesize-lessons.py per locked-pref B9 (flag conflict, ask before override). -->\n\n",
            encoding="utf-8",
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block = (
        f"\n## Conflict — {now}\n\n"
        f"**Candidate rule** ({candidate['count']} sources, "
        f"{', '.join(candidate['agents'])}, "
        f"{candidate['date_range'][0]}→{candidate['date_range'][1]}):\n\n"
        f"{candidate['canonical_body']}\n\n"
        f"**Conflicts with existing rule** `{conflict['rule']['id']}` "
        f"(cosine={conflict['similarity']:.2f}, polarity flip detected):\n\n"
        f"{conflict['rule']['body'][:400]}{'...' if len(conflict['rule']['body']) > 400 else ''}\n\n"
        f"**Action requested:** Visrut review — accept candidate (deprecate {conflict['rule']['id']}), "
        f"reject candidate (keep {conflict['rule']['id']}), or merge.\n\n---\n"
    )
    if not dry_run:
        with open(CONFLICT_QUEUE, "a", encoding="utf-8") as f:
            f.write(block)
    return conflict["rule"]["id"]


def post_conflict_alert(count: int, dry_run: bool = False) -> None:
    msg = (
        f"\n## [{datetime.now(timezone.utc).isoformat(timespec='minutes')}] "
        "— From: synthesize-lessons → TO: Jarvis\n"
        "**Status**: ⏳ pending\n"
        f"**Subject**: {count} lesson-promotion conflict(s) need review\n"
        f"**Action requested**: Review `Meta/memory/jarvis/conflict-queue.md`; "
        "accept/reject/merge each candidate.\n---\n"
    )
    if not dry_run:
        with open(AGENT_MESSAGES, "a", encoding="utf-8") as f:
            f.write(msg)


def archive_promoted_lessons(promoted_members: list[dict], dry_run: bool = False) -> None:
    """Move promoted lesson blocks from lessons.md to lessons.archive.md per agent."""
    by_source: dict[str, list[dict]] = defaultdict(list)
    for m in promoted_members:
        by_source[m["source_path"]].append(m)

    for source_path_str, members in by_source.items():
        source_path = Path(source_path_str)
        if not source_path.exists():
            continue
        archive_path = source_path.parent / "lessons.archive.md"
        original = source_path.read_text(encoding="utf-8")
        members_sorted = sorted(members, key=lambda x: x["header_pos"], reverse=True)
        archived_blocks = []
        new_text = original
        for m in members_sorted:
            block = original[m["header_pos"]:m["block_end"]]
            archived_blocks.append(block)
            new_text = new_text[: m["header_pos"]] + new_text[m["block_end"]:]

        if dry_run:
            continue
        archive_path.touch(exist_ok=True)
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(
                f"\n<!-- archived by synthesize-lessons.py at {datetime.now(timezone.utc).isoformat()} -->\n"
            )
            for block in reversed(archived_blocks):
                f.write(block.rstrip() + "\n\n")
        source_path.write_text(new_text, encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    lessons = collect_all_lessons()
    if not lessons:
        print("[synthesize-lessons] no lessons found; nothing to do.")
        return 0

    if args.verbose:
        print(f"[synthesize-lessons] collected {len(lessons)} lessons across {len(set(L['agent'] for L in lessons))} agents")

    clusters = cluster_lessons(lessons)
    candidates = select_candidates(lessons, clusters)

    if not candidates:
        print(f"[synthesize-lessons] {len(lessons)} lessons → 0 candidates meeting "
              f"size≥{MIN_CLUSTER_SIZE} AND day-span≥{MIN_DAY_SPAN}; nothing to promote.")
        if not args.dry_run:
            LAST_SYNTH_MARKER.write_text(datetime.now(timezone.utc).isoformat())
        return 0

    active_rules = parse_active_rules(ACTIVE_MD)
    promoted = []
    conflicts = []
    promoted_members: list[dict] = []

    for cand in candidates:
        conflict = detect_conflict(cand["canonical_body"], active_rules)
        if conflict:
            rid = write_conflict(cand, conflict, dry_run=args.dry_run)
            conflicts.append({"candidate": cand, "conflict_with": rid})
        else:
            new_id = write_promotion(cand, dry_run=args.dry_run)
            promoted.append({"candidate": cand, "new_id": new_id})
            promoted_members.extend(cand["members"])

    archive_promoted_lessons(promoted_members, dry_run=args.dry_run)

    if conflicts:
        post_conflict_alert(len(conflicts), dry_run=args.dry_run)

    # Output summary
    print(f"[synthesize-lessons] {len(lessons)} lessons → {len(candidates)} candidates")
    print(f"  ✓ {len(promoted)} promoted to active.md "
          f"({'DRY-RUN' if args.dry_run else 'WROTE'})")
    for p in promoted:
        c = p["candidate"]
        print(f"      {p['new_id']}: count={c['count']} agents={','.join(c['agents'])} "
              f"days={c['date_range'][0]}→{c['date_range'][1]}")
        print(f"        canonical: {c['canonical_body'][:120].strip()}...")
    print(f"  ⚠ {len(conflicts)} conflicts queued to conflict-queue.md")
    for c in conflicts:
        cd = c["candidate"]
        print(f"      vs {c['conflict_with']}: count={cd['count']}")
        print(f"        candidate: {cd['canonical_body'][:120].strip()}...")

    if not args.dry_run:
        LAST_SYNTH_MARKER.write_text(datetime.now(timezone.utc).isoformat())
        # change-log
        with open(CHANGE_LOG, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(
                    f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] "
                    f"synthesize-lessons → PROMOTED {len(promoted)} rules to active.md, "
                    f"FLAGGED {len(conflicts)} conflicts to conflict-queue.md "
                    f"(from {len(lessons)} lessons across {len(set(L['agent'] for L in lessons))} agents)\n"
                )
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[synthesize-lessons] FATAL: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
