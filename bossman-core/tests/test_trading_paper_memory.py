"""Бумажная торговля, математика качества и гейты памяти."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bossman.trading_learning.backtest import BacktestError, compile_backtest, run_backtest
from bossman.trading_learning.lessons import lesson_builder
from bossman.trading_learning.memory import (MIN_INDEPENDENT_EPISODES, LessonRecord,
                                             PromotionDenied, TradingMemory)
from bossman.trading_learning.metrics import (CostModel, MIN_SAMPLE, evaluate,
                                              wilson_interval)
from bossman.trading_learning.models import (Candle, Claim, ClaimType, Decision, Episode,
                                             MarketRegime, MemoryLayer, TradeResult)
from bossman.trading_learning.paper import (DuplicateOrder, ImpossibleFill, PaperBroker,
                                            PaperOnlyViolation, PaperOrder, paper_trade)
from bossman.trading_learning.safety import LiveExecutionForbidden, utcnow
from bossman.trading_learning.strategy import LevelZone, StrategyRule

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def series(n, start=70_000.0, drift=50.0, base_ts=T0, cvd_drift=200.0, oi_drift=100.0):
    out, price, cvd, oi = [], start, 1_000_000.0, 5_000_000.0
    for i in range(n):
        nxt = price + drift
        cvd += cvd_drift
        oi += oi_drift
        out.append(Candle(ts=base_ts + timedelta(hours=i), open=price,
                          high=max(price, nxt) + 20, low=min(price, nxt) - 20, close=nxt,
                          volume=1.0, cvd=cvd, open_interest=oi))
        price = nxt
    return out


def rule(rule_id="r1", direction=Decision.LONG):
    return StrategyRule(rule_id=rule_id, asset="BTCUSDT", venue="binance-futures",
                        timeframe="1h", direction=direction,
                        zones=[LevelZone("demand", 10_000.0, 200_000.0)],
                        stop_pct=0.02, take_pct=0.03, max_hold_bars=12,
                        derived_from=("src@0.0",))


def trade(pnl_r=1.0, oos=False, regime=MarketRegime.RANGE, eid="e"):
    entry, stop = 100.0, 98.0
    return TradeResult(episode_id=eid, direction=Decision.LONG, entry=entry,
                       exit=entry + 2.0 * pnl_r, stop=stop, size=1.0,
                       fees=0.01, funding=0.0, slippage=0.0, regime=regime,
                       out_of_sample=oos)


# ------------------------------------------------------------ paper trading
def test_paper_order_never_executes_at_zero_latency():
    candles = series(10)
    order = PaperOrder("o1", "BTCUSDT", Decision.LONG, 1.0, candles[0].close, candles[0].ts)
    broker = PaperBroker(latency_bars=0)
    with pytest.raises(PaperOnlyViolation):
        broker.submit(order, candles[1:])


def test_paper_fill_is_strictly_after_the_signal_bar():
    candles = series(10)
    order = PaperOrder("o2", "BTCUSDT", Decision.LONG, 1.0, candles[3].close, candles[3].ts)
    fill = PaperBroker().submit(order, candles[4:])
    assert fill.ts > order.signal_ts
    assert fill.price >= candles[4].open      # проскальзывание против нас


def test_duplicate_paper_order_is_rejected():
    candles = series(10)
    order = PaperOrder("dup", "BTCUSDT", Decision.LONG, 1.0, candles[0].close, candles[0].ts)
    broker = PaperBroker()
    broker.submit(order, candles[1:])
    with pytest.raises(DuplicateOrder):
        broker.submit(order, candles[1:])


def test_impossible_exit_price_is_rejected():
    candles = series(10)
    order = PaperOrder("o3", "BTCUSDT", Decision.LONG, 1.0, candles[0].close, candles[0].ts)
    broker = PaperBroker()
    fill = broker.submit(order, candles[1:])
    with pytest.raises(ImpossibleFill):
        broker.close(order, fill, candles[3], candles[3].high * 2, stop=0.0,
                     regime=MarketRegime.RANGE, hold_bars=1)


def test_fees_and_funding_are_always_charged():
    candles = series(10)
    order = PaperOrder("o4", "BTCUSDT", Decision.LONG, 1.0, candles[0].close, candles[0].ts)
    broker = PaperBroker()
    fill = broker.submit(order, candles[1:])
    result = broker.close(order, fill, candles[5], candles[5].close, stop=0.0,
                          regime=MarketRegime.RANGE, hold_bars=4)
    assert result.fees > 0 and result.funding > 0
    assert result.net_pnl < result.gross_pnl      # расходы не пропали


def test_no_future_bar_means_no_fill():
    candles = series(3)
    order = PaperOrder("o5", "BTCUSDT", Decision.LONG, 1.0, candles[2].close, candles[2].ts)
    with pytest.raises(ImpossibleFill):
        PaperBroker().submit(order, [])


def test_paper_broker_refuses_live_actions():
    with pytest.raises(LiveExecutionForbidden):
        from bossman.trading_learning.safety import assert_no_live_execution
        assert_no_live_execution("place_order", stage="paper_trading")


# ------------------------------------------------------------------ метрики
def test_small_sample_never_claims_profitability():
    report = evaluate([trade(1.0) for _ in range(5)])
    assert report.verdict == "INSUFFICIENT_EVIDENCE"
    assert any("sample" in b for b in report.blockers)


def test_positive_gross_but_negative_after_costs_is_not_profitable():
    trades = [TradeResult("e", Decision.LONG, 100.0, 100.5, 98.0, 10.0,
                          fees=3.0, funding=2.0, slippage=2.0, regime=MarketRegime.RANGE,
                          out_of_sample=(i % 3 == 0)) for i in range(40)]
    report = evaluate(trades)
    assert report.expected_value <= 0
    assert report.verdict == "INSUFFICIENT_EVIDENCE"
    assert "expected value is not positive after costs" in report.blockers


def test_single_regime_blocks_the_profitability_claim():
    trades = [trade(1.0, oos=(i % 3 == 0), eid=f"e{i}") for i in range(40)]
    report = evaluate(trades)
    assert any("regimes" in b for b in report.blockers)


def test_wilson_interval_is_honest_on_tiny_samples():
    lo, hi = wilson_interval(3, 3)
    assert lo < 1.0 and hi <= 1.0 and lo > 0.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_cost_sensitivity_is_reported():
    trades = [trade(1.0, oos=(i % 2 == 0), regime=(MarketRegime.RANGE if i % 2 else
                                                   MarketRegime.CONTINUATION), eid=f"e{i}")
              for i in range(40)]
    report = evaluate(trades)
    assert "breakeven_cost_multiple" in report.cost_sensitivity
    assert "ev_at_triple_costs" in report.cost_sensitivity


def test_calibration_error_punishes_overconfidence():
    from bossman.trading_learning.metrics import calibration_error
    overconfident = [(0.95, False)] * 10 + [(0.95, True)] * 10
    honest = [(0.5, False)] * 10 + [(0.5, True)] * 10
    assert calibration_error(overconfident) > calibration_error(honest)


# ----------------------------------------------------------------- бэктест
def test_compile_backtest_refuses_zero_latency():
    with pytest.raises(BacktestError):
        compile_backtest(rule(), latency_bars=0)


def test_plan_hash_changes_with_costs():
    a = compile_backtest(rule(), costs=CostModel(fee_rate=0.0004))
    b = compile_backtest(rule(), costs=CostModel(fee_rate=0.0010))
    assert a.plan_hash != b.plan_hash


def test_backtest_refuses_to_declare_profit_on_a_few_episodes():
    plan = compile_backtest(rule())
    episodes = [Episode(episode_id=f"ep{i}", asset="BTCUSDT", venue="binance-futures",
                        timeframe="1h",
                        decision_time=series(40, base_ts=T0 + timedelta(days=i))[20].ts,
                        candles=series(40, base_ts=T0 + timedelta(days=i)))
                for i in range(5)]
    run = run_backtest(plan, episodes)
    assert run.decision is Decision.INSUFFICIENT_EVIDENCE
    assert run.report.verdict == "INSUFFICIENT_EVIDENCE"
    assert run.evidence_class.value == "HISTORICAL_REPLAY"


def test_backtest_skips_episodes_from_another_asset():
    plan = compile_backtest(rule())
    foreign = Episode(episode_id="ethx", asset="ETHUSDT", venue="binance-futures",
                      timeframe="1h", decision_time=series(30)[10].ts, candles=series(30))
    run = run_backtest(plan, [foreign])
    assert run.episodes_traded == 0
    assert run.skipped.get("context_mismatch") == 1


def test_stop_is_checked_before_take_inside_one_bar():
    """Внутри одной свечи неизвестен порядок — берём худший исход."""
    from bossman.trading_learning.backtest import _exit_index
    bar = Candle(ts=T0, open=100.0, high=110.0, low=90.0, close=100.0, volume=1.0)
    idx, price = _exit_index(rule(), 100.0, [bar])
    assert idx == 0 and price == pytest.approx(98.0)


# ------------------------------------------------------------------ память
def _lesson(memory, *, episodes, report, provenance=("src@0.0",), opinion=False):
    r = rule()
    r.author_opinion_only = opinion
    record = LessonRecord(lesson_id="l1", rule=r, layer=MemoryLayer.QUARANTINE,
                          episode_ids=tuple(episodes), provenance=tuple(provenance),
                          report=report)
    memory.quarantine_lesson(record)
    return record


def _good_report():
    trades = [trade(1.0, oos=(i >= 25), regime=(MarketRegime.RANGE if i % 2 else
                                                MarketRegime.CONTINUATION), eid=f"e{i}")
              for i in range(40)]
    return evaluate(trades)


def test_new_knowledge_always_lands_in_quarantine():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report())
    assert record.layer is MemoryLayer.QUARANTINE
    assert memory.procedural == []


def test_author_opinion_can_never_be_promoted():
    """Главный инвариант памяти: мнение не становится процедурой."""
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report(), opinion=True)
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=True)
    assert "author opinion" in str(exc.value)
    assert memory.procedural == []


def test_promotion_requires_several_independent_episodes():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["only-one"], report=_good_report())
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=True)
    assert f"< {MIN_INDEPENDENT_EPISODES}" in str(exc.value)


def test_promotion_requires_clean_lookahead():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report())
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=False)
    assert "lookahead" in str(exc.value)


def test_promotion_requires_independent_verification():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report())
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="same", extraction_model="same",
                       lookahead_clean=True)
    assert "not independent" in str(exc.value)


def test_promotion_requires_out_of_sample_evidence():
    memory = TradingMemory()
    weak = evaluate([trade(1.0, eid=f"e{i}") for i in range(40)])   # нет out-of-sample
    record = _lesson(memory, episodes=["a", "b", "c"], report=weak)
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=True)
    assert "out-of-sample" in str(exc.value)


def test_promotion_requires_provenance():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report(), provenance=())
    with pytest.raises(PromotionDenied) as exc:
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=True)
    assert "provenance" in str(exc.value)


def test_full_evidence_allows_promotion_and_only_then():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["a", "b", "c"], report=_good_report())
    promoted = memory.promote(record, claims=[], verifier_id="independent-replay/v1",
                              extraction_model="rules/v1", lookahead_clean=True)
    assert promoted.layer is MemoryLayer.PROCEDURAL_MEMORY
    assert memory.procedural == [promoted]
    assert record not in memory.quarantine
    assert promoted.evidence_class.value == "HISTORICAL_REPLAY"


def test_denied_promotion_leaves_a_reason_in_quarantine():
    memory = TradingMemory()
    record = _lesson(memory, episodes=["one"], report=_good_report())
    with pytest.raises(PromotionDenied):
        memory.promote(record, claims=[], verifier_id="v", extraction_model="e",
                       lookahead_clean=True)
    assert any("promotion denied" in l.notes for l in memory.quarantine)


def test_episodic_memory_is_idempotent():
    memory = TradingMemory()
    ep = Episode(episode_id="e1", asset="BTCUSDT", venue="v", timeframe="1h",
                 decision_time=T0)
    memory.record_episode(ep)
    memory.record_episode(ep)
    assert len(memory.episodic) == 1


def test_memory_layers_are_separate_in_the_snapshot():
    memory = TradingMemory()
    memory.set_working("task", "audit")
    _lesson(memory, episodes=["a"], report=_good_report())
    snap = memory.snapshot()
    assert snap["working_state_keys"] == ["task"]
    assert snap["quarantine"] == 1 and snap["procedural"] == 0


def test_lesson_builder_puts_the_lesson_in_quarantine_with_gaps():
    memory = TradingMemory()
    report = evaluate([trade(1.0, eid="e") for _ in range(4)])
    record = lesson_builder(rule(), report, [], [], memory)
    assert record.layer is MemoryLayer.QUARANTINE
    assert "выборка" in record.notes or "недостаточная выборка" in record.notes
    assert memory.procedural == []
