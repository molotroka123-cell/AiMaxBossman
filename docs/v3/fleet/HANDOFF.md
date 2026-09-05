# Fleet OS — передача

## Что готово

- Пакет `bossman_v3/fleet` (15 модулей, stdlib + существующий V3): registry/health, scheduler (объяснимый),
  leases с fencing, flight state machine, durable queue с CAS-claim, dead letter, privacy router, credential broker,
  artifacts, resume kernel, event journal, digital twin, `FleetExecutionBridge`.
- Тесты: `tests/test_v3_fleet_core.py` (единицы), `tests/test_v3_fleet_e2e.py` (E2E #1–#4 + IRREVERSIBLE-блок).
- Организация подключена без изменения её контрактов: `OrganizationRuntime(execution=FleetExecutionBridge(plane, journal_root))`.

## Как собрать на машине владельца (один Ai Max = узел №1)

```python
from bossman_v3.fleet import FleetControlPlane, FleetExecutionBridge, LocalNodeTransport, NodeState, Heartbeat
from bossman_v3.organization import OrganizationRuntime, OrganizationStore, V3ExecutionBridge
from bossman_v3.adapters.command_center import CommandCenterRuntime, build_agent

transport = LocalNodeTransport()
plane = FleetControlPlane(data_dir / "fleet.sqlite", transport=transport, secret_provider=<адаптер к svc.vault>)
plane.registry.register(NodeState("ai-max-01", hostname="aimax", os_name="Windows", ram_gb=128, gpu_memory_gb=96,
                                  unified_memory=True, capabilities={"terminal.run", "browser.control", "code.edit"},
                                  pools={"private-local", "coding", "vision-large"}, models={"qwen3-72b"},
                                  privacy_level="private", trust_class="trusted_local"))
rt_cc = CommandCenterRuntime()
transport.attach("ai-max-01", V3ExecutionBridge(
    agent_factory=lambda agent_id, c: build_agent(rt_cc, svc, task=task_row, agent=agent_row, run_id=run_id),
    journal_root=data_dir / "v3-journals"))
org = OrganizationRuntime(store=OrganizationStore(data_dir / "organization.sqlite"),
                          execution=FleetExecutionBridge(plane, journal_root=data_dir / "v3-journals"))
# периодически: plane.registry.heartbeat(Heartbeat("ai-max-01", time.time(), load=..., warm_models=(...)))
#               plane.health()  — watchdog
```

Второй узел (ноутбук 8 GB) сегодня подключается только как **логический in-process узел** или через свой процесс с
общим durable-хранилищем; сетевого транспорта нет.

## Что нужно дальше (не Fleet-стадия)

- **Удалённый транспорт Node Agent**: подпись запросов, nonce/replay-окно, mTLS или device-principal из
  `bossman.remote_client.auth` + ротация; до этого `REMOTE_TRANSPORT_PRODUCTION_READY=NO`.
- **Измеритель ресурсов узла**: heartbeat сегодня заполняется вызывающим; интеграция с `bcc/metrics` (psutil) и
  локальным model runtime (warm_models) — отдельная задача.
- **Cost meter** для казначейства: `spend_meter`/`fable_cap` V2 → `Resources.usd` фактом, а не оценкой.
- **Autonomous Operations** — не начинать (отдельная стадия).

## Известные ограничения

- Регистрация узла доверена вызывающему (in-process). Самопроизвольная регистрация удалённого узла невозможна,
  потому что удалённого канала нет.
- Дедуп событий журнала — по event_id (детерминированный hash содержимого с округлением ts до 1 мс);
  два идентичных события в одну миллисекунду считаются одним — это намеренно.
- Твин показывает `remote_transport_production_ready: false` всегда, пока транспорт не реализован.
