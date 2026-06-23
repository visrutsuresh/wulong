# User Guide

## Install

**From PyPI (once published):**
```bash
pip install wulong
```

**From source:**
```bash
git clone https://github.com/<your-github-username>/wulong.git
cd wulong
pip install -e .
```

Python 3.10 or later is required. The only mandatory non-stdlib dependency
is `PyYAML>=6.0`. The optional `ml` extra (`pip install wulong[ml]`) adds
`scikit-learn` for the `synthesize-lessons.py` script.

## Init

After install, scaffold a vault skeleton:

```bash
cd /path/to/your/project
wulong init
```

This creates the directory structure and copies overlay templates:

```
.claude/agents/          -- agent definition directory (populate with .md files)
.claude/hooks/           -- Claude Code hooks
.claude/skills/          -- curated skill files
Meta/receipts/           -- task completion receipts
Meta/handoffs/           -- inter-agent handoffs
Meta/sync/               -- governance scripts (the engine)
Meta/playbooks/          -- per-agent task playbooks
Meta/knowledge-base/     -- per-agent knowledge files
Meta/context/            -- compiled per-agent context snapshots
.wulong/                 -- wulong config directory
.env                     -- env knobs (copied from .env.example)
Meta/brain.md            -- living world-state (copied from Meta/brain.md.example)
scrub-patterns.txt       -- scrub deny-list (copied from scrub-patterns.txt.example)
.wulong/projects.json    -- per-project config (copied from .wulong/projects.json.example)
```

`wulong init` skips any file that already exists, so re-running it on an
existing vault is safe.

To init into a specific directory:
```bash
wulong init /path/to/vault
```

## Set WULONG_ROOT

After init, tell the engine where your vault lives:

```bash
export WULONG_ROOT=/path/to/your/project
```

Add this to your `.env` file so it is set automatically:
```
WULONG_ROOT=/path/to/your/project
```

Most governance scripts read `WULONG_ROOT` to find `Meta/`, receipts,
and agent definitions. If the variable is absent, scripts fall back to
walking up from `__file__`.

## CLI subcommands

### wulong init

```
wulong init [target]
```

Scaffold a vault skeleton. `target` defaults to the current directory.

### wulong doctor

```
wulong doctor [vault-path]
```

Run the vault health scan (`vault-health-check.py`). Checks inbox backlog,
stray code outside allowed directories, un-archived handoffs, orphan notes,
and broken links. Exits 0 on GREEN, non-zero on RED.

Set `WULONG_ROOT` or pass `vault-path` to point at your vault.

### wulong gate

```
wulong gate --change-id X --gate {nn3,nn4} [--receipts-dir PATH]
```

Check whether a gate has been cleared for a `change_id`:
- `nn3`: contrarian PLAN-review PASS exists (pre-coder spawn check)
- `nn4`: tester DONE receipt exists (pre-deploy-close check)

Exits 0 (ALLOW) or 1 (REFUSE). Use `--receipts-dir` to override the default
receipts path (`$WULONG_ROOT/Meta/receipts/`).

Example:
```bash
wulong gate --change-id my-feature-2026 --gate nn3
# [ALLOW] gate=nn3 change_id=my-feature-2026 -- contrarian PASS found
```

### wulong pulse

```
wulong pulse --change-id X [--strict]
```

Session-close pulse: runs verify-change, doc-consistency check, and the
session-close audit for a `change_id`. Reports GREEN or RED.
`--strict` exits non-zero on RED (default is log-only).

## Configuring the overlay

After `wulong init`, open each overlay file and fill it in:

**`.env`**
```bash
WULONG_ROOT=/path/to/your/vault
# Add any other env knobs your scripts need
```

**`.wulong/projects.json`**
```json
{
  "projects": [
    {
      "id": "my-project",
      "name": "My Project",
      "repo": "/path/to/repo",
      "active": true
    }
  ]
}
```

**`Meta/brain.md`**

Fill in your current state: active projects, open threads, recent decisions.
Agents read this for context at spawn time.

**`scrub-patterns.txt`**

Add one pattern per line. These are regex patterns that `scripts/scrub.sh`
scans for before any push. Add your real API tokens, personal paths, and
usernames here.

```
my-real-api-token
my-username
/Users/realname/
76\.13\.190\.43
```

## Adapting the agent definitions

Agent definitions live in `.claude/agents/`. Each file has a `name:` field
that must match the filename stem. To use an agent:

1. Open the relevant `.md` file.
2. Read the `## Rules` and `## SOP` sections.
3. Adapt any hardcoded paths or references to your project.
4. Keep the `name:` frontmatter field unchanged.

To add a new agent, follow the `hire-agent.md` playbook in
`Meta/playbooks/ar-director/` (if present) or model the new file on an
existing one. The `name:` field is the routing key.

## Running the governance scripts directly

Every script in `Meta/sync/` (or `wulong/sync/` if using the installed package)
can be run directly:

```bash
python3 Meta/sync/vault-health-check.py
python3 Meta/sync/check_gate_precondition.py --change-id X --gate nn3
python3 Meta/sync/session-pulse.py --change-id X
python3 Meta/sync/validate-receipts.py
python3 Meta/sync/trace-change-chain.py --change-id X
```

Pass `--help` to any script for its argument list.
