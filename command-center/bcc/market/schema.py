"""`bossman.market-observation/1` — build, parse and validate one observation.

A missing metric is better than a fake one: every numeric field is either read
from THIS frame and confirmed, or null. Nothing here can carry a previous value
forward; `validate` refuses a record that claims VERIFIED without evidence.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

SCHEMA = "bossman.market-observation/1"

VERIFIED = "VERIFIED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
UNREADABLE = "UNREADABLE"
STALE_FRAME = "STALE_FRAME"
AMBIGUOUS_SYMBOL = "AMBIGUOUS_SYMBOL"
AMBIGUOUS_UNIT = "AMBIGUOUS_UNIT"
STREAM_OFFLINE = "STREAM_OFFLINE"
PLAYER_ERROR = "PLAYER_ERROR"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
STATUSES = (VERIFIED, LOW_CONFIDENCE, UNREADABLE, STALE_FRAME, AMBIGUOUS_SYMBOL, AMBIGUOUS_UNIT,
            STREAM_OFFLINE, PLAYER_ERROR, LOGIN_REQUIRED)
STREAM_STATES = ("LIVE", "OFFLINE", "PLAYER_ERROR", "LOGIN_REQUIRED")
METRIC_KEYS = ("price", "oi_value", "oi_unit", "cvd_value", "cvd_unit", "cvd_type")
UNITS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

# "74.44B", "19.57 B", "-6.05K", "−712.49K" (unicode minus), "1.2M"
_ABBREV = re.compile(r"^\s*([+\-−]?)\s*(\d{1,4}(?:[.,]\d{1,4})?)\s*([KMBT])\s*$")
# "83,865", "83865.5", "83 865"
_PRICE = re.compile(r"^\s*(\d{1,3}(?:[, ]\d{3})+|\d{3,7})(?:\.(\d{1,2}))?\s*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_abbrev(text: str) -> tuple[float, str] | None:
    """'74.44B' -> (74.44, 'B'). None when the text is not exactly one number+unit."""
    m = _ABBREV.match(text or "")
    if not m:
        return None
    sign = -1.0 if m.group(1) in ("-", "−") else 1.0
    return sign * float(m.group(2).replace(",", ".")), m.group(3)


_THOUSANDS = re.compile(r"^\s*(\d{1,3})[.,](\d{3})\s*$")


def parse_price(text: str) -> float | None:
    """'83,865' -> 83865.0. The axis prints a thousands separator; the reader
    sometimes returns it as '.' ('83.865'). Exactly three digits after one
    separator is read as thousands — an axis price here is never < 1000 with
    three decimals. Anything else must match the strict pattern."""
    t = _THOUSANDS.match(text or "")
    if t:
        return float(t.group(1) + t.group(2))
    m = _PRICE.match(text or "")
    if not m:
        return None
    whole = re.sub(r"[, ]", "", m.group(1))
    return float(f"{whole}.{m.group(2)}" if m.group(2) else whole)


def classify_badge(text: str) -> tuple[str, str] | None:
    """Badge text -> (metric, value text). The label proves WHICH metric the number
    is — a number without its label is never assigned to OI or CVD."""
    # the chart's dotted cursor line can cross a badge and read as "|" or ":"
    t = " ".join((text or "").replace("|", " ").replace(":", " ").split())
    m = re.match(r"^(?:CVD)\s+(.+)$", t, re.I)
    if m:
        return "cvd", m.group(1)
    m = re.match(r"^(?:Open\s+Interest|OI)\s+(.+)$", t, re.I)
    if m:
        return "oi", m.group(1)
    return None


def new_observation(*, channel: str, requested_url: str, resolved_url: str | None,
                    stream_state: str, captured_at: str | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "captured_at_utc": captured_at or utc_now(),
        "source": {"platform": "twitch", "channel": channel, "requested_url": requested_url,
                   "resolved_url": resolved_url, "stream_state": stream_state},
        "instrument": {"symbol": None, "exchange": None, "market_type": None, "timeframe": None},
        "metrics": {k: None for k in METRIC_KEYS},
        "quality": {"status": UNREADABLE, "confidence": 0.0, "fresh_frame": False, "manual_review": False,
                    "per_metric": {}},
        "evidence": {"frame_sha256": None, "crop_sha256": {}, "bbox": {}, "ocr_raw": {}, "extractor": None,
                     "video_time": None, "stream_clock": None},
    }


def validate(rec: dict[str, Any]) -> list[str]:
    """Errors (empty list = valid). Enforces the no-fabrication contract."""
    errs: list[str] = []
    if rec.get("schema") != SCHEMA:
        errs.append("schema")
    for key in ("captured_at_utc", "source", "instrument", "metrics", "quality", "evidence"):
        if key not in rec:
            errs.append(f"missing:{key}")
    if errs:
        return errs
    q, m, ev, src = rec["quality"], rec["metrics"], rec["evidence"], rec["source"]
    if q.get("status") not in STATUSES:
        errs.append("status")
    if src.get("stream_state") not in STREAM_STATES:
        errs.append("stream_state")
    conf = q.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        errs.append("confidence")
    if set(m) != set(METRIC_KEYS):
        errs.append("metric_keys")
    for v, u in (("oi_value", "oi_unit"), ("cvd_value", "cvd_unit")):
        if (m.get(v) is None) != (m.get(u) is None):
            errs.append(f"{v}_without_unit")
        if m.get(u) is not None and m.get(u) not in UNITS:
            errs.append(f"{u}")
    has_number = any(m.get(k) is not None for k in ("price", "oi_value", "cvd_value"))
    if has_number:
        if not q.get("fresh_frame"):
            errs.append("number_without_fresh_frame")        # never carry a value forward
        if not ev.get("frame_sha256"):
            errs.append("number_without_frame_evidence")
        if src.get("stream_state") != "LIVE":
            errs.append("number_while_not_live")
        if q.get("status") in (STALE_FRAME, UNREADABLE, STREAM_OFFLINE, PLAYER_ERROR, LOGIN_REQUIRED):
            errs.append("number_with_non_reading_status")
    for key in ("oi_value", "cvd_value", "price"):
        if m.get(key) is not None:
            metric = key.split("_")[0]
            if metric not in (ev.get("ocr_raw") or {}):
                errs.append(f"{key}_without_ocr_raw")
            if metric not in (ev.get("crop_sha256") or {}):
                errs.append(f"{key}_without_crop")
    if q.get("status") == VERIFIED and not (m.get("oi_value") is not None or m.get("cvd_value") is not None):
        errs.append("verified_without_metric")
    return errs


def dedupe_key(rec: dict[str, Any]) -> str:
    """source + frame hash + instrument + metric identity (spec §8)."""
    src, ev, m = rec["source"], rec["evidence"], rec["metrics"]
    return "|".join(str(x) for x in (src.get("channel"), ev.get("frame_sha256") or rec["captured_at_utc"],
                                      rec["instrument"].get("symbol"), m.get("oi_value"), m.get("oi_unit"),
                                      m.get("cvd_value"), m.get("cvd_unit")))
