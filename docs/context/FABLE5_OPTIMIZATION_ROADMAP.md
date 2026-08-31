# FABLE5 — OPTIMIZATION ROADMAP (по ROI)

Сортировка по `EXPECTED_GAIN / IMPLEMENTATION_COST`. Сначала — самое дешёвое и сильное.
Крупные архитектурные изменения (context-pipeline, V3) требуют бенчмарк-доказательства
ПЕРЕД реализацией (раздел 18 мастер-промта: «не превращай audit в rewrite»).

## Сделано в этом проходе (закрыто)

| # | Что | Gain | Cost | Статус |
|---|---|---|---|---|
| ✅ O1 | py3.12 teardown lifecycle fix (drain→dispose) | High (CI green на 3.12) | Low | DONE + 2 теста, full regression |
| ✅ O2 | Skip переэмбеддинга неизменного `memory.md` | Med-High (per-task) | Low | DONE + 2 теста |
| ✅ O3 | Memoize skill discovery (mtime-key) | Low-Med | Low | DONE + 2 теста |
| ✅ O4 | Удалён dead shim `bossman/core/db.py` | trivial | trivial | DONE |

## TOP-10 следующих оптимизаций

| # | NAME | CURRENT_PROBLEM | EXPECTED_GAIN | IMPL_COST | RISK | LAT | RAM | TOKEN | QUALITY |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Cheap observability timers** (O12) | нет per-stage latency/retry метрик — «куда ушло время» неизвестно | средний (диагностика всего остального) | **очень низкий** (perf_counter-брекеты) | очень низкий | нейтр | нейтр | нейтр | + |
| 2 | **Non-blocking sqlite в hot-path** (O5) | sync sqlite сериализует единственный worker event loop | **высокий** под конкуренцией | средний | средний (I/O переписка) | −− | нейтр | нейтр | нейтр |
| 3 | **Hash-keyed assembly cache** (O7) | `_history_messages()` пересчитывается 3× за шаг | средний (растёт с историей) | низкий | низкий | − | нейтр | нейтр | нейтр |
| 4 | **Trivial-task retrieval fast-path** (O8) | простая задача платит полный embed+hybrid-search | средний | низкий | низкий | − | − | нейтр | нейтр |
| 5 | **Bounded/indexed vector search** (O6) | full-scan вектор-таблицы + json.loads на retrieval | высокий при росте корпуса | средний | средний | −− (при росте) | −− | нейтр | нейтр |
| 6 | **Решить classify_reasoning (O9)** — подключить L0–L4 к выбору alias/глубины ИЛИ удалить | мёртвый код + упущенная экономия на дешёвых задачах | средний | низкий (решение) / средний (wiring) | низкий | − (medium/hard) | нейтр | − | + |
| 7 | **Incremental compaction** (O11) | ре-скан всего диалога ~15 регексами каждый раз | средний на границе compaction | низкий | низкий | − | нейтр | нейтр | нейтр |
| 8 | **V3 cleanup** — REMOVE computer_agent-orchestrator + recovery_kernel.LoopDetector; портировать raw-shell block/stale-obs/EV-decision | мёртвый дублирующий код, ложное ощущение возможностей | средний (maintainability) | средний | низкий (frozen, 0 импортёров) | нейтр | нейтр | нейтр | + |
| 9 | **Memory retention job** — последние N версий/задача + архив resolved-failures > TTL | агрегатный append-only рост | низкий сейчас | низкий | низкий | нейтр | − (long-term) | нейтр | нейтр |
| 10 | **Context OS 2 идеи** — портировать layer-cache + async-I/O в context_engine (НЕ переключаться) | средний-высокий | средний-высокий | средний | **сначала бенчмарк** | − | − | − | нейтр |

## Правила реализации (из мастер-промта)
- Самостоятельно можно: P0/P1 fixes, dead-work removal, perf/cache/async-lifecycle/bounded fixes,
  no-op wiring corrections. **Крупные архитектурные — только после бенчмарка.**
- Не подключать тяжёлый модуль в hot-path ради статуса WORK, если cost > польза.
- Не трогать frozen safety/red-team. Не увеличивать timeout / не скипать тесты вместо фикса lifecycle.

## Бенчмарк-харнесс (не построен в этом read-only проходе — TODO для реализации)
Раздел 16 требует A/B (BASELINE → +MEMORY → +CTX-OPT → +ROUTER → +REASONING → FULL) с
метриками VerifiedTaskSuccess / Latency p50-p95 / tokens / EffectiveCost / PeakRSS / Retries /
ContextTokens и `Efficiency = VerifiedTaskSuccess / EffectiveCost`. Это требует реальных моделей
и набора задач (owner hardware) — числа НЕ выдумываются. Пункт #1 (observability timers) —
предпосылка к этому харнессу: без per-stage метрик A/B нечем измерять.

## Приоритет владельца (учтено)
Владелец расставил параллельный приоритет по defense-in-depth (Security Hardening V1.1);
он идёт отдельной волной и по ROI сопоставим с пунктами 1–4 здесь. Оптимизация и hardening
не конфликтуют: разные файлы, разные инварианты.
