"""CLI: python -m exchange_volume.cli run|anonymise ... (run from the tools/ directory).

Local only: reads files, writes files. No network, no bot, no database writes."""
from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import random
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import calc, parse, reconcile
from .timeutil import to_prague


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _jsonable(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o))


def run(args) -> dict:
    t0 = time.perf_counter()
    notes, src_meta = [], {}
    cands = []
    if args.export:
        p = Path(args.export)
        msgs = parse.parse_export(p.read_text(encoding="utf-8"))
        cands = [parse.classify(m) for m in msgs]
        deals = calc.deals_from_chat(cands, include_provisional=True)
        local = {str(m.msg_id): m.ts_local for m in msgs}
        offs = collections.Counter(m.utc_offset_min for m in msgs)
        src_meta = {"type": "telegram_export_html", "sha256": _sha(p), "messages": len(msgs),
                    "export_utc_offsets_min": dict(offs),
                    "first_ts_export_local": min(m.ts_local for m in msgs).isoformat() if msgs else None,
                    "last_ts_export_local": max(m.ts_local for m in msgs).isoformat() if msgs else None,
                    "senders": len({m.sender for m in msgs}),
                    "media_without_text_messages": sum(1 for m in msgs if m.has_media and not m.text)}
        as_of = max((m.ts_utc for m in msgs), default=None)
    else:
        p = Path(args.db)
        deals = calc.deals_from_db(str(p), args.db_time_zone, not args.exclude_ads)
        local = {}
        src_meta = {"type": "bot_sqlite_orders", "sha256": _sha(p), "db_time_zone_assumed": args.db_time_zone,
                    "orders_completed": len(deals)}
        as_of = max((d.ts_utc for d in deals), default=None)
    as_of_date = to_prague(as_of).date() if as_of else None

    refs = set()
    if args.exclusions:
        refs = {ln.split("#")[0].strip() for ln in Path(args.exclusions).read_text(encoding="utf-8").splitlines()
                if ln.split("#")[0].strip()}
    rates = calc.RateBook.from_csv(args.rates, args.rate_max_age_h) if args.rates else None

    dup = calc.mark_duplicates(deals, args.dup_window_min)
    n_int = calc.apply_internal_exclusions(deals, refs)
    mirror = calc.mark_mirror_pairs(deals)
    outl = calc.flag_rate_outliers(deals)
    calc.price(deals, rates)
    periods = calc.aggregate(deals, args.period, as_of_date)

    result = {
        "schema": calc.SCHEMA, "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": src_meta, "as_of_prague_date": as_of_date and as_of_date.isoformat(),
        "assumptions": {
            "business_time_zone": "Europe/Prague (EU DST rule, built in); NOT verified against operator",
            "period": "business month 21st..20th inclusive, named by closing 20th" if args.period == "business"
                      else "calendar month",
            "volume_definition": "USDT turnover = amount of the USDT leg of each counted deal (one leg, "
                                 "not in+out). Deals without a USDT leg are unpriced unless an external "
                                 "rate book is supplied.",
            "duplicate_rule": f"identical in/out legs within {args.dup_window_min} min of an earlier counted deal",
            "internal_turnover": "only operator-listed refs are excluded; mirror pairs are flagged not removed",
            "rates": "no market rates in the chat; executed rate = sum_out/sum_in per pair; "
                     + ("external book: " + str(args.rates) if args.rates else "no external rate book"),
            "commission": "not present in chat messages; null unless every counted deal has fee in USDT",
        },
        "duplicates": dup, "rate_outliers": outl, "internal_excluded": n_int, "mirror_pair_rows_flagged": mirror,
        "periods": periods,
    }
    if cands:
        kinds = collections.Counter(c.kind for c in cands)
        flags = collections.Counter(f for c in cands for f in c.flags)
        prov = [c for c in cands if c.kind == "complex" and c.in_leg]
        result["message_classification"] = {
            "kinds": dict(kinds), "flags": dict(flags),
            "complex_refs": [c.msg_id for c in cands if c.kind == "complex"],
            "incomplete_refs": [c.msg_id for c in cands if c.kind == "incomplete"],
            "ambiguous_amount_refs": [c.msg_id for c in cands if c.kind == "ambiguous_amount"],
            "complex_provisional_usdt_leg_sum_not_in_totals": str(sum(
                ((c.in_leg.amount if c.in_leg.ccy == "USDT" else c.out_leg.amount)
                 for c in prov if "USDT" in (c.in_leg.ccy, c.out_leg.ccy)), Decimal(0))),
            "complex_provisional_with_usdt_leg": sum(
                1 for c in prov if "USDT" in (c.in_leg.ccy, c.out_leg.ccy)),
        }
        # Sensitivity: what the periods would be if the raw export wall-clock date were used.
        alt = []
        for d in deals:
            e = dataclasses.replace(d, flags=list(d.flags))
            e.ts_prague = local[d.ref]
            alt.append(e)
        a = calc.aggregate(alt, args.period, as_of_date)
        result["sensitivity_export_wallclock_date_instead_of_prague"] = {
            k: {"deals_counted": v["deals_counted"], "gross_usdt_direct": v["gross_usdt_direct"]}
            for k, v in a.items()}
        moved = sum(1 for d in deals if d.status == "counted"
                    and calc.business_period_close(d.ts_prague.date())
                    != calc.business_period_close(local[d.ref].date()))
        result["deals_that_change_business_period_with_zone_choice"] = moved
    if args.workbook:
        rows, meta = reconcile.read_workbook_ledger(args.workbook)
        result["workbook"] = {"sha256": _sha(Path(args.workbook)), **meta,
                              "reconcile": reconcile.reconcile_ledger(deals, rows),
                              "reconcile_with_provisional_complex": reconcile.reconcile_ledger(
                                  deals, rows, statuses=("counted", "provisional"))}
    if args.baseline:
        result["baseline_comparison"] = reconcile.compare_baseline(periods, args.baseline)
    result["elapsed_s"] = round(time.perf_counter() - t0, 3)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "volume_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8")
    with open(out / "deals.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "ref", "ts_utc", "ts_prague", "business_period_close", "in_ccy", "in_amt",
                    "out_ccy", "out_amt", "network", "status", "dup_of", "usdt_volume", "volume_basis",
                    "rate_used", "rate_source", "rate_ts_utc", "flags"])
        for d in deals:
            w.writerow([d.source, d.ref, d.ts_utc.isoformat(sep=" "), d.ts_prague.isoformat(sep=" "),
                        calc.business_period_close(d.ts_prague.date()).isoformat(), d.in_ccy, d.in_amt,
                        d.out_ccy, d.out_amt, d.network or "", d.status, d.dup_of or "",
                        d.usdt_volume if d.usdt_volume is not None else "", d.volume_basis or "",
                        d.rate_used or "", d.rate_source or "", d.rate_ts_utc or "", ";".join(d.flags)])
    return result


def anonymise(args) -> None:
    """Write an anonymised local copy of an export: sender removed, ids kept, free text replaced,
    deal amounts multiplied by a seeded factor and dates shifted by a whole number of days."""
    rng = random.Random(args.seed)
    factor = Decimal(rng.randint(70, 140)) / Decimal(100)
    shift = dt.timedelta(days=0)  # day shift would move business periods; keep dates, scale amounts
    src = Path(args.export).read_text(encoding="utf-8")
    msgs = parse.parse_export(src)
    parts = ['<html><body><div class="history">']
    for m in msgs:
        c = parse.classify(m)
        if c.kind in ("deal",) and c.in_leg and c.out_leg:
            i, o = c.in_leg, c.out_leg
            text = (f"Принял {(i.amount * factor).quantize(Decimal('1'))} {i.ccy} "
                    f"Выдал {(o.amount * factor).quantize(Decimal('1'))} {o.ccy}")
        elif c.kind in ("complex", "incomplete", "debt", "expense"):
            text = f"[{c.kind}]"
        else:
            text = ""
        ts = (m.ts_local + shift).strftime("%d.%m.%Y %H:%M:%S")
        sign = "-" if m.utc_offset_min < 0 else "+"
        hh, mm = divmod(abs(m.utc_offset_min), 60)
        parts.append(
            f'<div class="message default clearfix" id="message{m.msg_id}"><div class="body">'
            f'<div class="pull_right date details" title="{ts} UTC{sign}{hh:02d}:{mm:02d}">x</div>'
            f'<div class="from_name">anon</div><div class="text">{text}</div></div></div>')
    parts.append("</div></body></html>")
    Path(args.out).write_text("\n".join(parts), encoding="utf-8")
    print(json.dumps({"messages": len(msgs), "amount_factor": str(factor), "out": args.out}))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="exchange_volume")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", help="Telegram Desktop messages.html")
    g.add_argument("--db", help="bot sqlite DB (opened read-only)")
    r.add_argument("--db-time-zone", default="Europe/Prague", choices=["Europe/Prague", "UTC"])
    r.add_argument("--exclude-ads", action="store_true")
    r.add_argument("--period", default="business", choices=["business", "calendar"])
    r.add_argument("--dup-window-min", type=int, default=calc.DUP_WINDOW_MIN_DEFAULT)
    r.add_argument("--exclusions", help="text file: one ref (message/order id) per line, # comments")
    r.add_argument("--rates", help="CSV ts_utc,base,quote,rate,source")
    r.add_argument("--rate-max-age-h", type=int, default=calc.RATE_MAX_AGE_H_DEFAULT)
    r.add_argument("--workbook", help="ledger workbook .xlsx (sheet 'Сделки')")
    r.add_argument("--baseline", help="earlier calculator JSON to compare against")
    r.add_argument("--out", required=True)
    a = sub.add_parser("anonymise")
    a.add_argument("--export", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--seed", type=int, default=20261011)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        res = run(args)
        print(json.dumps({"periods": len(res["periods"]), "out": args.out, "elapsed_s": res["elapsed_s"]}))
    else:
        anonymise(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
