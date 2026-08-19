# User Guide

## Install

**From PyPI (once published):**
```bash
pip install wulong
```

**From source:**
```bash
git clone https://github.com/visrutsuresh/wulong.git
cd wulong
pip install -e .
```

Python 3.10 or later is required. The only mandatory non-stdlib dependency
is `PyYAML>=6.0`. The optional `ml` extra (`pip install wulong[ml]`) adds
`scikit-learn` for the `synthesize-lessons.py` script.

wulong is POSIX only. macOS and Linux are supported and Windows is not. 12
scripts in `wulong/sync/` import `fcntl` to take an advisory lock, 11 of them at
module top level, so those 11 fail loudly the moment they are imported on
Windows. The 12th, `wulong/sync/observer-disposition.py`, defers its import into
a function body guarded by `except OSError`, and a missing module raises
`ImportError`, which is not an `OSError`, so on Windows it dies uncaught at call
time, after its ledger append has already succeeded. A partial write followed by
a hard stop is worse than a refusal to start, and a no-op lock would be worse
still: it would trade a loud failure for silently interleaved writes to the audit
trail, which is the one file this whole design exists to keep honest.

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
Meta/sync/               -- created EMPTY, see "Running the governance scripts
                            directly" below: the engine lives in the package
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

### Wiring the delivery-gate hook (opt-in)

The hook script is installed at `.claude/hooks/stop-slop-hook.py`, but nothing
runs it until Claude Code is told to. Add `--with-hooks` and init also writes
`.claude/settings.json`:

```bash
wulong init /path/to/vault --with-hooks
```

That file wires the `Stop` event to `python3 <vault>/.claude/hooks/stop-slop-hook.py`
with a 10 second timeout. Three details are the whole reason this flag exists,
because each one fails silently rather than loudly:

| Detail | Value | Why it is not the obvious choice |
|---|---|---|
| Event | `Stop` | Fires when a turn ends, which is when a finished message exists to check. `SessionStart` looks right and has nothing to read. |
| Command | `python3` plus an absolute path | The exec bit does not survive a wheel reliably, so naming the interpreter removes the dependency on it. |
| Timeout | 10 seconds | A timeout under the real runtime kills the hook and reports nothing. |

There is no `matcher`: a matcher selects a tool name, and `Stop` carries no tool.

**What it does once wired.** It blocks your own delivery when the last assistant
turn contains an em dash and hands the model a reason to rewrite. It is not a
control over anyone but you: you wrote the settings file and you can delete it.

**What wulong does NOT do.** wulong ships nothing that runs a hook for you, and
no pre-commit, CI job or scheduler either. The binding gate is `wulong gate` and
the test suite. Until you wire something yourself, this repo enforces nothing
automatically.

**If `.claude/settings.json` already exists**, init leaves it exactly as it is,
because init never overwrites. It then says so by name and prints the JSON to
merge into your `"hooks"` object. Pass `--force` to overwrite the whole file
instead.

**To remove it**, delete `.claude/settings.json` or delete its `"Stop"` entry.
Re-running init cannot remove it.

**Was it wired, and has it ever fired?** Every invocation appends one JSON line
to `.wulong/hook-events.jsonl`, recording the outcome and, when the hook gave up
on an internal error, the exception class. No message text is ever written
there. `wulong doctor` reads it as axis `hook_health`: no records at all means
the hook has never fired, which is what a stale path or a kill by timeout looks
like, and records whose outcome is `allow` mean it fired and found nothing to
do. Without that distinction those two states are the same silence.

A wrong event is the third state, and it is not silence. The hook reads the
incoming `hook_event_name` and refuses to act on an event it does not handle,
recording the name it actually received. `wulong doctor` names that event back
to you. Without the check the hook would scan and block on someone else's event,
because `transcript_path` is a field every Claude Code event can carry, and the
log would look healthy over a gate that never ran on the turns it was for.

## Tell wulong which vault you mean

There are three ways, and they are tried in this order:

1. `--root /path/to/your/project`, on `doctor`, `gate` and `pulse`
2. the `WULONG_ROOT` environment variable
3. running the command from inside the vault, which walks up from the current
   directory looking for `CLAUDE.md` or `.wulong`

If none of the three answers, the command stops with an error that names all
three. It does not fall back to a guess. From a pip install the only guess
available is the `site-packages` directory the code was installed into, and a
health scan that finds nothing there would report a clean vault.

```bash
export WULONG_ROOT=/path/to/your/project
```

Add this to your `.env` file so it is set automatically:
```
WULONG_ROOT=/path/to/your/project
```

`--root` wins over `WULONG_ROOT`, which wins over your current directory.

31 of the 53 governance scripts read `WULONG_ROOT` to find `Meta/`, receipts,
and agent definitions, either directly or through the shared resolver in
`wulong/_root.py`. Of the other 22, 20 derive their paths from `__file__` and
never look at the variable; the remaining two never touch `__file__` at all
(`check_rename_diff.py` takes its paths as arguments, `research_router.py` has
no path logic). Not all 20 resolve a vault root: several take only their own
directory or one file beside it, and `wulong-init.py` resolves its own engine
and payload directories and no vault at all.

So the variable is still not one switch for all 53 scripts. What 0.4.0
guarantees instead is that each command resolves the root ONCE and hands it to
every process it starts. Before 0.4.0, `wulong pulse --root B` put the parent on
vault B and three of its four child processes on `site-packages`.

Watch one trap: `wulong-init.py` mentions `WULONG_ROOT` in its help text and
tells you to set it, but never reads it, and that is deliberate rather than a
gap. `init` creates a vault, so the directory to create is its argument, not
something to look up. Point `wulong init` at the vault with its path argument.
The count above is scripts that actually read the environment, not scripts that
mention the name.

## CLI subcommands

### wulong init

```
wulong init [target] [--force] [--with-hooks]
```

Scaffold a vault skeleton. `target` defaults to the current directory.
`--force` overwrites files init would otherwise skip. `--with-hooks` also writes
`.claude/settings.json` wiring the `Stop` hook; see "Wiring the delivery-gate
hook" above, including how to remove it.

### wulong doctor

```
wulong doctor [--root PATH] [--require-all-axes]
```

Run the vault health scan (`vault-health-check.py`). Nine axes: inbox backlog,
stray code outside allowed directories, un-archived handoffs, orphan notes,
empty folders, broken links, drift delta, the enforcer inventory, and hook
health.

The last line of every run is a single verdict token, and the run also prints
`PASSED`, `SKIPPED` and `FAILED` as three separate counts:

| Verdict | Meaning | Exit |
|---|---|---|
| `GREEN` | all nine axes ran and every one of them was silent | 0 |
| `ADVISORY` | all nine ran, nothing failed, but at least one raised a `YELLOW` or `WARNING` | 0 |
| `PARTIAL` | nothing failed, but at least one axis could not run | 0 |
| `RED` | at least one axis found something | 1 |

A skipped axis is neither a pass nor a failure. Each `SKIP` line says what that
axis would need in order to run, so `PARTIAL` is an instruction, not a shrug.

`ADVISORY` is the same idea one level down. A `YELLOW` or `WARNING` axis counts
as passed and does not move the exit code, deliberately: a vault installed with
`wulong init --with-hooks` raises `WARNING [I] hook_health` on every run until
the hook fires for the first time, so failing on it would break the quickstart.
What it may not do is print the all-clear line over itself. `PARTIAL` outranks
`ADVISORY`, so a run that both skipped an axis and raised a warning reports the
skips.

`--require-all-axes` turns any skip into exit 1. It is off by default, because a
correctly installed fresh vault legitimately cannot run four of the nine and a
quickstart that exits non-zero is not a quickstart. Turn it on in CI, where an
axis silently failing to run IS the thing you want to hear about.

A bare `wulong doctor /path/to/vault` still works and means the same as
`--root /path/to/vault`. The order of the tokens does not matter: a path you
write on the command line, in either form, always outranks `WULONG_ROOT` and
the directory you are standing in. Write both forms at once and `--root` wins.

> **Fixed in 0.4.0.** `vault-health-check.py` used to read `WULONG_ROOT` zero
> times, so only the positional argument worked and the bare form walked up from
> `__file__` and raised `FileNotFoundError` from a pip install. It now takes
> `--root`, honours `WULONG_ROOT`, and refuses to guess. Separately, a fresh
> vault used to skip 3 of the 8 axes it then had and still print GREEN and exit
> 0. That is
> the `PARTIAL` verdict above.

### wulong gate

```
wulong gate --change-id X --gate {nn3,nn4} [--receipts-dir PATH]
wulong gate --manifest --artifact PATH [--artifact PATH ...]
wulong gate --verify RECEIPT --artifact PATH [--artifact PATH ...]
```

Check whether a gate has been cleared for a `change_id`:
- `nn3`: contrarian PLAN-review PASS exists (pre-coder spawn check)
- `nn4`: tester DONE receipt exists (pre-deploy-close check)

Exits 0 (ALLOW) or 1 (REFUSE). Use `--receipts-dir` to point at your receipts
directory.

**Binding a PASS to what was reviewed.** `--manifest` hashes the artifacts you
name and prints `artifact_manifest_sha256` and `artifact_count` to paste into the
receipt. `--verify` recomputes that digest from the bytes you name and compares.
YOU enumerate the artifacts in both modes: neither one parses a file list out of
the receipt, and `--verify` never resolves the paths the receipt records, so a
verified artifact can be moved or renamed and still verify. Neither mode reads a
vault, so both work offline and outside one.

`nn3` consults the digest through one shared predicate. `--require-binding`
REFUSEs a PASS that carries none; it is OFF by default until 0.6.0 on
2027-02-01, and a warning prints instead. `--legacy-unbound-until YYYY-MM-DD`
exempts older receipts and is ADVISORY, because the date it reads is the date the
receipt reports about itself. `nn4` is unaffected: it reads `status`, never a
verdict.

What binding does NOT do: the digest is unkeyed, so it stops substitution and not
forgery. Mode bits and empty directories are outside it, and it binds the
multiset of file contents with no path inside it, so a rename and a swap of which
name holds which content are both invisible.

> **Fixed in 0.4.0.** The default receipts path used to come from the script's
> own file location, which from a pip install is inside `site-packages`, so the
> gate REFUSEd everything unless you passed `--receipts-dir`. It now defaults to
> `<root>/Meta/receipts`, where the root comes from `--root`, then
> `WULONG_ROOT`, then the directory you are standing in. `--receipts-dir` still
> overrides everything when your receipts live somewhere else.
>
> What has NOT changed: the gate proves a file claiming a PASS exists. It does
> not prove a review happened. See ARCHITECTURE.md for the full list of what it
> cannot check.

Example:
```bash
wulong gate --change-id my-feature-2026 --gate nn3
# [ALLOW] gate=nn3 change_id=my-feature-2026 -- contrarian PASS found
```

### wulong pulse

```
wulong pulse --change-id X [--root PATH] [--strict] [--no-exit-nonzero-on-red]
```

Session-close pulse: runs verify-change, the doc-consistency check, the
session-close audit and the compliance check for a `change_id`. Reports
`ALL CLEAR` or `ACTION REQUIRED`.

**Changed in 0.4.0.** `ACTION REQUIRED` now exits 1. Until 0.3.0 the pulse
printed `ACTION REQUIRED` and exited 0 unless you passed `--strict`, so any
script or hook that checked the exit code saw success. Pass
`--no-exit-nonzero-on-red` if you want the old log-only behaviour back.

`--strict` is a different switch and is unchanged. It does not merely set the
exit code: it changes what the child checks count as a failure and relabels a
RED verdict as a HARD-BLOCK. Use `--no-exit-nonzero-on-red` to soften the exit
code, not the absence of `--strict`.

The root is resolved once and handed to every child process. Before 0.4.0,
`wulong pulse --root B` put the parent on vault B and three of its four children
on `site-packages`.

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
my-server-ip-address
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

The governance scripts ship inside the installed package at `wulong/sync/`.
`wulong init` does NOT copy them into your vault, so there is no `Meta/sync/`
directory to run them from. Four of them have a CLI subcommand:

```bash
wulong doctor /path/to/vault
wulong gate --change-id X --gate nn3
wulong pulse --change-id X
wulong init /path/to/vault
```

The rest are run by path into the installed package. Resolve that path once:

```bash
SYNC=$(python3 -c "import wulong, pathlib; print(pathlib.Path(wulong.__file__).parent / 'sync')")
python3 "$SYNC/validate-receipts.py" --root /path/to/vault
python3 "$SYNC/trace-change-chain.py" --root /path/to/vault --change-id X
```

Pass `--help` to any script for its argument list. Where a script takes
`--root`, that flag wins over `WULONG_ROOT`, so passing it is the reliable way
to be certain which vault you are about to touch. Since 0.4.0 that precedence is
one shared implementation in `wulong/_root.py` rather than a copy per script,
and it is asserted by the test suite for every script that has the flag.

The engine scripts differ from the four CLI subcommands in exactly one place. If
nothing names a vault, a subcommand stops with an error, while an engine script
falls back to the directory it was installed into and warns. That is deliberate:
an engine script normally runs as a child of a subcommand which hands it the
root, so the fallback is only reached when you invoke one by hand from inside a
vault, where its own location is the right answer.
