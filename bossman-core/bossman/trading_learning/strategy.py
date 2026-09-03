"""Нормализация: набор claim'ов → проверяемое правило решения.

Мы учимся не сигналам «купить», а условиям, которые можно посчитать на
исторических данных. Правило без инвалидации и без стопа не нормализуется —
такое «правило» невозможно опровергнуть, а значит невозможно и проверить.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Claim, ClaimType, MarketRegime, Decision


class StrategyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LevelZone:
    """Зона интереса: спрос/предложение, дневной уровень, POC, value area."""

    kind: str          # demand | supply | daily | weekly | value_area | poc | extreme
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise StrategyError("zone low above high")

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class StrategyRule:
    """Нормализованное правило. Всё, что нужно, чтобы прогнать его по истории."""

    rule_id: str
    asset: str
    venue: str
    timeframe: str
    direction: Decision
    zones: list[LevelZone] = field(default_factory=list)
    required_regimes: tuple[MarketRegime, ...] = ()
    forbidden_regimes: tuple[MarketRegime, ...] = ()
    stop_pct: float = 0.0                 # доля от цены входа
    take_pct: float = 0.0
    max_hold_bars: int = 0
    no_averaging_regimes: tuple[MarketRegime, ...] = ()
    derived_from: tuple[str, ...] = ()    # ссылки на claim'ы (провенанс правила)
    author_opinion_only: bool = False     # правило собрано только из мнений автора

    def __post_init__(self) -> None:
        if self.direction not in (Decision.LONG, Decision.SHORT):
            raise StrategyError("rule direction must be LONG or SHORT")
        if self.stop_pct <= 0:
            raise StrategyError("rule without a stop cannot be falsified")
        if self.max_hold_bars <= 0:
            raise StrategyError("rule without a time limit never invalidates")
        if not self.derived_from:
            raise StrategyError("rule without provenance is not accepted")

    def zone_hit(self, price: float) -> bool:
        return any(z.contains(price) for z in self.zones) if self.zones else True

    def regime_ok(self, regime: MarketRegime) -> bool:
        if regime in self.forbidden_regimes:
            return False
        if self.required_regimes and regime not in self.required_regimes:
            return False
        return True

    def averaging_allowed(self, regime: MarketRegime) -> bool:
        """Запрет усреднения — часть правила, а не совет в комментарии."""
        return regime not in self.no_averaging_regimes

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "asset": self.asset, "venue": self.venue,
                "timeframe": self.timeframe, "direction": self.direction.value,
                "zones": [{"kind": z.kind, "low": z.low, "high": z.high} for z in self.zones],
                "required_regimes": [r.value for r in self.required_regimes],
                "forbidden_regimes": [r.value for r in self.forbidden_regimes],
                "stop_pct": self.stop_pct, "take_pct": self.take_pct,
                "max_hold_bars": self.max_hold_bars,
                "no_averaging_regimes": [r.value for r in self.no_averaging_regimes],
                "derived_from": list(self.derived_from),
                "author_opinion_only": self.author_opinion_only}


def normalize_strategy(claims: list[Claim], *, rule_id: str, direction: Decision,
                       zones: list[LevelZone] | None = None,
                       stop_pct: float = 0.01, take_pct: float = 0.02,
                       max_hold_bars: int = 48) -> StrategyRule:
    """Собрать правило из claim'ов и честно пометить его происхождение.

    Правило, собранное ТОЛЬКО из AUTHOR_CLAIM/HYPOTHESIS, помечается
    author_opinion_only=True. Такое правило допускается к бэктесту (проверять
    гипотезы — и есть задача), но память запрещает продвигать его в
    процедурную без независимых данных.
    """
    if not claims:
        raise StrategyError("no claims to normalize")
    assets = {c.asset for c in claims}
    venues = {c.venue for c in claims}
    timeframes = {c.timeframe for c in claims}
    # Смешение активов/таймфреймов в одном правиле — источник ложных выводов.
    if len(assets) != 1:
        raise StrategyError(f"claims mix assets: {sorted(assets)}")
    if len(venues) != 1:
        raise StrategyError(f"claims mix venues: {sorted(venues)}")
    if len(timeframes) != 1:
        raise StrategyError(f"claims mix timeframes: {sorted(timeframes)}")

    usable = [c for c in claims if c.verification_status.value != "QUARANTINED"]
    if not usable:
        raise StrategyError("all claims are quarantined")

    hard_types = {ClaimType.ENTRY_CONDITION, ClaimType.EXIT_CONDITION,
                  ClaimType.INVALIDATION, ClaimType.RISK_RULE,
                  ClaimType.MARKET_OBSERVATION, ClaimType.POSITION_MANAGEMENT}
    evidence_backed = [c for c in usable if c.claim_type in hard_types]

    no_avg: tuple[MarketRegime, ...] = ()
    if any(c.claim_type in (ClaimType.RISK_RULE, ClaimType.POSITION_MANAGEMENT)
           for c in usable):
        # Правило «не добавлять на вертикальном росте» кодируется режимами,
        # в которых усреднение запрещено, а не текстом.
        no_avg = (MarketRegime.PRICE_UP_CVD_WEAK_OI_UP, MarketRegime.SHORT_SQUEEZE,
                  MarketRegime.LONG_SQUEEZE)

    return StrategyRule(
        rule_id=rule_id, asset=assets.pop(), venue=venues.pop(), timeframe=timeframes.pop(),
        direction=direction, zones=list(zones or []),
        forbidden_regimes=(MarketRegime.FAILED_BREAKOUT,),
        stop_pct=stop_pct, take_pct=take_pct, max_hold_bars=max_hold_bars,
        no_averaging_regimes=no_avg,
        derived_from=tuple(f"{c.source_id}@{c.timestamp_start:.1f}" for c in usable),
        author_opinion_only=not evidence_backed)
