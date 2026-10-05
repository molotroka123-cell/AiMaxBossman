# Tonight's backlog for Bossman 1.9 (branch `claude/bossman-1.9-owner-bugtest-20260930`, HEAD `b6018ee3`, PR #89)

I read the files and git history, and checked GitHub Actions through the MCP read-only API. I did not run any tests.

**What changed since the last handoff:** several things `docs/owner/CONTINUATION_PROMPT_20260929.md` calls "open" are already done or merged on this branch:
- The RC19 cross-layer receipt failure is already fixed by commit `954e1441`. It was a Windows clock-tick problem, not missing wiring.
- `rc19/t-jeff-admin`, `feat/jeff-2.0`, autonomy lines A/B/C, `claude/rc19-audit-integration` and the three PR #84 commits are all ancestors of HEAD.
- CI on `0139fe27` finished: all 11 required workflows succeeded.

## 1. Canonical and release rules

**Canonical branch**
- `release/bossman-owner` is canonical: `AGENTS.md` (1.5 amendment section), `CURRENT_STATE.md:3`, `CLAUDE_NEXT_ACTION.md:112,125`. It is already an ancestor of this HEAD (`origin/release/bossman-owner` @ `90a807b0`).
- Conflict: `tools/owner_facing_branches.json` still says `"canonical": "integrate/bossman-1.7-unified-20260925"`. `MASTER_PROMPT_BOSSMAN_19_ONE_PRODUCT.md:95-97` says not to use that integrate branch as a base, because it is entirely inside PR #84.

**Never do without an explicit owner command** (`CONTINUATION_PROMPT_20260929.md:13-15`, `MASTER_PROMPT...:87-88`, `CONTINUATION.md:20-27`, `CLAUDE_NEXT_ACTION.md:116`):
- merge or push to `main`/`release/*`, create tags or releases;
- `--force`, `reset --hard`, merge into the default branch;
- payments, publications, messages to people other than the Пульт, deleting data;
- disabling Smart App Control, or touching the `BossmanKeepAwake` task.

**Owner data**
- Never touch `%LOCALAPPDATA%\Bossman\CommandCenter` (real `bcc.db`) without a verified backup. Data Jeff collected is never deleted.
- `tools/rc19_side_by_side.ps1` refuses that data root with exit 2.

**The two Telegram bots** (`MASTER_PROMPT...:74-81`)
- «Пульт» is the companion bot, `bcc/telegram_companion/**`. All owner reports go only here.
- Jeff is the participant bot, `bcc/pit/**`. No reports, no owner authority.
- One poller per token, enforced by `bcc/pit/bot_guard.token_poller_lock`.

**CD scheme** (`CONTINUATION_PROMPT_20260929.md:59-63`):
```
change -> branch -> gate: Claude + Codex (both review/tests green) -> staging
(side-by-side install tools/rc19_side_by_side.ps1, exact-SHA CI) -> OWNER presses Release
```
No release, tag or push to main/release without that press.

**Exact-SHA certification mechanics** (`docs/owner/RC19_RELEASE_PROCEDURE.md` §1, `RC19_FINAL_FREEZE_CI_PLAN.md`)
- The 11 required workflows are listed in `tools/exact_sha_certify.py:48` `DEFAULT_REQUIRED`.
- Four of them (Windows bundle, Windows owner run, Windows 100, Owner scenarios) run only on **push** to `claude/**`, `night/**`, `release/**` or the integrate branch. Several also need a path-filter hit.
- So the last commit must edit `tools/release_candidate.json` (`candidate_label`, `declared_for`). Today it declares `rc-2026-09-29-bossman-1.9-final-freeze-5` for `claude/bossman-1.9-final-freeze-cert`, so this branch needs a new declaration commit to be certifiable.
- `workflow_dispatch` returns 403 for agent tokens.
- Intelligence Preservation is **not** in `DEFAULT_REQUIRED`. It stays red and needs the owner (see J1).

**Measured CI state**
- `0139fe27` (`claude/bossman-1.9-final-freeze-cert`): all 11 required workflows `success` (runs 36643591xxx, finished 2026-09-30 01:11Z).
  - Still to check: the `ASTRA6-FREEZE.json` `status` in artifact `astra6-freeze-<SHA>`. The job badge can be green while the status is BLOCKED, because the publish gate is `continue-on-error`.
  - The previous candidate `d3385ec2` had Command Center CI = failure.
- HEAD `b6018ee3`: all runs queued/pending (PR #89 plus push).

**Status vocabulary used in handoffs**
- `PASS / FAIL / REAL_PASS / MOCK_ONLY / PARTIAL / NOT_VERIFIED / NOT_TESTED / NOT_RUN / DEFERRED / BLOCKED / OWNER_REQUIRED / INSUFFICIENT_EVIDENCE / NOT_PROVEN / NO_GO / CERTIFIED / NOT_CERTIFIED / NOT_FINAL / READY_FOR_OWNER_TEST / NOT_READY`.
- Standard marker block (`RC19_SESSION_AUDIT_20260928.md:27-37`, `MASTER_PROMPT...:257-262`):
  - `CORE_FREEZE, WINDOWS_BUNDLE, COMPUTER_USE, JEFF_UX, YOUTUBE_K1M6A, SWAPME, FRESH_VIBES, LEARNING, GENJUTSU_SOURCE_CONFIRMED, VIDEO_E2E, SHORTCUTS, BOSSMAN_1_9=READY_FOR_OWNER_TEST|NOT_READY`.
  - Jeff sub-markers: `JEFF, JEFF_MULTIUSER, JEFF_PC_CONTROL=DENIED, CROSS_USER_LEAKS=0, JEFF_VOICE`.
- `AGENTS.md` requires every major handoff to state the North Star ladder level:
  - Honest current level is `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`: `bcc/autonomy` exists, but no real cycle has run, so `SELF_REPAIR_SINGLE_CYCLE_PASS` is not reached.
  - The handoff must also restate the "Terminal Run = same product" contract.
- Handoff section pattern (`CONTINUE_FREEZE_FINAL.md`, `RC19_SESSION_AUDIT`, `CONTINUATION_PROMPT`):
  - where we stopped (branch→SHA);
  - what Cloud/tests proved vs cannot prove;
  - a `KEY=VALUE` status block;
  - branches pushed and unmerged;
  - WIP snapshots;
  - ordered next steps;
  - exact owner commands.

## 2. Unfinished backlog, deduplicated

### A. RC19 audit open items

**A1 — `test_v3_cross_layer_e2e`, `ActionReceipt.verified()=False`: already fixed on this branch.**
- Sources: `AUDIT_RC19_REPORT_20260928.md:60`, `CONTINUATION_PROMPT_20260929.md:46-47`.
- Code:
  - `bossman-core/bossman_v3/execution/compound.py:79-110` `CompoundRunner._action_receipt` builds `ActionReceipt.from_v3(started_at=r.started_at, finished_at=r.completed_at, observed_at=obs.observed_at, ...)`.
  - `bossman_shared/action_receipt.py:104-118`: `fresh()` needs `observed_at >= finished_at and observed_at > started_at`, and `verified()` needs `VERIFIED` + `post_state` + fresh.
  - `from_v3` downgrades `post_state` to `tool_result_only` unless `observation_ref ∈ {"bcc.v2.verification","fs"}` (`action_receipt.py:35,64-73`, commit `7b61d00d`).
- Root cause (commit `954e1441`, Codex, 2026-09-28 16:57 PDT): the test executor sets `started_at == completed_at == now`. On the Windows clock tick (0.5–15.6 ms) the observation got the same timestamp, so `fresh()` correctly returned "observed before/at execution start".
- Fix: `bossman_v3/contracts.py:14-29` `utcnow_after(instant, limit_s=0.25)`, applied in `bossman_v3/adapters/command_center.py` and the fake observer in `tests/test_v3_fleet_e2e.py`. Regression test: `bossman-core/tests/test_v3_receipt_clock_tick.py`.
- Remaining:
  - Re-run on Windows on the new SHA (`python -m pytest bossman-core/tests/test_v3_cross_layer_e2e.py bossman-core/tests/test_v3_receipt_clock_tick.py`).
  - Cosmetic: `compound.py:89` has a whitespace-mangled ternary that also lists `"fake"` as a post-state source. It is harmless because `from_v3` re-gates it.
  - Update the continuation doc: this item is not open.

**A2 — Full bossman-core suite not re-run after the fixes.**
- Source: `AUDIT_RC19_REPORT §2`: 22 failed / 3292 passed before the fixes, then a targeted 183/21/1.
- Needed: one full core run on the final SHA, with PYTHONPATH that includes the worktree and `bossman-core` but not `command-center`.

**A3 — Fable fix `8003d75d` and PR #84 commits `cc548917 / 445c32a0 / 84f5e0ac`: merged.**
- All four are ancestors of HEAD. Closed.

**A4 — Flake `test_cross_process_concurrent_reserve_never_exceeds_cap`.**
- Source: `AUDIT_RC19_REPORT:44-47`. Documented, not weakened. Still open.

**A5 — Owner decisions from the audit (evidence is on the owner PC, outside git)** (`CONTINUATION_PROMPT:50-53`):
- (a) Model defaults in the owner's real `bcc.db`: the fix was applied only to a COPY.
- (b) Extending the Host guard.
- (c) `learning/trace.py` bootstrap #7.
- (d) The Jeff logon task still points to old build `0d5d1d4d`. This contradicts `RC19_SESSION_AUDIT:21` (BossmanOne tasks → `84f5e0ac`); verify with `tools/owner_one_bossman.ps1 -Action Status`.
- (e) `core_url=:8800` in the owner's Jeff config. Fix with `owner_one_bossman.ps1 -Action Configure`.

**A6 — Environment trap: an editable install of an OLD tree hijacks imports.**
- Guard exists: `tests/test_import_visibility.py`.
- Always set `PYTHONPATH=<wt>\command-center;<wt>\bossman-core;<wt>`, using a short `--basetemp`.

### B. Jeff panel (Bossman Command v0.1 / Jeff Admin)

**B1 — Panel is merged** (`d6fcb067`).
- UI: `command-center/ui/pages/jeff_settings.js`, route `#/jeff-settings`.
- API: `/api/jeff-settings/...`:
  - `GET status`, `GET participants/{key}`;
  - `PUT participants/{key}/profile`;
  - `POST .../clear {scope}`, `DELETE .../facts/{id}`;
  - `POST .../pause-memory|revoke|restore`.
- Files: `pit-v1.7/jeff-settings.json` (env `BOSSMAN_JEFF_SETTINGS`), `pit-v1.7/owner-profiles/<key>.json`, heartbeat at `pit-v1.7/heartbeat.json` and `pit-v1.7/web/heartbeat.json`.
- UX status: PARTIAL, never verified on an installed build.

**B2 — Three open Jeff-window bugs** (`CONTINUATION_PROMPT:39-43`; findings file `evidence/rc19/jeff-final2-1039/jeffweb_findings.json` is not in the repo). I found no fix commits.
- **HIGH: Jeff does not recover after the model comes back** ("Модель-провайдер недоступен").
  - Text source: `bcc/pit/runtime.py:137` `PROVIDER_DOWN_RU`, returned at `runtime.py:2073,2081`.
  - Suspects: sticky cooldown in `bcc/pit/cloud_budget.py` (`cooldown_until`, per-model cooldowns, `blocked()` ~`:196-221`); the j2 breaker `bcc/pit/j2/pipeline.py:26 _Breaker`; `model_guard` "unload-only recovery".
- **MEDIUM: the previous reply is not sent to the model.**
  - `runtime.py:1926-1930`: history is added only when `memory_context_allowed` AND (the route is local OR `remote_personalization_enabled`). A local route gets only `history[-2:]`.
  - The privacy gate means a remote free route with personalization off gets no history. Decide whether this is intended; reproduce with `tools/ux_soak/jeff_web_blackbox.py`, which can see the context Jeff sends.
- **MEDIUM: history shrinks after F5 (25 → 6).**
  - Root cause is visible in code: `GET /api/jeff/history` (`bcc/pit/web.py:553-559`) returns `rt.store.history(key)`, which is the model-context window, not a display transcript.
  - `runtime.py:418-419` sets `HISTORY_PAIRS=16`, `HISTORY_CHAR_BUDGET=16000`. `remember()` (`:531-537`) deletes older pairs; `history()` (`:539-552`) truncates by characters.
  - Fix: separate display log (`store.log` already exists at `runtime.py:2114`), or an explicit "история сокращена" marker.

**B3 — Decisions backlog #20–22** (`DECISIONS_BACKLOG.md`):
- Spend caps in the panel have no effect ($0 free-only).
- Пульт `/jeff_preset` command is missing.
- Telegram display names need a consent decision.

**B4 — Jeff 2.0 known gaps**
- `docs/pit/JEFF_2_0_MEDIA.md:44`: the chat route never sets `ctx.extra["attachment"]`; no occurrence in `runtime.py`. OPEN.
- `JEFF_2_0_MEMORY_PALACE.md:51` "route_remote not set" is STALE: it is wired at `runtime.py:1947`.
- `JEFF_2_0_QUALITY_LAB.md:79`: the Пульт does not pull `owner_report` (`bcc/pit/j2/quality_lab.py:786`).
- Readiness table in `JEFF_2_0_VISION.md:190-201`: items 4–8 NOT_VERIFIED (free route on the current build, multi-step continuation after restart, self-repair 3 cycles, soak 24/48 h, CosyVoice 3).

**B5 — `cv/j` (4 Jeff self-disclosure and red-team fixes, `49e9f943`) is NOT in HEAD, and patches are not equivalent.**
- Decide whether to merge it. Merging before JEFF-0042 makes the autonomy task's target metric pre-solved by a teacher patch, which `AGENTS.md` says must not count as learning.

### C. Self-improvement / autonomy (JEFF-0042)

**C1 — Code is merged; the real cycle has never run.**
- Package: `command-center/bcc/autonomy/*`, 22 files, about 5.9k LOC.
- CLI: `bossman autonomy status|goals|journal verify|constitution status|pin` (`bcc/autonomy/cli.py`; routed from `bcc/terminal_cli/cli.py:316-318`).
- API (`bcc/features/autonomy.py:55-96`):
  - `GET /api/autonomy/status`, `GET /goals`, `GET /goals/{id}`;
  - `POST /goals/{id}/apply|confirm|reject|revise`;
  - `GET /journal`, `GET /journal/verify`.
- UI: `ui/pages/autonomy.js`.
- Data dir: `<data>/autonomy`, or env `BOSSMAN_AUTONOMY_ROOT`.

**C2 — Prerequisites before the owner-authorized real run** (`cycle.py:28,629-654,689-714`; `identity_task.py:1-18`):
1. The owner pins the constitution interactively: `bossman autonomy constitution pin`. Pin file `%LOCALAPPDATA%\Bossman\autonomy\constitution.sha256`; an unpinned constitution means BLOCKED.
2. Claude Max + Codex ChatGPT CLI logins (`bossman rave connectors` shows them).
3. Env vars: `BOSSMAN_AUTONOMY_JEFF_MODEL`, `BOSSMAN_AUTONOMY_JEFF_ENDPOINT` (default `http://127.0.0.1:11434/v1`), and optionally `OPENROUTER_API_KEY` for the Nemotron writer.
4. Baseline first: `python -m bcc.autonomy.identity_task --runtime guard+ollama --model <jeff model>`. If leaks are already 0 (the Jeff 2.0 safety filter `30948baf` plus `public_guard`), the cycle has nothing to improve. Choose a goal with a non-zero baseline, or report it honestly.
5. `python -m bcc.autonomy.cycle --goal JEFF-0042 --real`. Level is capped at 2, it stops at `USER_APPROVAL`, and exits 0 only on `USER_APPROVAL/COMPLETE`.

**C3 — Not merged:**
- `feat/bossman-autonomy-funding` (`54de1545`, funding docs, NOT_SUBMITTED).
- WIP snapshots `wip/autonomy-b-20260929` and `wip/autonomy-c-20260929`.

**C4 — Learning 24/7 (`rc19/n-self`, 3 commits `5be46396 / 1afda0f7 / 73049233`) is NOT in HEAD.**
- Contains `tools/owner_journeys/owner_notify.py`, `goal_reporter.py`, `lesson_pipeline.py`, `start_learning_247.ps1`. `start_learning_247.ps1` does not exist in HEAD.

**C5 — IP / learning metrics**
- Intelligence Preservation: INSUFFICIENT_EVIDENCE / effectively NO_GO. The gate needs at least 189 paired items per core metric.
- Local learning NOT_PROVEN (+2/52 vs a +3 threshold); `WEIGHTS_UNCHANGED`.
- The "agent over-uses tools on self-contained questions" fix `rc19/m-ip` `1ae271ea` is patch-equivalent in HEAD, but the IP re-measurement was never done.

### D. CMD
- **D1** — `rc19/o-models` WIP `7828fcaa` is not in HEAD and was not confirmed green. Parts landed elsewhere: `providers.py:277` sets `reasoning_effort:"none"`; `studio/providers/sdcpp.py:588-598` passes `llm_vision` only for reference images; `tests/test_rc19_model_recovery.py`. Still unreconciled: hiding thinking on the CMD path (`terminal_cli/human.py`), healing, live-catalog price 0/0 in `features/router.py` and `openrouter.py`.
- **D2** — Known pitfall (`MASTER_PROMPT:49-50`): `--agent` goes after `exec`, as in `bossman -p "..." exec --agent <id>`.
- **D3** — Token streaming exists on the answer path (a19/streaming merged). Provider first-token latency is not implemented (`KNOWN_LIMITATIONS.md` 1.9 section).

### E. Desktop UX
- **E1** — Second window after the console is closed (`RC19_OWNER_SMOKE.md` §8б). Addressed by `b8f43b4e` and `aa3fd329` in `bcc/desktop.py` (`_orphan_window`, `~:687`). Owner-click verification still needed.
- **E2** — Not implemented (`DECISIONS_BACKLOG`):
  - #3 memory-aware routing UI (`router.rules.memory_headroom_mb`, `router.memory_pressure` events): no UI references.
  - #18 Apps page polls a port that Bossman itself answers: no conflict state.
  - #19 ASTRA UI sweep leaves a live `bcc.market.collector` running.
- **E3** — Startup animation not implemented (`KNOWN_LIMITATIONS.md:27`).
- **E4** — Performance (`docs/audits/PERF_20260929.md`): a cold first probe still costs full time; `create_app` about 1.2 s (95 features, 561 routes). Lazy mounting was not attempted.
- **E5** — Owner smoke checklist `RC19_OWNER_SMOKE.md` (12 steps) has placeholders `{B_COMPUTER_USE_STEPS}` and `{C_VOICE_STEPS}` that are not filled. §9 still describes the Jeff window as a "fixed-phrase preview", which is stale since `rc19/c-jeff`.

### F. Computer Use
- Status: `COMPUTER_USE=REAL_PASS` for a–g, h is MOCK_ONLY (`docs/security/COMPUTER_USE_THREAT_MODEL.md:68-75`).
- Fixed: R6 (409 instead of 500, `bcc/api.py:1332-1340`) and R10 (`features/action_contract.py:444-456`).
- Still open (backlog B-1…B-6):
  - R1: full-screen screenshots → crop to the target window.
  - R2: shift+F10 inside file dialogs.
  - R4: `file_exists` oracle.
  - R5: approval shows no screen crop.
  - R7: Jeff holds the full-authority token → needs a scoped studio token.
  - R8: legacy bossman-core `/computer/tasks`.
  - R9: Smart App Control and `pywin32` in `verify_runtime`.
- Must be repeated on the new archive.

### G. Browser
- Nothing in these docs is Browser-specific; the only related gap is the 2026-09-26 owner UI smoke (`WORKBENCH_20260926.md:196-198`), which was BLOCKED because Computer Use could not establish the browser URL.
- The Jev ultrafast adapter status is in `docs/owner/JEV_TOMORROW.md` (exit codes 0/1/2/3).
- Chromium is downloaded at build time without a byte digest (P2, `RC19_FINAL_FREEZE_CI_PLAN.md`).

### H. Agentic Rave
- Merged (`24140668`). CLI `bossman rave ...` in `bcc/rave/cli.py`; API `/api/rave`; UI `ui/pages/rave.js`.
- `docs/v1.9/AGENTIC_RAVE.md:8` still says "NOT merged". Stale doc.
- Live Claude/Codex subscription runs were not re-verified (`rc19/p-green` live tests were partial). `rc19/p-green` has 16 commits not in HEAD, mostly voxel3d and collector.

### I. Motion Studio
- UI and API exist: `bcc/features/motion_studio.py` `/api/motion-studio/{status, examples/{name}, jobs, jobs/{id}, cancel, check, file}`; `ui/pages/motion_studio.js`. Backlog #16b is effectively done.
- **OPEN #16c:** `tools/motion_studio` is not in the Windows bundle allowlist (`tools/build_windows_bundle.py`); the tool can be located via env `BOSSMAN_MOTION_STUDIO_DIR`. Also open: #12–16a (vertical formats, RU narration, blocking invented-number check, epic style).
- Untracked files exist only on the owner disk (`wt-motion-animation-56`); WIP snapshot `wip/motion56-20260929`.

### J. YouTube/K1m6a, SwapMe, FreshVibes, 3D, video
- **J1 — K1m6a** (`docs/trading/K1M6A_YOUTUBE_VERIFICATION_RC19.md`): the pipeline is VERIFIED; claim extraction precision is only about 25–38%, which blocks learning. Discovery limit fix `872bfb1b` is merged. Marker: `YOUTUBE_K1M6A=PARTIAL`.
- **J2 — SwapMe / Fresh Vibes** (`docs/owner/journeys/ADMIN_JOURNEYS.md:104-115`): 9/9 journeys on fake data, but there are no product domain modules (only harness-registered tools). The markers `SWAPME_ADMIN` and `FRESH_VIBES_ADMIN` are PARTIAL. The Instagram Day-1 DM receptionist is not proven (`FRESH_VIBES_BEAUTY_INSTAGRAM_DAY1_20260926.md:311`).
- **J3 — Not in HEAD:** `rc19/h-3d` (9 commits, voxel3d); `rc19/i-collector`; `calls19`, `claude/telegram-live-calls-s7-work` (PR #87) and `tgcalls-work` (out of scope per `tools/release_candidate.json`); `feat/jeff-ux-integration-test-20260926` (8 commits: video pipeline, nightly mailing via Jeff).
- **J4 — Seedance/Genjutsu video through the Пульт** (`MASTER_PROMPT` stage 6): the live run waits for the owner's materials; `VIDEO_E2E` NOT_VERIFIED.

### K. Freeze / packaging
- **K1** — Old gate markers are still binding (`CONTINUE_FREEZE_FINAL.md:100`): `FREEZE_1_5_1_7=BLOCKED`, `READY_FOR_1_8=NO`.
- **K2** — Local `verify_windows_bundle.py` fails under Smart App Control (`opentimelineio .pyd`). Do not disable SAC.
- **K3** — `POST_FREEZE_BACKLOG.md`: multi-reservation bookkeeping for `DirectApiBudget`, and outreach sweeps.

## 3. Owner-run commands that exist (verified in the repo)

```powershell
# source-checkout start / doctor (start-bossman.ps1:16-31)
.\start-bossman.ps1 [-DoctorOnly] [-SkipInstall] [-EveningTest] [-Port N] [-Web]
# owner acceptance; needs $env:BCC_DATA_DIR; runs `python -I -m bcc.owner_acceptance`
.\owner-acceptance.ps1
python scripts\evening_acceptance.py run|status|report|verify [--only T06 T09] [--redo]
# side-by-side RC (tools/rc19_side_by_side.ps1:47-48)
pwsh -File tools\rc19_side_by_side.ps1 -Action Build|Install|Start|Status|Stop|Restart|RollbackCheck|Rehearse|Shortcuts|ShortcutTest|StopRC -Sha <40hex> [-DataDir] [-Port 8831] [-ArchiveSha256] [-JeffPort 8850]
# switch owner machine to ONE Bossman (tools/owner_one_bossman.ps1:38-39)
pwsh -File tools\owner_one_bossman.ps1 -Action Switch|Snapshot|Install|Launchers|StopOld|Backup|Configure|Tasks|Start|Agents|Shortcuts|Status -Sha <40hex> -Archive <zip> -ArchiveSha256 <h>
# certification
python tools\exact_sha_certify.py --sha <40hex> --fetch --repo molotroka123-cell/AiMaxBossman --output exact-sha-certification.json
# debug recorder (start BEFORE Bossman)
powershell -ExecutionPolicy Bypass -File tools\bossman_debug_recorder\install_desktop_shortcut.ps1
tools\bossman_debug_recorder\START-RECORDER.cmd ; tools\bossman_debug_recorder\STOP-RECORDER.cmd
# Telegram companion (Пульт)
.\scripts\Start-TelegramCompanion.ps1 -Mode run|diagnose|setup|setup-console|unlock-delegation
python -I -m bcc.telegram_companion --diagnose
```

**`bossman` CLI** (`bossman-core/bossman/cli.py:25-29`, which hands off to `bcc/terminal_cli/cli.py`):
- `chat`, `exec`, `-p`, `status`, `events`, `result`, `resume`, `approve`, `deny`, `pause`, `continue`, `stop [--all --json]`;
- `list models|agents|skills|tools|tasks|approvals`, `keys`, `code`, `evolution`, `repair`, `run`, `evolve`, `review`, `rate`, `start`, `market`, `version`;
- `rave …`, `autonomy …`;
- `bossman pit setup|status|doctor|start|stop|routes|web|web-user|web-setup|passport-checkpoint|master-parse|watch` (`bcc/pit/cli.py:36-37`).

Console scripts: `bcc`, `bcc-desktop`, `bcc-open`, `bossman-telegram`, `bossman`, `bossman-gateway`.

`tools/owner_journeys/start_learning_247.ps1` does **NOT** exist in HEAD (it is only on `rc19/n-self`).

## 4. Telegram owner reporting

**How the channel is configured**
- Config: `%LOCALAPPDATA%\Bossman\telegram-companion\config.json`, with `people[]` entries `{user_id, chat_id, role:"owner"|"guest"}`.
- Secrets live in `credentials.enc` (Vault), or in env/`companion.env` beside the config.
- Secret variables: `TG_COMPANION_BOT_TOKEN`, `TG_COMPANION_CORE_TOKEN`, `TG_COMPANION_LOCAL_TOKEN`, `TG_COMPANION_CLOUD_TOKEN`, `TG_COMPANION_PROXY` (`bcc/telegram_companion/config.py:230-259`).
- `BOSSMAN_TELEGRAM_CONFIG` overrides the path for `features/telegram_settings.py`.
- The core notification stack uses `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_WEBHOOK_SECRET` (`bossman-core/.env.example:36-40`, `bossman/config.py:51-57`). That is a separate channel, not the Пульт.

**Is there a CLI to send an owner report? No.**
- The companion `__main__` modes are only `--setup`, `--setup-console`, `--diagnose` and `--unlock-delegation`.
- The `/api/telegram/*` endpoints (`features/telegram_settings.py:204-748`) cover settings, test, token, commands, people, profile, export, status, start and stop. There is no send endpoint.
- The companion only pushes results of tasks the owner submitted from Telegram itself: `service.py:1364 notify_tasks` reads the `proposals` table.
- Previous sessions used an out-of-repo script, `C:\Users\asd\Bossman\evidence\rc19\lead\tg_owner_report.py <file.txt>` (text via stdin `-` or `--photo`; `CONTINUATION.md:42`, `MASTER_PROMPT:77-78`).

**In-repo pattern to copy for a new sender** (`bcc/market/notify.py:152-170`): `default_config()` → `load(config, env_file=config.parent/"companion.env")` → owner = the Person with `role=="owner"` → `Telegram(settings).send(owner, text)`. It uses sendMessage only, never getUpdates, so it does not create a second poller.

A fuller, not-yet-merged library also exists: `tools/owner_journeys/owner_notify.py` on `rc19/n-self` @ `5be46396`. It has a durable outbox, secret-pattern refusal, dedup and a daily cap, but no CLI `main`.