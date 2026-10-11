"""Transparent paper simulator of a single-owner, inventory-aware quoting strategy.

What this is: a teaching/analysis tool that answers "if I quoted both sides of a
book with my OWN capital, how would P&L split between spread capture, adverse
selection and inventory risk, and how wide is the distribution?".

What this is NOT:
  * not proof of live profit - there is no queue priority, no real latency, no
    competing market makers, no exchange outages, no fee tiers beyond the
    constant below; adverse selection is a MODEL parameter, not a measurement;
  * not a way to move a price or fake activity: every fill is an honest fill of
    a resting quote against another participant's order, the owner's inventory
    is the only inventory, and all orders are on ONE account. No wash trades,
    no self-matching, nothing hidden.

P&L decomposition (exact identity, tested):
  total = spread_capture + adverse_selection + inventory_risk
          + flatten_cost + fees
  for each fill (side=+1 we bought, -1 we sold; m = mid at fill):
    spread_capture    += side * (m - fill_price)             (>= 0 by construction)
    adverse_selection += side * (m[t+H] - m)                 (mid move right after fill)
    inventory_risk    += side * (m[T] - m[t+H])              (rest of the holding period)
Paper only: assert_no_live_execution guards every entry point.
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .safety import assert_no_live_execution

DISCLAIMER = ("PAPER SIMULATION. NOT proof of live profit: no queue position, real "
              "latency, competition, outages or exchange rule changes are modelled; "
              "adverse selection is an assumed parameter. Use it to understand risk "
              "composition, not to promise returns.")


class MMError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MMParams:
    half_spread: float = 0.05          # quote distance from mid (price units)
    skew_per_unit: float = 0.01        # quote-centre shift per unit of inventory
    quote_size: float = 1.0
    max_inventory: float = 10.0        # hard cap, quoting stops on the worsening side
    fee_rate: float = 0.0              # maker fee per notional (negative = rebate)
    flatten_half_spread: float = 0.05  # cost to cross the book when flattening
    flatten_fee_rate: float = 0.0005   # taker fee on the flattening trade

    def __post_init__(self) -> None:
        if self.half_spread < 0 or self.quote_size <= 0 or self.max_inventory <= 0:
            raise MMError("invalid market-making parameters")


@dataclass(frozen=True, slots=True)
class SyntheticMarket:
    """Order-flow model for the synthetic book."""

    steps: int = 2000
    start_mid: float = 100.0
    sigma: float = 0.04                # mid move per step (price units)
    arrival_rate: float = 0.6          # P(uninformed order per step) at zero distance
    k: float = 20.0                    # fill probability decays as exp(-k*distance)
    informed_fraction: float = 0.15    # P(order per step comes from an informed trader)
    adverse_horizon: int = 5           # H in the decomposition (steps)


@dataclass
class PathResult:
    total: float = 0.0
    spread_capture: float = 0.0
    adverse_selection: float = 0.0
    inventory_risk: float = 0.0
    flatten_cost: float = 0.0
    fees: float = 0.0
    fills: int = 0
    max_abs_inventory: float = 0.0
    final_inventory: float = 0.0
    max_drawdown: float = 0.0
    mark_to_market: float = 0.0       # cash + inventory * final mid (identity check)


def _decompose(fills: list[tuple[float, float, float, int]], mids: Sequence[float],
               params: MMParams, horizon: int, res: PathResult, final_inv: float) -> None:
    """fills: (side, price, size, idx). Computes decomposition + flatten + fees."""
    t_last = len(mids) - 1
    m_t = mids[t_last]
    for side, price, size, idx in fills:
        m = mids[idx]
        h = min(idx + horizon, t_last)
        res.spread_capture += side * (m - price) * size
        res.adverse_selection += side * (mids[h] - m) * size
        res.inventory_risk += side * (m_t - mids[h]) * size
        res.fees += abs(price * size) * params.fee_rate
    if final_inv:
        # flatten by crossing: pay half spread + taker fee on |inventory|
        res.flatten_cost = (abs(final_inv) * params.flatten_half_spread
                            + abs(final_inv) * m_t * params.flatten_fee_rate)
    res.total = (res.spread_capture + res.adverse_selection + res.inventory_risk
                 - res.flatten_cost - res.fees)


def simulate_path(market: SyntheticMarket, params: MMParams, seed: int) -> PathResult:
    assert_no_live_execution("mm_sim_path", stage="paper_trading")
    rng = random.Random(seed)
    mids = [market.start_mid]
    # pre-draw shocks so informed traders can know the NEXT move
    shocks = [rng.gauss(0.0, market.sigma) for _ in range(market.steps)]
    inv = 0.0
    fills: list[tuple[float, float, float, int]] = []
    cash = 0.0
    peak = 0.0
    worst = 0.0
    max_inv = 0.0
    for t in range(market.steps):
        m = mids[-1]
        centre = m - params.skew_per_unit * inv
        bid = centre - params.half_spread
        ask = centre + params.half_spread
        bid_ok = inv < params.max_inventory
        ask_ok = inv > -params.max_inventory
        nxt = shocks[t]
        # informed order: hits whichever side is about to be wrong, if profitable
        if rng.random() < market.informed_fraction:
            if nxt > 0 and ask_ok and m + nxt > ask:
                cash += ask * params.quote_size
                inv -= params.quote_size
                fills.append((-1.0, ask, params.quote_size, t))
            elif nxt < 0 and bid_ok and m + nxt < bid:
                cash -= bid * params.quote_size
                inv += params.quote_size
                fills.append((+1.0, bid, params.quote_size, t))
        # re-check caps: an informed fill above may already have used the room
        bid_ok = inv < params.max_inventory
        ask_ok = inv > -params.max_inventory
        # uninformed order: random side, probability decays with quote distance
        if rng.random() < market.arrival_rate:
            side_buyer = rng.random() < 0.5   # buyer lifts our ask
            dist = (ask - m) if side_buyer else (m - bid)
            p_fill = math.exp(-market.k * max(dist, 0.0))
            if rng.random() < p_fill:
                if side_buyer and ask_ok:
                    cash += ask * params.quote_size
                    inv -= params.quote_size
                    fills.append((-1.0, ask, params.quote_size, t))
                elif (not side_buyer) and bid_ok:
                    cash -= bid * params.quote_size
                    inv += params.quote_size
                    fills.append((+1.0, bid, params.quote_size, t))
        mids.append(m + nxt)
        equity = cash + inv * mids[-1]
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
        max_inv = max(max_inv, abs(inv))
    res = PathResult(fills=len(fills), max_abs_inventory=max_inv, final_inventory=inv,
                     max_drawdown=abs(worst), mark_to_market=cash + inv * mids[-1])
    _decompose(fills, mids, params, market.adverse_horizon, res, inv)
    return res


@dataclass
class Distribution:
    n_paths: int
    mean: float
    std: float
    quantiles: dict[str, float]
    prob_loss: float
    mean_ci95: tuple[float, float]
    components: dict[str, float]
    mean_fills: float
    mean_max_inventory: float
    mean_max_drawdown: float
    ci_includes_zero: bool
    text: str = ""
    paths: list[PathResult] = field(default_factory=list, repr=False)


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def summarize(paths: Sequence[PathResult], *, title: str = "synthetic book",
              track_drawdown: bool = True) -> Distribution:
    n = len(paths)
    if n == 0:
        raise MMError("no paths")
    totals = sorted(p.total for p in paths)
    mean = sum(totals) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in totals) / max(n - 1, 1))
    se = std / math.sqrt(n)
    ci = (mean - 1.96 * se, mean + 1.96 * se)
    comps = {k: sum(getattr(p, k) for p in paths) / n for k in
             ("spread_capture", "adverse_selection", "inventory_risk", "flatten_cost", "fees")}
    dist = Distribution(
        n_paths=n, mean=mean, std=std,
        quantiles={f"p{int(q * 100):02d}": _quantile(totals, q)
                   for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        prob_loss=sum(1 for x in totals if x < 0) / n, mean_ci95=ci, components=comps,
        mean_fills=sum(p.fills for p in paths) / n,
        mean_max_inventory=sum(p.max_abs_inventory for p in paths) / n,
        mean_max_drawdown=sum(p.max_drawdown for p in paths) / n,
        ci_includes_zero=ci[0] <= 0.0 <= ci[1], paths=list(paths))
    dist.text = render(dist, title, track_drawdown)
    return dist


def render(d: Distribution, title: str, track_drawdown: bool = True) -> str:
    c = d.components
    lines = [f"MARKET-MAKING PAPER ANALYSIS ({title})", DISCLAIMER,
             f"paths: {d.n_paths}; mean fills/path: {d.mean_fills:.1f}",
             f"P&L mean {d.mean:+.4f} (std {d.std:.4f}), 95% CI [{d.mean_ci95[0]:+.4f}, "
             f"{d.mean_ci95[1]:+.4f}]; P(loss) = {d.prob_loss:.1%}",
             "quantiles: " + ", ".join(f"{k}={v:+.3f}" for k, v in d.quantiles.items()),
             "mean components: "
             f"spread_capture={c['spread_capture']:+.4f}, "
             f"adverse_selection={c['adverse_selection']:+.4f}, "
             f"inventory_risk={c['inventory_risk']:+.4f}, "
             f"flatten_cost=-{c['flatten_cost']:.4f}, fees=-{c['fees']:.4f}",
             f"mean max |inventory|: {d.mean_max_inventory:.1f}; mean max drawdown: "
             + (f"{d.mean_max_drawdown:.3f}" if track_drawdown
                else "n/a (not tracked in replay)")]
    if d.ci_includes_zero:
        lines.append("RESULT: the confidence interval INCLUDES ZERO under these assumptions.")
    elif d.mean < 0:
        lines.append("RESULT: negative expected P&L under these assumptions.")
    else:
        lines.append("RESULT: positive expected P&L UNDER THESE ASSUMED PARAMETERS ONLY; "
                     "the assumption set (informed fraction, fill model) decides the sign.")
    return "\n".join(lines)


def run_synthetic(market: SyntheticMarket | None = None, params: MMParams | None = None,
                  *, n_paths: int = 200, seed: int = 1) -> Distribution:
    market = market or SyntheticMarket()
    params = params or MMParams()
    paths = [simulate_path(market, params, seed * 100003 + i) for i in range(n_paths)]
    return summarize(paths, title="synthetic book")


# ------------------------------------------------------------ recorded book
@dataclass(frozen=True, slots=True)
class BookEvent:
    kind: str       # "book" | "trade"
    ts: float
    bid: float = 0.0
    ask: float = 0.0
    price: float = 0.0
    size: float = 0.0
    taker_side: str = ""     # "buy" (lifts asks) | "sell" (hits bids)


def load_recorded(path: str | Path) -> list[BookEvent]:
    """CSV columns: kind,ts,bid,ask,price,size,taker_side. Read-only local file."""
    out: list[BookEvent] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kind = row["kind"].strip()
            if kind not in ("book", "trade"):
                raise MMError(f"unknown event kind {kind!r}")
            out.append(BookEvent(kind, float(row["ts"]), float(row.get("bid") or 0),
                                 float(row.get("ask") or 0), float(row.get("price") or 0),
                                 float(row.get("size") or 0), (row.get("taker_side") or "").strip()))
    out.sort(key=lambda e: e.ts)
    if not any(e.kind == "book" for e in out):
        raise MMError("recorded data has no book snapshots")
    return out


def replay_recorded(events: Iterable[BookEvent], params: MMParams, *,
                    at_touch_fill_probability: float = 0.25, adverse_horizon: int = 5,
                    seed: int = 1, blocks: int = 10) -> Distribution:
    """Replay one recording against our quotes.

    Conservative fill rule: a taker trade fills our quote only if its price is
    STRICTLY through our quote, or exactly at it with probability
    ``at_touch_fill_probability`` (queue position is unknown, so we do not
    pretend to be first in line). One path -> distribution is built from
    ``blocks`` consecutive time blocks, which understates dependence; treat
    the interval as a rough dispersion indicator.
    """
    assert_no_live_execution("mm_sim_replay", stage="paper_trading")
    rng = random.Random(seed)
    evs = list(events)
    mids: list[float] = []
    fills: list[tuple[float, float, float, int]] = []
    inv = 0.0
    last_bid = last_ask = None
    for e in evs:
        if e.kind == "book":
            if e.bid <= 0 or e.ask <= 0 or e.ask < e.bid:
                continue
            last_bid, last_ask = e.bid, e.ask
            mids.append((e.bid + e.ask) / 2)
            continue
        if last_bid is None or not mids:
            continue
        m = mids[-1]
        centre = m - params.skew_per_unit * inv
        bid = centre - params.half_spread
        ask = centre + params.half_spread
        idx = len(mids) - 1
        if e.taker_side == "buy" and inv > -params.max_inventory:
            through = e.price > ask
            touch = e.price == ask
            if through or (touch and rng.random() < at_touch_fill_probability):
                inv -= params.quote_size
                fills.append((-1.0, ask, params.quote_size, idx))
        elif e.taker_side == "sell" and inv < params.max_inventory:
            through = e.price < bid
            touch = e.price == bid
            if through or (touch and rng.random() < at_touch_fill_probability):
                inv += params.quote_size
                fills.append((+1.0, bid, params.quote_size, idx))
    if len(mids) < 2:
        raise MMError("recorded data too short")
    # split into time blocks by mid index
    paths: list[PathResult] = []
    size = max(2, len(mids) // blocks)
    for b in range(0, len(mids) - 1, size):
        seg = mids[b:b + size + 1]
        if len(seg) < 2:
            continue
        seg_fills = [(s, p, q, i - b) for s, p, q, i in fills if b <= i <= b + size]
        res = PathResult(fills=len(seg_fills))
        net_inv = sum(s * q for s, _, q, _ in seg_fills)
        res.final_inventory = net_inv
        res.max_abs_inventory = abs(net_inv)
        _decompose(seg_fills, seg, params, adverse_horizon, res, net_inv)
        paths.append(res)
    return summarize(paths, title="recorded book replay", track_drawdown=False)
