# Claims Audit

A line-by-line audit of the factual claims this repository makes about itself, run before the 0.2.0 release.

**Which release this ships in.** The audit was run against a working version numbered 0.2.0. That version was never uploaded to PyPI. Every correction recorded below, plus the later B5 and C work, ships in **0.3.0**. `CHANGELOG.md` keeps the `[0.2.0]` entry as a record of the work block, and "in 0.2.0" below means "in that work block", not "in a published release". Only 0.1.0 has ever been published.

Every claim below was checked against the code, not against the previous documentation. Each row carries one of four states:

| State | Meaning |
|---|---|
| **KEEP** | Verified true as written. No edit. |
| **FIX** | Verified false or misleading. Corrected in 0.2.0. |
| **DELETE** | Not testable, or asserts an enforcement guarantee the code does not provide. Removed in 0.2.0. |
| **DEFERRED** | Known false, and left false at 0.2.0 because a later change owns the fix. Every DEFERRED row names that change. |

The DEFERRED state exists so that a reader can tell a vetted-true line from a known-false line that is waiting on work. If a claim is not listed here, it was not audited.

Line numbers refer to the files as they stood before the 0.2.0 edits.

## KEEP

| Location | Claim | Why it stands |
|---|---|---|
| `CHANGELOG.md:21` | "Scrub passes on all 53 scripts (zero personal literals)." | True. `scripts/scrub.sh` was run over the packaged scripts and returned clean. The related defect was in the *test*, not the claim: `tests/test_scrub.py:25` only scrubbed `examples/`, so nothing would fail if this became false. That gap was closed by B5; see CLOSED below. |
| `docs/ARCHITECTURE.md:7` | "**Engine** (tracked in git): every file in this repo except the overlay set." | True when audited: this is a claim about git tracking, and `git ls-files .claude` returned 68. The published wheel shipping zero agent files did not falsify it, because the claim is not about the wheel. **Superseded by C1 and rewritten.** C1 moved the payload to `wulong/payload/` and gitignored the repo-root `CLAUDE.md` and `.claude/`, so those two paths now exist in a clone as init output that is neither engine nor overlay. `git ls-files .claude` now returns 0 and `git ls-files wulong/payload` returns 69. The sentence now names that third category explicitly. |
| `README.md:42-43` | "**Engine** (tracked in git): agent definitions, sync scripts, hooks, skills, Meta skeleton docs, and playbooks." | True, and still true after C1: the agent definitions, hook and skills are tracked, at `wulong/payload/` rather than at the repo root. The neighbouring sentence describing what `wulong init` does was too narrow after C4 and was widened; see the C rows in CLOSED. |
| `docs/ARCHITECTURE.md:114-120` | Import topology: `wulong/sync/` scripts are stdlib plus optional PyYAML, and no script imports from `wulong/`. | True when audited: the dependency set and the import direction were both as described, and only the conclusion drawn from it at `:120-121` was false, which is split out as a separate DEFERRED row. **Superseded by D and rewritten.** D introduced `wulong/_root.py`, and 15 `wulong/sync/` scripts now import `wulong._root`, so the no-script-imports-from-`wulong/` half is false as of 0.4.0. The dependency half (stdlib plus optional PyYAML) still stands. `docs/ARCHITECTURE.md` now states the new import direction explicitly, names the one module that crosses the line, and says why it sits outside `wulong/sync/`. |

## FIX

| Location | Claim as it stood | Correction in 0.2.0 |
|---|---|---|
| `README.md:2` | `assets/logo.png`, a repository-relative image path. | Rewritten to an absolute `raw.githubusercontent.com` URL so it renders on PyPI. |
| `README.md:12` | `License: MIT` badge, linked to the relative path `LICENSE`. | Badge reads Apache-2.0 and links to an absolute `raw.githubusercontent.com` URL. |
| `README.md:19-20` | Quickstart runs a bare `wulong doctor`. | Quickstart passes the vault path explicitly (`wulong doctor myvault`), which is the form that actually works from a pip install. See the DEFERRED row for the bare form. |
| `README.md:74` | "Every agent that writes or edits a file appends a line to `Meta/change-log.md`." | Reworded to what the code checks. `session-close-audit.py:354-395` cross-references per agent per audit window, not per file: it flags a non-exempt agent that wrote a receipt with no change-log line in the window, and vice versa. It cannot detect a single unlogged file write by an agent that logged something else. |
| `README.md:90` | "65 agent definitions ship in `.claude/agents/`." | Scoped, then closed. The 0.2.0 wording said 65 definitions are tracked in the git repository and the published wheel contains zero. C1 made the scoping unnecessary: the path is now `wulong/payload/.claude/agents/`, the 0.3.0 wheel carries all 65 and `wulong init` installs them, so the original unscoped claim became true by fixing the code. Found by sweeping for the twin of the `docs/ARCHITECTURE.md:41` claim below. |
| `README.md:161` | "MIT. See [LICENSE](LICENSE)." | Apache-2.0, with an absolute link. |
| `docs/ARCHITECTURE.md:41` | "65 agent definitions ship in `.claude/agents/`." | Scoped to the git repository at 0.2.0, with the wheel stated explicitly. Closed by C1 on the same reasoning as `README.md:90`. |
| `docs/ARCHITECTURE.md:95` | "Every agent that writes or edits a file appends a line to `Meta/change-log.md`." | Reworded to the per-agent-per-window detector, same as `README.md:74`. |
| `docs/USERGUIDE.md:70` | "Most governance scripts read `WULONG_ROOT`." | Counted: 17 of 53 scripts read it. That is a minority, not most. The text now gives the number. **Left as recorded.** The 17 was counted by MEMBERSHIP (the string `WULONG_ROOT` appears in the file), which over-counts by one because `wulong-init.py` names the variable and never reads it, so the true figure at the time was 16. The row records what the audit found, and is not edited in place. C0 moved the live claim to 23 of 53 counted by an actual environment read, and `tests/test_doc_claims.py` now enforces that stricter predicate. |
| `CHANGELOG.md:19` | "`WULONG_ROOT` env knob wires every vault-root reference." | False (17 of 53). This line sits inside the released `## [0.1.0]` entry, and 0.1.0 is live on PyPI, so it is **not** edited in place. Changelog history is a record of what was published, and rewriting it would make the file disagree with the artifact. The 0.2.0 entry carries a "Corrected" line naming the 0.1.0 line and the true number. |
| `SECURITY.md:40` | "No git remote is configured (no accidental push to a public remote)." | Already false before this change: `origin` points at the public GitHub repository. Rewritten to describe what the check now does. |
| `SECURITY.md:41-42` | Commits are authored under the pinned pseudonym `wulong / vault@local`, "not a personal identity". | Removed. The project is now attributed to a named author in `AUTHORS`, so a rule mandating a pseudonym contradicts it. |
| `scripts/pre-publish-assert.sh` check (a) | Fails if a git remote exists. | The remote exists and is intentional, so this check could never pass again. Replaced with a check that the remote is the expected publish target. |
| `scripts/pre-publish-assert.sh` check (b) | Fails any commit not authored by `wulong <vault@local>`. | Replaced with a check that no commit carries an author or email listed in the scrub deny-list. Attribution is now a stated goal, so pinning a pseudonym works against it. |
| `wulong/cli.py:59` | `wulong --version` printed the hardcoded string `wulong 0.1.0`. | Reads the installed distribution version through `importlib.metadata`, so it can no longer drift from `pyproject.toml`. |

## DELETE

These lines asserted guarantees that nothing in the codebase enforces. They are removed rather than softened, because a hedged version of an unenforceable guarantee is still an unenforceable guarantee.

| Location | Claim | Why it goes |
|---|---|---|
| `README.md:7` | "every change reviewed, every deploy verified, every decision logged" | Three universal guarantees. The gate checks that a file claiming a PASS exists, and the audit script samples per agent per window. Neither establishes "every". |
| `README.md:58` | "Two gates are binding on every code change" | Nothing binds them. The gate is a CLI an agent may call. It has no ability to prevent a change that never calls it. |
| `README.md:63-64` | "A FAIL triggers a parallel fix loop (up to 3 rounds) before escalating to the human." | Describes a process convention as if it were a mechanism. No code counts rounds or escalates. |
| `README.md:69-70` | "Together: no unreviewed change lands, and no deploy closes without a verified working state." | The strongest claim in the file and the least supported. See `docs/ARCHITECTURE.md`, "What the gate actually proves". |
| `docs/ARCHITECTURE.md:63` | "Two gates are binding on every code change" | Same as `README.md:58`. |
| `docs/ARCHITECTURE.md:90-91` | "This is the mechanical enforcement: a coder that checks its own spawn condition before writing any code." | Self-refuting on inspection. A check an agent runs on itself is a convention, not an enforcement. Replaced by the honest trust-model section. |
| `docs/CONTRIBUTING.md:47` | "No remote configured (local-only until explicit publish)." | False since publication. |
| `docs/CONTRIBUTING.md:48` | "All commits authored by the pinned pseudonym (`wulong <vault@local>`)." | Contradicts the new `AUTHORS` file. |

## DEFERRED

Known false or incomplete at 0.4.0. Each row names the change that owns the fix. None of these is fixed by this release, and none is presented as true in the shipped documentation. Rows a release does fix move to a CLOSED section rather than being deleted.

| Location | What is false or incomplete | Owning change |
|---|---|---|
| `WULONG_ROOT` coverage | 31 of the 53 scripts read it. The other 22 do not: 20 derive their paths from `__file__` (not all of them a vault root) and 2 never touch it. What 0.4.0 guarantees is narrower, that each command resolves ONCE and hands the answer to every process it starts. The docs state the narrower claim. | **E** |
| Platform support | 11 scripts import `fcntl` at module top level, so they do not import on Windows. Nothing in the docs claims Windows support, but nothing states the exclusion either. | **E** |
| NN#3 gate strength | The gate proves a receipt claiming a PASS exists. A PASS can now carry a `sha256` manifest over the artifacts reviewed and every `review_verdict` gate checks it, but the requirement is OFF by default until 0.6.0 on 2027-02-01, so an unbound PASS still passes today with a warning. The digest is unkeyed, so it stops substitution and not forgery. Still open and unchanged: no time-ordering check, and a frontmatter reader that is a line splitter rather than a YAML parser. Documented in `ARCHITECTURE.md`, listed here because these are real limits rather than fixed ones. | **E** |
| `verify-change.py` shell invocation | One site still shells out where a direct call would do. Not a correctness bug, an audit-surface one. | **E** |

## CLOSED in 0.4.0 (Change D)

Line numbers below refer to the files as they stood BEFORE the 0.4.0 edits.

| Location | What was false | How D closed it |
|---|---|---|
| `docs/ARCHITECTURE.md:120-121` | `wulong doctor` and `wulong gate` did not work in a clean venv: both resolved the vault root from `__file__`, which is `site-packages`. | All four subcommands take `--root`, honour `WULONG_ROOT`, then walk up from the working directory, then raise a named error. Asserted from an installed wheel outside any checkout in `tests/test_wheel_cli.py`. |
| `docs/USERGUIDE.md:94` | "Set `WULONG_ROOT` or pass `vault-path`." Only the second half worked. | `vault-health-check.py` reads the root through the shared resolver. |
| `docs/USERGUIDE.md:106-107` | The gate's default receipts path was not derived from `WULONG_ROOT`. | It is now `<root>/Meta/receipts`. |
| `wulong init` output | Init told the user to set a variable `doctor` then ignored. | `doctor` honours it. |
| Bare `wulong doctor` | Needed a vault root it could not find from a pip install. | Three named ways to supply one, and a clear error when none is given. |
| `wulong doctor` exit status | Skipped 3 of 8 axes on a fresh vault and printed "all checks passed". | Three separate counts and a distinct `PARTIAL` verdict. `GREEN` is emitted only when all nine ran. |
| `wulong pulse` exit status | Printed `ACTION REQUIRED` and exited 0 unless `--strict` was passed. | Exits 1 by default; `--no-exit-nonzero-on-red` opts out. |
| `session-guard` vs `session-start-gate` | Could address different session registries. | One resolver, one registry. |

## CLOSED in 0.3.0

A row leaves DEFERRED only when the change that owned it lands. It is recorded here rather than deleted, so a reader can tell a closed gap from one that was quietly dropped. Every row below closes in the 0.3.0 release; the section used to be headed "after 0.2.0", which named a version that was never uploaded.

| Location | What was false | Closed by |
|---|---|---|
| `README.md:32-34` | "agent definitions, policy documents, sync scripts, and hooks that you install into your Claude Code project". True of a git clone. The published 0.1.0 wheel contains 67 files and zero agent definitions, zero hooks and no `CLAUDE.md`, so a `pip install` gave you none of it. | **C**. The payload moved to `wulong/payload/` as the single tracked copy, four explicit package-data globs put it in the wheel, and `wulong init` installs it. CI builds the wheel and asserts the counts exactly (65 agents, 1 hook, 2 skills, 1 `CLAUDE.md`), so a silent drop fails the build instead of reaching PyPI. The 53 engine scripts are deliberately not installed into the vault. |
| `wulong/sync/wulong-init.py:134` | The never-clobber contract rested on `Path.exists()`. `Path.exists()` delegates to `os.path.exists()`, which swallows every `OSError` and returns `False`, so a filesystem that could not answer read as "not there" and init WROTE, destroying the user's `.env` (which holds `WULONG_ROOT` and `GITHUB_TOKEN`) or their scrub deny-list. Not found by the audit; found by C4 review. | **C4**. `_exists_or_fail()` calls `os.lstat` directly and treats only `ENOENT` and `ENOTDIR` as absent. Anything else raises `ClobberRisk` and init aborts before writing. `tests/test_init_payload.py` proves the abort with `ENAMETOOLONG`, which is deterministic on every POSIX filesystem and needs no `chmod`, so the test is not a silent no-op when the suite runs as root. A second test asserts all four overlays skip on a re-run; the suite previously asserted it for one, and the two unasserted ones were the two holding secrets. |
| `scripts/pre-publish-assert.sh` check (c) | The only check that blocks a push excluded `.github/` from its scan. Harmless while the scan was inert, because every deny-list pattern carried its trailing comment into `grep` and therefore matched nothing. B5 made the scan live and turned the exclusion into a real blind spot over the one directory where CI tokens live. | **C**, folded from the B5 output review. The `.github/` exclusion is removed from check (c) and was deliberately NOT copied into `scripts/scrub.sh`. A pattern that false-positives on a runner name like `ubuntu-latest` needs a word boundary, not a directory the scanner skips. `SECURITY.md` no longer calls the two scanners equivalent; it states the one real difference, which is tracked files versus a directory walk. |
| `tests/test_scrub.py:25` | The scrub test passed only `examples/` (2 files) to `scripts/scrub.sh`, so `CHANGELOG.md:21` ("scrub passes on all 53 scripts") was true but protected by no test and could become false silently. Worse, the scan it invoked could not fail at all: both `scripts/scrub.sh` and check (c) passed each deny-list line to `grep` with its inline trailing comment attached, and every live pattern carries one, so every pattern was a regex matching nothing. | **B5**. Both scanners now strip the comment, tags moved to a leading sigil, `[allow-public]` was added as a separate file-scan exemption so `[allow-author]` keeps its documented meaning, and `tests/test_scrub.py` widened to every git-tracked file plus fixture tests that prove the scan fires and that each tag exempts only its own check. |

## Sweeps run with zero hits

Recorded so that a later reader can see the search happened rather than assuming it did.

- Comparative marketing claims against other frameworks (`ruflo`, `claude-flow`): zero occurrences in tracked files. Nothing to remove.
- Harness boilerplate beyond the single pre-registered line handled in the 0.2.0 release: a probe of twelve candidate phrases across all 65 agent files returned zero matches for eleven of them. No open-ended sweep was run, because an open-ended sweep over prose can only produce invented removals.

## Method

- Counts (53 scripts, 17 readers of `WULONG_ROOT`, 65 agent files, 68 git-tracked files under `.claude/`, 67 files in the published 0.1.0 wheel) were measured, not carried over from earlier documents. Any count of a wheel names the release it was taken from, because the 0.1.0 and 0.3.0 wheels differ.
- The 17 readers figure is left as recorded and counted by MEMBERSHIP of the string `WULONG_ROOT`, on the same principle as the 68 above. Membership over-counts by exactly one: `wulong-init.py` names the variable in its docstring and prints it, and never reads it, so the true reader figure at the time of this audit was 16. C0 tightened the convention: the live claim in `README.md` and `docs/USERGUIDE.md` is now 23 of 53 counted by an actual `os.environ` read, and the predicate in `tests/test_doc_claims.py` was changed from substring membership to an AST match so the two conventions cannot be confused again.
- The 68 figure is the pre-C1 measurement and is left as recorded, because it is what the audit found. C1 moved those 68 files plus `CLAUDE.md` into `wulong/payload/`, so at 0.3.0 `git ls-files .claude` returns 0 and `git ls-files wulong/payload` returns 69. The 69 is asserted against the BUILT wheel by `tests/test_wheel_payload.py`, not against the tree, because 0.1.0 shipped a wheel with zero agent files while all 65 sat in the tree the whole time.
- Behavioural claims were reproduced by running the code, not by reading it.
- Claims about the published artifact were checked against a built wheel, not against the source tree, because the two differ and that difference is the source of several rows above.
