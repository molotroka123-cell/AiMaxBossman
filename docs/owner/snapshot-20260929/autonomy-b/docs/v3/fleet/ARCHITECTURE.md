# Bossman Fleet OS (V3) — архитектура

Пакет: `bossman-core/bossman_v3/fleet/`. Флаг: `BOSSMAN_V3_ENABLED` + `BOSSMAN_V3_FLEET`.

## Разделение ответственности (не смешивать)

| Слой | Вопрос | Где в репозитории |
|---|---|---|
| Organization | КТО делает | `bossman_v3/organization` (marketplace, teams, contracts) |
| **Fleet** | **ГДЕ исполняется** | `bossman_v3/fleet` |
| Model Broker | КАКАЯ МОДЕЛЬ | `command-center/bcc/v2/model_router.py` (+ `model_intelligence`) — флот его не заменяет |
| Action Engine | КАК исполняется | V3 `UniversalComputerAgent` → адаптеры → V2 `bcc.tools` (заморожен) |
| Verifier | СЛУЧИЛОСЬ ЛИ | V3 журнал (`TaskJournal`) ← `bcc/v2/verification` |
| Memory | ЧТО ПЕРСИСТИТСЯ | `TaskJournal`, `ScopedKnowledge`, канонический `bossman.context_engine` |

Флот **не** исполняет, **не** верифицирует, **не** одобряет, **не** выбирает модель.

## Точка склейки

`FleetExecutionBridge` реализует существующий порт `organization.bridges.ExecutionBridge`.
Организация вызывает `execute(contract, agent_id)` и ничего не знает о флоте.

```
OrganizationRuntime._attempt(contract)                      # КТО: agent_id, команда, казначейство
   └─ FleetExecutionBridge.execute(contract, agent_id)      # ГДЕ
        ├─ flights: PLANNED → QUEUED
        ├─ FleetResumeKernel(journal)  — безопасно ли продолжать после потери узла
        ├─ place(): scheduler.choose(nodes, PlacementRequirement.from_contract)   → PLACED (объяснимо)
        ├─ leases.acquire(node, ttl, fencing)                                        → LEASED
        ├─ transport.dispatch(node, NodeExecutionRequest)                            → DISPATCHED → EXECUTING
        │     └─ узел: V3ExecutionBridge → UniversalComputerAgent → CompoundRunner → TaskJournal (→ V2 через адаптеры)
        ├─ OBSERVED (улики = finished-шаги журнала)  → VERIFYING → contract.validate(result)
        └─ VERIFIED / FAILED / BLOCKED ; verified_mutations по MutationIdempotencyKey
```

## Модули

| Модуль | Ответственность | Мандат |
|---|---|---|
| `models.py` | NodeState как данные (RAM/GPU/unified/модели/warm/пулы/privacy/trust/failure-domain/артефакты), PlacementRequirement из контракта, Lease с fence, FlightState + `LEGAL_TRANSITIONS`, FailureClass, события, CredentialGrant | §10, §8 |
| `store.py` | SQLite (stdlib): nodes, leases, fences, flights, verified_mutations, work_queue, dead_letter, credential_grants, events, node_stats, artifacts | §25 |
| `registry.py` | регистрация, heartbeat, ONLINE/DEGRADED/DRAINING/OFFLINE, watchdog, reclaim аренд при OFFLINE | §14 |
| `scheduler.py` | жёсткие фильтры с кодами причин → детерминированный счёт (local-first, warm model, локальность артефактов, запас памяти, нагрузка, надёжность, anti-affinity, prefer_node); admission reject | §16, §17, §23, инн. 2/3/4/6/7 |
| `leases.py` | TTL, renew, expire, fencing-токен монотонный по (узел, класс ресурса), reclaim узла | §13, инн. 1 |
| `flight.py` | `DistributedFlightRecorder`: строгий автомат, VERIFIED только с trusted evidence refs, `MutationIdempotencyKey` | §8, Priority 2 |
| `queue.py` | durable очередь, атомарный single-owner claim (CAS), eligibility до claim, retry-классы, dead letter (requeue только human/policy) | §20, §21, инн. 5 |
| `privacy.py` | PRIVATE/LOCAL_ONLY → trusted_local; INTERNAL → trusted или cloud с MINIMIZED; секреты — только trusted_local; fail-closed | §18 |
| `credentials.py` | гранты (secret_id/узел/способность/скоуп/срок/кто выдал), выдача только human/policy principal, значение через `SecretProvider` (V2 Vault) | §19 |
| `artifacts.py` | sha256-идентичность, verify после переноса, реестр местоположений, объём переноса | §17 |
| `resume.py` | `FleetResumeKernel` над `TaskJournal`: finished не повторяются; не-идемпотентный шаг в полёте на потерянном узле → владелец | §15 |
| `node_agent.py` | `NodeTransport`, `LocalNodeTransport` (in-process узлы), MINIMIZED-контракт, `RemoteNodeTransport` — честно не реализован | §11, §12 |
| `journal.py` | durable журнал событий, дедуп по event_id, редакция секретов | §25, инн. 8 |
| `twin.py` | снимок из durable таблиц: узлы, online/offline/draining, warm-модели, аренды, полёты, миссии по узлам, blocked, migratable, очередь, dead letters, метрики | §22 |
| `control_plane.py` | композиция + `FleetExecutionBridge` + `FleetLearning` (надёжность узла по способности) | §7 |

## Adoption table (Fleet drop-in → репозиторий)

| ZIP-компонент | Решение | Куда / почему |
|---|---|---|
| `bcc/v5/fleet/*` путь | REPLACE | `bossman_v3/fleet` — V2 заморожен, V3 живёт в ядре |
| models.NodeState | ADAPT | + pools, warm_models, trust_class, failure_domain, unified_memory, artifacts, used-память, concurrency |
| store.FleetStore | ADAPT | + fences, flights, verified_mutations, dead_letter, grants, events, node_stats, artifacts; autocommit + явные транзакции |
| registry/health | MERGE | один `NodeRegistry` с watchdog и reclaim; DRAINING не снимается heartbeat'ом |
| scheduler + placement_explain + model_runtime + topology | MERGE | один объяснимый планировщик; warm-модель и локальность — компоненты счёта, не ворота |
| leases | ADAPT | + renew, fencing, valid(), reclaim |
| resume.ExecutionCheckpoint/ResumePlanner | REPLACE | второе хранилище чекпоинтов дублирует `TaskJournal`; заменено `FleetResumeKernel` + `verified_mutations` |
| work_stealing | ADAPT | CAS-claim с eligibility, release, dead letter |
| retry/dead_letter | MERGE | в `queue.py`; классы NEVER/HUMAN/REROUTE/BACKOFF/VERIFICATION |
| credentials | ADAPT | durable гранты, скоуп, revoke, typed principal, `SecretProvider` |
| privacy | ADAPT | trust_class + уровни, MINIMIZED-контекст реально применяется к контракту |
| artifacts | ADAPT | + реестр местоположений/объём переноса |
| resource_accounting | REPLACE | расширен единый `organization.Resources`/`ResourceTreasury` (gpu_seconds, gpu_memory_gb, network_bytes) |
| event_journal/events/metrics | MERGE | `FleetEventJournal` + `plane.metrics` |
| digital_twin | ADAPT | читает только durable-таблицы |
| node_agent.NodeTransport | ADAPT | Local реализован; Remote — `RemoteTransportUnavailable` |
| bridge.OrganizationFleetPort | REPLACE | `FleetExecutionBridge` реализует существующий порт организации |
| integration adapter template | SKIP | не нужен |
| tests | REPLACE | `test_v3_fleet_core.py`, `test_v3_fleet_e2e.py` на реальных V3-компонентах |

## Честные границы

- `REMOTE_TRANSPORT_PRODUCTION_READY=NO`, `NODE_AUTH_PRODUCTION_READY=NO`: в репозитории есть device/session
  principals (`bossman.remote_client.auth`) и WS-аутентификация периметра, но нет подписи запросов, nonce/replay-окна,
  mTLS и ротации ключей узлов. Регистрация узла — доверенная in-process операция.
- Оценки ресурсов не объявляются точными; admission детерминирован по объявленным требованиям.
- Время восстановления измеряется бенчмарком, SLA не заявляется.
