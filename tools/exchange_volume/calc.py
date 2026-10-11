"""Volume calculator: normalisation, duplicate/internal-turnover marking, period aggregation.

Everything is Decimal. A value that cannot be derived from the data is None and is
reported as such; it is never defaulted to zero, one, or an estimate.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from .parse import Candidate
from .timeutil import business_period_close, prague_to_utc, to_prague

SCHEMA = "swapme.exchange-volume/2"
DUP_WINDOW_MIN_DEFAULT = 10
RATE_MAX_AGE_H_DEFAULT = 24


@dataclass
class Deal:
    source: str                  # chat | db
    ref: str                     # message id or order id
    ts_utc: dt.datetime
    in_ccy: str
    in_amt: Decimal
    out_ccy: str
    out_amt: Decimal
    network: Optional[str] = None
    fee: Optional[Decimal] = None
    fee_ccy: Optional[str] = None
    flags: list = field(default_factory=list)
    status: str = "counted"      # counted | excluded_duplicate | excluded_internal | provisional
    dup_of: Optional[str] = None
    ts_prague: Optional[dt.datetime] = None
    usdt_volume: Optional[Decimal] = None
    volume_basis: Optional[str] = None   # direct_usdt_leg | converted_external_rate
    rate_used: Optional[Decimal] = None
    rate_source: Optional[str] = None
    rate_ts_utc: Optional[dt.datetime] = None

    def __post_init__(self):
        self.ts_prague = to_prague(self.ts_utc)

    @property
    def pair(self) -> str:
        return f"{self.in_ccy}>{self.out_ccy}"

    def key(self):
        return (self.in_ccy, self.in_amt, self.out_ccy, self.out_amt)


# ---------------------------------------------------------------- adapters
def deals_from_chat(cands: Iterable[Candidate], include_provisional: bool = False) -> list[Deal]:
    """`include_provisional` adds first-two-pairs readings of complex messages with status
    'provisional'; they never enter exact totals (see aggregate)."""
    out = []
    for c in cands:
        if c.kind == "complex" and include_provisional and c.in_leg and c.out_leg:
            net = c.in_leg.network or c.out_leg.network
            out.append(Deal("chat", str(c.msg_id), c.ts_utc, c.in_leg.ccy, c.in_leg.amount,
                            c.out_leg.ccy, c.out_leg.amount, net, flags=list(c.flags), status="provisional"))
            continue
        if c.kind != "deal":
            continue
        net = c.in_leg.network or c.out_leg.network
        out.append(Deal("chat", str(c.msg_id), c.ts_utc, c.in_leg.ccy, c.in_leg.amount,
                        c.out_leg.ccy, c.out_leg.amount, net, flags=list(c.flags)))
    return sorted(out, key=lambda d: (d.ts_utc, int(d.ref)))


def deals_from_db(path: str, db_time_zone: str = "Europe/Prague", include_ads: bool = True) -> list[Deal]:
    """Read the bot's `orders` table (audited repo schema) strictly read-only.

    `created_at` carries no zone (the column default is UTC but the bot writes local
    wall-clock), so the zone is an explicit assumption: 'Europe/Prague' or 'UTC'."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, state, from_ccy, from_amt, to_ccy, to_amt, fee, fee_ccy, created_at, "
            "client_type FROM orders WHERE state='completed' ORDER BY id").fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        ctype = r["client_type"] or "organic"
        if not include_ads and ctype == "ads":
            continue
        ts = dt.datetime.strptime((r["created_at"] or "")[:19], "%Y-%m-%d %H:%M:%S")
        ts_utc = ts if db_time_zone == "UTC" else prague_to_utc(ts)
        fee = Decimal(str(r["fee"])) if r["fee"] else None
        d = Deal("db", str(r["id"]), ts_utc, r["from_ccy"], Decimal(str(r["from_amt"])),
                 r["to_ccy"], Decimal(str(r["to_amt"])), None, fee, r["fee_ccy"] if fee else None)
        d.flags.append(f"db_time_zone_assumed={db_time_zone}")
        if ctype == "ads":
            d.flags.append("client_type_ads")
        out.append(d)
    return out


# ---------------------------------------------------------------- rates
@dataclass
class RateBook:
    """Optional external rates: CSV columns ts_utc(ISO),base,quote,rate,source."""
    rows: dict = field(default_factory=dict)   # (base, quote) -> sorted [(ts, rate, source)]
    max_age_h: int = RATE_MAX_AGE_H_DEFAULT

    @classmethod
    def from_csv(cls, path: str, max_age_h: int = RATE_MAX_AGE_H_DEFAULT) -> "RateBook":
        rb = cls(max_age_h=max_age_h)
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                ts = dt.datetime.fromisoformat(r["ts_utc"].replace("Z", "")).replace(tzinfo=None)
                rb.rows.setdefault((r["base"].upper(), r["quote"].upper()), []).append(
                    (ts, Decimal(r["rate"]), r.get("source") or "unspecified"))
        for v in rb.rows.values():
            v.sort(key=lambda x: x[0])
        return rb

    def to_usdt(self, ccy: str, ts: dt.datetime):
        """(rate ccy->USDT, source, rate_ts) or None if no rate at/before ts within max age."""
        for (b, q, invert) in ((ccy, "USDT", False), ("USDT", ccy, True)):
            lst = self.rows.get((b, q))
            if not lst:
                continue
            i = bisect.bisect_right([x[0] for x in lst], ts) - 1
            if i < 0:
                continue
            rts, rate, src = lst[i]
            if ts - rts > dt.timedelta(hours=self.max_age_h) or rate <= 0:
                continue
            return (Decimal(1) / rate if invert else rate), src, rts
        return None


# ---------------------------------------------------------------- marking
def mark_duplicates(deals: list[Deal], window_min: int = DUP_WINDOW_MIN_DEFAULT) -> dict:
    """A deal is a duplicate iff an earlier COUNTED deal has identical (in, out) legs and the
    gap is <= window_min minutes (a repost within minutes, PARSER_SPEC section 6).
    Same-day identical legs beyond the window are NOT removed (two genuine equal deals are
    plausible); they are only counted as candidates."""
    last: dict = {}
    cand_same_day = 0
    day_seen: set = set()
    for d in sorted((x for x in deals if x.status == "counted"), key=lambda x: (x.ts_utc, x.ref)):
        k = d.key()
        prev = last.get(k)
        if prev is not None and d.ts_utc - prev.ts_utc <= dt.timedelta(minutes=window_min):
            d.status, d.dup_of = "excluded_duplicate", prev.ref
            continue
        dk = (k, d.ts_prague.date())
        if dk in day_seen:
            cand_same_day += 1
            d.flags.append("same_day_identical_beyond_window")
        day_seen.add(dk)
        last[k] = d
    return {"window_min": window_min,
            "excluded": sum(1 for d in deals if d.status == "excluded_duplicate"),
            "same_day_identical_candidates_kept": cand_same_day}


def mark_mirror_pairs(deals: list[Deal]) -> int:
    """Flag (never remove) X>Y followed the same Prague day by Y>X with swapped identical amounts.
    PARSER_SPEC section 6: these are normally TWO real deals. Internal turnover is only removed
    through an explicit operator exclusion list."""
    n = 0
    open_pairs: dict = {}
    for d in sorted((x for x in deals if x.status == "counted"), key=lambda x: x.ts_utc):
        rk = (d.out_ccy, d.out_amt, d.in_ccy, d.in_amt, d.ts_prague.date())
        if rk in open_pairs:
            open_pairs[rk].flags.append("mirror_pair_candidate")
            d.flags.append("mirror_pair_candidate")
            n += 2
            del open_pairs[rk]
        else:
            open_pairs[(d.in_ccy, d.in_amt, d.out_ccy, d.out_amt, d.ts_prague.date())] = d
    return n


def flag_rate_outliers(deals: list[Deal], tol: Decimal = Decimal("0.25")) -> dict:
    """Plausibility check, NOT a correction: for each unordered pair the executed rate
    (quote per 1 base, base = the currency sorted first) is compared with the dataset median;
    deals further than `tol` away are flagged 'implied_rate_outlier' and stay in totals."""
    import statistics
    by_pair: dict = {}
    for d in deals:
        if d.status != "counted" or d.in_amt <= 0 or d.out_amt <= 0:
            continue
        base, quote = sorted((d.in_ccy, d.out_ccy))
        r = d.out_amt / d.in_amt if d.in_ccy == base else d.in_amt / d.out_amt
        by_pair.setdefault((base, quote), []).append((r, d))
    flagged = 0
    for (b, q), lst in by_pair.items():
        if len(lst) < 5:
            continue
        med = statistics.median(r for r, _ in lst)
        for r, d in lst:
            if abs(r / med - 1) > tol:
                d.flags.append("implied_rate_outlier")
                flagged += 1
    return {"tolerance": str(tol), "flagged": flagged}


def apply_internal_exclusions(deals: list[Deal], refs: set) -> int:
    n = 0
    for d in deals:
        if d.ref in refs and d.status == "counted":
            d.status = "excluded_internal"
            n += 1
    return n


def price(deals: list[Deal], rates: Optional[RateBook] = None) -> None:
    for d in deals:
        if d.status not in ("counted", "provisional"):
            continue
        for ccy, amt in ((d.in_ccy, d.in_amt), (d.out_ccy, d.out_amt)):
            if ccy == "USDT":
                d.usdt_volume, d.volume_basis = amt, "direct_usdt_leg"
                break
        else:
            if rates is None:
                continue
            r = rates.to_usdt(d.in_ccy, d.ts_utc)
            if r:
                d.rate_used, d.rate_source, d.rate_ts_utc = r
                d.usdt_volume = d.in_amt * d.rate_used
                d.volume_basis = "converted_external_rate"


# ---------------------------------------------------------------- aggregation
def _fee_usdt(d: Deal):
    if d.fee is None:
        return None
    if d.fee_ccy == "USDT":
        return d.fee
    return None  # no rate model for fees: unknown rather than guessed


def aggregate(deals: list[Deal], period: str = "business", as_of: Optional[dt.date] = None) -> dict:
    def pkey(d: Deal):
        day = d.ts_prague.date()
        return business_period_close(day) if period == "business" else day.replace(day=1)

    periods: dict = {}
    for d in deals:
        p = periods.setdefault(pkey(d), {"deals": [], "excl_dup": 0, "excl_int": 0, "prov": []})
        if d.status == "provisional":
            p["prov"].append(d)
        elif d.status == "counted":
            p["deals"].append(d)
        elif d.status == "excluded_duplicate":
            p["excl_dup"] += 1
        else:
            p["excl_int"] += 1
    out = {}
    for key in sorted(periods):
        p = periods[key]
        ds = p["deals"]
        direct = sum((d.usdt_volume for d in ds if d.volume_basis == "direct_usdt_leg"), Decimal(0))
        conv_ds = [d for d in ds if d.volume_basis == "converted_external_rate"]
        conv = sum((d.usdt_volume for d in conv_ds), Decimal(0))
        unpriced = [d for d in ds if d.usdt_volume is None]
        usdt_in = sum((d.in_amt for d in ds if d.in_ccy == "USDT"), Decimal(0))
        usdt_out = sum((d.out_amt for d in ds if d.out_ccy == "USDT"), Decimal(0))
        per_ccy: dict = {}
        per_pair: dict = {}
        for d in ds:
            per_ccy.setdefault(d.in_ccy, {"in": Decimal(0), "out": Decimal(0)})["in"] += d.in_amt
            per_ccy.setdefault(d.out_ccy, {"in": Decimal(0), "out": Decimal(0)})["out"] += d.out_amt
            pp = per_pair.setdefault(d.pair, {"deals": 0, "sum_in": Decimal(0), "sum_out": Decimal(0)})
            pp["deals"] += 1
            pp["sum_in"] += d.in_amt
            pp["sum_out"] += d.out_amt
        for pp in per_pair.values():  # executed (not market) rate: out per 1 in
            pp["vwap_out_per_in"] = pp["sum_out"] / pp["sum_in"] if pp["sum_in"] else None
        nets: dict = {}
        for d in ds:
            if d.network and d.usdt_volume is not None:
                nets[d.network] = nets.get(d.network, Decimal(0)) + d.usdt_volume
        fee_known = [d for d in ds if _fee_usdt(d) is not None]
        commission = (sum((_fee_usdt(d) for d in fee_known), Decimal(0))
                      if ds and len(fee_known) == len(ds) else None)
        net = direct - commission if commission is not None else None
        last = max((d.ts_prague.date() for d in ds), default=None)
        if period == "business":
            lo = (dt.date(key.year - 1, 12, 21) if key.month == 1
                  else dt.date(key.year, key.month - 1, 21))
            hi = key
        else:
            lo = key
            hi = (key.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        status = "closed" if as_of and as_of > hi else "open_partial_as_of_export"
        out[key.isoformat()] = {
            "from_prague": lo.isoformat(), "to_prague": hi.isoformat(), "status": status,
            "deals_counted": len(ds), "excluded_duplicates": p["excl_dup"],
            "excluded_internal": p["excl_int"],
            "gross_usdt_direct": str(direct),
            "gross_usdt_converted_external_rate": str(conv) if conv_ds else None,
            "usdt_received_from_clients": str(usdt_in), "usdt_paid_to_clients": str(usdt_out),
            "unpriced_deals": len(unpriced),
            "unpriced_pairs": sorted({d.pair for d in unpriced}),
            "commission_usdt": str(commission) if commission is not None else None,
            "net_usdt_direct": str(net) if net is not None else None,
            "fee_known_deals": len(fee_known),
            "per_currency_native": {k: {a: str(b) for a, b in v.items()} for k, v in sorted(per_ccy.items())},
            "per_pair": {k: {a: (str(b) if isinstance(b, Decimal) else b) for a, b in v.items()}
                         for k, v in sorted(per_pair.items())},
            "usdt_by_network": {k: str(v) for k, v in sorted(nets.items())},
            "usdt_deals_network_unspecified": sum(
                1 for d in ds if "USDT" in (d.in_ccy, d.out_ccy) and not d.network),
            "rate_outlier_deals": sum(1 for d in ds if "implied_rate_outlier" in d.flags),
            "rate_outlier_usdt_leg": str(sum((d.usdt_volume for d in ds if "implied_rate_outlier" in d.flags
                                              and d.usdt_volume is not None), Decimal(0))),
            "provisional_complex_messages": len(p["prov"]),
            "provisional_complex_usdt_leg_not_in_totals": str(sum(
                (d.usdt_volume for d in p["prov"] if d.volume_basis == "direct_usdt_leg"), Decimal(0))),
            "last_deal_date_prague": last.isoformat() if last else None,
        }
    return out
