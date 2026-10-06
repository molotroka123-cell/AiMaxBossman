# Cartography: Motion Studio, YouTube/K1m6a, SwapMe, Fresh Vibes, WIP ports

Scope: branch `claude/bossman-1.9-owner-bugtest-20260930` @ `b6018ee3`, working tree clean. I read code only; no tests or servers were run. One exception: to check that motion56 is complete, I extracted the WIP tree into the scratchpad (`git archive`) and ran its `build_library.py` there. Nothing in the repo was touched.

## 0. Summary

| Area | State in HEAD |
|---|---|
| Motion Studio | **Works for Epic only.** Backend endpoints, UI page and CLI renderer exist and are tested with a stub plus ffprobe. No brief→spec generation in the UI, no voice from the UI, library not exposed, not in the Windows bundle, no Terminal/Telegram surface. |
| YouTube/K1m6a | **CLI tools only.** Discovery, ingest (captions → local ASR → local vision) and claim verification against Binance/Bybit exist. The only product surface is `/api/v15/owner-run` (passes `youtube_url` to the self-improve runner). No UI or endpoint for the batch. Two batch tools duplicate each other. |
| Twitch collector (k1m6a) | **Exists and works** as a separate process. Read-only `/api/market/*` and `bossman market`. **Data-dir mismatch** between the CLI default and the backend. |
| "Jev Twitch collector" | **Not in code.** `bcc/jev/client.py` is the Jev/TypeSafe decision-API client. The only Twitch collector is `bcc/market/collector.py` (channel k1m6a). |
| SwapMe | **Harness only.** Fake-data domain and tools are registered only inside `tools/owner_journeys`. No product module. Doc marker is `SWAPME_ADMIN=PARTIAL`. |
| Fresh Vibes | **Harness plus stubs.** Same harness. `bossman-core` gmail/crm tools are `_stub` handlers. The Instagram Day-1 run is docs only, with status **OWNER LIVE PENDING / NOT ACCEPTED**. |

---

## 1. Motion Studio

### 1.1 Code map
- **Backend:** `command-center/bcc/features/motion_studio.py`, 332 lines. `FEATURE = Feature(name="motion_studio", router=router)` at L332. It is auto-mounted under `/api` with token auth by `bcc/features/__init__.py::load_features` (L34) through `bcc/api.py:118`.
- **UI:** `command-center/ui/pages/motion_studio.js`, 119 lines. Lazy page at `ui/pages/index.js:115` (`id:'motion-studio'`, `nav:'more'`, `section:'studio'`).
- **Tool (CLI), `tools/motion_studio/`:**
  - `make_video.py` (163 lines, entry point: `--style classic|epic`, `--preview`, `--no-voice`, `--tts-models`)
  - `spec.py` (validator, `SCENE_TYPES` L19)
  - `epic.py` (Pillow renderer; `SUPPORTED={'title','cards','grid','voice','logo','end_card','roadmap','bars'}`; `render()` L175 pipes raw frames into ffmpeg and writes `video.partial.mp4`, renamed to `video.mp4`)
  - `engine.html` (classic Chromium/Playwright renderer)
  - `score.py` (synth music), `generate_spec.py` (local LLM brief→spec; `assert_free_or_local` guard from `693ad44d`)
  - `lottie_assets.py` (100 pinned Noto items in `lottie/catalog.json`, fetched from `fonts.gstatic.com` into `~/.cache/bossman-motion/lottie`)
  - `library/build_library.py` (44 scenarios), `dataset/brief_to_spec.jsonl` (46 rows), `finetune_lora.py` (NOT_TESTED)
  - `verify_library.py`, `verify_lottie.py`
  - `examples/{bossman_32_days,bossman_epic_22s,jeff_voice_12s}.json`
- **Tests:**
  - `command-center/tests/test_motion_studio_ui.py` (stub `make_video.py`, real ffmpeg/ffprobe check, cancel, whitelist, auth, real Epic preview when deps exist)
  - `tests/test_motion_studio.py` (24 validator/generator tests)
  - `tests/test_motion_epic.py` (3)
- **Docs:** `docs/v1.8/MOTION_STUDIO.md`, `MOTION_EPIC_RECIPE.md`, `MOTION_STUDIO_OSS.md`, `CODEX_MASTER_PROMPT_MOTION_STUDIO.md`.
  - **Stale:** the header line 5 says "Кнопки в Command Center нет" (no button in the Command Center). The UI was added in `8720f09b`.

### 1.2 Endpoints (prefix `/api/motion-studio`)
- **`GET /status`**
  - Returns `{available, tool_dir, presets:["epic"], examples:[stems of examples/*.json], deps:{numpy,pillow,scipy,ffmpeg,ffprobe}, ready_preview, ready_full, why_not, jobs:[public×10]}`.
- **`GET /examples/{name}`**
  - Returns the raw spec JSON; 404 if the name is not in `examples/`.
- **`POST /jobs`** (202)
  - Body `JobIn`: `mode` (`preview|full`), `example|spec` (exactly one), `times` (≤8 floats, default `[1,5,9]`), `no_voice=True`, `tts_models=""`.
  - 409 if the tool is missing, a job is running, or ffmpeg is missing for full.
  - 422 for both or neither of example/spec, spec over 256 KB or without `scenes`, or `full` + voice without `tts_models`.
  - Spawns `python make_video.py spec.json --work DIR --style epic [--preview …|--no-voice|--tts-models X]` through `asyncio.create_subprocess_exec`. Output goes to `make_video.log`.
- **`GET /jobs`**, **`GET /jobs/{id}`**
  - Public job shape: `{id, mode, state (running|done|failed|cancelled|interrupted), error, started, finished, style, no_voice, scenes, subtitle_lines, times, music, source, log_tail (last 2000 chars), outputs[]}`.
- **`POST /jobs/{id}/cancel`**
  - `proc.kill()` if the process is owned by this backend, then marks the job `cancelled`.
- **`GET /jobs/{id}/check`**
  - Preview: every `epic-preview-*.png` must be larger than 1000 bytes.
  - Full: ffprobe must show a video stream, an audio stream and duration > 0; also returns sha256 and codecs.
- **`GET /jobs/{id}/file?name=`**
  - Whitelist: `OUTPUTS` (L45) plus `epic-preview-*.png`, with no `/` or `\`.
- **No SSE or bus events.** The UI polls every 2 s (`POLL_MS`, L111) only while a job is running.

### 1.3 What works
- Epic preview and Epic full render without voice.
- "Done" means the file was checked (`_settle` L129 → `_check` L161).
- Failure paths are covered: non-zero exit code, and missing audio is reported as a failure (tested).
- One job at a time.
- Job state is persisted atomically to `<data_dir>/motion-studio/<id>/job.json` (`_save` L86, tmp file + `os.replace`) and reloaded by `_load_jobs` (L101).
- Nothing is published. No cloud, login or paid action exists, so no approval gate is needed.

### 1.4 Defects and gaps (Motion Studio)
1. **Restart recovery is wrong both ways** (`_refresh` L111, `_pid_alive` L93).
   - If the orphaned render is still alive after a backend restart, the job stays `running`. It is not in `_PROCS`, so the timeout is never enforced.
   - `cancel` (L300) then only relabels the job `cancelled`; the pid is never killed. The job also blocks new jobs.
   - When the orphan exits, `_settle(job, None)` marks it `interrupted` without running `_check`. A render that finished and is valid is reported as lost.
   - PID reuse is not guarded.
   - Fix: in `_refresh`, for a job with no proc, run `_check` first when the pid is dead. Kill by pid (psutil) in `cancel`. Record the process create time next to the pid.
2. **Event-loop blocking.** `_settle` → `_check` runs synchronously inside async handlers (`status`, `jobs`, `get_job`): `subprocess.run(ffprobe, timeout=60)` plus `video.read_bytes()` for the sha256 of a whole MP4. Only `/check` uses `asyncio.to_thread`.
3. **Race (time-of-check vs. time-of-use).** `start_job` checks "running" and then awaits `create_subprocess_exec` before inserting into `_JOBS`. Two concurrent POSTs can both start. Needs an `asyncio.Lock`.
4. **Timeout depends on someone polling.** `JOB_TIMEOUT_SECONDS=40*60` is enforced only in `_refresh`. There is no `tick` in `FEATURE`.
5. **Cancel kills only the Python process** (`proc.kill()`). On Windows the ffmpeg child (`epic.py:181`) is orphaned; it gets EOF and exits. `video.partial.mp4` is left behind because the `finally` block never runs.
6. **The UI overwrites owner edits.** `render()` always calls `loadExample()` (L88). `ctx.refresh()` after start and every 2 s while running replaces the custom spec JSON in the textarea with the example.
7. **Voice is unreachable from the UI.** The page never sends `tts_models`. Unticking "Без озвучки" (no voice) produces a 422. There is no Kokoro path setting.
8. **Epic only.** `--style epic` is hard-coded (L257-258).
   - 19 of the 44 library scenarios use `sticker` and are rejected by `epic.Renderer`.
   - The library is not exposed at all: `_examples()` reads only `examples/`.
   - The classic path (Chromium, Lottie, fonts from `tools/intro_video/fonts`, not in git, fetched from the network) is CLI only.
9. **No brief→spec in the product.** `generate_spec.py` is not wired into any endpoint. There is no "preview → owner approves → full render → append to dataset" step, although the docs list it under "Что дальше" (next steps).
10. **Not on other surfaces.** Motion Studio is not in `terminal_cli/cli.py` and not in Telegram, which the Terminal Run same-product contract expects. It is not in `tools/build_windows_bundle.py`, so the installed product shows "Недоступно" (unavailable).
11. **Vertical 9:16 and Russian TTS are missing** (docs "Не сделано", not done). There is no job retention or cleanup.

---

## 2. YouTube / K1m6a

### 2.1 Code map
- **`tools/k1m6a_youtube_batch.py`** (233 lines)
  - `discover()` L42: `yt-dlp --skip-download --dump-json --dateafter/--datebefore`, timeout 1800 s. The limit is applied after the date filter (fix `52368bdc`; test `tests/test_k1m6a_discovery_limit.py`).
  - `ingest_one()` L124 runs `tools/youtube_trader_ingest_auto.py` with a 4 h timeout.
  - `episode_fingerprint()` L99 deduplicates reuploads.
  - Writes the manifest `data/trading/youtube_batches/k1m6a-START-END/batch-manifest.json` (`trust:"PUBLIC_UNTRUSTED_TEACHER"`, `promotion:"QUARANTINE_ONLY"`) after every video, so a rerun reuses PASS rows.
- **`tools/youtube_trader_ingest.py`** (568 lines; `_api_json` L259 guarded by `assert_free_or_local`)
- **`_auto.py`** (captions → local ASR, guarded at L87-88)
- **`_asr.py`**
- **`_claims.py`** (548 lines): local Ollama claims; verification with Binance BTCUSDT 1m klines and Bybit OI; `UNVERIFIED` by default. Tests in `tests/test_youtube_claim_verification.py`.
- **`_batch.py`** (132 lines). **This is the one the product uses:** `tools/bossman_15_self_improve.py:99` and the bundle (`build_windows_bundle.py:120`). `k1m6a_youtube_batch.py` is a parallel CLI that nothing calls.
- **Product surface:** `bcc/features/v15_owner_run.py`
  - `POST /api/v15/owner-run/start`, body `{repo, cycles 1-20, allow_glm, youtube_url, cadence 5-120}`.
  - Also `GET /status`, `POST /quick-test`, `POST /stop`.
  - Bus events `v15.owner_run.started` and `v15.owner_run.stop_requested`.
  - Shells out to `tools/bossman_15_owner_run.py` (`start()` L141 spawns the market collector and self-improve).
  - UI: `ui/pages/v15_owner_run.js`.
  - Env `BOSSMAN_K1MBA_YOUTUBE_URL`. Note the "K1MBA" spelling, not k1m6a; it is consistent in both scripts.
- **Status doc:** `docs/v1.6/runs/YOUTUBE-001-RESULT.md` says **PARTIAL / NOT ACCEPTED**. There are no verified outcomes and the bounded 08-14..08-27 batch was not completed.

### 2.2 Gaps
- `k1m6a_youtube_batch.main`:
  - `subprocess.TimeoutExpired` from `ingest_one` (L133) is not caught, so the whole batch crashes and no summary is written.
  - `independent_episodes = len(videos) - duplicates` counts FAIL and NOT_RUN videos as independent evidence. This over-claims.
- Discovery without `--break-on-reject`/`--break-match-filters` extracts every channel entry; for a large channel it is likely to hit the 1800 s timeout (my reading, not measured).
- Two batch implementations: pick `youtube_trader_ingest_batch.py` and retire or merge the other.

### 2.3 Twitch collector (`bcc/market/collector.py`, 473 lines)
- `TwitchSource` (L98): Playwright, headless, `https://www.twitch.tv/k1m6a`, pins 1080p. Allowed DOM actions are only decline-cookies and start-watching; no login, no chat.
- `Collector.run` (L385): STOP file checked every 0.5 s; ledger SQLite `market-observations.sqlite`; heartbeat `reports/collector-status.json`.
- Duplicate runs are refused through `collector.pid` plus `_pid_alive`.
- Vision reader is `extract.OllamaVisionReader` (local).
- Owner notifications go through `market/notify.py` → the existing Telegram transport.
- API in `bcc/features/market_metrics.py`: `GET /api/market/status`, `GET /api/market/export?day=` (CSV), `POST /api/market/stop`. There is intentionally no start, trade or order endpoint.
- CLI: `bossman market watch|status|export|stop` (`terminal_cli/cli.py:339`).
- **Defect: data-dir mismatch.** `ledger.default_root()` (L26) uses `BCC_DATA_DIR` or `%LOCALAPPDATA%|~/Bossman/CommandCenter`. The backend's `config._data_dir()` (L18) uses `command-center/data` for a source checkout (the owner's :8800 setup) and XDG on Linux.
  - So `bossman market watch` without `--root` writes where `/api/market/status` never reads.
  - Only the v15 owner-run path passes `--root <data_dir>/market-data/twitch/k1m6a` explicitly.
  - Fix: make `default_root` use `bcc.config._data_dir()`.

---

## 3. SwapMe and Fresh Vibes

- **`tools/owner_journeys/admin_domain.py`** (308 lines): "EVERYTHING HERE IS FAKE TEST DATA."
  - SwapMe: `SWAPME_RATES`, `swapme_validate` L57, `swapme_quote` L87, hash-chained `Journal` (L112, `verify_chain`).
  - Fresh Vibes: `faq_answer` L231, `medical_guard` L226, `capture_lead` L248, `request_booking` L275, `queue_reminder_draft` L297.
- **`tools/owner_journeys/admin_journeys.py`** (418 lines): `build_tools()` L68 defines `ToolSpec`s.
  - `swapme.queue_operator_review` and `freshvibes.request_booking` are `default_effect="ask"`, i.e. they go through a real bcc approval.
  - Everything else is `auto`.
  - They are registered **only** by the harness (`h.register(build_tools(store))` L360 → `bcc_harness.py:118` `REGISTRY.register`, unregistered on close).
  - The output states `"harness_only": ["domain ToolSpecs registered by harness (no SwapMe/Fresh Vibes product module)"]` (L384).
  - Tests: `tests/owner_journeys/test_admin_journeys.py` checks that a `swapme_execute_transfer` attempt is refused and that `queue_operator_review` does not run without approval. `test_admin_restart.py` covers create → save → reopen → restart.
- **Measured runs** (`docs/owner/journeys/ADMIN_JOURNEYS.md`): local model 5/9, then 8/9, then **9/9 in 127 s** on 2026-09-28. The suggested markers are `SWAPME_ADMIN=PARTIAL` and `FRESH_VIBES_ADMIN=PARTIAL`.
  - Listed product findings: there are no domain modules or business namespaces.
  - The same doc lists `OpenAICompatAdapter` as unable to turn Qwen thinking off. Commit `e94a79e9` on cv/e ("local Ollama /v1 chat runs with reasoning off") appears to address this; not verified here.
- **`bossman-core/agents/fresh-vibes/agent.yaml`**: `cloud_policy: never`; `gmail.send: confirm`, `crm.write: confirm`. But `bossman-core/bossman/toolkit/office.py` implements every gmail/crm tool as `_stub` ("коннектор ещё не настроен (этап v0.4)", i.e. connector not configured yet). **Stub.**
- **Instagram:**
  - `docs/owner/FRESH_VIBES_BEAUTY_INSTAGRAM_DAY1_20260926.md` is a plan with an owner-approval gate.
  - `docs/v1.6/runs/INSTAGRAM-001-RESULT.md` says **NOT ACCEPTED**: nothing was sent.
  - `apps/social-farm/` (66 .py files; Instagram official plus browser adapter with `approval_ref`) is standalone (`imports_bossman: false`), is not wired to bcc, and was not verified here.
- **In bcc itself:** no SwapMe or Fresh Vibes code. The only mention is the topic regex at `video_studio/service.py:60`.

**Gap to reach the "product path":** move `admin_domain` and the `build_tools` ToolSpecs into a bcc feature module, e.g. `bcc/features/business_admin.py`, registered at startup. Keep the `ask` effects and a per-business namespace or data dir, and expose them through CLI and Telegram as well.

---

## 4. WIP snapshots: verdicts

### 4.1 `origin/wip/motion56-20260929` (stash `a1593fe5`) — **PORT, but the WIP alone is incomplete**
- **Parents:** base `ef1c3909` (an ancestor of HEAD) and index `b8f9441e` (empty). There is no third parent, so the **untracked files were not saved**:
  - the 56 new `library/*.json` files;
  - `dataset/candidates/motion_animation_56.jsonl`;
  - `artifacts/motion-animation-56-20260928/blocked_font_dependency.json`.
- **Diff contents:**
  - `build_library.py`: +623 lines. Adds `add_candidate()` (sets `meta.status="candidate"` and `meta.provenance`), 56 `add_candidate` calls, the 5-tuple `LIB`, candidates written to a separate jsonl with `owner_approved:false, training_use:"excluded"`, `newline="\n"`, and a duplicate-id guard.
  - Tests: +236 lines, 12 new tests plus a rewrite of `test_library_has_44…` into `…_100…`.
  - Doc hunk.
- **HEAD vs WIP base:** `build_library.py` and `tests/test_motion_studio.py` are **identical**. `spec.py`, `engine.html`, `lottie*` and `finetune_lora.py` are unchanged. The only code change since the base is the route guard in `generate_spec.py`.
- **Generator result:** the builder is deterministic (no Kokoro; timing is the formula `0.3 + chars/24`). Run in the scratch copy it printed `LIBRARY 100 scenarios valid (44 approved, 56 candidate); dataset rows: 46 approved + 56 candidate`. All 44 existing JSONs and `brief_to_spec.jsonl` came out **byte-identical** to HEAD.
- **If only the three files are ported without regenerating** (my reading; tests not run):
  - About 10 of the 13 new or changed tests fail: `…_100…`, `splits`, `a_candidate` (KeyError on `intro_freelancer`), `every_candidate_row` and `brief_states_length` (FileNotFound), `no_scenario_renamed`, `cover_varied`, `only_numbers`, `lottie_use_mixes`, `generator_reproduces`.
  - Three pass without testing anything, because there are no candidates to loop over.
  - With the regenerated files, the existing tests are unaffected: approved rows are still checked against the dataset, and the dataset is unchanged.
- **Port instructions:**
  1. `git checkout origin/wip/motion56-20260929 -- tools/motion_studio/library/build_library.py tests/test_motion_studio.py`
  2. `python tools/motion_studio/library/build_library.py`, then stage the 56 new `library/*.json` files and `tools/motion_studio/dataset/candidates/motion_animation_56.jsonl`, listing paths explicitly (no `git add -A`).
  3. Add to `.gitattributes`: `tools/motion_studio/library/*.json -text` and `tools/motion_studio/dataset/** -text`. Otherwise `test_the_generator_reproduces_all_100_files_byte_for_byte_with_lf_endings` fails on a Windows autocrlf checkout. No such rule exists today.
  4. Port the doc hunk into `docs/v1.8/MOTION_STUDIO.md` "## Библиотека" by hand:
     - Remove the duplicated "Известная слабость: сцена grid" bullet; the WIP adds a new one and keeps the old one.
     - Mark "100/100 сценариев, 308 сцен … на ПК владельца" (100/100 scenarios, 308 scenes on the owner PC) as NOT VERIFIED, since its evidence file was lost.
     - Fix the stale status header ("Кнопки в Command Center нет").
  5. Say explicitly in the doc: 26 of 56 candidates use `sticker` and are **not renderable from the Command Center** (Epic only). The library is still not exposed in the UI.

### 4.2 `origin/wip/cv-d-20260929` (stash `abbb5d87`, base `7bc8d053`, ancestor of HEAD; `origin/cv/d` is merged) — **PORT (HEAD does not cover it)**
- **What HEAD does today:**
  - `_worker` (`pit/runtime.py:1119`) still has an unconditional `except asyncio.CancelledError: self.store.finish(update_id, "delivery_unknown"); raise` at **L1140-1142**.
  - `run()` (L968) cancels every worker in its `finally` (L988-991), on owner STOP, on a task exception, or on process shutdown.
  - So a graceful restart during `_generate_image` (L2283; phases `requesting` or `studio_submitted`) moves the inbox row from `processing` to `delivery_unknown`.
  - On the next start, `recover()` (`telegram_companion/store.py:103`) only moves `processing` rows to `interrupted_unknown`.
  - `interrupted_generations()` (L483) requires `i.phase='interrupted_unknown'`, so `_reconcile_generations` (L2359) never resumes the job. The image is lost and the `media_jobs` row is stuck.
- **Existing coverage:** HEAD's recovery tests (`command-center/tests/test_pit_generation_recovery.py`) only simulate crashed rows that stay `processing`. The L2298 comment covers crashes, not cancels.
- **Where the hunk goes now:**
  - Replace L1140-1142 with the WIP's guarded version.
  - Add the method `_generation_resumable_after_restart(self, update_id)` to `ParticipantRuntime`, for example right before `_voice_reply_wanted` (L1291). It uses `STOP_FLAG` (L78) and `self.store.generation_phase` (L489) and returns True only when no STOP flag exists and the phase is in `{"requesting","studio_submitted"}`.
  - Leave the second `CancelledError` handler (L1275, the reply-delivery phase) unchanged. `sending` must stay `delivery_unknown`.
- **Required tests** (none in the WIP), in `test_pit_generation_recovery.py`:
  - (a) Cancel the worker while `broker.generate` is awaiting, with no STOP flag → inbox stays `processing` → new runtime `recover()` + `_reconcile_generations()` → resumed once, sent once.
  - (b) Same with `stop.flag` present → `delivery_unknown`, nothing resumed (negative control).
  - (c) Cancel during `send_photo` (phase `sending`) → never uploaded again.

### 4.3 `origin/wip/cv-e-20260929` (stash `aeff89c1`, base `e94a79e9`, ancestor of HEAD) — **PARTIAL PORT / LOW PRIORITY**
- **HEAD already prevents the failure** (commit `9e813d1f`):
  - `sync()` clears `pricing_known` and prices for every provider model and restores them only for models present in the catalog.
  - `task_admission.py:86-91` skips agents with `refuses_unknown_price` and gives a precise reason ("модель удалена из каталога", i.e. model removed from the catalog).
  - `registry._catalog_state` (L185) and `provider_governance._UNKNOWN_PRICE_WHY["stale"]` name the withdrawn model in the refusal.
  - `v2/model_router.disqualify` also rejects "unknown or invalid cloud pricing".
- **What HEAD lacks:** registry and UI truth. The withdrawn row keeps `models.status='online'`.
- **DB:** no migration needed. `db.py:54-56` already has `status String(16)` ("unavailable" is 11 characters; SQLite, no CHECK), `status_detail Text` and `last_check DateTime`. `models_t` is already imported in `openrouter_catalog_service.py:10`. Only update the column comment at L54.
- **Port instructions:**
  - Take the `openrouter_catalog_service.py` part as is. HEAD's file is unchanged since the base.
    - Add the constants after L17.
    - Add `unavailable = await self._mark_catalog_presence(s, provider_id, remote_ids, now)` just before `stale_left = …` at L157.
    - Add `"unavailable": unavailable` to the returned dict at L169-170.
    - Add the static method before `catalog_status` (L172).
  - **Do not apply the `task_admission.py` hunk (L83) as is.** Adding "unavailable" to `{"offline","error"}` would fire before L86 and replace the precise catalog reason with the generic "модель не отвечает" (model not responding). Either skip it, or add a dedicated `status=="unavailable"` branch after L86 that reuses the catalog message.
  - Add `unavailable` to `STATUS_LABEL`/`STATUS_TONE` in `ui/components.js` (L253/L269).
  - Tests: missing → `unavailable`; back in the catalog → `unknown`; empty catalog → no change; pinned alias keeps its identity. No existing test asserts the exact sync dict (`test_v21_openrouter_router.py:173` checks individual keys).

---

## 5. Prioritized gaps (file:function)

1. **P1** `bcc/pit/runtime.py::_worker` L1140: port cv-d plus 3 tests. Images are lost on restart today.
2. **P1** `bcc/features/motion_studio.py::_refresh`/`_settle`/`cancel`: fix orphan recovery (check before calling it interrupted, kill by pid, apply the timeout without polling), move `_check` off the event loop, add a lock in `start_job`.
3. **P1** `bcc/market/ledger.py::default_root`: use `bcc.config._data_dir()` so `bossman market` and `/api/market/status` share one data directory.
4. **P2** `ui/pages/motion_studio.js::render` L88: do not overwrite a custom spec on refresh. Add a voice path (the `tts_models` setting) or hide the checkbox.
5. **P2** Motion product flow: endpoint for `generate_spec` (brief → spec), approval between preview and full render, append to the dataset on owner approval, library listing with a classic/epic compatibility flag, a `bossman motion` CLI command, and inclusion in the Windows bundle.
6. **P2** Port motion56 as in §4.1, including `.gitattributes`.
7. **P2** SwapMe / Fresh Vibes: move the `tools/owner_journeys/admin_domain.py` and `admin_journeys.build_tools` ToolSpecs into a product feature with business namespaces. Replace the `bossman-core/.../office.py` stubs or mark them clearly.
8. **P3** `tools/k1m6a_youtube_batch.py::main`: catch `TimeoutExpired` per video; count independent episodes only among PASS rows. Consolidate with `youtube_trader_ingest_batch.py`.
9. **P3** cv-e catalog-presence marking plus the UI label; update the `docs/v1.8/MOTION_STUDIO.md` status header.

Same-product (Terminal Run) note: Motion Studio is dashboard-only; the market collector is on dashboard and CLI; SwapMe and Fresh Vibes are harness-only. None of these moves a North Star ladder level. They are infrastructure, and nothing here counts as learning evidence.