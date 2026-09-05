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
