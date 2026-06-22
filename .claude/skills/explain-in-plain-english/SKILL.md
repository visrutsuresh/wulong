---
name: explain-in-plain-english
description: "The reusable implementation of CLAUDE.md Non-Negotiable #12 (teach mode). Load this whenever you produce text whose audience is the operator/user -- recommendations, summaries, plans, briefings, approval-queue rows, or the final user-facing turn. It encodes the (a)-(f) plain-English rules so any agent applies them consistently."
risk: none
source:
  - CLAUDE.md Non-Negotiable #12
---

# Explain in Plain English (teach mode)

This skill is the shared, reusable implementation of **CLAUDE.md Non-Negotiable #12**. NN#12 is the binding rule; this skill is HOW you satisfy it. Load it whenever your output's audience is the user or operator.

**Who you are writing for:** someone who may be expert in one domain but not in yours. Assume they can follow logical reasoning but have not memorized your system's jargon. Teach, don't just report.

## When this skill applies
Apply it to any text whose audience is the user or operator:
- recommendations and proposals
- summaries surfaced to the user
- plans, briefings, status updates
- approval-queue rows / any decision that needs a yes/no
- the final user-facing turn of a session

**When it does NOT apply:** internal agent-to-agent handoffs, receipts, and machine artifacts MAY stay technical and dense -- translating those wastes tokens and is explicitly not required.

## The six rules (from NN#12)

**(a) Define every technical term the first time it appears** -- in plain language, before optionally naming the jargon in parens.
- Bad: "WR is 47% on the OOS set."
- Good: "47% of actions succeeded (the 'success rate') when tested on data the system had never seen (called 'out-of-sample')."

**(b) Never use bare opaque labels as if memorized.** Either explain inline or skip the label. Watch for abbreviations, acronyms, and internal shorthand that the user might not know.

**(c) Lead with the human-level meaning, then the mechanism.** The headline must be understandable by someone without specialist background; technical detail is support, not the lede.

**(d) For any decision needing approval, break it down into four parts:**
1. **What's happening** -- the situation in one plain sentence.
2. **What it means plainly** -- why it matters, no jargon.
3. **What changes on yes vs no** -- the concrete fork.
4. **What could go wrong** -- the honest downside / risk of each path.

**(e) Use analogies where they help, and default to MORE explanation for technical topics, not less.**

**(f) No em dashes.** Do not use the em dash character (U+2014) in user-facing prose. Use full stops, commas, colons, or brackets instead.

## Quick self-check before sending user-facing text
- [ ] Did I define every technical/domain term on first use?
- [ ] Is the first sentence understandable with zero specialist background?
- [ ] If it's an approval ask, did I cover what / means / yes-vs-no / what-could-go-wrong?
- [ ] Did I avoid bare opaque labels?
- [ ] Would a smart non-specialist understand this on one read?
- [ ] No em dashes (U+2014) present?

If any box is unchecked, revise before sending. Teach, don't just report.
