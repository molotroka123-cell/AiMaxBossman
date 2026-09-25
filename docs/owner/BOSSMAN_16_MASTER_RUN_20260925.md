# MASTER RUN — Bossman 1.6 Closure — 2026-09-25

Repository: `molotroka123-cell/AiMaxBossman`

Goal: **converge 1.0 + 1.5 + 1.6, execute three owner verticals, prove learning/self-improvement, and close Bossman 1.6 on one exact SHA**.

Do not create a new architecture plan. Execute, test, fix, verify, learn, freeze.

## Absolute rules

- no force-push;
- no history rewrite;
- no fake PASS;
- model says DONE != PASS;
- old SHA PASS != current SHA PASS;
- Aster writes no product code;
- Jev has no approval/spend authority;
- Bossman never auto-registers provider/social accounts;
- no CAPTCHA/2FA bypass;
- no real trading;
- no silent payment/budget increase;
- LOCAL_ONLY/SECRET does not leave owner machine;
- tests are not weakened to make GREEN;
- if one lane blocks on owner input, continue independent lanes.

## Phase 0 — fetch and source truth

### Local brain preservation gate

Before changing versions, branches or installed binaries:

1. resolve the active external `BOSSMAN_DATA_DIR`;
2. prove it is outside the Git checkout/worktrees;
3. record a LOCAL-ONLY brain manifest (record counts + hashes, no private payload);
4. prove `git status` contains no owner-brain/runtime-memory files;
5. keep the same data root across 1.5 -> 1.6 -> 1.7 transitions;
6. after each installed-version replacement, run recall/restart checks against
   the same local brain.

Never copy the owner runtime brain into the repo to simplify testing or
evidence. Git receives only code, schemas, synthetic fixtures and deliberately
reviewed public/test data.

Read:
- `docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md`



Run:

```bash
git status
git fetch --all --prune
git branch -a
git worktree list
```

Read:

- `docs/v1.6/FULL_AUDIT_PLAN_MAX_20260925.md`
- `docs/v1.5/ASTER_SELF_IMPROVEMENT_MASTER_20260925.md`
- `docs/v1.6/FIRST_GAME_4H_MASTER_PROMPT.md`
- `docs/v1.6/INSTAGRAM_GROWTH_OPERATOR.md`
- `docs/trading/FINAL_TRADING_LEARNING_AUDIT_20260924.md`

Resolve fresh heads for:
- `release/bossman-owner`
- `release/bossman-1.5-rc2-20260925`
- `feat/bossman-1.6-bossnet-foundation-20260925`

Do not trust the SHA values written in old docs if remote moved.

## Phase 1 — one convergence branch

Create one branch only:

`integrate/bossman-1.6-owner-20260925`

Base:
`release/bossman-1.5-rc2-20260925`

Merge:
`feat/bossman-1.6-bossnet-foundation-20260925`

Conflict policy:
- preserve current RC2 autonomy/self-repair/economy/owner-input implementation;
- preserve unique v16 foundation/Game/Business code;
- no second society;
- no second Operating Graph;
- no second Resource Manager;
- no second Skill Compiler;
- newest verified trading guardrails win;
- retain all security/STOP/approval boundaries.

After merge, prove:
- no current 1.5 file silently disappeared;
- v16 unique modules exist;
- source compiles.

## Phase 2 — deterministic baseline before expensive work

Run targeted suites first.

### 1.5 autonomy/economy

```powershell
python -m pytest -q `
  bossman-core/tests/test_v15_autonomy_core.py `
  command-center/tests/test_v15_autonomy_feature.py `
  command-center/tests/test_v15_self_repair.py `
  command-center/tests/test_v15_economy_feature.py `
  command-center/tests/test_economy_orchestrator.py `
  command-center/tests/test_owner_input_v15.py `
  tests/test_v15_economy_orchestrator.py `
  tests/test_v15_owner_run.py `
  tests/test_v15_provider_pool.py `
  tests/test_bossman_15_economy_tools.py `
  tests/test_distill_recorder.py
```

### 1.6 foundation

```powershell
python -m pytest -q `
  command-center/tests/test_bossnet_foundation_v16.py `
  command-center/tests/test_coding_limit_saver_v16.py `
  command-center/tests/test_economy_swarm.py `
  command-center/tests/test_game_bootstrap_v16.py
```

### existing self-improvement

```powershell
python -m pytest -q `
  bossman-core/tests/test_v3_self_improvement.py `
  tests/test_self_improve_lab.py `
  tests/test_self_improve_lab_observers.py `
  tests/test_owner_run_self_improve.py
```

Any red deterministic test:
`REPRODUCE -> local/free/Ling fix -> regression -> verifier`.

Codex does not manually patch first.

## Phase 3 — start Bossman self-improvement immediately

This is a primary objective, not an end-of-day extra.

Run the shipped path:

```powershell
python tools/bossman_15_self_improve.py status
python tools/bossman_15_self_improve.py start
```

Record:
- campaign ID/path;
- baseline SHA;
- selected model/route;
- verifier;
- budget;
- STOP state.

Aster must leave the system with:
`SELF_IMPROVEMENT_PROCESS_STARTED`

or a concrete `OWNER_REQUIRED` packet.

Do not replace this with Aster writing fixes.

## Phase 4 — provider/resource preflight

Use Bossman Provider Pool / Resource Manager.

Required:
- local routes;
- legitimate free routes;
- current OpenRouter routes;
- already-funded routes;
- unknown cost -> blocked;
- rate limit -> failover, not account abuse.

If a missing provider could materially improve throughput:
- send owner a Telegram OWNER_REQUIRED packet;
- exact signup URL;
- fields to fill;
- key placement instruction;
- cost/privacy note.

Bossman never registers the account itself.

RunPod/rented GPU:
- optional Plan Maximum accelerator;
- only after explicit owner budget approval;
- workload + GPU + runtime + max spend bound to approval;
- terminate on completion/failure/budget/STOP;
- prove billing stop.

## Phase 5 — workload scheduler

Run five lanes.

### Lane A — integration spine
Owner: Codex as final integrator, not bulk coder.

Responsibilities:
- resolve convergence;
- review diffs;
- freeze candidate;
- release evidence.

### Lane B — YouTube training
Workers:
- local ingest/ASR/VLM;
- 3 independent Nemotron roles;
- Ling verifier/coder;
- bounded GLM finalizer only after free failure;
- Jev route order;
- independent verifier.

### Lane C — Instagram
Workers:
- local/browser Social Operator;
- Creative/Product;
- Compliance;
- verifier;
- Telegram owner approval/input.

Mutating Instagram actions are sequential.

### Lane D — Game
Workers:
- Game Director;
- Gameplay Coder;
- Voxel Coder;
- UI/Persistence;
- QA Player;
- Build/Performance;
- independent verifier.

Use at most two simultaneous code writers on disjoint worktrees.

### Lane E — learning/self-improvement
Continuously consumes verified failure/fix traces.

It may not steal the critical GPU lease from Game owner-emulation or local VLM at critical moments.

## Phase 6 — Coding Limit Saver policy

Every code task uses:

1. deterministic/no-LLM path if possible;
2. verified LOCAL coder;
3. legitimate FREE coder;
4. another bounded cheap attempt if useful;
5. `z-ai/glm-5.3-flash` for hard blocker/finalizer;
6. Codex only for integration/review when workers cannot safely resolve it.

Aster = AUDIT_ONLY.

Claude = disabled by default.

No model receives the entire repo unless evidence proves it is needed.

Use progressive context:
- objective;
- failing test;
- relevant files;
- small evidence packet;
- schemas.

## Phase 7 — YouTube vertical YOUTUBE-001

Source window:
`2026-08-14..2026-08-27`

Use exact owner source if supplied; otherwise current K1m6a channel config.

Run:

```powershell
python tools/k1m6a_youtube_batch.py `
  --start 2026-08-14 `
  --end 2026-08-27 `
  --root "$env:LOCALAPPDATA\Bossman\CommandCenter\youtube-run-20260925"
```

Then run the Bossman economy workflow against the produced batch manifest.

Requirements:
- no future teacher-language leakage;
- no duplicate replay as independent evidence;
- teacher stays UNVERIFIED;
- CVD/OI/level series identity preserved;
- full losses/no-trigger/UNKNOWN retained;
- purged/embargoed time split;
- multiple-testing guard;
- no live trading.

Three Nemotron jobs per video:
1. Transcript/claim extractor.
2. Chart/evidence skeptic.
3. Strategy/curriculum builder.

Ling independently verifies.

GLM is eligible only after free failure and inside configured budget.

Output:
`docs/v1.6/runs/YOUTUBE-001-RESULT.md`

At least one learning candidate gets an unseen transfer test.

## Phase 8 — Instagram vertical INSTAGRAM-001

Goal:
- correct owner account;
- truthful profile;
- avatar;
- exactly 1 feed post;
- exactly 1 Story;
- exactly 1 Highlight;
- exactly 5 approved follows;
- verified external state.

Before mutation:
- identity readback;
- revision hash;
- Telegram approval.

Credentials:
- owner-local only;
- never model context.

2FA/CAPTCHA/security checkpoint:
`OWNER_REQUIRED`

No bypass.

After every write:
`effect -> fresh readback -> evidence`.

Unknown outcome:
`reconcile before retry`.

DM acceptance:

A. allowed service question;
B. off-topic Newton question -> zero business LLM/web calls;
C. prompt injection -> zero unrelated research;
D. medical suitability -> clinical handoff.

Output:
`docs/v1.6/runs/INSTAGRAM-001-RESULT.md`

Compile at least one reusable verified Instagram skill candidate.

## Phase 9 — Game vertical BOSSBLOCKS-001

Timebox: 4 hours maximum for the game benchmark.

First execute real game bootstrap through Bossman.

Primary:
Godot/Voxel Tools pinned path.

If not READY inside bootstrap/plugin budget:
use documented fallback.

Required game:
- FPS movement/look/jump/gravity/collision;
- generated block terrain;
- 3 block types;
- target indication;
- break/place;
- hotbar;
- invalid placement blocked;
- pause/exit;
- save;
- kill/restart;
- load exact modifications.

Feature freeze by 02:30 of the game clock.

Then Owner Emulator:
- launch final deliverable;
- move/look/jump;
- break/place;
- invalid placement;
- save;
- exit;
- process kill/restart;
- reload;
- boundary stress;
- rapid interaction;
- soak.

Every discovered defect:
`REPRODUCE -> FIX -> REGRESSION -> OWNER REPLAY -> VERIFIER`.

Output:
`docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`

GREEN invariants:
- no manual human edits after start;
- `ASTER_CODE_WRITES=0`;
- zero known in-scope P0/P1/P2;
- exact build hash.

## Phase 10 — Scientific self-improvement proof

Complete at least one real cycle.

Example:
"new video selector is >=2x faster without lower verified recall"
or
"compiled Godot bootstrap skill reduces model calls by >=30% with equal owner-emulator result".

Required:

1. hypothesis;
2. frozen benchmark;
3. baseline;
4. isolated candidate;
5. same benchmark;
6. regression;
7. independent verifier;
8. unseen transfer;
9. promote/reject;
10. restart continuity.

A rejected candidate is valid evidence that the scientific loop works.

Output:
`docs/v1.6/runs/SELF-IMPROVEMENT-001-RESULT.md`

## Phase 11 — persistent society / graph / skill proof

After verticals:

- record verified performance by task class;
- prove later team selection changes/uses measured history;
- persist skill refs and memory refs;
- record task -> agent -> branch -> benchmark -> skill graph chain;
- restart;
- query same history;
- point-in-time query older state.

Compile at least:
- one YouTube/Market candidate;
- one Instagram/Product candidate;
- one Game candidate.

Only independently verified unseen transfer may promote.

## Phase 12 — 1.6 foundation live proof

### Distributed Brain
Kill one disposable worker and prove resume without duplicate side effect.

### Context Budget
Record actual context utilization by role.

### Provider Fleet
Use >=3 eligible routes if configured; prove quota/circuit failover.

### Model Foundry
Contract test is mandatory; actual weight training is optional unless owner approves a training burst.

### Simulation World
Use for planning/counterfactuals, but never count simulation as live PASS.

## Phase 13 — Aster checkpoints

Aster every 30 minutes receives only compact evidence:

- critical path;
- changed files;
- test outcomes;
- known blockers;
- context/cost telemetry;
- verifier verdicts;
- self-improvement status.

Aster returns:
- P0/P1/P2;
- false-PASS risk;
- wasted work;
- cheaper/better route;
- generalized lesson proposal.

Aster never edits product code.

Final verdict:
`FINAL_ACCEPT | FINAL_REJECT`

## Phase 14 — integration regression

Before freeze run:
- root CI;
- Bossman Core;
- Command Center;
- security;
- owner scenarios;
- Windows-100;
- 1.5 Economy CI;
- 1.6 Foundation CI;
- targeted Social Farm;
- YouTube tests;
- Game acceptance;
- STOP/restart/approval/Telegram.

No open release-blocking P0/P1.

## Phase 14.5 — local brain upgrade/reinstall continuity

Before release freeze, perform an installed-product continuity drill:

`OLD VERSION + EXISTING LOCAL BRAIN -> REPLACE PROGRAM FILES -> NEW VERSION -> MIGRATE/ATTACH -> RESTART -> RECALL`

PASS requires:
- the canonical `BOSSMAN_DATA_DIR` remained outside the checkout;
- no owner brain file appears in Git status/index/history;
- pre-upgrade record counts/hashes have a verified post-upgrade correspondence;
- at least one verified lesson is recalled;
- at least one project decision/fact is recalled;
- Persistent Agent Society / skill or routing history survives where enabled;
- restart does not create a second empty canonical brain;
- rollback/failed migration leaves the pre-upgrade local brain recoverable;
- release ZIP contains zero owner-brain payload.

A reinstall that resets learned owner state is a release-blocking P1.
Any private brain content committed/pushed or packaged publicly is P0.

## Phase 15 — freeze

Freeze one exact candidate SHA.

After freeze:
- no cosmetic commit;
- no new feature;
- no README-only commit.

Build one Windows ZIP from that exact SHA.

Clean extract.

Run installed-product acceptance.

Record:
- source SHA;
- ZIP name;
- bytes;
- SHA-256;
- artifact digest;
- owner acceptance.

## Phase 16 — release decision

Tag `bossman-v1.6` only if:

- base regressions green;
- 1.5 autonomy green;
- 1.6 foundation green;
- YouTube acceptance complete;
- Instagram acceptance complete;
- Game acceptance complete;
- self-improvement process real;
- scientific cycle complete;
- learning survives restart;
- no confirmed P0/P1;
- exact Windows artifact accepted;
- Aster FINAL_ACCEPT.

No tag for PARTIAL.

## Final owner report

Write:
`docs/owner/BOSSMAN_16_OWNER_RUN_20260925.md`

Include:

```
FINAL_SHA=
WINDOWS_ZIP=
WINDOWS_SHA256=
BASE_1_0=
V15_AUTONOMY=
V16_FOUNDATION=
YOUTUBE=
INSTAGRAM=
GAME=
SELF_IMPROVEMENT_PROCESS=
SCIENTIFIC_CYCLES=
SKILLS_COMPILED=
SKILLS_PROMOTED=
TRANSFER_GAIN=
SOCIETY_UPDATED=
GRAPH_RESTART_PASS=
PROVIDER_FAILOVER=
RUNPOD_USED=
OWNER_INTERVENTIONS=
LOCAL_CALLS=
FREE_CALLS=
GLM_CALLS=
CODEX_CODE_WRITES=
ASTER_CODE_WRITES=
ACTUAL_COST_USD=
OPEN_P0=
OPEN_P1=
OPEN_P2=
ASTER_VERDICT=
TAG_CREATED=
BRAIN_DATA_ROOT_EXTERNAL=
BRAIN_UPGRADE_CONTINUITY=
BRAIN_GIT_LEAKS=
BRAIN_RELEASE_ARTIFACT_LEAKS=
FINAL_STATUS=
```

## Primary mission

Do not optimize for the number of agents, commits or model calls.

The mission is:

**Owner gives a goal. Bossman executes, fixes itself, verifies the result, learns a reusable improvement and reports only what the owner needs.**

Tomorrow should move Bossman away from dependence on external coding agents and toward its own measured self-improvement loop.
