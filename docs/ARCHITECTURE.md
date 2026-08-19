# Architecture

## Engine and overlay

wulong separates public generic infrastructure from private personal data.

**Engine** (tracked in git): every file in this repo except the overlay set and
the init output below. Includes agent definitions, sync scripts, hooks, skills,
`CLAUDE.md`, the Meta/ skeleton, and the `wulong` CLI. Contains zero personal
data or credentials. Since 0.3.0 the agent definitions, the hook, both skills
and `CLAUDE.md` are tracked under `wulong/payload/`, not at the repo root, so
that one tracked copy is both what the wheel packages and what `wulong init`
writes.

**Overlay** (gitignored): four files hold the personal context that makes the
engine run against your specific operation. Each has a `.example` counterpart
committed as a template. `wulong init` copies the examples to their real names
and installs the payload alongside them.

**Init output at the repo root** (gitignored, since 0.3.0): running
`wulong init .` inside a clone writes the 69 payload files back to the repo root
as `CLAUDE.md` and `.claude/`. The test suite needs them there, because
`vault-health-check.py` walks up from its own location looking for `CLAUDE.md`
and raises when it finds none. They are gitignored so that staging them cannot
silently restore the double copy that moving the payload removed.

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
  payload/          what `wulong init` installs into your vault (since 0.3.0)
    CLAUDE.md       the governance policy
    .claude/agents/ 65 agent definitions
    .claude/hooks/  1 delivery-gate hook (installed always, wired only by
                    `wulong init --with-hooks`, which generates
                    .claude/settings.json rather than shipping one)
    .claude/skills/ 2 SKILL.md files agent definitions cite by literal path
pyproject.toml      [project.scripts] wulong = "wulong.cli:main"
```

`payload/` is addressed by four explicit `[tool.setuptools.package-data]` globs
rather than one recursive pattern. An explicit key list replaces the setuptools
defaults instead of extending them, and glob does not descend into a directory
whose name starts with a dot unless the dot is spelled out, so
`payload/**/*.md` packaged exactly one file and no agents. CI builds the wheel
and asserts the four counts (65, 1, 2, 1) exactly.

The 53 scripts in `wulong/sync/` are deliberately NOT part of the payload.
A second copy in your vault would go stale on `pip install -U`, and because init
never overwrites an existing file, the stale copy would win permanently.

The CLI dispatches to scripts in `wulong/sync/` via subprocess. Scripts keep
their existing `_THIS_DIR` sibling-import pattern and work by direct path or
via the CLI. This is intentional (Option A in the plan): rewriting 53 files to
true relative imports has no v0.1.0 payoff and is deferred.

## The agent model

65 agent definitions are tracked in `wulong/payload/.claude/agents/`. Since
0.3.0 the wheel packages that directory and `wulong init` installs it into your
vault's `.claude/agents/`, so the clone and the `pip install` produce the same
set. Each is a Markdown file with YAML frontmatter:

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

Two gates structure the workflow (Non-Negotiables 3 and 4 in CLAUDE.md). They are
conventions the agents follow, backed by a CLI check the agent runs on itself:

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

## What the gate actually proves

`check_gate_precondition.py` scans a receipts directory for a receipt matching a
`change_id`. Six facts about it, each checkable in source. A bare line citation
is to that file; where a fact lives in another file, that file is named:

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

## The audit model

Agents append a line to `Meta/change-log.md` for each file they write:

```
[YYYY-MM-DD HH:MM] agent-name -> ACTION filepath -- summary
```

What is actually checked is narrower than that convention.
`session-close-audit.py` cross-references agents per audit window, not files: for
each non-exempt agent (`EXEMPT_AGENTS` at `:51`) it flags a receipt written with
no change-log line in the window, and a change-log line written with no receipt.
It cannot detect a single unlogged file write by an agent that logged something
else in the same window.

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

Scripts import exactly one thing from the `wulong/` package: `wulong._root`,
which holds the single vault-root resolver. Nothing else crosses that line.
Direct path invocation still works, with one condition it did not have before:
`wulong` must be installed in the interpreter running the script. Run
`python3 wulong/sync/validate-receipts.py` from a checkout with no install and
you get `ModuleNotFoundError: No module named 'wulong'`. `pip install -e .` is
enough, and is what CI does.

`_root.py` sits at `wulong/_root.py`, deliberately NOT inside `wulong/sync/`.
A script executed by path gets its own directory as `sys.path[0]`, so
`from wulong._root import ...` can only resolve through the INSTALLED package.
That is the property that makes one resolver possible without any `sys.path`
manipulation, and it is why the file must not be moved into `wulong/sync/`.

> **Fixed in 0.4.0.** In a clean venv, `wulong doctor` and `wulong gate` used to
> resolve the vault root from their own file location, which is `site-packages`.
> `doctor` raised `FileNotFoundError` and `gate` REFUSEd everything. All four
> subcommands now take `--root`, honour `WULONG_ROOT`, fall back to walking up
> from the working directory, and stop with a three-option error rather than
> guessing. The CLI resolves the root ONCE and hands it to the engine process and
> to everything that process starts.

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
