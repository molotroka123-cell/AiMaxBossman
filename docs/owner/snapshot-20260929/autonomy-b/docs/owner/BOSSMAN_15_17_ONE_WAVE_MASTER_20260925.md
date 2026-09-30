# BOSSMAN 1.5 → 1.6 → 1.7 — ONE-WAVE OWNER MASTER

Repository: `molotroka123-cell/AiMaxBossman`. Machine: Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory. Date of this handoff: 2026-09-25.

## Execute, do not redesign

Codex is the owner integrator and independent auditor. Bossman is the normal executor and records tasks, worker routes, tests, verifier decisions, costs, lessons and checkpoints. Jev is a controller/router, never an approval, security, budget or final verification authority. Use deterministic work, then proven local workers, then verified free OpenRouter routes through Bossman. Use a bounded paid GLM route only for a documented hard blocker within an approved cap; escalate Codex coding only for unresolved P0/P1 or integration. Minimize context packets: goal, failing test, relevant files/diff, contract and expected result. Do not send secrets or LOCAL_ONLY content to models. Do not paste, log, commit or repeat the OpenRouter key supplied in chat. Check configured credentials via the Bossman provider gateway and report only configured/auth/free-route counts.

The required order is **accepted exact 1.5 SHA → accepted exact 1.6 SHA → tested exact 1.7 SHA → one integrated candidate → clean installed-product acceptance → release**. Do not call source-branch unit tests live acceptance. Do not merge 1.7 merely because its branch has a green test matrix. Do not create a second backend, Telegram stack, memory authority, provider registry, task engine or runtime brain. Never force-push, rewrite history, weaken tests, bypass STOP, make a silent paid call, bypass CAPTCHA/2FA/provider limits, enable live trading, or publish without owner approval.

Start immediately with `git status`, `git fetch --all --prune`, `git branch -a`, `git worktree list`, and fresh `git rev-parse` for the five branches below. Inspect every worktree and avoid writing to a worktree currently owned by the parallel 1.7 run. Treat remote heads as live source truth; the following values are observations, not acceptance:

```text
1.5 economy: fa594b7ccd2cd1077c0ef94281f8c8b001bf41b2
1.6 self-evolution: e574b497487f3f887374992fa013072b39f84acb
1.6 BossNet: b841de29f42ed08dc3dc203be914e011f01ed209
1.7 PIT: 1f8bb1fa7faf60287264afe97b2344507c6e92ff
existing local integrate/bossman-1.7-unified-20260925: e1bdc420b246843edafd37e7ad26079889c06107
```

The existing unified candidate has the observed 1.5 and BossNet commits as ancestors, but neither `e574b497` self-evolution nor the current 1.7 remote head as ancestors. Audit unique functionality and current branch drift before deciding whether to merge or port changes. Its merge commit titles are **not** acceptance evidence. The 1.7 test matrix currently reports `status=IN_PROGRESS` and `tested_17_sha=null`; require a real exact-SHA handoff and rerun affected tests on the integration SHA. Some older docs recommend 1.5 RC2 as base: that instruction is superseded. Use only the accepted `V15_FINAL_SHA` as the 1.6 baseline.

## Read these existing contracts before product changes

Read `AGENTS.md`, `BOSSMAN_1_5_START_HERE.md`, `docs/v1.5/README.md`, `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`, `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`, `CLAUDE_NEXT_ACTION.md`, and this document. For files absent in the current tree, inspect the named source branch with `git show`; do not silently skip them.

### 1.5

- `docs/v1.5/ASTER_1_5_TO_1_6_ONE_RUN_MASTER_20260925.md`
- `docs/v1.5/ASTER_SELF_IMPROVEMENT_MASTER_20260925.md`
- `docs/v1.5/BOSSMAN_1_5_CODE_FREEZE_20260925.md`
- `docs/v1.5/BOSSMAN_1_5_FINAL_CLOSURE_20260925.md`
- `docs/owner/BOSSMAN_1_5_1_6_DAY_MISSION_20260925.md`
- `docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md`
- `docs/v1.5/MODEL_ROUTING_STACK_20260925.md`
- `config/v1.5/model-routing-stack.json`
- `config/v1.5/provider-pool.json`
- `config/v1.5/self-improvement.json`
- `docs/v1.5/ECONOMY_ORCHESTRATOR.md`
- `docs/v1.5/ACCEPTANCE_AND_LEARNING.md`
- `docs/v1.5/AUTONOMY_SELF_REPAIR.md`
- `docs/v1.5/PRODUCT_ARCHITECTURE.md`
- `docs/v1.5/TELEGRAM_LOGIN_PRIVACY.md`

### 1.6 and the three owner runs

- `BOSSMAN_1_6_START_HERE.md`
- `docs/owner/BOSSMAN_16_MASTER_RUN_20260925.md`
- `docs/v1.6/FULL_AUDIT_PLAN_MAX_20260925.md`
- `docs/v1.6/FOUNDATION_FREEZE.md`
- `docs/v1.6/BOSSNET_16_ARCHITECTURE.md`
- `docs/v1.6/CONTEXT_BUDGET_POLICY.md`
- `docs/v1.6/MATHEMATICAL_MEMORY_RETRIEVAL.md`
- `docs/v1.6/DISTRIBUTED_BRAIN.md`
- `docs/v1.6/MODEL_FOUNDRY.md`
- `docs/v1.6/TEMPORAL_KNOWLEDGE_FABRIC.md`
- `docs/v1.6/SIMULATION_WORLD.md`
- `docs/v1.6/PROVIDER_FLEET_RUNPOD_PLAN.md`
- `docs/v1.6/LOCAL_BRAIN_PERSISTENCE.md`
- `docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md`
- `docs/trading/FINAL_TRADING_LEARNING_AUDIT_20260924.md`
- `docs/trading/K1M6A_BULK_LEARNING_SPRINT.md`
- `docs/trading/K1M6A_ADAPTIVE_LEARNING.md`
- `docs/trading/K1M6A_SELECTOR_V3_SPEED_ACCURACY.md`
- `tools/k1m6a_youtube_batch.py`
- `docs/v1.6/BUSINESS_GROWTH_ENGINE.md`
- `docs/v1.6/INSTAGRAM_GROWTH_OPERATOR.md`
- `docs/owner/FRESH_VIBES_BEAUTY_INSTAGRAM_DAY1_20260926.md`
- `docs/v1.6/FIRST_GAME_4H_MASTER_PROMPT.md`
- `docs/v1.6/FIRST_GAME_4H_BENCHMARK.md`
- `docs/v1.6/OWNER_SIMULATION_GREEN_CONTRACT.md`
- `docs/v1.6/JEV_ASTER_GAME_RUN_CONTRACT.md`
- `docs/v1.6/JEV_BOOTSTRAP_TELEGRAM.md`
- `docs/v1.6/CODING_LIMIT_SAVER.md`
- `docs/v1.6/GAME_STUDIO_IDEAS.md`
- `docs/v1.6/runs/OWNER_PREFLIGHT_20260925.md`
- `docs/v1.6/SENSITIVE_INPUT.md`
- `docs/owner/JEV_TOMORROW.md`
- `docs/JEV_DECISION_ENGINE.md`
- `docs/JEV_ULTRAFAST_BROWSER_CRITICAL.md`

### 1.7

- `docs/v1.7/README.md`
- `docs/v1.7/BOSSMAN_17_FULL_RC_MASTER_RUN_20260925.md`
- `docs/v1.7/NEXT_CHAT_CONVERGENCE_1_5_1_6_1_7_RU.md` (its RC2 base instruction is stale)
- `docs/v1.7/BASELINE_1_5_20260925.md`
- `docs/v1.7/COMPAT_1_6_MEDIA_20260925.md`
- `docs/v1.7/PERSONAL_IDENTITY_TRAINING_SPEC_RU.md`
- `docs/v1.7/PHOTO_PIPELINE_AI_MAX_RU.md`
- `docs/v1.7/DATASET_AND_GARBAGE_SORTER_RU.md`
- `docs/v1.7/JEFF_PUBLIC_BEHAVIOR_RU.md`
- `docs/v1.7/LAPTOP_REMOTE_RUN_RU.md`
- `docs/v1.7/OPEN_SOURCE_AND_DATASETS_20260925.md`
- `docs/v1.7/evidence/LAPTOP_SHADOW_REPORT.md`
- `docs/v1.7/evidence/BOSSMAN_17_TEST_MATRIX.json`
- `docs/v1.7/evidence/BOSSMAN_17_RC_HANDOFF.template.json`
- Require the completed `BOSSMAN_17_RC_HANDOFF.json` and `BOSSMAN_17_RC_FINAL.md`, if those are the branch's handoff contract.

## Stage A — accept 1.5 on the owner machine

Use current 1.5 economy source, no feature expansion. Previous evidence at `fa594b7`: root tests 2691 passed/10 skipped, targeted autonomy/economy 31 passed, owner scenarios 31/31. These do not freeze 1.5. Current blockers: coding sidecar `BOSSMAN_CODING_PATH_NOT_READY`, local verifier `EMPTY_RESULT`, self-improvement process not started, native UX/installed-product acceptance outstanding. Reproduce each blocker through normal Bossman CMD/UX; let Bossman classify, route cheapest capable worker, isolate candidate, test and get independent verification. Verify model IDs, quantization, first-token/throughput, memory, JSON/tool calls, STOP, budget and provider auth on actual hardware. Run `python tools/bossman_15_self_improve.py status` and, only when safe prerequisites are real, `start`. Prove one bounded repair cycle and learning after full restart on an unseen related task. Run 1.5 security, root, CMD, UX, Telegram, STOP/resume and owner-input checks. Resolve `BOSSMAN_DATA_DIR` outside all checkouts and release artifacts, record private hash/count manifest without payload. Only when release gates are real green record and freeze `V15_FINAL_SHA`; any later code change invalidates its acceptance.

## Stage B — converge and accept 1.6

Start from **exact accepted `V15_FINAL_SHA`**. Reuse the existing single unified candidate if its ancestry and semantics can be corrected safely; coordinate with the parallel owner of that worktree. Otherwise prepare one documented integration candidate, without force-push or duplicate final branches. Integrate self-evolution unique validated delta first, then BossNet unique validated delta. Preserve stricter accepted security/authority/STOP/memory behavior. Verify sensitive input and Jev fallback; run 1.5 regressions, 1.6 foundation, security, Command Center/Core, installed product and browser/Telegram suites. Keep a traceable merge/conflict ledger.

Complete **three distinct real Bossman owner runs**, not merely preflights: (1) K1m6a YouTube historical learning with transcript/adaptive vision, three independent Nemotron extractor/skeptic/curriculum roles when the free route is live, Ling/local independent verifier, quarantine, no lookahead, losses/UNKNOWN/no-trigger retention, unseen transfer and no live trading; (2) Fresh Vibes Instagram Day 1 with verified identity, immutable preview and owner approval before each external effect, readback and reconciliation, exactly the approved profile/avatar/post/Story/Highlight/follows, DM off-topic zero LLM/web calls and medical handoff; (3) BossBlocks four-hour Bossman-controlled build with pinned verified Godot/Voxel tooling, feature freeze at 02:30, playable package, Owner Emulator, save/kill/restart/reload and exact build hash. Instagram login, 2FA, CAPTCHA, public posts, follows and paid spend stop at `OWNER_REQUIRED` until actual owner action/approval; continue independent lanes. Mock contracts cannot certify live effects. RunPod rental requires separate explicit GPU/count/max cost/runtime/workload approval.

Separately complete **at least three scientific self-improvement cycles**, each hypothesis → frozen baseline → isolated candidate → same benchmark → regression → independent verifier → full restart → unseen transfer → promote/reject. At least one must show measured positive transfer. Update verified lessons, Skill Compiler, Society routing history and Operating Graph; query all after restart. Prove local brain continuity through 1.5→1.6 installed-file replacement with the same external data dir; scan Git and ZIP for private brain leakage. Freeze `V16_FINAL_SHA` only after three live owner runs, three cycles, UX/CMD/Telegram shared backend, STOP/approval/restart, no P0/release P1, independent final acceptance. Build the Windows ZIP from exactly that SHA and retest a clean extraction.

## Stage C — converge and accept 1.7

Let the parallel 1.7 run finish its exact SHA, laptop shadow, live-turn/soak, PIT isolation, photo/vision and completed handoff. Do not turn `IN_PROGRESS` into PASS. Inspect all post-handoff remote changes. Merge only tested unique 1.7 delta into the accepted 1.6 lineage, or audit/fix the existing `integrate/bossman-1.7-unified-20260925` candidate without overwriting parallel work. PIT participant has a separate external data root and no owner/admin/computer/shell/payment/trading/secret permissions; owner/global memory never enters its context. Preserve the one 1.6 media execution plane. Test 1.5/1.6/1.7 together at the new exact integrated SHA, including owner privacy, guest boundaries, prompt injection, STOP/races, provider failover, Telegram, CMD/UX, vision/photo, Windows package and brain continuity. Require independent verifier and owner live acceptance where specified. Freeze `V17_FINAL_SHA` only after all gates pass, then merge into the existing owner release branch with normal history, rebuild exact-SHA Windows artifact and clean-install retest. Never infer integrated PASS from component SHAs.

## Checkpoints and completion

At each closed stage or material blocker, commit and push an evidence checkpoint to the legitimate working branch (never private brain or secrets); send a compact Telegram checkpoint through the configured Bossman channel if working, with stage, exact SHA, PASS/FAIL evidence, repair/learning, local/free/paid calls, actual USD, owner action and next step. Telegram owner messages reach this Codex task only through a verified bridge; do not claim automatic intake. Keep a local immutable operations ledger outside Git for private counts and costs. Do not spam logs. Record skips explicitly. Final owner report must give exact 1.5/1.6/1.7 source and final SHAs, ancestry, test counts, three vertical outcomes, three-cycle outcomes/transfer, PIT/laptop outcome, verifier identity, brain continuity/leaks, CMD/UX/Telegram, Windows ZIP hash, worker and model call counts, actual paid cost, code-write attribution, P0/P1/P2 and `PASS|PARTIAL|BLOCKED`. If any required live dependency is missing, report the exact blocker and continue useful independent work; never fabricate GREEN.

**Execute the first source-truth and safe provider checks now. Do not answer with another plan.**
