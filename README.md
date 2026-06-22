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

## Install

Clone this repo into a Claude Code project root:

```bash
git clone https://github.com/wulong/wulong.git .
```

Then configure your agent identities and adapt `CLAUDE.md` to your context.
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
  brain.md               -- Foundational state (fill in for your context)
  change-log.md          -- Append-only audit trail
  vault-structure.md     -- Where things live and why
scripts/
  scrub.sh               -- Pre-publish sensitive-pattern scan
  pre-publish-assert.sh  -- Pre-push safety assertion
scrub-patterns.txt.example -- Deny-list template (cp scrub-patterns.txt.example scrub-patterns.txt then fill in your tokens)
```

## License

MIT. See LICENSE.
