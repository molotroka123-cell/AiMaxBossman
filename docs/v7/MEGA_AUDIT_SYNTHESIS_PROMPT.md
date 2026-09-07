# V7 Mega Audit — Synthesis Prompt

```text
BOSSMAN V7 — MULTI-MODEL MEGA AUDIT SYNTHESIS

Repository:
molotroka123-cell/AiMaxBossman

Canonical branch:
v7/audit-convergence-20260907

ROLE
You are the final evidence synthesizer. You are NOT allowed to overwrite or reinterpret model findings without showing the underlying evidence.
Your job is to turn multiple independent audits into ONE current-HEAD engineering truth and ONE implementation roadmap.

FIRST
- fetch/pull the latest canonical branch;
- record exact synthesis HEAD and tree SHA;
- enumerate every directory under docs/v7/audits/;
- read each model's IDENTITY.md, RAW_AUDIT.md, FINDINGS.json, TEST_EVIDENCE.md, VISION.md and RESPONSE_TO_OTHER_AUDITS.md;
- read docs/v7/CURRENT_AUDIT_2026-09-07.md;
- re-check current code/CI for every P0/P1 and every disputed finding before accepting it.

DO NOT SYNTHESIZE BY MAJORITY VOTE.
A finding accepted by three models but contradicted by a current regression test is not PROVEN.
A finding from one model with a deterministic reproducer may outrank five speculative opinions.

NORMALIZE FINDINGS
Deduplicate by underlying defect signature, not wording.
For every merged finding create:
- canonical id;
- title;
- severity P0/P1/P2/P3;
- status;
- confidence;
- source model(s);
- exact current-HEAD evidence;
- reproducer/test;
- affected paths/surfaces;
- owner-hardware-required flag;
- conflicts/overlap with existing work;
- recommended minimal fix;
- regression risk;
- verification/acceptance gate.

Allowed status values:
PROVEN
CORROBORATED
DISPUTED
SPECULATIVE
OWNER_HARDWARE_REQUIRED
CLOSED_BY_EVIDENCE
SUPERSEDED

REQUIRED CROSS-MODEL MATRIX
Write docs/v7/CONFLICT_MATRIX.md with rows for every meaningful disagreement:
finding | model A | model B | model C... | current evidence | resolution | confidence

REQUIRED OUTPUTS
1. docs/v7/MEGA_AUDIT.md
2. docs/v7/CONFLICT_MATRIX.md
3. docs/v7/V7_ROADMAP.md
4. docs/v7/OPEN_FINDINGS.json

MEGA_AUDIT.md MUST INCLUDE
- exact branch/HEAD/tree;
- model identities and starting SHAs;
- executive scorecard;
- proven P0/P1/P2/P3;
- closed/retracted stale findings;
- owner-machine evidence gaps;
- cross-model agreements;
- cross-model disagreements;
- architecture assessment;
- release implications;
- implementation sequence.

ARCHITECTURE SYNTHESIS
For each V7 hypothesis below return one decision:
ADOPT / ADOPT_WITH_LIMITS / EXPERIMENT / DEFER / REJECT

Hypotheses:
- Reality Compiler / formal Mission IR;
- World State Graph with freshness/provenance;
- strategy search / expected utility;
- adaptive model orchestration / Local Cognitive Fabric;
- dynamic temporary agent teams;
- self-improving skills with replay/shadow/benchmark/canary/rollback;
- counterfactual pre-effect simulation;
- attention/QoS scheduler;
- goal-first Mission/Reality UX;
- generation-aware single-flight/coalescing.

For each decision give:
problem solved, current evidence, expected benefit, implementation cost, safety risk, rollback path, minimum experiment, acceptance metric.

ROADMAP ORDER
Prioritize:
1. current proven P0/P1 correctness/reliability;
2. evidence integrity and owner-session regressions;
3. Windows/local-model/real media acceptance blockers;
4. measurable performance/resource improvements;
5. low-risk V7 architectural experiments;
6. only then large architecture migrations.

Do not let V7 redesign hide unresolved V6 runtime defects.

CODE RULE
The synthesis phase should not mass-refactor code. It may only make tiny documentation/registry corrections needed to produce an internally consistent audit. Implementation follows the accepted roadmap in subsequent commits.

FINAL CHECK
Before push:
- ensure every P0/P1 has current-HEAD evidence or is downgraded;
- ensure stale historical findings are not counted as current;
- ensure owner hardware evidence is not fabricated;
- ensure OPEN_FINDINGS.json matches MEGA_AUDIT.md;
- pull --rebase and confirm no peer audit was lost;
- push to the SAME canonical branch.

FINAL RESPONSE
State exact final SHA/tree, model audits consumed, total canonical findings by severity/status, unresolved P0/P1, owner-hardware-required items, top 10 implementation priorities, architecture decisions, and any disagreements left unresolved.
```
