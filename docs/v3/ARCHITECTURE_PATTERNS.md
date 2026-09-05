# Заимствованные паттерны и дедупликация против текущего Bossman

Источник: раздел «GITHUB ARCHITECTURE MINING — ACCEPTED PATTERNS» мастер-промпта. Пять референсов
(MetaGPT, crewAI, OpenHands, LiteLLM, Prefect) — **архитектурные ориентиры**. Доступ к чужим репозиториям в
этой сессии ограничен (GitHub-scope = только `molotroka123-cell/AiMaxBossman`), поэтому актуальные upstream-символы
**не верифицированы**: `UPSTREAM_VERIFIED=NO`. Код не копировался; паттерны реализованы нативно.

## A–E. Паттерн → решение

| Референс | Паттерн | Bossman | Классификация |
|---|---|---|---|
| MetaGPT | subscription-scoped observation | `organization.events.EventIntake` реагирует только на зарегистрированные `Reaction.event_kind`; `ScopedKnowledge.read(scope, include_parents)` — только свой скоуп + явные родители | EXTEND (реализовано в Org) |
| MetaGPT | dynamic role/team registration | `AdaptiveTeamFormer` + `CapabilityMarketplace` из реестра | ALREADY_PRESENT |
| MetaGPT | mission-level investment/budget до исполнения | `ResourceTreasury.reserve` до делегирования; INV-3 разбиение | ALREADY_PRESENT + EXTEND |
| crewAI | formal delegation + output validation | `DelegationContract.validate()` только по уликам | ALREADY_PRESENT |
| crewAI | hierarchical review by risk | `required_roles(risk)`: LOW executor; MEDIUM +reviewer; HIGH lead+reviewer(+risk) | ALREADY_PRESENT |
| OpenHands | action → observation loop, verification boundary | `UniversalComputerAgent.run`: policy→approval→execute→observe_fresh→verify; `StaleObservationError` | ALREADY_PRESENT (V3) |
| OpenHands | append-only event stream | `FleetEventJournal` (дедуп по event_id) + `OrganizationStore.log`; событие ≠ доказательство | NEW (fleet) / ALREADY_PRESENT (bcc.events для UI) |
| LiteLLM | provider/model routing, spend accounting, fallback | `bcc/v2/model_router.py`, `spend_meter`, `fable_cap` — **Model Broker остаётся отдельным от Fleet** | ALREADY_PRESENT; REJECT дублирование во флоте |
| Prefect | durable runs, bounded retry, worker heartbeat, work pools | `TaskJournal` + `FlightRecorder`; `queue.on_failure` классы; `NodeRegistry.heartbeat`; `NodeState.pools` / `PlacementRequirement.pools` | EXTEND (пулы), NEW (flight) |
| Prefect | strict state transitions | `LEGAL_TRANSITIONS` + `IllegalTransition` | NEW |

## F. Десять принятых апгрейдов — статус

| # | Апгрейд | Где | Статус |
|---|---|---|---|
| 1 | subscription-scoped event delivery | `events.Reaction`, `ScopedKnowledge.read` | EXTEND — реализовано для реакций и памяти; для агентов внутри bcc — не требуется (там свой bus) |
| 2 | unified mission treasury | `organization.Resources` (+gpu_seconds, gpu_memory_gb, network_bytes), `ResourceTreasury` org→dept→mission | MERGED (одно казначейство; `usd`-факт из V2 `spend_meter`/`fable_cap` — TZ-09, открыто) |
| 3 | typed SOP/delegation pipeline | mission → contract → role → capability → executor → evidence → review в `OrganizationRuntime._attempt` | ALREADY_PRESENT |
| 4 | conditional quality graph | `teams.required_roles(risk, department)` | ALREADY_PRESENT |
| 5 | observe-before-advance | `UniversalComputerAgent`, `CompoundRunner` | ALREADY_PRESENT |
| 6 | append-only operational stream | `FleetEventJournal`, `org_log` | NEW/EXTEND |
| 7 | Model Broker separated from Fleet | флот не выбирает модель; `required_models`/warm — только фильтр/счёт места | YES |
| 8 | resource pressure governor | `scheduler.reject_reasons` (память/GPU/concurrency/load) + `admission_reason`; `NodeRegistry.evaluate` → DEGRADED при load≥0.98 | NEW (детерминированно) |
| 9 | capability work pools | `NodeState.pools`, `PlacementRequirement.pools`, `WorkQueue.eligible_for` | NEW |
| 10 | strict distributed state machine | `FlightState` + `LEGAL_TRANSITIONS`; `PLACED→VERIFIED` невозможен | NEW |

## G. Отвергнуто как дубликат

- Второй EventBus (bcc `svc.bus` и `bossman.events` остаются каналами UI/фич; журнал флота — durable аудит, другой контракт).
- Второй планировщик миссий (`bossman.company.planner` + `contracts_from_company_plan`).
- Второе казначейство (drop-in `ResourceBudget` заменён расширением `Resources`).
- Второй чекпоинт-стор (drop-in `ExecutionCheckpoint` заменён `TaskJournal` + `verified_mutations`).
- Второй `EvidenceRequirement` (org расширяет `bossman.company.model.EvidenceRequirement`).
