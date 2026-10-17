"""Publication slots: N per day at fixed local times (default 3/day). Only owner-approved items get a slot."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT = {"per_day": 3, "times": ["10:00", "15:00", "19:30"], "tz": "Europe/Prague"}


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - tzdata may be missing on Windows
        return timezone(timedelta(hours=2))


def next_slots(cfg: dict, taken: list[str], n: int, now: datetime | None = None) -> list[str]:
    """Next n free slots (ISO 8601 UTC 'Z'), strictly in the future (+15 min), never two items in one slot."""
    cfg = {**DEFAULT, **(cfg or {})}
    tz = _tz(cfg["tz"])
    times = sorted(cfg["times"])[: max(1, int(cfg["per_day"]))]
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    taken_set = set(taken)
    out, day = [], now.date()
    while len(out) < n and day <= now.date() + timedelta(days=60):
        for hhmm in times:
            h, m = map(int, hhmm.split(":"))
            dt = datetime.combine(day, time(h, m), tzinfo=tz)
            iso = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if dt > now + timedelta(minutes=15) and iso not in taken_set:
                out.append(iso)
                taken_set.add(iso)
                if len(out) >= n:
                    break
        day += timedelta(days=1)
    return out
