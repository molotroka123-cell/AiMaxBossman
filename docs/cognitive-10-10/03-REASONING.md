# 03 — Reasoning 7/10 → 10/10

Код: `bossman-core/bossman/cognitive/reasoning.py`
Тесты: `bossman-core/tests/test_cognitive_reasoning.py`

## Structured Thought State (храним только структуру, не CoT)

`goal, constraints, verified_facts, hypotheses, unknowns, candidate_plans,
selected_plan, dag, evidence, verification_requirements, confidence,
next_action, stop_condition`. Durable — таблица `thoughts`.

`unsupported_certainty()`: `confidence ≥ 0.85` без `verified_facts/evidence`
→ нарушение, пишется `metric_events(unsupported_certainty)`.

## Режимы

`FAST (≤0.25) | STANDARD | DEEP (≥0.55) | MULTI_HYPOTHESIS | ADVERSARIAL | HUMAN_APPROVAL`.
Приоритет: `irreversible → HUMAN_APPROVAL`, `security_sensitive → ADVERSARIAL`,
`(D ≥ multi ∧ unknowns ≥ 2) ∨ conflict ≥ 0.7 → MULTI_HYPOTHESIS`.

## Complexity Estimator

```text
D = 0.20N + 0.15G + 0.20R + 0.15U + 0.10C + 0.10F + 0.10B
```

Все входы 0..1 (clamp). `calibrate_thresholds(labeled)` подбирает
`fast_max/deep` как середины между классами на benchmark; версия порогов
хранится (`v1-default` → `v-calibrated` + benchmark SHA в отчёте).

## Multi-hypothesis (сложный баг)

`MultiHypothesisTracker`: ≥2 гипотезы → `cheapest_informative_test(costs)` →
`observe(hid, supports, strength)` (байес-нормализация, `strength ≥ 0.6`
против → `refuted`) → `confirmed_root_cause()` при `p ≥ 0.9`.
Чинить только подтверждённую root cause (уменьшает случайные большие патчи).

## Stop rule (`should_stop`)

`verified → approval_required → timeout → blocked_insufficient_evidence →
low_marginal_gain (<0.02) → cost_exceeds_benefit → continue`.
Недостаток доказательств — честный `BLOCKED`, не выдуманный ответ.

## Fable (`EV = P*Value − Cost − Latency − Risk`)

`should_call_fable()`: вызов при `EV_fable > EV_local`; для P0-security —
раньше (`p_improve > 0` достаточно). Реальный клиент подключается через
`CognitiveRuntime.attach_fable()` (интерфейс `FableClient`, не импорт).

## Метрики приёмки (см. `verify.REASONING_GATES`)

`CriticalFastPathErrors = 0, UnnecessaryDeepRate ≤ 0.10, ReasoningLoopRate = 0,
UnsupportedCertaintyRate = 0, RootCausePrecision ≥ 0.95`,
плюс `VerifiedSuccess > fixed-depth baseline` и
`CostPerVerifiedSuccess < always-deep baseline` (см. `ab_compare`).
