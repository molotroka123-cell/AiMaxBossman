"""Does the exported data actually cover a reporting period?

``status == "closed"`` only says the period's end date has passed relative to the newest message. It says nothing
about the START: an export that begins on the 10th holds half of a 21st..20th month, and its turnover is a part of
the period, not the period. This module compares the data's date range with the period bounds (day granularity,
Europe/Prague dates; ``export_local`` is the fallback when an older result file has no Prague range).
"""
from __future__ import annotations

import datetime as dt

REASON_COVERED = "covered"
REASON_STARTS_LATE = "data_starts_after_period_start"
REASON_ENDS_EARLY = "data_ends_before_period_end"
REASON_UNKNOWN = "data_range_unknown"


def _date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def assess(period: dict, first_ts, last_ts, basis: str) -> dict:
    """``complete`` is True/False, or None when the data range or the period bounds are unknown."""
    lo, hi = _date(period.get("from_prague")), _date(period.get("to_prague"))
    first, last = _date(first_ts), _date(last_ts)
    out = {"basis": basis, "data_from": first.isoformat() if first else None,
           "data_to": last.isoformat() if last else None, "complete": None, "reason": REASON_UNKNOWN}
    if not (lo and hi and first and last):
        return out
    if first > lo:
        out.update(complete=False, reason=REASON_STARTS_LATE)
    elif last < hi:
        out.update(complete=False, reason=REASON_ENDS_EARLY)
    else:
        out.update(complete=True, reason=REASON_COVERED)
    return out


def for_period(result: dict, period: dict) -> dict:
    """The coverage recorded by the calculator, else recomputed from the result's source range."""
    recorded = period.get("coverage")
    if isinstance(recorded, dict) and "complete" in recorded:
        return recorded
    src = result.get("source", {})
    if src.get("first_ts_prague") and src.get("last_ts_prague"):
        return assess(period, src["first_ts_prague"], src["last_ts_prague"], "prague")
    return assess(period, src.get("first_ts_export_local"), src.get("last_ts_export_local"), "export_local")
