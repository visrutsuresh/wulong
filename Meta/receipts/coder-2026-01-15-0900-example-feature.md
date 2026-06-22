---
agent: coder
task: example feature implementation -- generic schema demonstration receipt
date: 2026-01-15
time: "0900"
status: DONE
change_type: feature
change_id: example-feature-2026-01-15
gated_by: [contrarian-2026-01-15-0830-example-feature-plan-review.md]
trigger_kind: upstream_handoff
trigger_ref: mastermind handoff example-feature-2026-01-15
tags: [example, schema, template]
---

## Task

Implement the example feature as specified in the approved plan.
This receipt demonstrates the canonical v3.0.2 schema including all recommended fields.

## Outcome

DONE. Feature implemented and committed. Backtest before: WR=52.1%. After: WR=54.3%.
Scrub CLEAN. All checks PASS.

## Rationale

This example receipt demonstrates the full v3.0.2 schema: required fields (agent, task, date, time, status), recommended fields (change_type, change_id, gated_by, trigger_kind, tags), and the gate-chain stamping fields (gated_by pointing to the contrarian plan-review receipt that cleared this change). Every coder receipt for a feature or fix should include these fields.

## Files written

- /absolute/path/to/feature_file.py
- /absolute/path/to/tests/test_feature.py

## Skills invoked

- .claude/skills/ponytail/SKILL.md: applied rung ladder. All content needed to exist (feature delivery). No avoidable deps. Minimal implementation only.

## Linked artifacts

- contrarian-2026-01-15-0830-example-feature-plan-review.md (PASS, plan-review)
- contrarian-2026-01-15-0945-example-feature-output-review.md (PASS, output-review)
