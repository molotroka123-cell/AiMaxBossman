"""Paper-арена: исполнение по котировкам, комиссии, упавшие tx, смерть агента, перезапуск, только чтение."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bossman.trading_learning import sol_arena as sa
from bossman.trading_learning.sol_arena import (ATA_RENT_LAMPORTS, BASE_FEE_LAMPORTS, LAMPORTS, SOL_MINT, USDC_MINT,
                                                Arena, Config, promotion_candidates)

TOKEN = "Tok1111111111111111111111111111111111111111"
AGENTS = ["cash", "hold_sol", "momentum", "dip_buyer", "new_pair"]


def hot(**over):
    t = {"mint": TOKEN, "symbol": "HOT", "price_usd": 0.001, "liq_usd": 200_000, "fdv": 1e6,
         "chg_m5": 3.0, "chg_h1": 30.0, "chg_h6": 50.0, "vol_m5": 1e4, "vol_h1": 1e5,
         "buys_m5": 30, "sells_m5": 10, "buys_h1": 300, "sells_h1": 200, "age_min": 500.0}
    t.update(over)
    return t


class FakeMarket:
    """Токен стоит token_price USDC за атомарную единицу; следующие котировки можно подменить."""

    def __init__(self, *, sol=100.0, prio=0, token_price=1.0, universe=None):
        self.sol, self.prio, self.token_price = sol, prio, token_price
        self._universe = universe if universe is not None else [hot()]
        self.next_out: list[int | None] = []
        self.calls = 0

    def quote(self, i, o, amount, slip):
        self.calls += 1
        if self.next_out:
            v = self.next_out.pop(0)
            return None if v is None else {"outAmount": str(v), "routePlan": []}
        lam = LAMPORTS / 1e6                              # атомарных SOL на атомарный USDC при цене 1
        if i == USDC_MINT:
            out = amount / self.sol * lam if o == SOL_MINT else amount / self.token_price
        else:
            out = amount * self.sol / lam if i == SOL_MINT else amount * self.token_price
        return {"outAmount": str(int(out)), "priceImpactPct": "0", "routePlan": []}

    def sol_usd(self): return self.sol
    def priority_fee_micro_lamports(self): return self.prio
    def universe(self): return list(self._universe)


def arena(tmp_path, market, agents=AGENTS, **cfg):
    return Arena(market, Config(agents=agents, **cfg), tmp_path, sleep=lambda s: None, clock=lambda: 1_000_000.0)


def test_fees_and_rent_are_charged_and_cash_does_nothing(tmp_path):
    m = FakeMarket(prio=1_000_000)                      # 1 лампорт за CU
    ar = arena(tmp_path, m)
    ar.tick()
    fee = (BASE_FEE_LAMPORTS + 300_000) / LAMPORTS * 100
    assert ar.tx_fee_usd(100, 1_000_000) == pytest.approx(fee)
    assert ar.agents["cash"].equity_usd == 50.0 and ar.agents["cash"].fees_usd == 0
    mo = ar.agents["momentum"]
    assert len(mo.positions) == 1 and mo.fees_usd == pytest.approx(fee)
    assert mo.locked_rent_lamports == ATA_RENT_LAMPORTS
    assert mo.equity_usd == pytest.approx(50 - fee, abs=1e-3)       # рента в капитале, ушла только комиссия
    hs = ar.agents["hold_sol"]
    assert hs.positions[0].mint == SOL_MINT and hs.locked_rent_lamports == 0


def test_requote_beyond_slippage_fails_tx_but_fee_is_paid(tmp_path):
    m = FakeMarket()
    ar = arena(tmp_path, m)
    a = ar.agents["momentum"]
    m.next_out = [1_000_000, 900_000]                   # за задержку котировка ухудшилась на 10% > 3%
    assert ar.swap(a, USDC_MINT, TOKEN, 1_000_000, 100, 0, why="t") is None
    assert a.failed_tx == 1 and a.fees_usd > 0 and a.cash_usd < 50
    m.next_out = [1_000_000, 990_000]                   # в допуске: исполняется по худшей
    assert ar.swap(a, USDC_MINT, TOKEN, 1_000_000, 100, 0, why="t") == 990_000
    m.next_out = [1_000_000, 1_100_000]                 # стало лучше — всё равно не лучше q0
    assert ar.swap(a, USDC_MINT, TOKEN, 1_000_000, 100, 0, why="t") == 1_000_000


def test_take_profit_closes_with_net_after_costs_and_refunds_rent(tmp_path):
    m = FakeMarket()
    ar = arena(tmp_path, m)
    ar.tick()
    m.token_price = 1.3                                  # +30% > TP 25%
    ar.tick()
    mo = ar.agents["momentum"]
    assert mo.trades == 1 and mo.wins == 1 and not mo.positions and mo.locked_rent_lamports == 0
    assert mo.realized_usd == pytest.approx(12.5 * 0.3 - 2 * ar.tx_fee_usd(100, 0), rel=1e-3)
    assert TOKEN in mo.seen                              # в тот же токен не перезаходит
    journal = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e.get("ev") == "close" and e["reason"] == "tp" for e in journal)


def test_agent_dies_below_floor_and_stays_dead(tmp_path):
    m = FakeMarket()
    ar = arena(tmp_path, m, death_floor_usd=45.0)
    ar.tick()
    m.token_price = 1e-7                                 # токен обнулился
    ar.agents["momentum"].cash_usd = 5.0                 # и денег почти нет
    ar.tick()
    mo = ar.agents["momentum"]
    assert not mo.alive and mo.died_at
    trades = mo.trades
    m._universe = [hot(mint="Other111111111111111111111111111111111111111")]
    ar.tick()
    assert mo.trades == trades and not mo.positions      # мёртвый не торгует


def test_state_survives_restart(tmp_path):
    m = FakeMarket()
    ar = arena(tmp_path, m)
    ar.tick()
    again = arena(tmp_path, m)
    assert again.ticks == 1
    assert len(again.agents["momentum"].positions) == 1
    assert again.agents["momentum"].equity_usd == pytest.approx(ar.agents["momentum"].equity_usd)


def test_promotion_requires_trades_alive_and_beating_benchmarks():
    board = [{"agent": "cash", "alive": True, "equity_usd": 50.0, "trades": 0},
             {"agent": "hold_sol", "alive": True, "equity_usd": 52.0, "trades": 1},
             {"agent": "a", "alive": True, "equity_usd": 60.0, "trades": 40},
             {"agent": "b", "alive": True, "equity_usd": 60.0, "trades": 5},
             {"agent": "c", "alive": True, "equity_usd": 51.0, "trades": 40},
             {"agent": "d", "alive": False, "equity_usd": 70.0, "trades": 40}]
    assert promotion_candidates(board) == ["a"]


def test_arena_size_is_5_to_10(tmp_path):
    with pytest.raises(SystemExit):
        sa.main(["--out", str(tmp_path), "--agents", "cash,hold_sol", "--ticks", "1"])


def test_module_never_signs_or_sends():
    src = Path(sa.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
               {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"solders", "solana", "nacl", "base58", "httpx"}
    for word in ("sendTransaction", "swap/v1/swap", "Keypair", "private_key"):
        assert word not in src
    with pytest.raises(PermissionError):
        sa.LiveMarket()._get("https://quote-api.jup.ag/v6/swap")


def test_missing_quote_is_unknown_price_not_zero(tmp_path):
    """09.10: один no_route по SOL обнулил hold_sol и убил эталон без убытка."""
    m = FakeMarket()
    ar = arena(tmp_path, m)
    ar.tick()
    hs = ar.agents["hold_sol"]
    before = hs.equity_usd
    real_quote = m.quote
    m.quote = lambda i, o, amount, slip: None if i == SOL_MINT else real_quote(i, o, amount, slip)
    ar.tick()
    assert hs.alive and hs.positions and hs.equity_usd == pytest.approx(before, rel=1e-6)
    journal = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert '"ev": "mark_unavailable"' in journal and '"ev": "dead"' not in journal


def test_position_without_any_sale_route_is_written_off_and_agent_can_die(tmp_path):
    m = FakeMarket()
    ar = arena(tmp_path, m, write_off_ticks=3)
    ar.tick()
    ar.agents["momentum"].cash_usd = 1.0
    real_quote = m.quote
    m.quote = lambda i, o, amount, slip: None if i == TOKEN else real_quote(i, o, amount, slip)
    m._universe = []
    for _ in range(2):
        ar.tick()
        assert ar.agents["momentum"].alive                 # цена неизвестна — не хороним
    ar.tick()                                              # маршрута нет 3 шага подряд: списано
    mo = ar.agents["momentum"]
    assert mo.equity_usd < 2.5 and not mo.alive and not mo.positions and mo.locked_rent_lamports == 0
    journal = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert '"ev": "written_off"' in journal and '"ev": "dead"' in journal
