"""Классификация режима рынка по связке цена + CVD + OI + ликвидации.

Зачем: правило, работающее в шорт-сквизе, обычно не работает в боковике. Без
явного режима бэктест смешивает разные миры и выдаёт среднюю температуру по
больнице. Классификатор детерминированный и объяснимый — иначе его нельзя
проверить тестом.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Candle, MarketRegime

# Пороги в долях. Подобраны как «заметное движение», а не как оптимизированный
# параметр: любой подбор порогов под результат — это подгонка.
PRICE_EPS = 0.002        # 0.2% — меньше считаем боковиком
CVD_WEAK_RATIO = 0.35    # CVD растёт втрое слабее цены => покупатель не подтверждает
OI_EPS = 0.002
SQUEEZE_LIQ_RATIO = 1.5  # ликвидации одной стороны против другой


@dataclass(frozen=True, slots=True)
class RegimeReading:
    regime: MarketRegime
    price_change: float
    cvd_change: float
    oi_change: float
    long_liq: float
    short_liq: float
    reason: str


def _rel(now: float, before: float) -> float:
    """Относительное изменение; нулевая база даёт 0, а не деление на ноль."""
    if before == 0:
        return 0.0
    return (now - before) / abs(before)


def classify(window: list[Candle], *, lookback: int = 10) -> RegimeReading:
    """Режим по окну свечей. Смотрит ТОЛЬКО назад — окно уже обрезано вызывающим."""
    if len(window) < 2:
        return RegimeReading(MarketRegime.UNKNOWN, 0.0, 0.0, 0.0, 0.0, 0.0,
                             "insufficient window")
    head = window[-1]
    base = window[max(0, len(window) - 1 - lookback)]
    dp = _rel(head.close, base.close)
    dc = _rel(head.cvd, base.cvd)
    doi = _rel(head.open_interest, base.open_interest)
    long_liq = sum(c.long_liquidations for c in window[-lookback:])
    short_liq = sum(c.short_liquidations for c in window[-lookback:])

    def out(regime: MarketRegime, reason: str) -> RegimeReading:
        return RegimeReading(regime, dp, dc, doi, long_liq, short_liq, reason)

    # Сквизы определяются перекосом ликвидаций, а не «ощущением вертикальности».
    if dp > PRICE_EPS and short_liq > 0 and short_liq > long_liq * SQUEEZE_LIQ_RATIO:
        return out(MarketRegime.SHORT_SQUEEZE, "price up with dominant short liquidations")
    if dp < -PRICE_EPS and long_liq > 0 and long_liq > short_liq * SQUEEZE_LIQ_RATIO:
        return out(MarketRegime.LONG_SQUEEZE, "price down with dominant long liquidations")

    if dp > PRICE_EPS:
        if doi > OI_EPS:
            # Слабый CVD при растущей цене и растущем OI — рост на шортистах,
            # а не на покупателе. Это ровно тот случай, где нельзя усредняться.
            if dc < dp * CVD_WEAK_RATIO:
                return out(MarketRegime.PRICE_UP_CVD_WEAK_OI_UP, "price up, CVD lags, OI up")
            return out(MarketRegime.PRICE_UP_CVD_UP_OI_UP, "price up, CVD up, OI up")
        return out(MarketRegime.CONTINUATION, "price up without OI expansion")
    if dp < -PRICE_EPS:
        if doi > OI_EPS:
            return out(MarketRegime.PRICE_DOWN_OI_UP, "price down, OI up (new shorts)")
        if doi < -OI_EPS:
            return out(MarketRegime.PRICE_DOWN_OI_DOWN, "price down, OI down (unwind)")
        return out(MarketRegime.CONTINUATION, "price down without OI change")
    return out(MarketRegime.RANGE, "price change below threshold")


def failed_breakout(window: list[Candle], level: float, *, lookback: int = 20) -> bool:
    """Пробой уровня, не удержавшийся к закрытию окна.

    Отдельная функция, потому что несостоявшийся пробой определяется не
    состоянием потоков, а фактом «вышли за уровень и вернулись».

    Прокол ищется по ВСЕМУ окну, а закрытие проверяется по последней свече.
    Ограничивать поиск прокола хвостом нельзя: сам прокол по определению
    случился раньше возврата, и такой поиск не нашёл бы ни одного ложного
    пробоя — функция молча возвращала бы False на самом важном кейсе.
    """
    if len(window) < 2 or level <= 0:
        return False
    tail = window[-lookback:] if lookback > 0 else window
    pierced_at = next((i for i, c in enumerate(tail) if c.high > level), None)
    if pierced_at is None:
        return False
    # Возврат должен произойти ПОСЛЕ прокола, иначе это не «не удержались».
    return pierced_at < len(tail) - 1 and tail[-1].close < level
