# Contributing

## Dev setup

```bash
git clone https://github.com/visrutsuresh/wulong.git
cd wulong
pip install -e ".[dev]"
```

The `dev` extra adds `pytest`. No other build tools are needed.

Copy the overlay templates:
```bash
cp .env.example .env
cp scrub-patterns.txt.example scrub-patterns.txt
cp .wulong/projects.json.example .wulong/projects.json
cp Meta/brain.md.example Meta/brain.md
```

Edit `scrub-patterns.txt` to add your own personal tokens. This file is
gitignored and is never committed.

## Running the tests

```bash
python -m wulong.cli init .   # required once per clone, see below
pytest tests/
```

`wulong init .` is a prerequisite for running the suite from a fresh clone.
Since 0.3.0 the agent payload lives in `wulong/payload/` and is no longer
tracked at the repo root, and `vault-health-check.py` raises when it cannot find
a `CLAUDE.md` by walking up from its own location. Without the init step,
`tests/test_cli.py::test_doctor` fails and `examples/01_init_and_doctor.py`
exits 1 with an uncaught traceback. The files init writes back to the repo root
are gitignored, so they cannot be committed by accident.

There are eleven test files:
- `tests/test_imports.py`: mechanical guard that every `wulong/sync/` script
  imports without error. Run after adding or removing a script.
- `tests/test_cli.py`: four tests covering `init`, `doctor`, `gate`, `pulse`.
- `tests/test_scrub.py`: scrub dry-run over every git-tracked file to verify no
  sensitive patterns slipped in, plus fixture tests that prove the deny-list
  scan actually fires and that each tag exempts only the check it names.
- `tests/test_gate.py`: ALLOW/REFUSE behaviour of the NN#3/NN#4 gate check,
  including the documented trust-boundary limitations.
- `tests/test_frontmatter.py`: the frontmatter reader, including the 4096-byte
  read window past which the closing delimiter is lost.
- `tests/test_verify_change.py`: the D3/D4 receipt checks.
- `tests/test_session_pulse.py`: pulse exit codes, default and `--strict`.
- `tests/test_doc_claims.py`: every number this repository publishes about
  itself is parsed out of the document and measured from disk.
- `tests/test_wheel_payload.py`: builds a wheel and asserts it carries exactly
  65 agent definitions, 1 hook, 2 skills and 1 `CLAUDE.md`.
- `tests/test_init_payload.py`: what `wulong init` installs, and that it aborts
  rather than overwriting a file it cannot stat.
- `tests/test_no_doubled_scripts.py`: no engine script exists in both
  `Meta/sync/` and `wulong/sync/`.

Zero skips is the standard, and CI enforces it: the workflow sets
`WULONG_REQUIRE_NO_SKIPS=1`, and `tests/conftest.py` turns any skipped test into
a non-zero exit. Locally the variable is unset, so an environment-dependent skip
(no `build` module, a filesystem that denies `stat()` on dotfiles) still lets you
work. It must never reach CI: the six wheel tests in `tests/test_wheel_cli.py`
skip themselves when a wheel cannot be built, and they are the only tests that
reproduce a `pip install wulong` user, so a silent skip there is a false green.

## Before every commit

Run the pre-publish assertion:
```bash
bash scripts/pre-publish-assert.sh
```

This checks:
1. The `origin` remote is the expected publish target
   (`github.com/visrutsuresh/wulong`).
2. No commit author name or email matches a pattern in the scrub deny-list,
   ignoring lines that lead with `[allow-author]` (see the DCO section below).
3. Scrub deny-list clean on all tracked files, ignoring lines that lead with
   `[allow-public]`.

All three must pass. Never commit if any check fails.

## Scrub discipline

`scripts/scrub.sh` scans a path against `scrub-patterns.txt`. Run it any time
you are unsure about a file:

```bash
bash scripts/scrub.sh examples/
bash scripts/scrub.sh docs/
```

The patterns file is yours to maintain. It is gitignored. The `.example`
template ships with a set of placeholder patterns to model yours on.

## Adding a script to `wulong/sync/`

1. Write the script. It must work standalone (direct `python3 script.py --help`
   runs without error).
2. Honor `WULONG_ROOT` for any vault-root references.
3. Add the script's name (without `.py`) to the `MODULES` list in
   `tests/test_imports.py` in the same commit.
4. Run `pytest tests/` to confirm the import-smoke test passes.
5. Run `bash scripts/pre-publish-assert.sh`.

## Adding an agent definition

Agent definitions live in `.claude/agents/`. Each file:
- Is named `<machine-id>.md` (lowercase, hyphen-separated)
- Has `name: <machine-id>` in YAML frontmatter (must match filename stem)
- Has a `## Rules` section and a `## SOP` section at minimum

Model new files on the existing ones. The `ar-director.md` definition
describes the hire-agent playbook in its `## SOP` section.

## Commit style

Use conventional prefixes:
- `feat:` new feature or capability
- `fix:` bug fix
- `chore:` tooling, config, dependency
- `docs:` documentation only
- `refactor:` restructuring without behaviour change
- `data:` model retrain, CSV update, baseline change

Keep commits small and focused. One logical change per commit.

## Pull requests

Before opening a PR:
1. `pytest tests/` passes with zero failures and zero skips.
2. `bash scripts/pre-publish-assert.sh` exits 0.
3. Both examples (`python3 examples/01_init_and_doctor.py`,
   `python3 examples/02_gate_check.py`) print the expected output and exit 0.
4. CI badge is green on the PR branch.

PRs that add new governance scripts should include a corresponding example or
test that actually runs the script. Stubs that do not execute are rejected.

## Licensing of contributions

wulong is licensed under Apache-2.0 from 0.2.0 onward. Inbound equals outbound:
unless you state otherwise in writing, any contribution you submit is submitted
under the terms of Apache License 2.0 section 5, and is licensed to the project
and its users under Apache-2.0. No copyright assignment is requested and no CLA
is required.

## Developer Certificate of Origin

Every commit must be signed off under the Developer Certificate of Origin 1.1
(https://developercertificate.org). Sign off with:

```bash
git commit -s -m "fix: your message"
```

That appends a line to the commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

By signing off you certify the DCO: you wrote the change, or you have the right
to submit it under the project licence, and you understand the contribution and
your sign-off are public and permanent.

Use your real name. `git commit -s` uses `user.name` and `user.email` from your
git config, so set those before your first contribution. Add your name to
`AUTHORS` in the same commit as your first accepted change; name only, no email
address, because that file is published.

If your name is also a pattern in your local `scrub-patterns.txt`, put an
`[allow-author]` sigil at the FRONT of that line. Otherwise the pre-publish
author check fails on your own sign-off, and the two rules on this page
contradict each other. The tag affects the author check only: the file scan
still enforces your name inside tracked files. If your name legitimately belongs
in a published file (a copyright line, a repo URL), add `[allow-public]` to the
same line as well. See the deny-list section of `SECURITY.md` for both tags and
for the migration if your file still carries the tag in its comment.

Your email is not covered by the tag. If you do not want a personal address
published, set `user.email` to your GitHub noreply address (the `ID+username`
form at `users.noreply.github.com`, shown under Settings, Emails).
`git commit -s` takes both the author field and the sign-off trailer from your
git config, so setting it once covers both. This project's own commits use that
form.
