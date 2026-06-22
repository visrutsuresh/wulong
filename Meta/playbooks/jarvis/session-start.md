---
agent: orchestrator
task: session-start
last_run: never
run_count: 0
success_rate: n/a
---

# Playbook: Session Start

Execute at the beginning of every session. This initializes the agent system,
loads context, and routes the user request through the correct pipeline.

## Steps

1. **Read mode** -- check `Meta/mode` (one token: `agent` or `inline`).
   Fails closed: if absent/unreadable/unexpected → treat as `agent` mode.

2. **Subscribe to agent bus** -- check for coordination messages since last spawn.
   Fold relevant messages into working context.

3. **Check halt** -- confirm execution is allowed (global scope + project scope).
   If halted: write receipt to `Meta/receipts/orchestrator-<date>-<time>-halted.md`
   and return `HALTED: <reason>` as the first line of output.

4. **Load context** -- read `Meta/context/orchestrator.md` (compiled per-agent context).
   Also read `Meta/brain.md` for current system state.

5. **Check handoffs** -- read any handoff at `Meta/handoffs/` addressed to you.
   Archive after reading.

6. **Check agent-messages.md** -- read all pending messages marked with your name.

7. **Read last 20 lines of change-log.md** -- catch recent changes.

8. **CLASSIFY the user request**:
   - Relay (read-only, no state change) → respond directly (NN#10 relay exemption)
   - Major task (code/data/model/architecture/deploy) → NN#10 full pipeline
   - Admin/housekeeping → NN#10 plan + output review (no contrarian gate needed)

9. **PLAN** (for non-relay tasks) -- draft a reviewable plan:
   - What will be done
   - By which agents
   - What outputs result
   - Which gates apply (NN#3 for coder tasks, NN#4 for deploys, NN#10 always)
   - What the blast radius is

10. **PLAN REVIEW** -- spawn contrarian in plan-review mode. Wait for PASS.
    If FAIL: spawn plan-fixer(s) in parallel (one per objection), merge, re-review.
    Maximum 3 loops; then ESCALATE to user with unresolved objections.

11. **EXECUTE** -- on contrarian PASS, spawn worker agents per plan.
    Enforce NN#3 (contrarian before coder), NN#4 (tester after deployer).

12. **ASSEMBLE + OUTPUT REVIEW** -- collect worker results, spawn contrarian in
    output-review mode. If FAIL: spawn output-fixer(s), re-review. Max 3 loops.

13. **CLOSE** -- write session log to `Meta/Sessions/YYYY-MM-DD-HHMM.md`.
    Update `Meta/company-registry.md` (NN#5). Run `vault-health-check.py`.
    Append to change-log. Write completion receipt.

## Owned files

- `Meta/Sessions/` -- session logs (ONLY orchestrator writes here)
- `Meta/company-registry.md` -- updated every session (NN#5)
- `Meta/mode` -- mode toggle (agent | inline)

## Hard rules

- Every user message is owned by the orchestrator from the first turn.
- Never route a coder task without contrarian PASS (NN#3).
- Never close a deploy without tester PASS (NN#4).
- Never skip NN#10 (contrarian plan-review + output-review) for any state change.

## History of runs

<!-- Append after each session: YYYY-MM-DD | user request summary | outcome -->
