"""Time helpers: fixed-offset export stamps, Europe/Prague local time (no tzdata needed)."""
from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc


def _last_sunday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def prague_offset(utc_dt: dt.datetime) -> dt.timedelta:
    """EU DST rule: CEST (+2h) from last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC."""
    y = utc_dt.year
    start = dt.datetime.combine(_last_sunday(y, 3), dt.time(1, 0))
    end = dt.datetime.combine(_last_sunday(y, 10), dt.time(1, 0))
    naive = utc_dt.replace(tzinfo=None)
    return dt.timedelta(hours=2) if start <= naive < end else dt.timedelta(hours=1)


def to_prague(utc_dt: dt.datetime) -> dt.datetime:
    """Return naive Prague wall-clock time for an aware/naive-UTC datetime."""
    if utc_dt.tzinfo is not None:
        utc_dt = utc_dt.astimezone(UTC).replace(tzinfo=None)
    return utc_dt + prague_offset(utc_dt)


def prague_to_utc(local_naive: dt.datetime) -> dt.datetime:
    """Naive Prague wall-clock -> naive UTC. Ambiguous autumn hour resolves to CEST (first occurrence)."""
    for off in (dt.timedelta(hours=2), dt.timedelta(hours=1)):
        cand = local_naive - off
        if prague_offset(cand) == off:
            return cand
    return local_naive - dt.timedelta(hours=1)  # spring-forward gap: treat as CET


def business_period_close(local_date: dt.date) -> dt.date:
    """Business month runs 21st..20th inclusive and is named by its closing 20th."""
    if local_date.day <= 20:
        return dt.date(local_date.year, local_date.month, 20)
    y, m = (local_date.year + 1, 1) if local_date.month == 12 else (local_date.year, local_date.month + 1)
    return dt.date(y, m, 20)
