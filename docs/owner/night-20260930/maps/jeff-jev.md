# Jeff / Jev / Jeff 2.0 / Jeff Admin: code map, root causes of the three open Jeff-window bugs, and a prioritized fix list

Repo `/home/user/AiMaxBossman`, branch `claude/bossman-1.9-owner-bugtest-20260930` @ `8cfa0481`. Everything below was read only; nothing was edited or run. Line numbers are HEAD unless noted.

**Summary:**
- Most likely explanation for all three RC19 findings: most chat turns went to a free-cloud route that had no key, and failed turns are never saved. Details in §4.
- HEAD partly fixes bug (a) with commit `b5402093`.
- HEAD does not fix bug (b) or bug (c).
- The evidence file `evidence/rc19/jeff-final2-1039/jeffweb_findings.json` is **not in the repo**. The harness that produces it is in the repo (`tools/ux_soak/jeff_web_blackbox.py`).

---

## 1. Real roles: Jeff vs Jev (and OpenCode)

**Jeff is the participant-facing chat persona.** The code is PIT ("Personal Identity Training"), `command-center/bcc/pit/*`. It has two surfaces:
- **Telegram Jeff bot:** `ParticipantRuntime` (`bcc/pit/runtime.py:555`). Started with `bossman pit start` (`bcc/pit/cli.py:505`) and supervised by `bossman pit watch` (`cli.py:552`).
- **Jeff window:** a loopback FastAPI app (`bcc/pit/web.py`, `WEB_APP_ID="bossman-jeff-web-v1"`, default port 8850 or `BOSSMAN_JEFF_PORT`).
  - It runs `WebParticipantRuntime(ParticipantRuntime)` (`web.py:249`).
  - It is launched by `bcc/jeff_desktop.py`: a Chromium `--app` window with its own profile `<data>/jeff-desktop-profile`, a build-SHA match check, and it never touches the owner's Command Center token or backend.
  - UI files: `ui/jeff.html` and `ui/jeff.js`, served by `web.py:482` (a whitelist of 4 files).
- Jeff has no owner authority. `FORBIDDEN_COMMANDS` is at `runtime.py:99`.
- The web transport has no Bot API: `WebTransport.call` raises `WEB_TRANSPORT_HAS_NO_TELEGRAM` (`web.py:199`).
- Person keys are HMAC values: `telegram:<id>` (`pit/identity.py:11`) and `web:<id>` (`web.py:241`).

**Jev is not a persona.** In code it is TypeSafe "System-1" (`bcc/jev/*`, stdlib only, **staged and disabled by default**):
- `bcc/jev/decision.py`: `JevDecisionProvider`, which records **shadow** routing hints only and never returns a model or approval (`BOSSMAN_JEV_ENABLED`, `BOSSMAN_JEV_SHADOW`).
- `bcc/jev/browser_fastpath.py`: a shadow adapter over `bcc.v2.browser_control` (`BOSSMAN_JEV_BROWSER_ENABLED`).
- `bcc/jev/client.py`:
  - endpoint `https://api.typesafe.ai/v1/systemone`, model `jev-1.13.0` (`jev/config.py:32-33`);
  - key from `BOSSMAN_JEV_API_KEY` or `TYPESAFE_API_KEY`;
  - kill file `BOSSMAN_JEV_KILL_FILE` (default `<data>/jev.disabled`).
- Consumers:
  - `/jev <request>` in the owner's Пульт companion bot (`telegram_companion/jev_bridge.py`). It picks one id from a closed `ACTIONS` list; `/approve`, `/confirm`, `/resume` and `/sh` are forbidden (`jev_bridge.py:33-53`).
  - `bcc/autonomy/planner.py:29-31`, `autonomy/skills.py:36`, `features/economy_swarm.py:22`.
- Docs: `docs/JEV_DECISION_ENGINE.md`, `docs/JEV_ULTRAFAST_BROWSER_CRITICAL.md`.

Things that are easy to confuse:
- `DEFAULT_FREE_CHAT_MODELS` contains `"typesafe/jev-1.5"` (`pit/config.py:27`). It is always rejected as `not_free_id` because it has no `:free` suffix (`runtime.py:703-704`). This is dead config.
- `docs/owner/BOSSMAN_JEV_TYPESCRIPT_AUTONOMY_ONE_RUN_PLAN.md` mixes the names ("Jeff plans…", "Jev is the only orchestration identity"). "Jev TypeScript" is an external orchestrator. Nothing of it is in this repo except the plan.
- `JEFF-0042` is an **autonomy goal about Jeff's identity** (`bcc/autonomy/identity_task.py:1`; `cycle.py:691` `--goal JEFF-0042`). It is not Jev.
- `identity_guard.py` says it covers "Jeff and Jev".

**OpenCode** has nothing to do with Jeff. It is the coding-session execution engine under the Command Center:
- `bcc/v2/opencode_bridge.py`: client for `opencode serve` v1 and v2; basic-auth env `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`.
- `bcc/features/opencode.py`: `/api/opencode/{health,roots,sessions,sessions/{id}/send|status|abort|fork|diff|children|todo},attach`.
- `features/tools_opencode.py`: model tools; the table is `opencode_sessions` in `bcc/v2/tables.py`.
- `tests/test_jeff_ux_isolation.py` asserts the Jeff server cannot reach `/api/opencode`.

---

## 2. Jeff 2.0 (branches `feat/jeff-2.0`, `-x`, `-y`, `-z` — all ancestors of HEAD, as are `rc19/t-jeff-admin` and `rc19/r-jeff-next-bugs`)

**Layer:** `bcc/pit/j2/`.
- `contract.py`: `TurnContext`, `Advice`, `J2Module`, `BaseModule`.
- `pipeline.py`: `J2Pipeline.discover(runtime)` imports each `j2.<name>.create(runtime)`.
- Timeouts: pre 0.4 s, augment 0.6 s, post 0.8 s. Notes budget 1800 chars. Breaker: 3 failures open it for 300 s.
- Global switch `BOSSMAN_JEFF_J2=off`.

**How it is wired into the runtime:**
- `ParticipantRuntime.j2` is created lazily (`runtime.py:1734`).
- `_chat_route` calls `j2.pre_route` (early reply), then `_chat_route_core`, which calls `j2.augment` right before the model call (`runtime.py:1945-1947`). Then `j2.post_reply`, then `guard_outgoing` (`runtime.py:1742-1757`).
- Background `start()` runs only in `ParticipantRuntime.run()` → `_j2_lifecycle` (`runtime.py:978`, `1021`). **This means Telegram only. The web window never starts j2 background work**: no proactive ticks, no insights snapshots, no model_guard canary.

**The ten modules:**

| Module (order) | File | Purpose | Env switch |
|---|---|---|---|
| safety (10) | `j2/safety.py` (742 lines) | Deterministic injection/jailbreak/harm/abuse patterns, per-participant rate limit and flood check, escalation audit `pit-v1.7/j2/safety-audit.jsonl`, post-reply check for system-prompt leaks | `BOSSMAN_JEFF_J2_SAFETY`, `_RATE_MAX`, `_FLOOD_MAX` |
| model_guard (20) | `j2/model_guard.py` (510) | `assess()` garbage/loop/foreign-script detection; canary ("столица Франции"); state machine healthy/suspect/degraded/recovering; unload-only recovery; APOLOGY replaces a garbage reply | `BOSSMAN_JEFF_J2_MODEL_GUARD` |
| director (30) | `j2/director.py` (721) | Intent and dialogue act, at most one clarifying question, topic state (`<person>/j2/director.json`), length/shape plan | `…_DIRECTOR`, `…_DIRECTOR_MODEL` |
| memory_palace (40) | `j2/memory_palace.py` (618) | BM25 plus an optional embeddings hook over the facts/events/style layers, forgetting curve, provenance; answers "what do you know about me / why" in pre_route | — |
| persona (50) | `j2/persona.py` (561) | Style deltas (±2) from the passport style layer; owner overlay wins; ~110-token notes; A/B log `j2/persona-ab.jsonl` | `…_PERSONA`, `…_PERSONA_AB` |
| research (60) | `j2/research.py` (612) | Extractive web research with citations and a cache; no model call | — |
| media (70) | `j2/media.py` (575) | Local vision/OCR, consent gate ("запомнить описание?") | — |
| proactive (80) | `j2/proactive.py` (1149) | Reminders, follow-ups, digest, quiet hours, idempotent sends; `<person>/proactive/schedule.json` | `BOSSMAN_JEFF_TZ_MIN` |
| quality_lab (90) | `j2/quality_lab.py` (1020) | Per-turn rubric scoring (`<person>/quality/scores.jsonl`), optional local judge, `run_comparison` (Jeff 1.0 vs 2.0 via `BOSSMAN_JEFF_J2` flip), `owner_report`, CLI `main()` at `:993` | `BOSSMAN_JEFF_QUALITY_JUDGE=on` |
| insights (95) | `j2/insights.py` (589) | Owner overview, trends, weekly digest; no turn hooks; exposed by `features/jeff_insights.py` | — |

Docs: `docs/pit/JEFF_2_0_*.md`, `docs/owner/JEFF_2_0_VISION.md`.

Also merged: commit `30948baf` added the mandatory `identity_guard.py` filter, red-team corpora, the memory-poisoning gate (`runtime.py:213`, `296`) and the pre-TTS audit (`speech_audit.py`).

**Wiring gaps (checked in code):**
- `model_guard.post_reply` reads `ctx.extra["served_by"]`, defaulting to `"local"` (`j2/model_guard.py:471`). The runtime never sets it, so cloud replies and error strings are all judged as local-model output.
- `ModelGuardModule.routing_hint()` is not used by the router (it appears only in status).
- `runtime.j2_escalate` and `runtime.j2_embed` are never defined, so there are no escalations and no embeddings (lexical retrieval only).
- A `j2.pre_route` early reply (`runtime.py:1752-1754`):
  - is not recorded in history;
  - is not passed through `guard_outgoing` on the web surface. `web.run_turn` never calls the guard; only Telegram's worker does, at `runtime.py:1208`.
- The docs say modules can be switched off through the owner overlay. In code they are switched off by env vars only; `pit/jeff_settings.py` has no j2 keys.
- `JEFF_VERSION = "1.1"` (`pit/version.py`) was not bumped.

---

## 3. Capability map (state, code locations, gaps)

**Answer quality, prompting and context assembly.** This works. Order in `_chat_route_core`:
1. `build_participant_context` (`participant_context.py:87`): `PIT_ASSISTANT_SYSTEM` (`:13`), rewritten for the web surface (`:105-109`); then the owner overlay `jeff_settings.style_for` (`:112`); then `participant_profile.system_text_for` (`:117`); then behaviour scales. The persona-facts system message is added only if memory is on, personalization is on, and (for a remote route) `remote_personalization_enabled` (`:122-129`).
2. The local route gets a hard-coded answer-shape and safety paragraph appended (`runtime.py:1897-1925`).
3. History (`:1926-1930`), then the `reply_to` quote, web-search block and roleplay.
4. The user message, then j2 notes inserted before it.

Gaps:
- Local "incomplete" heuristic: a reply ending in a letter with at least 5 words counts as truncated (`runtime.py:1993-1996`). That triggers a second call with "завершай ответ точкой", doubling latency on normal replies.
- Retrieval in `context.py:36` is token overlap only.

**Conversation context.** `PITStore`: `HISTORY_PAIRS=16`, `HISTORY_CHAR_BUDGET=16000` (`runtime.py:418-419`). `remember()` is at `:531`; `history()` is at `:539-552` (the newest pairs that fit the budget). Injection at `runtime.py:1926-1930`:
```python
use_saved_context = memory_context_allowed and (
    not route_is_remote or route_consent.remote_personalization_enabled)
if use_saved_context:
    history = self.store.history(who)
    messages += history[-2:] if provider == "local" else history
```
- A turn is written only on success with memory on (`:2092-2105` → `_record_chat` `:2107`).
- Failure replies return before recording (`:2063-2073`).
- Public-guard replies go only to `learning_log` (`:1431`).
- The web window has its own store `pit-v1.7/web/companion.sqlite3` (`web.py:259`).

**Personal memory per participant.**
- `PersonaVault` at `pit-v1.7/personalities/<person_key>/` (`vault.py:48`), path-escape checked in `identity.scoped_person_dir`.
- Files: `consent.json`, `facts.jsonl`, `raw/events.jsonl`, audit log.
- Extraction: `extract_candidates` (`runtime.py:273`) → `HighRecallCollector`.
- Participant commands: `/memory /forget /correct /pause_memory /resume_memory /delete_me /export_me`.
- Web endpoints: `GET /api/jeff/memory`, `POST /api/jeff/memory/correct` `{id,value}`, `POST /api/jeff/memory/delete` `{id}`, `GET|POST /api/jeff/privacy` `{memory_enabled, cloud_context_enabled}` (`web.py:662-721`).

**Participant passports (versioned).**
- `pit/passport.py`: schema `jeff.passport/1`, layers events/facts/style/procedures, envelope with source/confidence/scope/consent/correction history, `migrate_all`.
- Commands in `passport_commands.py`; checkpoint in `passport_checkpoint.py` (`bossman pit passport-checkpoint`).
- Web: `GET /api/jeff/passport`, `POST /api/jeff/passport/consent` `{action}` (`passport_api.py:19-42`).
- Master Parser 2.0: `pit/master_parser/*`; owner API `features/jeff_master_parser.py` (`GET|POST /api/pit/master-parse`, `GET /api/pit/master-parse/report`); UI `ui/pages/jeff_passports.js`.

**Voice.**
- STT: faster-whisper, local (`speech.py:105`); confirmation below 0.55 confidence; `BOSSMAN_PIT_ASR_THREADS`.
- TTS: Piper by default; `BOSSMAN_PIT_TTS_EXECUTABLE` and `…_MODEL_PATH` are defaulted from `<data>/voice` (`jeff_desktop.py:127`).
- CosyVoice 3 candidate slot (`tts_engines.py`): `BOSSMAN_JEFF_TTS_ENGINE`, `BOSSMAN_JEFF_TTS_COSYVOICE=1`, `BOSSMAN_JEFF_COSYVOICE_CMD`, and a consent JSON `BOSSMAN_JEFF_COSYVOICE_CONSENT` whose items `weights_license_checked`, `voice_prompt_is_own_or_licensed` and `no_third_party_voice_imitation` must all be true.
- Chatterbox clone is owner-only in Telegram (`runtime.py:1181-1197`, `BOSSMAN_PIT_TTS_BACKEND=chatterbox`).
- Pre-TTS audit: `speech_audit.capture`.
- Web: `POST /api/jeff/voice/transcribe` (WAV body → `{text, confidence, needs_confirm, latency_ms}`); `POST /api/jeff/voice/speak` `{text}` → audio/ogg.

Gap: web `speak` calls `speech.synthesize` → `run_engines(..., allow_candidate=True)` by default (`speech.py:213`). Any web guest can therefore get the candidate or cloned engine for arbitrary client-supplied text. Telegram restricts this to `role=="owner"` (`runtime.py:1219`).

**Model routing.**
- `refresh_catalog` (`runtime.py:670-736`): a remote route is allowed only when it is listed in the live catalog at 0/0 price and its id ends with `:free` (`model_route.route_verdict`, `PRICE_RECHECK_SECONDS=120`, `PAYMENT_BLOCK_SECONDS=3600`).
- Liquid/LFM ban as code: regex `lfm|liquid/` (`model_policy.py:36`), applied in `config.load` via `split_banned` and in the catalog.
- The ≥10B planning policy (`MIN_MAIN_PARAMS_B`) is **recorded only** (`runtime.py:710-714`); it is not enforced for chat.
- `_mixed_route` (`:851-879`): a 70/30 split, local on `(turn*37+50)%100 < local_share_percent`; otherwise the fastest remote by `route_log` p95.
- Attempts: up to 3 remote, then a local fallback (`:1838-1847`).
- `ResilientChat` (`resilient_chat.py`): one unload+retry per scope on an empty answer.
- `LocalCapacityGuard` (`resources.py:227`): needs 8000 MB free unified memory on AMD or 2000 MB on NVIDIA; **an unmeasurable host gives local = False**.
- `CloudBudget` (`cloud_budget.py`): 200 requests/day, model cooldown 120 s, provider daily-limit pause until UTC midnight; shared by the Telegram and web processes.

**Heartbeat.**
- `pit/heartbeat.py`: `Heartbeat` snapshot; `write_file` every 15 s only in the Telegram `run()` (`runtime.py:1013`).
- **The web window never writes `pit-v1.7/web/heartbeat.json`.** The owner panel reads it (`features/jeff_settings.py:513`) and so always shows it as absent.
- `/api/jeff/health` builds a live snapshot and is **unauthenticated**. It includes `route.free_remote`, `route.local` and `llm.model` model ids (`runtime.py:1009-1010`), which contradicts the "model names never appear" contract at `web.py:17-18`. Tests currently pin this (`test_jeff_1_8_web_stream.py:139`).

**Telegram polling.** Only two `getUpdates` callers exist: Jeff's `_poll` (`runtime.py:1062-1089`) and the Пульт `service.poll` (`telegram_companion/service.py:1346`).
- One poller:
  - per data dir: `single_instance(home)` kernel byte-lock `poller.lock` (`store.py:361`);
  - per token: `token_poller_lock` in `%LOCALAPPDATA%/Bossman/telegram-pollers/<sha24>.lock`, or `BOSSMAN_TELEGRAM_POLLER_LOCK_DIR` (`bot_guard.py:33`);
  - `assert_not_companion_bot` refuses the Пульт token;
  - HTTP 409 becomes `CONFLICT` (`adapters.py:150`), which is re-raised and ends the process (`runtime.py:1085`);
  - a `web_only` config refuses to start or watch (`cli.py:507`, `560`).
- Offset: `state.offset` (encrypted, `synchronous=FULL`) is advanced inside the same transaction as `ingest` (`store.py:87-101`). `INSERT OR IGNORE` on `update_id` deduplicates. A full lane defers without advancing the offset (`runtime.py:516`, `1075`).
- Restart: `recover()` marks `processing→interrupted_unknown` and never replays blindly (`store.py:103`); media jobs have their own state machine (`runtime.py:425-507`).
- STOP: `stop.flag` is written by `cmd_stop` (`cli.py:591`) and seen by `_stop_watcher` every 0.1 s (`runtime.py:1029`), `_poll`, the worker (`:1164`) and voice. `run()` removes it in `finally`.
- Web STOP is separate: a per-user `threading.Event` plus task cancel (`web.py:621-631`).

**Restart / watchdog (Jeff 1.1).**
- `Watchdog` (`heartbeat.py:181-335`) through `bossman pit watch` (`cli.py:552`).
- `backoff_delay`: equal jitter, base 2 s, cap 300 s. Startup grace 90 s; restart when the heartbeat is stale for 60 s; `EXIT_FATAL=2` five times means give up; `EXIT_LOCK_HELD=3`.
- State file `pit-v1.7/watchdog/state.json`; single instance via `single_instance(home/watchdog)`.
- **The Jeff window has no watchdog** (the launcher only).

**Observability.** Under `pit-v1.7/logs/`:
- `route_log.jsonl`: model, provider, ok, latency, context_chars, tokens, finish, error, surface, local_gate, route_reason;
- `runtime_error.jsonl`, `delivery_log.jsonl`, `identity_guard.jsonl`, `cloud_budget.jsonl`, the speech audit, j2 audits.

Owner surfaces:
- `bossman pit doctor/status/routes`;
- `GET /api/jeff-settings/status`;
- `GET /api/jeff-insights/{overview,participants,narratives,narratives/{key},trends,digest}`, `POST /api/jeff-insights/digest/send`.

Gap: `provider_last_error` and `transport_error` are **sticky**. They are set on failure (`runtime.py:2066-2070`, `1083`) and never cleared on success. The panel also reads only the Telegram store `pit-v1.7/companion.sqlite3` (`features/jeff_settings.py:491`, `cli.py:457`), never the web store.

**Isolation and secrets.**
- Per-person HMAC namespaces.
- `secret_filter.redact_secrets` (4 regexes).
- `PrivateBlocklist` (`blocklist.py`): `BOSSMAN_PIT_BLOCKED_IDS_FILE`, fail-closed, beats the open allowlist.
- Credentials in `credentials.enc` (`bcc.secrets.Vault`); env `BOSSMAN_PIT_{BOT_TOKEN,PROVIDER_KEY,CORE_TOKEN,VISION_TOKEN}`.
- `guard_outgoing` (`runtime.py:917`) strips internal terms (model ids, URLs, keys).
- Web: Host/Origin guard, CSRF header `x-jeff-request: 1`, CSP (`web.py:353-371`).
- Owner isolation report: `participant_admin.isolation_report`.

**Jeff Admin Panel (Bossman Command v0.1 + Jeff Admin 1.9, merged via `d6fcb067`).**
- Backend `bcc/features/jeff_settings.py`:
  - `GET|PUT /api/jeff-settings`
  - `GET|PUT|DELETE /api/jeff-settings/users/{person_key}`
  - `POST /api/jeff-settings/reset`
  - `GET /api/jeff-settings/participants/{key}`
  - `PUT …/profile`
  - `POST …/clear` `{scope}`
  - `DELETE …/facts/{fact_id}`
  - `POST …/pause-memory|revoke {clear_data}|restore`
  - `GET /api/jeff-settings/status`
- Overlay file `pit-v1.7/jeff-settings.json` (`BOSSMAN_JEFF_SETTINGS`), re-read on mtime by `pit/jeff_settings.py`.
- UI: `ui/pages/jeff_settings.js`, `jeff_insights.js`, `jeff_passports.js`.
- Guide: `docs/owner/BOSSMAN_COMMAND_V01.md`.

---

## 4. The three open Jeff-window bugs

**Evidence.**
- The `jeffweb_findings.json` file is absent.
- The generator, `tools/ux_soak/jeff_web_blackbox.py`, sets up the window with `web-setup --local-model soak-fast --local-url http://127.0.0.1:<stub>/v1 --no-cloud` (`:163`). The stub is `tools/ux_soak/fake_model.py`: it echoes `soak-ok: <last user msg>` and records `last_messages`.
- The checks:
  - (b) is `"Алиса" in stub.last_messages` (`:181-187`);
  - (c) compares bubble counts before and after `p.reload()` (`:201-209`);
  - (a) stops the stub and later restarts it, then expects `soak-ok` (`:211-225`).
- The tested build per `docs/owner/CONTINUATION_PROMPT_20260929.md:35-43` was `rc19/t-jeff-admin @ 970dbe70`.

### Common trigger (explains all three findings, matches "25→6" exactly)

1. `cmd_web_setup` **still configures the `:free` cloud models and the OpenRouter base URL when `--no-cloud` is given**. Only the key is empty (`web.py:846-847`, `857-860`):
   ```python
   models = [m.strip() for m in ns.cloud_models.split(",") if m.strip()] or [
       m for m in DEFAULT_FREE_CHAT_MODELS if m.endswith(":free")]
   ```
   OpenRouter's `/models` needs no auth, so `refresh_catalog` verifies these routes as live 0/0 routes (`runtime.py:691-714`). Every chat call to them then fails with 401.
2. `_mixed_route` sends a simple turn to local only when `(turn*37+50)%100 < 30` (`runtime.py:864`). For turns 0..11 the values are 50, 87, **24**, 61, 98, 35, 72, **9**, 46, 83, **20**, 57, so **only turns 2, 7 and 10 go local**.
3. At `970dbe70` an ordinary cloud failure had **no local fallback**. Local was added only when cloud was blocked or rate-limited (see `git show 970dbe70:command-center/bcc/pit/runtime.py`, around line 1390). One busy capacity probe also **deleted local from the shared catalog permanently**, since the catalog was refreshed only while `catalog_checked_at == 0.0`.
4. A failed turn returns `PROVIDER_DOWN_RU` and **is never recorded** (`runtime.py:2063-2073`).

What this produces:
- **(c)** 1 greeting + 12 turns × 2 = 25 bubbles; 3 recorded pairs = 6 bubbles after F5.
- **(b)** turns 0 and 1 both failed on the cloud, so the stub was never called: `sent == "[]"`.
- **(a)** turns 12 and 13 give 94 and 31, both remote, so "Модель-провайдер недоступен" appeared both while the model was down and after it came back.

### (a) HIGH — "Модель-провайдер недоступен" persists after the provider returns

**Partly fixed in HEAD** by `b5402093` / `dc7f0a9c`, which are not ancestors of `970dbe70`:
- busy demotion is now per turn (`turn_catalog`, `runtime.py:1790-1798`);
- the local fallback is always appended after the cloud attempts (`:1840-1847`);
- a missing local model is looked for again after `LOCAL_RECHECK_SECONDS=120` (`:155`, `1776-1782`).

Tests: `tests/test_pit_rc19_audit_jeff.py:59-100`.

Causes that remain in HEAD:
1. **Local eviction during an outage lasts at least 120 s.** `_ensure_live_prices` (`:761-767`) calls `refresh_catalog`. If the local `list_model_info()` fails, local is silently dropped (`:731-733`, `except Exception: pass`) and `catalog_checked_at` and `prices_verified_at` are set to now (`:717`, `735`). After the model returns, both recheck gates (`:1776-1784`) stay closed for 120 s, so every turn in that window returns `PROVIDER_DOWN_RU`. The web window has no background refresh.
2. **Local discovery sits behind the remote catalog fetch.** `refresh_catalog` lists remote first (`:691-692`). If that raises (offline, proxy, provider down), `_fail_closed_remote` keeps only already-known local entries (`:744-747`). A local model that was evicted, or never discovered, **never comes back while the cloud catalog is unreachable**. This can be permanent.
3. **Sticky `provider_last_error`** (`:2066-2070`). It is never cleared on success, so the panel keeps showing the failure after recovery.
4. On a host where capacity cannot be measured, `LocalCapacityGuard` returns False (`resources.py:264-267`), so local is never used in mixed mode.

**Fix:**
- Split `refresh_catalog` into `_refresh_remote()` and `_refresh_local()`, run each in its own try block, and never drop a known local endpoint because of one failed list call. Track `local_checked_at` separately.
- After any turn that ends with `PROVIDER_DOWN_RU`, `NO_MODEL_RU` or `CLOUD_PAUSED_RU`, set `self._catalog_dirty = True` so the next turn refreshes immediately (with a 5–10 s floor instead of 120 s).
- In `refresh_catalog`, reject remote routes with reason `no_provider_key` when `not settings.provider_key`.
- `web-setup --no-cloud` should write `chat_models=[]` with `local_chat_only=True`. `save_setup` needs a `local_chat_only` parameter because `PITSettings` requires it when `chat_models` is empty (`config.py:138`).
- On success, `store.put("provider_last_error", None)`. `_jeff_status_sync` should also read `pit-v1.7/web/companion.sqlite3`.

### (b) MEDIUM — previous turn not sent to the model

**Not fixed.** Causes in HEAD, all deterministic:
1. **Cloud turns never receive history** unless `remote_personalization_enabled`, the "cloud context" checkbox. That flag defaults to False (`models.py:26`), and `_welcome_if_first_contact` never turns it on (`runtime.py:1458-1466`). The gate is at `runtime.py:1926-1927`.
   - This is a documented owner privacy decision: `jeff.html:53` says "без этого облако получает только текущее сообщение".
   - It is pinned by tests: `test_pit_runtime.py:478`, `test_jeff_privacy_in_code.py`.
   - With the 70/30 mix, roughly 70% of simple turns get no conversational context.
2. **Local turns get only one previous pair** (`history[-2:]`, `:1930`).
3. **Failed, j2-early and command turns are never recorded** (`:2063-2073`, `1752-1754`). The "previous turn" is missing whenever it errored.
4. Pausing memory also removes all in-session context (`memory_context_allowed`, `:1885-1887`).

**Fix (needs an owner decision):** separate **session context** from **durable memory and personalization**. Send the last N turns of the current session (for example ≤6 pairs, ≤30 min, ≤6000 chars) whenever `remote_processing_enabled` is on. Keep facts and persona gated by `remote_personalization_enabled`. Raise the local window to 3 pairs within a character budget. Update the tests above to assert "no durable memory to remote" instead of "no history to remote".

### (c) MEDIUM — history shrinks after F5 (25 → 6)

**Not fixed.** Causes:
1. `GET /api/jeff/history` (`web.py:553-559`) returns `rt.store.history()`, which is the **model context window** (16 pairs, 16000 chars; `runtime.py:418-419`, `539-552`), not a transcript. With real 2–4k-character answers only about 3–4 pairs fit.
2. Failure replies, guard replies, command replies (`/start`, `/privacy`, …) and j2 early replies are never stored in `history`.
3. `jeff.js` `loadHistory` clears `#chat` and redraws from that endpoint (`jeff.js:149-155`). This happens on F5 and also on every reconnect (`setOnline` → `reloadAfterReconnect`, `jeff.js:81`, `487-490`), so a server restart also shrinks the visible chat.

**Fix:**
- Add a `transcript(id, who, role, text, kind, created)` table to `PITStore`, capped at about 500 rows per `who`.
- In `web.run_turn` (`web.py:419-470`), append the user message and the displayed reply, including errors and `stopped`, when memory is enabled.
- `/api/jeff/history?limit=` should read the transcript. `store.history()` stays for model context.
- `forget` and the admin clear paths (`store.forget`, `participant_admin.forget_chat_history`) must delete transcript rows too.
- Optionally: in `reloadAfterReconnect`, merge rather than wipe.

---

## 5. Baseline measurement infrastructure (what exists)

- **Quality:** `j2/quality_lab.py` scores every turn in `post_reply` with deterministic dimensions (helpfulness, honesty, tone, brevity, refusal_correctness, latency), stored in `<person>/quality/scores.jsonl`. The local judge is opt-in. `run_comparison` (1.0 vs 2.0 on fakes) and CLI `main` are at `:993`. It **does not score j2 early replies**; its pre_route is skipped when an earlier module answers.
- **Behaviour:** `behavior_scores.py` keeps a per-person engagement and profile-stability ledger (0–100) that feeds `memory_confidence_floor`.
- **Latency:**
  - `latency.py`: `LatencyStats` p50/p95 and `ReplyMetrics` (TTFT and total per route), served in the heartbeat as `reply_latency`;
  - speech STT/TTS latency (`speech.latency_snapshot`);
  - `route_log.jsonl` latency, tokens and context_chars per attempt, also used by the router's p95.
- **Voice:** `python -m bcc.pit.voice_bench [--out]` measures latency per installed engine and WER through local STT.
- **Reliability:** heartbeat `replies_ok/failed`, `last_error`, poll state; watchdog `restarts`/`delays`; `runtime_error.jsonl`; j2 pipeline stats (ok/timeout/error/skipped, breaker).
- **Learning / cost:** `learning_counters.py` (per-build before/after, `MIN_SAMPLES=5`) is **used only by long tasks** (`tasks.py:171`, `288`), not by chat. Cost is request counts only (`cloud_budget.json`); `_max_cost_usd` is 0.
- **Memory:** vault audit (`memory_audit`), memory_palace counters. There is **no recall/precision benchmark**.
- **Black-box:** `tools/ux_soak/jeff_web_blackbox.py` plus `fake_model.py` (context, long use, reload, model down/up, server restart, voice, owner-endpoint probes); `tools/ux_soak/jeff_blackbox.py`.
- **Missing:**
  - no chat-level eval corpus with success/latency/cost per build;
  - no memory-recall eval;
  - no F5-persistence or context-carry regression tests on the web surface;
  - no measurement of time to recover after a provider outage.

---

## 6. Prioritized fix list

1. **P0 (a):** `runtime.py:670-736` — decouple local and remote catalog refresh; never evict local on one failed list; add `local_checked_at`; set a dirty flag after failed turns (targets `:1773-1784`, `2063-2073`). Test: local down, then up, recovers within 10 s; remote catalog unreachable and local fine gives local answers.
2. **P0 (a/b/c trigger):** `web.py:842-860` — `--no-cloud` saves `local_chat_only=True` and `chat_models=[]` (extend `config.save_setup`). Plus the `refresh_catalog` guard `no_provider_key` at `runtime.py:696-714`.
3. **P1 (c):** a transcript table in `PITStore` (`runtime.py:411`); write it in `web.run_turn` (`web.py:419-470`); read it in `web.py:553-559`; clean it in `store.forget` / `participant_admin`. Test: 25 bubbles, F5, still 25.
4. **P1 (b):** a session-context window for remote routes at `runtime.py:1926-1930` (owner decision; update `test_pit_runtime.py:478` and `test_jeff_privacy_in_code.py`). Also record failure and early replies as transcript-only, not context.
5. **P1 observability:** clear `provider_last_error` on success (`runtime.py:2028-2037`); the panel reads the web store (`features/jeff_settings.py:491`); the web runtime writes `web/heartbeat.json` every 15 s and refreshes the catalog from `web.py` `_startup` (`:345`).
6. **P2 security and contract:**
   - `web.py:495-502`: remove `route` and `llm` model ids from the unauthenticated `/api/jeff/health`;
   - `web.py:759`: pass `allow_candidate=False` to TTS for web guests;
   - apply `guard_outgoing` in `web.run_turn` to every reply (commands, j2 early replies, uploads).
7. **P2 Jeff 2.0 wiring:**
   - pass `served_by` into the j2 ctx for `post_reply` (`runtime.py:1757`);
   - use `model_guard.routing_hint()` in `_mixed_route`;
   - provide `j2_escalate` and `j2_embed`;
   - run `j2.start()` in the web runtime;
   - record j2 early replies in the transcript.
8. **P3 quality/latency:** relax the local "incomplete" heuristic (`runtime.py:1993-1996`); remove `"typesafe/jev-1.5"` from `DEFAULT_FREE_CHAT_MODELS` (`config.py:27`); bump `JEFF_VERSION`; wire `learning_counters.record` for chat turns (ok, latency, route) to get a per-build baseline.