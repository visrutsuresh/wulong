#!/usr/bin/env python3
"""
metis-append-notebook.py — the ONLY sanctioned write path for Metis in OBSERVE mode.

Metis is brain-only — its agent definition excludes Write/Edit/Task tools.
It can run Bash, and Bash can run THIS script to append observations to its notebook.
This script validates input + target path, rejecting any other write attempt.

New flag (all subcommands that append content):
  --variables-touched <comma-separated names>
    Optional at call site, but Metis agent definition REQUIRES callers to pass it.
    Triggers manifest-warning for any variable not found in surface-manifest.yaml.
    At append-time: missing manifest = WARN-only (not reject).

first_spawn_iso logic:
  On the first update-cursor call where notebook frontmatter shows first_spawn_iso: null,
  this script also sets first_spawn_iso to now-ISO. This marks the start of the 30-day
  evaluation clock (Step 5h).

Usage (called by Metis via Bash):
    python3 metis-append-notebook.py append-observation \\
        --trigger <event-name> \\
        --hypothesis "<one-sentence>" \\
        --evidence "<path1>,<path2>" \\
        --confidence low|med|high \\
        --source-type <tag>  (use 'learning_output' for learning/ sources — required for Step 5h Metric B) \\
        --variables-touched "<name1>,<name2>"

    python3 metis-append-notebook.py reinforce-observation \\
        --pattern-id <slug-matching-existing-obs> \\
        --variables-touched "<name1>,<name2>"

    python3 metis-append-notebook.py update-status \\
        --pattern-id <slug> \\
        --new-status "proposal queued <id>"

    python3 metis-append-notebook.py update-cursor \\
        --last-event-id <id>

    python3 metis-append-notebook.py digest-overflow

Exit codes:
  0 = success
  1 = validation failure
  2 = filesystem error
"""
from __future__ import annotations
import argparse
import fcntl
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-untyped]

import os
_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
VAULT = Path(_WULONG_ROOT)
NOTEBOOK = VAULT / "Meta" / "metis" / "notebook.md"
NOTEBOOK_ARCHIVE = VAULT / "Meta" / "metis" / "notebook.archive.md"
CHANGE_LOG = VAULT / "Meta" / "change-log.md"
SURFACE_MANIFEST = VAULT / "Meta" / "hermes" / "surface-manifest.yaml"

ALLOWED_TARGETS = (NOTEBOOK, NOTEBOOK_ARCHIVE)


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


def _build_flat_manifest_index() -> set[str]:
    """Return flat set of ALL registered names across ALL manifest scopes.

    Includes every name stored under hermes_owns, metis_owns, and forbidden
    in every top-level scope block. Returns empty set on missing/malformed manifest.
    """
    if not SURFACE_MANIFEST.exists():
        return set()
    try:
        data = yaml.safe_load(SURFACE_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, dict):
        return set()
    index: set[str] = set()
    for scope_data in data.values():
        if not isinstance(scope_data, dict):
            continue
        for key in ("hermes_owns", "metis_owns", "forbidden"):
            for entry in scope_data.get(key, []) or []:
                if isinstance(entry, str):
                    index.add(entry)
                elif isinstance(entry, dict) and "name" in entry:
                    index.add(entry["name"])
    return index


def check_variables_touched(variables_touched: list[str]) -> None:
    """Emit WARN (not reject) for any variable not registered in surface-manifest.

    Uses a flat index of all stored names across all scopes.  Accepts a variable
    if its full stored name (dotted or bare) is present in that index; otherwise
    WARNs.  No dot-splitting — the full passed name is matched as-is.
    """
    if not variables_touched:
        return
    flat_index = _build_flat_manifest_index()
    if not flat_index:
        sys.stderr.write(
            "[metis-notebook] WARN: surface-manifest.yaml missing or empty — "
            "cannot validate --variables-touched. Observation accepted.\n"
        )
        return
    for name in variables_touched:
        if name not in flat_index:
            sys.stderr.write(
                f"[metis-notebook] WARN: variable {name!r} not registered in "
                "surface-manifest.yaml — observation accepted but cannot be promoted "
                "to PROPOSE until manifest updated. "
                "Bootstrap: post request to AR Director via Meta/agent-messages.md.\n"
            )


def _recount_observations(text: str) -> int:
    """Count the number of '## Observation ' blocks in the notebook body."""
    return len(re.findall(r"^## Observation ", text, re.MULTILINE))


def _rewrite_observation_count(count: int) -> None:
    """Read notebook, rewrite frontmatter observation_count to match live count."""
    text = NOTEBOOK.read_text(encoding="utf-8")
    text = re.sub(
        r"^observation_count:\s*\d+",
        f"observation_count: {count}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    NOTEBOOK.write_text(text, encoding="utf-8")


def format_variables_block(variables_touched: list[str]) -> str:
    if variables_touched:
        return f"Variables touched: {', '.join(variables_touched)}\n"
    return "Variables touched: (not specified)\n"


def append_observation(
    trigger: str,
    hypothesis: str,
    evidence: list[str],
    confidence: str,
    source_type: str,
    variables_touched: list[str],
) -> int:
    if confidence not in ("low", "med", "high"):
        sys.stderr.write(f"[metis-notebook] invalid confidence: {confidence}\n")
        return 1
    if len(hypothesis) < 10 or len(hypothesis) > 300:
        sys.stderr.write("[metis-notebook] hypothesis must be 10-300 chars\n")
        return 1

    check_variables_touched(variables_touched)

    pattern_id = slugify(hypothesis)
    block = (
        f"\n## Observation {now_str()} — trigger: {trigger}\n"
        f"Pattern id: {pattern_id}\n"
        f"Pattern hypothesis: {hypothesis}\n"
        f"Evidence cited: {evidence}\n"
        f"Confidence: {confidence}\n"
        f"Source type: {source_type or 'unspecified'}\n"
        f"Times observed: 1\n"
        f"First observed: {now_str()}\n"
        f"Last observed: {now_str()}\n"
        f"Status: still observing\n"
        + format_variables_block(variables_touched)
    )
    with open(NOTEBOOK, "a", encoding="utf-8") as f:
        f.write(block)
    # Recount from the live body — observation_count must equal block count
    text_after = NOTEBOOK.read_text(encoding="utf-8")
    _rewrite_observation_count(_recount_observations(text_after))
    log_event(f"append-observation: {pattern_id}")
    return 0


def _read_block_status(text: str, pattern_id: str) -> str:
    """Return the Status: field value for the given pattern_id block, or '' if not found.

    Status is the last structured field in each block. Match as a line-start prefix
    so 'accepted-by-design (ADR-006 ...)' and 'settled — ...' are caught correctly.
    """
    block_pat = re.compile(
        rf"Pattern id: {re.escape(pattern_id)}(?![\w-])(.*?)(?=\n## Observation |\Z)",
        re.DOTALL,
    )
    bm = block_pat.search(text)
    if not bm:
        return ""
    status_pat = re.compile(r"^Status:\s*(.+)", re.MULTILINE)
    matches = status_pat.findall(bm.group(1))
    return matches[-1].strip() if matches else ""


def reinforce_observation(
    pattern_id: str,
    variables_touched: list[str],
    evidence: list[str] | None = None,
    note: str = "",
) -> int:
    check_variables_touched(variables_touched)

    text = NOTEBOOK.read_text(encoding="utf-8")

    pat = re.compile(
        rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Times observed:\s*)(\d+)(.*?Last observed:\s*)([^\n]+)",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        sys.stderr.write(f"[metis-notebook] pattern_id not found: {pattern_id}\n")
        return 1

    # ponytail: one-way terminal latch — no count increment for settled/accepted-by-design
    status = _read_block_status(text, pattern_id)
    is_terminal = status.startswith("accepted-by-design") or status.startswith("settled")

    if is_terminal:
        # Annotation only — audit trail preserved, count frozen
        new_last = now_str()
        audit_note = note or "(terminal — count frozen)"
        audit_note = audit_note[:200]
        note_line = f"Reinforcement note ({new_last}): {audit_note}"
        lo_pat = re.compile(
            rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Last observed:\s*[^\n]+)(\n)",
            re.DOTALL,
        )
        new_text = lo_pat.sub(lambda mm: f"{mm.group(1)}{mm.group(2)}{note_line}\n", text, count=1)
        NOTEBOOK.write_text(new_text, encoding="utf-8")
        log_event(f"reinforce-observation: {pattern_id} → TERMINAL ({status[:30]}) count frozen")
        return 0

    new_count = int(m.group(2)) + 1
    new_last = now_str()
    new_text = pat.sub(
        lambda mm: f"{mm.group(1)}{new_count}{mm.group(3)}{new_last}", text
    )

    if evidence:
        ev_pat = re.compile(
            rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Evidence cited:\s*)([^\n]+)",
            re.DOTALL,
        )
        ev_m = ev_pat.search(new_text)
        if ev_m:
            try:
                existing = eval(ev_m.group(2).strip())  # noqa: S307
                if not isinstance(existing, list):
                    existing = [ev_m.group(2).strip()]
            except Exception:
                existing = [ev_m.group(2).strip()]
            merged = existing + [e for e in evidence if e not in existing]
            new_text = ev_pat.sub(lambda mm: f"{mm.group(1)}{merged!r}", new_text)

    if note:
        note = note[:200]
        note_line = f"Reinforcement note ({new_last}): {note}"
        # Insert after the Last observed line for this observation block
        lo_pat = re.compile(
            rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Last observed:\s*[^\n]+)(\n)",
            re.DOTALL,
        )
        new_text = lo_pat.sub(lambda mm: f"{mm.group(1)}{mm.group(2)}{note_line}\n", new_text, count=1)

    NOTEBOOK.write_text(new_text, encoding="utf-8")
    log_event(f"reinforce-observation: {pattern_id} → count={new_count}")
    return 0


def update_status(pattern_id: str, new_status: str) -> int:
    text = NOTEBOOK.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(Pattern id: {re.escape(pattern_id)}(?![\w-]).*?Status:\s*)([^\n]+)", re.DOTALL
    )
    if not pat.search(text):
        sys.stderr.write(f"[metis-notebook] pattern_id not found: {pattern_id}\n")
        return 1
    new_text = pat.sub(lambda mm: f"{mm.group(1)}{new_status}", text)
    NOTEBOOK.write_text(new_text, encoding="utf-8")
    log_event(f"update-status: {pattern_id} → {new_status}")
    return 0


def update_cursor(last_event_id: str) -> int:
    """Update notebook frontmatter last_event_id (and first_spawn_iso on first call).

    On the first call where first_spawn_iso is null, also sets first_spawn_iso to now-ISO.
    This starts the 30-day evaluation clock per Step 5h.
    """
    if not last_event_id or len(last_event_id) > 200:
        sys.stderr.write("[metis-notebook] invalid last_event_id\n")
        return 1

    text = NOTEBOOK.read_text(encoding="utf-8")

    # Update last_event_id
    text = re.sub(
        r"^last_event_id:\s*\S+",
        f"last_event_id: {last_event_id}",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    # Update last_invocation
    text = re.sub(
        r"^last_invocation:\s*\S+",
        f"last_invocation: {now_str()}",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    # Populate first_spawn_iso on first call (when it is null)
    first_spawn_match = re.search(r"^first_spawn_iso:\s*(\S+)", text, flags=re.MULTILINE)
    if first_spawn_match and first_spawn_match.group(1) == "null":
        iso = now_iso()
        text = re.sub(
            r"^first_spawn_iso:\s*null",
            f"first_spawn_iso: {iso}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        log_event(f"first-spawn: first_spawn_iso set to {iso}")

    NOTEBOOK.write_text(text, encoding="utf-8")
    log_event(f"update-cursor: last_event_id={last_event_id}")
    return 0


def digest_overflow() -> int:
    text = NOTEBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"(## Observation [\s\S]*?)(?=## Observation|\Z)", text)
    if len(blocks) <= 50:
        return 0
    to_archive = blocks[:50]
    NOTEBOOK_ARCHIVE.touch(exist_ok=True)
    with open(NOTEBOOK_ARCHIVE, "a", encoding="utf-8") as f:
        f.write(f"\n<!-- digest from {now_str()} — 50 oldest observations archived -->\n")
        for b in to_archive:
            f.write(b.rstrip() + "\n\n")
    header_match = re.search(
        r"^---[\s\S]*?---\s*<!--[\s\S]*?-->\s*## Observations\s*", text
    )
    header = header_match.group(0) if header_match else text.split("## Observation")[0]
    remaining = "## Observation" + "".join(blocks[50:]).split("## Observation", 1)[-1]
    digest = (
        f"\n## Digest {now_str()} (50 archived → notebook.archive.md)\n"
        f"50 oldest observations folded into archive; review notebook.archive.md if needed.\n"
    )
    new_body = header + digest + remaining
    NOTEBOOK.write_text(new_body, encoding="utf-8")
    # Recount after archival — count drops by however many blocks were archived
    _rewrite_observation_count(_recount_observations(new_body))
    log_event("digest-overflow: archived 50")
    return 0


def log_event(msg: str) -> None:
    try:
        with open(CHANGE_LOG, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(f"[{now_str()}] metis → NOTEBOOK {msg}\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("append-observation")
    p1.add_argument("--trigger", required=True)
    p1.add_argument("--hypothesis", required=True)
    p1.add_argument("--evidence", default="")
    p1.add_argument("--confidence", choices=["low", "med", "high"], required=True)
    p1.add_argument(
        "--source-type",
        default="",
        help="Tag the observation source (use 'learning_output' for learning/ outputs — required for Step 5h Metric B).",
    )
    p1.add_argument(
        "--variables-touched",
        default="",
        help="Comma-separated variable names this observation covers.",
    )

    p2 = sub.add_parser("reinforce-observation")
    p2.add_argument("--pattern-id", required=True)
    p2.add_argument("--variables-touched", default="")
    p2.add_argument(
        "--evidence",
        default="",
        help="Comma-separated path(s) to append to the observation's Evidence cited list.",
    )
    p2.add_argument(
        "--note",
        default="",
        help="Short reinforcement note (≤200 chars) appended after Last observed.",
    )

    p3 = sub.add_parser("update-status")
    p3.add_argument("--pattern-id", required=True)
    p3.add_argument("--new-status", required=True)

    p4 = sub.add_parser("update-cursor")
    p4.add_argument("--last-event-id", required=True)

    sub.add_parser("digest-overflow")

    args = ap.parse_args()

    def parse_vars(raw: str) -> list[str]:
        return [v.strip() for v in raw.split(",") if v.strip()]

    if args.cmd == "append-observation":
        evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
        return append_observation(
            args.trigger,
            args.hypothesis,
            evidence,
            args.confidence,
            args.source_type,
            parse_vars(args.variables_touched),
        )
    if args.cmd == "reinforce-observation":
        evidence = [e.strip() for e in args.evidence.split(",") if e.strip()]
        return reinforce_observation(
            args.pattern_id,
            parse_vars(args.variables_touched),
            evidence=evidence,
            note=args.note,
        )
    if args.cmd == "update-status":
        return update_status(args.pattern_id, args.new_status)
    if args.cmd == "update-cursor":
        return update_cursor(args.last_event_id)
    if args.cmd == "digest-overflow":
        return digest_overflow()
    return 1


if __name__ == "__main__":
    import os
    if os.environ.get("METIS_SELFTEST") == "1":
        # Self-test for the flat-index resolver in check_variables_touched().
        # Calls the REAL check_variables_touched via module-level monkey-patch of
        # _build_flat_manifest_index to inject a fixture that mirrors the REAL
        # manifest shape: top-level scope keys holding FULL stored names (dotted or bare).
        # Success bar: registered vars resolve (no WARN); invented vars still WARN.
        import io
        import sys as _sys
        from contextlib import redirect_stderr

        # Fixture mirrors the REAL surface-manifest.yaml shape:
        # - "agents" scope stores full dotted names: "jarvis.teach_mode_pedagogy"
        # - a project scope stores bare names: "MY_VAR"
        FIXTURE_FLAT: set[str] = {
            "jarvis.teach_mode_pedagogy",
            "active.R2_scope",
            "EDGE_THRESHOLD",
            "MIN_EDGE",
            "ATR_SKIP_PCT",
        }

        def _run_real_check(variables: list[str]) -> str:
            """Patch _build_flat_manifest_index to return FIXTURE_FLAT, then call the
            REAL check_variables_touched; capture and return stderr output."""
            mod = _sys.modules[__name__]
            original = mod._build_flat_manifest_index  # type: ignore[attr-defined]
            mod._build_flat_manifest_index = lambda: FIXTURE_FLAT  # type: ignore[attr-defined]
            buf = io.StringIO()
            try:
                with redirect_stderr(buf):
                    check_variables_touched(variables)
            finally:
                mod._build_flat_manifest_index = original  # type: ignore[attr-defined]
            return buf.getvalue()

        failures: list[str] = []

        # (a) registered full-dotted var (agents scope, full name "jarvis.teach_mode_pedagogy")
        #     must NOT warn — this is the exact case the old split-resolver broke
        out = _run_real_check(["jarvis.teach_mode_pedagogy"])
        if out.strip():
            failures.append(f"(a) FAIL — expected no WARN, got: {out!r}")
        else:
            print("(a) PASS: registered full-dotted var (jarvis.teach_mode_pedagogy) resolves without WARN")

        # (b) registered bare var (no dot, e.g. MY_VAR from a project scope)
        #     must NOT warn — old early-return `if "." not in name: WARN` would fail this
        out = _run_real_check(["EDGE_THRESHOLD"])
        if out.strip():
            failures.append(f"(b) FAIL — expected no WARN for bare registered var, got: {out!r}")
        else:
            print("(b) PASS: registered bare var (EDGE_THRESHOLD) resolves without WARN")

        # (c) invented var: MUST still warn
        out = _run_real_check(["invented.nonexistent_knob"])
        if "WARN" not in out:
            failures.append(f"(c) FAIL — expected WARN for invented var, got: {out!r}")
        else:
            print("(c) PASS: invented var (invented.nonexistent_knob) still WARNs")

        if failures:
            for f in failures:
                print(f)
            print(f"\nSELFTEST FAILED: {len(failures)} failure(s)")
            _sys.exit(1)
        print("\nSELFTEST: all 3 cases PASS")
        _sys.exit(0)

    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[metis-notebook] FATAL: {e}\n")
        sys.exit(2)
