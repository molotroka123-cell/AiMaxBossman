# BOSSMAN AI Command Center — архитектура MVP

Статус: принята для MVP-сессии. Раздел «OpenCode» дополняется отчётом исследования upstream.

## 0. Решения

| Вопрос | Решение | Почему |
|---|---|---|
| Язык/стек | Python 3.11+, FastAPI, asyncio | один язык на боксе (bossman-core уже на нём), быстрый старт, богатая экосистема |
| БД | SQLAlchemy 2 (async). По умолчанию SQLite (`aiosqlite`), через `DATABASE_URL` — PostgreSQL (`asyncpg`) | MVP работает без внешних сервисов на любой машине; на сервере — Postgres из bossman-infra той же кодовой базой |
| Очередь задач | Своя persistent-очередь в БД (таблица `task_runs` + lease/heartbeat) | требования (retries, priority, pause/resume, crash recovery) закрываются таблицей; ни Redis, ни Celery не нужны для single-user |
| Realtime | WebSocket `/api/events` (+ история событий в БД) | уже отработанный паттерн; UI переподключается и перечитывает состояние |
| UI | Статический single-page (HTML/JS без сборки), отдаётся тем же FastAPI | ноль build-инфраструктуры, работает локально и как PWA; «premium dark» стилем, не admin-template |
| Auth | Bind на 127.0.0.1 + локальный токен (генерируется при первом старте, хранится в data dir, UI получает через cookie/login) | single-user, но backend не открыт; наружу — только VPN/Tailscale |
| Secrets | Ключи провайдеров шифруются at rest (Fernet, ключ в data dir, права 600), в UI и логах — только маска `sk-…last4` | требование раздела 42 |
| OpenCode | Path B для MVP: свой лёгкий runtime; адаптер OpenCode — Phase 2 (см. отчёт исследования) | MVP-задачи (чат-задача агента с retries/checkpoint) не требуют полного coding-runtime |

## 1. Компоненты

```text
 ui/ (static SPA)
   │  HTTP + WS, токен
   ▼
 Control API (FastAPI)  ←  единственная точка входа, всё через /api/*
   │
   ├── Model Registry ── Provider Adapters (openai_compat | anthropic | …)
   ├── Agent Store    ── агенты: модель, prompt, инструменты, лимиты
   ├── Task Engine    ── persistent queue в БД, worker-цикл, retries, checkpoints
   ├── Scheduler      ── now | at | daily | interval (тик раз в 30 с, catch-up после reboot)
   ├── Approvals      ── очередь подтверждений (архитектура для dangerous actions)
   ├── Metrics        ── psutil: CPU/RAM/диск (+GPU при наличии), сэмплы в БД
   └── Event Bus      ── события в WS и в таблицу events
```

Слабая связность: каждый компонент — модуль со своим API; UI ничего не знает о
внутренностях, только `/api/*`.

## 2. Provider layer

Единый интерфейс адаптера:

```python
class ProviderAdapter:
    async def chat(self, model: str, messages: list[dict], **kw) -> ChatResult  # text, usage, finish
    async def health(self) -> Health        # ok | offline | error(text)
    async def list_models(self) -> list[str]
```

MVP-адаптеры:
- `openai_compat` — любой OpenAI-совместимый endpoint (llama.cpp server, Ollama,
  vLLM, LM Studio, LiteLLM, OpenRouter, GLM/Z.ai …): `base_url`, `api_key?`.
- `anthropic` — облачный adapter (Messages API), `api_key`.

Новый провайдер = один класс-адаптер, регистрируется в словаре `ADAPTERS`.

## 3. Схема БД (MVP-подмножество раздела 40)

```text
providers    id, name, kind(openai_compat|anthropic), base_url, api_key_enc, created_at
models       id, provider_id, name, alias UNIQUE, kind(local|cloud), context_window,
             caps JSON(vision,tools,reasoning,coding), price_in, price_out,
             status(unknown|online|offline|error), status_detail, last_check,
             bench JSON(prompt_tps,gen_tps,latency_ms,tested_at)
agents       id, name, role, system_prompt, model_id, fallback_model_id?,
             tools JSON, max_steps, max_tokens, budget_usd, permissions JSON,
             enabled, created_at
tasks        id, title, prompt, agent_id, status(draft|queued|running|paused|
             waiting_approval|completed|failed|stopped), priority, max_retries,
             schedule_id?, created_at, updated_at
task_runs    id, task_id, attempt, status(queued|leased|running|completed|failed|
             stopped), worker_lease_until, checkpoint JSON(messages,step,note),
             result TEXT, error TEXT, model_alias, tokens_in, tokens_out,
             cost_usd, started_at, finished_at
schedules    id, name, kind(once|interval|daily), at_time?, interval_minutes?,
             daily_time?, next_run_at, enabled, task_template JSON, last_fired_at
run_events   id, run_id, ts, level(info|warn|error), kind, message, data JSON
approvals    id, task_id?, run_id?, kind, preview, status(pending|approved|
             rejected), decided_by, decided_at, created_at
system_metrics id, ts, cpu_pct, ram_used_mb, ram_total_mb, disk_used_gb,
             disk_total_gb, gpu JSON?
events       id, ts, kind, data JSON        (история для ленты активности)
settings     key PK, value_enc              (токен UI, ключ шифрования — вне БД)
```

## 4. Task Engine (раздел F: persistent queue)

- `POST /api/tasks` создаёт task; `run now` ставит `task_runs(status=queued)`.
- Worker-цикл (asyncio-задача в том же процессе): берёт `queued` run с
  наименьшим priority/id, ставит `leased` + `worker_lease_until = now()+90s`,
  затем `running`; heartbeat продлевает lease каждые 30 с.
- **Crash recovery**: при старте и периодически — runs с истёкшим lease
  возвращаются в `queued` (attempt+1, если attempt ≤ max_retries, иначе `failed`).
  Задача переживает reboot: состояние только в БД.
- **Checkpoint**: после каждого шага модели messages+step пишутся в
  `checkpoint`; продолжение после рестарта — с checkpoint, не с нуля.
- **Stop/Pause**: флаг в БД; worker проверяет между шагами. Stop → `stopped`;
  Pause → `paused` (возврат в очередь по Resume с тем же checkpoint).
- **Retries**: ошибка провайдера → экспоненциальная пауза → новый attempt до
  `max_retries`; после — `failed` с человекочитаемой ошибкой (раздел 55).
- Выполнение шага: сообщение → модель агента (через registry+adapter); при
  ошибке primary и наличии fallback_model — попытка через fallback (лог в events).

## 5. Scheduler (раздел G)

Тик раз в 30 с: `schedules WHERE enabled AND next_run_at <= now()` → создать
task+run из `task_template` → пересчитать `next_run_at` (once → disabled;
daily → следующий день в `daily_time`; interval → now+interval). После reboot
пропущенные схемы срабатывают один раз (catch-up), не N раз.

## 6. Control API (контракт для UI)

Все ответы JSON; auth: заголовок `X-BCC-Token` (UI хранит в localStorage после
`/api/login`). Ошибки: `{error: {message, hint?, actions?}}` — не голые 500.

```text
POST /api/login {token}                    → {ok}
GET  /api/system                           → метрики сейчас + история 15 мин + health компонентов
GET  /api/providers/kinds                  → ["openai_compat","anthropic"]
GET/POST /api/providers, DELETE /api/providers/{id}      (api_key принимается, наружу — маска)
GET/POST /api/models, PATCH/DELETE /api/models/{id}
POST /api/models/{id}/check                → health-проверка endpoint
POST /api/models/{id}/test                 → мини-benchmark (короткий prompt; tps, latency) + запись в bench
POST /api/models/discover {extra_urls?}    → обнаружение локальных моделей: опрос известных портов
                                             (llama.cpp:8080, Ollama:11434, LM Studio:1234, vLLM:8000,
                                             LiteLLM:4000, SGLang:30000, tg-webui:5000) + скан диска на
                                             *.gguf (BCC_MODELS_DIRS; по умолчанию /opt/bossman/models и др.)
GET/POST /api/agents, PATCH/DELETE /api/agents/{id}
GET  /api/tasks?status=…                   → списки для очереди-экрана
POST /api/tasks {title,prompt,agent_id,run_now?,schedule?,priority?,max_retries?}
GET  /api/tasks/{id}                       → task + runs + последний результат
POST /api/tasks/{id}/run|stop|pause|resume|retry
GET  /api/runs/{id}                        → run + checkpoint meta (без секретов)
GET  /api/runs/{id}/events?after=…         → история логов run
GET/POST /api/schedules, PATCH/DELETE /api/schedules/{id}
GET  /api/approvals?status=pending, POST /api/approvals/{id} {approve,by}
GET  /api/activity                         → последние события (лента Home)
WS   /api/events                           → live: task.*, run.log, model.*, approval.*, system.metrics
```

События WS: `{kind, ts, ...}` c kind из:
`task.created|queued|started|progress|completed|failed|stopped|paused`,
`run.log`, `model.status`, `approval.created|decided`, `system.metrics`.

## 7. UI (страницы MVP)

Home (статус-строка, Quick Task, Running, Models, Approvals, System, Recent
activity — раздел 37 брифа) · Models (registry + add provider/model + Check/Test)
· Agents (карточки, create/edit) · Tasks (композер + очередь по статусам + карточка
задачи с live-логом и кнопками Stop/Pause/Resume/Retry) · Schedules · System
(метрики + health) · Settings (токен, маскированные ключи). Тёмная тема по
умолчанию, светлая — переключателем; Ctrl/Cmd+K — палитра из основных действий.

## 8. Safety (раздел J)

- Опасных авто-действий в MVP нет: у MVP-агентов нет инструментов записи/отправки.
- Архитектура подтверждений уже в БД/API/UI (`approvals`) — Phase 2 навешивает
  на неё email/deploy/invoice.
- Ключи шифруются at rest, маскируются везде; в run_events и логи не попадают.
- Bind 127.0.0.1; токен обязателен для всех /api/* кроме /api/login.

## 9. OpenCode — итоги исследования upstream (28.08.2026)

- **Upstream**: `github.com/sst/opencode`, лицензия **MIT** — код и идеи можно
  переиспользовать свободно (сохранить текст лицензии при копировании кода).
- **Факт-архитектура**: монорепо Bun + TypeScript (Effect-TS, Drizzle/SQLite);
  TUI/desktop/web — клиенты к HTTP-серверу. `opencode serve` — headless-сервер с
  OpenAPI 3.1 (`/doc`), SSE-событиями, REST-сессиями (create/fork/abort/revert,
  child-сессии, todo), permission-гейтингом (allow/ask/deny per-tool, ответ на
  pending permission тоже через API) и 75+ провайдерами, включая нативный
  OpenAI-compatible `baseURL` (llama.cpp, Ollama, LM Studio, свой шлюз).
- **Программное управление извне** — да: полный цикл через REST+SSE, официальный
  TS SDK; из Python достаточно HTTP-клиента. Проект очень активен (релизы каждые
  1–3 дня) → адаптер держать тонким и версионированным.

**Решение**: для MVP этой сессии — **Path B** (свой лёгкий task-runtime: MVP-задачи
— это чат-задачи агента с очередью/checkpoint, полный coding-runtime не нужен, а
Bun-подпроцесс — лишняя зависимость до приезда железа). Для **coding-задач в
Phase 2 — Path A**: subprocess `opencode serve` + тонкий adapter за тем же Control
API (`kind: opencode` у executor), что закроет bash/edit/grep/LSP и permissions
готовой зрелой системой вместо её дублирования.

## 10. Связь с уже сделанным

`bossman-core` (петля агентов с политиками облака) и Command Center — два слоя
одного будущего продукта: Core отвечает за приватность/петлю инструментов,
Command Center — за управление моделями/задачами/расписаниями. В Phase 2 Core
подключается как ещё один «executor» за тем же Control API.
