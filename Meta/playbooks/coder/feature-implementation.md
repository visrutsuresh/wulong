---
agent: coder
task: feature-implementation
last_run: never
run_count: 0
success_rate: n/a
---

# Playbook: Feature Implementation

Execute in this exact order. Do not skip steps.

## Prerequisites

- Contrarian PASS receipt exists for this change_id (NN#3 gate)
- Handoff from mastermind or contrarian with exact specification

## Steps

1. **Read the handoff** -- understand the exact change, not just the direction.

2. **Gate check** -- confirm contrarian PASS receipt is present in `Meta/receipts/`
   for this change_id. If absent: STOP, post BLOCKED to `Meta/agent-messages.md`.

3. **Read the source files** -- read every file that will be modified before writing
   a single line. Never edit blind.

4. **Capture baseline metrics** (if applicable to your project type):
   - Run existing tests; record pass rate.
   - If a performance metric exists, capture it now.

5. **Implement the approved change** -- minimal, self-contained, no scope creep.
   Apply ponytail discipline: climb the rung ladder before writing code.

6. **Verify** -- run the project's test suite. Confirm baseline metrics are equal
   or better. If worse: do NOT commit. Report numbers to mastermind.

7. **Run scrub + security checks**:
   ```bash
   bash scripts/scrub.sh
   gitleaks detect --source . --verbose
   bash scripts/pre-publish-assert.sh
   ```
   All three must exit 0.

8. **Commit** with conventional prefix:
   ```
   feat: <description> (before: X → after: Y)
   ```

9. **Write completion receipt** to `Meta/receipts/coder-<date>-<time>-<slug>.md`
   with frontmatter: `change_id`, `gated_by` (the contrarian plan-review receipt),
   `change_type: feature`, `## Skills invoked` section.

10. **Append to change-log** (NN#7):
    ```
    [YYYY-MM-DD HH:MM] coder -> FEAT <filepath> -- one-line summary
    ```

11. **Post to agent-messages.md** if deployer needs to act:
    ```
    [YYYY-MM-DD HH:MM] Coder -> TO: Deployer -- feature <name> committed, ready to deploy
    ```

## Known failure modes

- No contrarian PASS in handoff: STOP. Do not implement without approval (NN#3).
- Metrics show regression: do not commit. Report to mastermind with numbers.
- Scrub hits a sensitive pattern: fix before committing. Never bypass with --no-verify.
- Specification unclear: write a handoff back to mastermind for clarification.

## Definition of done

- [ ] Contrarian PASS confirmed in handoff
- [ ] Source files read before any edit
- [ ] Feature implemented
- [ ] Metrics verified (equal or better)
- [ ] Scrub + gitleaks + pre-publish-assert all exit 0
- [ ] Commit made with descriptive message
- [ ] Completion receipt written with all required fields
- [ ] Change-log line appended

## History of runs

<!-- Append after each successful run: YYYY-MM-DD | change_id | before metric | after metric -->
