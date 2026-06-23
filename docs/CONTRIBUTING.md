# Contributing

## Dev setup

```bash
git clone https://github.com/<your-github-username>/wulong.git
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
pytest tests/
```

There are three test files:
- `tests/test_imports.py` — mechanical guard that every `wulong/sync/` script
  imports without error. Run after adding or removing a script.
- `tests/test_cli.py` — four tests covering `init`, `doctor`, `gate`, `pulse`.
- `tests/test_scrub.py` — scrub dry-run over `examples/` to verify no
  sensitive patterns slipped in.

Zero skips is the standard. A skip is treated as a failure in CI.

## Before every commit

Run the pre-publish assertion:
```bash
bash scripts/pre-publish-assert.sh
```

This checks:
1. No remote configured (local-only until explicit publish).
2. All commits authored by the pinned pseudonym (`wulong <vault@local>`).
3. Scrub deny-list clean on all tracked files.

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
- `feat:` — new feature or capability
- `fix:` — bug fix
- `chore:` — tooling, config, dependency
- `docs:` — documentation only
- `refactor:` — restructuring without behaviour change
- `data:` — model retrain, CSV update, baseline change

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
