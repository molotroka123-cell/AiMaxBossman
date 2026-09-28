"""Independent verification of timestamped teacher claims against public market data.

Pure, deterministic, no network. A teacher claim extracted from a video starts as
``UNVERIFIED``. It is scored only against *independent* exchange data (never the
teacher's own later frames) at fixed horizons after the claim. Anything that
cannot be checked from the available independent data is ``UNKNOWN`` — values are
never guessed.

Market inputs
-------------
* ``klines``: 1-minute candles, dicts with ``t`` (open, epoch s), ``o h l c v``
  and ``tb`` (taker-buy base volume). CVD proxy = sum(2*tb - v).
* ``oi``: open-interest samples, dicts with ``t`` (epoch s) and ``oi``.
* Liquidations: no public historical feed is wired -> always ``UNKNOWN``.

Statuses per evaluation: VERIFIED, REFUTED, INCONCLUSIVE (inside the noise band),
UNKNOWN (not checkable). ``t0`` is the evaluation of STATE claims at claim time;
FORECAST claims are evaluated at each horizon in ``HORIZONS_MIN``.
"""
from __future__ import annotations

import bisect
import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

METRICS = ("PRICE", "CVD", "OI", "LIQUIDATIONS", "dPOC", "dVAH", "dVAL", "dOpen")
LEVEL_METRICS = ("dPOC", "dVAH", "dVAL", "dOpen")
KINDS = ("STATE", "FORECAST", "HISTORY", "LONG_TERM")
VERIFIABLE_KINDS = ("STATE", "FORECAST")
DIRECTIONS = ("UP", "DOWN", "ABOVE", "BELOW", "AT", "TOUCH")
HORIZONS_MIN = (1, 5, 15, 30, 60, 240)
STATUSES = ("VERIFIED", "REFUTED", "INCONCLUSIVE", "UNKNOWN")

PRICE_TOL = 0.0035      # 0.35 %: venue basis (Coinbase/CME vs Binance perp) + ASR rounding
TOUCH_TOL = 0.0005      # 0.05 % touch band for targets
RETURN_BAND = 0.0005    # |return| below 0.05 % = INCONCLUSIVE
CVD_BAND = 0.01         # |CVD delta| below 1 % of window volume = INCONCLUSIVE
OI_BAND = 0.0005        # |OI change| below 0.05 % = INCONCLUSIVE
STATE_LOOKBACK_S = {"PRICE": 15 * 60, "CVD": 15 * 60, "OI": 30 * 60, "TOUCH": 60 * 60}
PROFILE_BIN = 10.0      # USD bin for the developing volume profile
VALUE_AREA = 0.70


@dataclass
class Claim:
    claim_id: str
    video_id: str
    t_video_s: float
    quote: str
    metric: str
    kind: str
    direction: Optional[str] = None
    value: Optional[float] = None
    instrument: str = "UNSTATED"
    t_utc: Optional[float] = None
    transcript_source: str = ""
    status: str = "UNVERIFIED"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------- alignment

def align_from_chat(pairs: Iterable[tuple[float, float]], *, max_mad_s: float = 30.0) -> dict[str, Any]:
    """Estimate epoch of video offset 0 from (video_offset_s, wall_epoch_s) chat pairs.

    Only messages posted *during* the replay (offset > 0) are meaningful. Returns
    status UNKNOWN when too few pairs or the spread is too large.
    """
    deltas = sorted(w - o for o, w in pairs if o > 0)
    if len(deltas) < 3:
        return {"status": "UNKNOWN", "reason": "fewer than 3 in-stream chat timestamps", "n": len(deltas)}
    med = statistics.median(deltas)
    mad = statistics.median(abs(d - med) for d in deltas)
    kept = [d for d in deltas if abs(d - med) <= max(5.0, 5 * mad)]
    med = statistics.median(kept)
    mad = statistics.median(abs(d - med) for d in kept)
    status = "ALIGNED" if mad <= max_mad_s else "UNKNOWN"
    return {"status": status, "epoch_at_offset0": round(med, 3), "mad_s": round(mad, 3),
            "n": len(deltas), "n_kept": len(kept)}


# ----------------------------------------------------------------- numbers

_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|K|thousand|grand)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 1000.0
        out.append(v)
    return out


def value_supported_by_quote(value: float, quote: str) -> bool:
    """The claimed number must literally appear in the quote (x1 or x1000 shorthand)."""
    for n in numbers_in(quote):
        if math.isclose(n, value, rel_tol=1e-9) or math.isclose(n * 1000.0, value, rel_tol=1e-9):
            return True
    return False


def normalize_price_value(value: Optional[float], ref_price: float) -> tuple[Optional[float], Optional[str]]:
    """Map spoken shorthand to BTC price scale using only order of magnitude.

    ``72.5`` with ref 72,600 -> 72,500 ("x1000"). Values that do not land within
    +/-20 % of the reference in either scale return None (not a BTC price level).
    """
    if value is None or ref_price <= 0:
        return None, None
    if 0.8 * ref_price <= value <= 1.2 * ref_price:
        return value, None
    if 0.8 * ref_price <= value * 1000.0 <= 1.2 * ref_price:
        return value * 1000.0, "x1000"
    return None, "out_of_btc_scale"


# ----------------------------------------------------------------- market view

class Market:
    def __init__(self, klines: Sequence[dict[str, Any]], oi: Sequence[dict[str, Any]] = ()):  # noqa: D401
        self.k = sorted(klines, key=lambda r: r["t"])
        self.kt = [r["t"] for r in self.k]
        self.oi = sorted(oi, key=lambda r: r["t"])
        self.oit = [r["t"] for r in self.oi]

    # a 1m candle opened at t is complete at t+60
    def _idx_completed_before(self, t: float) -> int:
        return bisect.bisect_right(self.kt, t - 60) - 1

    def covers(self, start: float, end: float) -> bool:
        return bool(self.k) and self.kt[0] <= start and self.kt[-1] + 60 >= end

    def price(self, t: float) -> Optional[float]:
        """Close of the last candle completed at or before t (no future data)."""
        i = self._idx_completed_before(t)
        if i < 0 or t - (self.kt[i] + 60) > 120:
            return None
        return float(self.k[i]["c"])

    def candles(self, start: float, end: float) -> list[dict[str, Any]]:
        lo = bisect.bisect_left(self.kt, start)
        hi = bisect.bisect_right(self.kt, end - 60)
        return self.k[lo:hi]

    def cvd_delta(self, start: float, end: float) -> Optional[tuple[float, float]]:
        rows = self.candles(start, end)
        if not rows or len(rows) < max(1, int((end - start) // 60) - 2):
            return None
        delta = sum(2 * float(r["tb"]) - float(r["v"]) for r in rows)
        vol = sum(float(r["v"]) for r in rows)
        return delta, vol

    def oi_at(self, t: float) -> Optional[float]:
        i = bisect.bisect_right(self.oit, t) - 1
        if i < 0 or t - self.oit[i] > 600:
            return None
        return float(self.oi[i]["oi"])

    def high_low(self, start: float, end: float) -> Optional[tuple[float, float]]:
        rows = self.candles(start, end)
        if not rows:
            return None
        return max(float(r["h"]) for r in rows), min(float(r["l"]) for r in rows)

    def developing_levels(self, t: float) -> dict[str, Optional[float]]:
        """Developing daily (UTC session) dOpen/dPOC/dVAH/dVAL from completed 1m candles."""
        session = math.floor(t / 86400) * 86400
        rows = [r for r in self.candles(session, t)]
        if not rows or rows[0]["t"] != session:
            return {"dOpen": None, "dPOC": None, "dVAH": None, "dVAL": None}
        bins: dict[int, float] = {}
        for r in rows:
            lo, hi, vol = float(r["l"]), float(r["h"]), float(r["v"])
            b0, b1 = int(lo // PROFILE_BIN), int(hi // PROFILE_BIN)
            share = vol / (b1 - b0 + 1)
            for b in range(b0, b1 + 1):
                bins[b] = bins.get(b, 0.0) + share
        poc = max(sorted(bins), key=lambda b: bins[b])
        total = sum(bins.values())
        lo_b = hi_b = poc
        acc = bins[poc]
        while acc < VALUE_AREA * total:
            up = bins.get(hi_b + 1, 0.0) if hi_b + 1 <= max(bins) else -1
            dn = bins.get(lo_b - 1, 0.0) if lo_b - 1 >= min(bins) else -1
            if up < 0 and dn < 0:
                break
            if up >= dn:
                hi_b += 1
                acc += max(up, 0.0)
            else:
                lo_b -= 1
                acc += max(dn, 0.0)
        centre = lambda b: (b + 0.5) * PROFILE_BIN  # noqa: E731
        return {"dOpen": float(rows[0]["o"]), "dPOC": centre(poc),
                "dVAH": centre(hi_b), "dVAL": centre(lo_b)}


# ----------------------------------------------------------------- verification

def _sign_status(delta: float, band: float, want_up: bool) -> str:
    if abs(delta) < band:
        return "INCONCLUSIVE"
    return "VERIFIED" if (delta > 0) == want_up else "REFUTED"


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "reason": reason}


def _eval_state(c: Claim, m: Market, t0: float) -> dict[str, Any]:
    px = m.price(t0)
    if px is None:
        return _unknown("no market price at claim time")
    d = c.direction
    if c.metric == "PRICE":
        if d in ("AT", "ABOVE", "BELOW"):
            v, norm = normalize_price_value(c.value, px)
            if v is None:
                return _unknown("no checkable price value in quote")
            if d == "AT":
                err = abs(v - px) / px
                return {"status": "VERIFIED" if err <= PRICE_TOL else "REFUTED", "claimed": v,
                        "observed": px, "rel_err": round(err, 5), "normalized": norm}
            ok = px > v if d == "ABOVE" else px < v
            return {"status": "VERIFIED" if ok else "REFUTED", "claimed": v, "observed": px, "normalized": norm}
        if d == "TOUCH":
            v, norm = normalize_price_value(c.value, px)
            hl = m.high_low(t0 - STATE_LOOKBACK_S["TOUCH"], t0)
            if v is None or hl is None:
                return _unknown("no checkable touched value")
            hi, lo = hl
            touched = lo <= v * (1 + TOUCH_TOL) and hi >= v * (1 - TOUCH_TOL)
            return {"status": "VERIFIED" if touched else "REFUTED", "claimed": v, "high": hi, "low": lo,
                    "normalized": norm}
        if d in ("UP", "DOWN"):
            p0 = m.price(t0 - STATE_LOOKBACK_S["PRICE"])
            if p0 is None:
                return _unknown("no lookback price")
            ret = (px - p0) / p0
            return {"status": _sign_status(ret, RETURN_BAND, d == "UP"), "lookback_ret": round(ret, 5)}
        return _unknown("price state without checkable direction")
    if c.metric == "CVD":
        if d not in ("UP", "DOWN"):
            return _unknown("CVD state without direction")
        cv = m.cvd_delta(t0 - STATE_LOOKBACK_S["CVD"], t0)
        if cv is None:
            return _unknown("no CVD window")
        delta, vol = cv
        return {"status": _sign_status(delta, CVD_BAND * vol, d == "UP"), "cvd_delta": round(delta, 3)}
    if c.metric == "OI":
        if d not in ("UP", "DOWN"):
            return _unknown("OI state without direction")
        a, b = m.oi_at(t0 - STATE_LOOKBACK_S["OI"]), m.oi_at(t0)
        if a is None or b is None:
            return _unknown("no OI history window")
        rel = (b - a) / a
        return {"status": _sign_status(rel, OI_BAND, d == "UP"), "oi_rel_change": round(rel, 5)}
    if c.metric in LEVEL_METRICS:
        levels = m.developing_levels(t0)
        lvl = levels.get(c.metric)
        if lvl is None:
            return _unknown("level not computable (session data missing)")
        if d == "AT" or (d is None and c.value is not None):
            v, norm = normalize_price_value(c.value, px)
            if v is None:
                return _unknown("no checkable level value in quote")
            err = abs(v - lvl) / lvl
            return {"status": "VERIFIED" if err <= PRICE_TOL else "REFUTED", "claimed": v,
                    "computed_level": lvl, "rel_err": round(err, 5), "normalized": norm}
        if d in ("ABOVE", "BELOW"):
            ok = px > lvl if d == "ABOVE" else px < lvl
            return {"status": "VERIFIED" if ok else "REFUTED", "price": px, "computed_level": lvl}
        return _unknown("level state without checkable relation")
    return _unknown(f"unsupported metric {c.metric}")


def _eval_forecast(c: Claim, m: Market, t0: float, h_min: int) -> dict[str, Any]:
    t1 = t0 + h_min * 60
    if not m.covers(t0, t1):
        return _unknown("market data does not cover horizon")
    px0, px1 = m.price(t0), m.price(t1)
    if px0 is None or px1 is None:
        return _unknown("no market price at claim/horizon")
    d = c.direction
    if c.metric == "PRICE" or c.metric in LEVEL_METRICS:
        target: Optional[float] = None
        norm = None
        if c.value is not None:
            target, norm = normalize_price_value(c.value, px0)
            if target is None:
                return _unknown("claimed value not on the BTC price scale")
        elif c.metric in LEVEL_METRICS:
            target = m.developing_levels(t0).get(c.metric)
        if d == "TOUCH" or (d in ("UP", "DOWN") and target is not None):
            if target is None:
                return _unknown("no checkable target")
            hl = m.high_low(t0 + 60, t1)
            if hl is None:
                return _unknown("no candles in horizon")
            hi, lo = hl
            touched = lo <= target * (1 + TOUCH_TOL) and hi >= target * (1 - TOUCH_TOL)
            return {"status": "VERIFIED" if touched else "REFUTED", "target": target,
                    "high": hi, "low": lo, "normalized": norm}
        if d in ("ABOVE", "BELOW"):
            if target is None:
                return _unknown("no checkable reference level")
            ok = px1 > target if d == "ABOVE" else px1 < target
            return {"status": "VERIFIED" if ok else "REFUTED", "reference": target, "price_at_h": px1}
        if d in ("UP", "DOWN"):
            ret = (px1 - px0) / px0
            return {"status": _sign_status(ret, RETURN_BAND, d == "UP"), "ret": round(ret, 5)}
        return _unknown("forecast without checkable direction")
    if c.metric == "CVD":
        if d not in ("UP", "DOWN"):
            return _unknown("CVD forecast without direction")
        cv = m.cvd_delta(t0, t1)
        if cv is None:
            return _unknown("no CVD window")
        delta, vol = cv
        return {"status": _sign_status(delta, CVD_BAND * vol, d == "UP"), "cvd_delta": round(delta, 3)}
    if c.metric == "OI":
        if d not in ("UP", "DOWN"):
            return _unknown("OI forecast without direction")
        a, b = m.oi_at(t0), m.oi_at(t1)
        if a is None or b is None:
            return _unknown("no OI history window")
        rel = (b - a) / a
        return {"status": _sign_status(rel, OI_BAND, d == "UP"), "oi_rel_change": round(rel, 5)}
    return _unknown(f"unsupported metric {c.metric}")


def verify_claim(c: Claim, m: Market, horizons: Sequence[int] = HORIZONS_MIN) -> dict[str, Any]:
    """Return {"t0": eval} for STATE claims or {"<h>m": eval, ...} for FORECAST claims."""
    keys = [f"{h}m" for h in horizons] if c.kind == "FORECAST" else ["t0"]
    blocked: Optional[str] = None
    if c.metric == "LIQUIDATIONS":
        blocked = "no independent public historical liquidation source"
    elif c.metric not in METRICS or c.kind not in KINDS:
        blocked = "invalid claim schema"
    elif c.kind not in VERIFIABLE_KINDS:
        blocked = ("past-period statement; not a claim about the claim time" if c.kind == "HISTORY"
                   else "horizon longer than the 4 h verification window")
    elif c.t_utc is None:
        blocked = "claim time not aligned to UTC"
    elif c.instrument not in ("BTC", "UNSTATED"):
        blocked = f"instrument {c.instrument} not covered by the BTC verifier"
    if blocked:
        return {k: _unknown(blocked) for k in keys}
    if c.kind == "STATE":
        return {"t0": _eval_state(c, m, c.t_utc)}
    return {f"{h}m": _eval_forecast(c, m, c.t_utc, h) for h in horizons}


def overall_status(evals: dict[str, dict[str, Any]]) -> str:
    """Claim-level status: UNKNOWN if nothing checkable; else majority of decided evaluations."""
    st = [e["status"] for e in evals.values()]
    decided = [s for s in st if s in ("VERIFIED", "REFUTED")]
    if not decided:
        return "UNKNOWN" if all(s == "UNKNOWN" for s in st) else "INCONCLUSIVE"
    v = decided.count("VERIFIED")
    r = len(decided) - v
    return "VERIFIED" if v > r else "REFUTED" if r > v else "INCONCLUSIVE"


def summarize(claims: Sequence[Claim], results: Sequence[dict[str, dict[str, Any]]],
              horizons: Sequence[int] = HORIZONS_MIN) -> dict[str, Any]:
    keys = ["t0"] + [f"{h}m" for h in horizons]
    per = {k: {s: 0 for s in STATUSES} for k in keys}
    by_metric: dict[str, int] = {}
    overall = {s: 0 for s in STATUSES}
    for c, ev in zip(claims, results):
        by_metric[c.metric] = by_metric.get(c.metric, 0) + 1
        for k, e in ev.items():
            per[k][e["status"]] += 1
        overall[overall_status(ev)] += 1
    return {"claims": len(claims), "by_metric": by_metric, "claim_status": overall,
            "unknown_claims": overall["UNKNOWN"], "per_horizon": per}
