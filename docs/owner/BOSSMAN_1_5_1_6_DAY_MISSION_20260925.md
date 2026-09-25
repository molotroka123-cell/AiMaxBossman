# BOSSMAN — ONE DAY MISSION: 1.5 -> 1.6

Date: 2026-09-25

Status: **OWNER DIRECTIVE / ONE DAY / TWO STAGES / NO BRANCH SPRAWL**

This is one task for the whole day.

Goal:

```
CLOSE 1.5
   ->
FREEZE 1.5
   ->
MOVE IMMEDIATELY TO 1.6
   ->
START THE REAL SELF-IMPROVEMENT LOOP
   ->
CLOSE 1.6 WITH EVIDENCE
```

Do not start 1.7.
Do not create "final-final" branches.
Do not call a stage complete from model prose, documentation, mocks, or old-SHA evidence.

---

## Canonical branches

### Stage 1 — Bossman 1.5

`feat/bossman-1.5-economy-orchestrator-20260924`

Purpose: finish the economical multi-model Bossman that learns from public data, delegates bulk work to cheap/free workers, records reusable skills/workflows/memory, and keeps Codex/Aster out of the expensive worker role.

### Stage 2 — Bossman 1.6

`feat/bossman-1.6-self-improvement-20260925`

Purpose: make self-improvement itself the product loop.

1.6 MUST start from the exact final 1.5 tree.

Before any 1.6 implementation:

```
BASE_1_5_FINAL_SHA=<exact 1.5 final SHA>
```

If the 1.6 branch was created earlier, merge/fast-forward the final 1.5 tree into it first.
No feature from an older 1.5 tree may silently survive as a competing implementation.

---

# STAGE A — CLOSE BOSSMAN 1.5

## A1. Economy-first model stack

Use Bossman itself as the orchestrator.

Default cloud worker roles:

1. `nvidia/nemotron-3-ultra-550b-a55b:free`
   - Agent N1: extractor/researcher
   - Agent N2: skeptic/red-team
   - Agent N3: curriculum/skills/workflows/memory builder

2. `inclusionai/ling-3.0-flash-fin:free`
   - coding
   - finance/trading logic
   - tests
   - bounded patches
   - verifier preparation

3. `z-ai/glm-5.3-flash`
   - paid finalizer only
   - only after the free workers fail, disagree, or leave a release blocker
   - hard cost cap
   - no silent paid fallback

4. Jev
   - cheap typed routing
   - task classification
   - worker ordering
   - retry/escalation
   - strong-verifier request
   - never expands authority
   - never authorizes payment
   - never overrides STOP / never / ask / allowed

5. Codex
   - owner integrator only
   - reads compact evidence/diffs/failing tests
   - does NOT do bulk transcript reading, repetitive coding, or routine testing when Bossman workers can do it

6. Aster
   - overall coordinator + independent auditor
   - no routine implementation
   - must not implement a fix and then certify its own fix as independent evidence

## A2. YouTube trading-learning lane

Target source: K1m6a public YouTube material.

Target date window:

`2026-08-14 -> 2026-08-27`

Use the owner's exact K1m6a YouTube URL when available.

Pipeline:

```
discover videos
 -> local download/captions/ASR/frames
 -> local vision extraction
 -> UNVERIFIED evidence episodes
 -> Nemotron extractor
 -> Nemotron skeptic
 -> Nemotron curriculum builder
 -> Ling verification/tests/code tasks
 -> optional GLM finalizer
 -> deterministic outcome checks
 -> QUARANTINE
 -> unseen transfer test
 -> only then candidate skill/workflow/memory promotion
```

Teacher statements are never ground truth.

Trading remains:

```
TRADING_EXECUTION=OFF
PAPER_ONLY=true
EXTERNAL_WRITE_ACTIONS=DENY
```

No exchange credentials are needed for 1.5 acceptance.

## A3. Learning targets

Improve Bossman through:

- verified skills;
- executable workflows;
- memory lessons;
- routing policy;
- tool-selection examples;
- recovery recipes;
- distillation candidates;
- failure patterns.

Do not claim weight training unless weights actually change through a separately recorded training job.

Required causal test:

```
BEFORE
 -> verified lesson/workflow
 -> full restart
 -> unseen analogous task
 -> AFTER
```

Record:

- pass@1;
- retries;
- tool errors;
- intervention count;
- verifier result;
- cost;
- duration.

## A4. 1.5 acceptance

1.5 is closed only when:

- economical orchestrator is runnable through Bossman;
- Jev routing/fallback/STOP works;
- three independent Nemotron roles execute;
- Ling worker executes tests/coding scenarios;
- paid GLM is bounded and free-first;
- secret/private egress negative tests pass;
- YouTube episodes stay quarantined until independently verified;
- hidden owner scenarios execute;
- restart/resume works;
- no release-blocking P0/P1;
- one exact 1.5 SHA is frozen;
- final evidence report exists.

Expected final report:

`docs/owner/BOSSMAN_1_5_FINAL_20260925.md`

Record:

`BOSSMAN_1_5_FINAL_SHA=<sha>`

Then freeze Stage A.

---

# STAGE B — CLOSE BOSSMAN 1.6

## B1. Main goal

Bossman 1.6 is the first version where the central product goal is:

> Bossman continuously improves Bossman using measured evidence, while Aster and Codex become supervisors/fallbacks instead of the main workers.

The target loop:

```
task failures / new tasks / new public knowledge
 -> Jev classification
 -> cheapest capable worker
 -> candidate fix/skill/workflow
 -> isolated execution
 -> deterministic + adversarial verifier
 -> lesson
 -> restart
 -> unseen transfer
 -> measurable comparison
 -> promote winner
 -> retain rollback
 -> repeat
```

## B2. Provider expansion

Bossman may discover additional legitimate free/low-cost LLM providers to reduce dependence on OpenRouter limits.

Required provider workflow:

```
DISCOVER
 -> CURRENT API DOCS
 -> TERMS / AUTH METHOD
 -> COST / FREE QUOTA
 -> MODEL CATALOG
 -> ADAPTER
 -> SECRET REFERENCE
 -> ONE TEST CALL
 -> TOOL/JSON TEST
 -> RATE-LIMIT TEST
 -> FAILOVER TEST
 -> REGISTER IN BOSS MAN FLEET
```

Rules:

- no credential scraping;
- no bypass of provider limits;
- no fake accounts;
- no captcha/phone/identity circumvention;
- no automatic acceptance of legal terms on the owner's behalf;
- if signup needs human identity/phone/legal acceptance, mark `OWNER_REQUIRED`, prepare the exact minimal steps, then continue with other providers;
- never commit provider keys;
- free quota is not treated as infinite;
- rate limit exhaustion must fail over cleanly.

Goal: Bossman should be able to use multiple providers and rotate only according to legitimate quotas/cost policy.

## B3. Self-improvement authority

Self-improvement may automatically:

- inspect source;
- create isolated candidate worktrees;
- run tests;
- propose patches;
- create skills;
- create workflows;
- create memory lessons;
- run hidden/unseen tests;
- compare candidates;
- reject bad candidates;
- maintain provider/model performance statistics.

Promotion into the canonical product still requires existing Bossman release policy.

Never allow a model to:

- delete release history;
- force-push;
- disable safety/verifier gates;
- raise its own permissions;
- silently increase spending limits;
- replace evidence with self-reported PASS.

## B4. Minimum 1.6 proof

Run at least three complete self-improvement cycles.

Each cycle:

```
FAILURE
 -> local/free student attempt
 -> verifier
 -> teacher/worker help only if required
 -> verified lesson
 -> full restart
 -> new unseen related task
 -> independent result
```

At least one cycle must show measurable transfer improvement versus baseline.

If all three cycles only produce teacher patches with zero student transfer gain, self-improvement is not proven.

## B5. Economics

The system must learn when NOT to use expensive models.

Track by role/provider:

- attempts;
- success rate;
- verified success rate;
- tool accuracy;
- schema accuracy;
- retries;
- p50/p95 latency;
- input/output tokens;
- reported cost;
- cost per verified result;
- fallback rate.

Routing objective:

```
minimize(expected_cost + latency_penalty + failure_penalty)
subject to:
  quality gate
  safety gate
  owner authority
  exact verification
```

Free does not mean preferred if repeated failure costs more total time and finalizer usage.

## B6. Aster's final role

Aster's main task tomorrow:

**MAKE THE SELF-IMPROVEMENT LOOP ACTUALLY RUN.**

Aster coordinates and audits:

- source truth;
- branches;
- workers;
- evidence;
- regression;
- adversarial retest;
- cost;
- transfer gain;
- final freeze.

Aster should delegate coding/testing/research to Bossman workers first.

Aster must not become the permanent implementation engine.

Target end state:

```
OWNER
  |
BOSSMAN
  |
JEV ROUTER
  |
FREE / LOCAL WORKER FLEET
  |
VERIFIER
  |
SKILLS + WORKFLOWS + MEMORY + CANDIDATE PATCHES
  |
UNSEEN TRANSFER
  |
PROMOTION / REJECTION
```

Codex and Aster remain escalation layers.

---

# ONE-DAY ORDER

Tomorrow execute exactly in this order:

1. Fetch current remote.
2. Prove frozen 1.0 is not being modified.
3. Finish 1.5 branch.
4. Run 1.5 acceptance.
5. Freeze `BOSSMAN_1_5_FINAL_SHA`.
6. Bring exact final 1.5 into the 1.6 branch.
7. Run provider discovery/fleet expansion.
8. Start the self-improvement loop.
9. Complete three measured cycles.
10. Run unseen transfer.
11. Run Aster independent audit.
12. Freeze `BOSSMAN_1_6_FINAL_SHA`.
13. Write final reports.
14. Stop. Do not start 1.7.

---

# DAY SUCCESS TABLE

| Stage | Required final state |
|---|---|
| Bossman 1.0 | frozen; untouched by 1.5/1.6 work |
| Bossman 1.5 | exact final SHA + economy orchestrator + learning pipeline + acceptance |
| Bossman 1.6 | exact final SHA + running self-improvement loop + measured transfer evidence |
| Jev | routing authority only; cannot expand permissions |
| Nemotron | three independent free worker roles |
| Ling | free coding/testing/finance worker |
| GLM 5.3 Flash | bounded paid finalizer |
| Codex | reduced to integration/escalation |
| Aster | coordination + independent audit |
| Trading | data/research/paper only |
| Provider expansion | legitimate providers only; no quota/account circumvention |
| Learning | verified skills/workflows/memory; weight claims only if actually trained |

Final status must be factual:

```
BOSSMAN_1_5_FINAL_SHA=
BOSSMAN_1_5_STATUS=PASS|PARTIAL|BLOCKED

BOSSMAN_1_6_FINAL_SHA=
BOSSMAN_1_6_STATUS=PASS|PARTIAL|BLOCKED

SELF_IMPROVEMENT_CYCLES=
UNSEEN_TRANSFER_GAIN=
FREE_PROVIDER_FLEET=
PAID_COST_USD=
OPEN_P0=
OPEN_P1=
OPEN_P2=
```

Main rule for the day:

**Do not spend the day planning self-improvement. Make Bossman execute the self-improvement loop, measure whether it helped on unseen work, and preserve only verified gains.**
