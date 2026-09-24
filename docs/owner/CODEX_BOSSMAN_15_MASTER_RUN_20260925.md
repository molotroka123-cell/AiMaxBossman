# CODEX MASTER RUN — BOSSMAN 1.5 ECONOMY LEARNING

Date target: 2026-09-25

Repository:
molotroka123-cell/AiMaxBossman

Working branch:
feat/bossman-1.5-economy-learning-20260924

Owner intent:
finish and prove the Bossman 1.5 economy-learning lane while minimizing Codex
and paid-model usage.

This is an execution directive. Do not replace it with another plan.

## Absolute branch rule

Do not develop 1.5 on release/bossman-owner.

Bossman 1.0 is frozen separately. Never move, reset, rewrite or force-push the
1.0 release line while running this packet.

Start with:

    git fetch --all --prune
    git status --short
    git rev-parse origin/release/bossman-owner
    git rev-parse origin/feat/bossman-1.5-economy-learning-20260924

Checkout the 1.5 branch and preserve every remote commit.

No force-push.
No history rewrite.
No final-final branches.
No deletion of useful evidence.

## Read first

Read:

1. docs/v1.5/BOSSMAN_15_ECONOMY_SWARM.md
2. docs/v1.5/ACCEPTANCE_AND_LEARNING.md
3. docs/trading/YOUTUBE_TEACHER_INGEST.md
4. docs/owner/JEV_TOMORROW.md
5. docs/JEV_DECISION_ENGINE.md
6. command-center/bcc/features/economy_swarm.py
7. tools/bossman_15_setup.py
8. tools/k1m6a_youtube_batch.py
9. tools/bossman_15_economy_run.py
10. tools/bossman_15_memory_sync.py

Use repository behavior and current live evidence as truth. Documentation is not
a PASS by itself.

## Main principle

CODEX IS THE SCARCE INTEGRATOR, NOT THE BULK WORKER.

Before Codex spends its own reasoning/code budget on a task, ask:

Can Bossman do this through the configured free workers first?

Default execution order:

1. three free Nemotron workers;
2. free Ling verifier/tester/coder;
3. deterministic tests;
4. one paid GLM-5.3-Flash finalizer only if still needed;
5. deterministic tests;
6. Aster audit/control;
7. Codex integrates or resolves only what remains.

Do not use Codex to manually perform every video extraction, every first-pass
code review or every routine test-fix iteration.

## Model stack — exact ids

All model work must go through Bossman's canonical OpenRouter provider and
Bossman task engine.

Do not call OpenRouter directly from the owner workflow.

Bulk workers, three Bossman agents in parallel:

nvidia/nemotron-3-ultra-550b-a55b:free

Free finance verifier/tester/coder:

inclusionai/ling-3.0-flash-fin:free

Only paid finalizer:

z-ai/glm-5.3-flash

Aster:

AUDIT AND OVERALL CONTROL ONLY.
Aster does not write product code in this lane.

Jev:

required typed stage router.
Jev does not authorize spending, change budgets or bypass policy.

Do not silently substitute model ids. Sync Bossman's live OpenRouter catalog
first. If a free id is no longer zero-priced, refuse it as a free worker and
record the catalog evidence.

## Phase 1 — Bossman setup

Bossman and the existing OpenRouter key are already configured on the owner
machine.

Do not ask the owner to paste the key again.
Do not print, log or commit it.

Run:

    python tools\bossman_15_setup.py --glm-budget-usd 0.25 --out owner-test-pack\v15\setup.json

This must:

- use Bossman's OpenRouter catalog;
- pin the exact three model ids;
- create/update three Nemotron roles;
- create/update Ling-Fin-Verifier;
- create/update GLM53-Finalizer;
- probe capabilities through Bossman;
- confirm /api/economy/status;
- leave secrets in Bossman's vault.

If setup fails because of a provider/rate-limit problem, fix/retry the provider
path before changing the model choice.

## Phase 2 — Jev

Jev is mandatory for stage transitions.

Check the existing Jev owner path and contract.

The economy workflow must use:

POST /api/economy/route

with require_jev=true.

Hard invariant:

Jev sees only the actions already allowed by deterministic policy.

It must never make GLM available before:

- free Nemotron work exists;
- Ling has been attempted;
- Ling still reports a blocker;
- explicit paid allowance is on;
- paid budget remains;
- GLM call count is zero.

If Jev is unavailable, the workflow is BLOCKED_JEV.

Do not silently bypass Jev with Codex.

## Phase 3 — K1m6a source

Owner source:

https://www.youtube.com/@k1m6a/videos

Requested default date window, inclusive:

2026-08-14 through 2026-08-27.

First discover only:

    python tools\k1m6a_youtube_batch.py --start 2026-08-14 --end 2026-08-27 --discover-only --root owner-test-pack\v15\youtube

Save the discovered titles, video ids, dates and URLs.

Do not silently widen the date range.

If the exact window contains no videos, inspect channel metadata and report the
fact. Do not invent missing videos.

Then ingest:

    python tools\k1m6a_youtube_batch.py --start 2026-08-14 --end 2026-08-27 --root owner-test-pack\v15\youtube

For every selected public video:

- captions first;
- local ASR fallback;
- frame sampling;
- local vision;
- deterministic market analysis;
- future in-video outcomes when available;
- candidate_cases.jsonl;
- status UNVERIFIED.

Do not upload raw video, voice, frames or private browser material to a free
cloud model.

Cloud workers get only the bounded public structured episode bundle produced by
Bossman.

## Phase 4 — three Nemotron workers

For each ingested video, Bossman launches all three roles before moving on:

YT-Nemotron-Transcript
- teacher claims;
- triggers;
- invalidations;
- timestamps;
- opinion vs observation.

YT-Nemotron-Chart
- Price/CVD/OI consistency;
- series identity;
- structural levels;
- missing data;
- contradictions.

YT-Nemotron-Strategy
- candidate hypotheses;
- regime;
- trigger;
- invalidation;
- future-outcome evidence;
- counterexamples.

All outputs remain UNVERIFIED.

A Nemotron worker cannot promote memory.
A model saying DONE is not evidence.

Three agents should be submitted together so Bossman's worker pool can process
them concurrently.

If a free provider returns transient 429/5xx or an HTTP-200 in-body transient
error, use the bounded provider retry path. Do not interpret it as model
stupidity and do not immediately buy a paid call.

## Phase 5 — Ling free verifier and coder

Every video bundle plus all three Nemotron outputs goes to:

Ling-Fin-Verifier

Ling must:

- compare output against the source episode bundle;
- reject unsupported claims;
- preserve UNKNOWN;
- identify missing evidence;
- return PASS / FAIL / BLOCKED plus unresolved blockers;
- never promote a trading rule merely because Nemotron and Ling agree.

For repository code:

Ling is the first-line coder before Codex.

Give Ling failing test output and the exact relevant files.

Required workflow:

REPRODUCE
-> minimal patch
-> target tests
-> neighbour tests
-> negative control
-> deterministic verifier

Do not spend Codex tokens rewriting a patch Ling can produce and pytest can
judge.

## Phase 6 — deterministic verification

Run the actual targeted test suite, not a model opinion.

At minimum:

- command-center/tests/test_economy_swarm.py
- command-center/tests/test_market_collector.py
- command-center/tests/test_video_learning_primitives.py
- tests/test_distill_recorder.py
- tests/test_bossman_15_economy_scripts.py

Also run affected neighbour tests after any patch.

False DONE is a release failure.

## Phase 7 — paid GLM gate

GLM 5.3 Flash is paid and is a FINALIZER only.

Default owner budget for this run:

USD 0.25

No auto-recharge.

Use:

z-ai/glm-5.3-flash

Only if all are true:

- free workers were used first;
- Ling attempted verification/repair;
- deterministic tests still fail or a real blocker remains;
- Jev selected glm_finalize from the allowed set;
- remaining paid budget is positive;
- no previous GLM finalizer call was made.

Maximum:

ONE GLM finalizer task for the entire batch/repository closure.

Do not use one GLM call per video.

After GLM:

run deterministic tests again.

If cost is unknown after the call, mark COST_RECONCILE_REQUIRED and do not make
another paid call.

## Phase 8 — run the owner economy workflow

Run:

    python tools\bossman_15_economy_run.py --batch-manifest owner-test-pack\v15\youtube\batch-manifest.json --out owner-test-pack\v15\economy --repo-polish --allow-paid --glm-budget-usd 0.25

This must generate:

- economy-report.json;
- lesson-candidates.jsonl;
- ASTER_AUDIT_PACKET.md;
- task/run ids;
- token/cost accounting;
- free-cost violations if any;
- paid GLM count/cost;
- final deterministic test result.

No model inference in this workflow may bypass Bossman.

## Phase 9 — skills and memory

Use the role-specific skill profiles already encoded in the workflow.

Do not confuse a skill name in a prompt with proof of learning.

After successful execution:

    python tools\bossman_15_memory_sync.py owner-test-pack\v15\economy\economy-report.json

This may promote only operational workflow lessons supported by independent
deterministic evidence, such as:

- free-first routing before paid escalation;
- deterministic proof-before-DONE.

YouTube trading hypotheses stay quarantined.

Do not promote an AUTHOR_CLAIM into procedural trading memory.

Trading-memory promotion still requires its existing independent evidence,
multiple episodes, lookahead-clean outcome and out-of-sample gates.

Weights remain unchanged in this workflow.

Report:

WEIGHTS_UNCHANGED

unless a separate, explicitly executed weight-training job actually happened.

## Phase 10 — fresh scenarios

Use the prepared owner-20260924 scenario set plus fresh unseen variants.

At minimum exercise:

- hidden defect;
- misleading test;
- Windows path;
- false DONE;
- tool schema;
- free provider transient error;
- Jev unavailable;
- Ling failure;
- paid budget exhausted;
- GLM already used;
- restart before final verification;
- memory lesson after restart;
- an unseen analogous task using the verified workflow lesson.

Holdout tests and answers must not be shown to the learner.

Measure BEFORE and AFTER when claiming a learning gain.

Memory hit alone is not transfer gain.

## Phase 11 — Aster

Give Aster:

owner-test-pack\v15\economy\ASTER_AUDIT_PACKET.md

plus relevant git diff and test evidence.

Aster responsibilities:

- overall run supervision;
- independent audit;
- find false PASS;
- inspect model-cost routing;
- audit permissions/privacy;
- audit lesson provenance;
- challenge the transfer claim;
- produce P0/P1/P2 and blockers.

Aster must not write or repair product code.

If Aster finds a code defect:

1. send it back to the free Ling repair lane first;
2. run deterministic tests;
3. use paid GLM only if the normal paid gate still allows it;
4. Codex handles the residual only after those lanes are exhausted.

## Phase 12 — Codex budget discipline

Codex must actively minimize its own work.

Codex should personally do only:

- branch/source-of-truth reconciliation;
- reviewing agent diffs;
- resolving conflicts;
- investigating a blocker the free/paid Bossman lanes could not close;
- independent security/release review;
- final convergence and handoff.

Codex should not personally do:

- bulk video summarization;
- three-way strategy extraction;
- first-pass tests;
- ordinary code fixes before Ling;
- repeated code review before Ling/GLM;
- Aster's independent audit.

At end report estimated avoided Codex work:

- videos delegated;
- Nemotron tasks;
- Ling tasks;
- paid GLM tasks;
- direct Codex fixes;
- paid USD;
- unknown cost.

## Phase 13 — no live trading

This entire K1m6a lane is:

READ_ONLY_LEARNING / PAPER RESEARCH

No live order.
No exchange write credentials.
No automatic trade from Telegram.
No probability-of-profit claim from classification confidence.

The purpose is to build verified datasets, scenarios, memory and paper-tested
strategy knowledge.

## Phase 14 — completion

Do not call Bossman 1.5 finished merely because code compiles.

Final report:

docs/owner/BOSSMAN_15_OWNER_RUN_FINAL.md

Include:

BRANCH
HEAD_SHA
BASE_1_0_SHA

OPENROUTER
- exact model ids;
- catalog pricing;
- capabilities;
- provider failures.

YOUTUBE
- requested date window;
- discovered videos;
- ingested PASS/FAIL;
- local evidence coverage.

NEMOTRON
- three roles per video;
- task ids;
- PASS/BLOCKED;
- tokens;
- cost.

LING
- verification;
- code repairs;
- tests;
- cost.

GLM
- used yes/no;
- task id;
- measured cost;
- reason escalation was allowed.

JEV
- stage decisions;
- blocked decisions;
- no paid-bypass proof.

MEMORY
- candidate lessons;
- verified workflow lessons;
- restart retrieval;
- unseen transfer outcome;
- trading lessons still quarantined.

ASTER
- audit verdict;
- P0/P1/P2;
- contradictions.

CODEX
- direct work actually performed;
- work delegated to Bossman;
- remaining blockers.

TESTS
- exact commands;
- PASS/FAIL;
- hidden/fresh scenarios.

ECONOMY
- free tasks;
- paid tasks;
- total known paid USD;
- unknown cost;
- estimated Codex work avoided.

Final status must be one of:

BOSSMAN_1_5_OWNER_LIVE_PASS
BOSSMAN_1_5_PARTIAL
BOSSMAN_1_5_BLOCKED

No fake PASS.

## Execution style

Do not stop after writing plans.

Execute available local/reversible work without repeatedly asking the owner.

Preserve existing never/ask/allowed, STOP, privacy, budget and approval
boundaries for real external effects.

If an external dependency is genuinely unavailable, save all completed evidence,
name the exact blocker and continue every independent lane that can still run.

The objective is not maximum model usage.

The objective is:

maximum verified useful work per owner dollar and per Codex token.
