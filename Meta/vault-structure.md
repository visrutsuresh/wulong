---
type: meta
tags: [meta, structure]
updated: YYYY-MM-DD
---

# Vault Structure — Machine-Readable Map

This file is the canonical folder map for the vault. All agents read it before
filing or creating notes. Keep this up to date as the vault evolves.

---

## Critical Immutability Rules

These rules override any cleanup, consolidation, or reorganisation task.

**Rule 1 -- Meta/ machine artifacts are NEVER reorganised.**
`Meta/receipts/`, `Meta/handoffs/`, `Meta/handoffs/archive/`, and `Meta/Sessions/`
carry the receipt graph (`change_id` + `gated_by` chains) validated by
`validate-receipt-graph.py`, which ships in the installed package at
`wulong/sync/` and is run by path with `--root` pointed at this vault. It is not
copied into `Meta/sync/`. Moving, renaming, or deleting files in these
folders silently breaks the causal graph. These folders are permanently hands-off
for any note-cleanup or defrag operation. Only Cerebrum infrastructure scripts may
touch them, and only to append (never to move or delete).

**Rule 2 -- Code directories live outside the vault.**
Code repositories live at `~/Documents/GitHub/` or equivalent external path.
Do NOT store active code repos inside the vault. The vault holds knowledge
(notes, docs, plans, receipts) not executable code.

---

## Folder Map

```
/                           vault root (WULONG_ROOT)
|
+-- Meta/                   governance and agent infrastructure
|   +-- receipts/           task completion receipts (immutable, append-only)
|   +-- handoffs/           inter-agent handoff messages
|   |   +-- archive/        archived handoffs (read-only after archival)
|   +-- playbooks/          step-by-step procedures per agent
|   |   +-- <agent>/        one folder per agent
|   +-- knowledge-base/     per-agent knowledge files (<agent>.md)
|   +-- context/            compiled per-agent context files (auto-generated)
|   +-- sync/               governance scripts (validate-receipts.py etc.)
|   +-- doctor/             violation logs, audit outputs
|   +-- sessions/           session logs (orchestrator-owned, do not touch)
|   +-- agent-messages.md   async message queue between agents
|   +-- brain.md            Tier-1 system state (canonical truth)
|   +-- change-log.md       append-only event stream (NN#7 enforcement)
|   +-- task-board.md       active task queue
|   +-- approval-queue.md   pending CEO decisions
|
+-- 00-Inbox/               unprocessed incoming content
+-- 01-Projects/            active projects (PARA: Projects)
+-- 02-Areas/               ongoing responsibilities (PARA: Areas)
+-- 03-Resources/           reference material (PARA: Resources)
+-- 04-Archive/             archived projects and notes
+-- 05-People/              contact notes
+-- 06-Meetings/            meeting notes
+-- 07-Daily/               daily notes (date-keyed: YYYY-MM-DD.md)
```

---

## Naming Conventions

### Note filenames

Note filenames contain the topic, NOT the date. The date belongs in `date:` frontmatter.

Good: `Feature Design Notes.md` with `date: 2026-01-15` in frontmatter.
Bad: `2026-01-15 Feature Design Notes.md` (date in filename -- not allowed).

### Timestamps in filenames

Where timestamps appear in filenames (receipts, handoffs, session logs), they MUST
be rounded to the nearest `:00` or `:30`. Never use exact minutes.

Exceptions (date-keyed series where the date IS the canonical identifier):
- Daily notes: `07-Daily/YYYY-MM-DD.md`
- Session logs: `Meta/sessions/YYYY-MM-DD-HHMM.md`
- Receipts: `Meta/receipts/<agent>-YYYY-MM-DD-HHMM-<slug>.md`
- Handoffs: `Meta/handoffs/<from>-to-<to>-<topic>-YYYY-MM-DD-HHMM.md`

---

## Meta/ Immutability Contract

The following paths must NEVER be reorganised, renamed, or deleted without
an explicit CEO-approved plan and full receipt-graph re-link:

- `Meta/receipts/` -- receipt graph nodes
- `Meta/handoffs/` -- gate-chain edge references
- `Meta/change-log.md` -- append-only audit trail
- `Meta/brain.md` -- authoritative system state
- `Meta/sync/` -- infrastructure scripts

---

## Code Allow-List (files that may exist at vault root)

The following are the ONLY non-documentation files permitted at the vault root:
- `.claude/` -- agent definitions, hooks, skills
- `.gitignore`
- `CLAUDE.md`
- `LICENSE`
- `README.md`
- `SECURITY.md`
- `scrub-patterns.txt`
- `scripts/` -- build/scrub scripts

Any other non-`.md` file at root is a signal it was accidentally placed there.
