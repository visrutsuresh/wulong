#!/usr/bin/env python3
"""
vault-health-check.py — read-only vault health scanner.

Checks:
  A inbox_backlog       B stray_code        C handoff_backlog
  D orphan_notes        E empty_folder      F broken_wikilink

Exit 0 = no RED.  Exit 1 = one or more RED checks.
YELLOW never changes exit code.

Usage:
  python3 vault-health-check.py [vault_root]
  python3 vault-health-check.py --selftest
"""

import os
import re
import sys
import json
import tempfile
import pathlib
import subprocess

# ---------------------------------------------------------------------------
# Vault root resolution
# ---------------------------------------------------------------------------

def find_vault_root(start: pathlib.Path) -> pathlib.Path:
    """Walk up from *start* until we find CLAUDE.md."""
    current = start.resolve()
    for _ in range(20):
        if (current / "CLAUDE.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(f"CLAUDE.md not found walking up from {start}")


# ---------------------------------------------------------------------------
# Allow-list loading (stray_code check B)
# ---------------------------------------------------------------------------

# Markdown table heading the script looks for in vault-structure.md
_ALLOW_HEADING = re.compile(r"^##\s+Declared In-Vault Code Locations", re.IGNORECASE)
# A row like: | `02-Areas/Wulong/v3/` | ... |
_TABLE_ROW_PATH = re.compile(r"\|\s*`([^`]+)`")


def _load_allow_list_from_md(vault: pathlib.Path) -> list[str] | None:
    """
    Parse vault-structure.md for the 'Declared In-Vault Code Locations' table.
    Returns list of path strings (relative to vault root), or None if section absent.
    """
    vsfile = vault / "Meta" / "vault-structure.md"
    if not vsfile.exists():
        return None
    lines = vsfile.read_text(encoding="utf-8", errors="replace").splitlines()
    in_section = False
    paths: list[str] = []
    for line in lines:
        if _ALLOW_HEADING.match(line):
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and not _ALLOW_HEADING.match(line):
                break  # next section
            m = _TABLE_ROW_PATH.search(line)
            if m:
                paths.append(m.group(1).rstrip("/"))
    return paths if paths else None


def load_stray_allow_list(vault: pathlib.Path) -> tuple[list[str] | None, str]:
    """
    Returns (allow_list, source) where source is 'json', 'md', or 'absent'.
    JSON key 'stray_code_allow_list' takes precedence.
    """
    thresholds_path = vault / "Meta" / "sync" / "vault-health-thresholds.json"
    if thresholds_path.exists():
        try:
            data = json.loads(thresholds_path.read_text(encoding="utf-8"))
            if "stray_code_allow_list" in data:
                return data["stray_code_allow_list"], "json"
        except (json.JSONDecodeError, OSError):
            pass

    md_list = _load_allow_list_from_md(vault)
    if md_list is not None:
        return md_list, "md"

    return None, "absent"


def _path_in_allow_list(abs_path: str, vault: pathlib.Path, allow_list: list[str]) -> bool:
    """
    True if abs_path is covered by any allow-list entry.
    Entries with a literal path component 'attachments' match any path containing /attachments/.
    """
    vault_str = str(vault)
    for entry in allow_list:
        # ponytail: attachments glob matches any path with an /attachments/ segment
        if entry.rstrip("/") == "<any-project>/attachments":
            if f"{os.sep}attachments{os.sep}" in abs_path or abs_path.endswith(f"{os.sep}attachments"):
                return True
            continue
        # entry is relative to vault root; may end with /
        abs_entry = os.path.join(vault_str, entry.lstrip("/"))
        if abs_path.startswith(abs_entry + os.sep) or abs_path.startswith(abs_entry):
            return True
    return False


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def load_thresholds(vault: pathlib.Path) -> dict:
    defaults = {"handoff_backlog_red": 1000}
    thresholds_path = vault / "Meta" / "sync" / "vault-health-thresholds.json"
    if thresholds_path.exists():
        try:
            data = json.loads(thresholds_path.read_text(encoding="utf-8"))
            defaults.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAILY_NOTE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_CODE_EXTS = {".py", ".sh", ".js", ".ts"}

# Knowledge folders walked by most checks
_KNOWLEDGE_DIRS = [
    "00-Inbox", "01-Projects", "02-Areas",
    "03-Resources", "04-Archive", "05-People",
    "06-Meetings", "07-Daily", "MOC", "Templates",
]
# Orphan candidate folders (subset)
_ORPHAN_DIRS = ["01-Projects", "02-Areas", "03-Resources"]
# Stray-code scan folders
_STRAY_DIRS = ["01-Projects", "02-Areas", "03-Resources", "07-Daily", "MOC"]


def _iter_md(vault: pathlib.Path, folders: list[str]):
    """Yield all .md files under the given folder names."""
    for folder in folders:
        root = vault / folder
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fname in files:
                if fname.lower().endswith(".md"):
                    yield pathlib.Path(dirpath) / fname


def _iter_code(vault: pathlib.Path, folders: list[str]):
    """Yield all code files (.py/.sh/.js/.ts) under given folder names."""
    for folder in folders:
        root = vault / folder
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fname in files:
                if pathlib.Path(fname).suffix.lower() in _CODE_EXTS:
                    yield pathlib.Path(dirpath) / fname


# ---------------------------------------------------------------------------
# Check A — inbox_backlog
# ---------------------------------------------------------------------------

def check_a_inbox_backlog(vault: pathlib.Path) -> list[str]:
    inbox = vault / "00-Inbox"
    if not inbox.is_dir():
        return []
    items = [p for p in inbox.iterdir() if p.is_file()]
    count = len(items)
    if count > 10:
        return [f"RED  [A] inbox_backlog: {count} items in 00-Inbox/ (threshold 10)"]
    return []


# ---------------------------------------------------------------------------
# Check B — stray_code
# ---------------------------------------------------------------------------

def check_b_stray_code(vault: pathlib.Path) -> list[str]:
    allow_list, source = load_stray_allow_list(vault)
    if allow_list is None:
        return ["WARNING [B] stray_code: allow-list absent from both vault-structure.md and vault-health-thresholds.json — axis skipped (fail-open)"]

    strays = []
    for fpath in _iter_code(vault, _STRAY_DIRS):
        abs_str = str(fpath)
        if not _path_in_allow_list(abs_str, vault, allow_list):
            strays.append(str(fpath.relative_to(vault)))

    count = len(strays)
    lines = []
    if count == 0:
        pass  # GREEN — silent
    elif count <= 2:
        for s in strays[:20]:
            lines.append(f"YELLOW [B] stray_code: {s}")
    else:
        for s in strays[:20]:
            lines.append(f"RED  [B] stray_code: {s}")
        if count > 20:
            lines.append(f"RED  [B] stray_code: ... ({count - 20} more)")
    return lines


# ---------------------------------------------------------------------------
# Check C — handoff_backlog
# ---------------------------------------------------------------------------

def check_c_handoff_backlog(vault: pathlib.Path, thresholds: dict) -> list[str]:
    handoffs_dir = vault / "Meta" / "handoffs"
    if not handoffs_dir.is_dir():
        return []
    threshold = int(thresholds.get("handoff_backlog_red", 1000))
    count = sum(
        1 for p in handoffs_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".md"
        and "archive" not in str(p).split(os.sep)
    )
    if count > threshold:
        return [f"RED  [C] handoff_backlog: {count} handoffs in Meta/handoffs/ (threshold {threshold})"]
    return []


# ---------------------------------------------------------------------------
# Check D — orphan_notes
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _extract_link_stems(content: str) -> set[str]:
    """Return lowercased final-component stems from all [[...]] links in content."""
    stems: set[str] = set()
    for m in _WIKILINK_RE.finditer(content):
        target = m.group(1)
        # strip |alias
        target = target.split("|")[0]
        # strip #heading and ^blockref
        target = target.split("#")[0].split("^")[0].strip()
        if not target or "://" in target or target.startswith("/"):
            continue
        stem = pathlib.Path(target).stem.lower()
        if stem:
            stems.add(stem)
    return stems


def _is_skip_candidate(fname: str) -> bool:
    """Skip index, README, and YYYY-MM-DD daily notes."""
    lower = fname.lower()
    if lower in ("index.md", "readme.md"):
        return True
    if _DAILY_NOTE.match(fname):
        return True
    return False


def check_d_orphan_notes(vault: pathlib.Path) -> list[str]:
    # Build candidate set
    candidates: list[pathlib.Path] = []
    for fpath in _iter_md(vault, _ORPHAN_DIRS):
        if not _is_skip_candidate(fpath.name):
            candidates.append(fpath)

    candidate_stems = {p.stem.lower() for p in candidates}

    # Collect all wikilink targets mentioned anywhere among candidates
    mentioned: set[str] = set()
    for fpath in candidates:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        mentioned |= _extract_link_stems(content)

    orphans = [p for p in candidates if p.stem.lower() not in mentioned]
    count = len(orphans)
    if count <= 20:
        return []
    examples = [str(p.relative_to(vault)) for p in orphans[:5]]
    return [
        f"RED  [D] orphan_notes: {count} orphans in knowledge folders (threshold 20). "
        f"Examples: {', '.join(examples)}"
    ]


# ---------------------------------------------------------------------------
# Check E — empty_folder
# ---------------------------------------------------------------------------

_EMPTY_SKIP_FILES = {".gitkeep", ".ds_store"}

def _has_content(dirpath: pathlib.Path) -> bool:
    try:
        for item in dirpath.iterdir():
            if item.is_file() and item.name.lower() not in _EMPTY_SKIP_FILES:
                return True
            if item.is_dir():
                return True  # non-empty if it has subdirs (let child dirs be checked separately)
    except PermissionError:
        return True  # treat as non-empty if we can't read
    return False


_EMPTY_SCAN_DIRS = [
    "00-Inbox", "01-Projects", "02-Areas", "03-Resources",
    "04-Archive", "05-People", "06-Meetings", "07-Daily",
    "MOC", "Templates",
]

def check_e_empty_folder(vault: pathlib.Path) -> list[str]:
    empties = []
    for folder in _EMPTY_SCAN_DIRS:
        root = vault / folder
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            p = pathlib.Path(dirpath)
            real_files = [f for f in files if f.lower() not in _EMPTY_SKIP_FILES]
            if not real_files and not dirs:
                empties.append(str(p.relative_to(vault)))
    if not empties:
        return []
    lines = [f"RED  [E] empty_folder: {e}" for e in empties[:20]]
    if len(empties) > 20:
        lines.append(f"RED  [E] empty_folder: ... ({len(empties) - 20} more)")
    return lines


# ---------------------------------------------------------------------------
# Check F — broken_wikilink
# ---------------------------------------------------------------------------

_WIKILINK_SCAN_DIRS = [
    "00-Inbox", "01-Projects", "02-Areas", "03-Resources",
    "04-Archive", "05-People", "06-Meetings", "07-Daily",
    "MOC", "Templates",
]

def _build_stem_index(vault: pathlib.Path, folders: list[str]) -> set[str]:
    """Lowercase stems of every .md file in the vault (all folders for resolution)."""
    stems: set[str] = set()
    for dirpath, _dirs, files in os.walk(vault):
        # skip hidden dirs like .git, .obsidian
        for fname in files:
            if fname.lower().endswith(".md"):
                stems.add(pathlib.Path(fname).stem.lower())
    return stems


def check_f_broken_wikilink(vault: pathlib.Path) -> list[str]:
    stem_index = _build_stem_index(vault, _WIKILINK_SCAN_DIRS)
    broken = []
    for fpath in _iter_md(vault, _WIKILINK_SCAN_DIRS):
        # exclude any path containing /Meta/
        parts = fpath.parts
        if "Meta" in parts:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_RE.finditer(content):
            raw = m.group(1)
            target = raw.split("|")[0]   # strip alias
            target = target.split("#")[0].split("^")[0].strip()
            if not target:
                continue
            if "://" in target or target.startswith("/"):
                continue
            stem = pathlib.Path(target).stem.lower()
            if not stem:
                continue
            if stem not in stem_index:
                rel = str(fpath.relative_to(vault))
                broken.append(f"RED  [F] broken_wikilink: [[{raw}]] in {rel}")
                if len(broken) >= 20:
                    break
        if len(broken) >= 20:
            break
    return broken


# ---------------------------------------------------------------------------
# Check G — drift delta (stale reference count vs baseline)
# ---------------------------------------------------------------------------

_DRIFT_BASELINE_PATH_REL = pathlib.Path("Meta") / "sync" / "drift-baseline.json"
_DRIFT_SUMMARY_RE = re.compile(r"DRIFT:\s*(\d+)\s+high-signal")


def _get_drift_high(vault: pathlib.Path) -> int | None:
    """
    Run drift-scan.py as subprocess and parse the HIGH count from the summary line.
    Returns None if drift-scan is not available or fails.
    # ponytail: subprocess instead of import because drift-scan uses sys.exit() internally
    #           and requires REFMAP_PATH to resolve relative to its own location.
    #           Upgrade path: extract run_full_scan() into a library entry-point.
    """
    drift_script = vault / "Meta" / "sync" / "drift-scan.py"
    if not drift_script.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(drift_script)],
            capture_output=True, text=True, timeout=300,
        )
        output = result.stdout + result.stderr
        m = _DRIFT_SUMMARY_RE.search(output)
        if m:
            return int(m.group(1))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def check_g_drift_delta(vault: pathlib.Path) -> list[str]:
    baseline_path = vault / _DRIFT_BASELINE_PATH_REL
    H = _get_drift_high(vault)

    if H is None:
        return ["WARNING [G] drift_delta: drift-scan unavailable or timed out — axis skipped"]

    # Load or seed baseline
    if baseline_path.exists():
        try:
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
            B = int(baseline_data.get("high_signal_count", H))
        except (json.JSONDecodeError, OSError, ValueError):
            B = H
    else:
        B = H

    # Always write/update baseline to current value
    try:
        baseline_path.write_text(
            json.dumps({"high_signal_count": H}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # read-only mount; non-fatal

    delta = H - B
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    if H <= B:
        # GREEN — silent per vault-health-check convention
        return []

    return [
        f"WARNING [G] drift_delta: {H} high-signal stale refs (baseline {B}, delta {delta_str})"
        " — new stale refs introduced this session"
    ]


# ---------------------------------------------------------------------------
# Check H — warden validator (check-enforcement-rules.py)
# ---------------------------------------------------------------------------

def check_h_warden_validator(vault: pathlib.Path) -> list[str]:
    """
    Import check-enforcement-rules.check() and surface the missing count.
    Falls back to 'axis skipped' if the module is not present.
    # ponytail: direct import avoids subprocess + parse overhead; the module is
    #           importlib-safe (no sys.exit at import time, only in __main__).
    """
    import importlib.util
    cer_path = vault / "Meta" / "sync" / "check-enforcement-rules.py"
    if not cer_path.exists():
        return ["WARNING [H] warden_validator: check-enforcement-rules.py not found — axis skipped"]

    spec = importlib.util.spec_from_file_location("check_enforcement_rules", cer_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        return [f"WARNING [H] warden_validator: import error ({exc}) — axis skipped"]

    try:
        result = mod.check(repo_root=vault)
    except Exception as exc:
        return [f"WARNING [H] warden_validator: check() error ({exc}) — axis skipped"]

    present = result.get("present", [])
    missing = result.get("missing", [])
    N = len(present) + len(missing)
    M = len(present)
    lines = []
    if missing:
        lines.append(
            f"WARNING [H] warden_validator: {M}/{N} mechanical enforcers present"
            f" — MISSING: {', '.join(r for r, _ in missing)}"
        )
    # GREEN — silent if no missing
    return lines


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_checks(vault: pathlib.Path) -> tuple[list[str], int]:
    thresholds = load_thresholds(vault)
    results: list[str] = []
    results += check_a_inbox_backlog(vault)
    results += check_b_stray_code(vault)
    results += check_c_handoff_backlog(vault, thresholds)
    results += check_d_orphan_notes(vault)
    results += check_e_empty_folder(vault)
    results += check_f_broken_wikilink(vault)
    results += check_g_drift_delta(vault)
    results += check_h_warden_validator(vault)

    red_count = sum(1 for line in results if line.startswith("RED"))
    return results, red_count


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    """
    Build a minimal temp-dir fixture and assert the checks fire correctly.
    ponytail: assert-based, no framework.
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        vault = pathlib.Path(tmp)

        # Minimal vault skeleton
        (vault / "CLAUDE.md").write_text("# fake claude")
        (vault / "00-Inbox").mkdir()
        (vault / "01-Projects").mkdir()
        (vault / "02-Areas").mkdir()
        (vault / "03-Resources").mkdir()
        (vault / "04-Archive").mkdir()
        (vault / "05-People").mkdir()
        (vault / "06-Meetings").mkdir()
        (vault / "07-Daily").mkdir()
        (vault / "MOC").mkdir()
        (vault / "Templates").mkdir()
        (vault / "Meta" / "handoffs").mkdir(parents=True)
        (vault / "Meta" / "sync").mkdir(parents=True)

        # --- A: inbox > 10 should RED ---
        for i in range(11):
            (vault / "00-Inbox" / f"note{i}.md").write_text(f"# note {i}")
        lines_a, _ = run_checks(vault)
        assert any("[A]" in l and "RED" in l for l in lines_a), "A: expected RED for 11 inbox items"

        # clean inbox down to 0
        shutil.rmtree(vault / "00-Inbox")
        (vault / "00-Inbox").mkdir()

        # --- B: stray .py outside allow-list should RED (>= 3) ---
        stray_dir = vault / "01-Projects" / "SomeProject"
        stray_dir.mkdir(parents=True)
        for i in range(3):
            (stray_dir / f"stray{i}.py").write_text("# stray")

        # Write thresholds without allow-list so B is fail-open first
        thresholds_path = vault / "Meta" / "sync" / "vault-health-thresholds.json"
        thresholds_path.write_text('{"handoff_backlog_red": 1000}')
        lines_b_open, _ = run_checks(vault)
        assert any("WARNING" in l and "[B]" in l for l in lines_b_open), "B: expected WARNING when allow-list absent"

        # Add an allow-list that excludes SomeProject => stray should RED
        thresholds_path.write_text(json.dumps({
            "handoff_backlog_red": 1000,
            "stray_code_allow_list": ["02-Areas/Wulong/v3"]
        }))
        lines_b_red, _ = run_checks(vault)
        assert any("[B]" in l and "RED" in l for l in lines_b_red), "B: expected RED for stray .py files"

        # --- C: handoff count within threshold ---
        (vault / "Meta" / "handoffs" / "some-handoff.md").write_text("# handoff")
        lines_c, _ = run_checks(vault)
        assert not any("[C]" in l and "RED" in l for l in lines_c), "C: unexpected RED for 1 handoff"

        # --- E: empty folder should RED ---
        # 06-Meetings exists and is empty (no files except .gitkeep would count)
        # Add a real note to 01-Projects to avoid E firing there
        (vault / "01-Projects" / "SomeProject" / "index.md").write_text("# index")
        lines_e, _ = run_checks(vault)
        # 06-Meetings, 07-Daily, etc. are empty — should have at least one RED E
        assert any("[E]" in l and "RED" in l for l in lines_e), "E: expected RED for empty folder"

        # --- F: broken wikilink ---
        (vault / "01-Projects" / "SomeProject" / "linked.md").write_text(
            "See [[DoesNotExist]] for details."
        )
        lines_f, _ = run_checks(vault)
        assert any("[F]" in l and "RED" in l for l in lines_f), "F: expected RED for broken wikilink"

        # Good wikilink should NOT appear
        (vault / "01-Projects" / "SomeProject" / "target.md").write_text("# target")
        (vault / "01-Projects" / "SomeProject" / "linker.md").write_text(
            "See [[target]] here."
        )
        lines_good, _ = run_checks(vault)
        bad_linker = [l for l in lines_good if "linker" in l and "[F]" in l]
        assert not bad_linker, "F: [[target]] should resolve correctly"

    print("selftest: all assertions PASS")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)

    # Vault root: argv[1] or walk up from this file
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        vault = pathlib.Path(sys.argv[1])
    else:
        vault = find_vault_root(pathlib.Path(__file__).parent)

    lines, red_count = run_checks(vault)

    for line in lines:
        print(line)

    if red_count == 0:
        print("GREEN vault-health: all checks passed")
        sys.exit(0)
    else:
        print(f"RED vault-health: {red_count} check(s) failed")
        sys.exit(1)
