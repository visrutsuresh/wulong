<!-- PLACEHOLDER: replace assets/logo.png with the real Wulong logo before publishing -->
<p align="center">
  <img src="assets/logo.png" alt="Wulong" width="280" />
</p>

# wulong

A working multi-agent governance framework for Claude Code: every change reviewed, every deploy verified, every decision logged.

<!-- PLACEHOLDER: repo path below uses <your-github-username>/wulong. Replace before publishing. -->
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/<your-github-username>/wulong/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-github-username>/wulong/actions/workflows/ci.yml)

## Quickstart

```bash
pip install wulong
wulong init          # scaffold a vault skeleton into the current directory
wulong doctor        # run a health scan on your vault
```

## What it is

wulong is a project structure and policy layer that wraps Claude Code with
explicit governance: every code change requires a review gate before it lands
(contrarian PASS), every deploy requires a smoke test before the cycle closes
(tester PASS), every state change leaves an audit trail (change-log + causal
receipts), and the rules are written down in plain English (the Non-Negotiables
in `CLAUDE.md`).

It is not a SaaS tool and it has no server. It is a working system: agent
definitions, policy documents, sync scripts, and hooks that you install into
your Claude Code project and adapt to your context.

## Architecture

### Engine and overlay

wulong separates public generic code from private personal data:

- **Engine** (tracked in git): agent definitions, sync scripts, hooks, skills,
  Meta skeleton docs, and playbooks. Contains zero personal data.
- **Overlay** (gitignored locally): the files that hold your personal context.
  Each has a `.example` counterpart committed to the repo as a template.

`wulong init` bootstraps the overlay from those templates.

| Gitignored file | Template | Read by |
|---|---|---|
| `.env` | `.env.example` | all scripts (env knobs) |
| `scrub-patterns.txt` | `scrub-patterns.txt.example` | `scripts/scrub.sh` |
| `Meta/brain.md` | `Meta/brain.md.example` | `compile-context.py`, `drift-scan.py` |
| `.wulong/projects.json` | `.wulong/projects.json.example` | `compile-context.py`, `health-scan.py` |

### The gate model

Two gates are binding on every code change:

**Gate 1 (contrarian, pre-code):** Before any worker agent writes code, a
contrarian agent reviews the plan. It scores feasibility, hidden assumptions,
blast radius, and cheaper alternatives. A PASS verdict is required before the
coder receives the handoff. A FAIL triggers a parallel fix loop (up to 3
rounds) before escalating to the human.

**Gate 2 (tester, post-deploy):** After every deploy, a tester agent runs
smoke tests and issues a PASS or FAIL verdict before the cycle closes.

Together: no unreviewed change lands, and no deploy closes without a verified
working state.

### Audit trail

Every agent that writes or edits a file appends a line to `Meta/change-log.md`:

```
[YYYY-MM-DD HH:MM] agent-name -> ACTION filepath -- one-line summary
```

Every completed task produces a receipt at
`Meta/receipts/<agent>-YYYY-MM-DD-HHMM-<task>.md` with required fields:
agent, task, date, time, status, and the causal chain (`gated_by`) linking it
to the gate receipts that authorized it.

The `wulong/sync/` scripts validate receipts, walk causal chains, query the
audit history, and enforce session-close checks.

### Agent roster

65 agent definitions ship in `.claude/agents/`, each named by machine ID
(e.g. `jarvis.md`, `contrarian.md`, `coder.md`). The `name:` frontmatter
field matches the filename stem so Claude Code routing works without
configuration. Adapt the definitions to your context and drop the ones you
do not need.

## Configuration

Set `WULONG_ROOT` to your vault path in your shell or `.env`. Most scripts
read this variable to find `Meta/`, receipts, and agent definitions.

```bash
export WULONG_ROOT=/path/to/your/vault
```

After `wulong init`, edit these files to fit your setup:

- `.env`: env knobs (WULONG_ROOT, project paths, Telegram token if used)
- `.wulong/projects.json`: per-project config consumed by `compile-context.py`
- `Meta/brain.md`: your living world-state (agents read this for context)
- `scrub-patterns.txt`: personal tokens/patterns to block before any push

## CLI reference

```
wulong init [target]
    Scaffold a vault skeleton into target (default: current directory).
    Copies .example overlay files to their real names (skip-if-exists).

wulong doctor [vault-path]
    Run vault health scan via vault-health-check.py.
    Set WULONG_ROOT or pass vault-path explicitly.

wulong gate --change-id X --gate {nn3,nn4}
    Check NN#3/NN#4 gate preconditions for a change_id.
    Exits 0 (ALLOW) or 1 (REFUSE).

wulong pulse [--change-id ID] [--strict]
    Session-close pulse: verify-change + doc-consistency + audit.
```

## What this is NOT

wulong is the governance layer extracted from a real working operation. These
personal-infrastructure components are not included and will not be added as
engine code:

- **No Telegram bridge.** The personal Telegram notification and autonomous
  loop driver (telegram_bridge, telegram_queue, loop_driver) are personal
  infra. Wire your own notification layer into the `.env` knobs if needed.
- **No VPS sync.** The VPS deploy/sync scripts (vps-sync, safe_fetch) are
  specific to a single operator's setup. Deployer and tester agents document
  the pattern; the scripts are yours to write.
- **No autonomous loop driver.** The v3.4 autonomous shift engine
  (autonomy_guard, trust_ramp, loop_killswitch) requires a live operator
  environment. The gate model and agent definitions give you the building
  blocks; the driver is intentionally out of scope for v0.1.0.
- **No built-in LLM calls.** All LLM execution runs through your Claude Code
  session. wulong is the governance layer, not the runtime.

## Install from source

```bash
git clone https://github.com/<your-github-username>/wulong.git
cd wulong
pip install -e .
wulong init
```

## License

MIT. See [LICENSE](LICENSE).

---

<!-- PLACEHOLDER: Star History graph below renders once the repo is public and has stars.
     Replace <your-github-username> before publishing. -->
[![Star History Chart](https://api.star-history.com/svg?repos=<your-github-username>/wulong&type=Date)](https://star-history.com/#<your-github-username>/wulong&Date)
