#!/usr/bin/env python3
"""
AE-07 one-shot: apply functional `model:` + `tier:` frontmatter to each agent def.
Authored & run by ar-director (sole writer of agent defs, NN #6).

Guardrail (hard rule): gate-holders & deep-reasoning agents MUST be opus.
Only genuinely light deterministic I/O agents get haiku. Ambiguous -> sonnet.
phantom-troupe is a RETIRED tombstone -> no functional tier (marked retired).

Idempotent: skips a file that already has a `model:` line.
Validates every model value against the allowed alias set.
Inserts the two lines immediately AFTER the existing `tools:` line, inside frontmatter.
"""
import os, sys, pathlib

_WULONG_ROOT = os.environ.get("WULONG_ROOT", str(pathlib.Path(__file__).resolve().parent.parent.parent))  # ponytail: env knob; upgrade = set WULONG_ROOT in wulong init
AGENTS_DIR = pathlib.Path(_WULONG_ROOT) / ".claude" / "agents"
VALID_MODELS = {"opus", "sonnet", "haiku"}

# tier name per model class
TIER_FOR = {"haiku": "light-io", "sonnet": "workers", "opus": "deep-reasoning"}

HAIKU = [  # light, deterministic I/O — little reasoning
    "postman", "scheduler", "comms-agent", "portfolio-tracker",
    "monitor", "sorter", "scribe", "transcriber",
]
OPUS = [  # gate-holders + deep-reasoning / high-stakes judgement — NEVER tiered down
    "jarvis", "company-orchestrator", "mastermind", "head-of-arnd",
    "contrarian", "tester", "analyst", "data-scientist", "quant-researcher",
    "systems-architect", "backtester", "risk-manager", "financial-manager",
    "lawyer", "tax-strategist", "ar-director", "doctor",
    # contrarian-assistant already set at birth (opus) — included for verification, skipped if present
    "contrarian-assistant",
]
SONNET = [  # standard execution workers
    "coder", "web-designer", "seeker", "connector", "librarian", "writer",
    "hr-analyst", "compliance-officer", "accountant", "release-manager",
    "qa-engineer", "knowledge-curator", "project-manager", "architect",
    "researcher", "crypto", "deployer", "keepers", "wellness-guide", "food-coach",
]

# explicit retired tombstone — gets a retired marker, not a functional model
RETIRED = {"phantom-troupe": "retired"}

assignment = {}
for a in HAIKU:  assignment[a] = "haiku"
for a in OPUS:   assignment[a] = "opus"
for a in SONNET: assignment[a] = "sonnet"

# sanity: no agent assigned twice
dupes = [a for a in assignment if (HAIKU + OPUS + SONNET).count(a) > 1]
if dupes:
    sys.exit(f"FATAL: agent assigned to multiple tiers: {dupes}")

# validate model aliases
bad = {a: m for a, m in assignment.items() if m not in VALID_MODELS}
if bad:
    sys.exit(f"FATAL: invalid model alias(es): {bad}")

changed, skipped, missing, retired_marked = [], [], [], []

def insert_after_tools(text, insert_lines):
    out, done = [], False
    for line in text.splitlines(keepends=True):
        out.append(line)
        if not done and line.strip().startswith("tools:"):
            nl = "\n" if not line.endswith("\n") else ""
            out.append(nl + insert_lines)
            done = True
    return "".join(out), done

# functional tiering
for agent, model in assignment.items():
    f = AGENTS_DIR / f"{agent}.md"
    if not f.exists():
        missing.append(agent); continue
    txt = f.read_text()
    if "\nmodel:" in txt or txt.startswith("model:"):
        skipped.append(agent); continue
    block = f"model: {model}\ntier: {TIER_FOR[model]}\n"
    new, ok = insert_after_tools(txt, block)
    if not ok:
        missing.append(f"{agent}(no tools: line)"); continue
    f.write_text(new)
    changed.append(f"{agent}->{model}")

# retired tombstone marker
for agent, marker in RETIRED.items():
    f = AGENTS_DIR / f"{agent}.md"
    if not f.exists():
        continue
    txt = f.read_text()
    if "\nmodel:" in txt or txt.startswith("model:"):
        skipped.append(agent); continue
    block = f"model: {marker}  # tombstone — retired team, no functional tier\ntier: retired\n"
    new, ok = insert_after_tools(txt, block)
    if ok:
        f.write_text(new); retired_marked.append(agent)

print("=== AE-07 model tiering applied ===")
print(f"haiku  ({len(HAIKU)}): {', '.join(HAIKU)}")
print(f"sonnet ({len(SONNET)}): {', '.join(SONNET)}")
print(f"opus   ({len(OPUS)}): {', '.join(OPUS)}")
print(f"retired tombstone: {', '.join(RETIRED)}")
print("---")
print(f"CHANGED ({len(changed)}): {changed}")
print(f"SKIPPED already-tiered ({len(skipped)}): {skipped}")
print(f"RETIRED-MARKED ({len(retired_marked)}): {retired_marked}")
print(f"MISSING ({len(missing)}): {missing}")
print("---")
# guardrail assertion: every opus agent must NOT be in haiku list
gate_in_haiku = [a for a in OPUS if a in HAIKU]
print(f"GUARDRAIL CHECK — gate/deep-reasoning agents in haiku list: {gate_in_haiku} (must be empty)")
total = len(HAIKU) + len(SONNET) + len(OPUS)
print(f"TOTAL functional-tier agents: {total} (+1 retired tombstone = {total+1} defs)")
