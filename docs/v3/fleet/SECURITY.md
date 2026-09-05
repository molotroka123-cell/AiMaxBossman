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

## Findings аудита (Agent 6), не закрытые в этом проходе

- **P0 (V2, заморожен)** `command-center/bcc/tools.py:286-290`: пользовательские `tool_rules` применяются последними и могут
  вернуть `effect="auto"` после ужесточения hook'ом. Нужен неизменяемый пол политики. Не трогается без снятия заморозки V2.
- **P0 (ядро)** `bossman-core/bossman/gateway/auth.py:52`: `allow_unauthenticated_loopback` даёт loopback-клиенту
  `allowed_aliases={"*"}`; при Tailscale-serve внешний трафик выглядит как 127.0.0.1. Флот на этот шлюз не опирается;
  рекомендация владельцу — выключить флаг по умолчанию.
- **P1** bcc `approvals.consume` привязан к `(kind, preview)` без срока/скоупа. В V3-адаптере preview теперь включает
  `task#<id>` (закрыто для V3-пути); сам V2 не менялся.
- **P1** `settings_kv`/`providers` без проекта/узла — секреты не скоупированы по проекту (V2).
