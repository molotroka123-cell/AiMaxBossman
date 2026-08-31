"""Learning Quality Guard / Anti-Degradation Layer.

Тонкий детерминированный слой поверх существующей архитектуры. НЕ создаёт второй
Memory/Context/Router/Policy/Verifier/EventBus — только ПРИНИМАЕТ измерения
(same-model Raw vs Model+Bossman A/B, verified-успех) и ВЫДАЁТ вердикт/стадию.

Инварианты:
* доказательство = verified-успех, self-score ≠ evidence (req.6);
* VerifiedSuccess degradation ≤ 1 п.п. (req.3), IntelligenceRetention ≥ 0.99 (req.4);
* запрет single-episode promotion (req.5); per-task-class regression gates (req.9);
* secret holdout недоступен learning/memory/skills (req.2);
* candidate→validation→shadow→verified→owner, автопромоушена нет (req.7);
* context raw-evidence fallback (req.8); rollback-метаданные (req.10);
* security hard gates неоптимизируемы ради score; Self-Improvement — proposal-only.
"""
from __future__ import annotations

from .ab import (ABVerdict, DEGRADATION_MAX_PP, MIN_EPISODES, RETENTION_MIN,
                 context_fallback_to_raw, evaluate_ab)
from .holdout import HoldoutViolation, SecretHoldout
from .models import (ABResult, Candidate, PromotionStage, RollbackInfo,
                     SecuritySnapshot)
from .promotion import (MIN_SHADOW_RUNS, SecurityRegression, advance,
                        assert_no_security_regression, promote)
from .service import (get_holdout, guard_promotion, reject_if_holdout, set_holdout)

__all__ = [
    "ABResult", "ABVerdict", "Candidate", "PromotionStage", "RollbackInfo",
    "SecuritySnapshot", "SecretHoldout", "HoldoutViolation", "SecurityRegression",
    "evaluate_ab", "context_fallback_to_raw", "advance", "promote",
    "assert_no_security_regression", "DEGRADATION_MAX_PP", "RETENTION_MIN",
    "MIN_EPISODES", "MIN_SHADOW_RUNS",
    "set_holdout", "get_holdout", "reject_if_holdout", "guard_promotion",
]
