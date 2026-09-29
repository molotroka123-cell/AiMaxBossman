# NEXT CHAT MASTER — converge Bossman 1.5 + 1.6 + tested 1.7

Repository: `molotroka123-cell/AiMaxBossman`

## Purpose

Use this only **after** Bossman 1.7 has an exact tested RC handoff.

Do not re-implement 1.7 from memory. Consume exact evidence:

- `docs/v1.7/evidence/BOSSMAN_17_RC_HANDOFF.json`
- `docs/v1.7/evidence/BOSSMAN_17_RC_FINAL.md`
- `docs/v1.7/evidence/BOSSMAN_17_TEST_MATRIX.json`

If these files do not identify a concrete TESTED_17_SHA, stop and return to the 1.7 master run.

## Mission

Converge:
- latest accepted 1.5 autonomy/self-repair/free-first base;
- unique tested 1.6 BossNet/Game/Business/Studio capabilities;
- exact tested 1.7 Jeff/PIT capabilities;

into **one integration candidate**, then run all affected regressions and owner/live acceptance without weakening any gate.

Do not create another architecture fork.

## Absolute rules

- git fetch --all --prune first;
- no force-push;
- no history rewrite;
- preserve useful new commits from other agents;
- do not merge by branch name alone; merge by verified capability and evidence;
- do not claim a test from one SHA for another;
- no fake PASS;
- no weakening tests;
- preserve STOP, budgets, authority, LOCAL_ONLY, exact-SHA evidence and verifier independence;
- runtime owner brain/PIT data never enters Git;
- trading remains READ-ONLY/PAPER unless separately authorized;
- participant Jeff never receives owner/admin/computer/shell/payment/trading tools;
- participant direct PersonaVault/filesystem access remains structurally absent;
- paid model fallback stays disabled for 1.7 participant path;
- current 1.6 Studio remains the only media execution plane;
- do not create a second Telegram stack or second model registry.

## Inputs to resolve at runtime

Read the handoff JSON and record:

```
SOURCE_15_SHA=
SOURCE_16_SHA=
TESTED_17_SHA=
```

Then fetch current heads:

```bash
git status --short
git fetch --all --prune
git branch -a
git worktree list

git rev-parse origin/release/bossman-1.5-rc2-20260925
git rev-parse origin/feat/bossman-1.6-bossnet-foundation-20260925
git rev-parse origin/feat/bossman-1.7-personal-identity-training-20260925
```

If source branches advanced after the recorded handoff:
- inspect delta;
- preserve fixes;
- rerun affected tests;
- do not silently substitute a newer untested 1.7 SHA for TESTED_17_SHA.

## Integration branch

Use one convergence branch only.

Recommended name:

`integrate/bossman-1.5-1.6-1.7-20260925`

Do not create multiple final/convergence branches.

Base choice:
- start from the latest clean 1.5 RC2 / existing owner integration lineage;
- integrate verified 1.6 unique delta;
- integrate exact tested 1.7 delta;
- if an existing approved 1.6 integration branch already contains current 1.5 correctly, prefer it rather than rebuilding history.

No force-push.

## Merge priority by meaning

When conflicts occur, resolve by this order:

### Authority/security
Newest stricter verified behavior wins:
- STOP;
- approvals;
- no duplicate external effect;
- exact identity;
- privacy;
- secret handling;
- budgets;
- LOCAL_ONLY;
- verified bytes;
- independent verifier.

### Core/backend
Keep one Command Center/backend/task engine/provider registry.

### 1.5
Preserve:
- continuous self-repair;
- persistent agent society;
- skill/workflow learning;
- free-first economy;
- owner input loop;
- unified CMD/UX;
- operating graph/resource routing;
- runtime brain outside Git.

### 1.6
Preserve unique tested:
- BossNet foundation;
- distributed brain/model foundry;
- temporal knowledge;
- simulation/game/business verticals;
- current Studio API;
- Telegram media primitives;
- current Studio verified-byte/output contracts;
- current Bossman-only owner surface.

### 1.7
Preserve:
- Jeff participant-only mode;
- zero-start per Telegram ID;
- PersonaVault under dedicated PIT local folder;
- HMAC person_key isolation;
- public_guard;
- no model/provider disclosure;
- no owner/global memory in participant context;
- free-only participant routing;
- Risk + Engagement + Profile Stability local ledgers;
- bounded own-person retrieval;
- high-recall collection/outcome labels;
- roleplay/parody opt-in;
- politics/religion topic gate;
- participant-only memory controls;
- Qwen fast photo vision after AI Max;
- async visual-memory enrichment;
- photo edit through existing Studio only;
- direct participant media/filesystem/persona tools remain absent.

## Important 1.6 ↔ 1.7 media rule

Read:
`docs/v1.7/COMPAT_1_6_MEDIA_20260925.md`

1.7 must reuse current 1.6 Studio:

`reference -> job(media=reference) -> status -> verified run -> verified file`

Do not copy Qwen image execution into PIT.

Only add the minimal local Qwen image-edit provider/catalog integration needed for Studio to expose the configured free local model.

Keep:
- Studio independent byte verification;
- 10 MiB Telegram ingest limit;
- JPEG/PNG/WebP Telegram validation;
- PIT WebP→PNG normalization for Studio reference if Studio still lacks WebP import.

If current 1.6 changed these APIs, adapt the PIT broker and rerun both PIT and 1.6 media tests.

## Required compile/test order

### Gate A — compile and PIT

```bash
python -m compileall -q command-center/bcc/pit

PYTHONPATH=command-center python -m pytest   command-center/tests/test_pit_foundation.py   command-center/tests/test_pit_photo_foundation.py -q
```

### Gate B — Telegram shared primitives

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/telegram_contracts/test_companion.py   command-center/tests/telegram_contracts/test_companion_buttons.py   command-center/tests/telegram_contracts/test_companion_learning.py   command-center/tests/telegram_contracts/test_companion_local_routes.py   command-center/tests/telegram_contracts/test_companion_web_search.py   command-center/tests/telegram_contracts/test_companion_vision.py   command-center/tests/telegram_contracts/test_companion_image_gen.py   command-center/tests/telegram_contracts/test_companion_media_models.py   command-center/tests/telegram_contracts/test_companion_vision_review.py -q
```

### Gate C — Studio/media

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/test_studio_runtime.py   command-center/tests/test_studio_integrations.py   command-center/tests/test_studio_provider_states.py   command-center/tests/test_studio_zero_price_is_not_free.py   command-center/tests/test_studio_sdcpp_provider.py   command-center/tests/test_studio_sdcpp_output_contract.py   command-center/tests/test_studio_media_lifecycle.py   command-center/tests/test_media_roundtrip_studio.py   command-center/tests/test_v6_media_child_priority.py -q
```

### Gate D — 1.5 critical

```bash
python -m pytest   bossman-core/tests/test_v3_self_improvement.py   command-center/tests/test_economy_swarm.py   tests/test_bossman_15_economy_scripts.py   tests/test_owner_run_self_improve.py   tests/test_self_improve_lab.py   tests/test_self_improve_lab_observers.py   tests/test_youtube_trader_ingest.py   tests/test_youtube_trader_ingest_auto.py -q
```

Use existing repo PYTHONPATH conventions where needed. Do not skip due to import-path inconvenience.

### Gate E — 1.6 foundation

```bash
PYTHONPATH=command-center python -m pytest   command-center/tests/test_bossnet_foundation_v16.py   command-center/tests/test_coding_limit_saver_v16.py   command-center/tests/test_game_bootstrap_v16.py -q
```

### Gate F — terminal/CMD

Run existing terminal unit/e2e tests plus new `bossman pit` tests.

Prove:
- one Bossman CLI;
- one backend;
- `bossman pit start/status/doctor/stop`;
- existing 1.5 CMD/terminal behavior not regressed.

## Integration-specific tests to add

Add regressions for:

1. 1.5 owner Telegram and 1.7 participant Telegram using separate bot/config namespaces against the same backend.
2. Jeff cannot call owner console even when same human owns machine.
3. PIT memory root is separate from owner operating graph/global memory.
4. Restart keeps both 1.5 brain and 1.7 participant memory without mixing.
5. STOP behavior:
   - owner STOP affects governed backend work;
   - Jeff cannot issue STOP/admin from participant chat.
6. Jev routing:
   - owner path may have broader authorized resources;
   - Jeff participant path remains free-only/local-first.
7. Studio:
   - owner 1.6 media paths still work;
   - Jeff edits own photo through the same Studio;
   - no cross-user source media.
8. Qwen local resource scheduling:
   - foreground Jeff chat > explicit vision > edit > background visual-memory.
9. No model/provider name appears in participant UI even though owner diagnostics may display it locally.
10. No PIT risk/behavior scores enter shared owner memory or LLM context.

## Full 1.5 owner acceptance

Read current 1.5 owner run and rerun its mandatory live acceptance on the integrated SHA.

At minimum:
- unified CMD/UX;
- self-improvement/self-repair;
- planted defect;
- independent verifier;
- unseen transfer;
- YouTube/free-first worker routing if required by current contract;
- Twitch/market read-only flow;
- owner-input Telegram→browser fill;
- restart/STOP;
- no external auditor runtime dependency.

Do not transfer a 1.5 owner-live PASS from the old branch to the integrated SHA.

## Full 1.6 owner acceptance

Read current:
`docs/owner/BOSSMAN_16_MASTER_RUN_20260925.md`

Rerun the declared current verticals on the integrated SHA.

At minimum current contract includes:
- YouTube learning;
- Instagram business operator;
- Game Dev/BossBlocks;
- learning/self-improvement evidence;
- Windows/install acceptance if required by the current 1.6 master.

Do not call 1.6 closed based only on unit tests.

## Full 1.7 acceptance

Reuse the exact test matrix from:
`docs/v1.7/BOSSMAN_17_FULL_RC_MASTER_RUN_20260925.md`

On the integrated SHA re-run:
- Jeff owner shadow;
- two-ID isolation;
- multiuser isolation;
- memory;
- scores;
- roleplay;
- politics/religion gate;
- web;
- AI Max local-first;
- Qwen vision;
- background visual memory;
- photo edit;
- restart/idempotency;
- soak.

The old TESTED_17_SHA proves the feature lane, not the integrated SHA.

## Security/red-team

Attack the integrated SHA for:
- participant→owner command escalation;
- cross-user persona leak;
- owner memory entering Jeff context;
- prompt injection requesting filesystem/persona path;
- web injection;
- attachment/path traversal;
- duplicate Telegram updates;
- restart races;
- secret leakage;
- paid fallback;
- Qwen/Studio source-image substitution;
- media tamper/hash mismatch;
- roleplay bypass;
- model/provider disclosure;
- hidden location claims;
- PIT internal disclosure.

Any confirmed cross-user or owner-private leak = P0.

## Resource/performance check

On AI Max measure:
- local chat latency;
- vision latency;
- background memory impact;
- Studio edit peak resource use;
- simultaneous chat + background analysis;
- provider fallback behavior.

Background learning/vision must not starve foreground chat.

## Candidate freeze

After all code fixes stop:

`FINAL_INTEGRATION_SHA=<exact sha>`

No feature commits during certification.

Run all mandatory repository workflows for that exact SHA.

Queued/pending/cancelled/action_required/zero-jobs != PASS.

## Required evidence

Create:

- `docs/owner/BOSSMAN_1_5_1_6_1_7_CONVERGENCE_FINAL.md`
- `docs/owner/BOSSMAN_1_5_1_6_1_7_TEST_MATRIX.json`
- `docs/owner/BOSSMAN_1_5_1_6_1_7_HANDOFF.json`

Include:
- source SHAs;
- exact integrated SHA;
- commit ranges applied;
- conflict decisions;
- tests and counts;
- owner-live acceptance 1.5;
- owner-live acceptance 1.6;
- owner-live acceptance 1.7;
- AI Max local/vision/edit results;
- CI run IDs;
- installed Windows result if required;
- open P0/P1/P2;
- local brain continuity result;
- no-Git private-data result.

## Final verdict

Only say `READY_FOR_COMBINED_OWNER_TEST` if:
- no P0;
- no release-blocking P1;
- 1.5 mandatory acceptance passes on integrated SHA;
- 1.6 mandatory acceptance passes on integrated SHA;
- 1.7 mandatory acceptance passes on integrated SHA;
- exact-SHA mandatory CI passes;
- runtime brain continuity passes;
- no private data enters Git.

Otherwise return `NOT_READY` with exact blockers.

## Final output format

```
INTEGRATION_BRANCH=
SOURCE_15_SHA=
SOURCE_16_SHA=
SOURCE_17_SHA=
FINAL_INTEGRATION_SHA=
STATUS=
V15=
V16=
V17=
AI_MAX=
VISION=
PHOTO_EDIT=
WINDOWS=
CI=
P0=
P1=
P2=
HANDOFF_FILE=
```

No vague conclusions.
