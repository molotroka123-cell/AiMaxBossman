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

---

## Приложение: потребление RAM/VRAM — BOSSMAN Core против OpenCode (запрос пользователя)

Замер выполнен на этом окружении (idle, без загруженных LLM-моделей).

| Компонент | RAM (RSS) idle | VRAM | Примечание |
|---|---|---|---|
| **BOSSMAN Core** (Python/FastAPI, все 15 фич, SQLite, worker+scheduler+metrics) | **~105 МБ** | 0 | control-plane; моделей в себе НЕ держит |
| Postgres (в бою вместо SQLite) | +~30–50 МБ | 0 | опционально; на SQLite не нужен |
| Playwright/Chromium (браузер-сессия) | +~150–300 МБ на сессию | 0 | только когда открыта browser-сессия |
| OpenCode `opencode serve` (Bun+TS, для сравнения) | ~80–200 МБ (оценка по документации upstream, бинарь в этом окружении не установлен) | 0 | тоже control/execution-plane без весов |

### Вывод

**BOSSMAN Core сам по себе НЕ конкурирует за VRAM.** Ключевой архитектурный
принцип (раздел 6 ТЗ, Resource Brain): control-plane не держит веса моделей —
их держит llama-swap/llama.cpp отдельным процессом, и именно его учёт ведёт
Resource Brain (feature 12), сохраняя `reserve_floor_mb` (по умолчанию 16 ГБ из
128) свободными. control-plane BOSSMAN (~105 МБ) сопоставим с `opencode serve`
и на порядки меньше любой локальной модели (Qwen 35B Q8 ≈ 37 ГБ, gpt-oss-120b ≈
61 ГБ). Разница control-plane'ов (десятки-сотни МБ) в бюджете 128 ГБ
пренебрежимо мала — «сильно больше», чем OpenCode, BOSSMAN не потребляет.

### Как перемерить на боевой машине (с opencode)

```bash
# BOSSMAN Core idle
python3 -c "import subprocess as s; print(sum(int(open(f'/proc/{p}/status').read().split('VmRSS:')[1].split()[0]) for p in s.check_output(['pgrep','-f','bcc.app']).decode().split())//1024,'МБ')"
# opencode serve idle
opencode serve & sleep 3; ps -o rss= -p $(pgrep -f 'opencode serve') | awk '{print $1/1024" МБ"}'
# VRAM обеих (должно быть 0 — веса у llama-swap):
rocm-smi --showmeminfo vram   # или nvidia-smi
```

Resource Brain (feature 12) в бою держит учёт RAM/VRAM моделей, KV-cache,
браузер/терминал/OpenCode-процессов и не даёт исчерпать 128 ГБ (политики
balanced/performance/low_power, reserve_floor).
