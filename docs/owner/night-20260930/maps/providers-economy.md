# Model providers, routing, subscriptions, streaming, prompt caching and cost: code map and minimal change list

Read-only survey of HEAD `b6018ee3` on branch `claude/bossman-1.9-owner-bugtest-20260930`. Nothing was edited, run or tested. I only read the drop-in zip, in the scratchpad directory. Paths are relative to `command-center/bcc/` unless stated otherwise.

---

## 1. Providers: what exists

### 1.1 Adapters (`providers.py`)
- **Only two adapters are registered:** `ADAPTERS = {"openai_compat": OpenAICompatAdapter, "anthropic": AnthropicAdapter}` (`providers.py:638-641`). `build_adapter()` (`:644`) raises `ProviderError` for any other kind. `POST /api/providers` validates the kind against the same map (`api.py:846`), and `GET /api/providers/kinds` returns it (`api.py:838`).
- **No Ollama-native or llama.cpp-native adapter in the engine path.** Both go through `OpenAICompatAdapter` (docstring `:254`).
  - Special case for Ollama: `is_ollama_v1_url()` (`:125`) makes `chat()` send `reasoning_effort:"none"` (`:276-284`).
  - llama.cpp model state comes from `/models` in `list_model_info()` (`:404-441`).
  - Jeff (PIT) has its own native Ollama client: `pit/ollama_native.py:80-215`. It calls `/api/chat` with `think:false`, `keep_alive:"30m"` and NDJSON streaming. It is not in `ADAPTERS`.
- **No CLI-subscription adapter** (Claude Code CLI or Codex CLI) exists as a `ProviderAdapter`. Subscription use lives only in:
  - `autonomy/workers.py:335-357` (`cli_argv`): `claude -p --output-format json --permission-mode acceptEdits|plan …` and `codex exec --sandbox … --json -o … -`.
  - `autonomy/workers.py:366-400` (`final_text`, `_usage`).
  - `rave/connectors.py`: `ClaudeConnector` (`:350`), `CodexConnector` (`:400`).
  - `telegram_companion/claude_bridge.py:93-101`.
  - Login and subscription status comes from `rave/connectors.py:296` `claude_login()` (`claude auth status --json`, where `authMethod=="claude.ai"` means subscription) and `:318` `codex_login()`. It is exposed as **`GET /api/rave/connectors`** (`features/rave.py:78-90`), which returns `{claude:{installed,logged_in,auth_method,subscription,plan,auth,login_step,optin,sources}, codex:{…}, local:{endpoint,default_model}, api_key_path:{enabled,how}}`.
  - The API-key path is off unless `BOSSMAN_RAVE_ALLOW_API_KEY=1` (`rave/connectors.py:259`). `cli_env(..., api_key=False)` strips `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` and `OPENAI_API_KEY`/`CODEX_API_KEY` (`:260-286`).
  - `workers._usage` (`:392`) keeps only input and output tokens. The CLI's `cache_read_input_tokens`, `cache_creation_input_tokens` and `total_cost_usd` are dropped.
- **`hybrid/`** (`LocalModelRuntime`, `BackendType.LOCALAI`) is a capability registry behind feature flags. It is not imported by `api`, `app` or `engine`, so it is not in the inference path.

### 1.2 How local, cloud and paid are represented (four inconsistent rules)
1. **Database columns.** `models.kind` is `"local"|"cloud"`; `providers.kind` is `openai_compat|anthropic` (`db.py:31-58`). `price_in` and `price_out` are USD per 1M tokens. `pricing_known` is a bool. `models.health` (JSON) is added by the migration list in `db.py:549`. There are no cache-price columns.
2. **`v2/model_router.derive_local()`** (`:50-76`) is the strict rule. A model is local only if `models.kind=="local"` AND the provider kind is in `LOCAL_PROVIDER_KINDS` AND the host is loopback, RFC1918 or ULA. The router (`features/router.py:171`) and governance (`provider_governance.is_governed_local:59`) use it.
3. **`providers.is_local_url()`** (`:136`) is broader: it also counts `.local` hosts and all private ranges except link-local. It decides proxy bypass (`http_client:184`) and whether owner memory may leave the machine (`GovernedAdapter:175`).
4. **`terminal_cli/ops.locality()`** (`:25-39`) counts only loopback as local, and `model.kind=="cloud"` always wins. It feeds `bossman /models` (`terminal_cli/cli.py:860-869`, `chat.py:658`).
5. **Web UI** trusts the raw column: `ui/pages.js:259` renders `String(m.kind) === 'cloud' ? 'облако' : 'локальная'`. `modelLabel()` (`pages.js:55`) and `modelSelect()` (`:83`) show the alias only, with no locality or billing.
- **Honesty gap.** A loopback proxy that forwards to cloud (LiteLLM `127.0.0.1:4000` is in `discovery.KNOWN_ENDPOINTS:36`; the bossman-core gateway binds `:8765`) is classified local by rules 2 and 3. That exempts it from `free_only_refusal()` and from memory withholding in the command-center. The core gateway enforces free-only itself; LiteLLM does not.

### 1.3 Free-only product runtime policy (exists and works)
- **bossman-core** (commit `ddd9b055`):
  - `gateway/config.py`: `PAID_ENV_BACKENDS` (zai, openai, anthropic, google, groq, mistral, together, nvidia) are skipped when loading from environment keys, unless `BOSSMAN_ALLOW_PAID_CLOUD=1`.
  - `gateway/router.py`: `cloud_route_allowed()` only allows the `openrouter` backend with a model ending in `:free`.
- **command-center** (commit `c5ac7df3`), `provider_governance.py`:
  - `free_only_policy_active()` (`:90`) reads the same env switch.
  - `free_only_refusal(provider, model)` (`:113-140`):
    - A banned family (Liquid/LFM) is refused everywhere.
    - Local endpoints and the Anthropic "Fable boundary" (`fable_cap.paid_fable_boundary`, `:80`, any non-local `anthropic` provider) are exempt.
    - Any positive price is refused.
    - Otherwise the model must be OpenRouter with a `:free` id, or carry `caps.free_tier` on a `features/free_providers.PRESETS` host (nvidia_nim, groq, google_ai_studio, `:42-51`).
  - Enforced in `Registry.adapter_for()` (`registry.py:159-183`), which wraps adapters as `GovernedAdapter(FreeOnlyAdapter(capped(inner)))`. `FreeOnlyAdapter.chat` (`provider_governance.py:143-153`) raises `ProviderError(kind="budget")`; health and catalog reads still work.
  - Also enforced at admission: `task_admission.select_executor` (`:86-95`) skips such agents when auto-selecting.
  - `GovernedAdapter.chat` (`:164-182`) refuses non-local, non-capped models whose `pricing_known` is false, and strips `[MEMORY CONTEXT` system messages for non-local destinations unless `task.meta.memory_to_cloud`.
- **Jeff has its own free route check:** `pit/model_route.py:26` `route_verdict()` requires a live catalog price of 0/0, a `:free` id, and a recheck every 120 s.
- **Paid Anthropic direct is still possible** through `fable_cap.CappedAdapter` (`:107-172`), which reserves the worst case before the call against a global hard cap of `FABLE_HARD_CAP_USD = 3.00` (`bossman_shared/fable_budget.py:98`).
  - Prices come from `PRICE_TABLE` in code (`fable_budget.py:56-64`). `claude-opus-5-5` and `claude-sonnet-5-5` are **absent**, so they are refused before dispatch.
  - `claude-opus-5` is listed at a provisional 15/75.

### 1.4 How "Auto" chooses a model
- **Model resolution order** in `engine._call_model_scoped` (`engine.py:1357-1482`):
  1. `model_override` from the recovery ladder.
  2. The first non-None `pick_model` hook result (`:1429`).
  3. `agent.model_id`.
  4. `agent.fallback_model_id`.
  - `check_fallback_model` (`:1374-1400`) applies `features/router.check_forced_model` on routed tasks.
- **Smart Router hook** (`features/router.py:485-551`):
  - Active only when `task.meta.route` is set or `task.kind != "generic"`. Otherwise it goes to `_memory_pressure_fallback`.
  - Cloud is fail-closed through `cloud_policy()` (`:286-318`): it needs a strict `true` in `task.meta.cloud_allowed`, `agent.permissions.cloud_allowed` or `rules.cloud_default_allow`, and a non-positive `cloud_budget_usd` closes it.
  - Candidates come from `_candidates()` (`:126-208`), which reads DB rows plus bench, success rate and probes.
  - Scoring is the pure `v2/model_router.route()` (`:211`). The shortlist is capped at 12. Local gets +14. The cloud cost penalty is `-(price_in+price_out)/2`, capped at 20. Speed bonus is `min(gen_tps/10, 10)`.
  - With `rules.adaptive` on, `classify_reasoning` gives L0–L4. L3/L4 turn off `prefer_local` and turn on `require_verified`.
  - Emits `router.route_selected` and writes `task_runs.route`.
- **Forced model:** `features/forks.py:29-41` handles `meta.force_model_id` through the same policy and emits `router.force_refused`.
- **The UI has no "Auto" model option.**
  - `POST /api/tasks` `TaskIn` (`api.py:416-428`) has **no `meta` field**, so the UI and CLI cannot set `route` or `force_model_id` per task.
  - CLI `/models use` persistently patches the agent's `model_id` (`terminal_cli/chat.py:635-650`).
  - `home.js:58` `'auto'` means "run without clarification", not model routing.
  - Router UI: `ui/pages/router.js` uses `GET/PATCH /api/router/rules`, `POST /api/router/preview`, `GET /api/router/explain?task_id=` and `POST /api/router/candidates`.

### 1.5 Model health and speed
- `model_health.py`: statuses `healthy|silent|malformed|throttled|unauthorized|provider_down|timeout|unmeasured`, plus a cooldown. The router treats an in-cooldown model as offline.
- `model_speed.py` (TEL-001): `from_timings()` uses llama.cpp `timings.predicted_per_second` and `prompt_ms`, with differential fallback. The result is stored in `models.bench` as `{method, ttft_ms, prompt_tps, gen_tps, latency_ms}` through benchlab. `_timed` passes `temperature=0` (`:45`).

---

## 2. Streaming (exists and works for OpenAI-compatible; Anthropic does not stream)

**Call chain:**
1. `engine._run` creates `_AnswerStream(self, task_id, run_id, step+1)` per step (`engine.py:1057`).
2. It passes it as `on_delta` into `_call_model` → `_call_model_scoped`, where `kw["on_delta"]=on_delta` (`:1367`) → `adapter.chat`.
3. The adapter wrappers (`GovernedAdapter`, `FreeOnlyAdapter`, `CappedAdapter`) pass `**kw` through.
4. `OpenAICompatAdapter.chat` (`providers.py:285-290`) calls `_chat_streamed()` (`:337-389`):
   - Body: `{**payload, "stream": True, "stream_options": {"include_usage": True}}`.
   - Uses `client.stream("POST", …)` and `streaming.read_chat_stream(resp.aiter_lines(), push)` (`streaming.py:561-590`).
   - `read_chat_stream` folds frames through `_fold_chat_frame` (`:499`). It handles content, reasoning, tool-call deltas, usage (read once), `[DONE]`, and error frames. `_ThinkGate` strips inline `<think>`.
   - The result is rebuilt as a non-stream body (`ChatStream.body()`, `:483`), then `_result_from()`, with `provider_meta["streamed"]=True`.
5. `_AnswerStream.__call__` (`engine.py:2809-2836`) coalesces to about 10 events/s and at most 200 characters in the buffer, then calls `engine._emit_stream`, which calls `bus.emit`.

**Events** (`events.py:26-40`). `STREAM_ONLY` events are persisted to the `events` table except `run.reasoning_delta`.
- `run.answer_delta {task_id, run_id, step, attempt, idx, text}`
- `run.answer_reset {task_id, run_id, step, attempt}`, sent when a mid-stream failure discards what was shown.
- `run.assistant_message {step, model, text, chars, streamed}` (`engine.py:1167`).
- `run.assistant_delta`, `run.reasoning_delta {text, chars, source}`.
- `run.usage` (section 5).
- `cache.observation`, `task.progress`, `run.log`, `run.budget_exceeded`, `router.route_selected`, `router.fallback`, `model.status`.

**Transport:**
- `GET /api/events/stream?task_id|run_id&after=` is SSE (`api.py:1216-1275`). It sends `stream.open`, `stream.replayed`, `stream.lagged`, and keepalive comments; event `id:` is the durable `seq`.
- `GET /api/tasks/{id}/events?after&limit` replays history.
- WS `/api/events` (`api.py:728-757`) **drops every STREAM_ONLY kind except `WEB_LIVE = {run.answer_delta, run.answer_reset}`**. So `run.usage` never reaches the web UI.
- Web consumer: `ui/thinking.js:189-195` keeps the last 600 characters in `r.live`.

**Cancellation:**
- `Engine.stop()` (`engine.py:365-412`) sets the task to stopped and calls `worker.cancel()`.
- `CancelledError` (a `BaseException`) propagates through `_chat_streamed`. Only `httpx.TimeoutException` and `HTTPError` are caught there. The `async with client.stream(...)` and `AsyncClient` context managers close the HTTP connection, which stops llama.cpp and Ollama generation.
- `CappedAdapter` catches `BaseException` and holds the reservation (RECONCILING).
- `_execute_pooled` (`:584-628`) finalizes the run as stopped.

**Error mapping in `_chat_streamed`:**
- HTTP in `_STREAM_REJECTED {400,404,405,415,422,501}`: returns None, so the non-stream call follows.
- Other HTTP 4xx/5xx: `ProviderError(_explain(resp), kind="http")`. **HTTP 429 becomes `kind="http"`, not `rate_limit`.** Only a 429/503 inside a 200 body gets the retries `(2s, 5s)` and then `kind="rate_limit"` (`:291-307`).
- Non-SSE content type: parse JSON; an in-body error returns None.
- Timeout or `HTTPError`: `discard()` (sends `on_delta(None)` if something was shown), then `ProviderError(kind="network")`.
- Error frame, or a stream without a terminator: returns None if nothing was shown, otherwise discard and raise.
- `not cs.has_output`, i.e. reasoning-only with no content or tool calls: returns None, which triggers **a second full non-stream request**. That doubles latency and can double tokens.
- Engine side: `ProviderError` goes to `_handle_failure(kind=…)`; `PermissionError` (privacy) goes to `_fail_now`.

**Gaps:**
- The streamed path uses `CHAT_TIMEOUT=600` s as the per-read timeout. `streaming.DEFAULT_FIRST_BYTE_TIMEOUT=20` is used only by probes.
- `ChatStream` does not capture llama.cpp `timings`, so **every streamed run loses TEL-001 server timings.** The engine always passes `on_delta`, so streaming is now the normal path.
- **`AnthropicAdapter` has no streaming.** `on_delta` is silently ignored and the whole answer arrives at once.

---

## 3. Prompt caching

### 3.1 What exists in command-center HEAD
- **Anthropic, explicit caching (exists and works).** Arrived through the history import `d6b43cea`; the original `f43f8fba` is not an ancestor, but its content is present.
  - `AnthropicAdapter.cache_policy()` (`providers.py:503-510`): `BCC_ANTHROPIC_PROMPT_CACHE=0` turns it off; `BCC_ANTHROPIC_CACHE_TTL=1h` selects the long TTL.
  - `cache_control` goes on the last tool (`:539-540`) and on a single system text block (`:542-545`).
  - Usage is parsed: `cache_read_input_tokens` and `cache_creation_input_tokens` go to `ChatResult.cache_read_tokens` and `cache_write_tokens`; `tokens_in` = input + read + write (`:560-577`); `provider_meta.prompt_cache={applied, read_tokens, write_tokens, hit}`.
  - Tests: `tests/test_providers.py:104,134`.
- **Engine telemetry:**
  - A `model.prompt_cache` run log line when read or write is greater than 0 (`engine.py:1098-1103`).
  - `cache_observation_for()` (`:2932-2972`) emits `cache.observation` on every step, governed by `BOSSMAN_CACHE_TELEMETRY_V2`, default on (`:2925`). It uses `bossman_shared/cache_observation.py`: `normalize_anthropic_usage`, `normalize_openai_style_usage` (which reads `prompt_tokens_details.cached_tokens`), `classify` → HIT/WRITE/MISS/BYPASS/UNKNOWN/DEGRADED, and `cost_pair`.
  - Aggregated by `features/cache_intel.py`: **`GET /api/cache/economics`** and **`GET /api/cache/intelligence`** (`:149-155`, mounted under `/api`). Rendered in `ui/pages.js:1716-1745` (`cacheEconomicsPanel`).
  - `_cost()` (`engine.py:2904-2922`) prices fresh, read and write separately using `model.price_cache_read` and `price_cache_write`. **Those columns do not exist**, so both fall back to `price_in`, which is conservative.
- **Defects found:**
  1. **Memory is cached together with the agent prompt.** The Anthropic adapter joins *all* system messages into one cached block (`:523-545`). The engine puts per-task recalled memory into a system message right after `agent.system_prompt` (`engine.py:1001-1005`). The cached prefix therefore changes per task, so the cache only helps within one run.
  2. **No breakpoint on the conversation.** Each tool-loop step re-pays the whole growing history at the fresh-input rate.
  3. **1h TTL is under-counted.** `fable_budget.PRICE_TABLE` cache_write is the 5-minute rate (1.25×). With `BCC_ANTHROPIC_CACHE_TTL=1h` the write actually costs 2×, so the $3 cap under-counts.
  4. `cache_observation_for` hardcodes `ttl="5m"` (`:2969`). `provider` comes from `model.get("provider_kind") or model.get("kind")`, which yields "local"/"cloud". `route` uses `models.kind` instead of `derive_local`.
  5. **`OpenAICompatAdapter._result_from` (`:310-335`) never maps `usage.prompt_tokens_details.cached_tokens` to `cache_read_tokens`.** Cache use is visible only in the raw `provider_meta.usage` used for the observation, so `_cost` and the log line ignore it.
  6. `AnthropicAdapter` forwards `temperature` (`:547`), and `model_speed` sends `temperature=0`. Opus 5.5 and the Fable 5.x models reject sampling params with a 400. The adapter also does not send `thinking` or `output_config`, and its `max_tokens` default is 2048.
- **llama.cpp and Ollama:**
  - The engine sends nothing cache-specific. llama-server reuses the slot's KV for a matching prefix by default (`cache_prompt` defaults to true). Ollama reuses the prefix within a loaded model.
  - `keep_alive` is sent only by Jeff's native client (`pit/ollama_native.py:105,142`, `market/extract.py:114`). Ollama `/v1` ignores a `keep_alive` field in the body; `OLLAMA_KEEP_ALIVE` sets the default.
  - Nothing reads llama.cpp `timings.cache_n` or Ollama `prompt_eval_count` as a cache signal.

### 3.2 bossman-core gateway (exists, separate path)
- `bossman-core/bossman/gateway/prompt_cache.py` (269 lines) has:
  - `prepare_provider_payload(..., session_id, requested_ttl, default_ttl, enabled, session_affinity, endpoint)`
  - `minimum_cacheable_tokens`, `extract_cache_usage`, `SSEUsageCollector`, `cache_metadata_rejected`.
- It is integrated in `gateway/app.py:403-431`. The client sends headers `x-bossman-session-id` and `x-bossman-cache-ttl`. Settings come from the backend config: `prompt_cache_enabled`, `prompt_cache_ttl`, `session_affinity_enabled`.
- The Cost Governor uses cache prices (`app.py:109-182`, `274-296`).
- `anthropic_protocol.py:139-215` preserves `cache_control` on tools and parses cache usage.

### 3.3 `BOSSMAN_PROMPT_CACHE_OPUS5_DROPIN.zip` (on `origin/handoff/continuation-20260929`)
- **Contents:** `bossman-core/bossman/gateway/prompt_cache.py` (71 lines), one test, a README, `CLAUDE_INTEGRATION_PROMPT.md`, `docs/COST_EXAMPLES.md`, `docs/DASHBOARD_CACHE_FEATURES.md`, and an identical nested `x.zip` and `out/` copy.
- **What it proposes:**
  - `PromptCachePolicy(enabled, ttl "5m"|"1h", automatic)`.
  - `stable_session_id(agent, project, conversation)` → `"bossman-{prefix}-{sha256[:24]}"`, set as OpenRouter `session_id`.
  - For model ids containing `anthropic/` or `claude-`: a top-level `cache_control` (automatic), or an explicit breakpoint on the last text block of the first system/developer message.
  - `extract_cache_usage()`, which reads `prompt_tokens_details.cached_tokens`, `cache_write_tokens` and `cache_discount`.
  - Integration target: Opus 5 via OpenRouter, "stable content first".
  - Dashboard wish list: hit %, cached tokens, write tokens, saved $, session affinity, TTL, miss reasons.
- **Verdict: already superseded.** The in-repo 269-line version is a hardened superset and is wired in, so applying the zip would regress it.
- **Conflict with free-only:** its main target, `anthropic/claude-opus-5` on OpenRouter, is refused by `cloud_route_allowed` unless `BOSSMAN_ALLOW_PAID_CLOUD=1`. Do not apply it.

### 3.4 Which providers really support caching, and what the runtime can use
| Provider | Mechanism | What command-center sends | Money or latency value under free-only |
|---|---|---|---|
| Anthropic direct (Fable-capped) | Explicit `cache_control` or top-level automatic; ≤4 breakpoints; minimum prefix 512 tokens on 5.x (1024–4096 on older models); 5m write 1.25×, 1h write 2×; read about 0.1× (0.05× on Opus 5.5) | tools + system (see defects 1–3) | Money, within the $3 cap |
| OpenRouter `:free` | Implicit prefix caching for OpenAI, DeepSeek and Gemini families; Anthropic needs `cache_control` | nothing | Free anyway; latency only |
| OpenAI-compatible free tiers (groq, nim, google) | Provider-side implicit caching where offered | nothing; `cached_tokens` not mapped | Latency only |
| llama.cpp | Slot KV reuse (`cache_prompt` default true; `--cache-reuse`; `id_slot`); `timings.cache_n` | nothing; `timings` lost when streaming | **Main value: time to first token** |
| Ollama | Automatic prefix KV reuse while the model is loaded; `keep_alive` | Engine: nothing (env default). Jeff: `keep_alive 30m` | Time to first token |

**Where a stable prefix is built:**
- **Runs:** `engine.py:998-1005` builds `[system: agent.system_prompt][system: recalled memory, which starts with "[MEMORY CONTEXT"][user: task.prompt]`. Tools come from `TOOLS.resolve`, which returns them sorted (`tools.py:244-254`), so the order is deterministic. Checkpoints preserve messages across steps and resumes. The stable part is `tools + agent.system_prompt`; memory is the first volatile element.
- **Jeff:** `pit/participant_context.py:73-151` builds `ParticipantContext.as_messages()`:
  - Message 0 is `PIT_ASSISTANT_SYSTEM + style + per-person profile + behavior scales`, stable per person.
  - Message 1 is persona memory selected by the query (volatile).
  - `pit/runtime.py:1897-1943` appends fixed local-only text to message 0, adds conditional roast text, history (`[-2:]` for local), reply quote, web block, roleplay, and the user message.
  - `ollama_native.chat` (`:84-90`) turns later system messages into a user "context" message. Message 0 is the reusable KV prefix.

---

## 4. Cost ledger and budgets
- **Per-run totals:**
  - `task_runs.tokens_in`, `tokens_out`, `cost_usd`, `model_alias` (`db.py:92-108`).
  - Accumulated in `engine._run` (`:1086-1091`) with `_cost(model, result)`, using owner-editable `models.price_in` and `price_out`.
  - Persisted via `_save_checkpoint`, `_complete_run` and `_budget_stop`.
  - **Cache tokens are not persisted per run**; they exist only in `cache.observation` events.
- **Per-task limits:** `mission_budget.Limits.for_task` (`:69-86`) reads meta keys `max_tokens_total`, `max_cost_usd`, `max_identical_calls` and `max_stalled_steps`.
  - `check_spend` runs after every step (`engine.py:1092`) and emits `run.budget_exceeded`.
  - `run_metrics` backs **`GET /api/tasks/{id}/efficiency`** (`api.py:1370`).
- **Spend meter** (`features/spend_meter.py:320-418`): `GET /api/spend`, `POST /api/spend/limit`, `POST /api/spend/check`. The ledger is built from `task_runs.cost_usd > 0` plus `studio_jobs.cost_usd`.
- **Anthropic hard cap:** `bossman_shared/fable_budget.py` (reserve → commit or hold, overflow booking) through `fable_cap.CappedAdapter`.
- **Jeff:** `pit/cloud_budget.py` (daily budget, per-job USD cap, 402 block).
- **Autonomy:** `autonomy/savings.py` `SavingsLedger` records avoided subscription turns.
- **Gateway (core):** Cost Governor reserve → commit with cache prices.
- **"Cheap or local model for small fixes" already has these routes:**
  - Smart Router with `rules.adaptive=true` (L0/L1 keep `prefer_local`) and `rules.prefer_local`. Set `task.kind` (for example `"coding"` requires the `coding` capability). `meta.max_price_out`.
  - Autonomy `cycle.route_writer` (`autonomy/cycle.py:124-135`) sends `docs_tests` tasks touching at most 2 paths to the free Nemotron diff writer, and everything else to the seeded Claude/Codex subscription CLI.
  - Policy-only layers (no execution): `features/coding_limit_saver_v16.py` (`/api/coding-limit/route`, LOCAL → FREE → GLM-5.3-Flash) and `features/local_first.py` (flag `BOSSMAN_LOCAL_FIRST_ENABLED`, `/api/local-first/*`).
  - The minimal way to express it: submit small-fix tasks with `kind="coding"` and `rules.adaptive=true`. That requires `TaskIn` to accept `route` or `kind`; see (a5) below.

---

## 5. Minimal change list

### (a) Honest provider labels in the model picker
1. **`provider_governance.py`: new pure `model_billing(provider, model) -> dict`.**
   - Returns `{locality: "local"|"lan"|"cloud", billing: "local"|"free_cloud"|"paid_capped"|"blocked"|"unknown_price", refusal: str, provider_kind, host}`.
   - Built from `derive_local`, `is_local_url`, `free_only_refusal`, `paid_fable_boundary` and `priced`.
   - A loopback host on port 4000 (LiteLLM) or 8765 (bossman-core gateway), or `owned_by` containing a `/` vendor prefix, gets locality `"local_proxy"` (it may forward to cloud) rather than `"local"`.
   - Needs a negative-control test pair.
2. **`api.py:925` `list_models`**: join providers and append those fields plus `health.status`, `bench.{gen_tps,ttft_ms,method}` and `usable` (no refusal). Keep the raw keys.
   - Alternatively add `GET /api/models/picker` returning:
     - `[{id, alias, name, provider_id, provider_name, provider_kind, locality, billing, refusal, usable, status, health, context_window, caps, price_in, price_out, pricing_known, bench}]`
     - `auto: {label:"Auto (Smart Router)", rules:"/api/router/rules"}`
     - `subscriptions:` taken from `GET /api/rave/connectors` and marked `selectable_for_chat:false` ("only through autonomy/rave workers").
   - Do **not** invent a CLI-subscription `ProviderAdapter`.
3. **UI:** `ui/pages.js:55` `modelLabel` and `:83` `modelSelect` should append a `locality·billing` badge; `:259` should use `m.locality` instead of `m.kind`. `terminal_cli/ops.py:25` `locality()` should prefer the server field.
4. **`engine.cache_observation_for` (`:2951-2953`)**: take `route` and `provider` from `model_billing`.
5. **`api.py:416` `TaskIn`**: add optional `route: bool` and `force_model_id: int`, copied into `task.meta`. The existing `forks.py:29` and `router.check_forced_model` enforce policy, so the picker's "Auto" and a per-task explicit model do not mutate the agent.

### (b) Prompt caching where it really pays
1. **`providers.AnthropicAdapter.chat` (`:522-546`)**:
   - System becomes a list: block 1 is the first system message with `cache_control`; later system messages (memory) become a second block without it.
   - Add a rolling `cache_control` on the last content block of the last message, or top-level `cache_control`, so tool loops read their own history. Stay at ≤4 breakpoints.
   - Put `ttl` into `provider_meta.prompt_cache`.
   - Drop `temperature` for models that reject sampling params.
2. **`bossman_shared/fable_budget.py` `actual_usd` and `estimate_worst_case_usd`**: price the 1h write at 2×, or make `cache_policy()` refuse 1h when `paid_fable_boundary`. Add `claude-opus-5-5` and `claude-sonnet-5-5` to `PRICE_TABLE` if the owner wants them.
3. **`providers.OpenAICompatAdapter._result_from` (`:324-335`)**: `cache_read_tokens = prompt_tokens_details.cached_tokens` (or `timings.cache_n` as fallback), keeping `tokens_in = prompt_tokens`, which already includes cached tokens.
4. **`streaming.ChatStream` and `_fold_chat_frame` (`:456-558`)**: capture top-level `timings` and include them in `body()`.
5. **`engine.cache_observation_for` (`:2969`)**: `ttl = pc.get("ttl")`.
6. **Local:** no payload change needed. Optionally send `cache_prompt: true` for llama.cpp hosts and document `OLLAMA_KEEP_ALIVE`. Keep `agent.system_prompt` byte-stable (no timestamps); memory already comes after it.
7. **Do not apply the zip.** The core gateway already has the hardened version.

### (c) Usage, caching and speed telemetry in the UI footer
1. **Timing in `engine._run`** around `_call_model` (`:1057-1061`): record `t0`; add `first_at` to `_AnswerStream.__call__` (`:2809`). Compute `latency_ms`, `ttft_ms`, and `gen_tps = tokens_out / (latency - ttft)` when streamed, preferring `model_speed.from_timings(provider_meta.timings)`.
2. **`engine._emit_model_step` (`:2724-2759`)**: add these to `run.usage`: `step_cache_read_tokens`, `step_cache_write_tokens`, `cache_state` (from the observation), `latency_ms`, `ttft_ms`, `gen_tps`, `tps_method`, `step_cost_usd`, `streamed`, `locality`, `billing`. Use null when unknown.
3. **`events.py:34`**: add `"run.usage"` to `WEB_LIVE` so WS `/api/events` (`api.py:750`) delivers it.
4. **`ui/thinking.js`**: in `applyFacts` (`:186-200`) handle `run.usage` into `r.usage`. In `runCard` (`:97-115`) add a footer row: `tok/s · TTFT · latency · in/out (cached N) · $cost`, showing "цена неизвестна" when `!pricing_known`, plus the locality/billing badge.
5. **Persistence via `db.py:513` `V2_NEW_COLUMNS`**:
   - `("task_runs","cache_read_tokens","INTEGER DEFAULT 0")`, `("task_runs","cache_write_tokens","INTEGER DEFAULT 0")`, written in `_save_checkpoint` and `_complete_run`.
   - `("models","price_cache_read","FLOAT")`, `("models","price_cache_write","FLOAT")`, which `_cost` already reads (`engine.py:2917-2918`). Fill them from OpenRouter `pricing.input_cache_read` and `input_cache_write` in `v2/openrouter_catalog_service.sync` (`~:118-123`).
6. **CLI**: `terminal_cli/records.py:207` should pass the new `run.usage` fields.

---

## 6. Other risks found
- **429 misclassified:** HTTP 429 gets `kind="http"` in both the stream and non-stream paths (`providers.py:241,371`), so recovery does not see it as a rate limit.
- **Reasoning-only stream is re-sent:** a streamed answer with only reasoning is re-requested without streaming (`providers.py:385-386`).
- **Timeout:** the streamed path has no first-byte timeout (600 s per read).
- **Proxy locality:** loopback proxies (LiteLLM `127.0.0.1:4000`, gateway `:8765`) count as local, which bypasses free-only and memory-withholding checks in the command-center.

Scratch extraction of the zip: `/tmp/claude-0/-home-user-AiMaxBossman/fd3095c4-f45d-57f7-a462-621217d38bd3/scratchpad/pc/`.

**Terminal Run and North Star:** this is a read-only survey, so it demonstrates no level on the North Star ladder. All proposed changes stay inside the same backend (`bcc`), one registry, one events bus and one ledger, consistent with the single-product Terminal Run contract.