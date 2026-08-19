"""
Cerebrum substrate BM25 search over Meta/ knowledge documents.

The Cerebrum substrate is the vault's long-term memory layer: receipts, playbooks,
and the knowledge-base together form the ground truth of what this autonomous
company knows and how it operates. This script indexes all three tiers into an
SQLite FTS5 virtual table (FTS5 implements BM25 ranking internally) and exposes
a CLI for full-text search, incremental updates, index rebuilds, and statistics.
It has zero external dependencies — stdlib only.

Root resolution order (explicit beats ambient):
  1. --root CLI argument
  2. WULONG_ROOT environment variable
  3. The directory two levels above wulong/sync/ (the repo root in a source
     checkout, site-packages in a wheel install)
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from wulong._root import resolve_root
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TIER_NAMES = ("receipts", "playbooks", "kb")


def _resolve_root(cli_root: Optional[str] = None) -> str:
    """Vault root. Delegates to the ONE resolver in wulong/_root.py.

    This file used to carry its own 35-line copy of the precedence, as did six
    siblings. Seven copies of one rule in a tool whose headline defect was that
    rule is how the copies drifted apart in the first place.

    The floor here stays install-relative, unlike the CLI entry points which
    raise instead. This script runs as a child with the root handed down, so the
    floor is only reached on direct manual invocation, and a script sitting at
    <vault>/Meta/sync/ knows its own vault.
    """
    return resolve_root(
        cli_root,
        fallback=os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ),
        tool="cerebrum-search",
    )


def _make_tiers(vault_meta: Path) -> dict[str, Path]:
    return {
        "receipts": vault_meta / "receipts",
        "playbooks": vault_meta / "playbooks",
        "kb": vault_meta / "knowledge-base",
    }

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 3  # bump on any schema change to trigger auto-rebuild


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and return a connection."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _schema_ok(conn: sqlite3.Connection) -> bool:
    """Return True if the db schema version matches the current expected version."""
    try:
        row = conn.execute(
            "SELECT value FROM _meta WHERE key = 'schema_version'"
        ).fetchone()
        return row is not None and int(row["value"]) == _SCHEMA_VERSION
    except sqlite3.OperationalError:
        return False


def _create_schema(conn: sqlite3.Connection) -> None:
    """Drop existing tables and recreate the FTS5 table + _meta table."""
    conn.executescript(
        """
        DROP TABLE IF EXISTS docs;
        DROP TABLE IF EXISTS _meta;

        CREATE VIRTUAL TABLE docs USING fts5(
            path,
            tier,
            title,
            body,
            mtime UNINDEXED,
            tokenize = 'porter ascii'
        );

        CREATE TABLE _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO _meta (key, value) VALUES ('last_update', '0')",
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the start of a markdown string."""
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def _extract_title(filename: str, body: str) -> str:
    """Derive title from the first H1 in body, falling back to the filename stem."""
    m = _H1_RE.search(body)
    if m:
        return m.group(1).strip()
    return Path(filename).stem.replace("-", " ").replace("_", " ")


def _tier_for_path(path: Path, tiers: dict[str, Path]) -> str:
    """Return the tier name ('receipts'|'playbooks'|'kb') for a given file path."""
    for name, root in tiers.items():
        try:
            path.relative_to(root)
            return name
        except ValueError:
            continue
    return "unknown"


def _collect_files(tiers: dict[str, Path]) -> list[Path]:
    """Recursively gather all .md files across all indexed tiers."""
    files: list[Path] = []
    for root in tiers.values():
        if root.exists():
            files.extend(root.rglob("*.md"))
    return files


def _read_doc(path: Path, tiers: dict[str, Path]) -> tuple[str, str, str, float]:
    """
    Read a markdown file and return (tier, title, body, mtime).

    body has frontmatter stripped; title derived from first H1 or filename.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    body = _strip_frontmatter(raw)
    title = _extract_title(path.name, body)
    tier = _tier_for_path(path, tiers)
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return tier, title, body, mtime


# ---------------------------------------------------------------------------
# Index operations
# ---------------------------------------------------------------------------

def _get_last_update(conn: sqlite3.Connection) -> float:
    """Return the float timestamp of the last index update."""
    row = conn.execute(
        "SELECT value FROM _meta WHERE key = 'last_update'"
    ).fetchone()
    if row:
        return float(row["value"])
    return 0.0


def _set_last_update(conn: sqlite3.Connection, ts: float) -> None:
    """Persist the last-update timestamp."""
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('last_update', ?)",
        (str(ts),),
    )
    conn.commit()


def cmd_rebuild(conn: sqlite3.Connection, tiers: dict[str, Path]) -> None:
    """Drop and rebuild the entire index from scratch."""
    _create_schema(conn)
    files = _collect_files(tiers)
    rows = []
    for path in files:
        tier, title, body, mtime = _read_doc(path, tiers)
        rows.append((str(path), tier, title, body, mtime))
    conn.executemany(
        "INSERT INTO docs (path, tier, title, body, mtime) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    _set_last_update(conn, time.time())
    print(f"Rebuilt: {len(rows)} files indexed across {len(tiers)} tiers.")


def cmd_update(conn: sqlite3.Connection, tiers: dict[str, Path]) -> None:
    """Incrementally re-index files modified since the last update."""
    last = _get_last_update(conn)
    files = _collect_files(tiers)
    updated = 0
    for path in files:
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if mtime <= last:
            continue
        tier, title, body, mtime_val = _read_doc(path, tiers)
        # Delete existing entry for this path (FTS5 has no UPDATE shortcut)
        conn.execute("DELETE FROM docs WHERE path = ?", (str(path),))
        conn.execute(
            "INSERT INTO docs (path, tier, title, body, mtime) VALUES (?, ?, ?, ?, ?)",
            (str(path), tier, title, body, mtime_val),
        )
        updated += 1
    _set_last_update(conn, time.time())
    conn.commit()
    print(f"Updated: {updated} files re-indexed (unchanged files skipped).")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _make_snippet(body: str, query_terms: list[str], lines: int = 2) -> str:
    """
    Find the passage in body with the highest density of query_terms and
    return at most `lines` lines of it.
    """
    body_lines = body.splitlines()
    if not body_lines:
        return ""

    terms_lower = [t.lower() for t in query_terms if t]
    best_start = 0
    best_score = -1
    window = max(lines, 1)

    for i in range(len(body_lines) - window + 1):
        chunk = " ".join(body_lines[i : i + window]).lower()
        score = sum(chunk.count(t) for t in terms_lower)
        if score > best_score:
            best_score = score
            best_start = i

    snippet_lines = body_lines[best_start : best_start + window]
    return "\n".join(line.rstrip() for line in snippet_lines if line.strip())


def cmd_search(
    conn: sqlite3.Connection,
    query: str,
    vault_root: Path,
    limit: int = 10,
    tier: Optional[str] = None,
) -> None:
    """Execute a BM25 search and print ranked results with snippets."""
    fts_query = query
    base_sql = """
        SELECT path, tier, title, body,
               bm25(docs) AS score
        FROM docs
        WHERE docs MATCH ?
        {tier_filter}
        ORDER BY score
        LIMIT ?
    """
    if tier:
        sql = base_sql.format(tier_filter="AND tier = ?")
        params: tuple = (fts_query, tier, limit)
    else:
        sql = base_sql.format(tier_filter="")
        params = (fts_query, limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"Search error: {exc}")
        print("Hint: use double quotes around exact phrases, e.g. '\"contrarian pass\"'")
        return

    if not rows:
        print(f"No results for: {query!r}")
        return

    query_terms = re.split(r"\s+", query.strip())

    for rank, row in enumerate(rows, start=1):
        rel_path = Path(row["path"])
        try:
            display_path = str(rel_path.relative_to(vault_root.parent))
        except ValueError:
            display_path = str(rel_path)

        snippet = _make_snippet(row["body"], query_terms, lines=2)
        snippet_lines = snippet.splitlines()
        padded = "\n".join(f"    {line}" for line in snippet_lines)

        print(
            f"#{rank:>2}  score={row['score']:.4f}  [{row['tier']}]"
        )
        print(f"     path:  {display_path}")
        print(f"     title: {row['title']}")
        if padded:
            print(padded)
        print()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def cmd_stats(conn: sqlite3.Connection, db_path: Path) -> None:
    """Print index statistics: file count per tier, db size, last update."""
    rows = conn.execute(
        "SELECT tier, COUNT(*) AS n FROM docs GROUP BY tier ORDER BY tier"
    ).fetchall()
    total = sum(r["n"] for r in rows)
    db_bytes = db_path.stat().st_size if db_path.exists() else 0
    db_kb = db_bytes / 1024

    last_ts = _get_last_update(conn)
    if last_ts:
        last_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(last_ts))
    else:
        last_str = "never"

    print("Cerebrum index stats")
    print(f"  DB path:     {db_path}")
    print(f"  DB size:     {db_kb:.1f} KB")
    print(f"  Last update: {last_str}")
    print(f"  Total docs:  {total}")
    for row in rows:
        print(f"    {row['tier']:10s}: {row['n']}")


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def run_smoke_test(db_path: Path, tiers: dict[str, Path]) -> None:
    """
    Rebuild index, search for 'contrarian', assert at least one hit.
    Prints SMOKE OK line with counts on success; exits non-zero on failure.
    """
    if db_path.exists():
        db_path.unlink()

    conn = _connect(db_path)
    cmd_rebuild(conn, tiers)

    row_count = conn.execute("SELECT COUNT(*) AS n FROM docs").fetchone()["n"]

    # Execute search
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM docs WHERE docs MATCH 'contrarian'"
    ).fetchone()
    hits = rows["n"] if rows else 0

    conn.close()

    if hits == 0:
        print("SMOKE FAIL: 0 hits for 'contrarian' — index may be empty or broken.")
        sys.exit(1)

    print(f"SMOKE OK: {row_count} files indexed, {hits} hits for 'contrarian'")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cerebrum-search",
        description="BM25 search over the Cerebrum substrate (receipts, playbooks, kb).",
    )
    parser.add_argument("query", nargs="?", help="Search query string")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default 10)")
    parser.add_argument(
        "--tier",
        choices=list(TIER_NAMES),
        default=None,
        help="Filter results to a single tier",
    )
    parser.add_argument("--rebuild", action="store_true", help="Rebuild index from scratch")
    parser.add_argument(
        "--update", action="store_true", help="Incremental update (mtime-based)"
    )
    parser.add_argument("--stats", action="store_true", help="Show index statistics")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Rebuild the index and assert it is searchable, then exit",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="Vault root path (overrides WULONG_ROOT env var)",
    )
    return parser


def main() -> None:
    """Entry point for CLI invocation."""
    parser = _build_parser()
    args = parser.parse_args()

    root = Path(_resolve_root(getattr(args, "root", None)))
    meta_dir = root / "Meta"
    db_path = meta_dir / "sync" / ".cerebrum-index.db"
    tiers = _make_tiers(meta_dir)

    if args.smoke:
        run_smoke_test(db_path, tiers)
        return

    conn = _connect(db_path)

    # Auto-rebuild on schema mismatch
    if not _schema_ok(conn):
        print("Schema mismatch detected — auto-rebuilding index...")
        cmd_rebuild(conn, tiers)

    if args.rebuild:
        cmd_rebuild(conn, tiers)
    elif args.update:
        cmd_update(conn, tiers)
    elif args.stats:
        cmd_stats(conn, db_path)
    elif args.query:
        cmd_search(conn, args.query, meta_dir, limit=args.limit, tier=args.tier)
    else:
        parser.print_help()

    conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
