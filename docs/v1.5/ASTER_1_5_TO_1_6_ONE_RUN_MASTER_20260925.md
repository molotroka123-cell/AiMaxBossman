# ASTER — ONE RUN MASTER: Bossman 1.5 -> 1.6

> **ABSOLUTE CORRECTION — ASTER WRITES NO PRODUCT CODE.**
>
> Aster only coordinates, launches, measures, audits and reports. It cannot be
> the author of product fixes, patches, commits or merges. Coding/testing bulk
> work must run through Bossman and its local/free-first worker stack. Provider
> registration is never automatic: missing accounts/keys become OWNER_REQUIRED
> packets for the owner, preferably delivered through Telegram owner-input.
>
> If Aster finds a defect, its output is only a reproducer + failing test +
> evidence packet for Bossman.


Date: 2026-09-25
Start branch: `feat/bossman-1.5-economy-orchestrator-20260924`
Next branch: `feat/bossman-1.6-self-evolution-20260925`

## Mission

Do not spend the owner run planning another development cycle.

The run has two consecutive deliverables:

1. close Bossman 1.5 with real owner/runtime evidence;
2. without stopping, hand the exact accepted 1.5 state into Bossman 1.6 and leave Bossman's own self-evolution process running.

Primary sequence:

`1.5 FLEET TRUTH -> 1.5 OWNER READY -> SELF-IMPROVEMENT STARTED -> ASTER DETACH -> 1.6 HANDOFF -> FIRST SELF-EVOLUTION CYCLE`

Aster is the bootstrap auditor/coordinator, not the routine coder.

Codex is the owner integrator of last resort, not the bulk worker.

Claude is not required for the normal loop.

## 0. Source truth

```
git fetch --all --prune
git status --short
git rev-parse origin/feat/bossman-1.5-economy-orchestrator-20260924
git rev-parse origin/release/bossman-owner
```

No force-push.
No history rewrite.
No new final-final branches.
No feature expansion before owner evidence.

Read first:

- `config/v1.5/model-routing-stack.json`
- `config/v1.5/self-improvement.json`
- `config/v1.5/provider-pool.json`
- `docs/v1.5/ASTER_SELF_IMPROVEMENT_MASTER_20260925.md`
- `docs/v1.5/TELEGRAM_LOGIN_PRIVACY.md`
- `docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md`
- `docs/v1.5/BOSSMAN_1_5_FINAL_CLOSURE_20260925.md`

## 1. FIRST ACTION — refresh the real owner model fleet

Before YouTube, coding, provider signup work or long acceptance, resolve this owner routing intent to actual installed model IDs/runtime/quantization:

```text
OWNER
  ↓
BOSSMAN / JEV
  ├─ simple             -> Qwen3.6
  ├─ code/project       -> Qwen3.8 -> Xing / Occamy workers
  ├─ screen/browser     -> Nex-N2.5
  ├─ very hard          -> Flash-Next
  ├─ verifier           -> gpt-oss-120B
  ├─ image              -> Qwen-Image-2.1
  └─ video              -> LTX-2.5 / Wan
```

For every role record:

- exact served model id;
- endpoint/runtime;
- quantization if available;
- load PASS/FAIL;
- role-specific functional test;
- TTFT;
- tok/s when meaningful;
- RAM/unified memory;
- restart result;
- status GREEN / DEGRADED / BLOCKED / UNVERIFIED.

Write:

`owner-run/v1.5/model-routing-stack-live.json`

No silent alias replacement.

A fallback may become active only after passing the same role gate.

The verifier must be independent from the worker whose output it verifies.

## 2. Start self-improvement EARLY

Do not wait for every optional specialist/provider.

Minimum safe start:

- clean source identity;
- one verified coding route;
- one independent verifier;
- Jev/controller alive;
- STOP alive;
- budget controls alive;
- isolated candidate/worktree path alive.

Once those pass, immediately run Bossman's own bootstrap:

```
tools/bossman_15_self_improve.py status
tools/bossman_15_self_improve.py start
```

Milestone:

`SELF_IMPROVEMENT_PROCESS_STARTED`

Provider expansion, YouTube learning, Twitch collection and remaining owner acceptance continue in parallel.

Aster must not substitute its own patches for Bossman self-improvement.

## 3. Free-first worker economy

All bulk cloud work goes through Bossman.

### Teacher/reviewer swarm

Three independent agents, same model but separate roles/prompts/evidence:

`nvidia/nemotron-3-ultra-550b-a55b:free`

Roles:
1. evidence extractor;
2. strategy/synthesis worker;
3. adversarial skeptic.

### Coding/test worker

`inclusionai/ling-3.0-flash-fin:free`

Pattern:

`REPRODUCE -> RED -> LING PATCH -> TARGET TEST -> NEIGHBOURS -> NEGATIVE CONTROL -> VERIFIER`

### Paid finalizer

`z-ai/glm-5.3-flash`

Only after a verified unresolved blocker.
Hard policy cap remains authoritative.
No hidden paid fallback.
Unknown cost blocks further paid calls.

### Codex

Codex receives only compact evidence:
- failing tests;
- minimal diff;
- verifier reason;
- remaining blocker.

Do not feed Codex raw videos, giant logs or full repo context when a free worker can do the task.

## 4. Provider capacity

Use in this order:

1. verified local models;
2. already-configured zero-cost providers;
3. OpenRouter free capacity;
4. additional legitimate owner-configured free providers;
5. bounded paid finalizer.

Bossman/Aster may research legitimate providers and prepare OWNER_REQUIRED onboarding packets.

The packet must contain:
- provider name;
- official signup URL;
- free-tier finding;
- fields/actions the owner must complete;
- expected secret/env reference;
- post-key live verification step.

Do not:
- fabricate identities;
- create quota-evasion accounts;
- bypass CAPTCHA;
- auto-accept ToS;
- scrape credentials;
- auto-recharge.

Provider onboarding must not stop self-improvement when another verified route already exists.

## 5. K1m6a trading-learning run

Public YouTube window:

**2026-08-14 through 2026-08-27**

Use the configured K1m6a source.

Pipeline:

`discover -> local captions/ASR/vision -> typed evidence -> 3x Nemotron -> Ling verifier -> optional GLM -> quarantine -> independent outcome -> replay/backtest -> paper -> measure`

Raw teacher claims are UNVERIFIED.

Future outcomes never enter teacher prompts.

Trading strategy promotion must use the existing TradingMemory anti-lookahead/out-of-sample/verifier gates.

No live trading.
No exchange write credentials.

Twitch OI/CVD remains evidence-backed and read-only.

## 6. Login/password privacy owner test

Password rule is stronger than "local model only":

**the password is not visible to any model at all.**

The local runtime:
- reads the encrypted credential vault;
- inserts the password directly into the browser secret field;
- redacts browser output again.

Telegram receives only:
- account/login;
- names of ordinary post-login fields;
- one fresh post-login screenshot after verified success.

For the controlled test, `browser.login` must use a concrete `success_url_contains`.

PASS requires:
- credential domain matches the page;
- login route is local-only;
- URL changes to the expected post-login state;
- exactly one fresh success screenshot is sent;
- no password/OTP/API key/card secret reaches Telegram/model/tool args;
- incoming `/input` value message + bot ack + login transient messages are deleted after completion;
- failed/unverified login produces no LOGIN PASS.

## 7. Prove runtime self-repair

Plant one bounded reversible code defect in an isolated candidate/test environment.

Required trace:

`real task failure -> durable repair inbox -> isolated worktree -> Bossman coding worker -> executable regression -> green target test -> independent verifier -> candidate branch`

Stable/release remains unchanged.

Model says DONE != PASS.

Successful repair state before transfer:

`NOT_PROMOTED_REQUIRES_UNSEEN_TRANSFER`

## 8. Prove learning

Run a second unseen task in the same family.

Measure BEFORE vs AFTER:

- verified success;
- retries;
- tool/schema errors;
- owner interventions;
- time;
- cost.

For skills:
`NO_SKILL vs SKILL_ENABLED`.

A memory hit is not learning.

A teacher patch is not student success.

Only independently verified unseen-transfer gain may promote a skill/workflow/memory candidate.

## 9. Scientific self-improvement cycle

At least one cycle must execute:

1. hypothesis;
2. baseline;
3. isolated candidate;
4. benchmark;
5. regression;
6. independent verifier;
7. unseen transfer;
8. promote/reject;
9. durable evidence/lesson;
10. restart continuity.

A rejected candidate is a valid experiment.

Target after one cycle:

`SELF_REPAIR_SINGLE_CYCLE_PASS`

Strong target:

`TRANSFER_MEASURED_GAIN`

## 10. Persistent society / skill compiler / operating graph / resource manager

Verify all five autonomy pillars:

- Scientific Self-Improvement;
- Persistent Agent Society;
- Skill Compiler;
- Personal Operating Graph;
- Autonomous Resource Manager.

At minimum prove:
- agent performance history survives restart;
- later team choice uses verified history;
- one verified trace becomes an EXPERIMENTAL skill candidate;
- graph relation survives restart and supports historical `as_of`;
- resource manager rejects below-quality, unknown-price and over-RAM routes;
- cheapest sufficient verified route wins.

## 11. Owner supervision

Command Center, `Bossman-1.5.cmd` and Telegram must address the same backend/data root.

Test:
- status;
- start;
- STOP;
- resume;
- owner input;
- approval;
- login privacy flow;
- self-repair state.

No second hidden runner or competing state store.

## 12. Aster detachment

Once the Bossman self-improvement campaign is running and one scientific cycle has a verifier outcome:

1. disconnect Aster from routine execution;
2. do not use Codex/Claude for routine steps;
3. restart/continue Bossman;
4. run status/control;
5. run one new bounded task;
6. prove society/memory/skills/resource routing still function.

Required milestone:

`ASTER_CODEX_DETACHED_CONTINUITY_PASS`

## 13. Close 1.5

1.5 can become OWNER_READY only with:
- no confirmed P0;
- no release-blocking P1;
- critical owner paths proven;
- self-improvement process actually started;
- STOP/restart proven;
- no false PASS;
- exact evidence bound to current candidate.

Write:

`docs/owner/BOSSMAN_1_5_OWNER_RUN_FINAL.md`

If optional P2/provider/specialist work remains, list it but do not stall the next stage.

## 14. IMMEDIATE HANDOFF TO 1.6

Do not end the run after 1.5 when the critical gate allows continuation.

Next branch:

[Bossman 1.6 self-evolution](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Before doing 1.6 work, merge the exact accepted 1.5 SHA into the 1.6 branch preserving history.

Read:

`BOSSMAN_1_6_START_HERE.md`

Then continue in the same owner session.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

1.6 first job is not more feature coding. It is to keep the verified Bossman self-evolution loop operating without Aster/Codex/Claude routine dependence.

## Final owner report

The end-of-run report must state separately:

### 1.5
- SHA;
- model fleet truth table;
- owner paths;
- trading-learning evidence;
- provider capacity;
- login privacy evidence;
- self-repair cycle;
- transfer gain;
- P0/P1/P2;
- spend;
- verdict.

### 1.6
- handoff SHA;
- self-evolution campaign;
- Aster/Codex detached status;
- experiments promoted/rejected;
- new skills/workflows/memory;
- provider/resource routing;
- first autonomous transfer delta;
- blockers.

## Absolute truth rules

- No fabricated PASS.
- No old-SHA evidence transferred to new code.
- No model prose as verifier evidence.
- No teacher claim as trading truth.
- No live trading.
- No silent paid fallback.
- No password in Telegram/model/tool args.
- No automatic quota-evasion account creation.
- No stable self-write without verifier + unseen transfer.
- No external auditor as permanent runtime dependency.
- No stopping after 1.5 if 1.6 entry gate is satisfied.

The owner's target for the day is **both stages in one run**.
