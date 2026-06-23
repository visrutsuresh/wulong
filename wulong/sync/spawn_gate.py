#!/usr/bin/env python3
"""
spawn_gate.py — Spawn-gate wrapper module for ADR-007 inheritable gates.

A thin caller that every spawning agent (Jarvis or any piloted coordinator)
MUST consult before spawning a gated worker. Returns ALLOW immediately for
non-gated workers (short-circuit). Returns REFUSE when the gate precondition
is not satisfied.

Gated workers (require an active change_id + a satisfied precondition):
  coder     → gate nn3 (contrarian plan-PASS required)

Non-gated workers (always ALLOW, change_id optional):
  deployer  — NN#4 (tester-after-deploy) is a CLOSE/sequencing gate, not a
              spawn precondition. It is enforced at deploy-CLOSE by
              validate-receipt-graph.py _check_nn4 + verify-change.py D6,
              both of which block (exit 1, NN4_VIOLATION) when a deployer
              receipt lacks a forward tester-DONE. Spawning deployer before
              tester has run is correct: you must deploy before tester can test.
  All other agents: scribe, sorter, seeker, connector, librarian,
  transcriber, postman, analyst, researcher, writer, mastermind, etc.

Usage (Python):
  from spawn_gate import authorize_spawn, SpawnDecision
  decision = authorize_spawn("coder", change_id="adr-007-inheritable-gates-keepers-pilot")
  if not decision.allowed:
      raise RuntimeError(f"Spawn refused: {decision.reason}")

Usage (CLI):
  python3 spawn_gate.py --worker coder --change-id X
  python3 spawn_gate.py --worker scribe   # always ALLOW

Exit codes:
  0 — ALLOW
  1 — REFUSE
  2 — usage error

change_id: adr-007-inheritable-gates-keepers-pilot
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Optional

# The pre-spawn existence oracle (Artifact 1)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
from check_gate_precondition import check_gate_precondition, GateResult, _DEFAULT_RECEIPTS
from agent_identity import resolve_to_machine_id, FALLBACK_GATED_SLUGS

# bus.py lives at <vault>/Meta/agent-bus/bus.py;
# _THIS_DIR = <vault>/Meta/sync/, so vault = dirname(dirname(_THIS_DIR))
_VAULT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
_BUS_PY = os.path.join(_VAULT_ROOT, "Meta", "agent-bus", "bus.py")
# ponytail: degrade gracefully when bus is not installed (fresh wulong init user).
# Fail-closed behavior is PRESERVED when bus IS present. Only the no-bus path is new.
_BUS_CONFIGURED = os.path.isfile(_BUS_PY)


def _claim_bus_slot(
    change_id: str,
    worker: str,
    spawned_by: str = "spawn_gate",
) -> tuple[bool, Optional[str], str]:
    """Attempt to claim a live-worker slot via the agent bus.

    Returns (allowed: bool, slot_id: str | None, reason: str).
    Fail-closed: any error claiming the slot returns allowed=False so a broken
    bus cannot silently allow an uncounted spawn.
    When bus is not installed: allow-no-slot (fresh wulong init user, no ceiling).
    """
    if not _BUS_CONFIGURED:
        print("[spawn_gate] bus not configured — slot ceiling skipped (no agent-bus)", file=sys.stderr)
        return True, None, "no-bus: slot ceiling not enforced"
    try:
        result = subprocess.run(
            [
                sys.executable, _BUS_PY,
                "claim-slot",
                "--change-id", change_id,
                "--worker", worker,
                "--spawned-by", spawned_by,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(result.stdout)
        if data.get("result") == "ALLOW":
            return True, data.get("slot_id"), ""
        reason = data.get("reason", "ceiling hit (unknown reason)")
        return False, None, f"slot claim refused: {reason}"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"slot claim failed (fail-closed): {exc}"


def _issue_gate_token(
    worker: str,
    change_id: str,
    slot_id: Optional[str],
) -> None:
    """Issue a one-time gate-clearance token into the bus gate_tokens table.

    Best-effort: a failure here does NOT block the ALLOW decision — the token
    is an enforcement aid for the runtime hook, not a gate itself. Failure is
    logged to stderr so it's visible but doesn't propagate.

    Only called from the ALLOW-for-gated path in authorize_spawn().
    """
    try:
        cmd = [
            sys.executable, _BUS_PY,
            "issue-token",
            "--worker", worker,
            "--change-id", change_id,
        ]
        if slot_id:
            cmd += ["--slot-id", slot_id]
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        # ponytail: best-effort; TTL will expire any missing token gracefully
        print(f"[spawn_gate] warn: issue-token failed (non-fatal): {exc}", file=sys.stderr)


def _check_bus_halt(scope: str = "global") -> tuple[bool, str]:
    """Query the agent bus for a halt flag.

    Returns (halted: bool, reason: str).
    Fail-closed: any error reading the bus is treated as halted=True so a
    broken bus cannot silently allow spawns.
    When bus is not installed: not-halted (fresh wulong init user, no halt).
    """
    if not _BUS_CONFIGURED:
        print(f"[spawn_gate] bus not configured — halt check skipped for scope={scope}", file=sys.stderr)
        return False, "no-bus: halt check not enforced"
    try:
        result = subprocess.run(
            [sys.executable, _BUS_PY, "check-halt", "--scope", scope],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return True, f"bus check-halt exited {result.returncode}: {result.stderr.strip()}"
        data = json.loads(result.stdout)
        if data.get("active", 0) == 1:
            reason = data.get("reason") or "(no reason)"
            return True, f"bus halt active for scope={scope}: {reason}"
        return False, ""
    except Exception as exc:  # noqa: BLE001
        return True, f"bus check-halt failed (fail-closed): {exc}"

# ---------------------------------------------------------------------------
# Worker → gate mapping
# ---------------------------------------------------------------------------

# Workers that require a gate check before spawn.
# Key: canonical machine_id (lowercase, matches agent: field in receipts).
# Value: gate identifier for check_gate_precondition().
# ponytail: deployer removed — NN#4 is a CLOSE gate (validate-receipt-graph _check_nn4
# + verify-change D6), not a spawn precondition. Upgrade path: add deployer back here
# only if a spawn-time contrarian-style precondition is formally decided.
_GATED_WORKERS: dict[str, str] = {
    "coder": "nn3",
}


def _resolve_worker(raw: str) -> str:
    """Translate a spawn token (slug, machine_id, or persona) to canonical machine_id.

    Falls back gracefully: if aliases.json is missing/malformed, returns the
    normalized input unchanged. The _GATED_WORKERS check that follows is
    fail-closed for any FALLBACK_GATED_SLUGS token even without the file.
    """
    try:
        return resolve_to_machine_id(raw)
    except RuntimeError:
        # Broken bridge: pass through so _GATED_WORKERS + FALLBACK_GATED_SLUGS
        # still gate the known slugs.
        return raw


# ---------------------------------------------------------------------------
# Spawn decision type
# ---------------------------------------------------------------------------

class SpawnDecision:
    """Result of a spawn authorization check."""

    __slots__ = ("verdict", "reason", "worker", "change_id", "gate", "matching_receipt", "slot_id")

    def __init__(
        self,
        verdict: str,
        reason: str,
        worker: str,
        change_id: Optional[str],
        gate: Optional[str],
        matching_receipt: Optional[str] = None,
        slot_id: Optional[str] = None,
    ) -> None:
        self.verdict = verdict          # "ALLOW" or "REFUSE"
        self.reason = reason
        self.worker = worker
        self.change_id = change_id
        self.gate = gate                # None for non-gated workers
        self.matching_receipt = matching_receipt
        # slot_id is set on ALLOW; the orchestrator must call
        # `bus.py release-slot --slot-id <slot_id>` after the worker returns.
        # None when --skip-slot-check is used or when the spawn is refused.
        self.slot_id = slot_id

    def __str__(self) -> str:
        base = f"[{self.verdict}] worker={self.worker}"
        if self.gate:
            base += f" gate={self.gate}"
        if self.change_id:
            base += f" change_id={self.change_id}"
        base += f" — {self.reason}"
        if self.matching_receipt:
            base += f" (matched: {self.matching_receipt})"
        if self.slot_id:
            base += f" slot_id={self.slot_id}"
        return base

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def authorize_spawn(
    worker: str,
    change_id: Optional[str] = None,
    receipts_dir: Optional[str] = None,
    skip_halt_check: bool = False,
    skip_slot_check: bool = False,
    spawned_by: str = "spawn_gate",
) -> SpawnDecision:
    """Authorize or refuse a worker spawn.

    Parameters
    ----------
    worker:
        The agent to spawn (e.g. "coder", "scribe"). Case-insensitive.
    change_id:
        The change identifier for this work. Required for gated workers.
        Ignored (and logged) for non-gated workers.
    receipts_dir:
        Override the receipts directory path. Defaults to Meta/receipts/.
        Inject a tempdir in tests.
    skip_halt_check:
        Skip the bus halt check (for unit tests that stub the bus).
    skip_slot_check:
        Skip the in_flight_slots ceiling check (for unit tests that stub the bus).
        When skipped, decision.slot_id is None (no slot claimed).
    spawned_by:
        Identity of the caller for slot bookkeeping (default: "spawn_gate").

    Returns
    -------
    SpawnDecision with verdict ALLOW or REFUSE.

    On ALLOW, decision.slot_id is the claimed slot identifier (or None if
    --skip-slot-check was used). The CALLER (orchestrator) is responsible for
    calling `bus.py release-slot --slot-id <slot_id>` after the worker returns.
    A crashed worker's slot self-expires after SLOT_LEASE_MINUTES and is
    reclaimed automatically on the next claim-slot call.

    The function NEVER raises — all error paths return a REFUSE decision.
    """
    if not worker or not worker.strip():
        return SpawnDecision(
            verdict="REFUSE",
            reason="worker name is empty — cannot authorize spawn",
            worker=worker or "",
            change_id=change_id,
            gate=None,
        )

    # Translate persona slug / machine_id / display name to canonical machine_id.
    # BINDING CONSTRAINT 1: if aliases.json fails, FALLBACK_GATED_SLUGS keeps
    # head-forger gated so a broken bridge cannot silently open the gate.
    _normalized = worker.strip().lower()
    worker_key = _resolve_worker(_normalized)
    # Enforce fallback gated set when resolver cannot confirm identity:
    # if the raw token is in FALLBACK_GATED_SLUGS but resolved to itself (broken
    # bridge returned it unchanged), mark it gated by replacing it with its
    # canonical machine_id via hard-coded map.
    _FALLBACK_SLUG_TO_MACHINE: dict[str, str] = {"head-forger": "coder"}
    if worker_key not in _GATED_WORKERS and _normalized in FALLBACK_GATED_SLUGS:
        worker_key = _FALLBACK_SLUG_TO_MACHINE.get(_normalized, worker_key)

    # Bus halt check: consulted before ANY spawn (gated or non-gated).
    # Fail-closed: if bus read errors, REFUSE so a broken bus cannot allow spawns.
    if not skip_halt_check:
        halted, halt_reason = _check_bus_halt("global")
        if halted:
            return SpawnDecision(
                verdict="REFUSE",
                reason=f"REFUSED: global halt active — {halt_reason}",
                worker=worker_key,
                change_id=change_id,
                gate=None,
            )

    # Slot check: claim a live-worker slot against the global ceiling (ADR §3.4 R1).
    # Fail-closed: if claim fails or ceiling hit, REFUSE.
    slot_id: Optional[str] = None
    if not skip_slot_check:
        cid = (change_id or "no-change-id").strip()
        slot_allowed, slot_id, slot_reason = _claim_bus_slot(cid, worker_key, spawned_by)
        if not slot_allowed:
            return SpawnDecision(
                verdict="REFUSE",
                reason=f"REFUSED: {slot_reason}",
                worker=worker_key,
                change_id=change_id,
                gate=None,
            )

    # Non-gated worker: short-circuit ALLOW immediately.
    if worker_key not in _GATED_WORKERS:
        return SpawnDecision(
            verdict="ALLOW",
            reason=f"'{worker_key}' is not a gated worker — no gate check required",
            worker=worker_key,
            change_id=change_id,
            gate=None,
            slot_id=slot_id,
        )

    gate = _GATED_WORKERS[worker_key]

    # Gated worker: change_id is required.
    if not change_id or not change_id.strip():
        return SpawnDecision(
            verdict="REFUSE",
            reason=(
                f"'{worker_key}' is a gated worker (gate={gate}) but no change_id "
                f"was provided — cannot check precondition"
            ),
            worker=worker_key,
            change_id=change_id,
            gate=gate,
        )

    # Delegate to the existence oracle.
    result: GateResult = check_gate_precondition(
        change_id=change_id.strip(),
        gate=gate,
        receipts_dir=receipts_dir,
    )

    # If the gate rejects, release any slot we just claimed so it isn't stranded.
    if result.verdict != "ALLOW" and slot_id and not skip_slot_check:
        try:
            subprocess.run(
                [sys.executable, _BUS_PY, "release-slot", "--slot-id", slot_id],
                capture_output=True,
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            pass  # ponytail: best-effort release; TTL will reclaim on next claim

    effective_slot_id = slot_id if result.verdict == "ALLOW" else None

    # ALLOW-for-gated: mint a one-time gate token so the PreToolUse hook can
    # verify at spawn time that spawn_gate was actually called.
    # Non-gated ALLOWs (short-circuited above) NEVER reach this path.
    if result.verdict == "ALLOW":
        _issue_gate_token(worker_key, change_id.strip(), effective_slot_id)

    return SpawnDecision(
        verdict=result.verdict,
        reason=result.reason,
        worker=worker_key,
        change_id=change_id.strip(),
        gate=gate,
        matching_receipt=result.matching_receipt,
        slot_id=effective_slot_id,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Spawn-gate wrapper — authorize or refuse a worker spawn based on "
            "ADR-007 inheritable gate preconditions."
        )
    )
    p.add_argument(
        "--worker",
        required=True,
        metavar="AGENT",
        help="Agent to spawn (e.g. coder, deployer, scribe). Non-gated agents always ALLOW.",
    )
    p.add_argument(
        "--change-id",
        default=None,
        metavar="X",
        help="change_id for the work (required for gated workers — currently only coder).",
    )
    p.add_argument(
        "--receipts-dir",
        default=None,
        metavar="PATH",
        help="Override receipts directory path (default: Meta/receipts/ in vault).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout (exit code still indicates result).",
    )
    p.add_argument(
        "--skip-halt-check",
        action="store_true",
        dest="skip_halt_check",
        help="Skip bus halt check (for testing only).",
    )
    p.add_argument(
        "--skip-slot-check",
        action="store_true",
        dest="skip_slot_check",
        help="Skip in_flight_slots ceiling check (for testing only).",
    )
    p.add_argument(
        "--spawned-by",
        default="spawn_gate",
        dest="spawned_by",
        help="Caller identity for slot bookkeeping (default: spawn_gate).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    decision = authorize_spawn(
        worker=args.worker,
        change_id=args.change_id,
        receipts_dir=args.receipts_dir,
        skip_halt_check=getattr(args, "skip_halt_check", False),
        skip_slot_check=getattr(args, "skip_slot_check", False),
        spawned_by=getattr(args, "spawned_by", "spawn_gate"),
    )
    if not args.quiet:
        print(decision)
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
