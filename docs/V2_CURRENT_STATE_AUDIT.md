# V2 — аудит текущего состояния (до начала работ)

Дата: 28.08.2026. База: ветка `claude/bossman-control-v03-43igbk`, коммит `cf1863a`, 27 pytest зелёные.

## Что уже РЕАЛЬНО работает (проверено живыми прогонами, не отчётами)

| Область | Факт | Где |
|---|---|---|
| Провайдеры | `openai_compat` (llama.cpp/Ollama/LM Studio/vLLM/LiteLLM/OpenRouter) и `anthropic`: chat с usage, health, list_models; человекочитаемые ошибки | `bcc/providers.py` |
| Реестр моделей | CRUD, health-check (online/offline/error), мини-benchmark (tok/s, latency) с записью в `models.bench` | `bcc/registry.py` |
| Обнаружение локальных | опрос 7 известных портов + скан диска на `*.gguf`; добавление в 1 клик из UI | `bcc/discovery.py` |
| Агенты | CRUD: модель, fallback-модель, system prompt, max_steps/max_retries | `bcc/api.py`, таблица `agents` |
| Task engine | persistent-очередь в БД: lease+heartbeat, crash recovery (проверен kill-ом сервера), checkpoint {messages,step}, retries с экспоненциальной паузой (переживает рестарт), fallback-модель, stop/pause/resume | `bcc/engine.py` |
| Scheduler | once/interval/daily, catch-up после рестарта (ровно один раз) | `bcc/scheduler.py` |
| Метрики | psutil CPU/RAM/disk сэмплы в БД + история; GPU=null в контейнере (код best-effort есть) | `bcc/metrics.py` |
| События | шина → WS `/api/events` + история в таблицах | `bcc/events.py` |
| Approvals | таблица+API+экран+события; **ничто их пока не создаёт автоматически** | `bcc/approvals.py` |
| Auth/секреты | токен (600), Fernet-шифрование ключей, маски везде | `bcc/auth.py`, `bcc/secrets.py` |
| UI | 8 страниц, live по WS, тёмная/светлая, Ctrl+K, mobile 390px, PWA; QA в Chromium — 0 ошибок консоли | `ui/*` |

## Что существует только как UI / częściowo

- **Approvals** — экран и API живые, но поток «опасное действие → approval» не подключён ни к чему (в MVP нет опасных действий). Это фундамент для Reviewer Gate (08), NL Orchestration (11), Governor (03).
- **GPU-метрики** — код есть, в контейнере всегда `null` (нет /dev/dri).
- **caps моделей** (vision/tools/coding) — хранятся и показываются, но никем не используются при выборе модели → вход для Router (02).
- **price_in/price_out → cost_usd** — считается, но лимитов/остановок нет → вход для Governor (03) и Resource/бюджетов.

## Mock / чего нет совсем

- Нет: Mission/Objective, Orchestra/teams, Skills, Benchmark Lab (только мини-bench), Replay/Fork, Agent Map, worktree-sandboxes, Reviewer Gate, Browser live view (Playwright вообще не подключён к продукту — только в QA-скриптах), NL-оркестрация, Resource Brain (метрики есть, решений нет), KPI, Self-Healing (есть только retry/fallback в engine), выделенный mobile command mode (адаптив есть, спец-режима нет).
- Инференс в среде разработки — только mock OpenAI-совместимые endpoint'ы (реального GPU/моделей в контейнере нет). Итоговый smoke на реальном endpoint — на машине пользователя (§30 мастер-промпта это допускает).

## Какие API уже есть (переиспользовать, не дублировать)

`/api/login`, `/api/system`, `/api/providers[/kinds]`, `/api/models` (+`/check`,`/test`,`/discover`), `/api/agents`, `/api/tasks` (+`run/stop/pause/resume/retry`), `/api/runs/{id}[/events]`, `/api/schedules`, `/api/approvals`, `/api/activity`, WS `/api/events`. Формат ошибок `{error:{message,hint}}` — единый exception handler.

## Какие DB-сущности уже есть

`providers, models, agents, tasks, task_runs, schedules, run_events, approvals, system_metrics, events, settings` (SQLAlchemy 2 async, SQLite/Postgres). `task_runs.checkpoint` JSON уже хранит messages/step — основа для Replay/Fork (05).

## Частично реализовано из 15 функций

| # | Функция | Что уже есть |
|---|---|---|
| 01 Autopilot | task engine + scheduler + checkpoints — нет Mission-слоя |
| 02 Router | caps/bench/status в реестре — нет скоринга и выбора |
| 03 Governor | run_events/attempts в БД — нет наблюдателя |
| 04 Benchmark Lab | мини-bench одной кнопкой — нет фоновых прогонов/истории/сравнения |
| 05 Replay/Fork | checkpoint в task_runs — нет lineage/fork |
| 08 Reviewer Gate | max_retries в engine — нет роли reviewer |
| 12 Resource Brain | system_metrics + RAM-оценки — нет reservations/политик |
| 14 Self-Healing | retry+fallback+crash-recovery в engine — нет детекции падений endpoint'ов/эскалации |
| 15 Mobile | адаптив до 390px — нет command-режима и тестов 320–430 |

## Что нельзя ломать

1. 27 существующих тестов и все текущие API-пути (UI на них живёт).
2. Персистентность очереди (lease/recovery) — любые правки engine покрывать тестами.
3. Маскирование секретов и токен-auth.
4. `bossman-core/` и `bossman-infra/` — отдельные продукты, V2 их не трогает.
5. Формат ошибок и конверт WS-событий (`kind`, `ts` ставит шина).

## Технические решения для V2 (чтобы 15 агентов не передрались)

- **Схему БД для всех 15 функций закладывает лид одним коммитом** (см. V2_SHARED_CONTRACTS) — агентам менять общую схему запрещено, только использовать.
- **Точки расширения**: каждый агент кладёт backend в свой модуль `bcc/features/<имя>.py` (FastAPI router, авто-подключение), UI — в `ui/pages/<имя>.js` (реестр страниц), тесты — `tests/test_<имя>.py`. Общие файлы (`api.py`, `pages.js`, `db.py`, `engine.py`) в feature-ветках не редактируются; нужен хук в engine — лид добавляет его в core заранее.
- Среда: инференс — mock-endpoint'ы; браузер — Chromium `/opt/pw-browsers/chromium`; git worktrees поддерживаются.
