# Organization Layer — передача

## Что готово

- Пакет `bossman_v3/organization` (12 модулей, стандартная библиотека + существующий V3).
- Тесты: `test_v3_organization_core.py` (единицы), `test_v3_organization_e2e.py` (детерминированный E2E
  на реальных V3-компонентах + рестарт), `test_v3_organization_command_center.py` (живой стек через
  замороженный V2, skip без `bcc`), `test_v3_organization_company_bridge.py` (план AI Company Mode →
  контракты).
- Docs: `ARCHITECTURE.md`, `SECURITY.md`, `WORK_LOG.md`, этот файл.

## Как подключить на машине владельца

```python
from bossman_v3.adapters.command_center import CommandCenterRuntime, build_agent
from bossman_v3.organization import *

rt_cc = CommandCenterRuntime()                       # цикл событий для bcc
bridge = V3ExecutionBridge(
    agent_factory=lambda agent_id, contract: build_agent(rt_cc, svc, task=task_row, agent=agent_row, run_id=run_id),
    journal_root=data_dir / "v3-journals")
org = OrganizationRuntime(store=OrganizationStore(data_dir / "organization.sqlite"), execution=bridge,
                          human_review=<порт в UI/уведомления>, reporter=<порт в слой миссий>,
                          failure_root=str(data_dir / "v3-failures"))
org.register_department(Department("engineering", capabilities={"terminal.run", "code.edit"}, budget=Resources(usd=10)))
org.register_agent(AgentProfile("coder-local", "engineering", {EXECUTOR}, {"terminal.run"}, tier="local_small", model="glm-4.5-air"))
org.register_agent(AgentProfile("reviewer", "engineering", {REVIEWER}, {"terminal.run"}, tier="local_small", model="qwen3-14b"))
```

Контракты для миссии — либо вручную (`DelegationContract` со `steps` в формате `step_to_dict(PlanStep)`),
либо из плана AI Company Mode (`contracts_from_company_plan`). Владелец одобряет ASK в обычной очереди
V2; после решения `org.run_mission(id)` / `org.resume()` продолжает с того же журнала.

## Что ещё нужно (не Organization Layer)

- **Executive OS** — слой миссий над организацией. Точка входа уже есть: `receive_mission(...)` +
  `MissionReporter.report(MissionStatus)`. Планировщик «цель → контракты со шагами» для произвольных
  целей — задача Executive OS; сейчас есть детерминированный `bossman.company.planner` (домены seo/generic).
- **Реальный `agent_factory` по агенту**: сейчас один `build_agent` на V2-агента; чтобы `AgentProfile`
  организации соответствовал разным моделям V2, фабрика должна маппить `agent_id` → строку `agents` V2.
- **Cost meter**: `V3ExecutionBridge(cost_meter=...)` — подключить `spend_meter` V2, чтобы `usd`
  в казначействе был измеренным, а не оценкой контракта.
- **UI-проекция control plane**: `OrganizationRuntime.snapshot().to_dict()` уже отдаёт всё, что нужно.
- **Fleet Mode** — не начат намеренно. Forward-compatible интерфейсы: `ExecutionBridge` (можно
  указать на удалённого исполнителя), `MissionReporter`, `store` с `mission_id`/`team_id` как ключами.

## Известные ограничения

- Bossman Core CI не устанавливает `bcc` → тест живого стека там честно skip; локально проходит.
- Организация не строит планы шагов сама: контракт без `steps` завершается FAILED с причиной
  «no executable steps» (проверено в `test_event_reaction_runs_through_the_same_cycle`).
- Казначейство считает сообщённую стоимость; жёсткого провайдерского enforcement нет и не заявляется.
