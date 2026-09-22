# AGENTS.md — Mandatory context for AI contributors

Before making product, release, training, memory, model-routing or self-improvement decisions, read:

- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`
- `CLAUDE_NEXT_ACTION.md`
- current repair/audit checkpoints relevant to your work.

## Non-negotiable direction

**NORTH STAR: Bossman 1.1 = verified continuous self-improvement; 4–7 day learning experiment aims at measurable transfer and owner-approved revenue-capable work, without bypassing stable/review/approval boundaries.**

After release-critical safety/correctness blockers, this is the highest product priority.

Do not claim learning from memory hits, teacher-written patches, repeated known tasks or weakened tests.

Do not let the learner modify stable directly.

Use isolated candidate → tests → independent verification → review → promotion.

Major handoffs/checkpoints must mention current progress against the North Star ladder:
SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT.
