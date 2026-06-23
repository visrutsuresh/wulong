# Architecture

## Engine and overlay

wulong separates public generic infrastructure from private personal data.

**Engine** (tracked in git): every file in this repo except the overlay set.
Includes agent definitions, sync scripts, hooks, skills, CLAUDE.md, Meta/
skeleton, and the `wulong` CLI. Contains zero personal data or credentials.

**Overlay** (gitignored): four files hold the personal context that makes the
engine run against your specific operation. Each has a `.example` counterpart
committed as a template. `wulong init` copies the examples to their real names.

| Gitignored file | Template | What goes in it |
|---|---|---|
| `.env` | `.env.example` | WULONG_ROOT, project paths, API tokens |
| `scrub-patterns.txt` | `scrub-patterns.txt.example` | Personal tokens and paths to block at publish time |
| `Meta/brain.md` | `Meta/brain.md.example` | Living world-state: projects, people, open threads |
| `.wulong/projects.json` | `.wulong/projects.json.example` | Per-project config consumed by compile-context.py |

## Package layout

```
wulong/             installable Python package (thin shell)
  __init__.py       version constant
  cli.py            entry point: subprocess dispatcher, no business logic
  sync/             53 engine scripts (the governance substrate)
  templates/        4 overlay template files bundled as package data
                    (importlib.resources; works from both wheel and editable installs)
pyproject.toml      [project.scripts] wulong = "wulong.cli:main"
```

The CLI dispatches to scripts in `wulong/sync/` via subprocess. Scripts keep
their existing `_THIS_DIR` sibling-import pattern and work by direct path or
via the CLI. This is intentional (Option A in the plan): rewriting 53 files to
true relative imports has no v0.1.0 payoff and is deferred.

## The agent model

65 agent definitions ship in `.claude/agents/`. Each is a Markdown file with
YAML frontmatter:

```yaml
name: contrarian
description: Reviews plans and outputs for hidden assumptions and blast radius.
```

The `name:` field matches the filename stem (`contrarian.md` -> `name: contrarian`).
Claude Code routes spawn tokens to the correct file by this match.

Agents are divided by function:
- **Orchestrators**: jarvis (session owner), company-orchestrator, mastermind
- **Gate agents**: contrarian (pre-code review), tester (post-deploy smoke test)
- **Workers**: coder, deployer, analyst, data-scientist, and 60 others
- **Governance**: doctor, keepers, janitor, librarian, sorter

Adapt the definitions to your context. Drop what you do not need. Add agents by
following the ar-director playbook in `Meta/playbooks/ar-director/hire-agent.md`.

## The gate model

Two gates are binding on every code change (Non-Negotiables 3 and 4 in CLAUDE.md):

```
User request
    |
    v
Jarvis classifies + drafts plan
    |
    v
Contrarian reviews plan  <-- GATE 1 (NN#3): PASS required before coder spawns
    |
    v
Coder implements
    |
    v
Deployer deploys
    |
    v
Tester smoke-tests       <-- GATE 2 (NN#4): PASS required before cycle closes
    |
    v
Cycle closed
```

Gate receipts are written at `Meta/receipts/` with `review_verdict: PASS|FAIL`.
The `check_gate_precondition.py` script (exposed as `wulong gate`) checks
whether a PASS receipt exists for a given `change_id` before a worker spawns.
This is the mechanical enforcement: a coder that checks its own spawn
condition before writing any code.

## The audit model

Every agent that writes or edits a file appends a line to `Meta/change-log.md`:

```
[YYYY-MM-DD HH:MM] agent-name -> ACTION filepath -- summary
```

Every completed task produces a receipt at:
`Meta/receipts/<agent>-YYYY-MM-DD-HHMM-<task>.md`

Required frontmatter: `agent`, `task`, `date`, `time`, `status`.
Optional graph fields: `change_id`, `gated_by` (list of predecessor receipts),
`review_mode` (plan|output), `review_verdict` (PASS|FAIL).

The `gated_by` field creates a causal DAG. `trace-change-chain.py` walks it.
`validate-receipt-graph.py` checks that every `coder` receipt has a contrarian
PASS ancestor for the same `change_id`.

## Import topology

Scripts in `wulong/sync/` use stdlib-only imports plus optional PyYAML. The one
mandatory non-stdlib dep is `PyYAML>=6.0` (7 scripts hard-import `yaml`). The
`ml` extra (`scikit-learn>=1.0`) is optional: `synthesize-lessons.py` degrades
gracefully when sklearn is absent.

No script imports from `wulong/` (the thin shell package). They are standalone
files that work by direct path invocation. This is the guarantee that `wulong
doctor` and `wulong gate` work in a clean venv with only PyYAML installed.

## Session lifecycle

A typical session:
1. Jarvis reads context files and the change-log tail.
2. Jarvis classifies the request and drafts a plan.
3. Contrarian reviews the plan (NN#10 plan-review gate).
4. On PASS: Jarvis spawns the worker agent(s).
5. Workers complete their tasks, write receipts, and append to change-log.
6. Jarvis assembles output and spawns contrarian for output-review (NN#10 step 6).
7. On PASS: Jarvis closes the cycle (receipt + change-log + brain.md update).

The `session-pulse.py` script (exposed as `wulong pulse`) checks verify-change,
doc-consistency, and the session-close audit in one call.
