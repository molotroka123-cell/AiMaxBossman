"""Анти-lookahead: модель не имеет права видеть будущее в момент решения.

Это ядро честности всего модуля. Любой бэктест, где в T1 просочилась хоть одна
будущая свеча, покажет прибыль — и она будет ложью. Поэтому доступ к данным
идёт не «по договорённости», а через объект, который физически не отдаёт
будущее и падает с ошибкой на попытке его запросить.

Фазы: T0 — доступное до сигнала; T1 — решение; T2 — независимая проверка
исхода; T3 — разбор.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import (Candle, Claim, Episode, FORBIDDEN_AT_DECISION, MarketRegime,
                     Phase)
from . import market


class LookaheadViolation(RuntimeError):
    """Попытка использовать данные, недоступные на момент решения."""


class StaleObservation(RuntimeError):
    """Наблюдение слишком старое, чтобы участвовать в решении."""


class ContextMismatch(RuntimeError):
    """Смешение активов, площадок или таймфреймов в одном решении."""


# Наблюдение старше этого возраста не может обосновывать вход: рынок сменил
# режим много раз. Значение — не оптимизируемый параметр, а гигиена.
DEFAULT_MAX_OBSERVATION_AGE = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """То, что модель ВИДИТ в T1. Больше ничего ей не передаётся."""

    asset: str
    venue: str
    timeframe: str
    decision_time: datetime
    candles: tuple[Candle, ...]
    claims: tuple[Claim, ...]
    regime: MarketRegime
    regime_reason: str

    @property
    def last_price(self) -> float:
        return self.candles[-1].close if self.candles else 0.0


def visible_candles(candles: list[Candle], decision_time: datetime) -> list[Candle]:
    """Свечи, ЗАКРЫВШИЕСЯ не позже момента решения.

    Строгое `<=` по времени закрытия: свеча, закрывающаяся ровно в момент
    решения, уже известна; всё, что позже, — будущее.
    """
    if decision_time.tzinfo is None:
        raise LookaheadViolation("decision_time must be timezone-aware")
    return [c for c in candles if c.ts <= decision_time]


def future_candles(candles: list[Candle], decision_time: datetime) -> list[Candle]:
    """Явно отделённое будущее. Доступно только фазам T2/T3."""
    return [c for c in candles if c.ts > decision_time]


def assert_no_lookahead(used: list[Candle], decision_time: datetime) -> None:
    """Финальная проверка на границе: ни одна использованная свеча не из будущего."""
    leaked = [c for c in used if c.ts > decision_time]
    if leaked:
        raise LookaheadViolation(
            f"{len(leaked)} candle(s) after decision_time leaked into the decision "
            f"(first at {leaked[0].ts.isoformat()})")


def claims_for_phase(claims: list[Claim], phase: Phase, decision_time: datetime, *,
                     max_age: timedelta | None = DEFAULT_MAX_OBSERVATION_AGE) -> list[Claim]:
    """Отбор claim'ов по фазе.

    В T0/T1 отсекается три вещи сразу: наблюдения, сделанные ПОСЛЕ момента
    решения; типы знания, которые по своей природе являются ответом (ожидаемый
    исход, ретроспективный разбор); и протухшие наблюдения.
    """
    if phase in (Phase.T2, Phase.T3):
        return list(claims)
    allowed: list[Claim] = []
    for claim in claims:
        if claim.claim_type in FORBIDDEN_AT_DECISION:
            continue
        if claim.collected_at > decision_time:
            continue
        if max_age is not None and decision_time - claim.collected_at > max_age:
            continue
        if claim.verification_status.value == "QUARANTINED":
            continue
        allowed.append(claim)
    return allowed


def build_decision_context(episode: Episode, *, lookback: int = 10,
                           max_age: timedelta | None = DEFAULT_MAX_OBSERVATION_AGE,
                           strict_stale: bool = False) -> DecisionContext:
    """Собрать вход модели для T1. Единственный законный способ его получить."""
    past = visible_candles(episode.candles, episode.decision_time)
    if not past:
        raise LookaheadViolation("no data available before the decision time")
    assert_no_lookahead(past, episode.decision_time)

    for claim in episode.claims:
        if claim.asset != episode.asset:
            raise ContextMismatch(
                f"claim asset {claim.asset!r} != episode asset {episode.asset!r}")
        if claim.timeframe != episode.timeframe:
            raise ContextMismatch(
                f"claim timeframe {claim.timeframe!r} != episode timeframe {episode.timeframe!r}")
        if claim.venue != episode.venue:
            raise ContextMismatch(
                f"claim venue {claim.venue!r} != episode venue {episode.venue!r}")
        if strict_stale and max_age is not None and \
                episode.decision_time - claim.collected_at > max_age:
            raise StaleObservation(
                f"observation from {claim.collected_at.isoformat()} is older than {max_age}")

    usable = claims_for_phase(episode.claims, Phase.T1, episode.decision_time, max_age=max_age)
    reading = market.classify(past, lookback=lookback)
    return DecisionContext(
        asset=episode.asset, venue=episode.venue, timeframe=episode.timeframe,
        decision_time=episode.decision_time, candles=tuple(past), claims=tuple(usable),
        regime=reading.regime, regime_reason=reading.reason)


def outcome_window(episode: Episode, *, bars: int) -> list[Candle]:
    """Окно T2 — независимая проверка исхода. Недоступно в T1 по построению."""
    return future_candles(episode.candles, episode.decision_time)[:bars]
