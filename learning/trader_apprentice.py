"""Trader Apprentice — deterministic BTC order-flow interpretation helpers.

This module does NOT place orders.  It converts a sequence of already-observed
market snapshots (price, CVD, OI, liquidations and profile levels) into an
explicit, auditable regime description that a local model can use as evidence.

Core design rules learned from the September 2026 BTC sessions:
* Price, CVD and OI must be interpreted together, not one indicator alone.
* OI is total open contracts, not a direct long/short meter.
* Falling OI during a selloff often means deleveraging/position closure; rising
  OI during a selloff is a materially worse warning because new leverage is
  being added while price is falling.
* CVD can show aggression, but price response decides whether that aggression is
  effective.  Falling CVD with price holding can be absorption.
* A level touch is not acceptance.  Prefer reclaim + hold/retest.
* Never compare absolute CVD/OI values across different providers/settings.
* Never mix CME chart levels with a spot/perp execution price without tagging
  instrument/source and accounting for the basis/spread.
* Missing live values remain UNKNOWN; they are never invented.

The output is analysis-only.  Any execution/risk action remains a separate,
explicitly-authorized step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


class Regime(str, Enum):
    UNKNOWN = "UNKNOWN"
    DELEVERAGING_SELL_OFF = "DELEVERAGING_SELL_OFF"
    BEARISH_LEVERAGE_EXPANSION = "BEARISH_LEVERAGE_EXPANSION"
    BULLISH_LEVERAGE_EXPANSION = "BULLISH_LEVERAGE_EXPANSION"
    RECOVERY_WITHOUT_LEVERAGE = "RECOVERY_WITHOUT_LEVERAGE"
    SHORT_COVERING_OR_ABSORPTION = "SHORT_COVERING_OR_ABSORPTION"
    SELL_ABSORPTION_CANDIDATE = "SELL_ABSORPTION_CANDIDATE"
    BUYER_FAILURE_CANDIDATE = "BUYER_FAILURE_CANDIDATE"
    NEUTRAL_BALANCE = "NEUTRAL_BALANCE"


class Stance(str, Enum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    LONG_CANDIDATE = "LONG_CANDIDATE"
    RISK_OFF = "RISK_OFF"


@dataclass(frozen=True)
class Snapshot:
    price: float
    cvd: Optional[float] = None
    open_interest: Optional[float] = None
    long_liquidations: Optional[float] = None
    short_liquidations: Optional[float] = None
    buy_volume: Optional[float] = None
    sell_volume: Optional[float] = None
    source: str = "unknown"
    instrument: str = "unknown"
    timestamp: str = ""


@dataclass(frozen=True)
class LevelMap:
    dval: Optional[float] = None
    dpoc: Optional[float] = None
    dvah: Optional[float] = None
    dopen: Optional[float] = None
    pday_low: Optional[float] = None
    pday_high: Optional[float] = None
    week_open: Optional[float] = None
    month_open: Optional[float] = None
    week_eq: Optional[float] = None
    year_eq: Optional[float] = None
    settlement_d: Optional[float] = None
    settlement_w: Optional[float] = None
    extra: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for key in (
            "dval", "dpoc", "dvah", "dopen", "pday_low", "pday_high",
            "week_open", "month_open", "week_eq", "year_eq",
            "settlement_d", "settlement_w",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = float(value)
        out.update({k: float(v) for k, v in self.extra.items()})
        return out


@dataclass(frozen=True)
class Analysis:
    price_direction: Direction
    cvd_direction: Direction
    oi_direction: Direction
    regime: Regime
    stance: Stance
    confidence: float
    reasons: tuple[str, ...]
    nearest_supports: tuple[tuple[str, float], ...] = ()
    nearest_resistances: tuple[tuple[str, float], ...] = ()
    reclaimed_levels: tuple[str, ...] = ()
    lost_levels: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "price_direction": self.price_direction.value,
            "cvd_direction": self.cvd_direction.value,
            "oi_direction": self.oi_direction.value,
            "regime": self.regime.value,
            "stance": self.stance.value,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "nearest_supports": [[k, v] for k, v in self.nearest_supports],
            "nearest_resistances": [[k, v] for k, v in self.nearest_resistances],
            "reclaimed_levels": list(self.reclaimed_levels),
            "lost_levels": list(self.lost_levels),
        }


def relative_change(current: float, previous: float) -> float:
    denom = max(abs(previous), 1e-12)
    return (current - previous) / denom


def direction(current: Optional[float], previous: Optional[float], *, epsilon: float) -> Direction:
    if current is None or previous is None:
        return Direction.UNKNOWN
    change = relative_change(current, previous)
    if change > epsilon:
        return Direction.UP
    if change < -epsilon:
        return Direction.DOWN
    return Direction.FLAT


def weighted_average_entry(entries: Iterable[tuple[float, float]]) -> float:
    """Weighted average entry from (price, size_weight) tuples."""
    rows = [(float(price), float(weight)) for price, weight in entries if float(weight) > 0]
    total = sum(weight for _, weight in rows)
    if total <= 0:
        raise ValueError("total entry weight must be positive")
    return sum(price * weight for price, weight in rows) / total


def long_return_pct(mark: float, average_entry: float) -> float:
    """Raw unlevered long return in percentage points, before fees/funding."""
    if average_entry <= 0:
        raise ValueError("average_entry must be positive")
    return (mark - average_entry) / average_entry * 100.0


def _level_context(previous_price: float, current_price: float, levels: LevelMap, limit: int = 3):
    ordered = sorted(levels.as_dict().items(), key=lambda kv: kv[1])
    supports = [(k, v) for k, v in ordered if v <= current_price]
    resistances = [(k, v) for k, v in ordered if v > current_price]
    reclaimed = [k for k, v in ordered if previous_price < v <= current_price]
    lost = [k for k, v in ordered if current_price < v <= previous_price]
    return (
        tuple(reversed(supports[-limit:])),
        tuple(resistances[:limit]),
        tuple(reclaimed),
        tuple(lost),
    )


def classify_regime(
    previous: Snapshot,
    current: Snapshot,
    *,
    price_epsilon: float = 0.0005,
    cvd_epsilon: float = 0.0008,
    oi_epsilon: float = 0.0008,
) -> tuple[Direction, Direction, Direction, Regime, Stance, list[str]]:
    p = direction(current.price, previous.price, epsilon=price_epsilon)
    c = direction(current.cvd, previous.cvd, epsilon=cvd_epsilon)
    o = direction(current.open_interest, previous.open_interest, epsilon=oi_epsilon)
    reasons: list[str] = [f"Price={p.value}, CVD={c.value}, OI={o.value}"]

    if c is Direction.UNKNOWN or o is Direction.UNKNOWN:
        reasons.append("CVD or OI is missing; do not infer the unavailable metric")
        return p, c, o, Regime.UNKNOWN, Stance.NO_TRADE, reasons

    # The primary matrix used in the user's September 2026 sessions.
    if p is Direction.DOWN and c is Direction.DOWN and o is Direction.DOWN:
        reasons.append("Sell aggression is accompanied by falling OI: position closure/deleveraging dominates")
        return p, c, o, Regime.DELEVERAGING_SELL_OFF, Stance.WATCH, reasons

    if p is Direction.DOWN and c is Direction.DOWN and o is Direction.UP:
        reasons.append("Price and CVD fall while OI expands: new leverage is entering a falling market")
        reasons.append("This is materially worse for a long thesis than simple deleveraging")
        return p, c, o, Regime.BEARISH_LEVERAGE_EXPANSION, Stance.RISK_OFF, reasons

    if p is Direction.UP and c is Direction.UP and o is Direction.UP:
        reasons.append("Price, aggressive buying and open positions expand together")
        return p, c, o, Regime.BULLISH_LEVERAGE_EXPANSION, Stance.LONG_CANDIDATE, reasons

    if p is Direction.UP and c is Direction.UP and o is Direction.DOWN:
        reasons.append("Price and CVD recover while OI falls: healthy recovery/covering, but not yet a strong new leverage trend")
        return p, c, o, Regime.RECOVERY_WITHOUT_LEVERAGE, Stance.WATCH, reasons

    if p is Direction.UP and c in (Direction.FLAT, Direction.DOWN) and o is Direction.DOWN:
        reasons.append("Price rises without CVD confirmation while OI falls: short covering and/or sell absorption")
        return p, c, o, Regime.SHORT_COVERING_OR_ABSORPTION, Stance.WATCH, reasons

    if p in (Direction.UP, Direction.FLAT) and c is Direction.DOWN and o in (Direction.DOWN, Direction.FLAT):
        reasons.append("CVD sells are not producing lower price: possible passive buyer/absorption")
        return p, c, o, Regime.SELL_ABSORPTION_CANDIDATE, Stance.WATCH, reasons

    if p is Direction.DOWN and c in (Direction.UP, Direction.FLAT) and o in (Direction.UP, Direction.FLAT):
        reasons.append("Buy aggression fails to lift price: possible hidden seller/buyer failure")
        return p, c, o, Regime.BUYER_FAILURE_CANDIDATE, Stance.RISK_OFF, reasons

    reasons.append("No high-conviction matrix pattern; treat as balance/noise")
    return p, c, o, Regime.NEUTRAL_BALANCE, Stance.WATCH, reasons


def analyze(
    previous: Snapshot,
    current: Snapshot,
    levels: Optional[LevelMap] = None,
    *,
    price_epsilon: float = 0.0005,
    cvd_epsilon: float = 0.0008,
    oi_epsilon: float = 0.0008,
) -> Analysis:
    p, c, o, regime, stance, reasons = classify_regime(
        previous,
        current,
        price_epsilon=price_epsilon,
        cvd_epsilon=cvd_epsilon,
        oi_epsilon=oi_epsilon,
    )

    supports: tuple[tuple[str, float], ...] = ()
    resistances: tuple[tuple[str, float], ...] = ()
    reclaimed: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()
    if levels is not None:
        supports, resistances, reclaimed, lost = _level_context(previous.price, current.price, levels)
        if reclaimed:
            reasons.append("Reclaimed levels: " + ", ".join(reclaimed))
        if lost:
            reasons.append("Lost levels: " + ", ".join(lost))

    # Confidence is about classification quality, not probability of profit.
    known = sum(x is not Direction.UNKNOWN for x in (p, c, o))
    confidence = 0.45 + 0.15 * known
    if regime in (Regime.BEARISH_LEVERAGE_EXPANSION, Regime.BULLISH_LEVERAGE_EXPANSION, Regime.DELEVERAGING_SELL_OFF):
        confidence += 0.08
    if levels is not None:
        confidence += 0.03
    confidence = min(confidence, 0.95)

    return Analysis(
        price_direction=p,
        cvd_direction=c,
        oi_direction=o,
        regime=regime,
        stance=stance,
        confidence=confidence,
        reasons=tuple(reasons),
        nearest_supports=supports,
        nearest_resistances=resistances,
        reclaimed_levels=reclaimed,
        lost_levels=lost,
    )


def accepted_above(history: Iterable[Snapshot], level: float, *, observations: int = 2, buffer_bps: float = 0.0) -> bool:
    """Conservative acceptance proxy: N latest observations remain above level.

    A single wick/touch is intentionally not considered acceptance.
    """
    rows = list(history)
    if observations <= 0 or len(rows) < observations:
        return False
    threshold = level * (1.0 + buffer_bps / 10_000.0)
    return all(row.price >= threshold for row in rows[-observations:])


def sweep_and_reclaim(history: Iterable[Snapshot], level: float, *, lookback: int = 4) -> bool:
    """True when recent price traded below level and latest observation is back above it."""
    rows = list(history)[-max(2, lookback):]
    if len(rows) < 2:
        return False
    return any(row.price < level for row in rows[:-1]) and rows[-1].price > level
