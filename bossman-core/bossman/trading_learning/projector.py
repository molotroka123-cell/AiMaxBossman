"""Pump.fun Projector — discovery, прозрачный риск-скор и бумажная торговля новыми токенами Solana.

Только чтение и только симуляция (owner directive 01.11.2026, Workstream C):
* данные — публичные DexScreener-списки (те же хосты и тот же allowlist, что у sol_arena.LiveMarket);
* риск-скор — правила с видимыми причинами; это индикаторы, а не детектор мошенничества;
* бумажная книга — вход/выход по записанной цене снимка, комиссия и проскальзывание явные,
  сделка с price impact выше лимита отклоняется (как «упавшая» транзакция). Ключей, подписей и
  отправки транзакций здесь нет.
Каждый снимок помечен источником и временем; при сбое источника — SOURCE_UNAVAILABLE, не выдумки.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .sol_arena import DEX_BOOSTS, DEX_PROFILES, DEX_TOKENS, LiveMarket, snapshot

PUMP_DEXES = ("pumpfun", "pumpswap")
RULES_VERSION = "projector-risk-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- discovery
def discover(fetch: Callable[[str], Any] | None = None, *, pump_only: bool = False) -> dict:
    """Новые/продвигаемые токены Solana из DexScreener. fetch(url) -> JSON (по умолчанию LiveMarket._get)."""
    get = fetch or LiveMarket()._get
    started = _now_iso()
    mints: list[str] = []
    errors: list[str] = []
    for url in (DEX_PROFILES, DEX_BOOSTS):
        try:
            for row in get(url) or []:
                if isinstance(row, dict) and row.get("chainId") == "solana" and row.get("tokenAddress") not in mints:
                    mints.append(row["tokenAddress"])
        except Exception as exc:  # noqa: BLE001 — источник может лечь; это фиксируется, а не прячется
            errors.append(f"{url}: {type(exc).__name__}")
    pairs: list[dict] = []
    for i in range(0, len(mints), 30):
        try:
            pairs.extend(get(DEX_TOKENS + ",".join(mints[i:i + 30])) or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tokens batch {i // 30}: {type(exc).__name__}")
    best: dict[str, dict] = {}
    for p in pairs:
        mint = (p.get("baseToken") or {}).get("address")
        if not mint:
            continue
        if ((p.get("liquidity") or {}).get("usd") or 0) >= ((best.get(mint) or {}).get("liquidity") or {}).get("usd", -1):
            best[mint] = p
    tokens = []
    for p in best.values():
        if pump_only and p.get("dexId") not in PUMP_DEXES:
            continue
        snap = snapshot(p)
        snap.update(dex_id=p.get("dexId"), url=p.get("url"), name=(p.get("baseToken") or {}).get("name"))
        tokens.append(snap)
    status = "LIVE" if tokens else ("SOURCE_UNAVAILABLE" if errors else "EMPTY")
    return {"status": status, "source": "dexscreener public API (read-only)", "fetched_at": started,
            "tokens": tokens, "errors": errors, "mints_seen": len(mints)}


# ---------------------------------------------------------------- risk
def risk_score(t: dict, holders_top10_pct: float | None = None) -> dict:
    """0..100, чем больше — тем рискованнее. Каждое слагаемое объяснено. Не гарантия и не обвинение."""
    reasons: list[tuple[int, str]] = []
    liq, age = float(t.get("liq_usd") or 0), t.get("age_min")
    if liq <= 0:
        reasons.append((30, "нет данных о ликвидности"))
    elif liq < 10_000:
        reasons.append((30, f"очень низкая ликвидность ${liq:,.0f}"))
    elif liq < 50_000:
        reasons.append((15, f"низкая ликвидность ${liq:,.0f}"))
    if age is None:
        reasons.append((5, "нет времени создания пары"))
    elif age < 30:
        reasons.append((20, f"паре {age:.0f} мин"))
    elif age < 360:
        reasons.append((10, f"паре {age / 60:.1f} ч"))
    b5, s5 = int(t.get("buys_m5") or 0), int(t.get("sells_m5") or 0)
    if s5 > 2 * max(b5, 1):
        reasons.append((15, f"давление продаж за 5 мин ({s5} продаж / {b5} покупок)"))
    h1 = float(t.get("chg_h1") or 0)
    if h1 > 300:
        reasons.append((15, f"рост +{h1:.0f}% за час — риск резкого сброса"))
    elif h1 < -50:
        reasons.append((20, f"обвал {h1:.0f}% за час"))
    vol_h1 = float(t.get("vol_h1") or 0)
    if liq > 0 and vol_h1 / liq > 5:
        reasons.append((10, f"объём за час в {vol_h1 / liq:.1f}× больше ликвидности (возможна накрутка)"))
    fdv = float(t.get("fdv") or 0)
    if liq > 0 and fdv / liq > 50:
        reasons.append((10, f"FDV в {fdv / liq:.0f}× больше ликвидности"))
    if holders_top10_pct is not None and holders_top10_pct > 50:
        reasons.append((25, f"топ-10 держателей владеют {holders_top10_pct:.0f}%"))
    score = min(100, sum(w for w, _ in reasons))
    level = "LOW" if score < 30 else ("MEDIUM" if score < 60 else "HIGH")
    return {"score": score, "level": level, "reasons": [r for _, r in reasons], "rules": RULES_VERSION,
            "disclaimer": "индикаторы риска, не детектор мошенничества и не совет"}


# ---------------------------------------------------------------- paper book
@dataclass
class PaperPosition:
    mint: str
    symbol: str
    entry_price: float
    qty: float
    cost_usd: float
    opened_at: str
    status: str = "OPEN"
    exit_price: float | None = None
    proceeds_usd: float | None = None
    closed_at: str | None = None
    reason: str = ""


@dataclass
class PaperBook:
    """Бумажный счёт. Цена — из записанного снимка; комиссия и проскальзывание явные."""
    cash_usd: float = 100.0
    fee_bps: int = 125            # ~комиссия pump/PumpSwap + сеть на сторону, консервативно
    max_impact_pct: float = 3.0   # больше — отклоняем, как упавшую транзакцию
    size_usd: float = 10.0
    take_profit_pct: float = 30.0
    stop_loss_pct: float = -20.0
    max_hold_min: float = 60.0
    positions: list[PaperPosition] = field(default_factory=list)
    journal: list[dict] = field(default_factory=list)
    equity_peak: float = 100.0

    @staticmethod
    def impact_pct(size_usd: float, liq_usd: float) -> float:
        """Постоянное произведение: сторона пула ≈ liq/2; impact ≈ size / (liq/2 + size)."""
        side = liq_usd / 2
        return 100.0 * size_usd / (side + size_usd) if side > 0 else 100.0

    def buy(self, t: dict, *, now: str) -> dict:
        price, liq = float(t.get("price_usd") or 0), float(t.get("liq_usd") or 0)
        size = min(self.size_usd, self.cash_usd)
        if price <= 0 or size <= 0:
            return self._log("REJECTED", t, now, "нет цены или денег")
        if any(p.mint == t["mint"] and p.status == "OPEN" for p in self.positions):
            return self._log("REJECTED", t, now, "позиция уже открыта")
        imp = self.impact_pct(size, liq)
        if imp > self.max_impact_pct:
            return self._log("REJECTED", t, now, f"price impact {imp:.2f}% > {self.max_impact_pct}%")
        fee = size * self.fee_bps / 10_000
        fill = price * (1 + imp / 100)
        qty = (size - fee) / fill
        self.cash_usd -= size
        self.positions.append(PaperPosition(t["mint"], t.get("symbol", "?"), fill, qty, size, now))
        return self._log("BUY", t, now, f"fill {fill:.8g} impact {imp:.2f}% fee ${fee:.4f}", usd=size)

    def mark(self, prices: dict[str, dict], *, now: str, now_ts: float | None = None) -> list[dict]:
        """Переоценка открытых позиций; выход по TP/SL/времени по цене снимка с комиссией и impact."""
        out = []
        for p in self.positions:
            if p.status != "OPEN" or p.mint not in prices:
                continue
            t = prices[p.mint]
            price, liq = float(t.get("price_usd") or 0), float(t.get("liq_usd") or 0)
            if price <= 0:
                continue
            gross = p.qty * price
            imp = self.impact_pct(gross, liq)
            pnl_pct = 100.0 * (gross - p.cost_usd) / p.cost_usd
            held = ((now_ts or time.time()) - datetime.fromisoformat(p.opened_at).timestamp()) / 60
            why = ("TAKE_PROFIT" if pnl_pct >= self.take_profit_pct else "STOP_LOSS" if pnl_pct <= self.stop_loss_pct
                   else "TIME" if held >= self.max_hold_min else "")
            if not why:
                continue
            fill = price * (1 - imp / 100)
            proceeds = p.qty * fill * (1 - self.fee_bps / 10_000)
            p.status, p.exit_price, p.proceeds_usd, p.closed_at, p.reason = "CLOSED", fill, proceeds, now, why
            self.cash_usd += proceeds
            out.append(self._log("SELL", t, now, f"{why} fill {fill:.8g} pnl ${proceeds - p.cost_usd:+.4f}", usd=proceeds))
        return out

    def equity(self, prices: dict[str, dict]) -> float:
        open_value = sum(p.qty * float((prices.get(p.mint) or {}).get("price_usd") or p.entry_price)
                         for p in self.positions if p.status == "OPEN")
        eq = self.cash_usd + open_value
        self.equity_peak = max(self.equity_peak, eq)
        return eq

    def metrics(self, prices: dict[str, dict]) -> dict:
        eq = self.equity(prices)
        closed = [p for p in self.positions if p.status == "CLOSED"]
        realized = sum((p.proceeds_usd or 0) - p.cost_usd for p in closed)
        wins = sum(1 for p in closed if (p.proceeds_usd or 0) > p.cost_usd)
        return {"equity_usd": round(eq, 4), "cash_usd": round(self.cash_usd, 4), "realized_pnl_usd": round(realized, 4),
                "drawdown_pct": round(100.0 * (eq - self.equity_peak) / self.equity_peak, 2) if self.equity_peak else 0.0,
                "closed_trades": len(closed), "wins": wins,
                "open_positions": sum(1 for p in self.positions if p.status == "OPEN"),
                "evidence_class": "SIMULATED"}

    def _log(self, kind: str, t: dict, now: str, note: str, usd: float = 0.0) -> dict:
        row = {"at": now, "kind": kind, "mint": t.get("mint"), "symbol": t.get("symbol"), "usd": round(usd, 4), "note": note}
        self.journal.append(row)
        return row

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "PaperBook":
        d = dict(d)
        d["positions"] = [PaperPosition(**p) for p in d.get("positions", [])]
        return cls(**d)


# ---------------------------------------------------------------- one refresh cycle
def refresh(state_dir: Path, fetch: Callable[[str], Any] | None = None, *, pump_only: bool = False) -> dict:
    """discover -> score -> paper (mark open, maybe enter the best LOW-risk momentum token) -> persist."""
    state_dir.mkdir(parents=True, exist_ok=True)
    book_path = state_dir / "paper_book.json"
    book = PaperBook.from_json(json.loads(book_path.read_text(encoding="utf-8"))) if book_path.exists() else PaperBook()
    found = discover(fetch, pump_only=pump_only)
    now = found["fetched_at"]
    scored = []
    for t in found["tokens"]:
        t = dict(t)
        t["risk"] = risk_score(t)
        scored.append(t)
    scored.sort(key=lambda t: (t["risk"]["score"], -t["liq_usd"]))
    prices = {t["mint"]: t for t in scored}
    actions = book.mark(prices, now=now) if found["status"] == "LIVE" else []
    if found["status"] == "LIVE":
        cands = [t for t in scored if t["risk"]["level"] == "LOW" and t["chg_m5"] > 0 and t["buys_m5"] > t["sells_m5"]]
        if cands:
            actions.append(book.buy(cands[0], now=now))
    run = {"fetched_at": now, "status": found["status"], "source": found["source"], "errors": found["errors"],
           "tokens": scored[:100], "paper_actions": actions, "paper": book.metrics(prices),
           "positions": [asdict(p) for p in book.positions[-20:]],
           "labels": {"tokens": "LIVE (DexScreener, время в fetched_at)", "risk": "RULES (видимые причины)",
                      "paper": "SIMULATED"}}
    book_path.write_text(json.dumps(book.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    (state_dir / "latest.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    hist = state_dir / "history"
    hist.mkdir(exist_ok=True)
    stamp = now.replace(":", "").replace("+0000", "Z")
    (hist / f"{stamp}.json").write_text(json.dumps(
        {"fetched_at": now, "status": run["status"], "n": len(scored), "paper": run["paper"], "actions": actions},
        ensure_ascii=False), encoding="utf-8")
    return run
