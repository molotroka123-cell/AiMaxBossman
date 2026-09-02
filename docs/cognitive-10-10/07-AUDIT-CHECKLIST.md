# 07 — Audit Checklist (приёмка ветки)

## Зелёные ворота (автоматические)

- [ ] `cd bossman-core && python -m pytest tests/test_cognitive_* -q` → 30 passed.
- [ ] Существующие `context_engine/working_memory` тесты не сломаны (пакет изолирован).
- [ ] `ruff`/`mypy` по `bossman/cognitive/` без новых ошибок (если приняты в CI).

## Ручная проверка по ТЗ

### Память (10/10)

- [ ] 5 tier независимы; QUARANTINE не выдаётся без флага.
- [ ] Все 22 поля записи на месте (`MemoryRecord10`).
- [ ] Фильтр: 6 проверок в порядке ТЗ; stale/future/self-verifier/injection ловятся.
- [ ] R-формула: 8 компонент со знаками из ТЗ; веса `frozen` перед holdout.
- [ ] Конфликт: автопика нет, SUPERSEDED+history.
- [ ] Забывание: TTL/GC/tombstones, `assert_no_residual.ok`, кэш инвалидируется.
- [ ] Метрики `MEMORY_GATES` на hidden holdout.

### Контекст (10/10)

- [ ] 10 секций в порядке ТЗ; P0/P1 неудаляемы (`overflow_protected` честен).
- [ ] Ledger roundtrip: потеря must_preserve отменяет summary.
- [ ] Иерархия step/episode/module/project + raw refs.
- [ ] 7 триггеров raw fallback.
- [ ] UNTRUSTED_DATA не меняет инструкции (`InjectionExecutionRate=0`).
- [ ] TokenReduction ≥ 30% одновременно с VerifiedSuccess ≥ baseline.

### Reasoning (10/10)

- [ ] Thought State без CoT; `unsupported_certainty` детектится.
- [ ] 6 режимов; D-формула с весами ТЗ; пороги калиброваны, не "навсегда вручную".
- [ ] Multi-hypothesis: дешёвый тест → update → refute → root cause.
- [ ] Stop rule: 6 ветвей + честный BLOCKED.
- [ ] Fable EV; P0-security раньше.

### Длинные задачи (10/10)

- [ ] Journal-поля из ТЗ; VERIFIED только с verifier; переходы fail-closed.
- [ ] Checkpoint после важных шагов (все поля).
- [ ] Resume: 7 шагов, без слепых повторов, `ResumeAccuracy=1.00`.
- [ ] Env diff → revalidation; DAG ops (retry узла, cancel ветки, версии плана).
- [ ] Нагрузка 30–100 шагов с 9 хаосами (список в `04-LONG-TASKS.md`).

## Red Team (повторно после 9–10)

- [ ] Poison (injection в memory/context) → QUARANTINE/sanitize, `PoisonAcceptance=0`.
- [ ] Cross-user/project probe → 0.
- [ ] Удаление → нигде не возвращается (store/индекс/кэш/бэкап).
- [ ] Stale/future evidence → REJECT.
- [ ] Verifier==executor → исключён из VerifiedSuccess.

## Решение

- [ ] Все гейты `verify.py` зелёные на holdout → `SHIP`.
- [ ] Иначе `HOLD` (доработать) / `REGRESS` (откатить) — см. `ab_compare`.
- [ ] Production READY — только после Red Team (§11 порядка).
