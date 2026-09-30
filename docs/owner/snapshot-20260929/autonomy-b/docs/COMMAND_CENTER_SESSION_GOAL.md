# BOSSMAN AI COMMAND CENTER — Session Goal

> Цель, зафиксированная до приезда компьютера. Правила безопасности —
> в [`BOSSMAN_CLAUDE_CODE_SETUP.md`](BOSSMAN_CLAUDE_CODE_SETUP.md), они обязательны.

## Цель

Создать **BOSSMAN AI Command Center** — локальный dashboard и control plane для
управления всеми AI-моделями, агентами, задачами и автоматизациями на одной машине.
Лучше OpenCode именно для моего сценария, но не копия. OpenCode — архитектурный
референс: изучить фактический upstream и лицензию, не предполагать устройство по
памяти, не копировать код без права; если разумнее интегрироваться — предложить это.

## 1. Продукт

Разделы dashboard: Models · Agents · Orchestrator · Tasks · Schedules · Projects ·
Sessions · Tools · Browser · Terminal · Memory · Files · Usage · Costs · Logs ·
System · Settings.

Из одного интерфейса: подключать локальные и облачные модели; назначать роли;
создавать агентов и команды; давать задачу; запускать сейчас / на 24 часа / на
неделю / по расписанию / повторяющиеся; stop conditions; бюджеты и лимиты токенов;
инструменты агентам; видеть работу в реальном времени; вмешиваться; пауза/продолжить;
менять модель в середине задачи; переносить между локальной и cloud; расходы;
загрузка железа; результаты и история.

## 2. Основной принцип

UI простой, архитектура мощная. Никаких ручных JSON-конфигов на каждую задачу:
dropdown, switches, model picker, agent cards, task composer, scheduler, visual
orchestration, approval queue, live logs, charts. Все настройки — в понятном
формате и доступны через API.

## 3. Архитектура (предпочтительная концепция)

```text
Web UI → Control API → { Model Router · Orchestrator · Task Engine }
       → Agent Runtime / Workers → { Local LLMs · Cloud APIs · Tools }
```

Компоненты слабо связаны. Не monolith.

## 4–8. Модели

- **Provider layer**: единый Model Provider API. Local: llama.cpp server, Ollama,
  vLLM, SGLang, LM Studio / любой OpenAI-compatible. Cloud: OpenAI, Anthropic,
  Gemini, OpenRouter, Z.ai/GLM, любой OpenAI-compatible. Новый provider = один adapter.
- **Model Registry** (страница Models): name, provider, local/cloud, endpoint,
  status, context window, vision/tool calling/reasoning/coding, цены, RAM/GPU,
  скорости, active jobs, health + кнопка **Test Model** (benchmark с записью).
- **Local Model Manager**: discover, start/stop/unload/restart, health, benchmark,
  context test, memory, idle unload, max concurrent, queue. Без подтверждения
  большие модели не скачивать (показать size/RAM/disk/quant/compatibility).
- **Smart Router**: правила «тип задачи → модель» с учётом способностей, RAM,
  очереди, скорости, стоимости, контекста, предыдущих ошибок, предпочтений.
- **Fallback-цепочки**: Model A → fail/low confidence → Model B → fail → Cloud C.
  Настройка через UI.

## 9–12. Агенты и оркестр

Агент: name, role, system prompt, primary model, fallback models, tools, memory,
max runtime, budget, permissions, workspace, status. Шаблоны: Coding (fs, git,
terminal, browser, tests), Research (browser, search, files, notes), Sales (CRM,
email draft, website audit, pricing rules), Vision QA (screenshots, UI review, browser).

Orchestrator: команды manager/workers/reviewer с моделями, инструментами, порядком,
parallel/sequential, условиями перехода, max retries. Режимы: Sequential, Parallel,
Manager/Workers, Debate (judge выбирает), Review Loop (с лимитом итераций).

## 13–17. Задачи

- **Task Composer** — главное поле «What should BOSSMAN do?» обычным языком +
  Orchestra/Priority/Run/Duration/Budget/Approval + START.
- **Long-running**: run once / 1h / 6h / 24h / 3d / 7d / until completed / until
  date / recurring. Задача переживает закрытие браузера, logout, worker restart,
  reboot; после перезапуска продолжает с checkpoint.
- **Scheduler**: now, at time, hourly, daily, weekly, custom cron, run for N hours,
  run until date; notify only if meaningful change.
- **Checkpoints**: state, progress, artifacts, conversation, tool results, current
  agent/model, errors, remaining work. Pause → reboot → Resume без начала с нуля.
- **Queue-экран**: RUNNING / QUEUED / WAITING APPROVAL / PAUSED / FAILED /
  COMPLETED; по задаче — progress, elapsed, agent, model, tokens, cost,
  CPU/GPU/RAM, latest action, ETA только если адекватно оценивается.

## 18–21. Контроль человека

Live Session Viewer (agent, model, current task/tool/file, latest result, errors,
elapsed). Кнопки: Pause, Resume, Stop, Approve, Reject, Retry, Change model, Give
instruction, бюджет ±, Take over; инструкция агенту в середине работы — без потери
контекста. Approval Queue («Needs Your Approval»: send email, deploy, invoice,
цена, delete files, покупка credits, дорогая cloud-модель) — Approve/Reject/Edit.
Permissions per-агент (filesystem.read/write, terminal.run, git.commit,
browser.read/write, email.draft/send, invoice.create, deploy); dangerous — off
по умолчанию.

## 22–25. Инструменты

Tool Registry: terminal, filesystem, git, browser, Playwright, web search, GitHub,
Gmail, calendar, CRM, database, n8n, custom MCP servers, custom HTTP tools.
Архитектурно поддержать MCP + MCP Manager (add/remove/enable/disable/test/assign,
status/tools/permissions/latency/last error). Browser Control — live preview
Playwright-worker (страница, screenshot, URL, агент, действие), не Chrome
replacement. Terminal viewer: команды агентов видимы, dangerous — через approval,
модель не может скрывать выполненные команды.

## 26–29. Проекты и память

Projects (Fresh Vibes, Nimba Money, SwapMe, Lead Generation, Local AI Setup…):
repo, workspace, agents, sessions, tasks, files, memory, model policy, costs.
Persistent project memory (facts, decisions, preferences, architecture, TODO,
mistakes, previous sessions) через retrieval, не гигантский prompt. Session
memory: goal, plan, decisions, artifacts, progress, unfinished, summary. Context
Manager: usage/max, files/memory/conversation/tool tokens, auto-compaction без
потери решений, требований и состояния задачи.

## 30–33. Расходы, ресурсы, уведомления

Cost Control: Today/24h/7d/30d, per model/project/agent/task; лимиты (task $5,
daily $20, project $100) → STOP или ASK. Метрики: CPU, GPU, RAM, VRAM, SSD,
temperature, model load, inference speed, active workers. Resource scheduler: перед
запуском модели оценить память → queue / unload idle / ask user; не допускать swap.
Notifications: task completed/failed, approval required, budget exceeded, model
crashed, payment received, new client replied — сначала в dashboard, расширяемо на
Telegram/email/push.

## 34–37. UX

Глобальный search («Qwen», «failed tasks today»), Command Palette (Ctrl/Cmd+K: New
Task, New Agent, Load/Stop Model, Open Project, Running Tasks, Pause All, System
Status). Стиль: premium, dark/light, минимализм, desktop-first + нормальный mobile,
быстрый, без визуального мусора, понятен без документации; не admin template —
AI control room. Home: статус-строка, Quick Task, Running Tasks, Models,
Approvals, System, Recent activity.

## 38–43. Платформа

API-first: UI только через документированный Control API (`/api/models`, `/agents`,
`/tasks`, `/runs`, `/projects`, `/tools`, `/schedules`, `/approvals`, `/system`).
Event bus (websocket/SSE): task.started/progress/completed, agent.started/
tool_call/error, model.loaded/unloaded, approval.created, system.warning.
PostgreSQL: providers, models, agents, agent_tools, projects, tasks, task_runs,
schedules, sessions, messages, tool_calls, artifacts, approvals, usage, costs,
system_metrics, memories, events. Persistent job queue (retries, priority,
pause/resume, persistence, concurrency limits, crash recovery) — не в HTTP request.
Secrets: encrypted at rest, masked в UI, не в логах, не отдавать модели без
необходимости. Auth: локальный single-user, но backend не открыт; наружу — только
private network/VPN, не публичный интернет.

## 44–46. OpenCode и чат

Сравнить Path A (OpenCode как execution engine через adapter) и Path B (свой
runtime, только идеи): effort, stability, tool ecosystem, permissions, sessions,
local model support, extensibility, licensing. Выбрать рационально. Работать с
существующими git-папками без proprietary import. Multi-model chat: спросить
несколько моделей (Qwen, GLM, Gemini) + judge (Claude), видеть ответы отдельно и итог.

## 47–52. Автономность

Swarm mode («Spawn 5 workers», ограничено resource scheduler, без бесконечных
loops). Time-boxed autonomous mode: «работай 24 часа / неделю» с goal, max cloud
cost, max workers, checkpoint every 15 min, stop if goal/critical error/budget.
Daily/weekly objectives с прогрессом. Planner перед длинной задачей (goal,
milestones, tasks, dependencies, agents, models, resources, risks) → Approve/
Edit/Start. Automatic replanning при провале (причина → смена подхода/модели →
записать → продолжить; max retries). Evaluation layer: worker result → evaluator
(желательно другая модель) → score → pass/retry/human review.

## 53–59. Обслуживание

Artifacts (code, screenshot, PDF, report, CSV, proposal, website) внутри
task/session. Логи человекочитаемые + raw view, фильтры (task, agent, model,
provider, project, severity, time). Error UX: не «500», а «Local Qwen stopped
responding, heartbeat 21s ago → Restart / Switch to fallback / Retry / Open logs».
Health checks: db, queue, model endpoints, worker, browser, OpenCode adapter, n8n,
disk, memory. Backups: PostgreSQL, configs, prompts, agent definitions, project
memory — без весов моделей. Import/Export: agent config, orchestra, task template,
routing rules, project settings в JSON/YAML. Task templates: 24h Coding Sprint,
Website Audit, Lead Hunting, Daily Research, Weekly Market Watch, Repo Refactor,
SEO Audit, Bug Hunt.

## 60. Natural language configuration

«Создай команду из главного Qwen, vision-агента и GLM как fallback, максимум 12
часов, облако не больше $3» → Proposed configuration → подтверждение → применение.

## 61. Что НЕ делать в первой сессии

Все 60 функций production-ready; Kubernetes; microservices ради microservices;
облачный SaaS; billing; multi-tenant; мобильное приложение; десятки моделей;
новый LLM runtime.

## 62. MVP этой сессии (обязательное)

A. Dashboard shell (Home, Models, Agents, Tasks, System, Settings) ·
B. Providers (OpenAI-compatible local + один cloud adapter) ·
C. Model registry (add/edit/test/health) · D. Agents (create/edit/model/prompt/tools) ·
E. Task runner (create → choose agent → run → status → persistent result) ·
F. Persistent queue (переживает refresh; worker; retries) ·
G. Scheduler (now, specific time, daily, custom interval) · H. Live logs ·
I. System metrics (CPU/RAM/GPU) · J. Safety (secrets, no dangerous auto actions,
approval architecture).

## 63. После MVP

Phase 2: multi-agent orchestra, fallback, routing, Playwright, OpenCode adapter,
MCP manager, project memory, approval queue.
Phase 3: 24h/7d autonomous objectives, automatic replanning, evaluator, cost
budgets, second ROG worker, remote secure access, notification integrations.

## 64. Definition of Done — сессия

1. открыть dashboard; 2. добавить local OpenAI-compatible endpoint; 3. добавить
cloud model API; 4. увидеть обе online/offline; 5. создать агента; 6. выбрать
модель; 7. system prompt; 8. создать задачу; 9. запустить; 10. закрыть/обновить
страницу; 11. открыть снова; 12. задача продолжилась или завершилась; 13. live и
history logs; 14. задача по расписанию; 15. CPU/RAM/GPU; 16. результаты задачи;
17. остановить активную задачу. Если базовый путь не работает — MVP не готов.

## 65. UX Definition of Done

Без терминала: добавить/выбрать модель, создать agent, запустить/остановить task,
создать schedule, посмотреть logs. Терминал — advanced/debugging.

## 66. Development Workflow

Прочитать setup-бриф → изучить repo → git status → изучить OpenCode
upstream/архитектуру/лицензию → `docs/COMMAND_CENTER_ARCHITECTURE.md` →
`docs/COMMAND_CENTER_PLAN.md` → показать план → реализация. Ничего не удалять.
Отдельная ветка: `feature/bossman-command-center`.

## 67. Автономность

После утверждения плана — работать самостоятельно: не спрашивать на каждую мелочь,
чинить coding errors, запускать tests, проверять UI, screenshots, итерации.
Подтверждение — перед: удалением данных, изменением безопасности, публикацией в
интернет, покупкой API/услуг, использованием непредоставленных секретов,
production deployment.

## 68–70. QA, тесты, документация

UI QA после каждого значимого этапа: запуск, browser, desktop, mobile,
screenshots, основные actions, фиксы. Тесты минимум: provider adapter, task
persistence, scheduler, queue retry, basic API, UI smoke. После MVP —
`docs/COMMAND_CENTER_REPORT.md` (architecture, implemented, not implemented, how
to run, ports, services, database, providers, how-to модели/агент/schedule, known
issues, performance, next phase).

## FINAL INSTRUCTION

Не строить красивый mockup без backend. Не строить backend без usable UI. Нужен
сквозной рабочий продукт. Главный критерий:

> Я открываю BOSSMAN, подключаю локальную Qwen и облачную модель, создаю агента,
> назначаю ему задачу на несколько часов или по расписанию, закрываю браузер,
> возвращаюсь позже и вижу результат, логи и состояние машины.
