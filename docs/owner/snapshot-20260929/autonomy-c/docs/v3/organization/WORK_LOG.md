# BOSS-ORG-V3-INTEGRATE-001 — операционный журнал

Формат: OBSERVATION → EVIDENCE → DECISION → FILES → TEST → RESULT → NEXT.

## 1. Репозиторная истина

- OBSERVATION: HEAD `658ccaa` на `claude/v3-memory-context-kernel`; V2 заморожен на `ffda281`; V3 живёт
  в `bossman-core/bossman_v3/` (journal, compound, adapters/command_center). Executive OS отсутствует.
- EVIDENCE: `git log`, `find bossman_v3`, grep по `executive_os|organization|department` — совпадения
  только в `bossman/company/*` (AI Company Mode: планировщик, рантайм, роль ≠ полномочие).
- DECISION: drop-in подчинить репозиторию — пакет в `bossman_v3/organization`, не в `bcc/v3`;
  исполнение через существующие CompoundRunner/TaskJournal/UniversalComputerAgent; bossman.company
  подключить адаптером, не дублировать.

## 2. Drop-in: KEEP / ADAPT / REFACTOR / REPLACE / SKIP

| Файл ZIP | Решение | Причина |
|---|---|---|
| models.py | REFACTOR | Department-Enum → данные; Evidence с доверенным источником; Resources многомерные |
| contracts.py | REPLACE | Contract 2.0 со всеми полями §11; валидация по доверенным уликам |
| router.py | REFACTOR → marketplace.py | допуск к риску, штраф за ложный успех, независимый ревьюер, эскалация на ступень |
| policy.py | REFACTOR → treasury.py + `contract.problems()` | конверты org/dept/mission, reserve/commit/release |
| store.py | REFACTOR | таблицы для миссий, команд, конвертов, обучения, знаний, событий, журнала |
| kpi.py | REPLACE → learning.py | статистика агент × способность с забыванием |
| bridge.py | REPLACE → bridges.py | направление моста было обратным; V3ExecutionBridge + MissionReporter |
| orchestrator.py | REPLACE → runtime.py | полный цикл с WAITING_APPROVAL, ревью, эскалацией, публикацией |
| registry.py | SKIP (в runtime/marketplace) | отдельный класс избыточен при durable store |
| test_organization.py | REPLACE | тесты на реальных V3-компонентах; сценарии ZIP покрыты и расширены |
| validate script, `.pytest_cache`, `__pycache__` | SKIP | CI репозитория уже делает compile + secret scan + pytest |
| docs (ARCHITECTURE/SECURITY/…) | ADAPT → docs/v3/organization | переписаны под реальную архитектуру |

## 3. Реализация

- FILES: `bossman_v3/organization/{__init__,models,contracts,treasury,store,learning,marketplace,teams,
  memory_scope,events,bridges,control_plane,runtime}.py`; `feature_flags.py` (+`organization`).
- TEST: `tests/test_v3_organization_{core,e2e,command_center,company_bridge}.py`.

## 4. Провалы и исправления (чтобы не повторять)

- `AdaptiveTeamFormer` вернул пустую команду на HIGH-риске → причина не роутер, а дефолтный
  `risk_clearance=MEDIUM` у агентов. Это дизайн: допуск к риску задаётся явно. Тест поправлен, код нет.
- `run_mission` возвращал `state=active` после завершения → статус читался ДО `_finish_mission`.
  Порядок: finish → status.
- Дедуп событий стоял ПОСЛЕ backpressure → повтор при заполненной очереди выглядел как backpressure.
  Порядок: дедуп → backpressure → запись.
- `_drive` останавливался на PLANNED после провала → эскалация уровня не делала второго круга.
  PLANNED после провала = следующий круг; предел — `escalation.max_attempts`.
- Ложный успех не учитывался обучением: `CompoundResult.executed` содержит только подтверждённые шаги.
  Мост помечает `claimed_effect` — «чек есть, свежее наблюдение эффект отвергло» — и обучение его штрафует.
- CI secret-scan: канарейки `sk-…`/`ghp_…` в `test_v3_memory_kernel.py` (коммит 658ccaa) не были
  помечены `ci-secret-scan: allow` → compile-job ядра был бы красным. Помечены.

## 5. Результаты

- Targeted (organization + V3): 77 passed локально (включая 3 живых теста через `bcc`).
- Secret scan: PASS. Compile: OK.
- Полная регрессия ядра: см. итоговый отчёт миссии.

## 6. Swarm-миссия BOSS-SWARM-INTEGRATION-008 — findings, закрытые в Organization

| ID (аудит `docs/audit`) | Что было | Что сделано | Тест |
|---|---|---|---|
| ORG-03 | `deadline` не исполнялся | `_attempt`: `now > deadline → BLOCKED/deadline_missed`, владелец | `test_deadline_missed_blocks_before_placement` |
| ORG-04 | затухание 0.9 на всех счётчиках: `failing_agents(min_attempts=2)` слеп после 2 попыток, потолок надёжности 0.917 | `n_raw`/`verified_raw` отдельно; λ = 2^(−1/T½), T½=40 по умолчанию; пороги — по `n_raw`; `posterior`/`uncertainty` | `test_org04_…` |
| ORG-05 | точечная надёжность внутри tier = starvation новых агентов | LOW — Thompson (seed = digest контракта, детерминированно), MEDIUM — UCB μ+0.5σ, HIGH — только μ | `test_org05_…` |
| ORG-06 | `cost_per_call > budget` | E[calls] = |steps|·(1+retry_rate)·cost ≤ budget | `test_org06_…` |
| ORG-07 / INV-3 | конверты без разбиения; резервы терялись молча | `set_limit(parent=…)`: child ≤ parent и Σ children ≤ parent → `PartitionViolation`; `reserved_json`/`parent` в store (резервы не восстанавливаются намеренно — иначе двойной учёт; задокументировано) | `test_org07_…` |
| MEM-02 | `read(scope)` — строгое равенство | явное наследование `include_parents=(department, organization)`; чужой отдел/миссия → `PermissionError` | `test_mem02_…` |
| EH-01 (граница флота) | улика доверяется по префиксу `journal:` | `FleetExecutionBridge` перечитывает улики из журнала сам; присланные узлом и не подтверждённые журналом — отброшены, `TASK_REJECTED` | `test_node_returned_forged_journal_evidence_is_rejected_by_fleet` |
| Agent 3 | два `EvidenceRequirement`, слабая независимость ревьюера | один тип (org расширяет `bossman.company`); `ContractReviewer` через `deep_fix.Principal` | существующие тесты |
| Agent 6 P0-3 | approvals bcc по `(kind, preview)` | preview V3-адаптера включает `task#<id>` | `test_v3_command_center_adapters` |

Не сделано здесь (передано в TZ-04 §2 / §5 параллельной сессии): HTTP-feature `bcc/features/organization.py`,
`PlannerPort` для контрактов без шагов, saga-компенсации (ORG-01, ORG-02, ORG-08). Причина: изменения в
`command-center` затрагивают замороженный V2 и параллельно запланированы другой сессией — во избежание коллизий.
