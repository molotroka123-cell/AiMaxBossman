"""Explainable fit: an internal heuristic of how realistic the tender is for OUR company. NOT a probability of winning.

Weights follow the spec: 25 qualification, 20 suppliers, 20 capital, 15 time, 10 experience, 10 operational/geo risk.
Unknown inputs get a neutral 0.5 and LOWER the confidence; they are listed in `unknowns`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from .rules import LOW_RISK_SEED, RESTRICTED_SEED, country2

WEIGHTS = {"qualification": 25, "suppliers": 20, "capital": 20, "time": 15, "experience": 10, "risk": 10}
DEFAULT_CAPITAL_LIMIT_USD = Decimal("50000")


def _prefix_match(cpvs: list[str], prefixes: list[str]) -> bool:
    return any(c.startswith(tuple(prefixes)) for c in cpvs) if prefixes else False


def components(tender: dict, profile: dict, suppliers: list[dict], outcomes: list[dict], *, capital_limit_usd: Decimal,
               back_to_back: bool, usd_value: Decimal | None, today: date) -> dict:
    cpvs = tender.get("cpv", [])
    out: dict[str, tuple[float | None, str]] = {}

    caps = profile.get("cpv_capabilities") or []
    if caps and cpvs:
        share = sum(1 for c in cpvs if c.startswith(tuple(caps))) / len(cpvs)       # share of the notice's CPV codes we cover
        out["qualification"] = ((1.0, f"возможности покрывают {share:.0%} CPV тендера") if share >= 0.5
                                else (0.4, f"покрыто только {share:.0%} CPV тендера: основной предмет вне профиля") if share > 0
                                else (0.1, "CPV вне заявленных возможностей"))
    elif caps:
        out["qualification"] = (None, "у тендера нет CPV")
    else:
        out["qualification"] = (None, "возможности компании не заполнены")

    if suppliers:
        confirmed = [s for s in suppliers if s.get("confirmed") and _prefix_match(cpvs, s.get("cpv_prefixes", []))]
        quoted = [s for s in suppliers if _prefix_match(cpvs, s.get("cpv_prefixes", []))]
        out["suppliers"] = ((1.0, "есть подтверждённый поставщик") if confirmed
                            else (0.5, "есть поставщик без подтверждения") if quoted else (0.1, "поставщик под этот CPV не найден"))
    else:
        out["suppliers"] = (None, "реестр поставщиков пуст")

    if usd_value is None:
        out["capital"] = (None, "сумма контракта неизвестна или нет курса")
    elif back_to_back:
        out["capital"] = (0.9, "сделка состыкована (заказ + поставщик): лимит капитала не ограничивает, нужно одобрение владельца")
    else:
        need = usd_value * Decimal("0.85")
        out["capital"] = ((1.0, f"оборотный капитал ~{need:.0f} USD в лимите {capital_limit_usd}") if need <= capital_limit_usd
                          else (0.4, f"нужно ~{need:.0f} USD, лимит {capital_limit_usd} (до 2x)") if need <= capital_limit_usd * 2
                          else (0.0, f"нужно ~{need:.0f} USD, лимит {capital_limit_usd}: без состыковки заказа и поставщика не подходит"))

    left = None
    if tender.get("deadline"):
        try:
            left = (datetime.fromisoformat(tender["deadline"].replace("Z", "+00:00")).date() - today).days
        except ValueError:
            pass
    out["time"] = (None, "срок неизвестен") if left is None else ((1.0 if left >= 30 else 0.7 if left >= 14 else 0.4 if left >= 7 else 0.1), f"до срока {left} дн.")

    wins = [o for o in outcomes if o.get("result") == "won" and _prefix_match(o.get("cpv", []), [c[:4] for c in cpvs])]
    out["experience"] = ((1.0 if len(wins) >= 3 else 0.6 if wins else 0.1), f"выигранных контрактов в этой категории: {len(wins)}")

    ctry = country2(tender.get("country")) or country2(tender.get("jurisdiction"))
    if not ctry:
        out["risk"] = (None, "страна неизвестна")
    elif ctry in RESTRICTED_SEED:
        out["risk"] = (0.0, "страна в списке ограничений")
    elif ctry in LOW_RISK_SEED:
        out["risk"] = (1.0, "низкий страновой риск (seed)")
    else:
        out["risk"] = (0.4, "страна вне проверенного списка")
    return out


def fit(tender: dict, profile: dict | None, suppliers: list[dict] | None = None, outcomes: list[dict] | None = None, *,
        capital_limit_usd: Decimal = DEFAULT_CAPITAL_LIMIT_USD, back_to_back: bool = False, usd_value: Decimal | None = None,
        today: date | None = None, weights: dict | None = None) -> dict:
    w = weights or WEIGHTS
    comps = components(tender, profile or {}, suppliers or [], outcomes or [], capital_limit_usd=capital_limit_usd,
                       back_to_back=back_to_back, usd_value=usd_value, today=today or datetime.now(timezone.utc).date())
    total = sum(w.values())
    score = sum(w[k] * (0.5 if v is None else v) for k, (v, _) in comps.items()) / total * 100
    known = sum(w[k] for k, (v, _) in comps.items() if v is not None) / total
    return {
        "fit_score": round(score), "confidence": round(known, 2),
        "components": {k: {"weight": w[k], "value": v, "why": why} for k, (v, why) in comps.items()},
        "unknowns": [k for k, (v, _) in comps.items() if v is None],
        "disclaimer": "внутренняя эвристика реализуемости; это НЕ вероятность победы",
    }


def tier(score: int) -> str:
    return "EASY" if score >= 75 else "MEDIUM" if score >= 50 else "HARD"


def classify(gate_overall: str, fit_result: dict) -> dict:
    """complexity: final class per spec. tier_if_cleared: provisional planning tier from the score, ALWAYS shown (never hidden),
    with low_confidence=True when too many inputs are unknown; low confidence also keeps the final class at REVIEW_REQUIRED."""
    t = tier(fit_result["fit_score"])
    low = fit_result["confidence"] < 0.4
    if gate_overall == "BLOCKED":
        return {"complexity": "BLOCKED", "tier_if_cleared": None, "low_confidence": low}
    if gate_overall == "REVIEW_REQUIRED" or low:
        return {"complexity": "REVIEW_REQUIRED", "tier_if_cleared": t, "low_confidence": low}
    return {"complexity": t, "tier_if_cleared": t, "low_confidence": False}
