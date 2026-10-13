"""Rolling-origin backtest: TimesFM 2.5 (local CPU) vs simple baselines on LOCAL series.

Reproduces docs/owner/TIMESFM_20261013_RU.md. Runs in the isolated TimesFM venv
(torch + timesfm + statsmodels), never in the Bossman runtime:

    venv-timesfm-20261013\\Scripts\\python.exe command-center\\scripts\\timesfm_backtest.py ^
        --model-dir C:\\Users\\asd\\Bossman\\models\\timesfm\\timesfm-2.5-200m-pytorch ^
        --bcc-db <copy of bcc.db> --market-db <copy of market-observations.sqlite> ^
        [--deals-csv <private deals.csv>] --out <dir outside git>

Data are read from local SQLite copies / CSV only; nothing is uploaded. The private
deals export is reduced to a daily volume series in memory, and only scale-free metrics
(MASE, sMAPE, coverage) are printed for it unless --print-private-mae is given.

Protocol per series and horizon h: N cut-offs t spread evenly over the last part of the
series; at each cut-off every method sees y[:t] only and forecasts y[t:t+h]. MAE and
sMAPE are averaged over all h steps and all cut-offs; MASE divides MAE by the in-sample
one-step naive MAE of y[:t] (m=1 scaling for every series, so values are comparable
across methods; MASE < 1 means better than a one-step naive on history). Coverage = share
of actual points inside TimesFM's [q0.1, q0.9] band (nominal 80%).
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- series loaders
def cpu_per_minute(db: Path) -> np.ndarray:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select ts, cpu_pct from system_metrics where cpu_pct is not null order by ts").fetchall()
    con.close()
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for ts, v in rows:
        buckets[datetime.fromisoformat(ts[:19]).replace(second=0)].append(float(v))
    return _fill_grid(buckets, timedelta(minutes=1), agg=np.mean)


def events_per_hour(db: Path) -> np.ndarray:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select ts from events order by ts").fetchall()
    con.close()
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for (ts,) in rows:
        buckets[datetime.fromisoformat(ts[:19]).replace(minute=0, second=0)].append(1.0)
    return _fill_grid(buckets, timedelta(hours=1), agg=len, empty=0.0)


def btc_ocr_prices(db: Path) -> np.ndarray:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select price from observations where symbol='BTC1!' and price is not null "
                       "order by captured_at_utc").fetchall()
    con.close()
    return np.array([float(r[0]) for r in rows])


def deals_per_day(path: Path) -> np.ndarray:
    buckets: dict[datetime, list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["status"] not in ("counted", "provisional") or not row["usdt_volume"]:
                continue
            buckets[datetime.fromisoformat(row["ts_utc"][:10])].append(float(row["usdt_volume"]))
    return _fill_grid(buckets, timedelta(days=1), agg=sum, empty=0.0)


def _fill_grid(buckets, step, *, agg, empty=None) -> np.ndarray:
    keys = sorted(buckets)
    out, t, last = [], keys[0], None
    while t <= keys[-1]:
        if t in buckets:
            last = float(agg(buckets[t]))
            out.append(last)
        else:
            out.append(empty if empty is not None else last)  # gap: zero for counts, carry-forward for levels
        t += step
    return np.array(out, dtype=float)


# ---------------------------------------------------------------- baselines
def f_last(y, h, **_):
    return np.repeat(y[-1], h)


def f_seasonal(y, h, m, **_):
    if not m or len(y) < m:
        return f_last(y, h)
    season = y[-m:]
    return np.array([season[i % m] for i in range(h)])


def f_mavg(y, h, w, **_):
    return np.repeat(np.mean(y[-w:]), h)


def f_ets(y, h, m, **_):
    import warnings

    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        seasonal = "add" if m and len(y) >= 2 * m + 2 else None
        fit = ExponentialSmoothing(y, trend=None, seasonal=seasonal, seasonal_periods=m if seasonal else None,
                                   initialization_method="estimated").fit()
        return np.asarray(fit.forecast(h), dtype=float)


# ---------------------------------------------------------------- metrics
def smape(a, f):
    d = np.abs(a) + np.abs(f)
    return float(np.mean(np.where(d == 0, 0.0, 2 * np.abs(a - f) / np.where(d == 0, 1, d))) * 100)


def run(series: dict, model, n_cut: int, max_context: int) -> list[dict]:
    results = []
    for name, spec in series.items():
        y, m, w = spec["y"], spec["season"], spec["mavg"]
        for h in spec["horizons"]:
            first = max(spec["min_context"], int(len(y) * 0.5))
            last = len(y) - h
            cuts = sorted(set(np.linspace(first, last, n_cut).astype(int).tolist()))
            truth = [y[t:t + h] for t in cuts]
            scale = []
            for t in cuts:
                s = float(np.mean(np.abs(np.diff(y[:t]))))
                scale.append(s if s > 0 else 1e-9)
            preds: dict[str, list[np.ndarray]] = {}
            for meth, fn in (("last_value", f_last), ("seasonal_naive", f_seasonal), ("moving_avg", f_mavg),
                             ("ets", f_ets)):
                if meth == "seasonal_naive" and not m:
                    continue
                preds[meth] = [fn(y[:t], h, m=m, w=w) for t in cuts]
            t0 = time.perf_counter()
            pf, qf = model.forecast(horizon=h, inputs=[y[max(0, t - max_context):t] for t in cuts])
            tf_s = time.perf_counter() - t0
            preds["timesfm_2.5"] = [pf[i, :h] for i in range(len(cuts))]
            lo = [qf[i, :h, 1] for i in range(len(cuts))]   # index 0 = mean, 1..9 = q0.1..q0.9
            hi = [qf[i, :h, 9] for i in range(len(cuts))]
            cover = float(np.mean(np.concatenate([(a >= l) & (a <= u) for a, l, u in zip(truth, lo, hi)])))
            row = {"series": name, "h": h, "cutoffs": len(cuts), "n": len(y), "timesfm_batch_s": round(tf_s, 2),
                   "timesfm_q10_q90_coverage": round(cover, 3), "methods": {}}
            for meth, fs in preds.items():
                mae = float(np.mean([np.mean(np.abs(a - f)) for a, f in zip(truth, fs)]))
                mase = float(np.mean([np.mean(np.abs(a - f)) / s for a, f, s in zip(truth, fs, scale)]))
                sm = float(np.mean([smape(a, f) for a, f in zip(truth, fs)]))
                row["methods"][meth] = {"mae": mae, "mase": round(mase, 3), "smape": round(sm, 2)}
            best = min(row["methods"], key=lambda k: row["methods"][k]["mase"])
            row["best_by_mase"] = best
            results.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "methods"}),
                  {k: v["mase"] for k, v in row["methods"].items()}, flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--bcc-db", type=Path)
    ap.add_argument("--market-db", type=Path)
    ap.add_argument("--deals-csv", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cutoffs", type=int, default=20)
    ap.add_argument("--max-context", type=int, default=1024)
    a = ap.parse_args(argv)

    series: dict[str, dict] = {}
    if a.bcc_db:
        series["bossman_cpu_pct_1min"] = {"y": cpu_per_minute(a.bcc_db), "season": 60, "mavg": 60,
                                          "horizons": [1, 15, 60], "min_context": 128, "private": False}
        series["bossman_events_per_hour"] = {"y": events_per_hour(a.bcc_db), "season": 24, "mavg": 24,
                                             "horizons": [1, 6, 24], "min_context": 72, "private": False}
    if a.market_db:
        series["btc1_ocr_price_ticks"] = {"y": btc_ocr_prices(a.market_db), "season": 0, "mavg": 10,
                                          "horizons": [1, 5, 15], "min_context": 100, "private": False}
    if a.deals_csv:
        series["private_deals_usdt_per_day"] = {"y": deals_per_day(a.deals_csv), "season": 7, "mavg": 7,
                                                "horizons": [1, 7, 30], "min_context": 90, "private": True}

    import timesfm
    t0 = time.perf_counter()
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(a.model_dir, torch_compile=False)
    model.compile(timesfm.ForecastConfig(max_context=a.max_context, max_horizon=256, normalize_inputs=True,
                                         use_continuous_quantile_head=True, fix_quantile_crossing=True))
    print("model ready in", round(time.perf_counter() - t0, 2), "s", flush=True)
    res = run(series, model, a.cutoffs, a.max_context)
    for r in res:
        if series[r["series"]]["private"]:
            for v in r["methods"].values():
                v.pop("mae")
    a.out.mkdir(parents=True, exist_ok=True)
    meta = {name: {"n": len(s["y"]), "season": s["season"], "private": s["private"]} for name, s in series.items()}
    (a.out / "timesfm_backtest.json").write_text(json.dumps({"series": meta, "results": res}, indent=1),
                                                 encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
