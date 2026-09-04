"""SHADOW-классификатор режима рынка. Наблюдение, а не решение.

Он отвечает на вопрос «на что это похоже», и только. Ни BUY, ни SELL, ни
размера, ни плеча в его словаре нет физически — не «запрещено политикой», а
не существует в перечислении, поэтому и вернуть их нельзя.

Второе свойство важнее первого: он обязан уметь сказать «данных не хватает».
Классификатор, который на любых входных данных выдаёт красивый ярлык, вреден
именно тем, что выглядит одинаково уверенно и когда прав, и когда угадал.
Поэтому каждый вывод несёт список полей, которых не хватило, и объяснение —
что именно в наблюдении привело к ярлыку.

Связки цена/CVD/OI поддержаны явно, но НЕ как предсказания. «Цена вниз, CVD
плоский, OI вверх» — это описание того, что видно, а не обещание, что дальше
будет сквиз.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .schema import CoinwiseObservation

# Минимум, без которого разговор беспредметен.
CORE_FIELDS = ("price", "cvd", "open_interest")
# Порог «плоскости» CVD в долях: меньше — считаем, что дельта не изменилась.
FLAT_CVD_RATIO = 0.15
# Порог заметного движения цены между наблюдениями, в долях.
MOVE_RATIO = 0.001


class MarketState(str, Enum):
    """Всё, что классификатор вообще способен сказать."""

    OBSERVATION_ONLY = "OBSERVATION_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    RANGE = "RANGE"
    TREND = "TREND"
    POTENTIAL_RETEST = "POTENTIAL_RETEST"
    POTENTIAL_SHORT_SQUEEZE = "POTENTIAL_SHORT_SQUEEZE"
    POTENTIAL_LONG_SQUEEZE = "POTENTIAL_LONG_SQUEEZE"
    POTENTIAL_BREAKDOWN = "POTENTIAL_BREAKDOWN"
    STALE_OR_AMBIGUOUS = "STALE_OR_AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class MarketRead:
    """Вывод классификатора: ярлык, доводы и честно названная неуверенность."""

    state: MarketState
    confidence: float
    evidence: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    uncertainty: str = ""
    shadow: bool = True                # всегда: это не сигнал к действию

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "confidence": round(self.confidence, 3),
                "evidence": list(self.evidence), "missing": list(self.missing),
                "uncertainty": self.uncertainty, "shadow": self.shadow,
                "actionable": False, "instruction": None}


def _delta(now: float | None, before: float | None) -> float | None:
    if now is None or before is None:
        return None
    return now - before


def _ratio(delta: float | None, base: float | None) -> float | None:
    if delta is None or not base:
        return None
    return delta / abs(base)


def classify(observation: CoinwiseObservation,
             previous: CoinwiseObservation | None = None) -> MarketRead:
    """Прочитать наблюдение. Одно наблюдение — это ещё не движение.

    Без предыдущего кадра говорить о направлении нечем: цена «вниз» существует
    только относительно чего-то. Поэтому одиночное наблюдение честно называется
    OBSERVATION_ONLY, а не превращается в TREND по наклону, которого не видели.
    """
    if not observation.usable:
        return MarketRead(
            state=MarketState.STALE_OR_AMBIGUOUS, confidence=0.0,
            missing=observation.missing_fields(),
            uncertainty=(f"наблюдение не годится для разбора: "
                         f"{observation.validation_status.value}, "
                         f"возраст {observation.freshness_seconds:.0f} с"))

    missing = tuple(f for f in CORE_FIELDS if not observation.field_value(f).known)
    if missing:
        return MarketRead(
            state=MarketState.INSUFFICIENT_EVIDENCE, confidence=0.0, missing=missing,
            uncertainty="без цены, CVD и открытого интереса режим рынка не читается")

    if previous is None or not previous.usable:
        return MarketRead(
            state=MarketState.OBSERVATION_ONLY,
            confidence=min(observation.field_value("price").confidence, 0.6),
            evidence=(f"цена {observation.get('price')}",
                      f"CVD {observation.get('cvd')}",
                      f"OI {observation.get('open_interest')}"),
            missing=observation.missing_fields(),
            uncertainty="нет предыдущего наблюдения: направление не с чем сравнить")

    d_price = _ratio(_delta(observation.get("price"), previous.get("price")),
                     previous.get("price"))
    d_cvd = _ratio(_delta(observation.get("cvd"), previous.get("cvd")), previous.get("cvd"))
    d_oi = _ratio(_delta(observation.get("open_interest"),
                         previous.get("open_interest")), previous.get("open_interest"))
    if d_price is None or d_cvd is None or d_oi is None:
        return MarketRead(state=MarketState.INSUFFICIENT_EVIDENCE, confidence=0.0,
                          missing=CORE_FIELDS,
                          uncertainty="предыдущее наблюдение неполное — разницу не взять")

    price_down = d_price <= -MOVE_RATIO
    price_up = d_price >= MOVE_RATIO
    cvd_flat = abs(d_cvd) < FLAT_CVD_RATIO
    cvd_down = d_cvd <= -FLAT_CVD_RATIO
    cvd_up = d_cvd >= FLAT_CVD_RATIO
    oi_up = d_oi >= MOVE_RATIO
    oi_down = d_oi <= -MOVE_RATIO

    evidence = (f"цена {d_price:+.3%}", f"CVD {d_cvd:+.3%}", f"OI {d_oi:+.3%}")
    base = min(observation.field_value("price").confidence,
               observation.field_value("cvd").confidence,
               observation.field_value("open_interest").confidence)

    # Ниже — описания, а не прогнозы. Каждый случай назван так, как его называют
    # у графика, и снабжён оговоркой, что это лишь похожесть.
    if price_down and cvd_flat and oi_down:
        return MarketRead(MarketState.POTENTIAL_RETEST, base * 0.7, evidence,
                          observation.missing_fields(),
                          "цена ниже без продаж и с уходом позиций: похоже на снятие "
                          "плеча, а не на инициативу продавца. Это описание, не прогноз")
    if price_down and cvd_flat and oi_up:
        return MarketRead(MarketState.POTENTIAL_SHORT_SQUEEZE, base * 0.6, evidence,
                          observation.missing_fields(),
                          "цена ниже без продаж, позиции прибавляются: набор шорта. "
                          "Чем закончится — отсюда не видно")
    if price_down and cvd_down and oi_up:
        return MarketRead(MarketState.POTENTIAL_BREAKDOWN, base * 0.7, evidence,
                          observation.missing_fields(),
                          "продавец инициативен и позиции растут: похоже на пробой вниз")
    if price_up and cvd_up and oi_up:
        return MarketRead(MarketState.TREND, base * 0.7, evidence,
                          observation.missing_fields(),
                          "цена, дельта и позиции растут вместе: похоже на тренд")
    if price_up and cvd_flat and oi_down:
        return MarketRead(MarketState.POTENTIAL_LONG_SQUEEZE, base * 0.6, evidence,
                          observation.missing_fields(),
                          "цена выше без покупок и с уходом позиций: похоже на вынос шортов")
    if not price_up and not price_down:
        return MarketRead(MarketState.RANGE, base * 0.6, evidence,
                          observation.missing_fields(),
                          "цена стоит: это диапазон, а не подготовка к движению")
    return MarketRead(MarketState.OBSERVATION_ONLY, base * 0.5, evidence,
                      observation.missing_fields(),
                      "сочетание не описано ни одним из известных случаев")


__all__ = ["MarketState", "MarketRead", "classify", "CORE_FIELDS",
           "FLAT_CVD_RATIO", "MOVE_RATIO"]
