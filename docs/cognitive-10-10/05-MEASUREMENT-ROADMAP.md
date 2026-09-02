# 05 — Измерение: путь к production READY

Код: `bossman-core/bossman/cognitive/verify.py` + `runtime.py`
Тесты: `bossman-core/tests/test_cognitive_runtime.py`

## Четыре уровня (из ТЗ)

1. **Архитектура**: типизированная память, Context Compiler, thought state, journal, DAG — ✅ в этой ветке.
2. **Защита**: provenance, freshness, scope, quarantine, firewall, receipts, idempotency, approval, fail-closed — ✅ в этой ветке.
3. **Измерение**: A/B, ablation, mutation, hidden holdout, независимый verifier, CI, cost — ✅ harness в `verify.py`, данные — задача аудита (§Порядок).
4. **Production wiring**: единый runtime, dashboard, restart, container, telemetry, alerts, CI, rollback — каркас ✅ (`CognitiveRuntime`), деплой — после аудита.

## Гейты (пороги — константы в `verify.py`)

- `MEMORY_GATES`, `CONTEXT_GATES`, `REASONING_GATES`, `LONGTASK_GATES` — прямо из ТЗ.
- `evaluate_gates(metrics, gates)` — единая проверка; `nan` → fail.
- `verified_success_rate`: `verifier == executor` → trial исключён.
- `ab_compare(base, cand)`: SHIP только при `Δ ≥ 0.02` + непересекающиеся CI (Wilson 95%)
  + `cost_per_verified_cand ≤ 1.02 × base`. Экономия с падением качества → REGRESS/HOLD.
- `run_holdout(dataset_sha, items, score_fn, gates)`: счётчики (Leakage/Poison/Residual/
  Duplicates/Lost/FalseCompletion) суммируются, рейты усредняются.

## Порядок реализации 1–12 (статус)

1. ~~Атаковать benchmark~~ — осталось аудиту: зафиксировать dev/holdout SHA.
2. ✅ Durable journal + Working State (`tasks.py`, `runtime.working_state_to_thought`).
3. ✅ Provenance + типы памяти.
4. ✅ Compiler + ledger.
5. ✅ Raw fallback.
6. ✅ Adaptive Reasoning Controller.
7. ✅ Dynamic DAG + restart recovery.
8. ✅ Cost-aware Fable routing (EV; клиент — адаптер).
9. ⏳ A/B + ablation — harness готов, прогнать аудиту.
10. ⏳ 30–100-шаговые задачи с перезапусками — сценарий в `04-LONG-TASKS.md`.
11. ⏳ Red Team повторно атакует результат.
12. ⏳ Production READY — только после 9–11.

## Ablation/mutation (рекомендация аудиту)

- Выключать по одному: R-компоненты (V/P/C/X), ledger, firewall, multi-hypothesis,
  reconcile-гейт — каждый ablation обязан показать падение VerifiedSuccess
  (иначе компонент лишний).
- Mutation: подмена verifier==executor, stale evidence, injection — обязаны
  ловиться фильтром/firewall (PoisonAcceptance=0).
