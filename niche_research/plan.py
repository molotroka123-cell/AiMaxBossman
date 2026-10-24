"""The 4-week niche plan, day/week mapping and scoring criteria.

Run day N (1..28) = the N-th 24 h window after the recorded start timestamp; run week W = days 7W-6..7W.
Any 7 consecutive days contain exactly five weekdays and two weekend days, so each run week gets one niche per
calendar weekday (Mon–Fri, in plan order) and its weekend days do the weekly comparison (max 2 hypotheses).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from niche_research import RUN_DAYS

WEEKS: dict[int, dict] = {
    1: {"theme": "Локальные услуги для дома",
        "niches": ["уборка", "малярные работы", "переезды", "мелкий ремонт", "монтажные услуги"]},
    2: {"theme": "Авто и вело",
        "niches": ["детейлинг", "шиномонтаж", "автомойки", "ремонт велосипедов", "небольшие кузовные мастерские"]},
    3: {"theme": "Небольшие интернет-магазины",
        "niches": ["товары для дома", "одежда и аксессуары", "хобби", "handmade",
                   "аксессуары для животных (без медицинских обещаний)"]},
    4: {"theme": "Локальные заведения и обучение",
        "niches": ["кафе", "языковые преподаватели", "танцевальные студии", "площадки для мероприятий",
                   "локальные экскурсии"]},
}

# Scoring 1..5 (5 = best for us). «competition»: 5 = weak competition.
CRITERIA: dict[str, str] = {
    "problem_visibility": "видимость проблемы",
    "buyer_accessibility": "доступность покупателя",
    "competition": "конкуренция (5 = слабая)",
    "output_quality": "возможность сделать качественный результат",
    "expected_margin": "ожидаемая маржа",
}

NICHE_PIPELINE = ["researcher", "competitor", "packager", "draft", "critic"]
MAX_WEEKLY_HYPOTHESES = 2


@dataclass(frozen=True)
class DaySlot:
    day: int
    week: int
    date: str          # local calendar date of that run day
    weekday: int       # 0 = Monday
    kind: str          # "niche" | "weekly"
    niche: str | None  # niche name for kind == "niche"


def day_index(start_ts: float, now_ts: float) -> int:
    """1-based run day; 0 before start; > RUN_DAYS after the deadline."""
    if now_ts < start_ts:
        return 0
    return int((now_ts - start_ts) // 86400) + 1


def deadline_ts(start_ts: float) -> float:
    return start_ts + RUN_DAYS * 86400


def _local_date(ts: float, tz_offset_hours: float | None):
    tz = timezone(timedelta(hours=tz_offset_hours)) if tz_offset_hours is not None else None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (dt.astimezone(tz) if tz else dt.astimezone()).date()


def day_slot(start_ts: float, day: int, tz_offset_hours: float | None = None) -> DaySlot:
    if not 1 <= day <= RUN_DAYS:
        raise ValueError(f"day {day} outside 1..{RUN_DAYS}")
    week = (day - 1) // 7 + 1
    date = _local_date(start_ts + (day - 1) * 86400, tz_offset_hours)
    weekday = date.weekday()
    if weekday >= 5:
        return DaySlot(day, week, date.isoformat(), weekday, "weekly", None)
    first = (week - 1) * 7 + 1
    ordinal = sum(1 for d in range(first, day)
                  if _local_date(start_ts + (d - 1) * 86400, tz_offset_hours).weekday() < 5)
    return DaySlot(day, week, date.isoformat(), weekday, "niche", WEEKS[week]["niches"][ordinal])


def total_score(scores: dict) -> int:
    total = 0
    for key in CRITERIA:
        try:
            total += max(1, min(5, int(scores.get(key, 0) or 0)))
        except (TypeError, ValueError):
            total += 1
    return total
