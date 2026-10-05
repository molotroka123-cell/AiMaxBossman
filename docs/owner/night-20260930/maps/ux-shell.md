# Bossman desktop chat UX: map of the existing UI, APIs, streaming and pinned tests

Every endpoint below was checked by reading the code at HEAD `8cfa0481`. Both streaming branches are fully merged into HEAD: `git log HEAD..origin/a19/streaming` and `HEAD..origin/a19/runtime2` are empty. Nothing was run.

**Summary.**
- **Works today:** sending a message, live answer streaming, STOP, the model picker with Auto, microphone transcription, memory, tools, Agentic Rave and the thinking panel. All of these can be bound to real endpoints now.
- **Not backed by any endpoint:**
  - chat threads (create, rename, delete, pin, list);
  - projects;
  - a general attachment upload;
  - per-message model override;
  - a plan for a plain task;
  - `Last-Event-ID` reconnect.
- **Two things will break tests if changed:** the default theme (tests require `light`) and the landing page (tests require `home-v3`).

---

## 1. UI architecture

### `command-center/ui/index.html` (130 lines)
- `<html lang="ru" data-theme="light">`. The `<title>` contains "BOSSMAN".
- Stylesheets load in order: `style.css`, `theme.css`, `desktop.css`, `pages/video_studio.css`.
- `<script src="intro.js">` is the first script in `<body>`. `<script type="module" src="app.js">` is last.
- **Static ids:**
  - Login: `login`, `login-form`, `login-token` (type=text, masked), `login-hint`, `login-error`, `login-submit`.
  - Shell: `shell`, `desktop-skip`, `ui-release`, `build-sha`, `nav`, `palette-open`, `palette-kbd`, `conn`, `conn-dot`, `conn-text`, `mobile-menu`, `page-title`, `topbar-stats`, `think-open` (`aria-pressed`), `theme-toggle`, `refresh-btn`, `view` (`<main class="view" tabindex=-1>`), `mobilenav`.
  - Connection banner: `stale-banner`, `stale-text`, `stale-retry`, `stale-now`.
  - Outside `.shell` (on purpose, see BL-027 note at lines 104-110): `desktop-dock`, `scrim`, `modal-root`, `toast-root`, `palette`, `palette-input`, `palette-list`.

### `app.js` (900 lines): the shell
- **Page registry.**
  - `PAGES` from `pages.js` (old pages: home, models, agents, tasks, schedules, approvals, system, settings) plus `FEATURE_PAGES` from `pages/index.js` (lines 12-20). `PAGE_BY_ID` is a Map.
  - Landing (lines 62-64): `LANDING=['home-v3','overview','home']`, so `DEFAULT_PAGE='home-v3'`. The fallback landings are hidden from the menu (`SUPERSEDED`).
  - Sidebar sections (lines 75-96): `SECTIONS = main, work, studio, apps, brains, system`. Ordering comes from `MAIN_ORDER`, `SECTION_ORDER` and `MOBILE_ORDER`. `NAV_DUPLICATES` hides `command` from the sidebar.
- **Routing.**
  - Hash format is `#/<id>?k=v`. `parseHash` is at line 206, `navigate(id, params)` at line 220.
  - `onRoute` sets `#page-title` to `page.title` and `document.title` to `` `${title} · BOSSMAN` ``.
  - `renderPage` (lines 245-293): awaits `page.enter?`, shows a skeleton `loading(3)` only when the page changes, then awaits `page.render(ctx, params)`, then `retainAppFrame` or `replace`, then sets `#view.dataset.rendered = id`.
  - On `ApiError.isAuth` it calls `showLogin`. On any other error it shows an error panel with a button labelled "Повторить".
- **Automatic refresh.** `isTypingInView` and `pendingRefresh` stop auto re-renders from destroying typed input. A 2.5 s interval retries the deferred refresh.
- **Page context.** `ctx = {state, bus, navigate, refresh, scheduleRefresh, setBadge, getTheme, setTheme, logout, hasSession}`.
- **Theme.** Stored in `localStorage['bcc.theme']`. `preferredTheme()` in `desktop.js` returns `'dark'` only if the saved value is `'dark'`, otherwise `'light'`. `setTheme` also updates `meta[name=theme-color]`.
- **Event bus.** `bus = new EventStream()` (WebSocket).
  - `ws.*` events drive the connection dot and the stale banner.
  - `approval.*` updates the approvals badge.
  - `system.metrics` updates the top bar stats.
  - `page.onEvent(ev)` returning true triggers a debounced 350 ms refresh.
  - On reconnect, `onConnRestored` shows the toast "Соединение восстановлено … Данные обновлены."
- **Command palette.** Ctrl/⌘K. Actions include "Процесс работы", a new-task modal and "Остановить все активные" (`stopAllRunning` in `pages.js:1337`).
- **Login.** `api.login(token)` stores the CSRF token, then `boot()` runs: `api.system()` → `showShell` → `bus.start()` → `onRoute` → `mountTestingPeriod` → `mountCommandBar` → `showBuildIdentity` (`/api/identity`) → `loadCounts` (`/api/models`, `/api/agents`) → `loadTopStats` every 30 s.
- **Globals.** `window.__bxThinking`, `__bxConn={bus,label}`, `__bxPages`, `__bxSections`, `__bxTiming`.
- **Forced logout.** The `bcc:unauthorized` window event leads to `showLogin`.

### `api.js` (432 lines)
- **Auth.** An HttpOnly cookie plus a CSRF token in `localStorage['bcc.csrf']`, sent as header `X-BCC-CSRF` on POST, PUT, PATCH and DELETE. Requests use `credentials:'same-origin'` and `cache:'no-store'`. A Blob or FormData body is sent raw.
- **Errors** become `ApiError{status, hint, actions, path, code, detail}`.
  - `isAuth` means 401, or 403 with `code=='csrf'`.
  - `isOffline` means status 0.
  - The server error body is `{error:{message,hint?,code?}}`.
  - A 401 clears in-flight state and dispatches `bcc:unauthorized`. A 403 csrf also clears the stored CSRF token.
- **Concurrent GET merging:** identical GETs issued at the same time share one request (`inflight` map).
- **Gap:** `api.raw(path, {method, body})` has no `signal` parameter, so a request cannot be aborted through it.
- **Wrappers and the endpoints they call:**
  - `raw`; `login` (POST /api/login); `logout` (POST /api/logout).
  - `system` (GET /api/system); `identity` (/api/identity); `loginHint` (/api/login-hint); `cacheEconomics`; `cacheIntelligence`.
  - Providers: `providerKinds`, `providers`, `createProvider`, `deleteProvider`, `freeProviders`, `connectFreeProvider` (POST /api/free-providers/{name}/connect).
  - Models: `models` (GET /api/models), `createModel`, `updateModel` (PATCH), `deleteModel`, `checkModel` (POST …/check), `discoverModels` (POST /api/models/discover), `testModel` (POST …/test).
  - Agents: `agents`, `createAgent`, `updateAgent`, `deleteAgent`.
  - Tasks: `tasks(status)` (GET /api/tasks?status=), `createTask` (POST /api/tasks), `preflightTask` (POST /api/tasks/preflight), `task(id)`, `taskAction(id, action)` (POST /api/tasks/{id}/{action}).
  - Global STOP: `activeOwnerWork` (GET /api/control-plane/active), `stopAllOwnerWork` (POST /api/control-plane/stop-all).
  - Runs: `run(id)`, `runEvents(id, after)` (GET /api/runs/{id}/events?after=).
  - Schedules: `schedules` plus create, update, delete.
  - Approvals: `approvals(status)`, `decideApproval(id, approve, by)` (POST /api/approvals/{id}).
  - `activity` (GET /api/activity).
- **`EventStream`** (lines 289-432): WebSocket to `/api/events`, authenticated by the cookie.
  - Backoff is `min(1000·1.6^(n-1), 15000)` ±15%.
  - It reconnects on `visibilitychange` and `online`.
  - It emits `ws.open{reconnected, downtime_ms}`, `ws.closed`, `ws.connecting` and `ws.retry_scheduled{delay_ms, at}`.
  - `reconnectNow()` returns a bool. It exposes `attempt`, `nextRetryAt`, `lastOpenAt` and `disconnectedAt`.

### `thinking.js` (299 lines): the "Процесс работы" panel
- Appends `<aside id="think-pane" class="bx-think">` to `<body>`. Its buttons are `#think-clear` and `#think-close`.
- Open/closed state is stored in `localStorage['bx.think.open']`. The toggles are `#think-open` and Ctrl+. When open, `body` gets the class `bx-think-open`.
- **Two sections:**
  - "Сейчас": run cards `.bx-think-card[data-run][data-state]`. Each card has a grid of elapsed time, step (X из N), model, tool, waiting, retries and errors, plus a live answer tail `.bx-think-live[data-live]` (the last 600 characters of `run.answer_delta`).
  - "Лента": up to 80 rows `.bx-think-row[data-kind]`. The event buffer holds at most 200.
- **Sources.**
  - The WebSocket bus, handling `task.*`, `tool.called`, `tool.denied`, `router.fallback`, `model.status`, `run.log`, `approval.created`, `evaluation.completed`, `checkpoint.created`, `hook.*`, `worker.error`, `cache.observation`, and `run.answer_delta` / `run.answer_reset` (lines 189-198: appended to the card, never written to the feed).
  - On open, a seed from `api.tasks('running,queued,paused')` plus a one-time `api.activity()`.
- Duplicates are removed with a fingerprint of `kind|ts|run_id|task_id|step|tool|message|attempt|idx`. Renders are batched to one per animation frame.
- There is no plan and no sources view.
- **Gap:** `task.completed` and `task.failed` do not change a card's state; only `evaluation.completed` with PASS marks it completed.
- The API is `{open, close, isOpen, runs, events, stats()}`.

### `commandbar.js`
- Mounts `section#cmdbar` into `<body>` with ids `cmdbar-input`, `cmdbar-parse`, `cmdbar-form`, `cmdbar-hints`, `cmdbar-note`, `cmdbar-intent`, `cmdbar-tasks`, `cmdbar-summary`, `cmdbar-capability`, `cmdbar-args`, `cmdbar-reversible`, `cmdbar-confirm`, `cmdbar-run`.
- Endpoints: GET /api/command-bar, POST /api/command-bar/parse `{text}`, POST /api/command-bar/run `{intent_id, confirm}`, GET /api/command-bar/tasks (polled every 2 s), POST /api/command-bar/tasks/{id}/stop.
- It hides itself unless `BOSSMAN_COMMAND_BAR_ENABLED=1`. When visible it is fixed at the bottom centre (`z-index:60`), in the same place a bottom composer would go.

### `desktop.js`, `desktop.css`, `theme.css`
- `desktopPages()` builds the dock from `[landing, mission_console, apps, video-studio, web_designer, control]`.
- `desktop.css`:
  - Dark tokens `--desktop-surface:#151c2c`, `--desktop-glass`, `--desktop-wallpaper`, with light overrides.
  - At widths of 901 px and up, `.shell` is an inset rounded card and the `.desktop-dock` is fixed at bottom 14 px with `z-index:45`. `.view` gets `padding-bottom:108px` so the dock does not cover the last controls.
  - There are `prefers-reduced-motion`, `prefers-reduced-transparency` and `forced-colors` blocks. The reduced-motion block kills all animations and transitions.
- `theme.css` `:root` (dark by default):
  - Surfaces and ink: `--bx-bg:#06070a`, `--bx-bg-2`, `--bx-surface`, `-2`, `-3`, `--bx-hairline`, `-2`, `--bx-ink:#f2f5fa`, `--bx-ink-2`, `-3`.
  - Accents: `--bx-azure:#3d8bff`, `--bx-violet`, `--bx-ember`, `--bx-mint`, `--bx-amber`, `--bx-rose`, `--bx-accent`.
  - Fonts: `--bx-font`, `--bx-mono`.
  - Type roles: `--bx-hero`, `--bx-h1`, `--bx-h2`, `--bx-body:13.5px`, `--bx-caption:12px`.
  - Spacing `--bx-1…9`, radii `--bx-r-sm…full`, `--bx-shadow*`, `--bx-glow`, `--bx-ease`, `--bx-t`.
  - `[data-theme="light"]` overrides surfaces and ink. The thinking panel styles are at lines 915-937.
- `style.css` holds the older token set (`--bg`, `--panel`, `--line`, `--fg`, `--accent`, `--ok`, `--warn`, `--err`, …) with light overrides.
- **Packaging:** only `.html .js .css .svg .png .ico .json .webm` files are shipped (`setup.py:37`, `MANIFEST.in`). That means no web font files. The icon set is small (`components.js ICONS`); there are no mic, attach or send glyphs, so those need inline SVG.

### How a page is registered
- Add exactly one `lazyPage({id, title, icon, nav:'primary'|'more', section, sweep?}, () => import('./x.js'), m => m.default)` entry to `pages/index.js`.
- The module must `export default { id, title, icon, nav, section, render(ctx, params), onEvent(ev, ctx) }`, and its static fields must match the manifest exactly.
- `section` must be one of the six sections.
- Helpers: `pages/_shared.js` (`panel`, `pageHead`, `errorBanner`, `notAvailable`, …) and `pages/_ui.js` (`btn` sets `is-loading` and `aria-busy`; `pill`; `statusText`; `errorNote`; …).

### Existing chat-like pages
None of them streams tokens.

| Page | Where | What it does |
|---|---|---|
| `home-v3` (landing) | `pages/home.js` | Growing textarea `.bx-command-input` and a file input from `video_chat.attachmentInput()`. Agent picker plus mode toggles ("Умно", "Авто", "С агентами"). A live "Выполнит: …" line from POST /api/tasks/preflight (debounced 500 ms). Ctrl+Enter or ЗАПУСТИТЬ first tries `routeVideoRequest` (POST /api/video-studio/chat), then POST /api/tasks with `run_now`. |
| `bossman-chat` | `video_chat.js:45 ChatPage` ("История видео и чат") | Lists GET /api/video-studio/chat messages and the related tasks. Plain questions go to POST /api/tasks with a chosen agent. It is specific to video. |
| `mission_console` | `pages/mission_console.js` | Feed, right-hand rail and a command form (`#mc-command-agent`, `#mc-command-input`) that POSTs /api/tasks. Its "thinking" is run events (GET /api/runs/{id}/events). |
| `terminal` | `pages/terminal.js` | Shell commands only (/api/terminal/*). It is not a chat. |

**Closest real chat:** the CLI `bossman chat` in `bcc/terminal_cli/chat.py`, `ops.py` and `follow.py`. The web chat should copy its protocol (section 2A).

---

## 2. Backend endpoints for chat

Everything is under `/api`. It requires a session cookie (with CSRF on unsafe methods) or `X-BCC-Token` in legacy mode. Feature routers are mounted with the same dependency (`api.py:488-491`).

### A. Send and run: the canonical chat turn (same as the terminal)
1. **Pick the executor.** `POST /api/tasks/preflight` (`api.py:1037`) with body `TaskIn`. It writes nothing. It returns `{ok, mode:'auto'|'explicit', agent:{id,name}, model:{id,name,health}|null, reason, hint, code}`.
2. **Create the task.** `POST /api/tasks` (`api.py:1077`), body `TaskIn` (`api.py:416`): `{prompt:str, title='', agent_id:int|null, run_now=true, priority=5, max_retries=2, schedule?, client_request_id?:8-128 chars [A-Za-z0-9._:-]}`.
   - It returns `{task, schedule, replayed?}`. The same `client_request_id` returns the same task.
   - There are no `meta`, `model` or `attachments` fields.
   - If `run_now=false` and `agent_id=null`, the draft keeps a null agent and `/run` then blocks it (`engine.py:276-306`). **Auto must preflight first** and pass the agent id it returns.
3. **Start it.** `POST /api/tasks/{id}/run` (`api.py:1156`). It returns `{ok:true, status:'queued', run_id}` or `{ok:false, status:'blocked', run_id:null, code:'BLOCKED_CAPABILITY_UNAVAILABLE', reason}`.
4. **Follow it.** `GET /api/events/stream?task_id=X&after=0` (SSE, see section 3).
5. **Final answer.** `GET /api/tasks/{id}` (`api.py:1142`) returns `{task, runs:[run public: status, result, error, model_alias, tokens_in, tokens_out, cost_usd, route, checkpoint:{step, note, messages}, provenance], result, error}`.

**Conversation context** (`bcc/conversation_context.py`). The client puts the previous turns inside the prompt. It must use this exact format, otherwise the backend's action classifiers (`action_contract.py:76`, `action_router.py:52`) read earlier requests as new ones:
- `HEADER="Контекст беседы (предыдущие ходы этой сессии терминала):\n"`
- `MARKER="\n\nНовое сообщение владельца:\n"`
- Each part is `"Владелец: {text}\nBossman: {answer}"`, and parts are joined with `\n\n`.
- The CLI uses 3 turns (`CONTEXT_TURNS=3`), cuts each side to 1500 characters (`CONTEXT_CHARS=1500`) and limits the prompt to 64000 characters on the client (`MAX_PROMPT_CHARS=64000`).

### B. History, threads and search
| Need | Existing API | Status |
|---|---|---|
| List past turns | `GET /api/tasks?status=&limit≤500&mission_id=&before_id=` (desc, each row has `last_run`) | Works. The unit is one task per turn; there is no thread entity. |
| Messages of one chat | `GET /api/tasks/{id}` (prompt and result); `GET /api/tasks/{id}/events?after=&limit≤2000` returns `{task_id, status, events[with seq], cursor, more}` | Works per task. |
| Chat sessions | CLI sessions are JSON files in `data_dir/terminal/sessions/*.json`, with **no HTTP API**. `bcc/sessions.py` holds auth sessions, not chats. | **Missing** |
| Rename, delete or pin a chat | There is no PATCH or DELETE for tasks. | **Missing** |
| Search | `GET /api/search?q=(2..200 chars)&limit=20&total=100` (`unified_search.py:228`) searches events, tasks, task_runs, run_events, approvals and missions with provenance. **Off unless `BOSSMAN_UNIFIED_SEARCH_ENABLED=1`**; otherwise it returns `{enabled:false}`. | Works behind a flag |
| Projects | Only video-studio and web-designer projects exist. Missions (POST /api/missions) create their own plan tasks and are not suitable. Memory recall reads `task.meta.project_id` (`lifecycle_wiring.py:220`, default `"bossman"`), which `TaskIn` cannot set. | **Missing** |

### C. STOP and control
- **Per turn:** `POST /api/tasks/{id}/stop` (`engine.stop`, `engine.py:365`).
  - The stop is sticky. It revokes the task's approval leases, rejects approvals still waiting, emits `task.stopped` and cancels the worker, which aborts the provider stream (tested in `test_answer_streaming.py:167`).
  - It returns `{ok, status:'stopped'}`. It returns 409 `TASK_STATE_CONFLICT` if the task is already completed or failed.
  - Also available: `/pause`, `/resume`, `/retry` (a new attempt, only if idle; returns the same admission shape).
- **Global:** `GET /api/control-plane/active` for a preview, then `POST /api/control-plane/stop-all` returns `{ok, stopped, requested, remaining, errors, provider_outcome_unknown}`. The UI flow already exists in `pages.js:1337 stopAllRunning`.
- **Rave:** `POST /api/rave/{rid}/stop {agent?}`, `POST /api/rave/stop-all`.

### D. Model and agent picker
- `GET /api/agents` returns agent rows `{id, name, role, system_prompt, model_id, fallback_model_id, tools[], max_steps, max_tokens, budget_usd, permissions, enabled, workspace, created_at}`.
- `GET /api/models` returns `{id, provider_id, name, alias, kind:'local'|'cloud', context_window, caps, price_in, price_out, pricing_known, status, status_detail, health, bench}`.
- `GET /api/providers` returns `{id, name, kind:'openai_compat'|'anthropic', base_url}`.
- **Local vs cloud label:** use `model.kind`, or derive it from the provider's `base_url` being a loopback address (`terminal_cli/ops.py:24 locality()`).
- **"Subscription" label:** exists only for Rave connectors. `GET /api/rave/connectors` returns claude and codex CLI login state. Chat tasks cannot run on those subscriptions.
- **Auto:** `agent_id:null` goes to `select_executor` (`task_admission.py:46`), which ranks enabled agents by model health and refuses cloud models with unknown prices. The preflight response shows `mode:'auto'`.
- **Explicit model choice** means choosing an agent (whose `model_id` is fixed). There is **no per-message model override** in `TaskIn`. `force_model_id` exists only through the fork endpoint `POST /api/runs/{run_id}/fork` (`forks.py:65`).
- Router explanation: `POST /api/router/preview`, `GET /api/router/explain?task_id=`, `GET/PATCH /api/router/rules`.
- **Only the `openai_compat` adapter streams** (`providers.py:285`, `_chat_streamed` at line 337). `AnthropicAdapter` ignores `on_delta`, so its answers arrive whole.

### E. Attachments
- **There is no general chat attachment upload.** What exists:
  - `POST /api/video-studio/media?project_id&filename&expected_revision&operation_id` with a raw body, up to 8 GiB, tied to a video project (`video_studio/service.py:114`).
  - `POST /api/images/assets/import` with base64 PNG, JPG, WEBP, GIF or SVG, up to 15 MB, tied to a collection.
  - `POST /api/file-intelligence/extract {path}`, which reads a server path. It needs the File Intelligence flag and is not an upload.
- The home page's "+" sends media to video only (`video_chat.js:36`, accepting `video/*, audio/*, image/*, .srt, .vtt`).

### F. Microphone and speech-to-text
- `POST /api/oss/speech/transcribe?language=auto` (`oss_integrations.py:67`).
  - The body is **raw PCM16 WAV only**, 8-48 kHz, at most 32 MiB and 600 s. It uses local faster-whisper on CPU with int8 and has no cloud fallback.
  - It returns `{ok, provider, text, duration_seconds, …}`, or 413, 422 or 400 on bad input.
- Availability: `GET /api/oss/status` returns `.speech.status` = `'configured'` or `'unavailable'`, plus `reason`. Model path: `BOSSMAN_WHISPER_MODEL_PATH`; the package comes from the `[speech]` extra.
- Browser code to convert a recording to 16 kHz mono WAV already exists in `jeff.js:313 blobToWav16k`. The Jeff endpoint `/api/jeff/voice/transcribe` belongs to the separate `bossman pit web` server and **cannot be called from Command Center**.
- The WAV blob can be sent with `api.raw(path, {method:'POST', body: blob})`; Blob bodies pass straight through.

### G. Memory, tools and context
- **Memory:**
  - `GET/POST /api/memory/config` (`{configured, root, backend, …}`).
  - `GET /api/memory/stats`.
  - `POST /api/memory/search {query, candidate_k, rerank_k, max_context_tokens}` returns `{query, estimated_tokens, items:[{source, heading, score, chunk_hash, content}]}`.
  - `/expand`, `/write`, `/index`.
  - Facts: `GET/POST /api/memory/facts`, plus `/as-of` and `/history`.
  - Returns 503 with a hint if no vault is configured.
  - Automatic recall runs at task start. It logs `memory.recalled` with `data.sources`, or `memory.recall_skipped`. It can be switched off with `BCC_MEMORY_RECALL=0`.
- **Tools and skills:**
  - `GET /api/capabilities` returns `{capabilities:[…grant], probes}`.
  - Also `GET /api/skills`, `GET /api/mcp/tools` and `GET /api/mcp/servers`.
  - The tools the chosen agent actually has are in `agent.tools`.
- **Context and tokens:**
  - The `run.usage` event (SSE and task history, not the WebSocket) carries `{step_tokens_in, step_tokens_out, tokens_in, tokens_out, cost_usd, pricing_known, context_window, max_tokens_total, max_cost_usd, usage_reported}`.
  - Per run: `task_runs.tokens_in`, `tokens_out`, `cost_usd`.
  - `GET /api/spend` gives daily and per-model spend.
  - **There is no endpoint for "context window fill".** It has to be computed from `run.usage` against `model.context_window`.

### H. Agentic Rave
- `POST /api/rave {prompt(1..8000), agents:["mock:a,local:qwen,claude:c,codex:x"], repo?, allow?, test?}`.
  - If no repo is given, a scratch repository is created. Bus events are `rave.<kind>` with `rave_id`.
- Also: `GET /api/rave`, `GET /api/rave/{rid}`, `GET /api/rave/{rid}/events?after=`, pause, resume and stop, `/agents/{name}/diff`, `/conflicts`, and `POST /agents/{name}/apply {approval_id}`. The UI page is `pages/rave.js`.

### I. Data for the thinking panel (plan, actions, sources)
- **Actions:**
  - `run.tool_use {call_id, tool, source, args(redacted)}` and `run.tool_result {call_id, tool, ok, duration_ms, summary, preview, truncated}` arrive over SSE and task history only.
  - `tool.called` and `tool.denied` also arrive over the WebSocket.
  - `task.progress {step, max_steps, model, tool_calls, waiting_approval, tool}`.
  - `router.fallback`.
  - `approval.created`; decisions go to `POST /api/approvals/{id} {approve, by, lease?}`.
- **Sources:** `GET /api/runs/{run_id}/events?after=` returns `run_events` rows `{id, ts, level, kind, message, data}` (for example `memory.recalled` with `data.sources`). Also `GET /api/router/explain?task_id=`, `GET /api/observability/trace/{trace_id}` and `GET /api/provenance`.
- **Plan:** there is none for a plain task. Plans exist only for missions (`GET /api/missions/{id}` plan) and workflows (`/api/workflow/missions/{id}`). **This is a gap.**
- **Do not show:** `run.reasoning_delta`. It is the provider's reasoning; it is not stored, but it **is delivered live over SSE**. The client must drop it to honour the "no hidden chain of thought" rule.

---

## 3. Streaming (a19/streaming 18eab129 and a19/runtime2 b4f43539, both in HEAD)

**Producer side**
- The `openai_compat` adapter sends `stream:true, stream_options.include_usage` and folds the response with `streaming.read_chat_stream` (`streaming.py:561`).
  - `<think>` blocks are removed from the visible text; reasoning goes into a separate field.
  - If a failure happens **after** the first token, it calls `on_delta(None)` and raises `ProviderError` so the step is retried. If streaming is refused or no output arrives, it falls back to a single whole answer.
- `engine._AnswerStream` (`engine.py:2787`, created at line 1055) emits:
  - **`run.answer_delta` `{task_id, run_id, step, attempt, idx, text}`**: the first piece immediately, then batched to about 10 per second (0.1 s interval or 200 characters buffered).
  - **`run.answer_reset` `{task_id, run_id, step, attempt}`**: discard what was shown. `attempt` then increments and `idx` restarts at 0.
- At the end of each step comes **`run.assistant_message` `{step, model, text(≤16000), chars, streamed}`**. If `streamed` is true, the text was already shown live.
- Completion events: `task.completed`, `task.failed{error}`, `task.stopped`, `task.blocked{code, reason}`.

**Channels**
- **WebSocket `/api/events`** (`api.py:728`) sends everything except stream-only kinds, but lets `WEB_LIVE = {run.answer_delta, run.answer_reset}` through (`events.py:27-37`). Live messages carry `seq`. There is no replay.
  - **Consumer today: only the thinking panel's run-card tail** (`thinking.js:189-198`). No other pane uses it. "CLI + web pane" in the commit title means `terminal_cli/human.py` and `thinking.js`.
- **SSE `GET /api/events/stream?task_id=|run_id=&after=`** (`api.py:1216`):
  - Frames are `id: <seq>\ndata: <json>\n\n`. The first frame is `stream.open{task_id, run_id, after}`.
  - If both `task_id` and `after` are given, stored history is replayed in pages of 500, followed by `stream.replayed{cursor}`, then live events with anything already replayed skipped (`seq ≤ last`).
  - A keepalive comment `: keepalive` is sent every 15 s (`STREAM_KEEPALIVE_S`).
  - If the client falls behind, the queue (500) drops it and the server sends `stream.lagged{cursor}` and closes.
  - The stream ends when the client disconnects, with no leaked subscription (tested in `test_events_stream_reconnect.py`).
- **Answer deltas are stored** (not in `TRANSIENT`), so they replay. Reasoning deltas are live only.

**Reconnect caveats**
- The server **ignores the `Last-Event-ID` header**. It only reads the `after` query parameter.
- A browser `EventSource` reconnects automatically to the same URL. With the original `after` that means duplicates; without `after` it means no replay at all.
- Recommended client logic, as in `terminal_cli/api_client.py:398 EventPump`:
  - Keep the last `seq`.
  - On `error` or `stream.lagged`, close and reopen with `after=lastSeq`.
  - Drop anything with `seq ≤ lastSeq`.
  - Always pass `after=0` on the first open so a chat reopened mid-run gets its history.
  - Use GET /api/tasks/{id} as the final truth.
- **Rendering rules:** append `text` per (run_id, step, attempt) in `idx` order. On `answer_reset`, clear that step's text. On `assistant_message` with `streamed`, keep the live text; without it, render `text`.

---

## 4. Auth, CSRF and error surfacing
- `POST /api/login {token, label}` (`api.py:694`, rate-limited by `login_guard`, 429) sets the cookie `bcc_session` (HttpOnly, `SameSite=strict`, `Secure` only when the scheme is https or `BCC_COOKIE_SECURE=always`, TTL from `BCC_SESSION_TTL_HOURS` default 720). It returns `{ok, csrf, expires_at, csrf_header:'X-BCC-CSRF'}`.
- `require_token` (`api.py:277`): with a cookie, unsafe methods need a matching `X-BCC-CSRF` or get 403 `code:'csrf'`. Otherwise `X-BCC-Token` works if `BCC_LEGACY_TOKEN≠0`. Otherwise 401.
- The WebSocket checks `Origin` and the cookie (closes with 4403 or 4401). `HostGuard` middleware (`api.py:540`) blocks DNS rebinding.
- `EventSource` and `getUserMedia` work same-origin on http://127.0.0.1 (a secure context), and the cookie is sent automatically.
- Error bodies are always `{error:{message, hint?, code?}}`; validation errors come back as 422 in Russian. The UI surfaces them with `toastError(err, fallback)`, the `errorBanner` or `errorNote` from `_shared.js`, and the login redirect on `isAuth`.

---

## 5. Tests that pin the shell (a new UI must keep these)

**Browser tests** (Playwright, skipped without Chromium; live server from `tests/test_ux2_thinking_pane.py`). `_login`: `goto('/')`, fill `#login-token`, click `#login-submit`, wait for `#shell:not([hidden])`.

- **`tests/test_ux2_thinking_pane.py`**
  - `#think-pane` hidden at start. Clicking `#think-open` shows it with `aria-pressed="true"`.
  - `#conn-dot.dot-ok`.
  - `.bx-think-card[data-run="7"][data-state="waiting_approval"]` whose text contains "qwen-14b", "fs.read", "2 из 5" and "решение владельца (terminal.run)".
  - `.bx-think-grid b` index 5 is "1" and index 6 is "0". `.bx-think-elapsed` ticks.
  - At least 6 `.bx-think-row`, including "step 2 done" and "запасная модель", and no "chain".
  - Ctrl+. closes it, and the open/closed state survives a reload.
  - `window.__bxThinking.open()` and `.stats()` return `{events≤maxEvents, rows≤80, renders<total/2}`; responsiveness within +50 ms.
  - No console errors.
- **`test_continuity_desktop_ui.py`**
  - After first login `html[data-theme]=='light'`.
  - `#desktop-dock` visible at 1440 px; `[data-page="web_designer"]` click gives `aria-current="page"` and URL `#/web_designer`.
  - `#desktop-skip` plus Enter focuses `#view` without changing the hash.
  - `#theme-toggle`, reload, gives `dark`.
  - At 390 px the dock is hidden, `#mobilenav` is visible, and there is no horizontal scroll.
- **`tests/js/desktop.test.mjs`** (node)
  - `desktopPages(pages,'home-v3')` returns ids `['home-v3','web_designer','control']`.
  - `preferredTheme('dark')=='dark'`; anything else gives `'light'`.
- **`test_desktop_dock_overlap.py`**
  - On `#/command` (1440×900) the page is taller than 900 px and the `START` button inside `#view` is clickable.
  - `#desktop-dock` is `position:fixed`, `z-index ≥ 40`, and within the viewport.
- **`test_ux_navigation_shape.py`**
  - `window.__bxPages` has at least 25 entries with `{id, title, section, inSidebar}`, and every section is one of the six.
  - `__bxSections == ['main','work','studio','apps','brains','system']`, with 5 to 7 `.nav-section` headers.
  - No duplicate `.nav-item .nav-label`.
  - `video-studio`, `web_designer`, `trading_lab`, `browser` and `coding` are in `.nav-item[data-page]`. `{video-studio, web_designer, browser, coding}` are in section studio; `{resources, governor, healing, forks}` in system; no section is empty.
  - Exactly 5 `.mnav-item`.
  - `.nav-item` elements are `BUTTON:button` and focusable.
  - Pages hidden from the sidebar still load, with `#page-title` text equal to the title.
- **`test_ux2_reconnect.py`**
  - `#conn-text` text is exactly `'live-обновления'`.
  - Stopping the server shows `#stale-banner`; `#conn-text` matches `/нет соединения · повтор через \d+ с/`.
  - `#stale-text` contains "Нет связи с сервером с" and "могли устареть". `#stale-retry` matches `Повтор через \d+ с|Подключаемся…|Повтор…`.
  - `#conn[data-state]` is closed or connecting; `__bxConn.bus.attempt` is at least 1; `#stale-now` works.
  - After restart, a `.toast` with "Соединение восстановлено" containing "Данные обновлены" and "без связи"; `data-state=open`; `bus.disconnectedAt==0`; `__bxThinking.runs.has(777)`.
- **`test_cc_ux_p2_findings.py`**
  - A revoked session shows `#login` and hides `#shell` within 35 s, relying on the 30 s `loadTopStats`.
  - A server outage keeps `#login` hidden and `bus.stopped===false`.
- **`test_v6_lazy_pages.py`**
  - Every `FEATURE_PAGES` manifest equals the module's static fields, which must be only `{id, title, icon, nav, section, sweep}`. The only function keys allowed are `{render, onEvent}`.
  - At least 25 entries with unique ids.
  - The first render after login loads **at most 6 `/pages/*.js` modules** (not counting `_*.js` or `index.js`), and never `objectives.js` or `trading_lab.js`. `#/objectives` loads on demand.
  - Idle preload eventually loads everything.
- **`test_ux2_pages_sweep.py`** (every page in `__bxPages`)
  - `#page-title` equals the title; `#view` has no `.skeleton` and has children.
  - **No `<button>` anywhere in `#view` whose text is exactly "Повторить"**, including hidden ones.
  - Every visible button has text, `aria-label` or `title`.
  - No mojibake.
  - The first 3 enabled buttons whose label matches `^(Нов(ый|ая|ое)|Создать|Добавить|Настро(ить|йки)|Подключить|Импорт)` must open `#modal-root .modal` (closed by Esc) or change `location.hash`.
  - At 390 px, no page has horizontal overflow; `#think-pane` is at least 380 px wide; `#think-close` hides it.
  - The mobile drawer: `#mobile-menu` adds `.shell.menu-open`, and `#nav .nav-item` is on top (checked with `elementFromPoint`).
- **`test_release_ux_torture.py`**
  - Every `#nav .nav-item[data-page]` click ends with `#view.dataset.rendered===id`, non-empty text, no "Загрузка…" in the first 120 characters, and `aria-current="page"`.
  - No API response with status 404, 405, 422 or 500 during render.
  - `#theme-toggle` flips `data-theme`; `#palette-open` shows `#palette` and `#palette-input`, and Esc hides it; `#refresh-btn` loses `.spin`.
- **`test_ux2_desktop.py:195`** checks the `#login` form in the `--app` window and that the page title contains "BOSSMAN".
- **Landing page pins:**
  - `test_ux_soak_home_long_task.py:46,84` waits for **`#view[data-rendered='home-v3']` right after login**, then uses `#view textarea`, Ctrl+Enter, a toast containing "поставлена", and `#view input[type=file]`.
  - `test_ux2_home_attention.py` uses `#attention`.
  - `test_cc_ux_p1_findings.py` and `test_release_owner_feedback_ui.py` use `.bx-command-input` on `#/home-v3` and `#think-close`.
- `test_testing_period.py` pins `#think-open` and `#bcc-testing-*`. `test_command_bar_ui.py` pins the `#cmdbar-*` ids. `test_mission_console.py` pins `#mc-command-agent`, `#mc-command-input` and `.mc-*`.

**Static tests**
- **`test_intro_splash.py`:** in `index.html`, `<script src="intro.js"></script>` comes before `src="app.js"`. `intro.js` contains `navigator.webdriver`, `sessionStorage`, `prefers-reduced-motion`, `'bossman.intro'`, `nointro` and `setTimeout(close, 7000)`.
- **Login field** (`test_login_token_not_offered_to_password_manager.py`, `test_audit_sec_rc19.py:245`): `<input id="login-token"` is `type="text"`, has `-webkit-text-security: disc`, `autocomplete="one-time-code"` and class `token-mask`. There are no password inputs in `#login-form`. `style.css` must contain `.token-mask { -webkit-text-security: disc; }`.
- **`test_login_token_file_hint.py`:** `app.js` contains `api.loginHint()` and "Токен лежит в файле"; `index.html` has `id="login-hint"`.
- **`test_owner_control_liveness.py`:** every class toggled with `classList.add('x')` or `classList.toggle('x'` in any `ui/**/*.js` must have a `.x` rule in some CSS file or inline CSS literal. `.is-loading` must include `pointer-events`. `pages/_ui.js` must contain `is-loading` and `aria-busy`.
- **`test_pit_web.py:90`:** the Jeff server must return 404 for `index.html`, `app.js`, `api.js` and `desktop.js`, so the chat UI must not rely on Jeff.
- **`ui/tests/app_view.test.mjs`** pins `retainAppFrame` in `app_view.js`.

---

## 6. Which endpoint each new control should call, and where there is none

| Control | Bind to | Notes |
|---|---|---|
| Send | `preflight` → `POST /api/tasks {run_now:false, client_request_id}` → open SSE → `POST /api/tasks/{id}/run` | Prompt uses the section 2A context format |
| Live tokens | `GET /api/events/stream?task_id&after=` (`run.answer_delta`, `answer_reset`, `assistant_message`) | Reopen by hand with `after=lastSeq`, deduplicate by `seq`; WS `run.answer_delta` as secondary path |
| STOP | `POST /api/tasks/{id}/stop` | Treat 409 as already finished; global STOP is `control-plane/stop-all` |
| Error and retry | `task.failed`, `task.blocked` events, `GET /api/tasks/{id}.error`, `POST /api/tasks/{id}/retry` | Do not label the button exactly "Повторить" |
| Model picker | `GET /api/agents` + `/api/models` + `/api/providers`; Auto = `agent_id:null` via preflight | Labels: local, cloud, health; no per-message model override |
| Mic | `getUserMedia` → WAV 16 k (reuse `jeff.js blobToWav16k`) → `POST /api/oss/speech/transcribe`; enable using `GET /api/oss/status` | — |
| + Attach | Only video (`/api/video-studio/*`) or images import | **Gap:** no general chat upload |
| Memory chip | `GET /api/memory/config` + `/stats`; recall shown from `run_events` `memory.recalled` | — |
| Tools chip | `agent.tools` + `GET /api/capabilities` | — |
| Context chip | `run.usage` vs `model.context_window` | Computed on the client |
| Rave chip | `POST /api/rave`, `GET /api/rave/connectors` | — |
| Thinking panel | Existing `thinking.js` (keep ids) plus SSE `run.tool_use` / `tool_result`, `task.progress`, `/api/runs/{id}/events`, approvals | Drop `run.reasoning_delta`; no plan for plain tasks |
| History, search | `GET /api/tasks?before_id=`; `GET /api/search` (flag) | Threads, rename, delete, pin and projects have no API |

**Constraints on the new page**
1. Keep the shell ids and the landing page as they are.
2. Register the chat as a new lazy page in `pages/index.js`, for example id `chat`, section `main` or `work`.
3. Scope the dark look to the chat page, not the global default theme.
4. Make "Новый чат" change the hash, for example `#/chat?new=1`.
5. Put every animation behind `prefers-reduced-motion`. Note that `desktop.css` already disables all animations under that setting.

**Missing backend pieces:**
- a conversation or thread entity with list, rename, delete and pin;
- a projects API;
- a general chat attachment upload;
- per-message model override or `meta` (project, route) on `TaskIn`;
- honoring `Last-Event-ID`;
- a plan for a plain task;
- a `signal` (abort) option in `api.raw`.