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
  This rule is now ENFORCED, not merely documented: every metric carries an
  immutable series identity, and two snapshots whose identities do not prove
  they describe the SAME series are never classified.  A CVD printed in
  contracts by one venue and in USD by another is not a smaller number, it is
  a different measurement, and subtracting one from the other produces a
  direction that never existed.
* Never mix CME chart levels with a spot/perp execution price without tagging
  instrument/source and accounting for the basis/spread.
* Missing live values remain UNKNOWN; they are never invented.  A MISSING
  identity is likewise never assumed to match: two snapshots that both say
  "unknown" are the dangerous case, not the safe one, because that is exactly
  how two different providers collide.

The output is analysis-only.  Any execution/risk action remains a separate,
explicitly-authorized step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import ClassVar, Iterable, Optional


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


#: Strings a feed uses to say "I do not know".  They are placeholders, not
#: identities: treating two of them as equal is what lets a Binance perp CVD be
#: compared against a CME futures CVD because both rows happened to default.
UNKNOWN_TOKENS = frozenset({"", "unknown", "none", "null", "n/a", "na", "-", "?"})


def _stated(value: Optional[str]) -> bool:
    """True when a field actually names something rather than shrugging."""
    return isinstance(value, str) and value.strip().lower() not in UNKNOWN_TOKENS


class Incompatibility(str, Enum):
    """Why two snapshots may not be compared.  Each value is a refusal reason,
    reported to the caller instead of a fabricated direction."""
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    CVD_SERIES_MISMATCH = "CVD_SERIES_MISMATCH"
    OI_SERIES_MISMATCH = "OI_SERIES_MISMATCH"
    IDENTITY_MISSING = "IDENTITY_MISSING"
    TIMESTAMP_NOT_ADVANCING = "TIMESTAMP_NOT_ADVANCING"


@dataclass(frozen=True)
class SeriesId:
    """Immutable identity of ONE metric series.

    Two CVD readings are comparable only when every field below matches.  The
    fields are not decoration: `normalization` separates contracts from USD,
    `aggregation` separates a 1m bar from a tick print, `market` separates spot
    from perp from CME, and `version` separates a provider's v1 formula from the
    v2 that replaced it.  A change in any one of them makes the difference
    between two numbers meaningless.

    `version` may legitimately be empty (an unversioned feed is a real thing) but
    still participates in equality.  The rest must be stated.
    """
    provider: str = ""
    instrument: str = ""
    market: str = ""
    aggregation: str = ""
    normalization: str = ""
    version: str = ""

    #: Fields that must actually name something for the identity to be usable.
    REQUIRED: ClassVar[tuple[str, ...]] = (
        "provider", "instrument", "market", "aggregation", "normalization")

    def is_complete(self) -> bool:
        return all(_stated(getattr(self, name)) for name in self.REQUIRED)

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.REQUIRED if not _stated(getattr(self, name)))

    def key(self) -> tuple[str, ...]:
        """Comparison key.  Case- and whitespace-insensitive, because
        "Binance" and "binance " are the same venue and a spurious mismatch is
        as wrong as a spurious match."""
        return tuple(
            str(getattr(self, name)).strip().lower()
            for name in ("provider", "instrument", "market",
                         "aggregation", "normalization", "version")
        )

    @property
    def series_id(self) -> str:
        """Stable flat identifier, for logs and receipts."""
        return "|".join(self.key())


def _parse_ts(value: str) -> Optional[float]:
    """ISO-8601 timestamp to epoch seconds, or None when it does not parse.

    An unparseable timestamp is NOT treated as zero: it is missing identity, and
    the caller refuses.  Naive timestamps are read as UTC rather than as local
    time, so the same recording classified on two machines cannot disagree.
    """
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


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
    #: Identity of the CVD series this row's `cvd` was read from.  Required
    #: whenever `cvd` is present and is to be compared with another snapshot.
    cvd_series: Optional[SeriesId] = None
    #: Identity of the open-interest series behind `open_interest`.
    oi_series: Optional[SeriesId] = None


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


#: An entry price is money, and money in this domain is quoted to the cent.
#: Binary floats represent neither 0.15 nor most prices exactly, so the mean of
#: three equal tranches around 79 350 lands on 79350.00000000001 and stops being
#: equal to the middle price a trader actually filled at. The contract is stated
#: here rather than fitted to a test: the weighted mean is computed in decimal
#: arithmetic over the decimal forms of the inputs and returned to the cent.
#: Sub-cent precision in an average entry price is not information, it is noise
#: from the representation.
PRICE_QUANTUM = Decimal("0.01")


def _money(value: float | int | str) -> Decimal:
    """Decimal from the value's decimal form, never from its binary expansion."""
    return Decimal(str(value))


def weighted_average_entry(entries: Iterable[tuple[float, float]]) -> float:
    """Weighted average entry from (price, size_weight) tuples, exact to the cent.

    Returns a float so callers are unchanged; the arithmetic behind it is
    decimal, so equal tranches average to the middle price exactly instead of
    to the middle price plus a representation error.
    """
    rows = [(_money(price), _money(weight)) for price, weight in entries
            if _money(weight) > 0]
    total = sum((weight for _, weight in rows), Decimal(0))
    if total <= 0:
        raise ValueError("total entry weight must be positive")
    mean = sum((price * weight for price, weight in rows), Decimal(0)) / total
    return float(mean.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP))


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


def series_compatibility(
    previous: Snapshot,
    current: Snapshot,
) -> tuple[Optional[Incompatibility], list[str]]:
    """Prove two snapshots describe the SAME series before anything subtracts them.

    Returns `(None, [])` when the pair is comparable, otherwise the first
    incompatibility found and human-readable reasons.  Nothing here guesses: a
    field that does not name a series is a refusal, never a wildcard.

    The checks are ordered so the reported reason is the most specific one that
    applies — a caller who fixes the named problem makes progress rather than
    discovering the next hidden one.
    """
    reasons: list[str] = []

    # 1. The row-level identity must be stated at all.
    for label, row in (("previous", previous), ("current", current)):
        if not _stated(row.source):
            reasons.append(f"{label} snapshot does not state its source")
        if not _stated(row.instrument):
            reasons.append(f"{label} snapshot does not state its instrument")
    if reasons:
        return Incompatibility.IDENTITY_MISSING, reasons

    # 2. Same venue, same instrument.
    if previous.source.strip().lower() != current.source.strip().lower():
        return Incompatibility.SOURCE_MISMATCH, [
            f"source differs: {previous.source!r} vs {current.source!r}; "
            "absolute values from two providers are not a series"]
    if previous.instrument.strip().lower() != current.instrument.strip().lower():
        return Incompatibility.INSTRUMENT_MISMATCH, [
            f"instrument differs: {previous.instrument!r} vs {current.instrument!r}; "
            "a spot price and a perp price are not one instrument"]

    # 3. Per-metric series identity, checked only for metrics actually present.
    #    A metric that is absent is already UNKNOWN downstream and needs no
    #    identity; a metric that is PRESENT may never be compared without one.
    for metric, prev_value, cur_value, prev_id, cur_id, mismatch in (
        ("CVD", previous.cvd, current.cvd,
         previous.cvd_series, current.cvd_series, Incompatibility.CVD_SERIES_MISMATCH),
        ("OI", previous.open_interest, current.open_interest,
         previous.oi_series, current.oi_series, Incompatibility.OI_SERIES_MISMATCH),
    ):
        if prev_value is None or cur_value is None:
            continue
        if not isinstance(prev_id, SeriesId) or not isinstance(cur_id, SeriesId):
            return Incompatibility.IDENTITY_MISSING, [
                f"{metric} is present on both snapshots but its series identity is "
                "missing; two unidentified series are not proven to be one series"]
        for label, ident in (("previous", prev_id), ("current", cur_id)):
            if not ident.is_complete():
                return Incompatibility.IDENTITY_MISSING, [
                    f"{label} {metric} series identity is incomplete: missing "
                    + ", ".join(ident.missing_fields())]
        if prev_id.key() != cur_id.key():
            return mismatch, [
                f"{metric} series differs: {prev_id.series_id!r} vs {cur_id.series_id!r}"]

    # 4. Time must advance.  Equal timestamps are not a zero-length step, they
    #    are the same observation compared with itself or two rows whose order
    #    is unknown; either way the direction is not evidence.
    prev_ts, cur_ts = _parse_ts(previous.timestamp), _parse_ts(current.timestamp)
    if prev_ts is None or cur_ts is None:
        return Incompatibility.IDENTITY_MISSING, [
            "snapshot timestamps are missing or unparseable; ordering cannot be proven"]
    if prev_ts >= cur_ts:
        return Incompatibility.TIMESTAMP_NOT_ADVANCING, [
            f"previous timestamp {previous.timestamp!r} is not before "
            f"{current.timestamp!r}; a direction needs a forward step"]

    return None, []


def classify_regime(
    previous: Snapshot,
    current: Snapshot,
    *,
    price_epsilon: float = 0.0005,
    cvd_epsilon: float = 0.0008,
    oi_epsilon: float = 0.0008,
) -> tuple[Direction, Direction, Direction, Regime, Stance, list[str]]:
    # Compatibility is proven BEFORE any subtraction.  Returning UNKNOWN for all
    # three directions (not just the regime) is deliberate: if the series are not
    # the same series, then "price fell" is as unfounded as "CVD fell", and a
    # caller reading only `price_direction` must not be handed a number that the
    # gate already refused to stand behind.
    incompatibility, why = series_compatibility(previous, current)
    if incompatibility is not None:
        reasons = [f"INCOMPATIBLE_SERIES: {incompatibility.value}", *why,
                   "Refusing to compare: values from different or unproven series "
                   "are not a direction"]
        return (Direction.UNKNOWN, Direction.UNKNOWN, Direction.UNKNOWN,
                Regime.UNKNOWN, Stance.NO_TRADE, reasons)

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
    if levels is not None and series_compatibility(previous, current)[0] is None:
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
    window = rows[-observations:]
    return _ordered_history(window) and all(row.price >= threshold for row in window)


def sweep_and_reclaim(history: Iterable[Snapshot], level: float, *, lookback: int = 4) -> bool:
    """True when recent price traded below level and latest observation is back above it."""
    rows = list(history)[-max(2, lookback):]
    if len(rows) < 2:
        return False
    return (_ordered_history(rows) and any(row.price < level for row in rows[:-1])
            and rows[-1].price > level)


def _ordered_history(rows: list[Snapshot]) -> bool:
    """A history claim cannot combine different or unordered observations."""
    return (all(_stated(row.source) and _stated(row.instrument)
                and _parse_ts(row.timestamp) is not None for row in rows)
            and all(series_compatibility(previous, current)[0] is None
                    for previous, current in zip(rows, rows[1:])))
