# Changelog

All notable changes to this project will be documented in this file.

Released entries are a record of what was published and are not edited in place.
Corrections to a released entry appear as a "Corrected" item in a later entry.

## [Unreleased]

Not released. `pyproject.toml` and `wulong/__init__.py` both still read 0.4.0.

### Added

- **`wulong init --with-hooks` wires the delivery-gate hook, and nothing wires it
  otherwise.** The hook script has shipped in the payload since 0.3.0 and no
  settings file has ever existed anywhere in this repo, so it has never run for
  anyone who did not write one by hand. The flag generates
  `.claude/settings.json` at init time rather than shipping one as payload data,
  which keeps the payload set at exactly 69 files and leaves every published
  payload count untouched. It also keeps the file conditional: the payload is
  installed unconditionally, so a settings file delivered through it could not
  be opt-in without special-casing the very invariant
  (`tests/test_wheel_payload.py::test_init_installs_exactly_what_the_wheel_ships`)
  that exists to stop the 0.1.0 packaging bug recurring.

  The written file names the `Stop` event, the command
  `python3 <vault>/.claude/hooks/stop-slop-hook.py`, and a 10 second timeout. An
  interpreter rather than a bare path because the hook is mode 0644 in the repo
  and in the payload, init copies bytes and not modes, and wheels do not reliably
  preserve the exec bit on package data. No `matcher`, because a matcher selects
  a tool name and `Stop` carries none.

  Off by default: what it installs is code that runs on your machine every time a
  turn ends. Removing it is deleting that file or its `"Stop"` entry; init cannot
  remove it, because init never overwrites.

- **An existing `.claude/settings.json` is now named, not counted.** init never
  clobbers, so a user who already had that file (anyone who has configured
  Claude Code permissions) would have typed `--with-hooks`, seen nothing about
  it, and got no hook. The collision now prints `HOOK NOT WIRED`, the full path,
  and the exact JSON to merge into their own `"hooks"` object.

- **`.wulong/hook-events.jsonl`, one appended JSON line per hook invocation.**
  Every path in the hook that lets delivery through is a bare `return`, so a
  hook that fails open emits nothing at all and hook stderr is transient anyway.
  The record
  carries a UTC timestamp, the hook name, the event, the outcome, and on an
  internal error the exception class. It carries the em dash HIT COUNT and never
  the message text.

  A record is written on the ordinary allow too, and that heartbeat is the point:
  no records at all means the hook has NEVER FIRED, which is what a stale path or
  a kill by timeout looks like, while `allow` records mean it fired and had
  nothing to do. Without it those two are the same empty log. A wrong event is
  the third state and it is not silence: the hook reads the incoming
  `hook_event_name` and records the name it actually received, so a mis-wiring
  reads as itself rather than as a healthy log over a dead gate. The
  hook writes nothing unless a `.wulong/` directory already exists, so a
  mis-resolved root leaves no trace rather than scattering one.

- **The hook refuses an event it does not handle.** `main()` reads
  `hook_event_name` and returns without scanning when that name is present and is
  not `Stop`, recording the event it actually received. Until this, the script was
  event-agnostic and the guarantee was published anyway: `transcript_path` is a
  field every Claude Code payload can carry, not a Stop-only one, so a settings
  entry pointing another event at this script scanned that event's turn and
  emitted a Stop-shaped block decision on it, logged as `Stop`. An ABSENT name
  still runs, because hand-fed payloads and older runners omit the field and
  silently disabling the gate for them is the worse failure. `wulong doctor` axis
  I names the mis-wired event back to you, so a wrong wiring reads as itself
  instead of as a healthy log over a gate that never ran.

- **Ninth `wulong doctor` axis, `I hook_health`.** Reports never-fired, and
  fail-open counts by exception class over the last 7 days. It SKIPS when the
  hook is not wired, because skipping is neither a pass nor a failure and a red
  cross over a deliberate decline is how a health check teaches people to ignore
  it. Measured on a fresh vault: a default `wulong init` now skips four of the
  nine axes (B, G, H, I) and `--with-hooks` skips three.

- **`tests/test_hook_wiring.py`, 34 cases collected by pytest** from 31 test
  functions, one of them parametrised into four. The quoted number is what
  `pytest --collect-only -q` reports, because that is what actually runs. The
  first number published here was 25, which was the count before the last two
  tests were written and nothing measured it again. The event the settings file names is checked against the
  hook's own module-level `HOOK_EVENT` read by AST, not against its docstring,
  because a docstring stays green while the parsing underneath moves. Behaviour
  is proved by running the real script: a `Stop` payload blocks on stdout with
  empty stderr and exit 0, and a payload naming another event no-ops even when it
  carries a transcript that would block, which is what makes the check real,
  since `transcript_path` is a field every Claude Code event can carry. The
  vocabulary check runs in BOTH directions and is red under mutation in both.

- **`tests/test_examples_match_goldens.py`, 3 cases.** Every example runs inside
  pytest and its stdout is diffed against its committed golden. That diff used to
  exist only as a CI step, and the cost of the split was measurable: replacing
  `ignored = _ensure_gitignore(target)` in `wulong-init.py` with `ignored = []`
  left the full suite GREEN and reddened only the CI golden, while `wulong init`
  under that mutation wrote the secrets overlay into the target with no
  `.gitignore` created and therefore committable. That mutation now reds pytest.
  CI keeps its own copy of the step: two runners that disagree is information.

- **A test that no shipped doc publishes a stale axis count.** The existing one
  read only the CLI description string inside `vault-health-check.py`, and
  `docs/USERGUIDE.md` shipped "Eight axes:" over a list of eight, nine lines above
  a table saying "all nine axes ran". The sweep that was meant to catch it was
  case-sensitive and returned zero hits on the prose form. The new test measures
  the count from a real run and checks every live claim in the docs against it,
  number words included. Released CHANGELOG entries and `docs/CLAIMS-AUDIT.md` are
  out of scope by design and the reason is written into the test.

### Changed

- **`Meta/sync/cerebrum-health.py` stopped printing three red crosses at a
  correctly installed vault.** It demanded `UserPromptSubmit` and `PostToolUse`
  in a settings file wulong writes neither of, so a user who accepted the opt-in
  got a cross and one who declined got a cross for a deliberate choice. It now
  lists the events actually wired, and reports the two vault-local scripts as
  absent rather than wrong, because `wulong init` does not install them and never
  claimed to.

- **A review PASS can be bound to the artifacts it reviewed, and every gate that
  reads `review_verdict` checks the binding through one predicate.** Fourteen
  comparison sites across four scripts now call `verdict_is_binding_pass` or
  `binding_ok` in `wulong/_binding.py`: `check_gate_precondition.py` (nn3),
  `automerge_gate.py` (plan and output), `validate-receipt-graph.py` (three
  functions, five sites) and `judge-score.py` (one chokepoint feeding six
  comparisons). `spawn_gate.py` delegates to the nn3 oracle and inherits it
  without a change. What the digest stops is SUBSTITUTION: reviewing plan A then
  shipping plan B, reusing one `change_id`'s PASS for a different artifact, and
  editing an artifact after the PASS was written. It does not stop a rogue
  writer, because the hash is unkeyed, so the vocabulary stays advisory with
  attestation and facts 1, 2 and 3 of "What the gate actually proves" survive
  unchanged. Only fact 4 moves, and only once binding is required.
- **Three limits published beside it rather than one at a time.** The digest is
  unkeyed. It covers file contents only, so mode bits and empty directories sit
  outside it and a `chmod +x` is invisible. It binds the MULTISET of contents
  with no path inside it, so a rename is invisible and so is swapping which name
  holds which content inside the bound set.
- **The migration is a mechanism, not a promise.** `--require-binding` defaults
  OFF and an unbound PASS prints a bounded warning instead of failing. The flip
  to ON at 0.6.0 is dated **2027-02-01**, that date is a CONSTANT IN CODE at
  `wulong/_binding.py`, and `tests/test_binding.py` goes red once it passes while
  the default is still off. CI runs pytest with `WULONG_REQUIRE_NO_SKIPS=1` and
  `tests/conftest.py` turns any skip into a session failure, so the tripwire
  cannot be neutered with a skip marker. One hole, stated rather than hidden: if
  nobody commits again, CI never runs and the tripwire never fires.
  `--legacy-unbound-until` is documented as ADVISORY throughout, because it keys
  off the date a receipt reports about itself.
- **The manifest hashes CONTENT ONLY and the caller enumerates.** No path is
  inside the digest, which removes the anchor question entirely. The receipt's
  own Files-written list is NOT the enumeration source: its only parser,
  `verify-change.py` `_extract_files_written`, documents itself fail-open on
  prose, so feeding it to a fail-closed digest would produce a receipt attesting
  to files the manifest never hashed. Zero artifacts, a path supplied twice, a
  directory, a missing path, a symlink and an unreadable file each raise a named
  refusal. Two distinct artifacts with identical bytes emit two identical lines,
  deliberately, so the digest binds the count as well as the contents.
- **A refusal says WHY it refused, and never calls a PASS a FAIL.** Widening the
  condition without widening the reason makes a gate report an unbound PASS as a
  FAIL verdict, which is the opposite of what the receipt says. Every refusal
  reason an unbound PASS can now reach branches on the cause: NN#3 and both
  NN#10 arms in `validate-receipt-graph.py`, the nn3 REFUSE in
  `check_gate_precondition.py`, both REFUSEs in `automerge_gate.py`, and the two
  score deductions in `judge-score.py`. The rule each of them follows is that the
  explanation makes the SAME comparison as the decision it explains, so the two
  cannot disagree: `reads_pass` in `wulong/_binding.py` serves the three scripts
  that decide with `verdict_is_binding_pass`, and `judge-score.py` keeps its own
  `_reads_pass` because its decision uppercases the field first and a stricter
  explanation there could contradict a looser decision. Measured by running the
  validator over the author's own 6,635-receipt, 1,102-change_id vault on
  2026-08-19, with the requirement ON:
  before, 308 NN#3 messages asserted `review_verdict=FAIL` about receipts that
  read PASS and 981 NN#10 messages asserted that no PASS existed when one did;
  after, both are 0. That corpus only grows, and it grows in the direction that
  makes the numbers larger, because almost every receipt in it predates the
  binding fields. Violation counts, codes and sub-codes are byte-identical
  before and after in both the ON and OFF arms: this moved text, not verdicts.
- **`gated_by` and the manifest answer different questions.** The manifest is
  authoritative for what was hashed, `gated_by` for graph topology. They need not
  be co-extensive: a `gated_by` predecessor outside the manifest is REPORTED and
  never refused, and where both cover the same file the digest decides.
- **One frontmatter reader, `wulong/_frontmatter.py`.** Eight scripts each
  carried their own copy of the same line splitter, and four of them decide a
  governance verdict from what it returns, so changing how a receipt is read
  meant making the same edit eight times or leaving the gates disagreeing about
  what a receipt says. Three of the eight had already drifted: `judge-score.py`
  coerced a `[a, b]` value into a Python list, `query-receipts.py` lowercased
  keys and stripped quotes, and `validate-receipts.py` returned `None` where the
  others returned `{}`. Those three keep named wrappers at their own call sites,
  so none of their behaviour changed. The module sits at the package root beside
  `_root.py` for the same reason that one does: a script executed by path gets
  its own directory as `sys.path[0]`, so the import resolves only through the
  installed package.
- **The read window stayed at the call sites.** `check_gate_precondition.py`,
  `automerge_gate.py` and `session-close-audit.py` read the first 4096 bytes of a
  receipt and the other five read the whole file. The shared reader takes text
  and never opens a file, so that split is unchanged, and it is now asserted at
  4090 and 4102 bytes in both directions.
- **`judge-score.py` now requires the installed package.** It was the last script
  in `wulong/sync/` that ran standalone from a raw checkout; the other eight
  already imported `wulong._root`, directly or through the script they import.
  Measured rather than assumed: every touched tool was run as `python3 <path>`
  with the package importable and with it blocked, before and after, and this is
  the only difference in either direction.

### Fixed

- **A run carrying warnings printed "all checks passed".** 0.4.0 gave a SKIPPED
  axis its own `PARTIAL` verdict and left the other half of the same defect
  standing. An axis that emits `YELLOW` or `WARNING` and nothing red lands in the
  `passed` bucket, and the verdict consulted only `failed` and `skipped`, so the
  all-clear line printed straight over it. Reproduced on a vault where all nine
  axes run, against the real rulebook:

  ```
  WARNING [H] warden_validator: 2/8 mechanical enforcers present ...
  WARNING [I] hook_health: Stop hook is wired but has NEVER FIRED. ...
  PASSED: 9  SKIPPED: 0  FAILED: 0
  GREEN vault-health: all checks passed        (exit 0)
  ```

  Such a run now ends in `ADVISORY`. The predicate covers the advisory CLASS and
  not the one prefix the reproduction happened to show: the file emits four
  prefixes, and `YELLOW` (`check_b` at one or two strays) sits in the same bucket
  a `WARNING` does, so a fix written against the literal string `WARNING` would
  have left half of it standing. Both arms carry a test and both were proved by
  mutation.

  The exit code does NOT move, deliberately. A pristine `wulong init --with-hooks`
  raises `WARNING [I] hook_health: NEVER FIRED` on every run until the hook fires
  for the first time, so reddening a warning would exit 1 on the shipped
  quickstart, on a correct install, permanently. This is a reporting fix. `RED`
  and `PARTIAL` both outrank `ADVISORY`, so the indented skip list still prints
  and nothing changes on the failure path. `tests/expected/ex01.txt` does not
  move; the tokens are now guarded against both doc tables that publish them.

- **`vault-fresh.py` reported `[OK] vault-health` and `ALL OK` over a run that
  was not OK.** `_summarise` and the overall verdict keyed on the exit code
  alone, and doctor exits 0 for `PARTIAL` by design, so 0.4.0's new token never
  reached this consumer at all. Measured before the fix, on real doctor runs:
  a warned vault summarised as `[OK] vault-health: GREEN vault-health: all checks
  passed`, and a vault with three skips summarised as
  `[OK] vault-health:   SKIP [I] hook_health: ...`, the second showing an
  indented skip line under an `[OK]` label because the summary took the last
  non-blank line and the skip list prints after the verdict. It now reads the
  verdict line, so `RED`, `PARTIAL` and `ADVISORY` all mark the step `WARN` and
  all three drop `ALL OK`. `run_full`'s own exit code moves with the label: it
  returned 0 for a `PARTIAL` or `ADVISORY` doctor run before this fix (doctor
  exits 0 for both by design) and returns 1 for both now; a `RED` doctor run
  already returned 1 either way, unchanged. Scoped claim: this covers
  `vault-fresh.py`, which is the only other consumer of doctor in this repo.

- **Axis H reported an empty inventory as a clean one.**
  `check-enforcement-rules.check()` returns `present=[]  missing=[]` for both "no
  rulebook on disk" and "a rulebook that declares no mechanical enforcer", and
  `if missing:` read both as nothing to report. The first is now a `SKIP` naming
  `Meta/enforcement-rules.md`, the second says so in its own words, and neither
  can reach `GREEN`. LATENT rather than live: it needs a vault carrying
  `Meta/sync/check-enforcement-rules.py`, and `wulong init` copies no engine
  scripts, so on anything the installer produces the axis skips at the script
  gate instead. `check_h` keeps resolving that script vault-side, unchanged.

- **A published line citation that was already wrong before this change.**
  `README.md` and `docs/ARCHITECTURE.md` cited `:71-77` of
  `check_gate_precondition.py` as where a duplicated key resolves to the last
  value. Those lines are the loop header and the comment skip. The overwrite was
  at `:81`, outside the range. Both documents now cite
  `wulong/_frontmatter.py:64-87`, which contains it.
- **Fact 1's line citation re-pointed, and the fact-3 rationale corrected.** The
  binding branch lands inside the range fact 1 cites, so `:141-195` became
  `:154-208` in both `README.md` and `docs/ARCHITECTURE.md`, and the test that
  pins it gained a probe for the branch itself. Separately, the earlier note that
  fact 3 must stay unfixed had the reasoning backwards: checking time ordering
  would REINFORCE the post-PASS-edit invariant, not undermine it. Fact 3 stays
  because nothing checks time ordering yet, not because checking it would be
  wrong.
- **`tests/test_gate.py` fixtures that would have died silently.** Both
  `_pass_receipt` and the inline receipt in the duplicated-key test wrote a PASS
  with no artifact fields. Once binding is the default those tests would have
  stopped testing the trust boundary and started testing the migration default
  while still reporting green. Both now bind a real artifact, and each asserts
  its result with binding required as well as with it off. The deletion contract
  in the same file was REWRITTEN rather than overruled: it told a future reader
  to delete the whole class when "Change E strengthens the gate", which would
  have removed two disclosures that are still true.
- **The other citation in the same list, re-measured.** `:169-222` was true, and
  would have degraded to accidentally true once 30 lines came out of the file
  above it: it would still have caught the ALLOW block by luck while losing the
  read, the parse call and the `change_id` match. It is now `:141-195`, and both
  citations are asserted by tests that fail on a one-line drift in either
  direction and on a range widened to hide one. Grep of `tests/` for either
  citation previously returned nothing at all.
- **The sentence framing those six facts** promised each was "checkable in that
  file", which stopped being true for fact 2 the moment the reader moved out of
  it. Both documents now say which file a citation belongs to.

### Added

- **`wulong gate --manifest` and `wulong gate --verify`.** `--manifest` hashes
  the artifacts you name and prints the frontmatter block to paste into a
  receipt. `--verify` recomputes that digest from the bytes you name and compares
  it to the receipt's recorded value. Without the second one the digest is
  write-only and therefore decoration. `--verify` takes its bytes from repeatable
  `--artifact` and NEVER resolves the paths the receipt records, so a verified
  artifact can be moved or renamed and still verify; the recorded
  `artifact_paths` field is a diagnostic for a human. Neither mode resolves a
  vault root, so both work offline and outside a vault, which is where offline
  verification most needs to work.
- **Shape validation for the new fields in `validate-receipts.py`.** Every other
  receipt-graph field was already shape-validated, so an unvalidated one would
  let `artifact_manifest_sha256: banana` sit in a receipt looking like a binding
  that no gate can ever match. Four WARN codes cover a malformed digest, a
  non-positive count, one field present without the other, and an
  `artifact_paths` value that is not an inline list.
- **A platform statement in `README.md` and `docs/USERGUIDE.md`.** The
  `Operating System :: POSIX` classifier has been in `pyproject.toml` since
  0.4.0, and neither user-facing document said anything about a platform at all.
  A test now fails if the prose and the classifier disagree.
- **The 12th `fcntl` site, named and explained.** 11 scripts hard-import `fcntl`
  at module top level, which is the number the classifier justification carries
  and the number a test already enforced. The 12th,
  `wulong/sync/observer-disposition.py`, defers the import into a function body
  guarded by `except OSError`, and a missing module raises `ImportError`, which
  is not an `OSError`, so on Windows it dies uncaught at call time, after its
  ledger append has already succeeded. Both counts are now measured from disk
  against the published prose, and the guard's inability to catch the failure is
  asserted directly.
- **A test that exactly one function under `wulong/` scans for the frontmatter
  delimiter.** AST based, pinned to the shipped package because `Meta/sync/` and
  `build/lib/` hold full second copies, with a file-count floor so a walk that
  finds nothing cannot pass, and with every site named on failure.

### Corrected

- **Corrected, `[0.4.0]`.** That entry says "exactly 3 of the 8 axes cannot run"
  on a fresh skeleton, which was true when it was written and is superseded here.
  The count is now four of nine on a default install. The released entry is left
  as published, per the policy at the top of this file.

### Known

- **Axis G's skip line names a remedy that does not work.** It reads "needs a
  runnable `Meta/sync/drift-scan.py`", and copying that script there, which is
  verbatim what the line instructs, still leaves G skipping. `drift-scan.py:23`
  resolves its reference map from its own parent's parent, so a copy at
  `Meta/sync/` also needs `Meta/reference-map.md` beside it, and that file is
  100% user data with no shippable default. Measured both ways: script alone,
  still `SKIP`; script plus a reference map, G runs and reports. Not fixed here
  because the string is frozen into `tests/expected/ex01.txt:23` and correcting
  it moves a golden that this change's acceptance requires to stay byte
  identical. Carries its own change_id, `wulong-axis-g-skip-remedy-2026-08-19`.
- **The per-axis tag guard catches a REGISTRATION mutation, not a SEVERITY one.**
  `tests/test_hook_wiring.py::test_every_one_of_the_nine_axes_appears_in_the_output`
  reds when an axis call is deleted or substituted, which the registration sum
  cannot see. It does not red when an axis keeps its tag and changes its prefix:
  make `check_e` emit `YELLOW` instead of `RED` and both the tag and the sum
  survive. The selftest at the foot of `vault-health-check.py` pins `RED` for A,
  B, E and F and not-`RED` for C; D, G, H and I are unpinned. Pre-existing, and
  stated rather than chased.
- **`observer-apply.py:455` decides a contrarian PASS by raw substring over the
  whole file.** Those two strings anywhere in it, including in prose quoting
  some other receipt, clear the gate. Snapshot of 2026-08-20, measured over the
  6,627 receipts in `Meta/receipts/` at that point: 1,110 clear the substring
  test, 1,105 clear a real frontmatter parse, 5 differ. All 5 are contrarian
  receipts carrying `review_verdict: FAIL` in their own frontmatter while
  quoting the literal PASS string in prose about another receipt, so every
  difference is an accept that the parse would reject, none run the other way,
  and all 5 clear a live gate today. Read that as a dated snapshot and not as a
  property of the corpus: it rises by one with every further contrarian FAIL
  receipt that quotes a PASS string in prose. Two of the 5 are this change's own
  plan review and output review, which is why the number moved while the change
  was being written, and the second of those is the review that caught the count
  previously published in this bullet. The receipt that found the hole is an
  instance of it. Routing this call through the shared reader is a behaviour
  change and not a refactor, so it is deliberately excluded here and carries its
  own change_id, `wulong-observer-apply-verdict-2026-08-20`.
- **`validate-receipts.py` prints its violations in a different order on every
  run**, for identical input and identical code, because the order follows set
  iteration. Found by diffing the tool's own output before and after this change.
  Not fixed here.
- **`docs/CONTRIBUTING.md` says there are eleven test files and lists eleven.**
  There are fourteen. Untouched here because it is not this change's claim.

## [0.4.0] - 2026-08-19

Change D: root resolution, and a health scan that stops calling a skipped check
a passed one. Nothing in 0.3.0 reached PyPI; this is the release gate.

### Added

- **One vault-root resolver, `wulong/_root.py`.** Precedence: an explicit path,
  then `WULONG_ROOT`, then a floor. For the four CLI subcommands the floor is
  walking up from the working directory for a `CLAUDE.md` or `.wulong` marker,
  and failing that an error naming all three options. For engine scripts the
  floor is their own install-relative directory, because they normally run as
  children with the root handed down, so that tier is reached only on manual
  invocation, where a script sitting at `<vault>/Meta/sync/` knows its vault.
  15 scripts now share it. It lives outside `wulong/sync/` on purpose: a script
  executed by path gets its own directory as `sys.path[0]`, so
  `from wulong._root import ...` resolves only through the installed package.
- **`--root` on `doctor`, `gate` and `pulse`.** None of the four subcommands had
  a root flag before. `doctor`'s legacy positional still works, in any argument
  order, and a path written on the command line in either form outranks
  `WULONG_ROOT` and the working directory.
- **`--require-all-axes` on `doctor`**, default off. Turns a skipped axis into
  exit 1, for CI that wants completeness rather than a quickstart.
- **`--exit-nonzero-on-red` on `pulse`, default ON**, with
  `--no-exit-nonzero-on-red` to opt out. See Changed.
- **A machine-readable `COMPLIANCE-VERDICT:` line from `check-compliance.py`,**
  printed in every mode. Its exit code returns 1 for a new block violation only
  under `--strict`, so the exit code could not carry an honest verdict to a
  caller that had not passed the flag.

### Fixed

- **`wulong doctor` scanned the wrong vault, silently.** `cli.py` told users to
  set `WULONG_ROOT` for `doctor`, and `vault-health-check.py` read it zero times.
  With the variable exported and no positional argument it walked up from
  `__file__` and resolved the wulong installation itself, then reported that
  directory's health under the user's vault name. Same defect class as the
  wrong-vault delete fixed in 0.3.0.
- **`doctor` printed "all checks passed" over checks that never ran.** On a fresh
  `wulong init` skeleton, exactly 3 of the 8 axes cannot run, and all three
  emitted a warning that was counted as a pass. Runs now report `PASSED`,
  `SKIPPED` and `FAILED` as three separate counts and end in `GREEN`, `PARTIAL`
  or `RED`. A skip never changes the exit code, a failure always does, and each
  skip names what it would need. Five skip sites were involved, not the three
  that are visible on a fresh vault: axis H skipped silently on both of its
  error paths as well.
- **`wulong pulse --root B` ran three of its four children against
  `site-packages`.** Only `session-close-audit.py` was given the root. Every
  subprocess in the chain now receives the resolved root explicitly, including
  the second-level spawns (`check-compliance.py` to `verify-change.py`, and
  `enforcement-sweep.py` to its six validators) that no enumeration had listed.
- **`wulong gate` REFUSEd everything from a pip install.** Its default receipts
  directory came from `__file__`, which is inside `site-packages`. It now
  defaults to `<root>/Meta/receipts`.
- **The `session-guard.py` / `session-start-gate.py` registry split**, recorded
  as KNOWN, NOT FIXED at 0.3.0, is closed. Both go through the shared resolver.
- **`vault-health-check.py`'s docstring listed checks A to F** while the file
  defined `check_a` through `check_h`.

### Security

- **Removed the D7 plug-in dispatch.** It read `Meta/qa/e2e-plugins.yaml` out of
  the directory being scanned and passed each entry's `cmd` to a shell as a
  string, so the scanned directory chose the command as well as its arguments.
  Nothing has ever shipped such a manifest, and no test, document, golden file
  or claims-audit row referenced the feature, so this was a speculative
  capability with a real sink attached to it. `verify-change.py` now reports D7
  as N/A on every run and says why in the report. No verdict and no exit code
  moves: the check already returned N/A on any tree without a manifest.
- **`SECURITY.md` now states the runtime trust boundary.** wulong executes
  scripts that live in the vault you point it at, by design, the way `make`
  executes a Makefile. Removing D7 did not change that class of execution and
  this release does not claim otherwise. Two consequences are now written down:
  the verdict wulong prints is only as trustworthy as the vault it scanned, and
  root resolution can fall back to a walk up from the working directory, so the
  tree it settles on may be one you never named. Read that section before
  pointing the tool at a directory you did not write.
- **`--change-id` is bounded to a plain token**: 1 to 200 characters drawn from
  `[A-Za-z0-9._-]`, no leading dash, and not `.` or `..`. This is hygiene, not a
  fix for a live hole; the value reaches an escaped regex and list argv, never a
  shell string. Measured against 1105 real change_ids the longest is 84
  characters, and the only strings the bound rejects are malformed multi-id
  frontmatter that never matched anything.
- **`tests/test_execution_surface.py` freezes the execution surface.** An AST
  walk over every `.py` file in the package pins how many shell keywords,
  `exec_module`, `eval`, `exec` and `compile` calls exist, plus uses of the os
  module's `system` and `popen` helpers, which reach a shell without passing the
  keyword. There is a floor on the number of files scanned so that renaming a
  directory cannot make the guard pass over nothing. Adding an execution
  primitive stays allowed; adding one silently does not. The walk keys on what
  is written at each call site, so a shell flag arriving through a `**kwargs`
  unpack is outside it; SECURITY.md and the test file both record that.

### Changed

- **A RED `wulong pulse` verdict now exits 1.** It printed `ACTION REQUIRED` and
  exited 0 unless `--strict` was passed, so any hook or script reading the exit
  code saw success. `--strict` is deliberately NOT the switch that was flipped:
  it also changes what the child checks count as a failure and relabels RED as
  HARD-BLOCK. `--no-exit-nonzero-on-red` restores the old behaviour.
- **The published `WULONG_ROOT` reader count is 31 of 53, up from 23.** Moving
  seven scripts onto the shared resolver removed seven inline environment reads,
  which a purely syntactic detector reads as the count FALLING to 16 while more
  scripts than ever honour the variable. `tests/test_doc_claims.py` now follows
  the import into `wulong/_root.py`, and measures which of its public functions
  read the variable rather than assuming.

### Known, not fixed

- 22 of the 53 scripts still do not consult `WULONG_ROOT`; 20 derive their paths
  from `__file__` and 2 never touch it. Not all 20 resolve a vault root; some
  take only their own directory. `wulong-init.py` is in the 20 by design and
  resolves no vault root: it creates a vault, so the directory is its argument.
- Windows is unsupported: 11 scripts import `fcntl` at module top level.
- The NN#3 gate proves a receipt claiming a PASS exists. It does not prove a
  review happened, and the PASS is not bound to any artifact.

## [0.3.0] - 2026-08-18

0.2.0 was never uploaded to PyPI. Its entry below is kept as a record of that
work block, and everything in it ships here. 0.1.0 remains the only other
published release.

### Changed

- **Seven engine scripts existed in two copies and have been reconciled to one.**
  `cerebrum-search.py`, `query-receipts.py`, `session-close-audit.py`,
  `session-guard.py`, `trace-change-chain.py`, `validate-receipt-graph.py` and
  `validate-receipts.py` were tracked in both `Meta/sync/` and `wulong/sync/` and
  had drifted apart in both directions: the vault copies read `WULONG_ROOT` and
  the package copies did not, while the package copies carried self-tests and
  validation blocks the vault copies lacked. The union of both now lives in
  `wulong/sync/` only, with zero symbols dropped from either side, and
  `tests/test_no_doubled_scripts.py` fails if a filename ever appears in both
  directories again. `Meta/sync/session-close-audit-config.json` is deliberately
  kept: it is per-vault policy rather than package data, and its loader fails
  closed to `block_enabled=false` with no error, so an accidental delete would
  turn a blocking audit into a silent no-op.
- **The published `WULONG_ROOT` reader count is 23 of 53, not 24.** The old
  figure counted files that merely CONTAIN the string. `wulong-init.py` names the
  variable in its help text and prints a hint about it, and never reads it, which
  makes it the worst possible over-count because it is the script where a user
  would most expect the variable to apply. `tests/test_doc_claims.py` now matches
  an actual `os.environ` read by parsing the module rather than searching its
  text, so the documented verb and the measured predicate finally agree.
- **The scrub deny-list is now enforced. Read this before you upgrade.** Both
  `scripts/scrub.sh` and `scripts/pre-publish-assert.sh` check (c) used to hand
  each deny-list line to `grep` with its inline trailing comment still attached.
  Every live pattern in the shipped template carries such a comment, so every
  pattern was a regex that matched nothing and both scans were inert. They now
  strip the comment before matching. If you already keep a `scrub-patterns.txt`,
  expect hits on your first run after upgrading. They are real hits that were
  always there; nothing about your repository got worse.
- **Deny-list tags moved to a leading sigil, and there are now two of them.** A
  tag written in the trailing comment exempts nothing, and both scripts print
  one WARN per offending line naming the line and the one-step fix.
  `[allow-author]` exempts the commit-author check only. `[allow-public]` is new
  and exempts the file scan only, for a value that is public by construction
  such as a repository URL or a copyright line. Only an `[allow-...]` token
  counts as a sigil, so a pattern that legitimately opens with a bracket
  expression (the shipped personal-email pattern starts `[A-Za-z0-9._%+-]`) is
  not mis-parsed as a tagged line. Full rules in `SECURITY.md`.
- **`wulong init` installs the agent payload, not just the four overlay files.**
  65 agent definitions, 1 hook, 2 `SKILL.md` files and `CLAUDE.md` are written
  into your vault. It does NOT install the 53 engine scripts. A second copy of
  the engine in your vault would go stale the moment you `pip install -U`, and
  because init never overwrites an existing file, the stale copy would win
  permanently. The CLI runs the engine from the installed package instead.
- **The payload moved to `wulong/payload/`** and is no longer tracked at the
  repository root. One tracked copy is now both what the wheel packages and what
  init writes, so a clone and a `pip install` produce the same vault. In a
  clone, `git ls-files .claude` returns 0 and `git ls-files wulong/payload`
  returns 69. Running `wulong init .` inside a clone writes those 69 files back
  to the repo root, where the test suite needs them, and `.gitignore` now covers
  `/CLAUDE.md` and `/.claude/` so they cannot be staged by accident. Running the
  suite from a fresh clone therefore requires `python -m wulong.cli init .` once.
  `docs/CONTRIBUTING.md` documents it and CI runs it before pytest and before
  both example steps.

### Added

- **The governance tools now say so when they have no vault to check.** Six of
  them resolved a root, found no `Meta/` directory under it, scanned zero files
  and printed "0 violations", "coverage 100%" and "clean". In a wheel install
  with no root set that root is `site-packages`, so the reassuring output was
  produced by looking at nothing. Each now prints one WARN to stderr naming the
  resolved root and stating that a clean result means NOT CHECKED. Exit codes
  are unchanged, so nothing that consumes them breaks. One consequence worth
  knowing: `wulong pulse` reports the LAST line of the audit's combined output
  as its summary, so on a rootless install that summary line will now be the
  WARN instead of a false all-clear.
- **The wheel carries the payload.** The published 0.1.0 wheel contained 67
  files, zero agent definitions, zero hooks and no `CLAUDE.md`, so
  `pip install wulong` delivered none of the thing the README describes. The
  0.3.0 wheel carries all 69 under `wulong/payload/`. CI builds the wheel,
  unzips it, and asserts the four counts exactly (65 agents, 1 hook, 2 skills,
  1 `CLAUDE.md`), so a packaging change that silently drops files fails the
  build instead of reaching PyPI.
- Four explicit `[tool.setuptools.package-data]` globs for the payload. One
  pattern is not enough. An explicit key list REPLACES the setuptools defaults
  rather than extending them, and glob does not descend into a directory whose
  name starts with a dot unless the dot is spelled out, so `payload/**/*.md`
  packaged exactly one file and no agents at all.
- `wulong init` now writes a `.gitignore` entry for every overlay file it
  creates. `.env` holds `GITHUB_TOKEN` and `scrub-patterns.txt` is a list of the
  exact strings you never want published, so committing either is the leak the
  scrub tooling exists to prevent. Existing `.gitignore` content is appended to,
  never rewritten, and entries already present are not duplicated.
- Test suite grown from 3 files to 11: the NN#3/NN#4 gate, the frontmatter
  reader and its 4096-byte read window, the receipt checks, session-pulse exit
  codes, the built-wheel payload counts, what init installs and refuses to
  destroy, and a suite that parses every number this repository publishes about
  itself out of the document and measures it from disk.
- `docs/CLAIMS-AUDIT.md`, the line-by-line audit behind the documentation truth
  pass, including the rows this release closes.

### Fixed

- **The orchestrator session ledger never ran in 0.1.0.** `session-guard.py`
  compared the registered focus string against `"jarvis"`, while the agent
  definition shipped in the same release told the orchestrator to register as
  `orchestrator`. The two never matched, so every branch guarded by that
  comparison, the observe-pass ledger and the enforcement-violations write among
  them, was unreachable in the published artifact. The focus string is now a
  named constant carrying a comment that names the coupling to the agent file.
  This is a behaviour change, not a rename: code paths that never executed now
  execute.
- **`--root` was silently ignored whenever `WULONG_ROOT` was set.** Six scripts
  documented `--root` as an override and then returned the environment value
  first, so an explicit flag pointed at vault B while the script read, wrote and
  deleted in vault A. Measured before the fix: `query-receipts --root B` counted
  vault A's receipts, `validate-receipts --root B` wrote its report into vault A,
  and `cerebrum-search --smoke --root B` unlinked and rebuilt vault A's search
  index, left vault B untouched, and printed `SMOKE OK`. Resolution order is now
  `--root`, then `WULONG_ROOT`, then the install-relative path, which is what the
  help text always promised and what git, docker, pip and kubectl all do. If you
  have a script or cron job that passes `--root` while exporting `WULONG_ROOT`
  and relies on the environment winning, it will now use the flag. Affected:
  `cerebrum-search.py`, `query-receipts.py`, `session-close-audit.py`,
  `trace-change-chain.py`, `validate-receipt-graph.py`, `validate-receipts.py`.
  `session-guard.py` takes no `--root` and is unchanged.
- **`wulong pulse` audited a different vault from the one it reported on.**
  `session-pulse.py` resolves its root from its own file location and spawned
  `session-close-audit.py` with no root flag, so with `WULONG_ROOT` exported the
  parent and the child addressed two different vaults. The root is now passed
  down explicitly.
- **KNOWN, NOT FIXED AT 0.3.0: `session-guard.py` and `session-start-gate.py` can
  address different session registries.** The merge gave `session-guard.py` the
  `WULONG_ROOT` support the vault copy always had, and `session-start-gate.py`
  still resolves install-relative. Export the variable and a `register` through
  the guard lands under the environment vault while the gate reads the
  install-relative one and reports no active sessions. This could not happen in
  0.1.0, because no packaged script read the variable at all. Both files carry a
  comment naming the coupling. Deferred here because a one-file patch would move
  the reader count that `README.md`, `docs/USERGUIDE.md` and
  `tests/test_doc_claims.py` assert against.
  **Corrected in 0.4.0**, and the sentence naming the fix was wrong as written:
  it said Change D2 "widens root resolution across all 53 scripts at once", which
  is not what 0.4.0 does. See the 0.4.0 entry for what it does instead. Both
  files now share one resolver, so the split is closed.
- All seven merged scripts carried a docstring reading "Repo root inferred from
  this script's location (../../.. from Meta/sync/)". They live at
  `wulong/sync/`, and at `site-packages/wulong/sync/` in a wheel install, and
  `session-guard.py --help` printed the false path to the user. Corrected in all
  seven.
- **`wulong init` could destroy your `.env`.** The never-clobber contract rested
  on `Path.exists()`, which delegates to `os.path.exists()`, which swallows
  every `OSError` and returns `False`. A filesystem that could not answer read
  as "the file is not there" and init WROTE, overwriting the file holding
  `WULONG_ROOT` and `GITHUB_TOKEN`. The check now calls `os.lstat` directly and
  treats only `ENOENT` and `ENOTDIR` as absent; anything else aborts the run
  before a single byte is written. Two tests pin it, and a third asserts that
  all four overlay files survive a re-init. The suite previously asserted that
  for one of the four, and the two it left unasserted were the two that hold
  secrets.
- **`scripts/pre-publish-assert.sh` check (c) never scanned `.github/`.** Check
  (c) is the one that blocks a push. The exclusion was harmless while the scan
  was inert, and became a live blind spot over the single directory most likely
  to hold a CI token the moment the scan started working. The exclusion is gone
  from (c) and was deliberately not copied into `scripts/scrub.sh`. A pattern
  that false-positives on a runner name like `ubuntu-latest` needs a word
  boundary, not a directory the scanner skips. `SECURITY.md` no longer describes
  the two scanners as equivalent; it names the one real difference, which is
  tracked files versus a directory walk.
- The `LICENSE` copyright-line carve-out is deleted from both scanners and from
  `SECURITY.md`. It matched only `Copyright (c) YYYY Name` with no leading
  whitespace, and this project uses the Apache-2.0 appendix form, so it never
  fired once. `LICENSE` passes because the copyright holder's name carries
  `[allow-public]`.

### Corrected

- The 0.2.0 entry below records the context-weight figures "at 0.2.0" and locates
  them "under `.claude/agents/`". The numbers are unchanged and correct
  (65 files, 8,409 lines, 522,313 bytes of agent definitions, plus a
  44,103-byte `CLAUDE.md` that is re-sent on every turn), but they ship in 0.3.0
  and the tracked path is now `wulong/payload/.claude/agents/`. The figures are
  identical whether you clone or `pip install`, because both paths install the
  same files. One genuinely new fact: from 0.3.0 a `pip install` holds the
  payload twice on disk, once inside the installed package and once in the vault
  `wulong init` writes, so disk footprint roughly doubles. Context footprint does
  not, because only the vault copy is ever read into a session.

## [0.2.0] - 2026-08-18

### Changed

- **Licence is now Apache-2.0.** Applies from 0.2.0 onward. `LICENSE` carries the
  full Apache License 2.0 text, and `NOTICE` carries the copyright line and the
  third-party attribution. Release 0.1.0 was published under the MIT License and
  **stays MIT permanently**. It has not been yanked and will not be. If you are
  on 0.1.0 and do not upgrade, nothing about your licence changes.
- **Agent definitions no longer instruct the agent to reply in the user's
  language.** The line "Always respond to the user in their language. Match the
  language the user writes in." was removed from 57 of the 65 files in
  `.claude/agents/`. It was harness boilerplate, not part of any agent's role,
  and it was the only instruction telling agents to match the user's language.
  This is a behaviour change for non-English speakers: agents will now answer in
  whatever language the surrounding session establishes. Add the line back to any
  agent you want to keep the old behaviour. No `pip install` user is affected,
  because the published 0.1.0 wheel ships zero agent files; only git-clone users
  see this.
- **Documentation truth pass.** Claims the code does not support were deleted
  rather than softened, claims that were merely imprecise were corrected with
  measured numbers, and claims that cannot be made true at 0.2.0 are labelled as
  known gaps naming the change that owns the fix. The full line-by-line audit is
  in `docs/CLAIMS-AUDIT.md`. The largest edits: the "two gates are binding" and
  "no unreviewed change lands" guarantees are gone, the change-log claim is
  reworded to what `session-close-audit.py` actually cross-references, and the
  agent-count claims are scoped to the git repository because the wheel contains
  none of them.
- **README quickstart passes the vault path to `doctor` explicitly**
  (`wulong doctor myvault`). The bare `wulong doctor` form needs a vault root it
  cannot find from a pip install; that is targeted for 0.4.0.
- `docs/CONTRIBUTING.md` and `SECURITY.md`: the "no git remote is configured"
  rule and the pinned-pseudonym mandate are removed. Both were false once the
  repository was published and attributed.

### Corrected

- The 0.1.0 entry below states, under Phase C, that "`WULONG_ROOT` env knob wires
  every vault-root reference" (`CHANGELOG.md:19`). That is false. 17 of the 53
  scripts read `WULONG_ROOT`. Of the other 36, 34 resolve the vault root by
  walking up from `__file__`, and two resolve no vault root at all
  (`check_rename_diff.py` takes its paths as arguments, `research_router.py` has
  no path logic). The 0.1.0 entry is left as published, because it records what
  0.1.0 shipped. Widening `WULONG_ROOT` to every script is tracked as Change D2.

### Added

- `NOTICE` with the copyright line, the Anthropic non-affiliation disclaimer, and
  a third-party notices section carrying the MIT text for the vendored ponytail
  ruleset (`.claude/skills/ponytail/SKILL.md`, source DietrichGebert/ponytail).
- `AUTHORS`. Commits before 0.2.0 were authored under the placeholder identity
  `wulong <vault@local>`; git history is not rewritten.
- Contributor licensing terms in `docs/CONTRIBUTING.md`: inbound equals outbound
  under Apache-2.0 section 5, with a Developer Certificate of Origin sign-off
  (`git commit -s`). No CLA and no copyright assignment.
- Anthropic attribution and disclaimer in `README.md` and `NOTICE`. wulong is an
  independent project and is not affiliated with or endorsed by Anthropic PBC.
- A "what the gate actually proves" section in `README.md` and
  `docs/ARCHITECTURE.md`, listing six checkable facts about
  `check_gate_precondition.py` and stating plainly that the gate proves a file
  claiming a PASS exists, not that a review happened.
- Context-weight disclosure in `README.md`: at 0.2.0, 65 agent-definition files,
  8,409 lines and 522,313 bytes of markdown under `.claude/agents/`, plus a
  44,103-byte `CLAUDE.md` that is re-sent on every turn.
- PyPI metadata in `pyproject.toml`: description, authors, keywords, classifiers
  and `[project.urls]`.
- CI job that builds a distribution and asserts against the **built**
  `.dist-info`: `LICENSE`, `NOTICE` and `AUTHORS` are all present under
  `licenses/`, and `METADATA` carries `License-Expression: Apache-2.0`.

### Fixed

- `wulong --version` read a hardcoded `0.1.0` string. It now reads the installed
  distribution version through `importlib.metadata`, with a fallback for running
  from an uninstalled source tree, so it can no longer drift from
  `pyproject.toml`.
- `scripts/pre-publish-assert.sh` check (a) asserted that no git remote is
  configured, which could never pass again after publication. It now asserts that
  `origin` is the expected publish target and that no other remote is configured.
- `scripts/pre-publish-assert.sh` check (b) pinned every commit to the
  `wulong <vault@local>` pseudonym, which contradicts attributing the project. It
  now asserts that no commit author name or email matches a scrub deny-list
  pattern.

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
