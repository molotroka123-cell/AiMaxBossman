"""Математика качества. Здесь решается, имеем ли мы право говорить «работает».

Главная мысль: положительный P&L на 12 сделках одного дня — это не стратегия,
а совпадение. Поэтому «прибыльность» — не число, а вердикт, который требует
выборки, результата вне выборки, положительного EV ПОСЛЕ расходов и
устойчивости в нескольких режимах. Не хватает любого — ответ
INSUFFICIENT_EVIDENCE, и это нормальный, а не стыдный результат.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Decision, TradeResult

# Гейты. Не оптимизируются под результат: их смысл в том, чтобы мешать.
MIN_SAMPLE = 30              # меньше — статистики нет
MIN_OUT_OF_SAMPLE = 10
MIN_REGIMES = 2
Z_95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class CostModel:
    """Расходы, которые съедают «прибыльность» бумажных стратегий."""

    fee_rate: float = 0.0004          # taker, доля от оборота, односторонняя
    funding_rate: float = 0.0001      # за период удержания
    slippage_rate: float = 0.0005
    execution_error_rate: float = 0.0002   # промахи исполнения, задержка

    def total_rate(self) -> float:
        # Комиссия платится дважды — на входе и на выходе.
        return (self.fee_rate * 2 + self.funding_rate + self.slippage_rate
                + self.execution_error_rate)


@dataclass
class QualityReport:
    sample_size: int = 0
    out_of_sample_size: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0
    execution_error: float = 0.0
    expected_value: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    calibration_error: float = 0.0
    false_positive_rate: float = 0.0
    missed_opportunity_rate: float = 0.0
    stop_discipline: float = 1.0
    out_of_sample_ev: float = 0.0
    regimes_covered: tuple[str, ...] = ()
    regime_ev: dict[str, float] = field(default_factory=dict)
    cost_sensitivity: dict[str, float] = field(default_factory=dict)
    win_rate_ci95: tuple[float, float] = (0.0, 0.0)
    verdict: str = "INSUFFICIENT_EVIDENCE"
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["regimes_covered"] = list(self.regimes_covered)
        d["win_rate_ci95"] = list(self.win_rate_ci95)
        d["blockers"] = list(self.blockers)
        return d


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Доверительный интервал Уилсона: корректен на малых выборках.

    Нормальное приближение на 8 сделках даёт интервал вида [-0.1, 1.1] и
    создаёт иллюзию знания. Уилсон — нет.
    """
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def max_drawdown(trades: list[TradeResult]) -> float:
    """Максимальная просадка по кривой чистого P&L, в валюте счёта."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def calibration_error(predictions: list[tuple[float, bool]], bins: int = 5) -> float:
    """ECE: насколько заявленная уверенность совпадает с частотой попаданий.

    Модель, говорящая «уверен на 0.9» и попадающая в 50% случаев, опаснее
    модели, которая честно говорит 0.5.
    """
    if not predictions:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, hit in predictions:
        idx = min(bins - 1, max(0, int(conf * bins)))
        buckets[idx].append((conf, hit))
    total = len(predictions)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, h in bucket if h) / len(bucket)
        error += (len(bucket) / total) * abs(avg_conf - accuracy)
    return error


def evaluate(trades: list[TradeResult], *, costs: CostModel | None = None,
             predictions: list[tuple[float, bool]] | None = None,
             signals_taken: int = 0, signals_valid: int = 0,
             opportunities_total: int = 0, opportunities_taken: int = 0,
             stops_honoured: int = 0, stops_required: int = 0) -> QualityReport:
    """Полный отчёт качества плюс вердикт по гейтам."""
    report = QualityReport()
    if not trades:
        report.blockers = ("no trades",)
        return report

    cost = costs or CostModel()
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    n = len(trades)
    report.sample_size = n
    report.win_rate = len(wins) / n
    report.loss_rate = len(losses) / n
    report.average_win = (sum(t.net_pnl for t in wins) / len(wins)) if wins else 0.0
    report.average_loss = (abs(sum(t.net_pnl for t in losses)) / len(losses)) if losses else 0.0
    report.fees = sum(t.fees for t in trades) / n
    report.funding = sum(t.funding for t in trades) / n
    report.slippage = sum(t.slippage for t in trades) / n
    # Ошибка исполнения оценивается как доля оборота: в симуляции её не видно,
    # поэтому она добавляется явно, а не «предполагается нулевой».
    turnover = sum(abs(t.entry) * t.size for t in trades) / n
    report.execution_error = turnover * cost.execution_error_rate

    report.expected_value = (report.win_rate * report.average_win
                             - report.loss_rate * report.average_loss
                             - report.fees - report.funding - report.slippage
                             - report.execution_error)
    report.expectancy_r = sum(t.r_multiple for t in trades) / n
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    report.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0)
    report.max_drawdown = max_drawdown(trades)
    report.calibration_error = calibration_error(predictions or [])
    report.false_positive_rate = (
        1.0 - signals_valid / signals_taken) if signals_taken else 0.0
    report.missed_opportunity_rate = (
        1.0 - opportunities_taken / opportunities_total) if opportunities_total else 0.0
    report.stop_discipline = (stops_honoured / stops_required) if stops_required else 1.0
    report.win_rate_ci95 = wilson_interval(len(wins), n)

    oos = [t for t in trades if t.out_of_sample]
    report.out_of_sample_size = len(oos)
    if oos:
        oos_wins = [t for t in oos if t.net_pnl > 0]
        oos_losses = [t for t in oos if t.net_pnl <= 0]
        avg_w = (sum(t.net_pnl for t in oos_wins) / len(oos_wins)) if oos_wins else 0.0
        avg_l = (abs(sum(t.net_pnl for t in oos_losses)) / len(oos_losses)) if oos_losses else 0.0
        wr = len(oos_wins) / len(oos)
        report.out_of_sample_ev = (wr * avg_w - (1 - wr) * avg_l
                                   - sum(t.fees + t.funding + t.slippage for t in oos) / len(oos))

    by_regime: dict[str, list[TradeResult]] = {}
    for t in trades:
        by_regime.setdefault(t.regime.value, []).append(t)
    report.regimes_covered = tuple(sorted(by_regime))
    report.regime_ev = {name: sum(x.net_pnl for x in group) / len(group)
                        for name, group in by_regime.items()}

    # Чувствительность: во сколько раз надо увеличить расходы, чтобы EV умер.
    base_costs = report.fees + report.funding + report.slippage + report.execution_error
    gross_ev = report.expected_value + base_costs
    report.cost_sensitivity = {
        "gross_ev": gross_ev,
        "cost_per_trade": base_costs,
        "breakeven_cost_multiple": (gross_ev / base_costs) if base_costs > 0 else float("inf"),
        "ev_at_double_costs": gross_ev - base_costs * 2,
        "ev_at_triple_costs": gross_ev - base_costs * 3,
    }

    blockers: list[str] = []
    if n < MIN_SAMPLE:
        blockers.append(f"sample {n} < {MIN_SAMPLE}")
    if report.out_of_sample_size < MIN_OUT_OF_SAMPLE:
        blockers.append(f"out-of-sample {report.out_of_sample_size} < {MIN_OUT_OF_SAMPLE}")
    if report.expected_value <= 0:
        blockers.append("expected value is not positive after costs")
    if report.out_of_sample_ev <= 0:
        blockers.append("out-of-sample expected value is not positive")
    if len(report.regimes_covered) < MIN_REGIMES:
        blockers.append(f"regimes {len(report.regimes_covered)} < {MIN_REGIMES}")
    if report.win_rate_ci95[0] <= 0.0:
        blockers.append("win-rate confidence interval includes zero")
    report.blockers = tuple(blockers)
    report.verdict = "PROFITABLE_CANDIDATE" if not blockers else "INSUFFICIENT_EVIDENCE"
    return report


def decide(report: QualityReport, direction: Decision = Decision.LONG) -> Decision:
    """Перевод отчёта в действие. Без доказательств — не торгуем.

    Направление приходит из правила, а не выводится из отчёта: отчёт может
    только РАЗРЕШИТЬ торговать, но не может решить, в какую сторону.
    """
    if report.verdict != "PROFITABLE_CANDIDATE":
        return Decision.INSUFFICIENT_EVIDENCE
    if report.expected_value <= 0:
        return Decision.NO_TRADE
    return direction if direction in (Decision.LONG, Decision.SHORT) else Decision.NO_TRADE
