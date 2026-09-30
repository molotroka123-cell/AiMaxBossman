# BOSSMAN 1.1 — NON-NEGOTIABLE NORTH STAR

Status: **MANDATORY PRODUCT DIRECTION**
Owner decision: 2026-09-23

This document is mandatory context for Claude, Codex, Aster and every future AI agent working on AiMaxBossman.

## Mission

Bossman 1.0 is the stable base.

**Bossman 1.1 exists to become a continuously improving local AI operator that can improve Bossman itself, learn from verified experience, recover from failure, and turn that learning into useful owner-approved work with measurable business value.**

The system is not finished when it can chat, call tools or edit files.

The target loop is:

```
OBSERVE
→ CHOOSE A USEFUL TASK
→ ATTEMPT
→ EDIT / ACT
→ TEST
→ INDEPENDENT VERIFY
→ ACCEPT OR REJECT
→ LEARN
→ CHECKPOINT
→ RESTART / CONTINUE
→ NEXT TASK
```

The loop must survive process restarts and must not depend on one chat history.

## Tomorrow's required milestone

The next owner-hardware run must try to prove a real self-improvement cycle:

1. Owner gives Bossman: **"Improve Bossman."**
2. A local model investigates the real project through Bossman's supported coding path.
3. It selects or is assigned one bounded real defect.
4. It reproduces the defect.
5. It writes a minimal candidate patch in an isolated workspace.
6. It adds/runs a regression.
7. Independent verification tries to disprove the patch.
8. A bad patch is rejected without touching stable.
9. A good patch becomes a reviewed candidate.
10. The reusable correction is stored as a VERIFIED lesson/recipe.
11. Bossman fully restarts.
12. A new analogous task automatically retrieves the relevant lesson.
13. The learner attempts the new task without the teacher giving it the answer.
14. The result is measured.

If this chain does not complete, report the exact failed stage. Do not rename partial progress "self-learning".

## Continuous self-improvement target

After the first proven cycle, Bossman should be capable of running repeated bounded evolution cycles under owner control.

Required controls:

- STOP / PAUSE / RESUME;
- budgets;
- leases;
- watchdogs;
- maximum retries;
- maximum consecutive failures;
- bounded disk/RAM/model use;
- durable checkpoints;
- restart recovery;
- stable/candidate separation;
- independent verifier;
- rollback;
- owner-visible Telegram status.

Do not implement autonomous improvement as an unrestricted infinite loop.

## 4–7 day learning objective

**The 4–7 day window is an aggressive experiment target, not a guaranteed outcome.**

Starting only after the owner accepts the first Bossman 1.1 hardware run, run repeated verified learning cycles for 4–7 days.

Daily measurement must include:

- attempted improvement tasks;
- unassisted verified passes;
- coached passes;
- teacher patches;
- rejected bad patches;
- failures/timeouts;
- successful restart/resume;
- verified lessons written;
- automatic lesson retrieval rate;
- transfer success on unseen analogous tasks;
- time-to-result;
- owner interventions;
- frontier usage/cost;
- system uptime and resource leaks.

A memory hit is not learning.
A copied old patch is not transfer.
A Claude patch is not a local-model success.
A green test that no longer checks the requirement is not progress.

## Business-value objective

By the end of the 4–7 day experiment, the goal is for Bossman to demonstrate that its accumulated verified experience lets it complete **owner-approved, revenue-relevant work** increasingly autonomously.

"Can earn" means Bossman can create verified outputs that have plausible and measurable commercial value, for example:

- build or improve software/features;
- prepare websites/landing pages;
- create media or marketing assets;
- research markets and public business information;
- prepare client deliverables;
- automate internal business workflows;
- prepare offers, analyses, documentation or prototypes;
- identify and fix defects that reduce operating cost or improve throughput.

It does **not** mean promising guaranteed profit.

It does **not** authorize Bossman to independently:

- move money;
- trade real funds;
- gamble;
- purchase services without budget/approval;
- sign contracts;
- submit regulated applications;
- send bulk unsolicited outreach;
- bypass platform/account controls;
- publish/deploy consequential changes without the required owner gate.

Any real transaction, external submission, financial action or consequential publication keeps its normal approval boundary.

## Revenue-readiness gate

Do not claim `REVENUE_CAPABLE` because Bossman generated an idea.

A first revenue-readiness proof requires a real owner-selected business task where Bossman:

1. understands the requested outcome;
2. retrieves relevant project context;
3. produces the actual deliverable;
4. uses tools correctly;
5. verifies the artifact/result;
6. requires materially fewer owner interventions than baseline;
7. preserves evidence and provenance;
8. survives restart/resume;
9. stays within budget;
10. leaves any consequential external action at the proper approval gate.

Preferred measurement:

```
verified business output / wall-clock time / owner interventions / cost
```

Compare against the same task before the learning period where possible.

## Claude's role

Claude is the senior teacher, architect and final integrator.

Claude must not silently do the student's work and then report that Bossman learned.

For ordinary repair:

```
LOCAL BOSSMAN ATTEMPTS
→ TESTS
→ CLAUDE AUDITS
→ MINIMAL HINT
→ LOCAL BOSSMAN RETRIES
→ VERIFIER DECIDES
```

Escalation levels:

- L0: no help;
- L1: violated invariant;
- L2: root-cause class;
- L3: verification strategy;
- L4: architecture approach;
- L5: teacher patch.

L5 is always recorded as `TEACHER_PATCH`.

Every difficult verified correction should become a reusable generalized lesson or executable recipe when appropriate.

## Codex / Aster role

Codex may implement bounded independent fixes and challenge Claude's solution.

Aster is the independent economical adversarial auditor.

Neither may overwrite evidence from another agent.

Conflicts are reproduced and resolved by tests/evidence, not authority.

## Memory architecture rule

Do not create another canonical memory database.

Preserve:

- canonical human-readable Bossman memory;
- structured temporal facts;
- rebuildable retrieval indexes;
- LearningStore for verified episodes/lessons/skills.

Only VERIFIED and applicable knowledge may guide autonomous planning.

Memory cannot grant permissions, budgets, approvals or cloud access.

## Skill / community knowledge rule

External skills, fine-tunes and community recipes are candidates, not truth.

Record:

- source;
- exact commit/revision;
- license;
- allowed tools;
- evaluation;
- applicability;
- security status.

Promote only after Bossman-specific evaluation.

## Model rule

Do not equate a benchmark win with a production promotion.

The local tournament must measure:

- verified solve rate;
- tool/schema reliability;
- full task time;
- TTFT/prefill/decode;
- memory pressure;
- recovery;
- owner intervention;
- cost.

The best model for Bossman is the one producing the most verified useful work under the target hardware constraints.

## Stable / lab boundary

Stable is never rewritten directly by the learner.

```
stable
→ isolated lab
→ candidate patch
→ tests
→ independent verifier
→ teacher/integrator review
→ owner/release gate
→ promotion
```

A deliberately bad patch must be rejected and discarded in acceptance testing.

## Readiness ladder

Use these statuses literally:

- `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`
- `SELF_REPAIR_SINGLE_CYCLE_PASS`
- `SELF_REPAIR_3_CYCLE_PASS`
- `TRANSFER_MEASURED_GAIN`
- `24H_SOAK_PASS`
- `48H_SOAK_PASS`
- `WEEK_MODE_READY`
- `REVENUE_CAPABLE_PILOT`

Never skip levels by prose.

### Suggested gates

**SELF_REPAIR_SINGLE_CYCLE_PASS**
One full find/fix/test/verify/learn/restart/analogous-task cycle.

**SELF_REPAIR_3_CYCLE_PASS**
Three consecutive verified cycles without manual system recovery; includes one rejected bad patch and one restart/resume.

**TRANSFER_MEASURED_GAIN**
Unseen analogous tasks show measurable improvement with verified memory/skills versus controlled baseline.

**24H_SOAK_PASS**
No critical leak, stuck queue, retry storm, orphan process/model, Git corruption or lost STOP across 24 hours.

**48H_SOAK_PASS**
Same for 48 hours with multiple task classes.

**WEEK_MODE_READY**
Owner can leave Bossman running for a multi-day bounded evolution campaign and supervise through Telegram.

**REVENUE_CAPABLE_PILOT**
At least one real owner-approved business-value task completed to a verified deliverable with evidence, acceptable cost and reduced owner intervention.

## Every future AI agent must keep this in context

Every future AI agent working on this repository must:

1. read this document before making roadmap/release/self-improvement decisions;
2. preserve its intent when summarizing context;
3. mention the current North Star status in major handoffs/checkpoints;
4. distinguish what is implemented, tested, hardware-proven and merely targeted;
5. not deprioritize self-improvement into an indefinite backlog unless a release-critical safety issue requires temporary precedence.

Required one-line context marker for major handoffs:

> **NORTH STAR: Bossman 1.1 = verified continuous self-improvement; 4–7 day learning experiment aims at measurable transfer and owner-approved revenue-capable work, without bypassing stable/review/approval boundaries.**

## Priority statement

This is not an optional side experiment.

**After release-critical safety/correctness blockers, Bossman 1.1 self-improvement is the highest product priority.**

The product should progressively require less frontier intervention because verified experience becomes reusable local capability.

The long-term target is not "Claude fixes Bossman forever".

The target is:

> **Bossman becomes increasingly capable of improving Bossman, proving the improvement, retaining the lesson and applying it to useful real work — while Claude moves from primary fixer to teacher/auditor.**

If evidence does not support progress, report that directly and change the learning mechanism rather than changing the benchmark.
