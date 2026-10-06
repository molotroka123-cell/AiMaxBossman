# Bossman 1.6 — Full Audit + Plan Maximum — 2026-09-25

Status: **AUDITED / FOUNDATION HARDENED / TOMORROW OWNER RUN REQUIRED**

This document is the source-of-truth plan for the 2026-09-25 maximum run.

## 0. Audited refs

At audit time:

- Bossman 1.0 owner line: `release/bossman-owner @ 90a807b03918e88e7214cebc2581a5e74544dc81`
- Bossman 1.5 RC2: `release/bossman-1.5-rc2-20260925 @ 21a8092b75763aefb741b4b150344a0673e01344`
- Bossman 1.6 foundation after audit hardening:
  `feat/bossman-1.6-bossnet-foundation-20260925 @ c4e3ece3b20ac8fe99aa5911b9cf0e0bc4c7a762`

Critical graph fact:

`1.5 RC2 -> 1.6 = DIVERGED: 1.6 ahead 80, behind 114 commits`.

Therefore 1.6 is **not** currently a clean child of the latest 1.5 RC2.

Do not tag or declare 1.6 ready from the current 1.6 branch.

---

# 1. Executive verdict

Bossman 1.6 is currently:

- **strong architecture + partial implementation**;
- **not release-ready**;
- **not compatible-by-proof with current 1.5 RC2 yet**;
- **three real owner verticals are still required**:
  1. YouTube learning,
  2. Instagram business operator,
  3. Game Dev / BossBlocks-001;
- full GREEN also requires self-improvement/learning evidence across those runs.

The biggest blocker is not one code bug. It is **convergence + execution proof**.

## P0

No confirmed new P0 in the audited 1.6 foundation code.

Any new security/authority violation discovered tomorrow immediately blocks release.

## P1 / release blockers

1. **Branch divergence**: current 1.6 is missing 114 commits from RC2 1.5.
2. **Distributed Brain is contract-only**: no live node registry/lease/failover/STOP fanout acceptance.
3. **Model Foundry is promotion logic, not a live train/evaluate pipeline**.
4. **Temporal Knowledge Fabric is a minimal record layer, not yet integrated with the 1.5 Operating Graph/runtime**.
5. **Simulation World is an aggregation contract, not a full digital-twin runner**.
6. **Provider Fleet is routing/decision code, not three verified live provider adapters + rented-GPU lifecycle acceptance**.
7. **Game bootstrap is PLAN_ONLY**. Actual download/hash/unpack/export/game generation is not implemented by this feature.
8. **Instagram 1.6 is a contract over Social Farm foundation**; live account/profile/publish/highlight/follow/DM E2E is still owner-live.
9. **YouTube code exists**, but the full owner window has not yet completed one unified 1.6 run with economy swarm + verifier + learning transfer on the converged SHA.
10. There is not yet one **final integrated exact SHA / Windows artifact / owner acceptance / 1.6 tag**.

## P2 / engineering debt

- 1.6 top-level README was stale before this audit.
- several foundation modules were not separately tested.
- 1.6 had no dedicated foundation CI.
- some architecture modules duplicate concepts already implemented better in 1.5; integration should reuse instead of building second stores/routers.

---

# 2. Hardening completed during this audit

Commit:
`c4e3ece3b20ac8fe99aa5911b9cf0e0bc4c7a762`

Added dedicated:
`.github/workflows/v16-foundation-ci.yml`

Added:
`command-center/tests/test_bossnet_foundation_v16.py`

Hardened:

### Distributed Brain node contract

A remote/node route now requires an explicit owner-bound node and valid non-negative resource telemetry.

### Context budget

Negative token counts and invalid relevance/novelty inputs are refused.

### Knowledge Fabric

Facts require identity/provenance, valid confidence and valid temporal intervals.

### Memory Retrieval

Signals are normalized and candidates cannot use negative token budgets.

### Model Foundry

Non-finite/invalid benchmark values cannot promote a model.

### Provider Fleet

A route now refuses:

- zero/exhausted quota;
- secondary account without explicit provider-terms eligibility;
- rented GPU without explicit `rental_authorized`;
- negative/invalid provider metrics.

This closes the audit finding where RENTED_GPU could previously be selected by the routing contract without an owner-authorization field.

### Simulation World

Negative/NaN scenario weights and non-finite metrics are refused.

---

# 3. 1.6 pillar audit

## 3.1 Distributed Brain

Implemented today:
- Node;
- capability/resource eligibility;
- cost/load selection.

Missing for full 1.6:
- durable node registry;
- signed/attested owner binding;
- lease issuance/expiry;
- heartbeat persistence;
- STOP fanout;
- task checkpoint/resume on another worker;
- duplicate-result/idempotency proof.

Closure test:

`worker A dies -> lease expires -> worker B resumes exact logical task -> no duplicate side effect`.

## 3.2 Specialist Model Foundry

Implemented:
- immutable train/holdout manifest identity;
- benchmark/safety non-regression promotion check.

Missing:
- live dataset builder;
- actual train/distill/LoRA job;
- holdout isolation proof;
- checkpoint/evaluation runner;
- rollback activation.

For tomorrow, Model Foundry may pass as **FOUNDATION_GREEN** if contracts/tests pass.

Do not require an expensive weight-training job to close the three owner verticals unless the owner explicitly authorizes a training burst.

Weights changing is optional for 1.6; verified skills/workflows/memory improvement is mandatory.

## 3.3 Temporal Knowledge Fabric

Current v16 code is minimal.

Latest 1.5 RC2 already has a stronger Personal Operating Graph.

Tomorrow:
- do NOT create a second canonical graph;
- wire/extend the existing 1.5 Operating Graph;
- use v16 bitemporal ideas as contract/tests;
- prove point-in-time history and provenance.

## 3.4 Simulation World

Current code correctly labels results SIMULATED.

Tomorrow use it for:
- Game scenario envelopes;
- provider scheduling alternatives;
- trading research counterfactuals.

Simulation never counts as live acceptance.

## 3.5 Provider Fleet / Economic Scheduler

Current v16 code:
- provider/account classes;
- secondary-account rules;
- rental estimate;
- route selection;
- cleanup decision helper.

Tomorrow full acceptance target:
- >= 3 real configured provider/local routes;
- quota-aware failover;
- unknown-price fail closed;
- local fallback;
- cost ledger;
- owner-required provider setup packet via Telegram;
- optional capped RunPod create -> workload -> verify -> terminate if owner approves spend.

Bossman must never auto-register an account, accept ToS, solve CAPTCHA or rotate accounts to evade limits.

If more capacity is needed:
`OWNER_REQUIRED -> Telegram exact fields -> owner completes -> same task resumes`.

---

# 4. Five autonomy pillars inherited from 1.5

These are implemented in RC2 1.5 and MUST be preserved during convergence:

1. Scientific Self-Improvement
2. Persistent Agent Society
3. Skill Compiler
4. Personal Operating Graph
5. Autonomous Resource Manager

Files include:
- `bossman_v3/self_improvement/scientist.py`
- `bossman_v3/society.py`
- `bossman_v3/skill_factory/compiler.py`
- `bossman_v3/operating_graph.py`
- `bossman_v3/resource_manager.py`
- `bossman_v3/autonomy_kernel.py`
- `bcc/features/v15_self_repair.py`
- `bcc/features/v15_autonomy.py`

Current 1.6 branch does NOT contain all current RC2 implementations.

Tomorrow convergence must preserve RC2 as the stronger implementation where overlapping concepts exist.

---

# 5. OSS audit / reuse policy

Do not rewrite mature components blindly.

Relevant current open-source references:

- OpenHands Software Agent SDK — coding agents, local/ephemeral workspaces, multi-agent refactors.
- Letta / Letta Code — stateful agents, persistent identity/memory/skills.
- Graphiti — temporal context graphs with provenance and historical facts.
- LangGraph — durable long-running stateful workflows and human-in-the-loop patterns.
- Zylann Voxel Tools — Godot voxel terrain/game foundation.

Important:
- reuse patterns or pinned components only after license/security review;
- Bossman remains the authority/control plane;
- external framework memory does not become canonical Bossman memory;
- do not introduce a second orchestration product merely because it is popular.

For memory specifically, shared Operating Graph remains necessary: persistent per-agent memory alone is not enough for society-wide learning.

---

# 6. Tomorrow branch convergence — mandatory first step

Create exactly one branch:

`integrate/bossman-1.6-owner-20260925`

Base it from:
`release/bossman-1.5-rc2-20260925`

Then merge:
`feat/bossman-1.6-bossnet-foundation-20260925`

Do not cherry-pick 80 commits manually unless merge becomes impossible.

Conflict policy:

1. preserve RC2 1.5 autonomy/self-repair/economy/owner-input implementation;
2. preserve unique v16 BossNet/Game/Business modules;
3. for overlapping v1.5 trading/economy files, use newest verified behavior;
4. no duplicate canonical stores;
5. no duplicate resource manager;
6. no duplicate society/skill compiler;
7. no force-push;
8. one integration branch only.

After merge:

`git diff release/bossman-1.5-rc2-20260925...HEAD`

must show intentional 1.6 delta, not accidental deletion of 1.5.

---

# 7. Tomorrow workload distribution

## Lane A — Integration / release spine

Owner: Codex integrator through Bossman where possible.

Tasks:
- converge branches;
- resolve compile/import failures;
- run targeted regression;
- create exact integration SHA;
- no feature work after freeze.

Coding:
- Ling/free/local first;
- bounded GLM for hard merge/blocker;
- Codex only reviews/merges difficult residuals.

## Lane B — YouTube / Market learning

Run in parallel after baseline GREEN.

Workers:
- local yt-dlp/ASR/vision;
- 3 independent Nemotron roles;
- Ling verifier/coder;
- optional bounded GLM finalizer;
- Jev routing;
- independent verifier.

Do not send local/private evidence to free cloud routes.

Target source:
K1m6a, 2026-08-14..2026-08-27.

## Lane C — Instagram business pilot

Sequential external writes only.

Workers:
- Local browser/session operator;
- Social/Creative/Product role;
- Compliance/verifier;
- Telegram owner-input/approval;
- Aster audit at checkpoints only.

Never run concurrent mutating Instagram workers on the same account.

## Lane D — Game Dev / BossBlocks-001

Largest active compute allocation.

Workers:
- Game Director;
- Godot/Gameplay Coder;
- Voxel/World Coder;
- UI/Persistence;
- QA Player;
- Build/Performance;
- Independent Verifier.

Use max 2 concurrent code writers on disjoint files/worktrees.

One canonical project writer at merge time.

## Lane E — Self-improvement

Runs continuously but does not steal critical-path resources.

Pipeline:
`failure -> self-repair inbox -> isolated candidate -> tests -> verifier -> unseen transfer -> lesson/skill -> restart -> retrieval`.

At least one real self-repair/scientific experiment must complete during the owner run.

## Aster

Audit/control only:
- 30-minute checkpoint;
- immediate checkpoint for P0/P1/false-PASS;
- final acceptance.

`ASTER_CODE_WRITES = 0`.

## Jev

Mission director:
- task DAG;
- route selection;
- retry/escalation;
- context budgets;
- provider choice;
- no approval/spend authority.

## Claude

Default disabled for implementation.

Use only if owner explicitly keeps it as an emergency teacher path.

The goal is to leave 1.6 able to operate without Claude.

---

# 8. Hardware/concurrency policy

Target machine has shared CPU/GPU/unified memory.

Rules:

- one heavy local VLM/large local LLM GPU lease at a time by default;
- Godot interactive QA/build gets a resource lease;
- CPU/network discovery/download/transcript tasks may run in parallel;
- 3 Nemotron public-cloud roles may run concurrently if quota permits;
- if OpenRouter rate limits, use 2+1 stagger instead of endless retry;
- Ling coding worker gets one worktree per task;
- verifier never edits the candidate it verifies;
- no two writers edit the same file simultaneously.

Priority order:
1. release/convergence blocker;
2. Game critical path;
3. Instagram external-state verification;
4. YouTube background learning;
5. optional deep experiments.

---

# 9. Test matrix — BASE / 1.0 / 1.5 / 1.6

## 9.1 Convergence baseline

Mandatory:
- compile all changed Python;
- root/core/Command Center target suites;
- secret scan;
- path/security regressions;
- STOP/restart;
- approvals;
- exact source identity.

## 9.2 Bossman 1.5 regression

Run at minimum:

- `bossman-core/tests/test_v15_autonomy_core.py`
- `command-center/tests/test_v15_autonomy_feature.py`
- `command-center/tests/test_v15_self_repair.py`
- `command-center/tests/test_v15_economy_feature.py`
- `command-center/tests/test_economy_orchestrator.py`
- `command-center/tests/test_owner_input_v15.py`
- `tests/test_v15_economy_orchestrator.py`
- `tests/test_v15_owner_run.py`
- `tests/test_v15_provider_pool.py`
- `tests/test_bossman_15_economy_tools.py`
- `tests/test_distill_recorder.py`
- Windows bundle contract.

## 9.3 Bossman 1.6 foundation

Mandatory:
- `test_bossnet_foundation_v16.py`
- `test_coding_limit_saver_v16.py`
- `test_economy_swarm.py`
- `test_game_bootstrap_v16.py`
- dedicated `Bossman 1.6 Foundation CI`.

## 9.4 YouTube tests

Contract:
- exact date discovery;
- duplicate filtering;
- no direct OpenRouter bypass;
- teacher remains UNVERIFIED;
- future transcript leakage blocked;
- Twitch/YouTube evidence dedupe;
- purged/embargoed split;
- multiple-testing guard;
- outcome labels do not leak into T0/T1;
- promotion requires independent verifier.

Live:
- discover window;
- ingest every accessible target video;
- captions or local ASR;
- local VLM evidence extraction;
- 3 Nemotron reports/video;
- Ling verification;
- optional GLM only after free failure;
- zero live trading;
- produce quarantine/promoted counts;
- at least one unseen transfer test.

## 9.5 Instagram Day-1 tests

Before writes:
- correct account identity;
- secrets absent from model/log/Git;
- login/2FA/CAPTCHA owner handoff;
- official provider profile verified OR governed browser route selected;
- Telegram approval bound to exact revision.

Required Day-1:
- truthful profile;
- avatar;
- exactly 1 feed post;
- exactly 1 Story;
- exactly 1 Highlight;
- exactly 5 approved follows;
- external-state readback after each mutation.

Reliability:
- duplicate approval does not duplicate side effect;
- restart does not replay stale write;
- STOP cancels pending write;
- unknown outcome reconciles before retry.

DM:
A. in-scope service question;
B. off-topic Newton question -> 0 business LLM/web calls;
C. prompt injection -> 0 unrelated research;
D. medical-risk -> human clinical handoff.

## 9.6 Game Dev / BossBlocks tests

Bootstrap:
- actual download;
- SHA-256;
- safe extraction;
- version smoke;
- export smoke;
- fallback if primary >15 minutes.

Game A-F:
- boot/build;
- movement;
- collision;
- terrain;
- break/place;
- 3 block types;
- hotbar;
- invalid placement;
- save/restart/load;
- pause/exit;
- rapid interaction;
- boundaries;
- repeated save/relaunch.

Learning G:
- reproduced bug;
- verified fix;
- lesson persists restart;
- relevant agent retrieves it;
- at least one skill/benchmark candidate.

Final:
- exact packaged build;
- owner emulator;
- bounded soak;
- zero known in-scope P0/P1/P2;
- `ASTER_CODE_WRITES=0`.

---

# 10. Cross-domain learning requirement

1.6 is not GREEN if the three verticals run but Bossman learns nothing.

Minimum tomorrow:

### YouTube
one verified workflow/lesson candidate from evidence extraction or market research.

### Instagram
one verified reusable skill such as:
`instagram_day1_profile_publish_verify`
or
`fresh_vibes_dm_scope_gate`.

### Game
one verified reusable skill such as:
`godot_voxel_bootstrap`,
`save_restart_verify`,
or
`owner_game_emulator`.

For each:
- source trace;
- verifier;
- unseen transfer;
- restart;
- retrieval by relevant role.

At least one Scientific Self-Improvement experiment:

`hypothesis -> baseline -> candidate -> same benchmark -> verifier -> transfer -> promote/reject`.

Rejecting a bad candidate is a valid PASS of the scientific loop.

---

# 11. Provider / account / owner-input policy

No Bossman auto-signup.

When a provider/account/token is missing:

1. generate `OWNER_REQUIRED`;
2. send Telegram packet:
   - provider;
   - exact URL;
   - fields owner must fill;
   - required permission/scope;
   - why it is needed;
   - expected cost/free quota;
   - how to paste the key securely;
3. continue other independent tasks;
4. same task resumes after owner input.

Never:
- accept ToS for owner;
- solve CAPTCHA;
- enter payment card;
- create fake identities;
- create multiple accounts to evade quotas.

---

# 12. Cost / model policy

Normal order:

1. deterministic code;
2. verified local;
3. legitimate free provider;
4. already-funded provider;
5. bounded GLM finalizer;
6. owner-approved rented GPU only if it materially wins.

Aster is never a coder.

Codex is not bulk implementation.

Track:
- requests/model;
- retries;
- actual cost;
- context tokens;
- cache hits;
- verified success;
- owner interventions.

Optimization target:

`verified useful result / dollar / owner intervention / elapsed time`.

---

# 13. Plan Maximum schedule

## Phase 0 — 00:00–00:30

- fetch/prune;
- create integration branch from RC2;
- merge 1.6;
- resolve conflicts;
- compile;
- targeted base regression.

No vertical starts before baseline imports/boot are sane.

## Phase 1 — 00:30–01:00

Parallel:
- YouTube discovery/ingest begins;
- Game bootstrap begins;
- Instagram session/account preflight begins;
- self-improvement status starts.

## Phase 2 — 01:00–03:00

Parallel:
- Game critical implementation;
- YouTube free swarm;
- Instagram drafts/creative/profile with owner handoff as needed;
- self-repair fixes any code/harness failure.

Aster checkpoint every 30 min.

## Phase 3 — 03:00–04:30

- Game owner emulator/fixes;
- Instagram approved writes + readback;
- YouTube verifier/learning compilation;
- cross-domain skills/graph/society statistics.

## Phase 4 — 04:30+

- full integration regressions;
- restart/resume/STOP;
- Windows artifact;
- exact owner acceptance;
- Aster final audit;
- freeze exact SHA.

If external provider delays block one lane, do not idle the run. Continue all independent lanes.

---

# 14. 1.6 release definition

Bossman 1.6 may be tagged only when ALL are true:

1. one converged exact SHA;
2. 1.0 safety/release regressions green;
3. 1.5 autonomy/self-repair/economy regression green;
4. v16 foundation CI green;
5. YouTube vertical reaches its declared owner acceptance;
6. Instagram Day-1 reaches declared owner acceptance;
7. BossBlocks reaches GREEN or every required benchmark item is explicitly passed;
8. self-improvement process actually runs;
9. at least one scientific cycle completed;
10. cross-domain learning artifacts survive restart;
11. no confirmed P0/P1;
12. exact Windows artifact built/tested;
13. Aster FINAL_ACCEPT;
14. final branch/tree identities recorded;
15. tag points to exact tested SHA.

Target tag:
`bossman-v1.6`

If one external OWNER_REQUIRED dependency is unavailable, final status is PARTIAL, not fake GREEN.

---

# 15. Final artifacts tomorrow

Write:

- `docs/owner/BOSSMAN_16_OWNER_RUN_20260925.md`
- `docs/v1.6/runs/YOUTUBE-001-RESULT.md`
- `docs/v1.6/runs/INSTAGRAM-001-RESULT.md`
- `docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`
- `docs/v1.6/runs/SELF-IMPROVEMENT-001-RESULT.md`
- final exact-SHA certificate;
- Windows ZIP + SHA-256;
- lessons/skills/graph evidence.

Final report must state:

`FINAL_SHA`
`WINDOWS_SHA256`
`BASE_REGRESSION`
`V15_AUTONOMY`
`V16_FOUNDATION`
`YOUTUBE`
`INSTAGRAM`
`GAME`
`SELF_IMPROVEMENT`
`TRANSFER_GAIN`
`OWNER_INTERVENTIONS`
`ACTUAL_COST_USD`
`OPEN_P0`
`OPEN_P1`
`OPEN_P2`
`ASTER_VERDICT`
`FINAL_STATUS`

---

# 16. End state after 1.6

The important end state is not a bigger GitHub repository.

After 1.6, normal operation should be:

`owner goal -> Bossman plans -> society chooses roles -> resource manager chooses route -> workers execute -> verifier checks -> failure self-repairs -> skill/memory/graph learns -> owner receives result/OWNER_REQUIRED in Telegram`.

Codex/Claude/Aster should no longer be required for routine work.

Aster may remain an optional high-level external auditor for major releases.

Bossman itself owns the daily execution and verified self-improvement loop.
