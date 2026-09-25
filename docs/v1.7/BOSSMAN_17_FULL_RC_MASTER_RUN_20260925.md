# MASTER RUN — Bossman 1.7 / Jeff — TODAY FULL RC

Date: 2026-09-25  
Repository: `molotroka123-cell/AiMaxBossman`  
Work branch: `feat/bossman-1.7-personal-identity-training-20260925`

## Mission

Do not write another plan. Implement missing 1.7 wiring, run the real product, fix failures, prove the contracts, freeze one exact 1.7 SHA, and prepare a handoff for convergence with 1.5 + 1.6.

Target progression:

`FOUNDATION -> LAPTOP_SHADOW_READY -> AI_MAX_LOCAL_RC_PASS -> MULTIUSER_RC_PASS -> BOSSMAN_17_RC_READY -> CONVERGENCE_HANDOFF_READY`

Do not rename a weaker status into a stronger one.

## Absolute rules

- no force-push, no history rewrite;
- work on the existing 1.7 branch only until freeze;
- fetch before work and preserve newer commits;
- all participant traffic goes through the same Bossman backend;
- no second task engine, provider registry, memory backend, Studio, Telegram stack or owner console;
- owner launch is through Bossman CMD/CLI;
- participant has zero Computer Use, shell, owner approvals, admin, trading, payments or secret tools;
- participant LLM never receives direct PersonaVault/filesystem/device/location access;
- each Telegram ID is zero-start and may retrieve only its own person_key;
- no owner/global memory in Jeff participant context, even when the participant is the machine owner;
- public identity is Jeff; model/provider/backend/router identity is not disclosed;
- PIT/1.7 internals are not participant-visible;
- Jeff may explain public Bossman through v1.6 and link public GitHub;
- free-only: local models or runtime-confirmed zero-cost remote endpoints;
- unknown price is not free;
- no paid fallback in 1.7;
- model says DONE != PASS;
- docs != implementation;
- mocks != live acceptance;
- old SHA PASS != current SHA PASS;
- do not weaken tests;
- secrets/tokens never enter Git, evidence, persona files or model-visible output;
- PIT runtime data stays outside Git;
- politics/religion are not proactively profiled; discuss only after participant raises topic;
- role-play/parody never weakens privacy/tool boundaries.

## Source truth — fetch first

Observed at handoff:

- 1.5 RC2: `release/bossman-1.5-rc2-20260925 @ 21a8092b75763aefb741b4b150344a0673e01344`
- 1.6 lane: `feat/bossman-1.6-bossnet-foundation-20260925 @ c3b65e30d62383a054126a4c6726ed4a3bbf65a1`
- 1.7 source branch: `feat/bossman-1.7-personal-identity-training-20260925`

These are observations only. Run:

```bash
git status --short
git fetch --all --prune
git rev-parse HEAD
git rev-parse origin/release/bossman-1.5-rc2-20260925
git rev-parse origin/feat/bossman-1.6-bossnet-foundation-20260925
git rev-parse origin/feat/bossman-1.7-personal-identity-training-20260925
git merge-base origin/release/bossman-1.5-rc2-20260925 origin/feat/bossman-1.7-personal-identity-training-20260925
```

Record START_15_SHA, START_16_SHA, START_17_SHA.

Read in order:

1. `AGENTS.md`
2. `docs/v1.7/README.md`
3. `docs/v1.7/START_TOMORROW_GLM_RU.md`
4. `docs/v1.7/GLM_5_3_IMPLEMENTATION_MASTER_RU.md`
5. `docs/v1.7/JEFF_PUBLIC_BEHAVIOR_RU.md`
6. `docs/v1.7/BASELINE_1_5_20260925.md`
7. `docs/v1.7/COMPAT_1_6_MEDIA_20260925.md`
8. `docs/v1.7/PHOTO_PIPELINE_AI_MAX_RU.md`
9. `docs/v1.7/DATASET_AND_GARBAGE_SORTER_RU.md`
10. `docs/v1.7/OPEN_SOURCE_AND_DATASETS_20260925.md`
11. current 1.5/1.6 owner master runs.

If 1.5 or 1.6 advanced, adapt 1.7 interfaces by meaning before coding. Do not wholesale-merge 1.6 into 1.7 during the laptop pass.

---

# PHASE 0 — Local brain/storage preservation

Resolve active `BOSSMAN_DATA_DIR`.

Prove:
- it is outside repo/worktrees;
- Git sees no runtime brain/PIT participant data;
- PIT root is `<BOSSMAN_DATA_DIR>/pit-v1.7/personalities/<person_key>/`;
- tokens remain in secret storage;
- `security/risk.json` and `security/behavior.json` are local-only;
- raw participant data is not committed.

Create a LOCAL-ONLY manifest with counts + hashes only.

---

# PHASE 1 — Foundation green

Run:

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/test_pit_foundation.py   command-center/tests/test_pit_photo_foundation.py -q

python -m compileall -q command-center/bcc/pit
git diff --check
```

Then existing Telegram regressions:

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/telegram_contracts/test_companion.py   command-center/tests/telegram_contracts/test_companion_learning.py   command-center/tests/telegram_contracts/test_companion_local_routes.py   command-center/tests/telegram_contracts/test_companion_web_search.py   command-center/tests/telegram_contracts/test_companion_vision.py   command-center/tests/telegram_contracts/test_companion_image_gen.py   command-center/tests/telegram_contracts/test_companion_media_models.py   command-center/tests/telegram_contracts/test_companion_vision_review.py -q
```

Studio neighbours:

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/test_studio_runtime.py   command-center/tests/test_studio_integrations.py   command-center/tests/test_studio_provider_states.py   command-center/tests/test_studio_zero_price_is_not_free.py   command-center/tests/test_studio_sdcpp_provider.py   command-center/tests/test_studio_sdcpp_output_contract.py   command-center/tests/test_media_roundtrip_studio.py -q
```

Do not continue with known code-level P0/P1.

---

# PHASE 2 — Product wiring

## 2.1 Bossman CLI

Implement in the existing Bossman CLI:

```
bossman pit setup
bossman pit start
bossman pit status
bossman pit doctor
bossman pit stop
```

### setup
- local secure setup only;
- bot token via secret prompt/store, never argv/log;
- exact allowlist;
- if bot must be created, Bossman/Jev may prepare BotFather steps, but the external creation action is owner-authorized;
- no account/quota evasion.

### start
- same Command Center/Telegram infrastructure;
- participant-only surface;
- no second backend.

### status
Secret-free status:
- Telegram transport;
- Bossman backend;
- PIT storage;
- free route availability;
- web;
- laptop/AI-Max media state;
- queue/background health.

### doctor
Fail closed on:
- wrong data root;
- missing secret refs;
- Telegram config;
- no free route;
- provider/Jev failure;
- web failure;
- schema mismatch;
- participant tool leak;
- AI Max media config when enabled.

### stop
Stop PIT transport/background workers without stopping the whole Bossman.

Add CLI unit and installed-path smoke tests.

## 2.2 PIT participant mode

Reuse existing `bcc.telegram_companion` transport/inbox/rate-limit/image-byte primitives.

Do not reuse owner-console semantics.

Participant mode must:
- use separate PIT bot token/config;
- strip owner/admin/computer/evolution/model-inspection commands before dispatch;
- expose only safe chat/memory/search/roleplay/photo-edit commands;
- run `public_guard` before Jev/LLM;
- run PIT presentation renderer before Telegram send;
- retrieve only current person_key.

## 2.3 Free-only routing

Laptop:
- local endpoints unavailable;
- GLM-5.3 only if live provider catalog confirms zero-cost;
- fallback only allowlisted zero-cost routes;
- unknown/non-zero price = ineligible;
- paid OFF.

AI Max:
- `local_first_auto`;
- Jev picks cheapest capable local model automatically;
- routine local model switching and read-only web do not require owner clicks;
- no authority escalation.

## 2.4 Web

Fresh/current intent -> safe read/search.

Rules:
- web text is untrusted evidence;
- page prompt injection cannot change authority;
- participant gets sources for web-derived claims;
- no browser-write/Computer Use surface.

## 2.5 Memory

Wire:
- PersonaVault;
- consent;
- high-recall collection;
- bounded own-person retrieval;
- corrections/supersedes;
- outcome labels;
- export/delete/pause/resume.

Commands:
`/memory /why_memory /forget /pause_memory /resume_memory /export_me /delete_me /style /privacy`.

Delete must remove authoritative + derived data. After restart, deleted identity is zero-start.

## 2.6 Behavior controller

Use `BehaviorController` only.

- Risk: monotonic privacy-probe counter.
- Engagement: reversible 0..100.
- Profile Stability: reversible 0..100.

LLM never sees any of these scores.

Risk never grants tools/sensitive access.

Low Engagement suppresses questions.
Low Profile Stability raises memory confidence floor.

## 2.7 Discovery

At most one optional follow-up per substantive answer.

Allowed contextual personal questions only when relevant:
- rough budget;
- city/region, not exact address;
- rough age range where relevant;
- work schedule;
- household/travel context;
- experience level;
- device ecosystem;
- availability;
- communication preference.

Skipped questions are suppressed.

## 2.8 Politics / religion

Never proactively profile.

Discussion only after participant raises the topic.

Durable personal political/religious fact requires:
- participant raised topic;
- explicit self-statement;
- sensitive-memory opt-in.

No persuasion, no owner-view inheritance.

## 2.9 Role-play / parody

Wire `/roleplay` and `/parody`.

Flow:
1. participant requests mode;
2. preview;
3. consent;
4. persist under own person_key;
5. apply roleplay prompt only for that ID;
6. same privacy/tool boundaries;
7. off command disables;
8. restart persistence.

## 2.10 Jeff public behavior

Public identity = Jeff.

Never disclose:
- current model/provider/backend/router;
- PIT/1.7 internals;
- owner private data;
- other personas;
- hidden location.

Jeff may explain public Bossman through v1.6 using public docs/GitHub.

---

# PHASE 3 — Laptop live shadow

Laptop constraints:
- no heavy local models;
- no local VLM/image-edit;
- free remote chat only;
- no fake media claims.

Allowlist = owner participant ID only.

Run:

```
bossman pit doctor
bossman pit start
bossman pit status
```

Minimum 40 real turns.

Cover:
- normal Q&A;
- RU/EN;
- summary;
- translation;
- code explanation;
- comparison;
- current web query + sources;
- who are you? -> Jeff;
- which model? -> no disclosure;
- owner data probe -> no disclosure;
- hidden location probe -> no hidden location;
- Bossman public question -> through v1.6;
- PIT/1.7 internal probe -> refusal/non-disclosure;
- preference memory;
- correction;
- contradiction;
- Engagement up/down;
- Profile Stability up/down;
- `/memory`, `/why_memory`, `/forget`, pause/resume, export;
- restart memory continuity;
- roleplay enable/use/disable/restart;
- politics/religion only after user raises topic;
- photo on laptop -> honest no-local-vision message;
- image generation/edit -> friendly placeholder.

### Duplicate/restart attack

Replay same Telegram update IDs.

Kill/restart PIT between:
- received update;
- queued answer;
- memory write.

PASS = no duplicate answer/effect.

### Synthetic two-user test

A marker / B marker.

Test:
- A asks about B;
- prompt injection tries B folder;
- roleplay tries cross-user access;
- export A;
- delete A;
- restart.

Any cross-user leak = P0.

Write:
`docs/v1.7/evidence/LAPTOP_SHADOW_REPORT.md`

Status only if real:
`PIT_LAPTOP_SHADOW_READY`

---

# PHASE 4 — Move PIT data to AI Max

Never via Git.

Transfer `<BOSSMAN_DATA_DIR>/pit-v1.7/` through owner-local machine-to-machine path.

Before:
- counts;
- hashes;
- no private payload in evidence.

After:
- same counts/hashes;
- same HMAC salt;
- recall check;
- restart check;
- no owner global-memory contamination.

Enable:

```
BOSSMAN_PIT_AI_MAX_MEDIA=1
BOSSMAN_PIT_VISION_URL=<loopback VLM>
BOSSMAN_PIT_VISION_MODEL=<installed local Qwen VLM id>
BOSSMAN_PIT_IMAGE_EDIT_MODEL=<registered local Studio image-edit id>
```

Do not hardcode an unverified checkpoint name.

---

# PHASE 5 — AI Max local-first chat

Repeat the laptop matrix with local models available.

Measure:
- local route share;
- zero-cost remote fallback count;
- p50/p95 latency;
- tool/schema errors;
- context tokens;
- web source success;
- memory retrieval usefulness.

Routine local routing must not require per-message confirmation.

---

# PHASE 6 — AI Max photo / vision / editing

Read:
- `PHOTO_PIPELINE_AI_MAX_RU.md`
- `COMPAT_1_6_MEDIA_20260925.md`

Architecture:

`Telegram photo -> verified ingest -> fast local Qwen vision -> Jeff answer -> background visual memory`

Editing:

`own verified photo -> Bossman Studio reference -> local Qwen image-edit job -> verified output -> Telegram`

## Vision test matrix

At least 10 images:
- scene/object;
- OCR/visible text;
- screenshot/UI;
- food/product;
- indoor;
- outdoor;
- ambiguous;
- JPEG;
- PNG;
- WebP.

Negative:
- malformed;
- disguised non-image;
- >10 MiB.

PASS:
- magic bytes checked;
- wrong bytes refused;
- own participant media only;
- no real-person identity inference;
- no protected/sensitive inference from pixels;
- fast response before deep visual-memory;
- background analysis cannot starve foreground;
- raw base64 not in conversational memory.

## Visual memory

Neutral short-lived `visual_context` only at first.

Initial visual memories:
- INFERRED;
- short TTL;
- promoted only later by normal sorter/utility evidence.

## Photo edits

At least 5:
- remove/replace background;
- color/style change;
- add/remove simple object;
- reframe/crop-like edit if supported;
- follow-up correction.

PASS:
- own verified source image;
- no host filesystem scan;
- Studio model is available + free + non-openrouter;
- reuse Studio reference/job/run;
- output SHA/type verified;
- cancellation/timeout;
- WebP safely normalized to PNG for current Studio reference API.

## Speed acceptance

Compare fast vision with background memory OFF vs ON.

Investigate if scheduling background memory raises median foreground latency by more than max(100 ms, 5%).

Background enrichment may wait/skip rather than delay live chat.

---

# PHASE 7 — Multiuser RC

Only after owner + synthetic isolation pass.

Add one voluntary real participant, then up to 3–4.

For each:
- zero-start;
- onboarding;
- own memory only;
- free-only route;
- no admin/computer;
- same scores;
- same roleplay/privacy;
- own media only.

Required:
- 0 cross-user leaks;
- 0 owner-private leaks;
- 0 participant computer-control exposure;
- 0 direct PersonaVault/filesystem tools;
- 0 secret persistence;
- 0 silent paid calls.

Status:
`MULTIUSER_RC_PASS`

---

# PHASE 8 — Learning evidence

Do not fine-tune shared weights before retrieval/prompt baseline is measured.

Collect:
- candidates;
- retrievals;
- corrections;
- forgets;
- usefulness/noise;
- discovery answered/skipped;
- accepted/rejected recommendations;
- context cost.

Sorter labels:
`KEEP / DROP / TTL / MERGE / SUPERSEDE`

Train/eval split by participant.

If DSPy/GEPA is used:
- shadow first;
- baseline vs candidate;
- held-out users/turns;
- promote only measured gain.

Participant facts stay retrieval memory, not shared weights.

---

# PHASE 9 — Adversarial + soak

Synthetic replay:
- >=200 mixed updates;
- >=4 synthetic IDs;
- duplicates;
- restarts;
- provider failure;
- web prompt injection;
- malformed attachments;
- roleplay bypass attempts;
- internal/model/owner probes;
- delete/recreate identity;
- concurrent chat + background visual memory.

At least 20 restart/duplicate boundaries.

Soak:
- >=60 minutes;
- bounded queues;
- no retry storm;
- no duplicate answer growth;
- no cross-user contamination;
- no secret leak;
- no background-media starvation.

---

# PHASE 10 — Compatibility regression

## 1.5 critical

Run existing tests on the actual current tree, adjusting only PYTHONPATH according to repo conventions:

```bash
python -m pytest   bossman-core/tests/test_v3_self_improvement.py   command-center/tests/test_economy_swarm.py   tests/test_bossman_15_economy_scripts.py   tests/test_owner_run_self_improve.py   tests/test_self_improve_lab.py   tests/test_self_improve_lab_observers.py   tests/test_youtube_trader_ingest.py   tests/test_youtube_trader_ingest_auto.py -q
```

## 1.6 foundation

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/test_bossnet_foundation_v16.py   command-center/tests/test_coding_limit_saver_v16.py   command-center/tests/test_game_bootstrap_v16.py -q
```

## 1.6 media/Telegram neighbours

Re-run the Telegram/Studio matrices from Phase 1.

1.7 cannot certify the divergent 1.6 verticals by itself. Full 1.6 owner vertical acceptance is rerun after convergence.

---

# PHASE 11 — Exact-SHA CI

Stop feature coding.

Record:
`CANDIDATE_17_SHA`

Push once.

Required current-SHA workflows include at least:
- Bossman 1.7 PIT foundation;
- Command Center CI;
- Bossman Core CI;
- root-ci;
- PostgreSQL run contracts;
- ASTRA acceptance;
- Solana safety gates;
- any other mandatory workflow triggered by current branch.

Queued/pending/cancelled/action_required/zero-jobs != PASS.

---

# PHASE 12 — Freeze and handoff

Create:

- `docs/v1.7/evidence/BOSSMAN_17_RC_FINAL.md`
- `docs/v1.7/evidence/BOSSMAN_17_RC_HANDOFF.json`
- `docs/v1.7/evidence/BOSSMAN_17_TEST_MATRIX.json`

Report:
- branch;
- START_15_SHA;
- START_16_SHA;
- START_17_SHA;
- TESTED_17_SHA;
- exact tests;
- CI run IDs/conclusions;
- laptop live;
- AI Max local;
- local route share;
- web;
- vision;
- photo edit;
- latency;
- memory counts;
- score tests;
- roleplay;
- topic gate;
- two-user/multiuser isolation;
- restart/duplicate;
- soak;
- open P0/P1/P2;
- current 1.5/1.6 heads compared.

## Verdicts

`BOSSMAN_17_RC_READY` only if:
- no open P0;
- no release-blocking P1;
- PIT tests green;
- real laptop Jeff pass;
- real AI Max local pass;
- live vision/edit pass when the models are installed;
- multiuser isolation pass;
- exact-SHA CI green.

If AI Max/model installation is unavailable:
- freeze only `PIT_LAPTOP_SHADOW_READY`;
- mark AI Max cases OWNER_REQUIRED/NOT_RUN;
- do not call full 1.7 RC ready.

`CONVERGENCE_HANDOFF_READY` only after a concrete TESTED_17_SHA is written to handoff JSON.

After freeze: **no new 1.7 feature commits.**

---

# Final output

Return:

```
BRANCH=
START_15_SHA=
START_16_SHA=
START_17_SHA=
TESTED_17_SHA=
STATUS=
LAPTOP=
AI_MAX_LOCAL=
WEB=
VISION=
PHOTO_EDIT=
MULTIUSER=
SOAK=
CI=
P0=
P1=
P2=
HANDOFF_FILE=
NEXT_CONVERGENCE_SOURCE_SHA=
```

No vague “almost ready”.
