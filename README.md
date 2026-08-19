<p align="center">
  <img src="https://raw.githubusercontent.com/visrutsuresh/wulong/main/assets/logo.png" alt="Wulong" width="280" />
</p>

# wulong

A multi-agent governance layer for Claude Code: agent definitions, a review-gate CLI, and an audit trail of receipts and change-log lines.

<!-- repo path below uses visrutsuresh/wulong -->
[![PyPI](https://img.shields.io/pypi/v/wulong.svg)](https://pypi.org/project/wulong/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](https://raw.githubusercontent.com/visrutsuresh/wulong/main/LICENSE)
[![CI](https://github.com/visrutsuresh/wulong/actions/workflows/ci.yml/badge.svg)](https://github.com/visrutsuresh/wulong/actions/workflows/ci.yml)

## Quickstart

```bash
pip install wulong
wulong init myvault             # scaffold the vault skeleton into ./myvault
wulong doctor --root myvault    # run a health scan against that vault
```

`wulong doctor myvault` works too, and in any argument order. So does exporting
`WULONG_ROOT`, and so does running `wulong doctor` from anywhere inside the
vault. A vault you name on the command line always wins over the other two. If
none of the three says which vault you mean, the command stops and names all
three instead of guessing.

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

One honest caveat about what you will see:

- On a freshly initialised vault, `doctor` cannot run 4 of its 9 axes
  (`stray_code`, `drift_delta`, `warden_validator`, `hook_health`), because the
  files they need are not there yet. `hook_health` drops off that list if you
  initialise with `--with-hooks`, because it then has a wiring to report on.
  It says so: the verdict is `PARTIAL`, the counts are printed
  separately as `PASSED` / `SKIPPED` / `FAILED`, and each skip names what it
  needs. The exit code stays 0, because a fresh vault is not a broken vault. Use
  `--require-all-axes` if you want a skip to be an error.

## What it is

wulong is a project structure and policy layer that wraps Claude Code with
explicit governance: a review gate an agent calls before it writes code
(contrarian PASS), a smoke-test gate an agent calls before a deploy cycle closes
(tester PASS), an audit trail of change-log lines and causal receipts, and the
rules written down in plain English (the Non-Negotiables in `CLAUDE.md`).

It is not a SaaS tool and it has no server. It is a working system: agent
definitions, policy documents, sync scripts, and hooks that you install into
your Claude Code project and adapt to your context.

> **Closed at 0.3.0.** That sentence used to be true of a git clone only. The
> published 0.1.0 wheel contained 67 files and zero agent definitions, zero
> hooks and no `CLAUDE.md`. The 0.3.0 wheel carries all of them as package data
> under `wulong/payload/`, and `wulong init` installs them, so `pip install`
> and a clone now produce the same vault. See "What ships in the wheel" below.

## Weight

Adopting the engine puts 65 agent-definition files into `.claude/agents/`:
8,409 lines, 522,313 bytes of markdown. Alongside them sits a 44,103-byte
`CLAUDE.md`, which is loaded into every session and re-sent on every turn, so it
is charged against your context window continuously, not once. The figures are
the same whether you clone or `pip install`, because both paths install the same
files. Measure the current weight yourself with
`wc -lc .claude/agents/*.md CLAUDE.md`. Delete the agent definitions you do not
need, and trim `CLAUDE.md` to the rules you actually enforce.

On disk, a `pip install` holds the payload twice from 0.3.0 onward: once inside
the installed package at `wulong/payload/`, and once in the vault that
`wulong init` writes. Disk footprint roughly doubles. Context footprint does
not, because only the vault copy is ever read into a session.

## Architecture

### Engine and overlay

wulong separates public generic code from private personal data:

- **Engine** (tracked in git): agent definitions, sync scripts, hooks, skills,
  Meta skeleton docs, and playbooks. Contains zero personal data.
- **Overlay** (gitignored locally): the files that hold your personal context.
  Each has a `.example` counterpart committed to the repo as a template.

`wulong init` installs the payload (65 agent definitions, 1 hook, 2 skills and
`CLAUDE.md`) and bootstraps the overlay from those templates. It never
overwrites a file that already exists, so re-running it is safe and your edits
survive; pass `--force` when you do want the shipped version back.

### Wiring the hook is opt-in

The payload includes the delivery-gate hook script, but `wulong init` does not
wire it up. Pass `--with-hooks` and init also writes `.claude/settings.json`
pointing Claude Code's `Stop` event at it:

```bash
wulong init /path/to/vault --with-hooks
```

Off by default because what it installs is code that runs on your machine every
time a turn ends, and that should be something you typed rather than something
you received. What wulong contributes is the wiring itself: the right event, a
path that survives `pip install -U`, and a timeout above the hook's real
runtime. Get any of those wrong and nothing happens, with no error to tell you.

Two separate things are true about this, and one sentence cannot carry both.

- **At the project level, hooks are not part of wulong's enforcement chain.**
  The binding gate is `wulong gate` and the test suite, and **wulong ships
  nothing that runs them for you**: no pre-commit, no CI job, no scheduler.
  Wiring any of that into your own workflow is your step, and until you take it
  this repo has no automatic enforcement.
- **At the session level, this particular hook is not passive.** Once wired, it
  **blocks your own delivery** when the last assistant turn contains an em dash,
  and feeds a rewrite reason back to the model. That is a real interruption to
  your own session, which is exactly why it is opt-in.

Every invocation appends one line to `.wulong/hook-events.jsonl`, so a hook that
crashes and gives up leaves a record, and a hook that never fires leaves none.
`wulong doctor` reads that file as axis `hook_health`. Message text is never
written to it.

**To remove it:** delete `.claude/settings.json`, or delete its `"Stop"` entry.
Re-running `wulong init` cannot remove it, because init never clobbers. If
`.claude/settings.json` already exists when you pass `--with-hooks`, init leaves
your file alone, names it, and prints the JSON to merge in by hand.

| Gitignored file | Template | Read by |
|---|---|---|
| `.env` | `.env.example` | all scripts (env knobs) |
| `scrub-patterns.txt` | `scrub-patterns.txt.example` | `scripts/scrub.sh` |
| `Meta/brain.md` | `Meta/brain.md.example` | `compile-context.py`, `drift-scan.py` |
| `.wulong/projects.json` | `.wulong/projects.json.example` | `compile-context.py`, `health-scan.py` |

### The gate model

Two gates structure the workflow. They are conventions the agents follow, backed
by a CLI check the agent runs on itself.

**Gate 1 (contrarian, pre-code):** Before any worker agent writes code, a
contrarian agent reviews the plan. It scores feasibility, hidden assumptions,
blast radius, and cheaper alternatives. A PASS verdict is required before the
coder receives the handoff. A FAIL sends the plan back for revision.

**Gate 2 (tester, post-deploy):** After every deploy, a tester agent runs
smoke tests and issues a PASS or FAIL verdict before the cycle closes.

### What the gate actually proves

`check_gate_precondition.py` (exposed as `wulong gate`) scans a receipts
directory for a receipt matching a `change_id`. Six facts about it, each
checkable in source. A bare line citation is to that file; where a fact lives in
another file, that file is named:

1. A receipt is a file the reviewing agent writes about itself. There is no
   signature, no checksum and no identity check, so any process that can write
   into the receipts directory can mint a PASS (`:155-219`).
2. The frontmatter reader is a line splitter, not a YAML parser, and every tool
   shares one copy of it. On a duplicated key the last value wins
   (`wulong/_frontmatter.py:64-87`), so a `review_verdict: FAIL` line followed by
   a `review_verdict: PASS` line resolves to PASS.
3. It does not check time ordering. A PASS written after the code satisfies the
   gate exactly as well as one written before.
4. A PASS can now carry a `sha256` manifest over the artifacts that were
   reviewed (`wulong/_manifest.py`), and every gate that reads `review_verdict`
   checks it through one predicate in `wulong/_binding.py`. The requirement is
   OFF by default and prints a warning instead, until 0.6.0 on 2027-02-01, so
   today an unbound PASS still authorises anything else carrying its
   `change_id`. The digest stops SUBSTITUTION, which is reviewing plan A and
   shipping plan B, reusing one `change_id`'s PASS for a different artifact, and
   editing an artifact after the PASS was written. It does not stop a rogue
   writer, so the vocabulary stays advisory with attestation rather than
   becoming binding.
5. Fixed in 0.4.0: the default receipts directory used to come from the script's
   own file location rather than from the vault root, so a pip-installed user had
   to pass `--receipts-dir` or the gate REFUSEd everything. It now defaults to
   `<root>/Meta/receipts` via the shared resolver.
6. It fails closed on a blank `change_id`, an unknown gate name, a missing
   receipts directory, an unreadable file, and absent frontmatter.

Three limits of that digest, stated together because each one alone reads better
than the truth. It is UNKEYED, so it attests rather than signs, and fact 1 above
is exactly as true with it as without it: whoever can write the receipt can also
read the artifact and compute the digest. It covers FILE CONTENTS ONLY, so mode
bits and empty directories are outside it and a `chmod +x` is invisible to a
digest that still matches. It binds the MULTISET of contents with no path inside
it, so a rename is invisible, and so is swapping which name holds which content
inside the bound set.

Two fields carry it, `artifact_manifest_sha256` and `artifact_count`, and the
caller names every artifact explicitly. Nothing parses a file list out of the
receipt's prose. The manifest is authoritative for WHAT WAS HASHED and `gated_by`
stays authoritative for graph topology; they are not required to cover the same
files, a `gated_by` predecessor outside the manifest is reported rather than
refused, and where both cover the same file the digest decides.

The gate proves that a file claiming a PASS exists. It does not prove that a
review happened.

### Audit trail

Agents append a line to `Meta/change-log.md` for each file they write:

```
[YYYY-MM-DD HH:MM] agent-name -> ACTION filepath -- one-line summary
```

What is actually checked is narrower than that convention.
`session-close-audit.py` cross-references agents per audit window, not files: for
each non-exempt agent it flags a receipt written with no change-log line in the
window, and a change-log line written with no receipt. It cannot detect a single
unlogged file write by an agent that logged something else in the same window.

Every completed task produces a receipt at
`Meta/receipts/<agent>-YYYY-MM-DD-HHMM-<task>.md` with required fields:
agent, task, date, time, status, and the causal chain (`gated_by`) linking it
to the gate receipts that authorized it.

The `wulong/sync/` scripts validate receipts, walk causal chains, query the
audit history, and enforce session-close checks.

### Agent roster

65 agent definitions are tracked in `wulong/payload/.claude/agents/`, each named
by machine ID (e.g. `jarvis.md`, `contrarian.md`, `coder.md`). That directory is
the single source: it is what the wheel packages and what `wulong init` writes
into your vault's `.claude/agents/`. The `name:` frontmatter field matches the
filename stem so Claude Code routing works without configuration. Adapt the
definitions to your context and drop the ones you do not need.

### What ships in the wheel

The 0.3.0 wheel carries, under `wulong/payload/`, 65 agent definitions, 1 hook,
2 skills and 1 `CLAUDE.md`, alongside the 53 engine scripts in `wulong/sync/`.
CI builds the wheel and asserts those four counts exactly, so a packaging change
that silently drops files fails the build rather than reaching PyPI.

`wulong init` installs the payload but NOT the 53 engine scripts. A second copy
of the engine in your vault would go stale the moment you `pip install -U`, and
because init never overwrites an existing file, the stale copy would win. The
CLI runs the engine from the installed package instead.

## Configuration

There are three ways to say which vault you mean, in this order:

1. `--root /path/to/your/vault` on any of `doctor`, `gate` and `pulse`
2. the `WULONG_ROOT` environment variable
3. running the command from inside the vault, which walks up looking for
   `CLAUDE.md` or `.wulong`

```bash
export WULONG_ROOT=/path/to/your/vault
```

If none of the three answers, the CLI stops and says so rather than guessing. A
guess here points the tool at the wrong vault, and these are the tools that
scan, write and prune.

31 of the 53 governance scripts read this variable, either directly or through
the shared resolver in `wulong/_root.py` that 0.4.0 introduced. Of the other 22,
20 derive their paths from their own file location (`__file__`); the remaining
two never touch `__file__` at all (`check_rename_diff.py` takes its paths as
arguments, `research_router.py` has no path logic). Not all 20 are resolving a
vault root: some take only their own directory or a single file beside it.
`wulong-init.py` is in the 20 and resolves no vault root whatsoever. It names
`WULONG_ROOT` in its help text and never reads it, and the two paths it does
take from `__file__` are its own engine and payload directories, because init
takes the directory to scaffold as its argument.

So `WULONG_ROOT` still is not a single switch across all 53. What 0.4.0 does
guarantee is narrower and more useful: every command you run resolves the root
ONCE and hands it to every process it starts, so a parent and its children can
no longer end up on two different vaults.

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

wulong doctor [--root PATH] [--require-all-axes]
    Run vault health scan via vault-health-check.py.
    Reports PASSED / SKIPPED / FAILED as three separate counts.
    Exits 1 only on FAILED. A skipped axis prints PARTIAL and exits 0,
    unless --require-all-axes is set. A bare vault-path still works, in
    any argument order, and outranks WULONG_ROOT and your current
    directory. --root outranks the bare path.

wulong gate --change-id X --gate {nn3,nn4} [--root PATH]
    Check NN#3/NN#4 gate preconditions for a change_id.
    Receipts default to <root>/Meta/receipts; --receipts-dir overrides.
    Exits 0 (ALLOW) or 1 (REFUSE).
    --require-binding REFUSEs an nn3 PASS that carries no artifact
    manifest digest. OFF by default until 0.6.0; a warning prints
    instead. --legacy-unbound-until YYYY-MM-DD is an ADVISORY
    exemption for older receipts: it reads the date the receipt
    reports about itself.

wulong gate --manifest --artifact PATH [--artifact PATH ...]
    Hash the artifacts you name and print the frontmatter block to
    paste into a receipt. Refuses zero artifacts, a repeated path, a
    directory, a symlink, a missing file and an unreadable one.

wulong gate --verify RECEIPT --artifact PATH [--artifact PATH ...]
    Recompute the manifest from the bytes you name and compare it to
    RECEIPT's recorded digest. It reads no vault, works offline, and
    never resolves the paths recorded in the receipt: those are
    diagnostics for a human and no path is inside the digest.
    Exits 0 (match) or 1 (mismatch or unbound).

wulong pulse [--change-id ID] [--root PATH] [--strict]
    Session-close pulse: verify-change + doc-consistency + audit.
    A RED verdict exits 1 by default. --no-exit-nonzero-on-red restores
    the old log-only behaviour. --strict is separate: it changes what
    counts as failure and how it is labelled, not only the exit code.
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
  blocks; the driver is intentionally out of scope.
- **No built-in LLM calls.** All LLM execution runs through your Claude Code
  session. wulong is the governance layer, not the runtime.

## Install from source

```bash
git clone https://github.com/visrutsuresh/wulong.git
cd wulong
pip install -e .
wulong init myvault
```

## Attribution

wulong is an independent project. It is not affiliated with, endorsed by, or
sponsored by Anthropic PBC. "Claude" and "Claude Code" are trademarks of
Anthropic PBC and are used here only to identify the tool this framework is
built to run alongside. Anthropic provides no warranty or support for this
project, and nothing here is an official Anthropic product.

Third-party material vendored into this repository is listed in
[NOTICE](https://raw.githubusercontent.com/visrutsuresh/wulong/main/NOTICE).

## License

Apache-2.0. See [LICENSE](https://raw.githubusercontent.com/visrutsuresh/wulong/main/LICENSE).

Release 0.1.0 was published under the MIT License and stays MIT permanently. It
has not been yanked. Apache-2.0 applies from 0.2.0 onward.

---

<!-- Star History graph below renders once the repo is public and has stars. -->
[![Star History Chart](https://api.star-history.com/svg?repos=visrutsuresh/wulong&type=Date)](https://star-history.com/#visrutsuresh/wulong&Date)
