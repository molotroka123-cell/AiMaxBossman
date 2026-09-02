# Cognitive 10/10 — обзор для аудита и подключения

Ветка: `feature/cognitive-10-10-memory-context-reasoning-longtasks`
Пакет: `bossman-core/bossman/cognitive/` (только stdlib, без Postgres/Redis)
Тесты: `bossman-core/tests/test_cognitive_*.py` — 30 тестов, все зелёные.

## Замкнутый контур (все 4 модуля)

```text
Наблюдение → проверка → сохранение → использование
     ↑                              ↓
     └──── измерение результата ←───┘
```

Каждое улучшение обязано доказать рост `VerifiedSuccess` без ухудшения
безопасности и без необоснованного роста расходов. Проверка — `verify.py`
(A/B + holdout + независимый verifier + cost accounting).

## Карта файлов

| Модуль | Код | Тесты | Док |
|---|---|---|---|
| Память (5 уровней, фильтр, R-формула, конфликты, забывание) | `cognitive/memory.py`, `cognitive/storage.py` | `test_cognitive_memory.py` (9) | `01-MEMORY.md` |
| Контекст (compiler P0-P4, ledger, сжатие, fallback, firewall) | `cognitive/context.py` | `test_cognitive_context.py` (5) | `02-CONTEXT.md` |
| Reasoning (thought state, D, режимы, multi-hyp, stop, Fable EV) | `cognitive/reasoning.py` | `test_cognitive_reasoning.py` (7) | `03-REASONING.md` |
| Длинные задачи (journal, DAG, checkpoint, resume) | `cognitive/tasks.py` | `test_cognitive_tasks.py` (5) | `04-LONG-TASKS.md` |
| Измерение + единый runtime | `cognitive/verify.py`, `cognitive/runtime.py` | `test_cognitive_runtime.py` (4) | `05-MEASUREMENT-ROADMAP.md` |
| Подключение после аудита | `cognitive/runtime.py` (адаптеры) | — | `06-INTEGRATION-GUIDE.md` |
| Чек-лист приёмки | — | — | `07-AUDIT-CHECKLIST.md` |

## Как читать код

1. Начни с `storage.py` (схема SQLite, `FixedClock`, tombstones).
2. Затем `memory.py` → `context.py` → `reasoning.py` → `tasks.py`.
3. Затем `verify.py` (гейты) и `runtime.py` (wiring одной системой).
4. Затем `06-INTEGRATION-GUIDE.md` — что подключить умной модели.

## Ключевые гарантии (доказаны тестами)

- Фильтр записи: stale/future/self-verifier/injection/protected-tests/security — REJECT/QUARANTINE.
- Scope-изоляция: чужой `owner_id/project_id` не возвращается (`CrossUserLeakage=0`).
- Конфликты: автопик запрещён, проигравший → `SUPERSEDED`, история в `conflicts`.
- Забывание: TTL/GC/tombstones + `assert_no_residual` (кэш инвалидируется).
- Контекст: P0/P1 неудаляемы, ledger roundtrip отменяет summary при потере факта.
- Injection: `UNTRUSTED_DATA` санитизируется, факт фиксируется → raw fallback.
- Reasoning: D-формула, калибруемые пороги, честный `BLOCKED`, Fable по EV (P0 раньше).
- Задачи: `VERIFIED` только с verifier, `effect_id` переиспользуется (no duplicates),
  resume без слепых повторов, `lost_verified_steps=0`, env-change → revalidation.

## Что НЕ делать

- Не переносить пакет в Postgres/Redis до аудита — сначала holdout на SQLite.
- Не калибровать веса R и пороги D на holdout — только на dev, затем `freeze()`.
- Не подключать Fable-вызовы без `should_call_fable` (EV) и бюджета.
