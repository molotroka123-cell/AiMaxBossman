# LEARNING QUALITY GUARD / ANTI-DEGRADATION LAYER

Тонкий детерминированный слой (`bossman/learning_guard/`) поверх существующей
архитектуры. **Не** второй Memory/Context/Router/Policy/Verifier/EventBus — он
только ПРИНИМАЕТ измерения (same-model Raw vs Model+Bossman A/B, verified-успех) и
ВЫДАЁТ вердикт/стадию. Персистентность — через существующую память вызывающего.
Self-Improvement остаётся proposal-only; security hard gates неоптимизируемы ради
benchmark score.

## Что это решает
Не дать «обучению» тихо ухудшить систему: любое продвижение памяти/навыка/контекста/
конфига должно ДОКАЗАТЬ, что Model+Bossman не хуже Raw модели (на одинаковой модели
и одинаковых задачах), прежде чем стать production — и только по решению владельца.

## 10 требований → реализация

| # | Требование | Реализация | Тест |
|---|---|---|---|
| 1 | same-model Raw vs Model+Bossman A/B | `ABResult(raw_verified, guarded_verified)` + `evaluate_ab()` | `test_ab_aggregates_same_model...` |
| 2 | Secret Holdout, недоступный learning/memory/skills | `SecretHoldout.seal()` хранит только солёные хеши; есть `is_holdout/reject_if_holdout/filter_learnable`, **нет** перечисления | `test_secret_holdout_rejects_and_cannot_be_enumerated` |
| 3 | VerifiedSuccess degradation ≤ 1 п.п. | `ABVerdict.degradation_pp ≤ DEGRADATION_MAX_PP` | `test_degradation_gate_blocks_over_1pp`, `..._within_1pp_passes` |
| 4 | IntelligenceRetention ≥ 0.99 | `ABVerdict.intelligence_retention = guarded/raw ≥ RETENTION_MIN` | `test_retention_gate` |
| 5 | запрет single-episode promotion | `MIN_EPISODES=20` + `MIN_SHADOW_RUNS=20` | `test_single_episode_never_passes`, pipeline-тест |
| 6 | Bossman self-score ≠ evidence | гейты используют только `*_verified`; `bossman_self_score` — audit-only | `test_self_score_is_not_evidence` |
| 7 | candidate→validation→shadow→verified→owner | `PromotionStage` + `advance()` (до VERIFIED) + `promote(owner_approved=…)` | `test_promotion_pipeline_requires_each_stage_and_owner` |
| 8 | Context raw-evidence fallback | `context_fallback_to_raw(retention)` → True, если retention < порога | `test_context_fallback_to_raw...` |
| 9 | per-task-class regression gates | per-class деградация; любой просевший класс → fail, даже если среднее ок | `test_per_class_regression_blocks_even_if_overall_ok` |
| 10 | rollback metadata | `RollbackInfo` обязателен в `promote()`; кандидат несёт его | `test_promotion_carries_rollback_metadata` |

Плюс инварианты:
- **security hard gates неоптимизируемы**: `assert_no_security_regression(before, after)`
  блокирует продвижение при росте leaks/bypasses или падении containment_rate —
  нельзя «улучшить efficiency», ослабив security (`test_security_regression_blocks...`).
- **Self-Improvement proposal-only**: слой НИЧЕГО не продвигает сам; `OWNER_PROMOTED`
  достижим только через `promote(owner_approved=True, rollback=…)`.
- **fast path не тронут**: чистые функции, без сети/моделей/IO; `service.reject_if_holdout`
  — no-op, пока holdout не задан.

## Тонкие точки интеграции (adapters, не второй движок)

```python
from bossman import learning_guard as lg

# 1) На входе learning/memory/skills ingest (benchmark-эпоха задаёт holdout):
lg.set_holdout(lg.SecretHoldout.seal(owner_holdout_task_ids))
lg.reject_if_holdout(task_id)          # HoldoutViolation, если это holdout; иначе no-op

# 2) Промоушен памяти/навыка/контекста/конфига — один вызов:
candidate, verdict = lg.guard_promotion(
    lg.Candidate(kind="skill", ref=skill_id),
    ab_results,                        # list[ABResult] из real A/B
    security_before=sec0, security_after=sec1,
    shadow_runs=n_shadow)
# candidate дойдёт максимум до VERIFIED; дальше — только владелец:
final = lg.promote(candidate, owner_approved=True,
                   rollback=lg.RollbackInfo(prev_stage="verified", prev_ref=prev))

# 3) Контекст: если измеренный retention просел — брать сырые доказательства:
if lg.context_fallback_to_raw(measured_retention):
    use_raw_evidence_context()
```

Существующие промоушен-пути, которые ДОЛЖНЫ проходить через этот гейт (когда
появятся реальные A/B-данные): `context_engine.memory.promote`,
`bossman_v3/skill_factory` (frozen), `cybersec/learning`, `bossman_v3/self_improvement`
(proposal-only). Guard — единая анти-деградационная проверка для всех них, а не
их дубликат.

## Честная граница (без fake-green)
Слой РЕАЛИЗОВАН и покрыт тестами, но **сам по себе не доказывает улучшения**.
`ABResult` требует РЕАЛЬНОГО same-model прогона Raw vs Model+Bossman на наборе задач
с внешней верификацией — это owner hardware + honest benchmark. Пока такого прогона
нет: любое «Bossman уже лучше» было бы fake-green. Guard — это ворота, которые не
дадут продвинуть регрессию, когда прогон появится; числа он не выдумывает.
