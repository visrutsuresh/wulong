# wulong

A governed multi-agent orchestration framework for Claude Code.

## What it is

wulong is a project structure and policy layer that wraps Claude Code with
explicit governance: every code change requires a review gate before it lands
(contrarian PASS), every deploy requires a smoke test before the cycle closes
(tester PASS), every state change leaves an audit trail (change-log + causal
receipts), and the rules are written down in plain English (the Non-Negotiables
in CLAUDE.md).

It is not a tool or a library. It is a working system: agent definitions,
policy documents, sync scripts, and hooks that work together to run an
autonomous multi-agent operation with a human in the loop at every decision
that matters.

## Gate model

Two gates are binding on every code change:

**Gate 1 (contrarian, pre-code):** Before any worker agent writes code, a
contrarian agent reviews the plan. It scores feasibility, hidden assumptions,
blast radius, and cheaper alternatives. A PASS verdict is required before the
coder receives the handoff. A FAIL with objections triggers a parallel fix
loop (up to 3 rounds) before escalating to the human.

**Gate 2 (tester, post-deploy):** After every deploy, a tester agent runs
smoke tests (process health, log validation, output checks) and issues a PASS
or FAIL verdict before the cycle closes. A FAIL blocks closure until fixed.

Together these two gates mean: no unreviewed change lands in production, and
no deploy closes without a verified working state.

## Audit model

Every agent that writes or edits a file appends a line to `Meta/change-log.md`
in the format:

```
[YYYY-MM-DD HH:MM] agent-name -> ACTION filepath -- one-line summary
```

Every completed task produces a receipt at `Meta/receipts/<agent>-YYYY-MM-DD-HHMM-<task>.md`
with required fields: agent, task, date, time, status, and the causal chain
(`gated_by`) linking it to the gate receipts that authorized it.

The `Meta/sync/` scripts validate receipts, walk causal chains, query the
audit history, and enforce session-close checks.

## Engine and overlay

wulong separates public generic code from private personal data:

- **Engine** (tracked in git): agent definitions, sync scripts, hooks, skills, Meta
  skeleton docs, and playbooks. Contains zero personal data.
- **Overlay** (gitignored locally): the files that hold your personal context.
  Each has a `.example` counterpart committed to the repo as a template.

Overlay files (bootstrapped by `wulong init`):

| Gitignored file | Template | Read by |
|---|---|---|
| `.env` | `.env.example` | all scripts (env knobs) |
| `scrub-patterns.txt` | `scrub-patterns.txt.example` | `scripts/scrub.sh` |
| `Meta/brain.md` | `Meta/brain.md.example` | `compile-context.py`, `check-doc-consistency.py`, `session-guard.py`, `drift-scan.py` |
| `.wulong/projects.json` | `.wulong/projects.json.example` | `compile-context.py`, `health-scan.py` |

## Install

Clone this repo and install the CLI:

```bash
git clone https://github.com/wulong/wulong.git
cd wulong
pip install -e .
wulong init          # bootstraps overlay files from .example templates
```

`wulong init` copies each `.example` file to its real name (skip-if-exists), so
re-running it after the overlay is set up is always safe.

Then adapt `CLAUDE.md` and the `.claude/agents/` definitions to your context.
Agent definitions for all 65 agents ship in `.claude/agents/` — each file is
named by machine ID (e.g. `jarvis.md`, `contrarian.md`, `coder.md`) and the
`name:` frontmatter field matches the filename stem so routing works without
further configuration.

## Structure

```
CLAUDE.md                -- Policy layer: Non-Negotiables 1-21
.claude/
  hooks/                 -- Stop hooks (em-dash enforcement, spawn gate)
  skills/                -- Curated agent skills (ponytail, plain-English)
Meta/
  sync/                  -- Governance scripts (receipt validation, audit, scrub)
  receipts/              -- Completion receipts (one per task, see Cerebrum)
  brain.md               -- [OVERLAY] Your living world-state (gitignored)
  brain.md.example       -- Generic skeleton; bootstrapped by wulong init
  change-log.md          -- Append-only audit trail
  vault-structure.md     -- Where things live and why
.wulong/
  projects.json          -- [OVERLAY] Per-project config (gitignored)
  projects.json.example  -- Template; bootstrapped by wulong init
scripts/
  scrub.sh               -- Pre-publish sensitive-pattern scan
  pre-publish-assert.sh  -- Pre-push safety assertion
.env.example             -- Env knobs template; cp to .env and fill in
scrub-patterns.txt.example -- Deny-list template; cp to scrub-patterns.txt and fill in
```

## License

MIT. See LICENSE.
