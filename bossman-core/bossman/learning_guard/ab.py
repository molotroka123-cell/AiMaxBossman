"""A/B оценка и анти-деградационные гейты (req.1,3,4,5,9).

Same-model Raw vs Model+Bossman. Всё детерминировано, без LLM. Доказательство —
verified-успех, не self-score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import ABResult

# Гейты (значения из мастер-промта).
DEGRADATION_MAX_PP = 1.0          # VerifiedSuccess degradation ≤ 1 п.п. (req.3)
RETENTION_MIN = 0.99              # IntelligenceRetention ≥ 0.99 (req.4)
MIN_EPISODES = 20                # запрет single-episode promotion (req.5)


@dataclass(frozen=True)
class ABVerdict:
    episodes: int
    raw_success: float
    guarded_success: float
    degradation_pp: float             # (raw - guarded) в п.п.; >0 = guarded хуже
    intelligence_retention: float     # guarded / raw
    per_class_ok: bool
    enough_episodes: bool
    passing: bool
    reasons: tuple[str, ...] = ()


def _rate(results: list[ABResult], attr: str) -> float:
    n = len(results)
    return (sum(1 for r in results if getattr(r, attr)) / n) if n else 0.0


def _retention(raw: float, guarded: float) -> float:
    if raw <= 0.0:
        # Нечего терять: raw ничего не решил верно — Bossman не может «понизить интеллект».
        return 1.0
    return round(guarded / raw, 4)


def evaluate_ab(results: Iterable[ABResult], *,
                degradation_max_pp: float = DEGRADATION_MAX_PP,
                retention_min: float = RETENTION_MIN,
                min_episodes: int = MIN_EPISODES) -> ABVerdict:
    """Свести A/B в вердикт. Использует ТОЛЬКО verified-поля (req.6)."""
    rs = list(results)
    n = len(rs)
    raw = _rate(rs, "raw_verified")
    guarded = _rate(rs, "guarded_verified")
    degradation_pp = round((raw - guarded) * 100.0, 4)
    retention = _retention(raw, guarded)

    # per-task-class regression gate (req.9): ни один класс не должен просесть.
    by_class: dict[str, list[ABResult]] = {}
    for r in rs:
        by_class.setdefault(r.task_class, []).append(r)
    per_class_ok = True
    reasons: list[str] = []
    for cls, group in sorted(by_class.items()):
        d = round((_rate(group, "raw_verified") - _rate(group, "guarded_verified")) * 100.0, 4)
        if d > degradation_max_pp:
            per_class_ok = False
            reasons.append(f"class {cls} regressed -{d}pp")

    enough = n >= min_episodes
    if not enough:
        reasons.append(f"insufficient episodes {n} < {min_episodes}")
    if degradation_pp > degradation_max_pp:
        reasons.append(f"degradation {degradation_pp}pp > {degradation_max_pp}pp")
    if retention < retention_min:
        reasons.append(f"retention {retention} < {retention_min}")

    passing = bool(enough and per_class_ok
                   and degradation_pp <= degradation_max_pp
                   and retention >= retention_min)
    return ABVerdict(n, round(raw, 4), round(guarded, 4), degradation_pp, retention,
                     per_class_ok, enough, passing, tuple(reasons))


def context_fallback_to_raw(retention: float, *, retention_min: float = RETENTION_MIN) -> bool:
    """req.8: если guarded-контекст снижает retention ниже порога — вернуться к
    сырым доказательствам (raw evidence). Возвращает True = использовать raw."""
    return retention < retention_min
