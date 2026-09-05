# Bossman Organization Layer (V3) — архитектура

Пакет: `bossman-core/bossman_v3/organization/`. Флаг: `BOSSMAN_V3_ENABLED` + `BOSSMAN_V3_ORGANIZATION`.

## Место в стеке

```
ВЛАДЕЛЕЦ
  ↓
ORGANIZATION LAYER  — КТО делает работу (отделы, роли, команды, делегирование, бюджеты, качество, обучение)
  ↓ MissionReporter (пассивный отчёт наверх)        ← (Executive OS — будущий слой миссий; сейчас его роль
  ↓ DelegationContract 2.0 (+privacy, placement)        выполняет владелец/тест или bossman.company-план)
FLEET OS            — ГДЕ исполняется: `FleetExecutionBridge` реализует порт `ExecutionBridge`
  ↓                   (см. docs/v3/fleet/ARCHITECTURE.md); без флота порт реализует V3ExecutionBridge напрямую
V3 FOUNDATION       — CompoundRunner + TaskJournal + UniversalComputerAgent: память, состояние, возобновление
  ↓ adapters/command_center (V3-порты → bcc)
V2 ACTION ENGINE    — ЗАМОРОЖЕН (ffda281): реестр инструментов, decide_effect, approvals, свежая верификация
  ↓
РЕАЛЬНЫЕ ИНСТРУМЕНТЫ / КОМПЬЮТЕР / ИНТЕРНЕТ
```

Организация **не исполняет, не верифицирует, не одобряет и не расширяет права**. Она выбирает отдел и
команду, выдаёт контракт, отдаёт его цепочке V3 и принимает результат только с уликами нижнего слоя.

## Компоненты

| Модуль | Ответственность | Инновация из `FABLE_10_INNOVATIONS` |
|---|---|---|
| `models.py` | Отделы как данные, роли как метаданные, агенты, многомерные ресурсы, улики, результаты | — |
| `contracts.py` | Delegation Contract 2.0: цель/входы/ограничения/поставляемое/способность/критерии/улики/бюджет/риск/приоритет/родитель/зависимости/эскалация; `validate()` читает только улики доверенных источников | §5 |
| `marketplace.py` | Рынок способностей: отдел → роль → способность → допуск к риску → нагрузка → лестница уровней → штраф за ложный успех → надёжность → цена → задержка; независимый ревьюер; эскалация на одну ступень | §2 |
| `teams.py` | Динамический граф: временная команда под контракт, слоты по риску (LOW: executor; MEDIUM: +reviewer; HIGH: lead+executor+reviewer[+risk]); рёбра ownership/delegation/review; распуск после работы | §1, §3, §6 |
| `treasury.py` | Конверты organization → department → mission; reserve → commit/release; перерасход записывается и эскалируется владельцу | §8 |
| `learning.py` | Статистика (агент × способность): попытки, подтверждённые успехи, провалы, ложные успехи, ретраи, эскалации, цена, задержка; Beta(1,1) с забыванием | §7 |
| `memory_scope.py` | `KnowledgePort` (узкий порт памяти) + `ScopedKnowledge`: скоупы `organization / department:* / project:* / mission:* / team:* / agent:*`; происхождение, уверенность, срок годности; явное наследование `include_parents` (MEM-02); экспорт по allowlist отдела; FailureMemory — по корню на отдел. Путь слияния с каноническим `bossman.context_engine` (Agent 2): колонка `scope` рядом с `project` в `context_engine/store.py`, `org_knowledge` становится view — интерфейс порта не меняется | §4 |
| `events.py` | Реакции на события → контракты (не побочные эффекты); дедуп по idempotency-ключу, backpressure, ограниченные ретраи, только зарегистрированные виды | §9 |
| `control_plane.py` | Снимок организации из durable store: активные миссии, владельцы, работающие агенты, блокеры, ожидание владельца, казначейство, проваливающиеся агенты, подтверждённо завершённое | §10 |
| `store.py` | SQLite (stdlib): отделы, агенты, миссии, команды, контракты, результаты, конверты, обучение, знания, события, журнал | восстановление после рестарта |
| `bridges.py` | `V3ExecutionBridge` (контракт → CompoundRunner → улики из журнала), `HumanReviewPort`, `MissionReporter`/`MissionStatus`, `contracts_from_company_plan` | §18 интеграция |
| `runtime.py` | `OrganizationRuntime`: зависимости → корректность → **план (ORG-02)** → команда → резерв → делегирование → валидация → ревью → факт → обучение → состояние → публикация фактов; `resume()` после рестарта | всё вместе |
| `planner.py` | `PlannerPort` + `DeterministicPlanner`: контракт без `steps` получает план из лексикона односложных целей («создай файл X», «открой URL», «выполни команду») и только из зарегистрированных способностей; иначе `BLOCKED/no_executable_steps` (не FAILED) | ORG-02 |
| `command-center/bcc/features/organization.py` | Точка входа продукта (ORG-01): за флагами `BOSSMAN_V3_ENABLED`+`BOSSMAN_V3_ORGANIZATION`; `/api/org/*`; агент организации → агент V2 по имени/`metadata.v2_agent_id`; задача+run V2 на контракт (`kind=organization`) для аудита/approvals/стоимости; организация гоняется в рабочем потоке, вызовы в V2 — на цикл svc | ORG-01 |

## Один контракт — один цикл

1. Зависимости COMPLETED? иначе BLOCKED (без запроса владельцу).
2. Контракт корректен (`problems()`)? иначе BLOCKED + владелец.
2b. Есть исполняемые шаги? иначе `PlannerPort.plan()`; плана нет → BLOCKED/no_executable_steps + владелец.
3. Попытки не исчерпаны? иначе FAILED.
4. Команда по риску с эскалацией уровня после провалов (`escalated_min_tier`, исключая провалившихся).
5. Казначейство: резерв оценки во всех конвертах; отказ → BLOCKED + владелец.
6. Делегирование в V3 (`ExecutionBridge.execute`). Падение моста = «не исполнено».
7. Нижний слой ждёт владельца → `WAITING_APPROVAL`, резерв снят, попытка не списана, запрос владельцу.
8. `contract.validate(result)` — единственный источник `success`.
9. Независимое ревью (только вето; подтвердить непроверенное ревьюер не может).
10. Казначейство: факт; перерасход → владелец.
11. Обучение по наблюдаемому исходу (включая «чек есть, эффекта нет» = ложный успех).
12. COMPLETED (публикация verified-фактов в `mission:*` и скоуп отдела) / эскалация (PLANNED → следующий круг) / FAILED / BLOCKED.

## Инварианты, унаследованные без ослабления

- `SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == FALSE → TASK_SUCCESS == FALSE` — `contracts.validate`.
- `ANY_REQUIRED_CHILD_UNVERIFIED_OR_FAILED → PARENT_SUCCESS == FALSE` — зависимости + `_finish_mission`
  (миссия COMPLETED только когда каждый контракт COMPLETED **и** verified).
- Улика с `verified=True` доверяется только из `journal:*`, `bcc.v2.verification`, `bossman_v3.verifier`.
- Рестарт: COMPLETED-контракт никогда не делегируется повторно; незавершённый продолжается с первого
  незакрытого шага журнала V3 (`test_restart_resumes_without_duplicate_side_effects`,
  `test_completed_work_is_never_delegated_again_even_if_journal_is_lost`).

## Материальные отклонения от drop-in ZIP

| Было в ZIP | Стало | Почему |
|---|---|---|
| `command-center/bcc/v3/organization/` | `bossman-core/bossman_v3/organization/` | V2 заморожен на ffda281; V3 в репозитории живёт в ядре |
| `Department(Enum)` из 6 значений | `Department` — датакласс в реестре/store | §6: новые отделы без правки ядра |
| `ExecutiveOSBridge.execute_delegated` (Executive OS исполняет) | `V3ExecutionBridge` над CompoundRunner + `MissionReporter` наверх | Исполняет V3/V2, не слой миссий; Executive OS ещё не существует |
| `Evidence.verified` — свободный флаг | `verified` принимается только из доверенных источников | флаг в словаре — не доказательство |
| `Budget(usd, tokens, compute_seconds)` на отдел | `Resources` (+wall, concurrency) и конверты org/dept/mission с резервом | §15 |
| Роутер: tier → load → cost → success | + допуск к риску, штраф за ложный успех, независимый ревьюер по модели, эскалация на одну ступень | §9, §13 |
| KPI по списку результатов | Обучение (агент × способность) с забыванием, persist в store | §14 |
| `MemoryBridge.publish_department_fact` | `ScopedKnowledge` с экспортом по allowlist + FailureMemory по отделам | §12: нет второй памяти |
| — | `EventIntake`, `control_plane`, `contracts_from_company_plan` | §16, §17, §18 |
