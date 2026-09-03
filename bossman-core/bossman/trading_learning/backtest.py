"""Компиляция правила в исполняемый план и его прогон по истории.

Разделение compile/run намеренное: скомпилированный план фиксирует ВСЕ
параметры, которые влияют на результат (расходы, задержку, окно, разбиение на
in/out-of-sample), и его хеш попадает в отчёт. Иначе «мы прогнали ещё раз и
стало лучше» невозможно отличить от подгонки.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .metrics import CostModel, QualityReport, evaluate
from .models import Candle, Decision, Episode
from .paper import ImpossibleFill, PaperBroker, PaperOrder
from .replay import build_decision_context, LookaheadViolation
from .safety import EvidenceClass, assert_no_live_execution
from .strategy import StrategyRule


class BacktestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BacktestPlan:
    """Замороженные условия прогона. Меняешь условия — меняется plan_hash."""

    rule: StrategyRule
    costs: CostModel
    latency_bars: int
    lookback: int
    out_of_sample_fraction: float
    plan_hash: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule.as_dict(),
                "costs": {"fee_rate": self.costs.fee_rate,
                          "funding_rate": self.costs.funding_rate,
                          "slippage_rate": self.costs.slippage_rate,
                          "execution_error_rate": self.costs.execution_error_rate},
                "latency_bars": self.latency_bars, "lookback": self.lookback,
                "out_of_sample_fraction": self.out_of_sample_fraction,
                "plan_hash": self.plan_hash}


def compile_backtest(rule: StrategyRule, *, costs: CostModel | None = None,
                     latency_bars: int = 1, lookback: int = 10,
                     out_of_sample_fraction: float = 0.3) -> BacktestPlan:
    """Заморозить условия прогона и посчитать их хеш."""
    if not 0.0 < out_of_sample_fraction < 1.0:
        raise BacktestError("out_of_sample_fraction must be within (0,1)")
    if latency_bars < 1:
        raise BacktestError("latency_bars must be at least 1 (no instant execution)")
    plan = BacktestPlan(rule=rule, costs=costs or CostModel(), latency_bars=latency_bars,
                        lookback=lookback, out_of_sample_fraction=out_of_sample_fraction)
    blob = json.dumps(plan.as_dict(), sort_keys=True, ensure_ascii=False).encode()
    return BacktestPlan(rule=plan.rule, costs=plan.costs, latency_bars=plan.latency_bars,
                        lookback=plan.lookback,
                        out_of_sample_fraction=plan.out_of_sample_fraction,
                        plan_hash=hashlib.sha256(blob).hexdigest()[:32])


@dataclass
class BacktestRun:
    plan_hash: str
    report: QualityReport
    episodes_total: int = 0
    episodes_traded: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    evidence_class: EvidenceClass = EvidenceClass.HISTORICAL_REPLAY
    decision: Decision = Decision.INSUFFICIENT_EVIDENCE

    def as_dict(self) -> dict:
        return {"plan_hash": self.plan_hash, "report": self.report.as_dict(),
                "episodes_total": self.episodes_total,
                "episodes_traded": self.episodes_traded, "skipped": dict(self.skipped),
                "evidence_class": self.evidence_class.value,
                "decision": self.decision.value}


def _exit_index(rule: StrategyRule, entry_price: float, future: list[Candle]) -> tuple[int, float]:
    """Первый из стопа/тейка/лимита времени. Стоп проверяется РАНЬШЕ тейка.

    Если внутри одной свечи задеты и стоп, и тейк, мы не знаем порядок и
    обязаны выбрать худший исход. Обратный выбор — это скрытая подгонка,
    завышающая win rate.
    """
    long = rule.direction is Decision.LONG
    stop = entry_price * (1 - rule.stop_pct) if long else entry_price * (1 + rule.stop_pct)
    take = entry_price * (1 + rule.take_pct) if long else entry_price * (1 - rule.take_pct)
    for i, bar in enumerate(future[:rule.max_hold_bars]):
        hit_stop = bar.low <= stop if long else bar.high >= stop
        if hit_stop:
            return i, stop
        hit_take = bar.high >= take if long else bar.low <= take
        if hit_take:
            return i, take
    idx = min(len(future), rule.max_hold_bars) - 1
    if idx < 0:
        raise BacktestError("no future bars to evaluate the exit")
    return idx, future[idx].close


def run_backtest(plan: BacktestPlan, episodes: list[Episode]) -> BacktestRun:
    """Прогон плана по эпизодам. Каждое решение строится только из прошлого."""
    assert_no_live_execution("run_backtest", stage="historical_analysis")
    rule = plan.rule
    broker = PaperBroker(costs=plan.costs, latency_bars=plan.latency_bars)
    trades = []
    skipped: dict[str, int] = {}
    predictions: list[tuple[float, bool]] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    ordered = sorted(episodes, key=lambda e: e.decision_time)
    split = int(len(ordered) * (1 - plan.out_of_sample_fraction))
    for index, episode in enumerate(ordered):
        if episode.asset != rule.asset or episode.timeframe != rule.timeframe:
            skip("context_mismatch")
            continue
        try:
            ctx = build_decision_context(episode, lookback=plan.lookback)
        except LookaheadViolation:
            skip("no_history")
            continue
        if not rule.regime_ok(ctx.regime):
            skip(f"regime:{ctx.regime.value}")
            continue
        if not rule.zone_hit(ctx.last_price):
            skip("outside_zone")
            continue
        future = [c for c in episode.candles if c.ts > episode.decision_time]
        if len(future) <= plan.latency_bars:
            skip("no_future_data")
            continue

        order = PaperOrder(client_order_id=f"{episode.episode_id}:{plan.plan_hash[:8]}",
                           asset=episode.asset, direction=rule.direction, size=1.0,
                           signal_price=ctx.last_price, signal_ts=episode.decision_time)
        try:
            entry = broker.submit(order, future)
        except ImpossibleFill:
            skip("impossible_fill")
            continue
        tail = [c for c in future if c.ts > entry.ts]
        if not tail:
            skip("no_exit_data")
            continue
        exit_i, exit_price = _exit_index(rule, entry.price, tail)
        result = broker.close(order, entry, tail[exit_i], exit_price, stop=(
            entry.price * (1 - rule.stop_pct) if rule.direction is Decision.LONG
            else entry.price * (1 + rule.stop_pct)),
            regime=ctx.regime, hold_bars=exit_i + 1, out_of_sample=index >= split)
        trades.append(result)
        # Калибровка: уверенностью считаем среднюю уверенность claim'ов входа.
        conf = (sum(c.confidence for c in ctx.claims) / len(ctx.claims)) if ctx.claims else 0.5
        predictions.append((conf, result.net_pnl > 0))

    report = evaluate(trades, costs=plan.costs, predictions=predictions,
                      signals_taken=len(trades),
                      signals_valid=sum(1 for t in trades if t.net_pnl > 0),
                      opportunities_total=len(ordered), opportunities_taken=len(trades))
    run = BacktestRun(plan_hash=plan.plan_hash, report=report,
                      episodes_total=len(ordered), episodes_traded=len(trades),
                      skipped=skipped)
    from .metrics import decide  # локальный импорт: цикл не нужен на уровне модуля
    run.decision = decide(report, rule.direction)
    return run
