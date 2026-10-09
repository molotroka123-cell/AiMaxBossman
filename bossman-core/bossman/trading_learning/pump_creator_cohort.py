"""Сколько на самом деле зарабатывает создатель токена pump.fun на creator fee.

Владелец: «они деплоят кучу токенов и зарабатывают … с объёма пула … комиссии,
пусть умеет вести токен». Прежде чем вести свой токен, Bossman измеряет, что
приносит creator fee на НАСТОЯЩИХ токенах, без выборки по успешным:

* poll — каждые N минут берёт все новые пулы pump.fun из GeckoTerminal
  (new_pools), записывает токен и время появления в cohort.jsonl;
* measure — когда токену исполнилось 24 ч, берёт его пулы (кривая pump-fun и,
  если мигрировал, pumpswap), объём за 24 ч и капитализацию;
* creator fee = объём кривой × ставка кривой + объём pumpswap × ставка тира по
  капитализации. Ставки — снимок аккаунтов mainnet (pump_fee_tables_*.json),
  не по памяти;
* report — распределение по всей когорте: медиана, доля токенов, где
  создатель получил хотя бы $1/$10/$100.

Объём включает и чужую, и самоторговлю создателя: самоторговля платит ВСЕ
комиссии (≈1.25% на кривой) ради 0.30% себе, то есть убыточна. Модуль ничего
не покупает и не подписывает.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
TABLES = Path(__file__).with_name("pump_fee_tables_20261009.json")
DAY_S = 24 * 3600


def load_tables(path: Path = TABLES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tier_bps(tiers: list[dict], mcap_sol: float) -> int:
    """Ставка создателя по капитализации — та же логика, что calculateFeeTier в SDK."""
    tiers = sorted(tiers, key=lambda t: t["mcap_sol"])
    if mcap_sol < tiers[0]["mcap_sol"]:
        return int(tiers[0]["creator"])
    for t in reversed(tiers):
        if mcap_sol >= t["mcap_sol"]:
            return int(t["creator"])
    return int(tiers[0]["creator"])


def creator_fee_usd(curve_vol_usd: float, swap_vol_usd: float, mcap_usd: float, sol_usd: float, tables: dict) -> dict:
    curve_bps = tier_bps(tables["bonding_curve"]["tiers"], 0.0)
    swap_bps = tier_bps(tables["pumpswap"]["tiers"], mcap_usd / sol_usd if sol_usd else 0.0)
    curve = curve_vol_usd * curve_bps / 10_000
    swap = swap_vol_usd * swap_bps / 10_000
    return {"curve_bps": curve_bps, "swap_bps": swap_bps, "curve_usd": curve, "swap_usd": swap, "total_usd": curve + swap}


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "bossman-cohort/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Cohort:
    def __init__(self, out: Path, *, get: Callable[[str], Any] = _get, now: Callable[[], float] = time.time,
                 tables: dict | None = None):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.path = self.out / "cohort.jsonl"
        self.get, self.now = get, now
        self.tables = tables or load_tables()

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _write(self, rows: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(self.path)

    def poll(self, pages: int = 3) -> int:
        rows = self.rows()
        known = {r["token"] for r in rows}
        added = 0
        for page in range(1, pages + 1):
            for p in self.get(f"{GT}/new_pools?page={page}").get("data", []):
                if p["relationships"]["dex"]["data"]["id"] != "pump-fun":
                    continue
                token = p["relationships"]["base_token"]["data"]["id"].removeprefix("solana_")
                if token in known:
                    continue
                rows.append({"token": token, "name": p["attributes"].get("name"),
                             "created_at": p["attributes"].get("pool_created_at"),
                             "seen_at": datetime.fromtimestamp(self.now(), timezone.utc).isoformat(timespec="seconds"),
                             "measured": None})
                known.add(token)
                added += 1
        self._write(rows)
        return added

    def measure(self, sol_usd: float, *, max_n: int = 25) -> int:
        rows = self.rows()
        done = 0
        for r in rows:
            if r["measured"] or done >= max_n or not r.get("created_at"):
                continue
            created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).timestamp()
            age = self.now() - created
            if age < DAY_S:
                continue
            pools = self.get(f"{GT}/tokens/{r['token']}/pools").get("data", [])
            dex = lambda p: p["relationships"]["dex"]["data"]["id"]
            vol = lambda p: float(p["attributes"]["volume_usd"]["h24"] or 0)
            curve = sum(vol(p) for p in pools if dex(p) == "pump-fun")
            swap_pools = [p for p in pools if dex(p) == "pumpswap"]
            swap = sum(vol(p) for p in swap_pools)
            mcap = max((float(p["attributes"].get("fdv_usd") or 0) for p in (swap_pools or pools)), default=0.0)
            r["measured"] = {"age_h": round(age / 3600, 2), "curve_vol_usd": curve, "swap_vol_usd": swap,
                             "migrated": bool(swap_pools), "mcap_usd": mcap, "sol_usd": sol_usd,
                             **creator_fee_usd(curve, swap, mcap, sol_usd, self.tables)}
            done += 1
        self._write(rows)
        return done


def summarize(rows: list[dict]) -> dict:
    m = [r["measured"] for r in rows if r.get("measured")]
    if not m:
        return {"tokens_seen": len(rows), "measured": 0}
    fees = sorted(x["total_usd"] for x in m)
    share = lambda k: round(sum(f >= k for f in fees) / len(fees), 4)
    return {"tokens_seen": len(rows), "measured": len(m),
            "migrated_share": round(sum(x["migrated"] for x in m) / len(m), 4),
            "creator_fee_median_usd": round(statistics.median(fees), 4),
            "creator_fee_mean_usd": round(statistics.fmean(fees), 4),
            "creator_fee_p90_usd": round(fees[int(0.9 * (len(fees) - 1))], 4),
            "creator_fee_max_usd": round(fees[-1], 2),
            "share_ge_1usd": share(1), "share_ge_10usd": share(10), "share_ge_100usd": share(100),
            "total_creator_fee_usd": round(sum(fees), 2)}


def report_md(rows: list[dict], tables: dict) -> str:
    s = summarize(rows)
    lines = ["# Creator fee pump.fun — замер на реальной когорте", "",
             f"Ставки — снимок mainnet {tables['fetched_at']}: кривая {tables['bonding_curve']['tiers'][0]['creator']} bps "
             "создателю; PumpSwap 95 bps при капитализации 420–1470 SOL, дальше ниже (до 5 bps).",
             "Выборка: ВСЕ новые пулы pump.fun из GeckoTerminal new_pools, без отбора по успеху; замер через ≥24 ч.", ""]
    if not s.get("measured"):
        lines.append(f"Токенов в когорте: {s['tokens_seen']}. Замеров ещё нет — первым токенам нужно 24 ч.")
        return "\n".join(lines)
    lines += [f"- токенов в когорте: {s['tokens_seen']}, замерено: {s['measured']}, "
              f"мигрировало: {s['migrated_share'] * 100:.1f}%",
              f"- creator fee за первые сутки: медиана ${s['creator_fee_median_usd']}, среднее ${s['creator_fee_mean_usd']}, "
              f"90-й перцентиль ${s['creator_fee_p90_usd']}, максимум ${s['creator_fee_max_usd']}",
              f"- получили ≥$1: {s['share_ge_1usd'] * 100:.1f}%, ≥$10: {s['share_ge_10usd'] * 100:.1f}%, "
              f"≥$100: {s['share_ge_100usd'] * 100:.1f}%",
              "", "Объём включает самоторговлю создателей; она платит ≈1.25% комиссий ради 0.30% себе."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bossman.trading_learning.pump_creator_cohort")
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=600.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--report", action="store_true")
    a = p.parse_args(argv)
    c = Cohort(Path(a.out))
    if a.report:
        print(report_md(c.rows(), c.tables))
        return 0
    sol_mint = "So11111111111111111111111111111111111111112"
    while True:
        log = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            log["added"] = c.poll()
            sol = float(_get(f"https://lite-api.jup.ag/price/v3?ids={sol_mint}")[sol_mint]["usdPrice"])
            log["measured"] = c.measure(sol)
        except Exception as exc:
            log["error"] = f"{type(exc).__name__}: {exc}"[:300]
        with (c.out / "poll.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(log) + "\n")
        (c.out / "REPORT.md").write_text(report_md(c.rows(), c.tables), encoding="utf-8")
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
