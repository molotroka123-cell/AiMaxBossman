"""Reconciliation of chat-derived deals against the owner's ledger workbook and a prior baseline JSON.

Matching is by multiset of (in leg, out leg) inside a +-1 day window; no fuzzy amount matching.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
from decimal import Decimal
from typing import Optional

from .calc import Deal

_LEG = re.compile(r"^\s*([\d\s.,]+?)\s*([A-Za-z]{3,5})\s*$")


def read_workbook_ledger(path: str, sheet: str = "Сделки") -> tuple[list, dict]:
    """Rows: (date, in_ccy, in_amt, out_ccy, out_amt, usdt_volume|None, source, note)."""
    import openpyxl  # optional dependency, only for this reconciliation
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows, bad, multi, multi_usdt = [], 0, 0, Decimal(0)
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or r[0] is None:
            continue
        try:
            date = r[0].date() if hasattr(r[0], "date") else dt.date.fromisoformat(str(r[0])[:10])
            a, b = _LEG.match(str(r[1])), _LEG.match(str(r[2]))
            if not a or not b:
                if "+" in str(r[1]) + str(r[2]):
                    multi += 1
                    multi_usdt += Decimal(str(r[3])) if r[3] not in (None, "") else Decimal(0)
                    continue
                raise ValueError("leg")
            num = lambda s: Decimal(s.replace(" ", "").replace(",", "."))  # noqa: E731
            vol = Decimal(str(r[3])) if r[3] not in (None, "") else None
            rows.append((date, a.group(2).upper(), num(a.group(1)), b.group(2).upper(), num(b.group(1)),
                         vol, r[5] if len(r) > 5 else None, r[6] if len(r) > 6 else None))
        except Exception:
            bad += 1
    return rows, {"rows": len(rows), "unparsed_rows": bad, "multi_leg_rows_not_matched": multi,
                  "multi_leg_rows_usdt_book_value": str(multi_usdt),
                  "date_min": min((r[0] for r in rows), default=None),
                  "date_max": max((r[0] for r in rows), default=None)}


def reconcile_ledger(deals: list[Deal], ledger_rows: list, window_days: int = 1,
                     statuses: tuple = ("counted",)) -> dict:
    lo = min((r[0] for r in ledger_rows), default=None)
    hi = max((r[0] for r in ledger_rows), default=None)
    if lo is None:
        return {"status": "no_ledger_rows"}
    chat = [d for d in deals if d.status in statuses and lo <= d.ts_prague.date() <= hi]
    pool = collections.defaultdict(list)
    for i, r in enumerate(ledger_rows):
        pool[(r[1], r[2], r[3], r[4])].append((r[0], i))
    matched, only_chat = 0, []
    used = set()
    for d in sorted(chat, key=lambda x: x.ts_utc):
        hit = None
        for date, i in pool.get(d.key(), []):
            if i not in used and abs((date - d.ts_prague.date()).days) <= window_days:
                hit = i
                break
        if hit is None:
            only_chat.append(d.ref)
        else:
            used.add(hit)
            matched += 1
    only_ledger = [i for i in range(len(ledger_rows)) if i not in used]
    ol_usdt = sum((ledger_rows[i][5] or Decimal(0) for i in only_ledger), Decimal(0))
    ol_unparsed_vol = sum(1 for i in only_ledger if ledger_rows[i][5] is None)
    return {"status": "compared", "window_days": window_days,
            "overlap_from": lo.isoformat(), "overlap_to": hi.isoformat(),
            "chat_deals_in_window": len(chat), "ledger_rows": len(ledger_rows),
            "matched": matched, "only_in_chat_refs": only_chat,
            "only_in_ledger_row_indexes": only_ledger,
            "only_in_ledger_usdt_book_value": str(ol_usdt),
            "only_in_ledger_rows_without_book_value": ol_unparsed_vol,
            "statuses_compared": list(statuses)}


def compare_baseline(result_periods: dict, baseline_path: str) -> dict:
    """Compare per-period counts/turnover with a prior calculator output (a hypothesis, not truth)."""
    base = json.load(open(baseline_path, encoding="utf-8")).get("periods", {})
    out = {}
    for k in sorted(set(base) | set(result_periods)):
        b, r = base.get(k), result_periods.get(k)
        row = {"baseline_deals": b and b.get("deals_counted"),
               "baseline_usdt": b and b.get("turnover_usdt_known"),
               "now_deals": r and r["deals_counted"], "now_usdt": r and r["gross_usdt_direct"]}
        if b and r:
            row["delta_deals"] = r["deals_counted"] - int(b["deals_counted"])
            row["delta_usdt"] = str(Decimal(r["gross_usdt_direct"]) - Decimal(b["turnover_usdt_known"]))
        out[k] = row
    return out
