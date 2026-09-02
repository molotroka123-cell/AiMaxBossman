# 01 — Память 7/10 → 10/10

Код: `bossman-core/bossman/cognitive/memory.py` + `storage.py`
Тесты: `bossman-core/tests/test_cognitive_memory.py`

## Уровни (независимые tier-namespaces)

`Tier.WORKING | EPISODIC | SEMANTIC | PROCEDURAL | QUARANTINE`.
QUARANTINE исключён из выдачи по умолчанию (`include_quarantine=False`).

## Запись `MemoryRecord10` — все 22 поля из ТЗ

`memory_id, owner_id, principal_id, source_type, source_id, task_id, run_id,
session_id, project_id, corpus_id, domain_id, head_sha, environment_digest,
created_at, collected_at, expires_at, confidence, verification_status,
verifier_id, sensitivity, allowed_consumers, contradictions, supersedes,
schema_version, content_hash` (+ служебные `tier/text/transfer_*` в `extra`).

Без этих полей запись не используется для критических решений
(проверка: `to_row/from_row` roundtrip, `schema_version=1`).

## Фильтр записи (`WriteFilter.decide`, порядок из ТЗ)

1. `not independently_verified` → QUARANTINE
2. `stale/from_future/bad_timestamp` → REJECT (допуск +5 мин, TTL default 30д)
3. `verifier == executor` → REJECT
4. `prompt_injection` → QUARANTINE
5. `protected_tests_failed` → REJECT
6. `security_worsened` → REJECT
7. иначе ACCEPT

## R-формула (`score_memory`)

```text
R = ws*S + wt*T + wv*V + wr*R + wp*P − wa*A − wc*C − wx*X
```

- S: token-cosine(query, text); T: task/domain fit; V: verification_quality;
  R: `exp(-age_days/30)`; P: `transfer_wins/transfer_uses`; A: `min(1, age/180д)`;
  C: `min(1, contradictions/3)`; X: sensitivity + длина + quarantine-штраф.
- Defaults `DEFAULT_WEIGHTS` заморожены (`.freeze()`).
- `calibrate_weights(dev_pairs)` — coordinate ascent, детерминирован
  (seed 20260902), возвращает НЕзамороженные веса; `freeze()` делает вызывающий
  код явно. Калибровать только на dev, holdout — после freeze.

## Конфликты

- Детект: overlap ≥ 0.6 значимых токенов + разная полярность (NEG-лексикон RU+EN).
- Автопик запрещён: обе записи живут, `conflicts` ← `open`.
- `resolve_conflict` требует `new_evidence`, проигравший → SUPERSEDED (не delete),
  история — в `conflicts.history`.

## Забывание

- TTL по tier (WORKING 24ч, остальные 180д, QUARANTINE 30д), `garbage_collect()`.
- `delete()` — только свой `owner_id`, пишет tombstone, инвалидирует кэш.
- `revoke_sensitive()` — отзыв sensitive/secret владельца.
- `assert_no_residual()` — store + tombstone + кэш + content_hash.
- Negative transfer: 3 провала без успехов → QUARANTINE (`report_transfer`).

## Метрики приёмки (см. `verify.MEMORY_GATES`)

`MemoryPrecision ≥ 0.98, CriticalMemoryRecall ≥ 0.97, StaleFactUsage = 0,
CrossUserLeakage = 0, PoisonAcceptanceHoldoutSuccess = 0,
VerifiedTransferGain > 0, CriticalNegativeTransfer = 0, DeletionResidual = 0`.
