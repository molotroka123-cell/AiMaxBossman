# BOSSMAN V2 — Implementation Report

> **Исторический отчёт волны V2** (коммит `24d24eb`, 110 тестов).
> Актуальное состояние — [`V2_1_IMPLEMENTATION_REPORT.md`](V2_1_IMPLEMENTATION_REPORT.md).

Дата: 28.08.2026. Ветка: `claude/bossman-control-v03-43igbk` (интеграционная метка
`feature/bossman-command-center-v2`). Тесты: **110 pytest passed**.

## Architecture

Единый BOSSMAN control-plane на существующем Command Center (FastAPI + SQLAlchemy 2
async, SQLite/Postgres, persistent-очередь, WS-события). V2 добавляет:

- **Хуки движка** (`bcc/engine.py`): `pick_model`, `before_run`, `on_step`,
  `gate_completion`, `on_failure`, `after_run` — точки, куда 15 функций
  подключаются, не переписывая ядро.
- **Загрузчик фич** (`bcc/features/__init__.py`): каждая функция — модуль
  (`FEATURE = Feature(name, router, setup, tick, tick_seconds)`), авто-монтируется
  под `/api` с токен-auth, регистрирует хуки в `setup`, ведёт фоновую работу в `tick`.
- **Worker Pool** (`BCC_WORKERS`, default 3) + **Hard Cancel** (Stop рвёт активный
  HTTP-inference через отмену asyncio-задачи).
- **Пак пользователя** (`bcc/v2/*`, `.agents/skills/*`): готовая чистая логика
  (router-скоринг, governor, resource_brain, reviewer_gate, kpi, replay, recovery,
  browser_control, terminal_control, skill_library, mcp_hub, openrouter,
  capability_probe, agent_graph, opencode_bridge) — LOCKED-решения приняты,
  обёрнуты в feature-адаптеры.

## 15 функций — что реально работает

| # | Функция | Механизм |
|---|---|---|
| 01 | Missions | цель→план→задачи; tick держит ≤max_workers, прогресс, таймаут; start/pause/resume/stop; переживает рестарт |
| 02 | Router | скоринг по caps/health/bench/успехам/бюджету → хук pick_model; route в task_runs.route; explain/preview API |
| 03 | Governor | on_failure/on_step: повтор ошибки (backstop за max_retries), бюджет, no-progress, stuck → interventions + stop/pause/escalate |
| 04 | Benchmark Lab | фон (одна за раз), реальные TTFT/tok-s/latency/stability замеры; compare/recommendations из данных; failed-путь |
| 05 | Replay/Fork | checkpoints на каждый шаг → fork новой задачей с override инструкции/агента/модели; lineage; оригинал неизменен |
| 06 | Agent Map | граф из реальных данных (агенты/оркестры + живой статус по run'ам); рёбра manager→workers/reviewer |
| 07 | Terminal | 3 режима sandbox/project_host/system_admin, AUTO/ASK/DENY, cwd в allowed_roots, kill/stdin/live-output |
| 07 | OpenCode | health/attach/abort/diff (PARTIAL — нужен бинарь opencode) |
| 08 | Reviewer Gate | gate_completion: Coder→FAIL→фидбек→fix→PASS; эскалация→waiting_approval+approval; override |
| 09 | Browser | Playwright DOM-first; политика (payment/wallet→deny, login→ask); Take Over блокирует агента; скриншоты |
| 10 | Skills+MCP | discovery .agents/skills (без рекурсии); run влияет на prompt; versions/clone/export/import/assign; MCP реестр + AUTO/ASK/DENY |
| 11 | NL Orchestration | детерминированный разбор RU/EN; сверка имён с реестром; ничего до confirm |
| 12 | Resource Brain | before_run/after_run: резерв/defer по политике; enforce opt-in; expiry-tick |
| 13 | KPI | kpi_history (value после delta, прогресс=среднее долей); защита чужой миссии; авто-KPI по task.completed |
| 14 | Self-Healing | окно сетевых ошибок→degraded→recovered; generic /healing/report; лимит→эскалация |
| 15 | Mobile | командная страница (UI-этап) |

## What is partial

- **OpenCode (07)**: клиент готов, health честно возвращает unavailable без
  установленного `opencode serve`. Полный цикл — на машине с бинарём.
- **Mobile (15)** и **UI всех функций**: backend готов и протестирован; страницы
  `ui/pages/*.js` добавляются отдельным этапом поверх стабильных API.
- **Реальные LLM-прогоны**: в этом окружении нет GPU/моделей — использованы
  mock OpenAI-совместимые endpoint'ы (§30 мастер-промпта это допускает).
  Итоговый smoke на реальном llama-swap — на машине пользователя.

## Migrations

Схема V2 — в `bcc/db.py` (12 новых таблиц: missions, kpi_history, orchestras+members,
skills+skill_versions, benchmarks, checkpoints, session_forks, resource_reservations,
interventions, recovery_attempts) + runtime-таблицы пака (browser/terminal/mcp/
opencode/catalog/capability/evaluations) на той же metadata. Новые колонки
существующих таблиц — идемпотентный ALTER в `Database._migrate()`. Один owner на
таблицу (дубли пака удалены в пользу core).

## API changes

Добавлено ~70 endpoint'ов под `/api` (missions, router, governor, resources, skills,
mcp, terminal, benchmarks, browser, agentmap, orchestras, forks, healing, openrouter,
opencode). Все под токен-auth, единый формат ошибок `{error:{message,hint}}`.
Полный список: `docs/V2_ORCHESTRATION_STATE.md` и код `bcc/features/*.py`.

## Tests

**110 pytest passed** (34 базовых MVP + 76 V2). Разбивка V2: core/hooks (7),
worker-pool/hard-cancel (3), router (5), missions+kpi (5), governor+reviewer (5),
resources+fork+healing (8), skills+mcp (7), terminal+agentmap (10),
openrouter (3), nl-orchestra (5), benchmark+opencode (6), browser (5),
discovery (6), пак-логика (14). Реальные проверки: worker-pool параллелизм,
hard-cancel <2.5с, crash-recovery рестартом, реальный Playwright-браузер,
OpenRouter sync/pin/probe через fake-provider.

## Failure injection (проверено)

- Endpoint down → healing degraded→recovered цикл (tick re-check).
- Repeated error → Governor stop (не бесконечный цикл).
- Reviewer FAIL → фидбек→fix→PASS; лимит→эскалация.
- Resource нехватка → defer по политике.
- Benchmark мёртвый endpoint → failed, фон не виснет.
- Hard cancel во время inference → run stopped за <2.5с.

## Persistence / reboot

Очередь и checkpoints только в БД. Проверено: kill сервера посреди run → после
рестарта задача доезжает (test_persistence, core-1). Retry-паузы, mission-состояние,
резервы — переживают рестарт (expiry-страховка резервов в tick).

## Security

- Токен-auth на всём `/api/*`; ключи провайдеров шифрованы (Fernet), маски везде.
- Permission-модель (`bcc/permissions.py`): опасные действия → approval.
- Terminal: НЕТ глобального «весь компьютер»; cwd ограничен allowed_roots;
  rm -rf //push --force→deny; system_admin→всегда ask.
- Browser: payment/wallet/bank→deny; login/upload→ask; Take Over блокирует агента.
- MCP: AUTO/ASK/DENY на инструмент; только назначенные — в контекст модели.
- Секреты не в логах; `.env`/data в `.gitignore`.

## Performance

BOSSMAN Core idle ~105 МБ RSS, 0 VRAM (control-plane не держит веса) — см.
`docs/V2_CURRENT_STATE_AUDIT.md` (сравнение с OpenCode). Benchmark и browser не
блокируют API (фон/отдельные сессии).

## Known limitations / next

1. UI-страницы функций — завершить (backend готов, API стабильны).
2. OpenCode полный цикл — на машине с `opencode serve`.
3. Полные кросс-сценарии §39–41 на реальных моделях (сейчас компоненты
   протестированы по отдельности + mock).
4. Alembic вместо идемпотентных ALTER — при росте команды.
5. Auth: перевести токен с localStorage/query на secure cookie (для доступа с
   телефона через Tailscale) — отмечено в бэклоге.

## Git

Ветка `claude/bossman-control-v03-43igbk`, множество мелких логических коммитов
(пак → core → 15 функций поблочно), каждый с зелёными тестами и push. Финальный
scorecard: `docs/V2_FINAL_SCORECARD.md`. Handoff: `docs/NIGHT_HANDOFF.md`,
`data/night_tasks.json`.

## Recommended V3

Единый canonical runtime (свести bossman-core tool-loop с Command Center engine),
Alembic + CI (tests→Playwright→secret-scan→build), System Snapshot (safe checkpoint
+ rollback перед автономными миссиями), Night/Harvest Mode (95% ресурсов ночью).
