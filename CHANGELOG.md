# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-06-23

### Added

**Phase B: agent definitions**
- 65 genericized agent definitions in `.claude/agents/`, all named by machine
  ID with matching `name:` frontmatter fields.
- Governance roles: jarvis, contrarian, tester, coder, deployer, ar-director,
  keepers, scribe, sorter, doctor, and 55 others.
- All personal paths, personal project references, and Wulong-specific context
  stripped. Definitions describe the role and protocol; adapt to your operation.

**Phase C: engine genericization**
- 53 governance scripts genericized into `wulong/sync/`.
- `WULONG_ROOT` env knob wires every vault-root reference.
- `spawn_gate.py` degrade: works without agent-bus subsystem on a fresh init.
- Scrub passes on all 53 scripts (zero personal literals).

**Phase D: Python packaging + CLI**
- `pyproject.toml`: `pip install wulong` installs the package.
- `wulong` CLI with four subcommands: `init`, `doctor`, `gate`, `pulse`.
- `tests/test_imports.py`: mechanical import-smoke guard for all 53 scripts.
- Minimal deps: PyYAML only (mandatory); scikit-learn optional (`[ml]` extra).

**Phase E: overlay model**
- `.gitignore` block for the four personal-data files.
- `.example` templates for all four overlay files.
- `wulong init` copies templates, skip-if-exists.

**Phase F: usability and portfolio layer**
- Professional `README.md` with logo hero, badges, quickstart, architecture,
  config reference, and honest "What this is NOT" section.
- `examples/01_init_and_doctor.py`: runnable, deterministic, no network.
- `examples/02_gate_check.py`: runnable, deterministic, no network.
- `tests/expected/ex01.txt`, `tests/expected/ex02.txt`: committed expected
  output for CI char-for-char assertion.
- `tests/test_cli.py`: four tests covering all four CLI subcommands.
- `tests/test_scrub.py`: scrub dry-run over examples/.
- `docs/ARCHITECTURE.md`, `docs/USERGUIDE.md`, `docs/CONTRIBUTING.md`.
- `.github/workflows/ci.yml`: runs pytest + both examples with output assertion.
- Placeholder `assets/logo.png` (swap real logo before publishing).

### Not yet included in v0.1.0

- **Telegram bridge**: personal infra (telegram_bridge, telegram_queue,
  loop_driver). Wire your own notification layer.
- **VPS sync**: operator-specific deploy scripts (vps-sync, safe_fetch).
  Deployer agent definition documents the pattern.
- **Autonomous loop driver**: the v3.4 shift engine (autonomy_guard,
  trust_ramp, loop_killswitch) requires a live operator environment.
- **Agent bus subsystem**: the inter-agent coordination bus used by the
  production Wulong operation. spawn_gate degrades gracefully when absent.
- **True package imports**: scripts use `_THIS_DIR` sibling-import pattern
  (Option A). Relative imports across the package are deferred to a future
  named refactor.
