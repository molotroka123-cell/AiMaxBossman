# V2 — Shared Contracts (обязательны для всех 15 агентов)

Нарушение контракта = функция не принимается интеграцией. Общую схему БД и общие
файлы меняет ТОЛЬКО лид (они уже заложены в V2-core коммите). Агент работает в
своём worktree и только в своих файлах (см. §7 «Границы модулей»).

## 1. Сущности → таблицы

Заложены лидом в `bcc/db.py` (create_all + идемпотентные ALTER для новых колонок).

```text
missions              id, title, goal, status, duration_minutes, max_workers,
                      cloud_budget_usd, spent_usd, plan JSON, progress REAL,
                      kpi_targets JSON, meta JSON, started_at, finished_at,
                      created_at, updated_at
kpi_history           id, mission_id, key, value, delta, source_task_id, ts
orchestras            id, name, mode(sequential|parallel|manager|debate|review_loop),
                      config JSON, created_at
orchestra_members     id, orchestra_id, agent_id, role(manager|worker|reviewer), position
skills                id, name, slug UNIQUE, description, current_version_id, created_at
skill_versions        id, skill_id, version, input_schema JSON, output_schema JSON,
                      required_tools JSON, process TEXT, permissions JSON, created_at
benchmarks            id, model_id, kind(quick|full), status, results JSON,
                      error, started_at, finished_at, created_at
checkpoints           id, run_id, step, messages JSON, note, created_at
session_forks         id, source_run_id, checkpoint_id, new_task_id, changes JSON, created_at
resource_reservations id, kind(ram|gpu), amount_mb, holder_kind(model|task|benchmark),
                      holder_id, status(held|released|expired), detail,
                      created_at, released_at, expires_at
interventions         id, target_kind(task|run|mission|model), target_id, reason,
                      action(paused|stopped|switched|throttled|escalated),
                      detail, created_at
recovery_attempts     id, target_kind, target_id, failure, action, attempt,
                      status(started|completed|escalated), detail, created_at
```

Расширения существующих таблиц (уже добавлены лидом):

```text
tasks     + mission_id, orchestra_id, skill_version_id, kind(generic|classification|
            coding|review|research|browser|vision), parent_task_id, workspace_path, meta JSON
task_runs + route JSON (объяснение выбора модели), reservation_id
agents    + workspace (у agents уже есть: role, tools JSON, permissions JSON, budget_usd)
```

Существующие таблицы MVP (`providers, models, agents, tasks, task_runs, schedules,
run_events, approvals, system_metrics, events, settings`) — менять запрещено.

## 2. Статусы (единый словарь)

```text
draft · planning · queued · running · waiting_approval · paused · retrying ·
degraded · failed · completed · cancelled
```

Missions используют весь словарь; tasks/runs — подмножество MVP + `retrying|degraded|cancelled`.
Никаких собственных статусов («done», «ok», «error») в БД.

## 3. События (шина `bus.emit(kind, **data)`; kind и ts ставит шина)

К существующим MVP-событиям добавляются РОВНО эти kind'ы:

```text
mission.created|started|progress|paused|completed|failed
task.paused|resumed                       (созд./queued/started/progress/completed/failed уже есть)
agent.started|tool_call|warning|failed|completed
router.route_selected|fallback            data: task_id, model_id, alias, reason, scores
governor.intervention                     data: intervention_id, target_kind, target_id, reason, action
model.failed|degraded                     (loaded/unloaded/status уже есть)
resource.reserved|released|denied         data: reservation_id|kind, amount_mb, holder
checkpoint.created                        data: checkpoint_id, run_id, step
session.forked                            data: fork_id, source_run_id, new_task_id
evaluation.completed                      data: task_id, verdict(pass|fail), score, reasons
recovery.started|completed|escalated      data: attempt_id, target, failure, action
benchmark.started|progress|completed|failed
skill.created|updated|assigned
```

Поля данных не должны называться `kind`/`ts` (шина перезапишет) — используйте
`provider_kind`, `log_kind` и т.п. (как в MVP).

## 4. Permission model (строковые константы, `bcc/permissions.py` в core)

```text
filesystem.read  filesystem.write  terminal.read  terminal.run
git.read  git.write  browser.read  browser.control
model.load  model.unload  email.draft  email.send
deploy.preview  deploy.production  invoice.create  payment.read  settings.write
```

Опасные по умолчанию (требуют approval, если не выданы агенту явно):
`filesystem.write, terminal.run, git.write, browser.control, model.unload,
email.send, deploy.*, invoice.create, settings.write`.
Проверка — `bcc/permissions.py: is_dangerous(perm), agent_allowed(agent, perm)`.

## 5. API-конвенции (как в MVP, обязательны)

- Ошибки: HTTP-код + `{"error": {"message": str, "hint": str?}}` — через ApiError.
- Пагинация: `?limit=` (default 50, max 200) + `?after_id=`; ответ — список.
- ID:整 BIGINT autoincrement. Времена: naive-UTC ISO (как весь MVP), поля `created_at/updated_at`.
- Идемпотентность retry: POST-экшены (`/run`, `/pause`…) безопасны при повторе.
- Auth: всё под `X-BCC-Token` (роутер фичи получает это бесплатно, см. §7).
- Конверт WS: `{kind, ts, ...data}` — не менять.

## 6. Statuses/Events — правило записи

Любое изменение статуса сущности пишется ДВАЖДЫ: в таблицу (источник истины) и в
шину (для UI). Прямых записей в UI мимо шины нет.

## 7. Границы модулей (кто что трогает)

| Агент | Backend (только эти файлы) | UI | Тесты |
|---|---|---|---|
| 01 Autopilot | `bcc/features/missions.py` | `ui/pages/missions.js` | `tests/test_missions.py` |
| 02 Router | `bcc/features/router.py` | `ui/pages/routing.js` | `tests/test_router.py` |
| 03 Governor | `bcc/features/governor.py` | `ui/pages/governor.js` | `tests/test_governor.py` |
| 04 Benchmark Lab | `bcc/features/benchlab.py` | `ui/pages/benchmarks.js` | `tests/test_benchlab.py` |
| 05 Replay/Fork | `bcc/features/forks.py` | `ui/pages/forks.js` | `tests/test_forks.py` |
| 06 Agent Map | `bcc/features/agentmap.py` | `ui/pages/agentmap.js` | `tests/test_agentmap.py` |
| 07 Worktrees | `bcc/features/worktrees.py` | `ui/pages/worktrees.js` | `tests/test_worktrees.py` |
| 08 Reviewer Gate | `bcc/features/review_gate.py` | `ui/pages/reviewgate.js` | `tests/test_review_gate.py` |
| 09 Browser Live | `bcc/features/browser_live.py` | `ui/pages/browser.js` | `tests/test_browser_live.py` |
| 10 Skills | `bcc/features/skills.py` | `ui/pages/skills.js` | `tests/test_skills.py` |
| 11 NL Orchestration | `bcc/features/nl_orchestra.py` | `ui/pages/orchestras.js` | `tests/test_nl_orchestra.py` |
| 12 Resource Brain | `bcc/features/resources.py` | `ui/pages/resources.js` | `tests/test_resources.py` |
| 13 Mission KPI | `bcc/features/kpi.py` | `ui/pages/kpi.js` | `tests/test_kpi.py` |
| 14 Self-Healing | `bcc/features/healing.py` | `ui/pages/healing.js` | `tests/test_healing.py` |
| 15 Mobile | — (backend не нужен; мелочи → `bcc/features/mobile.py`) | `ui/pages/mobile.js` + СВОЙ `ui/mobile.css` | `tests/test_mobile.py` (playwright) |

Общие файлы (`bcc/api.py, engine.py, db.py, events.py, ui/pages.js, app.js,
components.js, style.css` и пр.) в feature-ветках НЕ редактируются. Нужен новый
хук/колонка — записать в отчёт «Integration notes», лид добавит при интеграции.

## 8. Точки расширения (уже в V2-core)

**Backend** — `bcc/features/__init__.py` автоматически подключает каждый модуль
пакета: модуль экспортирует
`FEATURE = Feature(name, router: APIRouter|None, setup: async (svc)->None|None, tick: async (svc)->None|None, tick_seconds: float=0)`.
`router` монтируется под `/api` с токен-auth; `setup(svc)` зовётся на старте
(svc: db, bus, engine, registry, scheduler, approvals, settings);
`tick` — периодическая фоновая работа (Governor, Healing, Resource-expiry).

**Хуки engine** (`svc.engine.hooks`, списки корутин — регистрация в `setup`):

```text
pick_model(task, agent) -> model_id | None      # Router: перекрыть выбор модели
before_run(task, run) -> None | str("deny:…")   # Resource Brain: резерв/отказ
on_step(task, run, checkpoint_dict)             # чекпоинты в таблицу, Governor-фид
gate_completion(task, run, result_text) -> None | dict(verdict, reasons)
                                                # Reviewer Gate: не дать completed без PASS
on_failure(task, run, error_text)               # Self-Healing/Governor
after_run(task, run, status)                    # release ресурсов, KPI
```

Engine вызывает их в этом порядке; исключение хука логируется и не роняет run.

**UI** — `ui/pages/index.js`: `export const FEATURE_PAGES = [...]` (лид держит
список, агент добавляет ровно одну строку импорта своей страницы — конфликт
одной строки интеграция сольёт). Страница экспортирует объект
`{id, title, icon, nav: 'primary'|'more', render(ctx), onEvent(ev)}` — тот же
интерфейс, что страницы MVP; хелперы — из `../components.js`, API-клиент — из
`../api.js` (новые методы фичи добавляет в СВОЙ файл через `api.raw(path, opts)`).

## 9. Тестовая изоляция (§30 мастер-промпта)

Модели — только фейковые адаптеры/mock-endpoint'ы (`tests/conftest.py: FakeAdapter`
уже есть); БД — sqlite в tmp_path; git — временные репозитории; браузер —
`/opt/pw-browsers/chromium`. Секреты/креды в тестах запрещены. Никаких
хардкоженных результатов benchmark/score.

## 10. Формат отчёта агента

`docs/V2_AGENT_REPORTS/XX_FEATURE.md` по шаблону §36 мастер-промпта; proof —
`docs/V2_PROOFS/XX_*.md` по шаблону §37. `[x]` только за реально выполненную
проверку. Ложный `[x]` = функция отклоняется целиком.
