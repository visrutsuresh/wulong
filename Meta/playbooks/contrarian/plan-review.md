---
agent: contrarian
task: plan-review
last_run: never
run_count: 0
success_rate: n/a
---

# Playbook: Plan Review (NN#10 Step 2)

The contrarian reviews a proposed plan BEFORE execution. This is the NN#3 gate
for coder tasks and the NN#10 step-2 gate for all multi-step tasks.

## Inputs required

- The proposed plan (what will be done, by whom, with what outputs, what gates apply)
- Relevant source files or context
- The `change_id` for this plan

## Review dimensions (score each; any FAIL = overall FAIL)

1. **Feasibility** -- Is the plan technically executable as stated? Are there missing
   dependencies, absent data, or environment assumptions that may not hold?

2. **Hidden assumptions** -- What must be true for this plan to work that the plan
   does NOT state explicitly? Are those assumptions verified or verifiable?

3. **Missing gates** -- Does the plan skip a required gate? NN#3 (contrarian before
   coder), NN#4 (tester after deploy), NN#10 (both plan-review and output-review)?

4. **Overfitting / bias risk** -- Is the plan optimising for a metric that can be
   gamed or that does not reflect real-world performance?

5. **Cheaper alternative** -- Is there a simpler path to the same outcome that the
   plan overlooks?

6. **Blast radius** -- What breaks if this goes wrong? Is the blast radius acceptable?
   Is rollback documented?

7. **Sycophancy risk** -- Is the plan accepted because it sounds good, not because
   the evidence supports it? Are claims backed by data we can point at?

## Verdict options

- **PASS** -- proceed. Write receipt with `review_mode: plan`, `review_verdict: PASS`.
- **SOFT FAIL (override available)** -- proceed with documented objections that the
  orchestrator has explicitly overridden with rationale.
- **FAIL** -- do not proceed. List specific objections. Orchestrator routes to
  plan-fixer agents, then re-submits for re-review.

## Steps

1. Read the plan carefully. Read all referenced source files.

2. Score each dimension above (PASS / WARN / FAIL + one-line reason).

3. Write verdict receipt to `Meta/receipts/contrarian-<date>-<time>-<slug>.md`:
   - `review_mode: plan`
   - `review_verdict: PASS | FAIL`
   - List all objections (even on PASS, document any WARNs).

4. If PASS: write handoff to mastermind / coder as appropriate.
   If FAIL: write handoff back to orchestrator with specific objections
   (one objection per plan-fixer to parallelize the fix loop).

5. Append to change-log (NN#7).

## Hard rules

- Never PASS a plan that skips a required gate (NN#3, NN#4, NN#10).
- Never PASS a plan with a secret value in frontend code (NN#13 web security).
- A ponytail ladder violation (unrequested abstraction, avoidable dependency,
  speculative scaffolding, clever-with-no-payoff) is a HARD-FAIL. Name the
  violated rung and clear all safety paths before issuing.
- Maximum 3 fix loops before ESCALATE to the user.

## History of runs

<!-- Append after each run: YYYY-MM-DD | change_id | verdict | objections (if FAIL) -->
