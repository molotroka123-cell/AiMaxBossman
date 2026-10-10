"""Paper-арена Solana: 5–10 агентов с виртуальными $50 на реальных котировках.

Зачем: прежде чем владелец доверит агенту настоящие $50, стратегия должна
прожить на виртуальных деньгах в тех же условиях, что и настоящая:

* цена входа и выхода — НЕ средняя цена, а реальная котировка Jupiter на
  точный размер сделки (price impact и комиссии пулов уже внутри);
* задержка: решение по котировке q0, исполнение по повторной котировке q1
  через latency_s; берётся худшая из двух; если q1 хуже q0 больше допуска
  slippage_bps — транзакция «падает», как упала бы в сети: комиссия сети
  списана, обмена нет;
* каждая транзакция платит base fee 5000 лампортов + priority fee
  (p75 последних слотов из RPC × лимит CU), пересчёт в $ по цене SOL;
* покупка нового токена блокирует ренту токен-аккаунта, продажа целиком
  закрывает аккаунт и возвращает ренту;
* оценка позиции — котировка продажи, то есть сколько реально вернётся;
* агент, у которого капитал упал ниже death_floor_usd, ликвидируется и
  выключается навсегда.

Ключей, подписей и отправки транзакций здесь нет и не будет: вызываются
только /quote, /price, списки DexScreener и getRecentPrioritizationFees.
Настоящие сделки делает владелец сам; арена выдаёт лишь кандидатов.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # ci-secret-scan: allow (публичный mint USDC)
USDC_DECIMALS = 6
LAMPORTS = 1_000_000_000
BASE_FEE_LAMPORTS = 5_000                 # одна подпись
ATA_RENT_LAMPORTS = 2_039_280             # рента токен-аккаунта SPL
DEFAULT_CU_LIMIT = 300_000                # лимит вычислений свопа, консервативно

JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
JUP_PRICE = "https://lite-api.jup.ag/price/v3"
DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
ALLOWED_HOSTS = ("lite-api.jup.ag", "api.dexscreener.com", "api.mainnet-beta.solana.com")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ======================================================================
# Рынок: только чтение
# ======================================================================

class Market(Protocol):
    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> dict | None: ...
    def sol_usd(self) -> float: ...
    def priority_fee_micro_lamports(self) -> int: ...
    def universe(self) -> list[dict]: ...


class LiveMarket:
    """Публичные бесплатные API. Никаких ключей, ничего не подписывается."""

    def __init__(self, *, timeout: float = 10.0, min_gap_s: float = 1.1):
        self.timeout = timeout
        self.min_gap_s = min_gap_s
        self._last = 0.0

    def _get(self, url: str, body: dict | None = None) -> Any:
        host = urllib.parse.urlparse(url).hostname
        if host not in ALLOWED_HOSTS:
            raise PermissionError(f"host {host} is not a read-only market source")
        wait = self.min_gap_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                              "User-Agent": "bossman-paper-arena/1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        finally:
            self._last = time.monotonic()

    def quote(self, input_mint, output_mint, amount, slippage_bps):
        q = urllib.parse.urlencode({"inputMint": input_mint, "outputMint": output_mint,
                                    "amount": int(amount), "slippageBps": int(slippage_bps)})
        try:
            out = self._get(f"{JUP_QUOTE}?{q}")
        except Exception:
            return None
        return out if isinstance(out, dict) and out.get("outAmount") else None

    def sol_usd(self):
        return float(self._get(f"{JUP_PRICE}?ids={SOL_MINT}")[SOL_MINT]["usdPrice"])

    def priority_fee_micro_lamports(self):
        out = self._get(SOLANA_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees",
                                     "params": [["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"]]})  # ci-secret-scan: allow (публичная программа Jupiter v6)
        fees = sorted(int(x["prioritizationFee"]) for x in out.get("result", []))
        return fees[int(len(fees) * 0.75)] if fees else 0

    def universe(self):
        mints: list[str] = []
        for url in (DEX_PROFILES, DEX_BOOSTS):
            try:
                for row in self._get(url):
                    if row.get("chainId") == "solana" and row.get("tokenAddress") not in mints:
                        mints.append(row["tokenAddress"])
            except Exception:
                continue
        pairs: list[dict] = []
        for i in range(0, len(mints), 30):
            try:
                pairs.extend(self._get(DEX_TOKENS + ",".join(mints[i:i + 30])))
            except Exception:
                continue
        return best_pairs(pairs)


def best_pairs(pairs: list[dict]) -> list[dict]:
    """Одна, самая ликвидная пара на токен, в виде снимка."""
    best: dict[str, dict] = {}
    for p in pairs:
        mint = (p.get("baseToken") or {}).get("address")
        if not mint or mint in (SOL_MINT, USDC_MINT):
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if liq > ((best.get(mint) or {}).get("liquidity") or {}).get("usd", -1):
            best[mint] = p
    return [snapshot(p) for p in best.values()]


def snapshot(p: dict, *, now_ms: float | None = None) -> dict:
    """Плоский снимок пары DexScreener: только то, что видно в момент решения."""
    tx = p.get("txns") or {}
    pc = p.get("priceChange") or {}
    vol = p.get("volume") or {}
    created = p.get("pairCreatedAt")
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    return {
        "mint": p["baseToken"]["address"], "symbol": p["baseToken"].get("symbol", "?"),
        "price_usd": float(p.get("priceUsd") or 0),
        "liq_usd": float((p.get("liquidity") or {}).get("usd") or 0),
        "fdv": float(p.get("fdv") or 0),
        "chg_m5": float(pc.get("m5") or 0), "chg_h1": float(pc.get("h1") or 0), "chg_h6": float(pc.get("h6") or 0),
        "vol_m5": float(vol.get("m5") or 0), "vol_h1": float(vol.get("h1") or 0),
        "buys_m5": int((tx.get("m5") or {}).get("buys") or 0), "sells_m5": int((tx.get("m5") or {}).get("sells") or 0),
        "buys_h1": int((tx.get("h1") or {}).get("buys") or 0), "sells_h1": int((tx.get("h1") or {}).get("sells") or 0),
        "age_min": (now_ms - created) / 60000 if created else None,
    }


# ======================================================================
# Стратегии: правила, не нейросеть; каждая видит только снимок «сейчас»
# ======================================================================

def _ratio(b: int, s: int) -> float:
    return b / s if s else (float(b) if b else 0.0)


@dataclass(frozen=True)
class Strategy:
    name: str
    describe: str
    entry: Callable[[dict], bool]
    tp: float                 # +доля
    sl: float                 # −доля
    max_hold_min: float
    size_frac: float          # доля капитала на сделку
    max_positions: int = 2
    exit_signal: Callable[[dict], bool] | None = None


def _momentum(t):  return t["liq_usd"] >= 50_000 and t["chg_h1"] > 20 and t["chg_m5"] > 0 and _ratio(t["buys_m5"], t["sells_m5"]) > 1.2
def _dip(t):       return t["liq_usd"] >= 100_000 and t["chg_h1"] < -25 and t["chg_m5"] > 2
def _new_pair(t):  return t["age_min"] is not None and t["age_min"] < 60 and t["liq_usd"] >= 20_000 and t["buys_m5"] > t["sells_m5"]
def _flow(t):      return t["liq_usd"] >= 75_000 and t["chg_h1"] > 0 and _ratio(t["buys_m5"], t["sells_m5"]) >= 1.5 and _ratio(t["buys_h1"], t["sells_h1"]) >= 1.2
def _flow_exit(t): return _ratio(t["buys_m5"], t["sells_m5"]) < 0.8
def _blue(t):      return t["liq_usd"] >= 1_000_000 and -3 < t["chg_h1"] < 3 and t["chg_h6"] < -8 and t["chg_m5"] > 0.5


BENCHMARKS = ("cash", "hold_sol")
STRATEGIES: dict[str, Strategy] = {s.name: s for s in [
    Strategy("cash", "эталон: держит USDC, ничего не делает", lambda t: False, 0, 0, 0, 0, 0),
    Strategy("hold_sol", "эталон: купил SOL на всё и держит", lambda t: False, 0, 0, 0, 0.98, 1),
    Strategy("momentum", "рост h1 >20%, m5 зелёный, покупок больше продаж; TP+25% SL-12% ≤60 мин",
             _momentum, 0.25, 0.12, 60, 0.25),
    Strategy("dip_buyer", "падение h1 >25% и отскок m5 >2%, ликвидность ≥$100k; TP+15% SL-10% ≤45 мин",
             _dip, 0.15, 0.10, 45, 0.25),
    Strategy("new_pair", "пара младше 60 мин, ликвидность ≥$20k, покупок больше; TP+40% SL-20% ≤30 мин",
             _new_pair, 0.40, 0.20, 30, 0.20),
    Strategy("flow_matrix", "Playbook C: цена h1 вверх + перевес покупок m5≥1.5 и h1≥1.2; выход при перевесе продаж (G)",
             _flow, 0.20, 0.10, 90, 0.25, exit_signal=_flow_exit),
    Strategy("blue_reversion", "ликвидность ≥$1M, h6 ниже −8%, h1 стабилизировался, m5 вверх; TP+6% SL-4% ≤120 мин",
             _blue, 0.06, 0.04, 120, 0.30),
]}


# ======================================================================
# Агент и исполнение
# ======================================================================

@dataclass
class Position:
    mint: str
    symbol: str
    amount: int               # атомарные единицы токена (из котировки)
    cost_usd: float           # всё, что ушло: USDC + рента + комиссия входа
    opened_at: float
    rent_lamports: int
    last_mark_usd: float | None = None   # последняя подтверждённая котировка продажи
    unpriced_ticks: int = 0               # шагов подряд без котировки


@dataclass
class Agent:
    name: str
    strategy: str
    cash_usd: float
    start_usd: float
    alive: bool = True
    died_at: str | None = None
    positions: list[Position] = field(default_factory=list)
    trades: int = 0
    wins: int = 0
    realized_usd: float = 0.0
    fees_usd: float = 0.0
    failed_tx: int = 0
    locked_rent_lamports: int = 0
    equity_usd: float = 0.0
    seen: list[str] = field(default_factory=list)     # не перезаходить в тот же токен


@dataclass
class Config:
    agents: list[str]
    start_usd: float = 50.0
    death_floor_usd: float = 2.5
    write_off_ticks: int = 30          # столько шагов без маршрута продажи = позиция стоит 0 (rug)
    slippage_bps: int = 300
    latency_s: float = 2.0
    cu_limit: int = DEFAULT_CU_LIMIT


class Arena:
    def __init__(self, market: Market, cfg: Config, out_dir: Path, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.m, self.cfg, self.out = market, cfg, Path(out_dir)
        self.sleep, self.clock = sleep, clock
        self.out.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out / "state.json"
        self.journal_path = self.out / "journal.jsonl"
        self.agents: dict[str, Agent] = {}
        self.ticks = 0
        self._load()

    # ---- состояние переживает перезапуск
    def _load(self) -> None:
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.ticks = raw.get("ticks", 0)
            for a in raw["agents"]:
                a["positions"] = [Position(**p) for p in a["positions"]]
                self.agents[a["name"]] = Agent(**a)
        for name in self.cfg.agents:
            if name not in STRATEGIES:
                raise ValueError(f"unknown strategy {name}")
            self.agents.setdefault(name, Agent(name, name, self.cfg.start_usd, self.cfg.start_usd,
                                               equity_usd=self.cfg.start_usd))

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ticks": self.ticks, "saved_at": _now(),
                                   "agents": [asdict(a) for a in self.agents.values()]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def log(self, **event) -> None:
        event = {"ts": _now(), **event}
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # ---- стоимость одной транзакции в $
    def tx_fee_usd(self, sol_usd: float, prio_micro: int) -> float:
        prio_lamports = prio_micro * self.cfg.cu_limit // 1_000_000
        return (BASE_FEE_LAMPORTS + prio_lamports) / LAMPORTS * sol_usd

    def swap(self, agent: Agent, in_mint: str, out_mint: str, amount: int, sol_usd: float, prio: int,
             *, why: str) -> int | None:
        """Обмен с задержкой и повторной котировкой. Полученное или None (tx упала / нет маршрута)."""
        q0 = self.m.quote(in_mint, out_mint, amount, self.cfg.slippage_bps)
        if not q0:
            self.log(agent=agent.name, ev="no_route", why=why, in_mint=in_mint, out_mint=out_mint, amount=amount)
            return None
        self.sleep(self.cfg.latency_s)
        q1 = self.m.quote(in_mint, out_mint, amount, self.cfg.slippage_bps)
        fee = self.tx_fee_usd(sol_usd, prio)              # комиссия сети платится и за упавшую tx
        agent.cash_usd -= fee
        agent.fees_usd += fee
        out0 = int(q0["outAmount"])
        min_out = out0 * (10_000 - self.cfg.slippage_bps) // 10_000
        out1 = int(q1["outAmount"]) if q1 else 0
        if out1 < min_out:
            agent.failed_tx += 1
            self.log(agent=agent.name, ev="tx_failed_slippage", why=why, q0=out0, q1=out1, min_out=min_out, fee_usd=fee)
            return None
        got = min(out0, out1)
        self.log(agent=agent.name, ev="fill", why=why, in_mint=in_mint, out_mint=out_mint, amount_in=amount,
                 out=got, q0=out0, q1=out1, impact_pct=float(q1.get("priceImpactPct") or 0), fee_usd=fee,
                 route=[(r.get("swapInfo") or {}).get("label") for r in q1.get("routePlan", [])])
        return got

    def mark(self, pos: Position) -> float | None:
        """Сколько USDC реально вернётся при продаже сейчас; None — котировки сейчас нет."""
        q = self.m.quote(pos.mint, USDC_MINT, pos.amount, self.cfg.slippage_bps)
        if not q:
            pos.unpriced_ticks += 1
            return None
        pos.unpriced_ticks = 0
        pos.last_mark_usd = int(q["outAmount"]) / 10 ** USDC_DECIMALS
        return pos.last_mark_usd

    # ---- один шаг
    def tick(self) -> None:
        self.ticks += 1
        sol_usd = self.m.sol_usd()
        prio = self.m.priority_fee_micro_lamports()
        live = [a for a in self.agents.values() if a.alive]
        universe = self.m.universe() if any(STRATEGIES[a.strategy].tp for a in live) else []
        by_mint = {t["mint"]: t for t in universe}
        now = self.clock()
        for a in live:
            s = STRATEGIES[a.strategy]
            values: dict[int, float | None] = {}
            for pos in list(a.positions):                         # выходы
                value = values[id(pos)] = self.mark(pos)
                if value is None or a.strategy in BENCHMARKS:
                    continue
                back = value + pos.rent_lamports / LAMPORTS * sol_usd       # рента вернётся при закрытии
                pnl = (back - pos.cost_usd) / pos.cost_usd if pos.cost_usd else 0.0
                age = (now - pos.opened_at) / 60
                snap = by_mint.get(pos.mint)
                reason = ("tp" if pnl >= s.tp else "sl" if pnl <= -s.sl else
                          "time" if age >= s.max_hold_min else
                          "signal" if s.exit_signal and snap and s.exit_signal(snap) else None)
                if reason and self.close(a, pos, sol_usd, prio, reason):
                    values.pop(id(pos), None)
            if a.strategy == "hold_sol" and not a.positions and a.trades == 0:   # входы
                self.open(a, {"mint": SOL_MINT, "symbol": "SOL"}, a.cash_usd * s.size_frac, sol_usd, prio, "benchmark")
            elif s.tp:
                for t in universe:
                    if len(a.positions) >= s.max_positions:
                        break
                    if t["mint"] in a.seen or not s.entry(t):
                        continue
                    a.seen.append(t["mint"])
                    self.open(a, t, a.cash_usd * s.size_frac, sol_usd, prio, "entry")
            # Оценка и смерть. Сбой котировки — это незнание цены, а не нулевая цена:
            # 09.10 один no_route по SOL обнулил hold_sol и убил его без убытка.
            held, unknown = 0.0, []
            for pos in a.positions:
                v = values[id(pos)] if id(pos) in values else self.mark(pos)
                if v is None and pos.unpriced_ticks >= self.cfg.write_off_ticks:
                    v = 0.0                                       # продать нельзя давно — списано
                elif v is None:
                    unknown.append(pos.symbol)
                    v = pos.last_mark_usd or 0.0
                held += v
            a.equity_usd = a.cash_usd + held + a.locked_rent_lamports / LAMPORTS * sol_usd
            if unknown:
                self.log(agent=a.name, ev="mark_unavailable", symbols=unknown, equity_usd=a.equity_usd)
            elif a.equity_usd < self.cfg.death_floor_usd:
                for pos in list(a.positions):
                    if not self.close(a, pos, sol_usd, prio, "liquidation") and                             pos.unpriced_ticks >= self.cfg.write_off_ticks:
                        a.positions.remove(pos)                   # продать нельзя: убыток целиком
                        a.locked_rent_lamports -= pos.rent_lamports
                        a.trades += 1
                        a.realized_usd -= pos.cost_usd
                        self.log(agent=a.name, ev="written_off", symbol=pos.symbol, loss_usd=pos.cost_usd)
                if a.positions:                                   # ликвидация не прошла — пробуем на следующем шаге
                    self.log(agent=a.name, ev="liquidation_pending", symbols=[p.symbol for p in a.positions])
                else:
                    a.alive, a.died_at = False, _now()
                    self.log(agent=a.name, ev="dead", equity_usd=a.equity_usd)
        self.log(ev="tick", n=self.ticks, sol_usd=sol_usd, prio_micro=prio, universe=len(universe),
                 equity={a.name: round(a.equity_usd, 4) for a in self.agents.values()})
        self.save()

    def open(self, a: Agent, t: dict, usd: float, sol_usd: float, prio: int, why: str) -> bool:
        rent = 0 if t["mint"] == SOL_MINT else ATA_RENT_LAMPORTS
        rent_usd = rent / LAMPORTS * sol_usd
        need = usd + rent_usd + 2 * self.tx_fee_usd(sol_usd, prio)          # вход и будущий выход
        if usd < 1 or need > a.cash_usd:
            return False
        fee_before = a.fees_usd
        got = self.swap(a, USDC_MINT, t["mint"], int(usd * 10 ** USDC_DECIMALS), sol_usd, prio,
                        why=f"{why}:{t['symbol']}")
        if not got:
            return False
        a.cash_usd -= usd + rent_usd
        a.locked_rent_lamports += rent
        a.positions.append(Position(t["mint"], t["symbol"], got, usd + rent_usd + (a.fees_usd - fee_before),
                                    self.clock(), rent))
        self.log(agent=a.name, ev="open", symbol=t["symbol"], mint=t["mint"], usd=usd, snap=t)
        return True

    def close(self, a: Agent, pos: Position, sol_usd: float, prio: int, reason: str) -> bool:
        fee_before = a.fees_usd
        got = self.swap(a, pos.mint, USDC_MINT, pos.amount, sol_usd, prio, why=f"exit:{reason}:{pos.symbol}")
        if not got:
            return False
        usd = got / 10 ** USDC_DECIMALS
        refund = pos.rent_lamports / LAMPORTS * sol_usd           # аккаунт закрыт в той же tx
        a.cash_usd += usd + refund
        a.locked_rent_lamports -= pos.rent_lamports
        a.positions.remove(pos)
        net = usd + refund - pos.cost_usd - (a.fees_usd - fee_before)
        a.trades += 1
        a.wins += int(net > 0)
        a.realized_usd += net
        self.log(agent=a.name, ev="close", symbol=pos.symbol, reason=reason, usd=usd, net_usd=net)
        return True

    # ---- итог
    def leaderboard(self) -> list[dict]:
        rows = []
        for a in self.agents.values():
            rows.append({"agent": a.name, "alive": a.alive, "equity_usd": round(a.equity_usd, 2),
                         "pnl_pct": round((a.equity_usd / a.start_usd - 1) * 100, 2), "trades": a.trades,
                         "win_rate": round(a.wins / a.trades, 2) if a.trades else None,
                         "fees_usd": round(a.fees_usd, 4), "failed_tx": a.failed_tx,
                         "open": [p.symbol for p in a.positions]})
        return sorted(rows, key=lambda r: -r["equity_usd"])


def promotion_candidates(board: list[dict], *, min_trades: int = 30) -> list[str]:
    """Кого можно проверять на реальных деньгах: живой, ≥min_trades сделок, лучше обоих эталонов."""
    bench = [r["equity_usd"] for r in board if r["agent"] in BENCHMARKS]
    floor = max(bench) if bench else 0.0
    return [r["agent"] for r in board if r["agent"] not in BENCHMARKS and r["alive"]
            and r["trades"] >= min_trades and r["equity_usd"] > floor]


def report_md(arena: Arena) -> str:
    board = arena.leaderboard()
    lines = [f"# Paper-арена Solana — шаг {arena.ticks}, {_now()}", "",
             f"Виртуально по ${arena.cfg.start_usd:.0f} на агента. Цены — реальные котировки Jupiter на размер сделки, "
             f"задержка {arena.cfg.latency_s:.0f} с, допуск {arena.cfg.slippage_bps / 100:.1f}%, комиссии сети и рента учтены. "
             "Реальных сделок нет.", "",
             "| Агент | Жив | Капитал $ | PnL % | Сделок | Win | Комиссии $ | Упавших tx | Открыто |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in board:
        lines.append(f"| {r['agent']} | {'да' if r['alive'] else 'НЕТ'} | {r['equity_usd']} | {r['pnl_pct']} | "
                     f"{r['trades']} | {r['win_rate'] if r['win_rate'] is not None else '—'} | {r['fees_usd']} | "
                     f"{r['failed_tx']} | {', '.join(r['open']) or '—'} |")
    cands = promotion_candidates(board)
    lines += ["", "Стратегии:"] + [f"- {n}: {STRATEGIES[n].describe}" for n in arena.cfg.agents]
    lines += ["", "Кандидаты на проверку реальными деньгами (≥30 сделок, живы, лучше cash и hold_sol): "
              + (", ".join(cands) if cands else "пока нет — мало сделок или хуже эталонов")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bossman.trading_learning.sol_arena")
    p.add_argument("--out", required=True)
    p.add_argument("--agents", default=",".join(STRATEGIES))
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--ticks", type=int, default=0, help="0 = бесконечно")
    p.add_argument("--report", action="store_true", help="только напечатать отчёт по сохранённому состоянию")
    a = p.parse_args(argv)
    names = [n.strip() for n in a.agents.split(",") if n.strip()]
    if not 5 <= len(names) <= 10:
        p.error("арена — от 5 до 10 агентов")
    arena = Arena(LiveMarket(), Config(agents=names), Path(a.out))
    if a.report:
        print(report_md(arena))
        return 0
    n = 0
    while not a.ticks or n < a.ticks:
        started = time.monotonic()
        try:
            arena.tick()
        except Exception as exc:                       # сеть упала — шаг пропущен, состояние цело
            arena.log(ev="tick_error", error=f"{type(exc).__name__}: {exc}"[:300])
        n += 1
        (arena.out / "REPORT.md").write_text(report_md(arena), encoding="utf-8")
        if not any(ag.alive for ag in arena.agents.values()):
            break
        time.sleep(max(0.0, a.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
