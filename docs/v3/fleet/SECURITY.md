# Fleet OS — инварианты безопасности

1. **Размещение ≠ исполнение.** `PLACED`, `LEASED`, heartbeat, текст узла — не доказательства. `FlightState.VERIFIED`
   достижим только через `OBSERVED → VERIFYING` и только с evidence refs из `journal:*` / `bcc.v2.verification` /
   `bossman_v3.verifier` (`flight.py`, `IllegalTransition`).
2. **Приватность — жёсткий фильтр, не счёт.** PRIVATE/LOCAL_ONLY → только `trusted_local`; секреты — только
   `trusted_local`; без локальной способности — `BLOCKED`/`CAPABILITY_UNAVAILABLE`, а не облако (`privacy.py`,
   `scheduler.reject_reasons`, E2E #3). Недоверенный узел получает MINIMIZED-контракт без inputs/constraints/metadata.
3. **Секреты не в флоте.** `fleet_credential_grants` хранит авторизацию; значение — у `SecretProvider` (V2 Vault) и
   только через живой грант. Журнал, объяснения размещения, twin проходят `redact`.
4. **Гранты выдают люди/политики.** `CredentialBroker.grant(granted_by=...)` отвергает `model:*`/`agent:*`
   (тот же `untrusted_approver_reason`, что в bossman.company). Requeue из dead letter — тоже только human/policy.
5. **Один владелец работы.** Claim — атомарный CAS в SQLite; гонка узлов даёт ровно одного победителя (E2E #4).
6. **Устаревшая аренда не имеет власти.** Fencing-токен монотонен; результат, вернувшийся со stale-арендой,
   не принимается как подтверждённый (`FleetExecutionBridge`: BLOCKED, улики сброшены).
7. **Потеря узла не переигрывает опасное.** `FleetResumeKernel`: finished-шаги не повторяются; не-идемпотентный шаг
   в полёте → BLOCKED до владельца; идемпотентный — переносится (E2E #2 и тест на IRREVERSIBLE).
8. **Флот не расширяет права.** Узел исполняет через свой V3-мост → V2 `decide_effect`/approvals; флот не вызывает
   инструменты и не трогает разрешения.
9. **Событие ≠ доказательство.** Журнал флота — аудит; дедуп по event_id; без chain-of-thought.
10. **Удалённый транспорт не подделывается.** `RemoteNodeTransport` поднимает `RemoteTransportUnavailable`.

## Findings аудита (Agent 6) — состояние

- **ЗАКРЫТО P0-B** `command-center/bcc/tools.py` `decide_effect`: пол политики — DENY любого слоя абсорбирующий,
  подсказка хука — пол (`ToolSpec.hook_is_floor`, по умолчанию True); явный опт-аут только у хука-константы OpenCode.
  Тесты `command-center/tests/test_policy_algebra.py`.
- **ЗАКРЫТО P0-A** `bossman-core/bossman/gateway/auth.py`: loopback-проход только для прямого 127.0.0.1/::1 без
  proxy-заголовков; `loopback_allowed_aliases` в конфиге. Тесты `bossman-core/tests/test_gateway_loopback_proxy.py`.
- **ЗАКРЫТО FL-01 (TZ-05 §2)** `command-center/bcc/engine.py`: fencing-токен `task_runs.fence` поверх аренды; условные
  записи и heartbeat, `assert_fence` до внешнего эффекта (в V2 `_run_tool_now` и в V3 `CommandCenterExecutor`),
  replay-guard неидемпотентного шага. Fleet `Lease.fence` и engine `fence` — два независимых уровня: флот защищает
  размещение/claim в `WorkQueue`, движок — сами записи и эффекты run'а. Тесты `command-center/tests/test_fence_fl01.py`.

## Findings аудита, не закрытые в этом проходе

- **P1** bcc `approvals.consume` привязан к `(kind, preview)` без срока/скоупа. В V3-адаптере preview теперь включает
  `task#<id>` (закрыто для V3-пути); сам V2 не менялся.
- **P1** `settings_kv`/`providers` без проекта/узла — секреты не скоупированы по проекту (V2).
