---
type: meta
tags: [meta, brain, state]
updated: YYYY-MM-DD
owned-by: orchestrator
version: v1
---

# Brain — System State

This is the Tier-1 (Foundational) memory document for the vault's agent system.
It is the canonical source of truth for current system state, project status, and
key decisions. Update this after any significant change. Do NOT use it as a scratchpad.

---

## Active Projects

<!-- List your active projects here. Example: -->
<!--
| Project | Status | Priority | Goal |
|---------|--------|----------|------|
| project-alpha | Active | P1 | Launch by YYYY-MM-DD |
| project-beta | Paused | P2 | Resume after alpha ships |
-->

_No projects configured yet. Add them above._

---

## Agent System State

### Active Agents

<!-- List the agents deployed in your system. -->

| Machine ID | Role | Status |
|-----------|------|--------|
| orchestrator | Session owner and pipeline coordinator | Active |
| coder | Implementation | Active |
| contrarian | Plan and output review gate | Active |
| tester | Post-deploy smoke test and gate | Active |
| deployer | Deployment execution | Active |

### Gate Status

<!-- Track the current state of gated changes. -->

| change_id | Gate status | Notes |
|-----------|-------------|-------|
| _none_ | | |

---

## Key Decisions (Append-only)

<!-- Record significant, irreversible, or high-blast-radius decisions here.
     Format: YYYY-MM-DD | Decision | Rationale | Agent who decided -->

| Date | Decision | Rationale | Agent |
|------|----------|-----------|-------|
| YYYY-MM-DD | System initialized | Bootstrapped from wulong public template | orchestrator |

---

## Domain Notes

<!-- Append domain-specific knowledge here. One line per note.
     Format: YYYY-MM-DD | note text -->

_None yet._

---

## Known Issues / Open Questions

<!-- Track blockers and unresolved questions. Remove when resolved. -->

_None._

---

## Configuration

<!-- Document key environment variables and configuration here. -->

| Variable | Default | Purpose |
|----------|---------|---------|
| `WULONG_ROOT` | (script-relative) | Override vault root for all Meta/sync scripts |
