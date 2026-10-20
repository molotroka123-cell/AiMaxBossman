"""Publication times are clock times, not text: «9:30» comes before «10:00».

Before the fix sorted(["9:30", "10:00", "15:00", "19:30"]) gave 10:00, 15:00, 19:30, 9:30; with per_day=3
the owner's morning 9:30 slot silently disappeared and slots came out of chronological order.
"""
from datetime import datetime, timezone

from shortsfactory import schedule

NOW = datetime(2026, 10, 17, 5, 0, tzinfo=timezone.utc)      # 07:00 Prague


def _local_hhmm(iso):
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Prague")).strftime("%H:%M")


def test_single_digit_hour_is_earliest_and_kept_within_per_day():
    s = schedule.next_slots({"per_day": 3, "times": ["10:00", "19:30", "9:30", "15:00"], "tz": "Europe/Prague"}, [], 3, now=NOW)
    assert [_local_hhmm(x) for x in s] == ["09:30", "10:00", "15:00"]


def test_slots_are_chronological_and_duplicates_collapse():
    s = schedule.next_slots({"per_day": 3, "times": ["18:00", "8:05", "8:05"], "tz": "Europe/Prague"}, [], 4, now=NOW)
    assert s == sorted(s) and len(set(s)) == 4
    assert [_local_hhmm(x) for x in s] == ["08:05", "18:00", "08:05", "18:00"]
