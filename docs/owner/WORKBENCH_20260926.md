# Рабочий стол — Bossman 1.5 → 1.6 → 1.7 closure

## 2026-09-27 owner checkpoint — release candidate, not freeze

The active acceptance line is PR #84, `claude/bossman-freeze-closure-ohvmon`.
After `git fetch --all --prune`, local and remote HEAD matched at
`7f90a197f0833d58bdb30b073f9a12cdd652b7c5`; the worktree was clean.
The older `integrate/bossman-1.7-unified-20260925` reference below is historical.
No 1.8 work is authorized until the mandatory 1.5–1.7 gate passes.

- Exact-SHA Windows release archive was built locally from a clean tree:
  `BOSSMAN-Windows-x64-7f90a197f083.zip`, 788,049,956 bytes,
  SHA-256 `9d2f14bdc56189deef8ba927ad26a1232c3f78a317413c374bb743951d74217d`.
  Its isolated archive verifier returned `BOSSMAN_BUNDLE_ACCEPTANCE=PASS` with
  source SHA `7f90a197`, zero reported problems. The GitHub Windows `bundle`
  job and the `owner-experience` job for this same SHA both passed. The latter
  exercises the archive on a GitHub Windows runner; it is not a complete live
  smoke on the owner's AI Max PC.
- The live candidate backend on `127.0.0.1:8801` reports SHA `7f90a197`;
  an older backend on `127.0.0.1:8800` reports `e76de12b`. The Jeff/PIT
  poller is one logical process tree and points to 8801. `ONE_BACKEND=BLOCKED`:
  the old elevated process refused a normal stop attempt (`Access denied`).
  Installed Jeff `pit doctor` passed config, Telegram authentication, free
  route, web, media configuration, and participant tool perimeter. This does
  not prove media delivery. After the owner requested local uncensored
  priority with a free cloud fallback, the installed PIT config was backed up
  and set to use local `bossman-community-qwen-uncensored:latest` first, with a
  22-second local timeout and free OpenRouter models as fallbacks. PIT was
  restarted through its installed CLI, leaving one poller. A real incoming
  message at 15:37:27 UTC took the local route (22,015 ms, failed), then a
  free Nemotron fallback (4,640 ms, success) and received Telegram delivery
  receipt `message_id=241`. Further fallback replies were delivered through
  `message_id=252`. A later request failed on both local and cloud routes;
  receipt `254` may be an error reply and was not counted as model success.
  This proves some live replies, but route reliability remains a defect.
  Existing private per-user conversation/route logs remain in the
  canonical data directory; no chat contents or secrets are in Git.
  The 8801 coding-task readiness endpoint reports that
  `BOSSMAN_OPENHANDS_COMMAND` is unset. One live local-model task (#56) returned
  a textual plan and was marked `PASS` without any tool call or file change;
  this is a false task-success signal, not BOSSBLOCKS evidence.
- The archive's isolated Python runtime imported installed `bcc`, opened the
  canonical SQLite data read-only, decrypted 7/7 existing provider keys, and
  loaded the existing PIT bot token, provider key and identity salt. No secret
  values were printed. This is a compatibility check; migration and live
  continuity still need proof. The pre-upgrade backup is PARTIAL because two
  ACL-protected personality files were unreadable.
- Exact-SHA CI at this check: root-ci, Bossman Core CI, ASTRA, PostgreSQL,
  Solana, PIT, Economy, Local bundle, Shipped app contracts, Editors and Fable
  PASS. Command Center CI was pending. `Intelligence Preservation` FAILS at
  `Require current same-model evidence`: the committed measurement file is
  absent. The owner initially kept this gate mandatory. The current corpus
  has insufficient independent samples for the 95% retention lower bound;
  do not fabricate evidence. A later owner request to defer optional tests
  cannot turn a red required CI check green.
- The owner deferred `YOUTUBE-001` and `INSTAGRAM-001` for this pass and allowed
  a reduced BOSSBLOCKS-001 check: about ten minutes of real game coding via
  free AI plus a concrete result. This changes the owner mission scope, not
  the measured-intelligence gate or the requirement for honest live evidence.
- That reduced game continuation now has a local Qwen-assisted HUD change,
  isolated game commit `f285f95e1b1125ff71ad534b74b1ae28bb44e381`, fresh
  Godot render, and 6/6 tests on a clean extraction. Artifact:
  `docs/v1.6/runs/artifacts/BossBlocks-LOCAL-20260927.zip`, SHA-256
  `EECCB1893014921F920522D2B7535AC9109E1D6BA3E6E1A1426E33E2B8A788DD`.
  `REDUCED_GAME_CONTINUATION=PASS`; the full owner emulator and autonomous
  Bossman-only game benchmark remain `DEFERRED`/`BLOCKED`. Details and observed
  token/time counts are in `docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`.

Current release status: `V15=NOT_TESTED`, `V16=BLOCKED`, `V17=BLOCKED`,
`ALL_MANDATORY_CI=BLOCKED`, `OWNER_PC_ACCEPTANCE=BLOCKED`,
`BOSSMAN_1_5_1_6_1_7=NOT_FROZEN`, `READY_FOR_1_8=NO`.

An owner-PC UI observation was attempted, but the Computer Use session could
not establish the active browser URL confidently and stopped before any UI
interaction. `OWNER_UI_SMOKE=BLOCKED`; no click-through success is claimed.
The old desktop shortcut still targets an older installed archive and connects
to `:8800`. That legacy backend is an orphaned Session-0 process rather than a
managed service or running scheduled task. No supported stop endpoint was
found. The candidate backend on `:8801` latched `SOURCE_IDENTITY_UNKNOWN`
while this worktree was dirty, so it must be restarted after a clean commit.
An installed desktop built from the same final SHA is then needed for a valid
same-build UI smoke; using the prior `7f90a197` bundle against a newer SHA
would fail the identity check. No old data directory was deleted.
Phase-0 gate at this checkpoint: `ONE_CHECKOUT=BLOCKED`, `ONE_VENV=BLOCKED`,
`ONE_DATA_DIR=BLOCKED`, `ONE_BACKEND=BLOCKED`, `ONE_POLLER=PASS`,
`KEYS_IN_VAULT=PASS` (7/7 existing provider keys opened),
`BACKEND_SHA=BLOCKED` until clean restart and same-build verification.
At 15:48 UTC legacy port `:8800` was closed; only `:8801` was listening.
The old desktop process remained and is not evidence of the new installed UI.
The source backend on `:8801` still reported `SOURCE_IDENTITY_UNKNOWN` because
the audit/code worktree had pending edits. One PIT poller remained active.
`ONE_BACKEND=PASS` as a listener count at that instant, while the full Phase-0
gate remains `BLOCKED` until a same-build installed desktop and clean backend
identity are observed.

Local uncensored diagnosis: a direct cold Ollama load took 23.6 seconds;
the model then answered a short prompt with `think=false` in 0.25 seconds.
The installed PIT already sends `think=false`; its recent failures were at
the 22-second local timeout with zero output. Its selected free Nemotron
fallback succeeded for three requests but failed for the next one within
the remaining eight-second budget. A warm model was present in `/api/ps` at
the time of this checkpoint. These synthetic timing checks are not a fresh
Telegram acceptance after the pending source changes.
The source fixes now awaiting final build are: classify explicit `Make ... code
change` requests as actions so a text-only pseudo-tool plan cannot close the
task; retain the local Ollama Jeff model for 30 minutes after a call; and try
the configured, catalog-eligible free cloud fallbacks in sequence within the
existing 30-second deadline. No paid fallback or participant authority was
added. The combined focused regression for these changes passed `143/143`
on the owner PC. These are source test results, not installed Jeff/UX proof.

Owner-directed code-lock status: `REDUCED_GAME_CONTINUATION=PASS`,
`YOUTUBE_001=DEFERRED`, `INSTAGRAM_001=DEFERRED`,
`FULL_REGRESSION=DEFERRED` (the owner stopped the local Command Center run at
73%, so it has no final verdict). A clean tree and CI from the final commit
are still required even for an operational candidate. The same-product
Terminal Run contract remains in force: CLI, dashboard and Telegram must use
the same backend, tasks, models, memory and approvals. Current North Star
ladder: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` is evidenced by code and
tests; `SELF_REPAIR_SINGLE_CYCLE_PASS` and all later levels are `NOT_TESTED`.

Date: 2026-09-26. Canonical operational handoff for Codex / OpenCode / other coding agents.

## Source truth
Repository: `molotroka123-cell/AiMaxBossman`
Only line: `integrate/bossman-1.7-unified-20260925`

Start every run with `git status --short`, `git fetch --all --prune`, switch/pull this branch, then `git rev-parse HEAD`. Remote HEAD after fetch is truth.

## Mission
Do not redesign. Finish 1.5 + 1.6 + 1.7 as ONE installed Windows product:
`FIX BLOCKERS → RESTORE COMPUTER USE → TARGETED GREEN → V1.5 PROOF → V1.6 LIVE RUNS → V1.7 JEFF/MEDIA PROOF → FULL REGRESSION → EXACT-SHA CI → FRESH WINDOWS BUILD → FREEZE`.

Only then `READY_FOR_1_8=YES`.

Owner target: Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory / Windows.

## Roles
- Bossman = authority/control plane: execution, approvals, permissions, STOP, budgets, canonical state.
- Jev = router only; never approval, STOP bypass, permission/budget expansion or final verifier.
- GPT-6 Sol / primary coding agent = builder/integrator/final test manager when available.
- GLM-5.3 = auditor/orchestrator/replanner, not bulk coder.
- Aster = UX/release auditor, `ASTER_CODE_WRITES=0`.
- OpenHands = executor/owner-PC worker, never authority.
- Jeff = participant social assistant: chat/web/memory/vision/media; NO Computer Use, shell, GitHub write, owner approval or owner-private brain.

## Already converged
Unified line contains 1.5 foundation, 1.6 self-evolution/BossNet, PIT/Jeff 1.7, Telegram safeguards, bounded Jev routing, Qwen Image 2.1 Studio integration, participant media flows and local/free routing.

Important commits: `92f5d479`, `2c19cd88`, `1be504f3`, `60088d6b`, `4def95cf`, `3ac37a23`, `dd89151e`, `285615bf`, `82bfbaeb`. Do not return to old RC branches as a new base.

## Proven
- 20-minute attack: 497 targeted PASS + compileall + diff-check.
- Aster: 58 isolated Chromium/UX PASS; 11 source-backed candidates.
- Last snapshot: root-ci/Core/PostgreSQL/ASTRA/Solana PASS; Command Center CI red.
- Jeff/PIT: real local Qwen response, two-participant testing, no observed cross-user leak in tested sample, restart/idempotency/memory contracts, local-first routing, Qwen Vision live.
- Owner preflight: real public YouTube source downloaded/transcribed; three Nemotron roles produced quarantined pilot material; local Qwen responded; verified paid spend USD 0.
- Jeff media: a real 1024×1024 Studio image was generated and delivered in Telegram; see `docs/v1.7/evidence/JEFF_PHOTO_20260926.md`. This proves that one media path, not all 1.7 acceptance.
- BossBlocks: a playable Godot ZIP passed headless launch, six game checks and a separate-process save/restart check; see `docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`. The owner emulator and Bossman-only boundary are still unproven.

## Not yet proven
No final SHA/freeze, no complete one-click Windows product, installed OpenHands/local sidecar not fully proven, 1.6 owner missions incomplete, scientific cycles 0/3, positive transfer 0, Qwen Image hardware output not fully proven, Jeff soak incomplete, no fresh final installed Windows artifact. Instagram owner login and approval remain pending. (2026-09-26: Jeff cloud chat unblocked — new OpenRouter key installed from owner, `pit doctor` provider_auth=HTTP 200, ZERO_COST_OK on live catalog.)

## Current blockers
1. Command Center full regression on owner Windows — a clean full rerun is RUNNING on SHA 3bf7d658 (2026-09-26); result to be recorded below when done.
2. ~~Owner scenarios full clean rerun~~ **DONE 2026-09-26**: `tests/owner_scenarios` = 31/31 PASS on owner Windows (incl. OS-81/83/84/85 installed-product) after installing `setuptools` into the runtime Python. Note for agents: run pytest with PLAIN `python -m pytest` on this machine — `-X utf8` poisons parent subprocess decoding of cp1251 children and produces false OS-28/29/81 failures.
3. ~~Jeff cloud-only chat blocked by expired OpenRouter key~~ **DONE 2026-09-26**: owner provided a replacement key (file on desktop, name = key). Rotated into the encrypted PIT credential store (`credentials.enc`) and `%LOCALAPPDATA%\Bossman\secrets\openrouter-test.env` (both outside Git). `pit doctor`: provider_auth HTTP 200, telegram_auth PASS, free_route ZERO_COST_OK, tool perimeter PASS. Live poller restarted onto the new key.
4. ~~Computer Use native pipe unavailable / os error 2~~ **DONE 2026-09-26**: root cause was missing Windows desktop deps (DO-001). `pip install pywinauto pyautogui` restored `availability()=(True,'')`. Full live owner-desktop sequence PASS through product handlers: fresh screenshot (1.78 MB PNG, visually verified), UIA window list, Notepad launch, deterministic typing (fresh UIA readback `typed_text_visible=true`), Save-As dialog drive, disk-verified saved file (60 bytes, exact content), harmless wait, owner STOP aborted the in-flight action (`stop_file_persisted=true`, post-STOP acts refused), resume restored actions, Calculator launched by product launcher and interacted (UIA readback "Выражение — 77 × 6=" → "Отображать как 462", arithmetic correct), closed via product path. Policy guard correctly refuses any action on Bossman-titled surfaces.

## 2026-09-26 evening — closure state (SHA c2dfe919)

## Jeff participant UX — 2026-09-27 working goal

**Jeff answer integrity / owner acceptance checkpoint (2026-09-27):** the owner Telegram roast and emergency prompts returned mid-sentence at 160 and 14 output tokens respectively, so output-budget exhaustion is unproven. Native Ollama chat already sends `think=false`. The adapter previously hid Ollama `done_reason=length`; Jeff did not gate incomplete text. The candidate repair sets the participant answer limit to 2048, preserves finish reason, retries an incomplete answer once (up to 4096 and within the existing deadline), and returns an explicit incomplete status if both attempts fail. Local `stop` replies ending mid-sentence are also treated as incomplete. Route telemetry records finish and token counts without message content. The local-only uncensored test stays local-only until the owner ends it; no silent cloud fallback.

**Live response workflow:** Telegram update → single poller / durable inbox → participant identity and authority guard → consent-scoped context → measured local/free model route → answer-completeness gate → Telegram send receipt → memory/history, deterministic fact extraction and contextual discovery. Memory analysis runs after successful delivery; failed delivery leaves no new learned turn. Discovery asks at most one opt-in, task-specific question and skips generic closing questions. A separate owner-only route/delivery log records timings and receipt IDs; no audit content is appended to participant messages. `STOP`, revocation and per-user memory isolation remain gates throughout. These repairs are candidate code until the exact-SHA tests and live Telegram retest pass; Jeff acceptance and release freeze remain BLOCKED.

- Owner-only style controls: eight scales, each 1–10, initially 5: initiative, curiosity, depth, brevity, warmth, humor, directness, creativity. Tune in `pit-v1.7/config.json`; never expose scale names/values in participant replies. These controls affect wording only, never permissions or privacy.
- Answer the actual question first. Ask at most one specific, relevant question when material information is missing. Do not append generic thanks, “Чем могу помочь?” or repeated meta-questions about preferred answer style.
- Telegram presentation: use safe HTML entities for concise headings, bold, code, quotes and spoilers; render Markdown tables as readable labeled list items. Preserve reply context via `reply_parameters`; on entity rejection retry once as plain text. Evaluate richer Bot API message blocks separately before adoption; never lose a reply because of formatting.
- Participant image broadcasts require an owner-requested, visually verified local artifact and a private-chat recipient list. A public caption contains only the intended participant-facing text; generation timing, model route, backend identity and audit stay in owner-only records. Record individual delivery receipts; never automatically retry an ambiguous send.
- Uncensored community Qwen is an opt-in single-model local chat test, with the same Jeff persona, per-user memory boundary and tool perimeter. Keep free-cloud routing as the normal mode unless the owner explicitly changes it. Do not infer model availability from a specification or partial download.

**Working product tonight (all verified live):**
- Jeff chat LIVE through free OpenRouter (rotated owner key): fresh owner turn answered in **6.2 s**, ok. Historical 40–90 s latencies were the local-27B era.
- Engine P0 "unknown cloud pricing" FIXED: provider connected (458-model catalog synced), free model pinned with price 0/0 (`pricing_known=true`), agent registered; "Ответь одним словом: ГОТОВ" → completed.
- Jeff Telegram formatting SHIPPED (`d3608b97`): HTML quote/spoiler/bold/code with per-part conversion and plain-text fail-open (a markup mistake can never lose a reply). 261/261 telegram contracts green.
- Disclosure guard verified: "Какая модель?" → "Я Джефф, помощник." Owner rule recorded: answers may ask for the data we collect, formatting mandatory, Jeff never knows which AI answers.
- OpenHands coding path WORKS END-TO-END on owner hardware: SDK 1.44.1 sidecar + free model, task `88ed0c9fbfdf` completed. Repairs: credential+model forwarding from the vault (456fd730, da00a335 — the client never received the resolved key before), surrogate sanitization (fdbf1bf1), one fresh-retry on model serialization hiccups (0b850c60), per-call telemetry for the lab (c2dfe919), bash-env note + cache/garbage pruning (103d37e9, a67e5c28).
- Scientific protocol cycle 1 (lab, case `sample`, RAW+PLAN_EXECUTE_VERIFY): both variants produced REAL sidecar telemetry (22 tool calls, tool_accuracy 1.0, schema 1.0); case NOT solved in the 8-min budget by the free model → honest FAIL, weights unchanged. **SCIENTIFIC_CYCLES=1/3, POSITIVE_TRANSFER=0.**
- Full CC regression on owner Windows: 5095 passed / 2 failed → both fixed and committed (`bc3ddf9a`): UX2 exit-code contract (7 = backend-build-mismatch, 4 = foreign app; protection intact) + touch-throttle de-flaked with controlled clock.
- Owner scenarios 31/31 PASS incl. installed-product OS-81..85. AMD AI Max capacity: measured unified memory, LOCAL_ALLOWED at idle, restart-stable.
- Computer Use live owner-desktop sequence PASS (screenshot/UIA/type/readback/save/STOP-race/resume/Calculator).
- YOUTUBE-001: bounded-batch manifest discovered and committed (11 videos 2026-08-14..27); local faster-whisper ASR server built and running on 127.0.0.1:8000; ingest not yet run.
- INSTAGRAM-001: SKIPPED by owner decision 2026-09-26 (needs owner live login later).
- Instagram/BossBlocks/one-click-bundle: not closed. Validation Windows bundle blocked earlier by dirty tree; tree now clean, build not yet re-run.

**NOT frozen.** Remaining for freeze: scientific cycles 2–3 + lesson/transfer, YouTube ingest + outcome verification, BossBlocks-001, bundle validation build + one-click smoke, ONE full regression re-run, exact-SHA CI re-verify, fresh-install smoke.

## 2026-09-26 CI truth (SHA 3bf7d658 and successors — all mandatory green)
ALL mandatory CI GREEN: root-ci, Command Center CI (rest/security/stage8-14/gateway-context py3.11+3.12, Real media/Web/Fleet, Windows workspace+PID, compile+security), PostgreSQL 3.11/3.12/3.14, ASTRA (portable ubuntu+windows, runner recovery), Solana safety, autonomy-and-foundation, economy-contract, foundation, vertical-contracts.
Repairs that made it green (all pushed to the SAME unified branch):
- `ac60e62c` merged owner-PC fixes + Jeff media candidate with remote docs (no force).
- `eb25603b` **AMD AI Max capacity**: `bcc/pit/resources.py` now measures the owner's Ryzen AI Max+ 395 unified memory via `GlobalMemoryStatusEx` when no NVIDIA probe exists (winreg AMD/Radeon detection, honest `unified-*` reason labels, 8 GB default unified headroom, owner `BOSSMAN_PIT_VRAM_FREE_MIN_MB` override wins, fail-safe demote when unmeasured, never unloads anything). Live owner machine: `unified-free-99150mb` → LOCAL_ALLOWED at idle, restart-stable; heavy-workload demote and telemetry-unavailable fallback covered by 19 unit tests (`test_pit_resources.py`).
- `290f8dfb` + `3bf7d658` CI: 4 workflows (`v15-economy`, `v16-foundation`, `v16-integration`) got their first-ever run on this branch and failed at install — fixed by installing the root `bossman-shared` package and the `command-center[dev]` extra (pytest-asyncio) first, matching the pattern of the already-green workflows.

## AMD capacity (1.7 closure repair)
Owner AMD/Strix Halo environment is now measured, not guessed: idle → LOCAL_ALLOWED, heavy owner workload → LOCAL_DEMOTED (below headroom), telemetry unavailable → safe known fallback (free cloud), restart → same behavior. The advanced 1.8 Resource Brain remains out of scope.

## Aster P1 candidates
Live-reproduce before fixing: UX-001 privacy personalization/history; UX-002 pause_memory/durable history; UX-003 global STOP; UX-004 one-click startup/Jeff autostart; UX-005 build/backend identity; UX-011 Terminal approval ID flow.

## Targeted gate
Run affected PIT, Windows, autonomy, packaging, sidecar, OpenHands, approvals, STOP, privacy, Jev, Studio/Qwen and Telegram tests. Require `TARGETED_FAILS=0` before any full regression.

## V1.5
One real `FAULT → DETECT → CANDIDATE → TEST → INDEPENDENT VERIFY → RESTART → RETEST`, then unseen analogous task. Verify Society, Skill Compiler, Operating Graph, Resource Manager, coding path, verifier, CMD/UI/Telegram, STOP and brain persistence. Gate: `V15=PASS`.

## V1.6
YOUTUBE-001 is PARTIAL: real source + local transcript + 3 Nemotron roles exist. Finish deterministic outcome verification + unseen transfer, then bounded 2026-08-14..2026-08-27 batch. No live trading.

INSTAGRAM-001 is OWNER LIVE PENDING: Fresh Vibes login/readback → owner approval → profile/avatar → 1 post → 1 Story → 1 Highlight → exactly 5 approved follows → fresh verification. Password/OTP/CAPTCHA = OWNER_REQUIRED.

BOSSBLOCKS-001 is PARTIAL: real Godot game, portable ZIP and save/restart checks exist (`docs/v1.6/runs/BOSSBLOCKS-001-RESULT.md`). The coding-task stalled and a direct fallback produced part of the game; Bossman-only boundary, full Owner Emulator, Aster verdict and scientific learning are not proven. Keep the original 4h Bossman → Jev → local/free workers → bounded GLM orchestration → OpenHands → build → QA → Owner Emulator → verifier → repair gate before PASS.

Scientific self-improvement is separate: minimum 3 cycles `hypothesis → baseline → candidate → same benchmark → verifier → restart → unseen transfer → PROMOTE/REJECT`; need >=1 positive transfer.

## V1.7
Live-test Jeff chat, continuity, memory, restart, web, Qwen Vision, image generation/edit/reference, second participant, isolation, hostile prompt and PC-control denial. Need `JEFF=PASS`, `JEFF_MULTIUSER=PASS`, `JEFF_PC_CONTROL=DENIED`, `CROSS_USER_LEAKS=0`.

Media uses existing Studio only. PASS requires real readable non-zero artifact, MIME check, fresh verification and Telegram delivery.

## Routing
`deterministic/no-LLM → verified local → verified free OpenRouter → another cheap/free route → GLM-5.3 when justified → strong frontier only for hard blocker`.
Qwen = normal worker; Jev = router; verifier != author.

## Owner control
Existing owner Telegram AI-control channel is the control surface. Jeff is not. Use it for tasks, Jev, approvals, screenshots, OpenHands, Computer Use, STOP, restart and checkpoints.

## Freeze
After targeted green run ONE full regression. Fix classified failures before rerun. Pin exact candidate SHA, require mandatory exact-SHA CI green, build fresh Windows artifact and clean-install smoke.

Freeze requires V15 PASS; YouTube/Instagram/BossBlocks PASS; >=3 scientific cycles and >=1 positive transfer; V16 PASS; Jeff/multiuser/media PASS; Jev/OpenHands/Computer Use PASS; one-click Windows/STOP/restart/shared backend PASS; targeted/full regression and mandatory CI PASS; brain/secrets Git leaks 0; P0 0; release P1 0.

Then:
`FINAL_SHA=<exact>`
`BOSSMAN_1_5_1_6_1_7=FROZEN`
`READY_FOR_1_8=YES`


## 1.8 research decisions already fixed in documentation

Do NOT create separate planning files for these. The canonical 1.8 design lives in:
`docs/v1.8/BOSSMAN_18_EVOLUTION_ENGINE.md`.

After `READY_FOR_1_8=YES`, use the existing document and extend the current Bossman architecture rather than spawning new platforms.

The validated borrow-first shortlist now includes:

- Internet Radar → `unclecode/crawl4ai`
- Capability Market → `modelcontextprotocol/registry`
- Replay / Chaos Lab → `mockagents/mockagents`
- Agent self-optimization → `gepa-ai/gepa`
- External Agent Gym → `harbor-framework/harbor`
- NPU Reflex Brain → `ROCm/FastFlowLM`
- Qwen Windows/MTP acceleration → `olliehm/qwen-flash-next-windows`
- Durable workflow semantics → `dbos-inc/dbos-transact-py`
- Adaptive business experiments → `facebook/Ax`
- GenAI telemetry semantics → `open-telemetry/semantic-conventions-genai`
- Cognitive-state UI → `Jakubantalik/thinking-orbs` (MIT; UI-only, driven by real Observatory state)

Also retained in the 1.8 design:
- Distillation Foundry;
- Streaming Giant Runtime with `FareedKhan-dev/kimi-k3-in-c` as reference;
- ROCm/PyTorch side-by-side experimental lane;
- latest supported Wan local-video benchmark through existing Studio/Model Market;
- FreeToken as a strategic MoE/resource-management reference, not a production dependency until AMD/Windows owner-machine compatibility is proven.

Implementation rule:
**borrow working open-source components/patterns first; write Bossman-specific code only for authority, unified state, owner policy, hardware/resource integration, business logic or differentiated learning.**

Do NOT interpret this shortlist as permission to install ten new daemons. All of it must converge through one Bossman control plane, one task state, one brain, one Jev/router, one Studio and one evidence system.



### Thinking Orbs / live cognitive-state UI decision

Bossman 1.8 may use `Jakubantalik/thinking-orbs` as a lightweight status visualization in Command Center/Observatory.

Rules:
- presentation only; canonical task/telemetry state remains in Bossman backend;
- map real states to working/searching/solving/listening/connecting/weaving/composing/breathing/shaping;
- explicit STOP/FAIL/OWNER_REQUIRED text always overrides decorative animation;
- reduced-motion/accessibility and hidden/offscreen pause are mandatory;
- do not infer progress merely because an orb is animated;
- future phone UI may evaluate the React Native/native ports after parity tests.



### Kadr / AI video editing decision

Bossman 1.8 now treats `HelpFreedom/kadr` as the canonical borrow-first candidate for AI-native timeline editing.

Integration stance:
- external application/process + MCP/adapter;
- do NOT copy GPL-3.0 source into Bossman core by default;
- Bossman remains authority/director;
- Jev routes media intent;
- Studio/Wan/Qwen create assets;
- Kadr edits/assembles/transcribes/captions/renders;
- visual verifier checks preview/final export;
- Jeff never receives unrestricted Kadr/PTY/file authority.

Mandatory first Kadr owner-machine smoke after 1.7 freeze:
`KADR_5S_INTRO`

Create a 5-second vertical Fresh Vibes advertisement intro:
- 1080×1920 / 9:16;
- 30 fps unless generator requires a fixed rate;
- base visual generated/selected via verified Studio route, preferably latest supported local/free Wan path;
- import to Kadr;
- assemble exact ~5 s timeline;
- add simple approved Fresh Vibes branding;
- preview + fresh snapshot;
- visual verifier;
- export;
- ffprobe duration/resolution/fps/codec;
- owner-machine playback first/middle/last frame;
- record wall time, peak unified memory, model route, calls, cost and bytes;
- send preview/final artifact only to owner AI-control channel.

Gate:
`KADR_5S_INTRO=PASS` only for a real playable independently verified artifact with no black/corrupt frames or permission/privacy regression.


## Agent start rule
Read this file first, fetch current unified HEAD, finish 1.5 + 1.6 + 1.7 on owner AI Max with real Computer Use/OpenHands evidence, freeze one exact SHA, and do not start 1.8 until `READY_FOR_1_8=YES`.
