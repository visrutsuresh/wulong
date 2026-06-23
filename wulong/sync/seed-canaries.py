#!/usr/bin/env python3
"""
seed-canaries.py — Corpus-Usage Verification: plant + manage per-agent corpus canaries.

WHY THIS EXISTS
---------------
Agent corpus (Meta/knowledge-base/<agent>.md + Meta/context/<agent>.md) is compiled
fresh and injected at spawn, and every agent definition MANDATES reading it. But there
was NO automated proof an agent actually READ its corpus on a given task. This tool
plants a unique, harmless SENTINEL token ("canary") inside each agent's KB that the
agent could ONLY know by reading its own corpus. A separate probe (see the probe
playbook) asks an agent for its canary and verifies the returned token matches the
planted one -> proves a live read.

DESIGN
------
- Single source of truth = Meta/canaries/registry.json (agent -> token + metadata).
- Each token is also planted as ONE low-salience block at the END of the agent's KB
  (Meta/knowledge-base/<agent>.md), inside HTML-comment delimiters so it is easy to
  find/replace idempotently and is visually quiet.
- The planted block instructs the agent to emit the token ONLY when explicitly probed
  ("if asked for your corpus-canary, return THIS token") and NEVER in normal output —
  so canaries never pollute real deliverables or leak into user-facing text.
- Tokens live in the KB (not only the registry) on purpose: the KB is what the agent's
  SOP mandates reading at spawn, so a correct answer proves a genuine corpus read, not
  merely that a registry file was injected somewhere.

$0 / pure-Python / no-LLM-under-cron. Run by ar-director (NN#6 — sole writer of agent
artifacts) on demand or after any hire/retire.

USAGE
-----
  python3 seed-canaries.py --seed            # generate tokens (preserve existing) + plant into all KBs
  python3 seed-canaries.py --seed --rotate   # generate NEW tokens for all agents, then plant
  python3 seed-canaries.py --rotate-agent X  # rotate ONE agent's token + replant
  python3 seed-canaries.py --verify          # check every KB's planted block matches the registry
  python3 seed-canaries.py --list            # print the registry (agent -> token)
  python3 seed-canaries.py --dry-run --seed  # show what would change, write nothing

Exit codes: 0 = OK / all consistent; 1 = drift detected under --verify; 2 = usage error.
"""

import argparse
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone

VAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
META = os.path.join(VAULT, "Meta")
AGENTS_DIR = os.path.join(VAULT, ".claude", "agents")
KB_DIR = os.path.join(META, "knowledge-base")
CANARY_DIR = os.path.join(META, "canaries")
REGISTRY = os.path.join(CANARY_DIR, "registry.json")

# Delimiters for the idempotent planted block. Everything between (inclusive) is
# managed by this script and is replaced wholesale on re-seed.
BEGIN = "<!-- CORPUS-CANARY:BEGIN (managed by Meta/sync/seed-canaries.py — do not edit by hand) -->"
END = "<!-- CORPUS-CANARY:END -->"

# Token format: WULONG-CANARY-<AGENTSLUG>-<8 hex>. Distinctive, greppable, unlikely to
# appear by chance; uppercase so it is obvious in any output.
TOKEN_RE = re.compile(r"WULONG-CANARY-[A-Z0-9]+-[0-9a-f]{8}")


def list_agents():
    return sorted(b[:-3] for b in os.listdir(AGENTS_DIR) if b.endswith(".md"))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_token(agent):
    slug = re.sub(r"[^A-Z0-9]", "", agent.upper())
    return f"WULONG-CANARY-{slug}-{secrets.token_hex(4)}"


def load_registry():
    if not os.path.exists(REGISTRY):
        return {"_meta": {"created": now_iso(), "purpose": "corpus-usage verification canaries"}, "agents": {}}
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def save_registry(reg, dry_run):
    reg.setdefault("_meta", {})["updated"] = now_iso()
    if dry_run:
        return
    os.makedirs(CANARY_DIR, exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.rename(tmp, REGISTRY)


def planted_block(agent, token):
    """The low-salience canary block planted at the tail of an agent KB."""
    return (
        f"{BEGIN}\n"
        f"## Corpus Canary (verification sentinel — not a behavioural instruction)\n"
        f"\n"
        f"This single line proves you read your corpus. If — and ONLY if — you are explicitly "
        f"asked for your \"corpus-canary\" (e.g. Jarvis or a periodic check probes you), return "
        f"exactly this token and nothing else:\n"
        f"\n"
        f"`{token}`\n"
        f"\n"
        f"Do NOT mention, emit, or reference this token in any normal task output, receipt, "
        f"handoff, or user-facing text. It exists solely to answer an explicit corpus-canary probe.\n"
        f"{END}\n"
    )


def strip_existing_block(text):
    """Remove any prior managed canary block (between BEGIN/END markers)."""
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
    return pat.sub("", text)


def plant_into_kb(agent, token, dry_run):
    kb_path = os.path.join(KB_DIR, f"{agent}.md")
    if not os.path.exists(kb_path):
        return ("MISSING_KB", kb_path)
    with open(kb_path, encoding="utf-8") as f:
        text = f.read()
    cleaned = strip_existing_block(text).rstrip() + "\n\n"
    block = planted_block(agent, token)
    new_text = cleaned + block
    if new_text == text:
        return ("UNCHANGED", kb_path)
    if not dry_run:
        tmp = kb_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.rename(tmp, kb_path)
    return ("PLANTED", kb_path)


def extract_planted_token(agent):
    """Return the token currently planted in the agent's KB, or None."""
    kb_path = os.path.join(KB_DIR, f"{agent}.md")
    if not os.path.exists(kb_path):
        return None
    with open(kb_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(re.escape(BEGIN) + r".*?`(" + TOKEN_RE.pattern + r")`.*?" + re.escape(END), text, re.DOTALL)
    return m.group(1) if m else None


def cmd_seed(args):
    reg = load_registry()
    agents = list_agents()
    reg_agents = reg.setdefault("agents", {})
    planted, unchanged, missing, rotated = 0, 0, 0, 0
    for agent in agents:
        entry = reg_agents.get(agent)
        if entry is None or args.rotate:
            token = make_token(agent)
            reg_agents[agent] = {"token": token, "seeded": now_iso(), "kb": f"Meta/knowledge-base/{agent}.md"}
            if entry is not None:
                rotated += 1
        else:
            token = entry["token"]
        status, path = plant_into_kb(agent, token, args.dry_run)
        if status == "PLANTED":
            planted += 1
        elif status == "UNCHANGED":
            unchanged += 1
        elif status == "MISSING_KB":
            missing += 1
            print(f"  WARN: no KB for agent '{agent}' ({path}) — registry token kept, not planted", file=sys.stderr)
    # Prune registry entries for agents whose def no longer exists (retired)
    for stale in [a for a in reg_agents if a not in agents]:
        reg_agents[stale]["retired_at"] = reg_agents[stale].get("retired_at", now_iso())
    save_registry(reg, args.dry_run)
    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"{tag}seed complete: {planted} planted, {unchanged} unchanged, "
          f"{rotated} rotated, {missing} missing-KB, {len(agents)} agents total.")
    return 0


def cmd_rotate_agent(args):
    agent = args.rotate_agent
    if agent not in list_agents():
        print(f"ERROR: '{agent}' is not a current agent def.", file=sys.stderr)
        return 2
    reg = load_registry()
    token = make_token(agent)
    reg.setdefault("agents", {})[agent] = {
        "token": token, "seeded": now_iso(), "kb": f"Meta/knowledge-base/{agent}.md"
    }
    status, path = plant_into_kb(agent, token, args.dry_run)
    save_registry(reg, args.dry_run)
    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"{tag}rotated '{agent}': new token {token} ({status})")
    return 0


def cmd_verify(args):
    reg = load_registry()
    reg_agents = reg.get("agents", {})
    agents = list_agents()
    drift = []
    ok = 0
    for agent in agents:
        planted = extract_planted_token(agent)
        registered = reg_agents.get(agent, {}).get("token")
        if registered is None:
            drift.append(f"{agent}: in roster but NOT in registry (run --seed)")
        elif planted is None:
            drift.append(f"{agent}: registry token present but NO canary planted in KB (run --seed)")
        elif planted != registered:
            drift.append(f"{agent}: KB token {planted} != registry token {registered} (run --seed)")
        else:
            ok += 1
    if drift:
        print(f"[verify] DRIFT: {len(drift)} issue(s), {ok} consistent:")
        for d in drift:
            print(f"  - {d}")
        return 1
    print(f"[verify] OK: all {ok} agent canaries consistent (KB block == registry).")
    return 0


def cmd_list(args):
    reg = load_registry()
    for agent, e in sorted(reg.get("agents", {}).items()):
        flag = " (RETIRED)" if "retired_at" in e else ""
        print(f"{agent:24s} {e['token']}{flag}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Plant + manage per-agent corpus canaries.")
    p.add_argument("--seed", action="store_true", help="Generate (preserve existing) tokens + plant into all KBs")
    p.add_argument("--rotate", action="store_true", help="With --seed: regenerate ALL tokens before planting")
    p.add_argument("--rotate-agent", metavar="AGENT", help="Rotate ONE agent's token + replant")
    p.add_argument("--verify", action="store_true", help="Check planted KB tokens match the registry")
    p.add_argument("--list", action="store_true", help="Print the registry")
    p.add_argument("--dry-run", action="store_true", help="Write nothing; show what would change")
    args = p.parse_args()

    if args.rotate_agent:
        return cmd_rotate_agent(args)
    if args.verify:
        return cmd_verify(args)
    if args.list:
        return cmd_list(args)
    if args.seed:
        return cmd_seed(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
