# Source map: что уже есть и что соединить

Read-only review трёх помощников и сверка интегратором. База `e7d6b6d319e75609c3457da4910b0fbd2040301a`, 22.10.2026. Номера строк ориентировочные для этой базы; актуальную реализацию перечитать. Продуктовые тесты, модели, GUI и paid calls в этом обзоре не запускались.

| Контур | Существующий путь / API | Вывод для Аргона |
|---|---|---|
| Agent definitions | `command-center/bcc/db.py`, `bcc/api.py`; `/api/agents` GET/POST/PATCH/DELETE | Расширить профиль; не создавать второй registry |
| Task execution | `bcc/engine.py`: TaskEngine enqueue/stop/recovery; `/api/tasks`, preflight, events, run detail | Основная очередь, durable task state и approvals уже есть |
| Session orchestration | `bcc/features/agent_opencode.py`: `/api/agent/sessions`, messages, events, stream, stop | Использовать общую session и route; CODE path отдельно проверить |
| Router/catalog | `agent_opencode.py`: `/api/agent/models`, `/api/agent/router/select`; `bcc/v2/model_router.py` | Согласовать нынешние routing semantics, не писать третий router |
| Coding workers | `bcc/features/coding_tasks.py`: `/api/coding-tasks/workers`, readiness, local-coders, task, receipt, cancel, apply | Уже есть isolates/allowed_paths/patch approval; cloud spending проверить отдельно |
| Cloud consultation | `bcc/features/tools_cloud.py`: ToolSpec `cloud.ask`; `agent_extras.py`: GET/PUT `/api/agent/cloud-ask` | Bound approval, price/STOP/budget существуют именно здесь |
| Orchestra UI | `bcc/features/nl_orchestra.py`, `agentmap.py`, `/api/orchestras`, `/api/agentmap` | Состав и graph edges ещё не managed delegation |
| Mission plan | `bcc/features/missions.py`, `bcc/v2/task_graph.py` | Проверить scheduler dependencies, не только topological compile |
| Delegation guard | `bcc/self_improve/delegation.py` | Имеются depth<=2, cycle/subset checks; доказать вызов guard в реальном dispatch |
| Desktop | `command-center/ui/pages.js`, `ui/api.js`, `ui/pages/coding.js` | Agent cards/modal, Tasks board и worker chips уже есть |
| Mobile | `apps/bossman-mobile/bossmobile/api.py`, `ui/app.js` | Проксирует тот же backend; native local-agent filter сохранить |
| Memory | `bcc/features/tools_memory.py`: `/api/memory/write`, search, expand; tool registry `memory.*` | Записать lesson штатно, проверить именно её retrieval; конфиг не менять |
| Existing agent teaching | `.agents/skills/agent-method/SKILL.md`, `.agents/skills/bossman-elder-brother-agent/SKILL.md`, `tools/agent_school/playbook.json` | Дополнить практикой делегирования, не дублировать runtime skills |
| Verified learning | `learning/lesson_format.py`, `learning/lessons.py`, `docs/learning/AGENT_LEARNING_TRACE_POLICY.md` | Заметка vault и VERIFIED LessonBook — разные evidence уровни |

## Конкретные пробелы на этой базе

1. **Typed agent-visible spawn/delegate ToolSpec не найден** в просмотренных поверхностях. `cloud.ask` и roster недостаточны для утверждения «Gemma вызывает managed agents». Если Аргон уже добавил tool в dirty/new code, проверить его фактическое имя/schema/dispatch, а не писать ещё один.
2. **WebSocket STOP**: `_pump_in` в `agent_opencode.py`, ветка `action == 'stop'`, около 2719, испускает события `task.failed`/`agent.stopped`; canonical HTTP `/api/agent/sessions/{id}/stop` около 2244 вызывает фактическую остановку. Требуется один cancel handler и regression на все UI surfaces. Это finding исходника, не подтверждённая атака.
3. **Idempotency**: CODE branch создаёт coding task до lookup `client_request_id` основного messages path. В `/coding-tasks` создаётся новый случайный ID; общий durable intent claim до side effect не обнаружен. Mobile генерирует новый client_request_id внутри `ask`; retry unknown outcome должен повторять исходный ключ.
4. **DAG**: `missions._compile_plan` компилирует зависимости, но `_tick` enqueue draft tasks по free slots без видимого runtime dependency check. Порядок списка не гарантирует завершение prerequisites при concurrency>1.
5. **Shared budgets**: cloud.ask проверяет approval/route hash/known price/STOP; в create coding-task не обнаружено такого же общего spend reservation gate. Нельзя распространять гарантию одного инструмента на все cloud paths. Это статический пробел, не доказательство фактического расхода.
6. **Readiness**: enabled и credentials present означают настройки, а не успешный live вызов. Каталог free/price_display содержит статические сведения. Разделить configured/health/price freshness/quota/last successful run.
7. **Events/cost state**: AgentSessionRuntime events, counters и task/session mappings частично in-memory. Durable task events есть, но восстановление всей child tree/stream и budget после restart нужно доказать. Ввести epoch/snapshot resync, если текущая реализация этого не делает.
8. **UI стоимость**: fallback coding UI может показать FREE при отсутствующем cost_display, а строка budgets задана статически. Возвращать N/A и реальные effective server caps.
9. **Model identity**: receipt должен подтверждать actual model/agent после router → resolver → executor. Запрошенная модель и реально исполнившая не всегда автоматически равны.

## Независимая проверка

Три помощника отдельно изучили reuse/API, desktop/mobile UX и memory/learning. Это независимые участки source review, **не три live-аудита модели**. Их выводы проверены по исходным поверхностям и перенесены сюда. Без claims о полном security/freeze/доходе.

Прежние broad security findings частично получили implementation commits после раннего аудита. Свежую приёмку проводить на exact candidate SHA. Этот пакет не переоткрывает закрытое без фактов и не перекрашивает code fix в runtime PASS.
