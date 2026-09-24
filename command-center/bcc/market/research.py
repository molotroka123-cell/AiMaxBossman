"""Research transform over VERIFIED observations — status DATA_COLLECTION.

    python -m bcc.market.research [--root ...] [--asof 2026-09-24T12:00:00Z]

Builds a per-minute table (last VERIFIED value in each minute) with:
  * features at t: ΔOI, ΔCVD, price return over 1/5/15/30 min back;
  * labels: forward price return +1/+5/+15/+30 min, ΔOI/ΔCVD forward, realized
    volatility — a label exists ONLY when t+h <= asof AND a verified row exists
    at t+h (±30 s tolerance). No label is ever filled from the future of asof;
  * hypothesis buckets: price↑/↓ × OI↑/↓, price/CVD divergence, OI expansion;
  * split BY UTC DAY (train < valid < holdout, chronological) — never random
    rows, so one market episode cannot leak across the split.

This is a dataset, not a trading model: no signal, no order, no advice.
The summary always says DATA_COLLECTION (never TRADING_MODEL_READY).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import default_root
from .schema import UNITS

HORIZONS_MIN = (1, 5, 15, 30)
TOLERANCE_S = 30
STATUS = "DATA_COLLECTION"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_verified(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT captured_at_utc, symbol, price, oi_value, oi_unit, cvd_value, cvd_unit "
                           "FROM observations WHERE status='VERIFIED' ORDER BY captured_at_utc").fetchall()
    finally:
        con.close()
    out = []
    for at, sym, price, oi, oiu, cvd, cvdu in rows:
        out.append({"t": _ts(at), "symbol": sym,
                    "price": float(price) if price not in (None, "") else None,
                    "oi": float(oi) * UNITS[oiu] if oi not in (None, "") else None,
                    "cvd": float(cvd) * UNITS[cvdu] if cvd not in (None, "") else None})
    return out


def minute_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last verified value per metric per minute; the bar time is the minute start."""
    bars: dict[datetime, dict[str, Any]] = {}
    for r in rows:
        key = r["t"].replace(second=0, microsecond=0)
        bar = bars.setdefault(key, {"t": key, "symbol": r["symbol"], "price": None, "oi": None, "cvd": None,
                                    "obs_t": {}})
        for k in ("price", "oi", "cvd"):
            if r[k] is not None:
                bar[k] = r[k]
                bar["obs_t"][k] = r["t"]
    return [bars[k] for k in sorted(bars)]


def _at(index: dict[datetime, dict], t: datetime, key: str) -> float | None:
    """Value of `key` at minute t, only if that bar's own observation is within tolerance."""
    bar = index.get(t)
    if not bar or bar.get(key) is None:
        return None
    obs = bar["obs_t"].get(key)
    return bar[key] if obs is not None and abs((obs - t).total_seconds()) <= 60 + TOLERANCE_S else None


def _chg(a: float | None, b: float | None, rel: bool = True) -> float | None:
    if a is None or b is None:
        return None
    if rel:
        return (b - a) / a if a else None
    return b - a


def _sign(x: float | None, eps: float = 0.0) -> int | None:
    if x is None:
        return None
    return 1 if x > eps else (-1 if x < -eps else 0)


def build_table(rows: list[dict[str, Any]], asof: datetime) -> list[dict[str, Any]]:
    bars = minute_bars([r for r in rows if r["t"] <= asof])
    index = {b["t"]: b for b in bars}
    out = []
    for b in bars:
        t = b["t"]
        rec: dict[str, Any] = {"t": t.isoformat().replace("+00:00", "Z"), "day": t.date().isoformat(),
                               "symbol": b["symbol"], "price": b["price"], "oi": b["oi"], "cvd": b["cvd"]}
        for h in HORIZONS_MIN:
            back = t - timedelta(minutes=h)
            rec[f"ret_back_{h}m"] = _chg(_at(index, back, "price"), b["price"])
            rec[f"doi_back_{h}m"] = _chg(_at(index, back, "oi"), b["oi"])
            rec[f"dcvd_back_{h}m"] = _chg(_at(index, back, "cvd"), b["cvd"], rel=False)
            fwd = t + timedelta(minutes=h)
            ready = fwd + timedelta(seconds=TOLERANCE_S) <= asof
            rec[f"label_ready_{h}m"] = ready
            rec[f"ret_fwd_{h}m"] = _chg(b["price"], _at(index, fwd, "price")) if ready else None
            rec[f"doi_fwd_{h}m"] = _chg(b["oi"], _at(index, fwd, "oi")) if ready else None
            rec[f"dcvd_fwd_{h}m"] = _chg(b["cvd"], _at(index, fwd, "cvd"), rel=False) if ready else None
        # realized volatility of 1-min returns over the next 15 min (label, same readiness rule)
        rets = []
        if t + timedelta(minutes=15, seconds=TOLERANCE_S) <= asof:
            for k in range(1, 16):
                r_ = _chg(_at(index, t + timedelta(minutes=k - 1), "price"), _at(index, t + timedelta(minutes=k), "price"))
                if r_ is not None:
                    rets.append(r_)
        rec["vol_fwd_15m"] = (math.sqrt(sum(x * x for x in rets) / len(rets)) if len(rets) >= 5 else None)
        p5, o5, c5 = rec["ret_back_5m"], rec["doi_back_5m"], rec["dcvd_back_5m"]
        sp, so, sc = _sign(p5), _sign(o5), _sign(c5)
        rec["bucket_price_oi"] = (None if sp is None or so is None or 0 in (sp, so) else
                                  f"price{'↑' if sp > 0 else '↓'}_oi{'↑' if so > 0 else '↓'}")
        rec["div_price_cvd"] = (None if sp is None or sc is None or 0 in (sp, sc) else
                                ("price↑_cvd↓" if sp > 0 > sc else "price↓_cvd↑" if sp < 0 < sc else "aligned"))
        out.append(rec)
    # OI expansion: |ΔOI 5m| above 2 sigma of the table so far (only past rows -> no look-ahead)
    hist: list[float] = []
    for rec in out:
        d = rec["doi_back_5m"]
        flag = None
        if d is not None:
            if len(hist) >= 10:
                mu = sum(hist) / len(hist)
                sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist))
                flag = bool(sd and abs(d - mu) > 2 * sd)
            hist.append(d)
        rec["oi_expansion"] = flag
    return out


def split_by_day(table: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({r["day"] for r in table})
    if len(days) < 3:
        for r in table:
            r["split"] = "unsplit"
        return {"days": days, "status": "INSUFFICIENT_DAYS",
                "note": "time/day split needs >= 3 days; rows are marked unsplit, not randomly split"}
    n = len(days)
    train = days[: max(1, int(n * 0.6))]
    valid = days[len(train): len(train) + max(1, int(n * 0.2))]
    hold = days[len(train) + len(valid):]
    assign = {**{d: "train" for d in train}, **{d: "valid" for d in valid}, **{d: "holdout" for d in hold}}
    for r in table:
        r["split"] = assign[r["day"]]
    return {"days": days, "train": train, "valid": valid, "holdout": hold, "status": "SPLIT_BY_DAY"}


def summarize(table: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"rows": len(table), "status": STATUS, "trading_model_ready": False}
    for h in HORIZONS_MIN:
        out[f"labels_ready_{h}m"] = sum(1 for r in table if r[f"ret_fwd_{h}m"] is not None)
    buckets: dict[str, list[float]] = {}
    for r in table:
        if r["bucket_price_oi"] and r["ret_fwd_5m"] is not None:
            buckets.setdefault(r["bucket_price_oi"], []).append(r["ret_fwd_5m"])
        if r["div_price_cvd"] and r["ret_fwd_5m"] is not None:
            buckets.setdefault("cvd:" + r["div_price_cvd"], []).append(r["ret_fwd_5m"])
    out["hypotheses_fwd5m"] = {k: {"n": len(v), "mean": sum(v) / len(v)} for k, v in sorted(buckets.items())}
    out["caveat"] = "descriptive only; n is tiny until many days are collected; no trading use"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bcc.market.research")
    ap.add_argument("--root", default=None)
    ap.add_argument("--asof", default=None, help="UTC time labels may use (default: now)")
    ns = ap.parse_args(argv)
    root = Path(ns.root) if ns.root else default_root()
    asof = _ts(ns.asof) if ns.asof else datetime.now(timezone.utc)
    table = build_table(load_verified(root / "market-observations.sqlite"), asof)
    split = split_by_day(table)
    out_dir = root / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = asof.strftime("%Y%m%dT%H%M%SZ")
    if table:
        with open(out_dir / f"table-{stamp}.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(table)
    summary = {"asof": asof.isoformat(), "split": split, **summarize(table)}
    (out_dir / f"summary-{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
