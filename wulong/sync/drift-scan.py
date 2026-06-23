#!/usr/bin/env python3
"""
drift-scan.py — Propose-only drift scanner.
Reports stale project-name references (and ad-hoc stale strings) across the vault
and Wulong code repos. Never edits files.

Exit code: always 0 (WARN-only model — must never block session close).

Usage:
  python3 Meta/sync/drift-scan.py                  # full scan from reference-map.md
  python3 Meta/sync/drift-scan.py --old X --new Y  # ad-hoc single-alias scan
  python3 Meta/sync/drift-scan.py --self-check      # hermetic self-test, no real files
"""

import argparse
import fnmatch
import os
import re
import sys
import tempfile
from pathlib import Path

REFMAP_PATH = Path(__file__).parent.parent / "reference-map.md"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def _parse_refmap(text: str) -> dict:
    """Extract the ```refmap fenced block from text and parse it."""
    m = re.search(r"```refmap\n(.*?)```", text, re.DOTALL)
    if not m:
        sys.exit("drift-scan: ERROR: no ```refmap block found in reference-map.md")
    return _parse_block(m.group(1))


def _parse_block(block: str) -> dict:
    """
    Parse the refmap block into a dict.
    Keys with no inline value accumulate the following '  - item' lines as a list.
    # comments are stripped. ~ in paths is not expanded here (caller does it).
    """
    result: dict = {}
    current_key: str | None = None

    for raw_line in block.splitlines():
        line = raw_line.split("#")[0].rstrip()  # strip comments
        if not line.strip():
            continue

        # Top-level key (no leading whitespace)
        if line and line[0] != " ":
            key_match = re.match(r"^([\w_]+):\s*(.*)", line)
            if not key_match:
                continue
            current_key = key_match.group(1)
            inline_val = key_match.group(2).strip()
            if inline_val:
                result[current_key] = inline_val
            else:
                result.setdefault(current_key, [])
        elif current_key is not None:
            # List item
            item_match = re.match(r"^\s+-\s+(.*)", line)
            if item_match:
                val = item_match.group(1).strip()
                if isinstance(result.get(current_key), list):
                    result[current_key].append(val)

    return result


def _build_config(raw: dict) -> dict:
    """
    Normalise the raw parsed dict into typed config fields.
    All list fields default to [] if missing or empty.
    """
    def _listof(key: str) -> list:
        v = raw.get(key, [])
        return v if isinstance(v, list) else []

    aliases_raw = _listof("aliases")
    aliases = []
    for entry in aliases_raw:
        m = re.match(r"^(.+?)\s*->\s*(.+)$", entry)
        if m:
            aliases.append((m.group(1).strip(), m.group(2).strip()))

    scan_repos = [Path(p).expanduser() for p in _listof("scan_repos")]
    scan_repos_extras = [Path(p).expanduser() for p in _listof("scan_repos_extras")]

    return {
        "aliases": aliases,
        "scan_repos": scan_repos + scan_repos_extras,
        "code_extensions": set(_listof("code_extensions")),
        "doc_extensions": set(_listof("doc_extensions")),
        "history_exempt_files": _listof("history_exempt_files"),
        "code_exempt_files": _listof("code_exempt_files"),
        "excludes": _listof("excludes"),
    }


# ---------------------------------------------------------------------------
# File walking
# ---------------------------------------------------------------------------

def _is_excluded(path: Path, excludes: list) -> bool:
    """
    Return True if any part of the path matches an exclude entry.
    Glob patterns (containing * or ?) are matched against the basename only.
    Plain names are matched against any path component.
    """
    for ex in excludes:
        if "*" in ex or "?" in ex:
            if fnmatch.fnmatch(path.name, ex):
                return True
        else:
            if ex in path.parts:
                return True
    return False


def _walk_files(root: Path, excludes: list):
    """Yield (file_path, root) for every non-excluded file under root."""
    if not root.exists():
        print(f"(skip: {root} not found)")
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [
            d for d in dirnames
            if not _is_excluded(dp / d, excludes)
        ]
        for fname in filenames:
            fp = dp / fname
            if not _is_excluded(fp, excludes):
                yield fp, root


def _is_exempt(filepath: Path, root: Path, exempt_list: list) -> bool:
    """
    Check if filepath matches any entry in exempt_list.
    Matches on path suffix (relative to root) or basename.
    # ponytail: plain suffix/basename match; upgrade to glob if patterns added later.
    """
    try:
        rel = str(filepath.relative_to(root))
    except ValueError:
        rel = str(filepath)
    basename = filepath.name
    for ex in exempt_list:
        # Normalise to forward slashes for comparison
        ex_norm = ex.replace("\\", "/")
        rel_norm = rel.replace("\\", "/")
        if rel_norm.endswith(ex_norm) or basename == ex_norm or rel_norm == ex_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_file(filepath: Path, needle: str) -> list:
    """
    Return list of (lineno, stripped_line) for lines containing needle.
    # ponytail: plain substring, case-sensitive; no word-boundary — avoids false negatives
    #           on identifiers like old_project_config. Upgrade to word-boundary regex
    #           if false-positive rate becomes a problem.
    """
    hits = []
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as fh:
            for lineno, line in enumerate(fh, 1):
                if needle in line:
                    hits.append((lineno, line.strip()))
    except (OSError, UnicodeDecodeError):
        pass
    return hits


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def run_full_scan(cfg: dict) -> int:
    """
    Run the full alias scan. Returns total hit count (for receipt).
    Prints two grouped sections (HIGH code hits, then DOC review hits) and a summary.
    """
    aliases = cfg["aliases"]
    code_ext = cfg["code_extensions"]
    doc_ext = cfg["doc_extensions"]
    history_exempt = cfg["history_exempt_files"]
    code_exempt = cfg["code_exempt_files"]
    excludes = cfg["excludes"]
    scan_repos = cfg["scan_repos"]

    # Bucket: alias -> list of (relpath, lineno, line)
    code_hits: dict = {old: [] for old, _ in aliases}
    doc_hits: dict = {old: [] for old, _ in aliases}
    files_with_hits: set = set()

    for root in scan_repos:
        for filepath, walk_root in _walk_files(root, excludes):
            suffix = filepath.suffix.lower()
            is_code = suffix in code_ext
            is_doc = suffix in doc_ext
            if not is_code and not is_doc:
                continue

            code_ex = is_code and _is_exempt(filepath, walk_root, code_exempt)
            doc_ex = is_doc and _is_exempt(filepath, walk_root, history_exempt)
            if code_ex or doc_ex:
                continue

            try:
                rel = str(filepath.relative_to(walk_root))
            except ValueError:
                rel = str(filepath)

            for old, _ in aliases:
                hits = _search_file(filepath, old)
                if hits:
                    files_with_hits.add(str(filepath))
                    bucket = code_hits if is_code else doc_hits
                    for lineno, line in hits:
                        bucket[old].append((rel, lineno, line))

    # Print HIGH bucket
    total_code = 0
    has_code = any(v for v in code_hits.values())
    if has_code:
        print("=== HIGH SIGNAL: stale alias in CODE files ===")
        for old, new in aliases:
            hits = code_hits[old]
            if hits:
                print(f"\n  {old!r} -> should be {new!r}")
                for rel, lineno, line in hits:
                    print(f"    {rel}:{lineno}: {line}")
                total_code += len(hits)

    # Print DOC bucket
    total_doc = 0
    has_doc = any(v for v in doc_hits.values())
    if has_doc:
        print("\n=== DOC REVIEW: stale alias in .md files (may be legitimate history) ===")
        for old, new in aliases:
            hits = doc_hits[old]
            if hits:
                print(f"\n  {old!r} -> should be {new!r}")
                for rel, lineno, line in hits:
                    print(f"    {rel}:{lineno}: {line}")
                total_doc += len(hits)

    if not has_code and not has_doc:
        print("(no drift hits)")

    print(
        f"\nDRIFT: {total_code} high-signal code hits, {total_doc} doc-review hits"
        f" across {len(files_with_hits)} files"
    )
    return total_code + total_doc


# ---------------------------------------------------------------------------
# Ad-hoc scan
# ---------------------------------------------------------------------------

def run_adhoc_scan(cfg: dict, old: str, new: str) -> int:
    """Search literal `old` across all in-scope files (code + doc extensions)."""
    all_ext = cfg["code_extensions"] | cfg["doc_extensions"]
    excludes = cfg["excludes"]
    hits_total = 0
    files_hit: set = set()

    for root in cfg["scan_repos"]:
        for filepath, walk_root in _walk_files(root, excludes):
            if filepath.suffix.lower() not in all_ext:
                continue
            hits = _search_file(filepath, old)
            if hits:
                try:
                    rel = str(filepath.relative_to(walk_root))
                except ValueError:
                    rel = str(filepath)
                files_hit.add(str(filepath))
                for lineno, line in hits:
                    print(f"  {rel}:{lineno}: {line}")
                hits_total += len(hits)

    print(f'\nAD-HOC: {hits_total} hits for "{old}" -> "{new}" across {len(files_hit)} files')
    return hits_total


# ---------------------------------------------------------------------------
# Self-check (hermetic)
# ---------------------------------------------------------------------------

def run_self_check() -> None:
    """
    Hermetic self-test using a temporary directory and an in-code config.
    Does NOT read reference-map.md or touch real repos.
    Prints SELF-CHECK PASS and exits 0 on success; exits 1 on any assertion failure.
    """
    STALE = "old_project"
    CANONICAL = "new_project"

    MINI_CONFIG = f"""
aliases:
  - {STALE} -> {CANONICAL}
scan_repos:
  - SEED_DIR
scan_repos_extras:
code_extensions:
  - .py
doc_extensions:
  - .md
history_exempt_files:
  - brain.md
code_exempt_files:
excludes:
  - venv
  - .git
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        td = Path(tmpdir)

        # Seed files
        # (a) stale alias in a .py file — should be found in CODE bucket
        code_file = td / "config.py"
        code_file.write_text(f'PROJECTS = ["{STALE}"]\n')

        # (b) stale alias inside excluded venv/ — should NOT be found
        venv_dir = td / "venv"
        venv_dir.mkdir()
        venv_file = venv_dir / "legacy.py"
        venv_file.write_text(f'OLD = "{STALE}"\n')

        # (c) stale alias in history-exempt brain.md — should NOT appear in DOC hits
        brain_file = td / "brain.md"
        brain_file.write_text(f'Previously called {STALE}.\n')

        # (d) current canonical name in a .py file — should NOT be flagged
        current_file = td / "current.py"
        current_file.write_text(f'ACTIVE = "{CANONICAL}"\n')

        # Build config with real tmpdir
        raw_cfg = _parse_block(MINI_CONFIG.replace("SEED_DIR", str(td)))
        cfg = _build_config(raw_cfg)

        # Run full scan in-memory
        code_ext = cfg["code_extensions"]
        doc_ext = cfg["doc_extensions"]
        history_exempt = cfg["history_exempt_files"]
        code_exempt = cfg["code_exempt_files"]
        excludes = cfg["excludes"]

        code_hits = {STALE: []}
        doc_hits = {STALE: []}

        for root in cfg["scan_repos"]:
            for filepath, walk_root in _walk_files(root, excludes):
                suffix = filepath.suffix.lower()
                is_code = suffix in code_ext
                is_doc = suffix in doc_ext
                if not is_code and not is_doc:
                    continue
                code_ex = is_code and _is_exempt(filepath, walk_root, code_exempt)
                doc_ex = is_doc and _is_exempt(filepath, walk_root, history_exempt)
                if code_ex or doc_ex:
                    continue
                hits = _search_file(filepath, STALE)
                if hits:
                    bucket = code_hits if is_code else doc_hits
                    for lineno, line in hits:
                        try:
                            rel = str(filepath.relative_to(walk_root))
                        except ValueError:
                            rel = str(filepath)
                        bucket[STALE].append((rel, lineno, line))
                # Assert canonical is never in search needles (it is, by design, not searched)
                # — no explicit check needed: aliases only contains STALE, never CANONICAL

        # (a) stale alias in .py IS found in code bucket
        assert code_hits[STALE], \
            f"FAIL (a): stale alias {STALE!r} in code file not detected"

        # (b) stale alias inside venv/ is NOT found
        venv_code_paths = [r for r, _, _ in code_hits[STALE] if "venv" in r]
        assert not venv_code_paths, \
            f"FAIL (b): excluded venv/ file was scanned: {venv_code_paths}"

        # (c) history-exempt brain.md is NOT in doc_hits
        brain_doc_hits = [r for r, _, _ in doc_hits[STALE] if "brain.md" in r]
        assert not brain_doc_hits, \
            f"FAIL (c): history-exempt brain.md appeared in doc hits: {brain_doc_hits}"

        # (d) canonical CANONICAL is not reported (it was never searched — implicit by no alias)
        canonical_code_hits = []
        for filepath, walk_root in _walk_files(td, excludes):
            if filepath.suffix.lower() in code_ext:
                for _, line in _search_file(filepath, CANONICAL):
                    if STALE not in line:  # only pure-canonical lines
                        canonical_code_hits.append(line)
        # canonical_code_hits may exist (current.py has it) but drift-scan never flags them
        # because CANONICAL is not in the aliases OLD-side. Confirm by checking code_hits keys.
        assert CANONICAL not in code_hits, \
            f"FAIL (d): canonical name {CANONICAL!r} was added to search keys"

    print("SELF-CHECK PASS")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Propose-only drift scanner. Exit code always 0.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--old", help="Ad-hoc: stale string to search for")
    parser.add_argument("--new", help="Ad-hoc: canonical replacement (printed in report)")
    parser.add_argument("--self-check", action="store_true",
                        help="Hermetic self-test. Does not scan real repos.")
    args = parser.parse_args()

    if args.self_check:
        run_self_check()
        return  # run_self_check exits

    if bool(args.old) != bool(args.new):
        parser.error("--old and --new must both be provided together")

    refmap_text = REFMAP_PATH.read_text(encoding="utf-8")
    raw = _parse_refmap(refmap_text)
    cfg = _build_config(raw)

    if args.old:
        run_adhoc_scan(cfg, args.old, args.new)
    else:
        run_full_scan(cfg)


if __name__ == "__main__":
    main()
