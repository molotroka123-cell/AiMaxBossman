# BOSSMAN AI Command Center — отчёт по MVP-сессии

Дата: 27–28.08.2026. Код: [`command-center/`](../command-center/).
ТЗ: [`COMMAND_CENTER_SESSION_GOAL.md`](COMMAND_CENTER_SESSION_GOAL.md) ·
Архитектура: [`COMMAND_CENTER_ARCHITECTURE.md`](COMMAND_CENTER_ARCHITECTURE.md).

## Architecture

Web UI (static SPA) → Control API (FastAPI) → Model Registry / Task Engine /
Scheduler / Approvals / Metrics / Event Bus → Provider Adapters (openai_compat,
anthropic). Хранение — SQLAlchemy 2 async: SQLite по умолчанию, PostgreSQL через
`DATABASE_URL`. Очередь — persistent в БД (lease/heartbeat/crash recovery), без
внешнего брокера. Для coding-задач в Phase 2 выбран адаптер OpenCode
(`opencode serve`, MIT, headless HTTP API) — см. раздел 9 архитектуры.

## Implemented (MVP, раздел 62 ТЗ — всё)

- **A. Dashboard shell** — Главная, Модели, Агенты, Задачи, Расписания,
  Подтверждения, Система, Настройки; тёмная/светлая тема; Ctrl/⌘+K палитра;
  mobile-версия; вход по локальному токену.
- **B–C. Providers + Registry** — адаптеры `openai_compat` (llama.cpp, Ollama,
  LM Studio, LiteLLM, OpenRouter, GLM — любой совместимый endpoint) и
  `anthropic`; add/edit/delete, health-check (online/offline/error с
  человекочитаемой причиной), mini-benchmark (tok/s prompt+generation, latency)
  с записью в реестр.
- **D. Agents** — create/edit/delete: имя, роль, system prompt, primary модель,
  fallback-модель, max_steps/max_retries.
- **E–F. Task runner + persistent queue** — задачи из композера или Quick Task;
  очередь в БД переживает refresh страницы, рестарт worker'а и reboot: lease +
  heartbeat, crash recovery возвращает прерванные run'ы в очередь, checkpoint
  (messages/step) сохраняется после каждого шага; retries с экспоненциальной
  паузой (переживающей рестарт), fallback-модель при отказе primary; статусы
  running/queued/paused/failed/completed/stopped; Stop/Pause/Resume/Retry.
- **G. Scheduler** — once / daily / interval; catch-up после перезагрузки
  (пропущенное срабатывает один раз); enable/disable из UI.
- **H. Live logs** — run-события в реальном времени по WS + история из БД;
  лента активности на Главной.
- **I. System metrics** — CPU/RAM/диск (psutil), GPU best effort (на боксе с
  Radeon появится автоматически), история со спарклайнами, health компонентов
  (db, worker, scheduler, metrics).
- **J. Safety** — ключи провайдеров шифруются at rest (Fernet, ключ 600),
  в UI/API/логах только маска; токен-auth на всём /api/*; bind 127.0.0.1;
  архитектура approvals (таблица, API, экран, события) готова под Phase 2;
  опасных авто-действий в MVP нет.

## Проверено (Definition of Done, раздел 64 — 17/17)

Живой прогон: сервер + два OpenAI-совместимых endpoint'а (быстрый и медленный) +
облачный anthropic-провайдер. Добавление моделей → online/error; агент; задача →
completed с результатом, токенами и стоимостью; **kill сервера посреди run** →
после рестарта задача сама доехала до completed (recovery подтверждён логом
`attempt:1, recovered:true`); стоп активной задачи → stopped; interval-расписание
само создало и выполнило задачи; логи live/history; CPU/RAM/GPU на экране.
UI QA в Chromium: desktop 1440px и mobile 390px, все 8 страниц, создание задачи
из композера кликами, раскрытая карточка с live-логом, обе темы — ноль ошибок
консоли и ни одного упавшего запроса. Тесты: **22 pytest** (адаптеры,
персистентность, retry, scheduler, API, stop) — зелёные.

## How to run

```bash
cd command-center
pip install -e ".[dev]"
python -m pytest              # 22 passed
bcc                           # или python -m bcc.app
# токен печатается в консоли при старте → вставить на экране входа
```

| Параметр | Значение |
|---|---|
| Порт | 8800 (BCC_PORT), bind 127.0.0.1 (BCC_HOST) |
| Данные | ./data (BCC_DATA_DIR): bcc.db, token, secret.key |
| БД | SQLite по умолчанию; `DATABASE_URL=postgresql+asyncpg://…` для Postgres |
| Сервисы в процессе | API, queue-worker, scheduler (тик 30 с), metrics (тик 10 с) |

**Добавить локальную модель**: Модели → «+ Добавить модель» → провайдер
`openai_compat` + base_url (например `http://127.0.0.1:8080/v1` llama-swap) →
модель (имя как в endpoint, алиас, контекст) → Check.
**Добавить облачную**: то же с kind `anthropic` + api_key (хранится шифрованно,
показывается маской).
**Создать агента**: Агенты → «+ Новый агент» → имя, роль, system prompt, модель.
**Задача по расписанию**: Задачи → «По расписанию…» или Расписания → «+ Новое
расписание» (время / ежедневно / каждые N минут).

## Not implemented (по плану — Phase 2/3, раздел 63 ТЗ)

Orchestra/teams, smart routing и fallback-цепочки в UI, Playwright-панель,
OpenCode adapter, MCP manager, project memory, multi-model chat, долгие
автономные objectives (24h/7d) с replanning и evaluator, бюджеты облака с
авто-остановкой, второй узел ROG, интеграции уведомлений, глобальный поиск,
«Change model» посреди задачи.

## Known issues

- GPU-метрики в контейнере разработки недоступны (нет /dev/dri) — код best
  effort, на реальном боксе появятся; иначе честное «GPU: недоступно».
- `POST /api/models/{id}/test` меряет tok/s по usage ответа — на mock-endpoint
  цифры синтетические; реальные появятся на llama.cpp.
- Наивные ISO-времена трактуются UI как UTC (правится одной строкой в
  `components.js:parseTs`, если backend перейдёт на локальные).
- Эндпоинт выгрузки моделей llama-swap (панель из bossman-core) — сверить с
  актуальным README llama-swap; к Command Center не относится.

## Performance

Пустой сервер: ~60 МБ RSS; API < 10 мс на локальных запросах; тик worker'а не
нагружает CPU (< 1 %). Скорость инференса зависит от подключённых моделей.

## Next phase

1. Phase 2 из ТЗ: approvals в действии (email/deploy), OpenCode adapter для
   coding-задач, smart routing + fallback-цепочки в UI, MCP manager.
2. Свести с `bossman-core` (политики облака never/ask/allowed, петля
   инструментов) под общий Control API.
3. На железе: подключить llama-swap как openai_compat-провайдер, прогнать
   benchmark реальных моделей, включить GPU-метрики.
