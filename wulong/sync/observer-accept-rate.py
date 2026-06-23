#!/usr/bin/env python3
"""
observer-accept-rate.py — compute and report observer proposal accept rates.

SOURCE OF TRUTH: Meta/observer-proposals/ledger.jsonl (written by
observer-disposition.py). Disposition counts and accept rates are derived from
the ledger's `kind: proposal` rows (latest row per observer+proposal_id wins).

Folder counts under Meta/hermes-proposals/{queued,rejected,archive,deferred}
and Meta/metis-proposals/{queued,approved,rejected,deferred} are reported as a
DERIVED/RECONCILED view only — the dispositioner moves files on verdict, so any
divergence between ledger and folders is flagged, never silently resolved.

Backward-safe: if the ledger is missing or empty, the report honestly shows
"0 dispositioned" (the open-loop indicator) and never crashes or fabricates a
rate from zero evidence.

Writes to Meta/doctor/observer-accept-rate.md.
Always exits 0.

Pure Python / stdlib only. No LLM, no network, no spend.

Usage:
  python3 Meta/sync/observer-accept-rate.py
  python3 Meta/sync/observer-accept-rate.py --verbose
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent.parent
DOCTOR = VAULT / "Meta" / "doctor"
LEDGER = VAULT / "Meta" / "observer-proposals" / "ledger.jsonl"
HERMES_BASE = VAULT / "Meta" / "hermes-proposals"
METIS_BASE = VAULT / "Meta" / "metis-proposals"

# Verdict → folder name per observer vocabulary (mirrors observer-disposition.py)
FOLDER_MAP = {
    "hermes": {"accepted": "archive", "rejected": "rejected", "deferred": "deferred"},
    "metis": {"accepted": "approved", "rejected": "rejected", "deferred": "deferred"},
}


def _count_files(directory: Path) -> int:
    """Count non-hidden, non-README files (proposals) in a directory."""
    if not directory.exists():
        return 0
    return sum(
        1 for p in directory.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name.lower() != "readme.md"
    )


def _load_ledger_dispositions() -> dict[str, dict[str, str]]:
    """
    Returns {observer: {proposal_id: final_status}} from kind=proposal ledger rows.
    Latest row per (observer, proposal_id) wins. Missing/empty/malformed-line
    ledger → empty dict (backward-safe).
    """
    result: dict[str, dict[str, str]] = {"hermes": {}, "metis": {}}
    if not LEDGER.exists():
        return result
    try:
        lines = LEDGER.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") != "proposal":
            continue
        observer = row.get("observer", "")
        pid = row.get("proposal_id", "")
        status = row.get("status", "")
        if observer in result and pid and status:
            result[observer][pid] = status
    return result


def _compute_observer(observer: str, base: Path, ledger: dict[str, str]) -> dict:
    accepted = sum(1 for s in ledger.values() if s == "accepted")
    rejected = sum(1 for s in ledger.values() if s == "rejected")
    deferred = sum(1 for s in ledger.values() if s == "deferred")
    dispositioned = accepted + rejected  # deferred = parked, not a final verdict

    if dispositioned == 0:
        accept_rate_str = "N/A (0 dispositioned in ledger)"
    else:
        rate = accepted / dispositioned
        accept_rate_str = f"{rate:.0%} ({accepted} accepted / {dispositioned} dispositioned)"

    fmap = FOLDER_MAP[observer]
    folders = {
        "queued": _count_files(base / "queued"),
        fmap["accepted"]: _count_files(base / fmap["accepted"]),
        "rejected": _count_files(base / "rejected"),
        "deferred": _count_files(base / "deferred"),
    }

    # Reconciliation: ledger verdict counts vs folder file counts
    diverged = []
    if folders[fmap["accepted"]] != accepted:
        diverged.append(f"{fmap['accepted']}/ has {folders[fmap['accepted']]} files vs {accepted} accepted in ledger")
    if folders["rejected"] != rejected:
        diverged.append(f"rejected/ has {folders['rejected']} files vs {rejected} rejected in ledger")
    if folders["deferred"] != deferred:
        diverged.append(f"deferred/ has {folders['deferred']} files vs {deferred} deferred in ledger")

    return {
        "accepted": accepted,
        "rejected": rejected,
        "deferred": deferred,
        "dispositioned": dispositioned,
        "accept_rate_str": accept_rate_str,
        "open_loop": dispositioned == 0,
        "folders": folders,
        "diverged": diverged,
    }


def _write_report(hermes: dict, metis: dict, ledger_exists: bool) -> Path:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# Observer Proposal Accept Rate",
        "",
        f"Generated: {ts}",
        "",
        f"Source of truth: `Meta/observer-proposals/ledger.jsonl`"
        + ("" if ledger_exists else " **(not found — all counts 0)**"),
        "",
        "---",
        "",
    ]

    for name, data in (("Hermes", hermes), ("Metis", metis)):
        lines += [
            f"## {name} Proposals (ledger)",
            "",
            f"- Accepted: {data['accepted']}",
            f"- Rejected: {data['rejected']}",
            f"- Deferred (parked, not final): {data['deferred']}",
            f"- Dispositioned total (accepted+rejected): {data['dispositioned']}",
            f"- Accept rate: {data['accept_rate_str']}",
            "",
            f"Folder view (derived): "
            + ", ".join(f"{k}={v}" for k, v in data["folders"].items()),
        ]
        if data["diverged"]:
            lines.append("")
            lines.append("> **RECONCILE WARNING — folder state diverges from ledger:**")
            for d in data["diverged"]:
                lines.append(f"> - {d}")
        if data["open_loop"]:
            lines.append(
                f"\n> OPEN-LOOP INDICATOR: {data['folders']['queued']} queued, "
                "0 dispositioned in ledger. No proposal has been accepted or rejected yet."
            )
        lines.append("")

    lines += [
        "---",
        "",
        "## Summary",
        "",
        "| Observer | Queued (folder) | Accepted | Rejected | Deferred | Accept rate |",
        "|---|---|---|---|---|---|",
        (
            f"| hermes | {hermes['folders']['queued']} | {hermes['accepted']} | "
            f"{hermes['rejected']} | {hermes['deferred']} | {hermes['accept_rate_str']} |"
        ),
        (
            f"| metis | {metis['folders']['queued']} | {metis['accepted']} | "
            f"{metis['rejected']} | {metis['deferred']} | {metis['accept_rate_str']} |"
        ),
        "",
        (
            "*Accept rate = accepted / (accepted + rejected), from ledger rows only. "
            "'N/A' when no proposal has been dispositioned (open loop). "
            "Disposition is recorded via `Meta/sync/observer-disposition.py` "
            "(human-in-the-loop — CEO/contrarian, never the observer itself).*"
        ),
    ]

    DOCTOR.mkdir(parents=True, exist_ok=True)
    out_path = DOCTOR / "observer-accept-rate.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    dispositions = _load_ledger_dispositions()
    hermes = _compute_observer("hermes", HERMES_BASE, dispositions["hermes"])
    metis = _compute_observer("metis", METIS_BASE, dispositions["metis"])

    if verbose:
        print(f"Ledger exists: {LEDGER.exists()}")
        print(f"Hermes: accepted={hermes['accepted']} rejected={hermes['rejected']} "
              f"deferred={hermes['deferred']} folders={hermes['folders']}")
        print(f"Metis:  accepted={metis['accepted']} rejected={metis['rejected']} "
              f"deferred={metis['deferred']} folders={metis['folders']}")

    out_path = _write_report(hermes, metis, LEDGER.exists())

    if verbose:
        print(f"Report written: {out_path}")

    return 0  # always exits 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"[observer-accept-rate] unhandled error:\n{traceback.format_exc()}\n")
        sys.exit(0)
