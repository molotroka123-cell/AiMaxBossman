# Рабочий стол — Bossman 1.5 → 1.6 → 1.7 closure

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

## 2026-09-26 CI truth (SHA 3bf7d65806497417c00f04283cec6a0b3ddf270f)
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
