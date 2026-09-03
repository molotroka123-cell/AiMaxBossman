"""Симулятор paper trading. Ордера существуют только в памяти процесса.

Что здесь важнее удобства:
  * идеальное исполнение запрещено — заявка не может исполниться лучше цены,
    доступной на рынке, и не может исполниться вне диапазона свечи;
  * задержка обязательна — решение принимается по одной свече, исполняется по
    следующей, потому что в реальности между ними проходит время;
  * дублирующийся ордер отклоняется по client_order_id — иначе повторный вызов
    ручки удвоит позицию и испортит статистику;
  * комиссия, funding и проскальзывание считаются всегда, включая ноль-режимы.

Никакого сетевого клиента здесь нет и не будет: см. safety.assert_no_live_execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .metrics import CostModel
from .models import Candle, Decision, MarketRegime, TradeResult
from .safety import EvidenceClass, assert_no_live_execution


# Насколько исполнение может оказаться ЛУЧШЕ сигнальной цены, прежде чем это
# перестаёт быть рынком и становится признаком подглядывания в будущее.
MAX_FAVOURABLE_GAP = 0.5


class ImpossibleFill(RuntimeError):
    """Исполнение по цене, которой на рынке не было."""


class DuplicateOrder(RuntimeError):
    """Ордер с тем же client_order_id уже принят."""


class PaperOnlyViolation(RuntimeError):
    """Попытка выйти за пределы бумажной торговли."""


@dataclass(frozen=True, slots=True)
class PaperOrder:
    client_order_id: str
    asset: str
    direction: Decision
    size: float
    signal_price: float
    signal_ts: datetime

    def __post_init__(self) -> None:
        if self.direction not in (Decision.LONG, Decision.SHORT):
            raise PaperOnlyViolation("paper order direction must be LONG or SHORT")
        if self.size <= 0 or self.signal_price <= 0:
            raise PaperOnlyViolation("size and signal price must be positive")


@dataclass(frozen=True, slots=True)
class Fill:
    client_order_id: str
    price: float
    ts: datetime
    fee: float
    slippage_cost: float
    evidence_class: EvidenceClass = EvidenceClass.SIMULATED


@dataclass
class PaperBroker:
    """Бумажный брокер: единственный «исполнитель» в модуле."""

    costs: CostModel = field(default_factory=CostModel)
    latency_bars: int = 1                 # решение по бару N исполняется на N+1
    _seen: set[str] = field(default_factory=set)
    fills: list[Fill] = field(default_factory=list)

    def submit(self, order: PaperOrder, future: list[Candle]) -> Fill:
        """Принять заявку и исполнить её по СЛЕДУЮЩЕЙ доступной свече."""
        assert_no_live_execution("paper_submit", stage="paper_trading")
        if order.client_order_id in self._seen:
            raise DuplicateOrder(f"order {order.client_order_id!r} already submitted")
        if self.latency_bars < 1:
            raise PaperOnlyViolation("zero-latency execution is not allowed")
        if len(future) < self.latency_bars:
            raise ImpossibleFill("no future bar available to execute against")
        bar = future[self.latency_bars - 1]
        if bar.ts <= order.signal_ts:
            raise ImpossibleFill("execution bar is not after the signal bar")

        # Проскальзывание всегда против нас: покупаем дороже, продаём дешевле.
        drift = bar.open * self.costs.slippage_rate
        raw = bar.open + drift if order.direction is Decision.LONG else bar.open - drift
        price = min(max(raw, bar.low), bar.high)
        if not (bar.low <= price <= bar.high):        # pragma: no cover — защита от правки выше
            raise ImpossibleFill(f"fill {price} outside bar range [{bar.low},{bar.high}]")
        # Исполнение лучше сигнала на входе — признак подглядывания в будущее.
        if order.direction is Decision.LONG and \
                price < order.signal_price * (1 - MAX_FAVOURABLE_GAP):
            raise ImpossibleFill("fill is implausibly better than the signal price")

        fee = price * order.size * self.costs.fee_rate
        slip = abs(price - bar.open) * order.size
        self._seen.add(order.client_order_id)
        fill = Fill(order.client_order_id, price, bar.ts, fee, slip)
        self.fills.append(fill)
        return fill

    def close(self, order: PaperOrder, entry: Fill, exit_bar: Candle, exit_price: float,
              *, stop: float, regime: MarketRegime, hold_bars: int,
              out_of_sample: bool = False) -> TradeResult:
        """Закрыть позицию и посчитать чистый результат со всеми расходами."""
        assert_no_live_execution("paper_close", stage="paper_trading")
        if not (exit_bar.low <= exit_price <= exit_bar.high):
            raise ImpossibleFill(
                f"exit {exit_price} outside bar range [{exit_bar.low},{exit_bar.high}]")
        exit_fee = exit_price * order.size * self.costs.fee_rate
        funding = (entry.price * order.size * self.costs.funding_rate * max(hold_bars, 1))
        return TradeResult(
            episode_id=order.client_order_id, direction=order.direction,
            entry=entry.price, exit=exit_price, stop=stop, size=order.size,
            fees=entry.fee + exit_fee, funding=funding, slippage=entry.slippage_cost,
            regime=regime, out_of_sample=out_of_sample)


def paper_trade(orders: list[tuple[PaperOrder, list[Candle], float, MarketRegime]],
                *, costs: CostModel | None = None) -> list[TradeResult]:
    """Пакетная бумажная торговля. Возвращает только чистые результаты."""
    broker = PaperBroker(costs=costs or CostModel())
    results: list[TradeResult] = []
    for order, future, stop, regime in orders:
        entry = broker.submit(order, future)
        tail = [c for c in future if c.ts > entry.ts]
        if not tail:
            raise ImpossibleFill("no bar available to close the position")
        exit_bar = tail[-1]
        results.append(broker.close(order, entry, exit_bar, exit_bar.close,
                                    stop=stop, regime=regime, hold_bars=len(tail)))
    return results
