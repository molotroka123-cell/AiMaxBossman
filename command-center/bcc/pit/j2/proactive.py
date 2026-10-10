"""Jeff 2.0 module ``proactive`` (order 80): the proactive companion.

What it does
------------
* reminders from plain Russian ("напомни завтра в 10 позвонить маме"), a labelled-set-tested time parser;
* follow-ups on open threads ("завтра у меня собеседование" -> "как прошло?") and on long tasks
  (``bcc/pit/tasks.py`` states WAITING_INPUT / WAITING_APPROVAL / UNKNOWN_OUTCOME, optionally DONE / FAILED);
* an optional daily digest built locally from the participant's own tasks and reminders (no model call);
* quiet hours, per-participant rate limits, and a one-message opt-out ("стоп напоминания").

Rules it keeps
--------------
* Consent gated. A reminder the participant asked for is delivered unless they opted out. Everything Jeff
  would start on its own (follow-ups, digest, thread questions) needs an explicit "включи напоминания".
* Never spams: quiet hours (default 22-08 local), at most ``max_per_day`` unsolicited messages (default 3) and
  a minimum gap between them (default 1 h). Reminders the participant asked for ignore quiet hours.
* Survives restart: the schedule lives in ``<personalities>/<person_key>/proactive/schedule.json`` (atomic,
  fsynced writes, the same helper the passport uses). Nothing is kept in memory only.
* Idempotent: every item carries an idempotency key. ``sending`` is persisted BEFORE the sender is called and
  ``sent`` right after, mirroring ``tasks.py``. After a crash an item found in ``sending`` is never blindly
  repeated: the sender's optional ``already_sent(key)`` verifier decides, otherwise the item becomes
  ``unknown`` and is not resent. A sender exception is treated as ambiguous for the same reason.
* Isolated: a hook or tick only ever touches one ``person_key`` namespace at a time.

Sender contract: ``async sender(person_key, text, idempotency_key)`` returns ``True``/"sent" (delivered),
``False``/"retry" (certainly NOT delivered, retried with backoff) or "undeliverable" (this surface cannot push,
e.g. the Jeff window; the reminder is shown at the participant's next turn instead).

Status keys: ``name, version, pending, participants, sent, deferred, failed, running, last_tick``.
Privacy: status and logs carry counts only, never reminder text. Reminder text is stored only in the
participant's own namespace; secrets are redacted before storing.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import inspect
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..identity import derive_person_key, scoped_person_dir, validate_person_key
from ..secret_filter import redact_secrets
from ..vault import _atomic_json
from .contract import Advice, BaseModule, TurnContext

log = logging.getLogger("bcc.pit.j2.proactive")

SCHEMA = "jeff.proactive/1"
TICK_SECONDS = 30.0
SEND_TIMEOUT_S = 20.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 60.0
MAX_PENDING = 50
MAX_PER_DAY_LIMIT = 10
REMINDER_DAILY_CAP = 50
LATE_AFTER_S = 15 * 60
EXPIRE_REMINDER_S = 48 * 3600
EXPIRE_FOLLOWUP_S = 48 * 3600
FOLLOWUP_DELAY_S = 3 * 3600
FOLLOWUP_SECOND_S = 24 * 3600
UNKNOWN_DELAY_S = 30 * 60
DIGEST_WINDOW_H = 3
KEEP_TERMINAL_S = 14 * 86400
MAX_KEYS = 2000
DEFAULT_TZ_MIN = 180                       # Moscow; the participant can change it ("мой часовой пояс UTC+5")
OPT_OUT_HINT = "Чтобы я не писал первым: «стоп напоминания»."

DEFAULT_PREFS: dict[str, Any] = {
    "proactive": False, "opted_out": False, "digest": False, "digest_hour": 9, "notify_finished": False,
    "quiet_start": 22, "quiet_end": 8, "max_per_day": 3, "min_gap_s": 3600, "tz_offset_min": None,
}

_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_MONTH_NAMES = {v: k for k, v in _MONTHS.items()}
_WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "четверг": 3, "пятницу": 4, "субботу": 5,
             "воскресенье": 6}
_WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_DEFAULT_HOUR = 9
_PART_OF_DAY = {"утром": 9, "днем": 13, "после обеда": 15, "вечером": 19, "ночью": 23}

Sender = Callable[[str, str, str], Awaitable[Any]]
TasksProvider = Callable[[str], list]


# ---------------------------------------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------------------------------------
def _tz(minutes: int) -> timezone:
    return timezone(timedelta(minutes=int(minutes)))


def parse_utc(value: str) -> float:
    """``2026-09-29T09:00:00Z`` (the task store's stamp) -> epoch seconds."""
    return float(calendar.timegm(time.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")))


def _local(ts: float, tz_min: int) -> datetime:
    return datetime.fromtimestamp(ts, _tz(tz_min))


def _at(day: date, hour: int, minute: int, tz_min: int) -> float:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_tz(tz_min)).timestamp()


def in_quiet_hours(ts: float, tz_min: int, start: int, end: int) -> bool:
    if start == end:
        return False
    hour = _local(ts, tz_min).hour
    return start <= hour < end if start < end else hour >= start or hour < end


def quiet_end(ts: float, tz_min: int, start: int, end: int) -> float:
    """The next moment at or after ``ts`` when quiet hours end (``ts`` itself outside quiet hours)."""
    if not in_quiet_hours(ts, tz_min, start, end):
        return ts
    now = _local(ts, tz_min)
    candidate = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _next_midnight(ts: float, tz_min: int) -> float:
    now = _local(ts, tz_min)
    return _at(now.date() + timedelta(days=1), 0, 0, tz_min)


def _hhmm(ts: float, tz_min: int) -> str:
    return _local(ts, tz_min).strftime("%H:%M")


def format_when(due: float, now: float, tz_min: int) -> str:
    """Human Russian phrase for ``due`` relative to ``now`` ("завтра в 10:00")."""
    d, n = _local(due, tz_min), _local(now, tz_min)
    delta = (d.date() - n.date()).days
    clock = d.strftime("%H:%M")
    if delta == 0:
        return f"сегодня в {clock}"
    if delta == 1:
        return f"завтра в {clock}"
    if delta == 2:
        return f"послезавтра в {clock}"
    label = f"{d.day} {_MONTH_NAMES[d.month]}"
    if 2 < delta < 7:
        label += f" ({_WEEKDAY_SHORT[d.weekday()]})"
    return f"{label} в {clock}"


# ---------------------------------------------------------------------------------------------------------
# Russian time-phrase parser
# ---------------------------------------------------------------------------------------------------------
_TRIGGER = re.compile(r"^\W*(?:пожалуйста\W+)?(?:напомни(?:ть)?|напоминай|поставь\s+напоминание|"
                      r"создай\s+напоминание)\b", re.IGNORECASE)
_PREFIX = re.compile(r"^\W*(?:пожалуйста\W+)?(?:напомни(?:ть)?|напоминай|поставь\s+напоминание|"
                     r"создай\s+напоминание)\b\W*(?:пожалуйста\b\W*)?(?:мне\b)?", re.IGNORECASE)
_UNIT = r"(мин\w*|час\w*|ч\b|дн\w+|день|недел\w*|нед\b|месяц\w*)"
_REL_N = re.compile(rf"\bчерез\s+(\d{{1,3}})\s*{_UNIT}")
_REL_HALF = re.compile(r"\bчерез\s+(?:полчаса|пол\s+часа)\b")
_REL_ONEHALF = re.compile(r"\bчерез\s+полтора\s+час\w*")
_REL_FEW = re.compile(r"\bчерез\s+(пару|несколько)\s+(минут\w*|часов|часа|дней|дня|недель|недели)\b")
_REL_ONE = re.compile(r"\bчерез\s+(минуту|час|день|неделю|месяц)\b")
_TIME_COLON = re.compile(r"(?:\b[вк]\s+)?(?<![\d:.])(\d{1,2}):(\d{2})(?![\d:])(?:\s*(утра|дня|вечера|ночи)\b)?")
_TIME_DOT = re.compile(r"\b[вк]\s+(\d{1,2})\.(\d{2})(?![\d.])(?:\s*(утра|дня|вечера|ночи)\b)?")
_DATE_MONTH = re.compile(r"(?:\b[вк]\s+)?(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\b")
_DATE_DOT = re.compile(r"(?<![\d:.])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d:.])")
_DAYWORD = re.compile(r"\b(послезавтра|завтра|сегодня)\b")
_WEEKDAY = re.compile(r"\b(?:во?|на)\s+(?:следующ\w+\s+)?(" + "|".join(_WEEKDAYS) + r")\b")
_NOON = re.compile(r"\bв\s+(полдень|полночь)\b")
_HOUR = re.compile(r"\b[вк]\s+(\d{1,2})(?:\s*(?:час\w*|ч\b))?(?:\s+(утра|дня|вечера|ночи)\b)?(?![:.]\d)")
_PART = re.compile(r"\b(утром|днем|после\s+обеда|вечером|ночью)\b")


@dataclass(frozen=True)
class Parsed:
    due: float
    what: str
    when_text: str
    explicit_time: bool = True


def _norm(text: str) -> str:
    return str(text).lower().replace("ё", "е")


def _mask(work: list[str], match: re.Match) -> None:
    for i in range(*match.span()):
        work[i] = " "


def _hour_with_period(hour: int, period: str | None) -> int | None:
    if hour > 23 or hour < 0:
        return None
    if period == "утра":
        return 0 if hour == 12 else hour
    if period == "дня":
        return hour if hour >= 12 else (hour + 12 if hour >= 1 else None)
    if period == "вечера":
        return hour + 12 if hour < 12 else hour
    if period == "ночи":
        return 0 if hour == 12 else (hour if hour <= 5 else hour + 12 if hour < 12 else hour)
    return hour + 12 if 1 <= hour <= 6 else hour


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _scan(text: str, now: float, tz_min: int):
    """Find the time expression. Returns (due, mask, explicit_time) or None. ``mask`` marks consumed chars."""
    norm = _norm(text)
    work = list(norm)
    now_dt = _local(now, tz_min)
    today = now_dt.date()

    def rest() -> str:
        return "".join(work)

    delta: timedelta | None = None
    day_shift: int | None = None
    month_shift = 0
    explicit_date: date | None = None
    hour = minute = None
    invalid = False
    used_relative_days = False

    m = _REL_HALF.search(rest())
    if m:
        delta = timedelta(minutes=30); _mask(work, m)
    if delta is None:
        m = _REL_ONEHALF.search(rest())
        if m:
            delta = timedelta(minutes=90); _mask(work, m)
    if delta is None:
        m = _REL_FEW.search(rest())
        if m:
            n = 2 if m.group(1) == "пару" else 3
            unit = m.group(2)
            if unit.startswith("мин"):
                delta = timedelta(minutes=n)
            elif unit.startswith("час"):
                delta = timedelta(hours=n)
            else:
                day_shift = n * (7 if unit.startswith("недел") else 1)
                used_relative_days = True
            _mask(work, m)
    if delta is None and day_shift is None:
        m = _REL_N.search(rest())
        if m:
            n, unit = int(m.group(1)), m.group(2)
            if unit.startswith("мин"):
                delta = timedelta(minutes=n)
            elif unit.startswith("час") or unit == "ч":
                delta = timedelta(hours=n)
            elif unit.startswith("нед"):
                day_shift, used_relative_days = 7 * n, True
            elif unit.startswith("месяц"):
                month_shift, used_relative_days = n, True
            else:
                day_shift, used_relative_days = n, True
            _mask(work, m)
    if delta is None and day_shift is None and not month_shift:
        m = _REL_ONE.search(rest())
        if m:
            unit = m.group(1)
            if unit == "минуту":
                delta = timedelta(minutes=1)
            elif unit == "час":
                delta = timedelta(hours=1)
            elif unit == "день":
                day_shift, used_relative_days = 1, True
            elif unit == "неделю":
                day_shift, used_relative_days = 7, True
            else:
                month_shift, used_relative_days = 1, True
            _mask(work, m)

    for pattern in (_TIME_COLON, _TIME_DOT):
        m = pattern.search(rest())
        if m and hour is None:
            h, mi = int(m.group(1)), int(m.group(2))
            got = _hour_with_period(h, m.group(3)) if m.group(3) else (h if 0 <= h <= 23 else None)
            if got is None or mi > 59:
                invalid = True
            hour, minute = got, mi
            _mask(work, m)

    m = _DATE_MONTH.search(rest())
    if m:
        d, month = int(m.group(1)), _MONTHS[m.group(2)]
        year = today.year
        try:
            candidate = date(year, month, d)
            if candidate < today:
                candidate = date(year + 1, month, d)
            explicit_date = candidate
        except ValueError:
            invalid = True
        _mask(work, m)
    if explicit_date is None:
        m = _DATE_DOT.search(rest())
        if m and 1 <= int(m.group(2)) <= 12:
            d, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else today.year
            year += 2000 if year < 100 else 0
            try:
                candidate = date(year, month, d)
                if candidate < today and not m.group(3):
                    candidate = date(year + 1, month, d)
                explicit_date = candidate
            except ValueError:
                invalid = True
            _mask(work, m)

    word = _DAYWORD.search(rest())
    if word:
        day_shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[word.group(1)] + (day_shift or 0)
        _mask(work, word)
    weekday = _WEEKDAY.search(rest())
    if weekday and explicit_date is None:
        ahead = (_WEEKDAYS[weekday.group(1)] - today.weekday()) % 7 or 7
        explicit_date = today + timedelta(days=ahead)
        _mask(work, weekday)

    if hour is None:
        m = _NOON.search(rest())
        if m:
            hour, minute = (12 if m.group(1) == "полдень" else 0), 0
            _mask(work, m)
    if hour is None:
        m = _HOUR.search(rest())
        if m:
            got = _hour_with_period(int(m.group(1)), m.group(2))
            if got is None:
                invalid = True
            hour, minute = got, 0
            _mask(work, m)
    explicit_time = hour is not None
    if hour is None:
        m = _PART.search(rest())
        if m:
            hour, minute = _PART_OF_DAY[re.sub(r"\s+", " ", m.group(1))], 0
            explicit_time = True
            _mask(work, m)

    if invalid:
        return None
    found_anything = (delta is not None or day_shift is not None or month_shift or explicit_date is not None
                      or hour is not None)
    if not found_anything:
        return None
    mask = [ch != o for ch, o in zip(work, norm)]

    if delta is not None:
        return now + delta.total_seconds(), mask, False
    base: date | None = None
    if explicit_date is not None:
        base = explicit_date
    elif day_shift is not None or month_shift:
        base = _add_months(today, month_shift) + timedelta(days=day_shift or 0)
    if base is None:                                    # only a time of day: today, else tomorrow
        due = _at(today, hour, minute or 0, tz_min)
        if due <= now:
            due = _at(today + timedelta(days=1), hour, minute or 0, tz_min)
        return due, mask, True
    if hour is None:
        if used_relative_days and not word:            # "через неделю": keep the current clock time
            due = _at(base, now_dt.hour, now_dt.minute, tz_min)
        else:
            due = _at(base, _DEFAULT_HOUR, 0, tz_min)
    else:
        due = _at(base, hour, minute or 0, tz_min)
    if due <= now:
        return None
    return due, mask, explicit_time


def parse_when(text: str, now: float, tz_offset_min: int = DEFAULT_TZ_MIN):
    """Return ``(due, mask, explicit_time)`` for the first time expression in ``text`` or ``None``."""
    return _scan(text, now, tz_offset_min)


def _clean_what(original: str, mask: list[bool]) -> str:
    kept = "".join(" " if masked else ch for ch, masked in zip(original, mask))
    kept = _PREFIX.sub("", kept, count=1)
    kept = re.sub(r"\s+", " ", kept).strip(" ,.;:!?-—")
    kept = re.sub(r"^(?:о том,? что|что|чтобы|про то,? что)\s+", "", kept, flags=re.IGNORECASE)
    if re.fullmatch(r"(?:в|на|к|во|через|и|а)", kept, flags=re.IGNORECASE):
        kept = ""
    return kept.strip(" ,.;:!?-—")[:300]


def parse_reminder(text: str, now: float, tz_offset_min: int = DEFAULT_TZ_MIN) -> Parsed | None:
    """Parse "напомни ..." into a due time and the thing to remember; ``None`` if it is not a reminder,
    has no usable time or lies in the past."""
    text = str(text or "")
    if not _TRIGGER.match(text):
        return None
    found = _scan(text, now, tz_offset_min)
    if found is None:
        return None
    due, mask, explicit = found
    return Parsed(due=due, what=_clean_what(text, mask), when_text=format_when(due, now, tz_offset_min),
                  explicit_time=explicit)


# ---------------------------------------------------------------------------------------------------------
# open threads ("завтра у меня собеседование")
# ---------------------------------------------------------------------------------------------------------
_THREAD_TOPICS = (("собеседовани", "собеседование"), ("экзамен", "экзамен"), ("зачет", "зачёт"),
                  ("встреч", "встреча"), ("операци", "операция"), ("защит", "защита"),
                  ("презентаци", "презентация"), ("поездк", "поездка"), ("свидани", "свидание"),
                  ("переговор", "переговоры"), ("интервью", "интервью"))
_THIRD_PERSON = re.compile(r"\bу\s+(?!меня\b)[а-я]{3,}")


@dataclass(frozen=True)
class Thread:
    topic: str
    due: float


def detect_thread(text: str, now: float, tz_offset_min: int = DEFAULT_TZ_MIN) -> Thread | None:
    """An upcoming personal event the participant mentioned; the follow-up is due in the evening of that day."""
    norm = _norm(text)
    if len(norm) > 300 or _THIRD_PERSON.search(norm):
        return None
    topic = next((name for stem, name in _THREAD_TOPICS if stem in norm), None)
    if topic is None:
        return None
    found = _scan(text, now, tz_offset_min)
    if found is None:
        return None
    due, _, explicit = found
    if explicit:
        follow = due + 3 * 3600
    else:
        follow = _at(_local(due, tz_offset_min).date(), 20, 0, tz_offset_min)
    return Thread(topic=topic, due=follow) if follow > now else None


# ---------------------------------------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------------------------------------
def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


class ProactiveStore:
    """Per-participant schedule and preferences under ``<root>/<person_key>/proactive/``."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _dir(self, person_key: str) -> Path:
        return scoped_person_dir(self.root, person_key) / "proactive"

    def participants(self) -> list[str]:
        try:
            names = sorted(p.name for p in self.root.iterdir() if p.is_dir())
        except OSError:
            return []
        out = []
        for name in names:
            try:
                key = validate_person_key(name)
            except ValueError:
                continue
            if (self._dir(key)).is_dir():
                out.append(key)
        return out

    def touch(self, person_key: str) -> None:
        self._dir(person_key).mkdir(parents=True, exist_ok=True)

    # -- preferences -----------------------------------------------------------------------------
    def prefs(self, person_key: str) -> dict[str, Any]:
        out = dict(DEFAULT_PREFS)
        raw = _read_json(self._dir(person_key) / "prefs.json")
        if isinstance(raw, dict):
            out.update(_clean_prefs(raw))
        return out

    def set_prefs(self, person_key: str, **changes: Any) -> dict[str, Any]:
        merged = {**self.prefs(person_key), **_clean_prefs(changes)}
        _atomic_json(self._dir(person_key) / "prefs.json", merged)
        return merged

    # -- schedule ----------------------------------------------------------------------------------
    def _load(self, person_key: str) -> dict[str, Any]:
        raw = _read_json(self._dir(person_key) / "schedule.json")
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            return {"schema": SCHEMA, "items": [], "sent_log": [], "done_keys": []}
        raw.setdefault("sent_log", [])
        raw.setdefault("done_keys", [])
        return raw

    def _save(self, person_key: str, data: dict[str, Any], now: float | None = None) -> None:
        now = time.time() if now is None else now
        keep, dropped = [], []
        for item in data["items"]:
            terminal = item.get("state") in ("sent", "cancelled", "expired", "failed", "unknown", "missed") \
                and item.get("state") != "missed"
            if terminal and now - float(item.get("updated", item.get("due", now))) > KEEP_TERMINAL_S:
                dropped.append(item["key"])
            else:
                keep.append(item)
        data["items"] = keep
        data["done_keys"] = (data["done_keys"] + dropped)[-MAX_KEYS:]
        data["sent_log"] = data["sent_log"][-300:]
        data["schema"] = SCHEMA
        _atomic_json(self._dir(person_key) / "schedule.json", data)

    def items(self, person_key: str, states: tuple[str, ...] | None = None,
              kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        rows = [i for i in self._load(person_key)["items"]
                if (states is None or i.get("state") in states) and (kinds is None or i.get("kind") in kinds)]
        return sorted(rows, key=lambda i: (float(i.get("due", 0)), i.get("id", "")))

    def has_key(self, person_key: str, key: str) -> bool:
        data = self._load(person_key)
        return key in data["done_keys"] or any(i.get("key") == key for i in data["items"])

    def add_item(self, person_key: str, kind: str, text: str, due: float, key: str, *,
                 source: str = "", now: float | None = None) -> dict[str, Any] | None:
        """Add one scheduled item; ``None`` when the key is known (idempotent) or the schedule is full."""
        data = self._load(person_key)
        if key in data["done_keys"] or any(i.get("key") == key for i in data["items"]):
            return None
        if sum(1 for i in data["items"] if i.get("state") == "pending") >= MAX_PENDING:
            return None
        stamp = time.time() if now is None else now
        item = {"id": _key(person_key, key, len(data["items"]))[:12], "kind": kind,
                "text": redact_secrets(str(text))[0][:400], "due": float(due), "key": str(key),
                "state": "pending", "attempts": 0, "source": source[:120], "created": stamp,
                "updated": stamp}
        data["items"].append(item)
        self._save(person_key, data, now)
        return item

    def update_item(self, person_key: str, item_id: str, **fields: Any) -> dict[str, Any] | None:
        data = self._load(person_key)
        for item in data["items"]:
            if item.get("id") == item_id:
                item.update(fields)
                item["updated"] = fields.get("updated", time.time())
                self._save(person_key, data)
                return item
        return None

    def log_sent(self, person_key: str, ts: float, kind: str) -> None:
        data = self._load(person_key)
        data["sent_log"].append([float(ts), kind])
        self._save(person_key, data)


def _read_json(path: Path) -> Any:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clean_prefs(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("proactive", "opted_out", "digest", "notify_finished"):
        if name in raw:
            out[name] = bool(raw[name])
    for name, low, high in (("digest_hour", 0, 23), ("quiet_start", 0, 23), ("quiet_end", 0, 23),
                            ("max_per_day", 0, MAX_PER_DAY_LIMIT), ("min_gap_s", 0, 86400)):
        if name in raw:
            try:
                out[name] = max(low, min(high, int(raw[name])))
            except (TypeError, ValueError):
                continue
    if "tz_offset_min" in raw:
        value = raw["tz_offset_min"]
        out["tz_offset_min"] = None if value is None else max(-14 * 60, min(14 * 60, int(value)))
    return out


# ---------------------------------------------------------------------------------------------------------
# senders
# ---------------------------------------------------------------------------------------------------------
class TelegramSender:
    """Default sender: private Telegram chat resolved from the person key, never stored anywhere."""

    def __init__(self, runtime: Any, *, salt: bytes | None = None):
        self.runtime = runtime
        self._salt = salt
        self._people: dict[str, Any] | None = None

    def _salt_bytes(self) -> bytes | None:
        if self._salt:
            return self._salt
        return getattr(getattr(self.runtime, "vault", None), "identity_salt", None)

    def _resolve(self, person_key: str):
        salt = self._salt_bytes()
        if not salt:
            return None
        if self._people is None or person_key not in self._people:
            people: dict[str, Any] = {}
            candidates = list(getattr(getattr(self.runtime, "settings", None), "people", ()) or ())
            for who in list(getattr(self.runtime, "_spawned_workers", ()) or ()):
                try:
                    from bcc.telegram_companion.config import Person
                    user_id = int(str(who).split(":", 1)[0])
                    candidates.append(Person(user_id=user_id, chat_id=user_id, role="guest"))
                except (ValueError, TypeError):
                    continue
            for person in candidates:
                try:
                    people[derive_person_key(person.user_id, salt)] = person
                except (ValueError, AttributeError):
                    continue
            self._people = people
        return self._people.get(person_key)

    async def __call__(self, person_key: str, text: str, key: str):
        person = self._resolve(person_key)
        if person is None:
            return "undeliverable"                      # Jeff window or unknown surface: shown next turn
        await self.runtime.telegram.send(person, text)
        return "sent"


def task_store_provider(task_store: Any) -> TasksProvider:
    def provide(person_key: str) -> list:
        try:
            return list(task_store.list(person_key))
        except Exception:                               # noqa: BLE001 - tasks never break the schedule
            return []
    return provide


def _normalize_result(result: Any) -> str:
    if result is True or result is None or result == "sent":
        return "sent"
    if result is False or result == "retry":
        return "retry"
    if result == "undeliverable":
        return "undeliverable"
    return "unknown"


def _short(text: str, limit: int = 80) -> str:
    clean = re.sub(r"\s+", " ", redact_secrets(str(text or ""))[0]).strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------------------------------------
class ProactiveEngine:
    def __init__(self, store: ProactiveStore, *, clock: Callable[[], float] = time.time, sender: Sender,
                 tasks: TasksProvider | None = None, access: Callable[[str], bool] | None = None,
                 default_tz_min: int = DEFAULT_TZ_MIN):
        self.store = store
        self.clock = clock
        self.sender = sender
        self.tasks = tasks
        self.access = access or (lambda person_key: True)
        self.default_tz_min = default_tz_min
        self.totals: Counter = Counter()
        self.last_tick: float | None = None

    # -- helpers ----------------------------------------------------------------------------------------
    def tz_for(self, prefs: dict[str, Any]) -> int:
        value = prefs.get("tz_offset_min")
        return self.default_tz_min if value is None else int(value)

    def watch(self, person_key: str) -> None:
        self.store.touch(person_key)

    def opt_out(self, person_key: str) -> None:
        self.store.set_prefs(person_key, opted_out=True, proactive=False, digest=False)
        self._cancel_pending(person_key)

    def opt_in(self, person_key: str) -> None:
        self.store.set_prefs(person_key, opted_out=False, proactive=True)

    def _cancel_pending(self, person_key: str, kinds: tuple[str, ...] | None = None) -> int:
        n = 0
        for item in self.store.items(person_key, states=("pending", "sending"), kinds=kinds):
            self.store.update_item(person_key, item["id"], state="cancelled")
            n += 1
        return n

    # -- one pass over everybody --------------------------------------------------------------------------
    async def tick(self) -> dict[str, int]:
        now = self.clock()
        stats: Counter = Counter()
        for person_key in self.store.participants():
            try:
                await self._tick_person(person_key, now, stats)
            except Exception as exc:                    # noqa: BLE001 - one participant never blocks the rest
                stats["errors"] += 1
                log.warning("proactive tick failed for one participant: %s", type(exc).__name__)
        self.last_tick = now
        self.totals.update(stats)
        return dict(stats)

    async def _tick_person(self, person_key: str, now: float, stats: Counter) -> None:
        prefs = self.store.prefs(person_key)
        if prefs["opted_out"] or not self.access(person_key):
            if self._cancel_pending(person_key):
                stats["cancelled"] += 1
            return
        tz = self.tz_for(prefs)
        await self._recover(person_key, stats)
        if prefs["proactive"]:
            self._plan_followups(person_key, prefs, now, tz)
            self._plan_digest(person_key, prefs, now, tz)
        for item in self.store.items(person_key, states=("pending",)):
            if item["due"] <= now:
                await self._deliver(person_key, item, prefs, now, tz, stats)

    async def _recover(self, person_key: str, stats: Counter) -> None:
        """Items left in ``sending`` by a crash: verify, otherwise never repeat."""
        for item in self.store.items(person_key, states=("sending",)):
            verdict = None
            verifier = getattr(self.sender, "already_sent", None)
            if verifier is not None:
                try:
                    verdict = verifier(item["key"])
                    if inspect.isawaitable(verdict):
                        verdict = await verdict
                except Exception:                       # noqa: BLE001 - unverifiable == unknown
                    verdict = None
            if verdict is True:
                self.store.update_item(person_key, item["id"], state="sent", sent_at=self.clock())
                stats["recovered_sent"] += 1
            elif verdict is False and item.get("attempts", 0) < MAX_ATTEMPTS:
                self.store.update_item(person_key, item["id"], state="pending")
                stats["recovered_retry"] += 1
            else:
                self.store.update_item(person_key, item["id"], state="unknown")
                stats["unknown"] += 1

    # -- planning ----------------------------------------------------------------------------------------
    def _tasks(self, person_key: str) -> list[dict[str, Any]]:
        if self.tasks is None:
            return []
        try:
            return [t for t in self.tasks(person_key) if isinstance(t, dict)]
        except Exception:                               # noqa: BLE001
            return []

    def _plan_followups(self, person_key: str, prefs: dict[str, Any], now: float, tz: int) -> None:
        wanted: set[str] = set()
        for row in self._tasks(person_key):
            plan = self._task_plan(row, prefs, now)
            for stage, (due, text, source) in enumerate(plan):
                wanted.add(source.rsplit(":", 1)[0])
                if due < now - EXPIRE_FOLLOWUP_S:
                    continue
                self.store.add_item(person_key, "followup", text, due, _key(person_key, source), source=source)
        for item in self.store.items(person_key, states=("pending",), kinds=("followup",)):
            source = item.get("source", "")
            if source.startswith("task:") and source.rsplit(":", 1)[0] not in wanted:
                self.store.update_item(person_key, item["id"], state="cancelled")     # task moved on

    @staticmethod
    def _task_plan(row: dict[str, Any], prefs: dict[str, Any], now: float) -> list[tuple[float, str, str]]:
        state, tid = row.get("state"), str(row.get("id", ""))
        goal = _short(row.get("goal", ""))
        try:
            updated = parse_utc(row.get("updated_at", ""))
        except (ValueError, TypeError):
            updated = now
        if state == "WAITING_INPUT":
            since_raw = (row.get("awaited_input") or {}).get("since") or row.get("updated_at", "")
            try:
                since = parse_utc(since_raw)
            except (ValueError, TypeError):
                since = updated
            base = f"task:{tid}:{state}:{since_raw}"
            return [(since + FOLLOWUP_DELAY_S,
                     f"Задача «{goal}» ждёт от тебя ответа. Когда будет время, продолжим?", base + ":0"),
                    (since + FOLLOWUP_SECOND_S,
                     f"Напоминаю про задачу «{goal}»: без твоего ответа она стоит. Продолжим или отменить?",
                     base + ":1")]
        if state == "WAITING_APPROVAL":
            base = f"task:{tid}:{state}:{row.get('updated_at', '')}"
            return [(updated + FOLLOWUP_DELAY_S,
                     f"Задача «{goal}» ждёт твоего одобрения следующего шага. Одобришь?", base + ":0"),
                    (updated + FOLLOWUP_SECOND_S,
                     f"Задача «{goal}» всё ещё ждёт одобрения. Одобрить или отменить?", base + ":1")]
        if state == "UNKNOWN_OUTCOME":
            base = f"task:{tid}:{state}:{row.get('updated_at', '')}"
            return [(updated + UNKNOWN_DELAY_S,
                     f"Не уверен, выполнено ли действие в задаче «{goal}». Подтверди, пожалуйста, что произошло.",
                     base + ":0")]
        if state in ("DONE", "FAILED") and prefs.get("notify_finished") and now - updated < 86400:
            verb = "готова" if state == "DONE" else "не удалась"
            base = f"task:{tid}:{state}:{row.get('updated_at', '')}"
            return [(updated, f"Задача «{goal}» {verb}.", base + ":0")]
        return []

    def _plan_digest(self, person_key: str, prefs: dict[str, Any], now: float, tz: int) -> None:
        if not prefs["digest"]:
            return
        local = _local(now, tz)
        if not prefs["digest_hour"] <= local.hour < prefs["digest_hour"] + DIGEST_WINDOW_H:
            return
        key = _key(person_key, "digest", local.date().isoformat())
        if self.store.has_key(person_key, key):
            return
        lines = []
        for row in self._tasks(person_key):
            label = {"WAITING_INPUT": "ждёт твоего ответа", "WAITING_APPROVAL": "ждёт одобрения",
                     "UNKNOWN_OUTCOME": "нужно подтвердить результат", "RUNNING": "выполняется",
                     "PLANNED": "запланирована"}.get(row.get("state"))
            if label:
                lines.append(f"• «{_short(row.get('goal', ''), 60)}» — {label}")
        reminders = [i for i in self.store.items(person_key, states=("pending",), kinds=("reminder",))
                     if now <= i["due"] < now + 24 * 3600]
        for item in reminders[:8]:
            lines.append(f"• {_hhmm(item['due'], tz)} — {_short(item['text'], 60)}")
        if not lines:
            return
        self.store.add_item(person_key, "digest", "Сводка на сегодня:\n" + "\n".join(lines[:12]), now, key,
                            source="digest:" + local.date().isoformat())

    # -- delivery ------------------------------------------------------------------------------------------
    def _sent_today(self, person_key: str, now: float, tz: int, unsolicited: bool) -> tuple[int, float | None]:
        data = self.store._load(person_key)
        today = _local(now, tz).date()
        count, last = 0, None
        for ts, kind in data["sent_log"]:
            is_unsolicited = kind in ("followup", "digest")
            if is_unsolicited != unsolicited:
                continue
            if _local(ts, tz).date() == today:
                count += 1
            last = ts if last is None or ts > last else last
        return count, last

    def _defer(self, person_key: str, item: dict[str, Any], until: float, stats: Counter) -> None:
        self.store.update_item(person_key, item["id"], due=until)
        stats["deferred"] += 1

    async def _deliver(self, person_key: str, item: dict[str, Any], prefs: dict[str, Any], now: float, tz: int,
                       stats: Counter) -> None:
        kind = item["kind"]
        unsolicited = kind in ("followup", "digest")
        age = now - item["due"]
        if age > (EXPIRE_REMINDER_S if kind == "reminder" else EXPIRE_FOLLOWUP_S):
            self.store.update_item(person_key, item["id"], state="expired")
            stats["expired"] += 1
            return
        if unsolicited:
            if not prefs["proactive"]:
                return
            if in_quiet_hours(now, tz, prefs["quiet_start"], prefs["quiet_end"]):
                self._defer(person_key, item, quiet_end(now, tz, prefs["quiet_start"], prefs["quiet_end"]), stats)
                return
            count, last = self._sent_today(person_key, now, tz, True)
            if count >= prefs["max_per_day"]:
                self._defer(person_key, item, _next_midnight(now, tz), stats)
                return
            if last is not None and now - last < prefs["min_gap_s"]:
                self._defer(person_key, item, last + prefs["min_gap_s"], stats)
                return
        else:
            count, _ = self._sent_today(person_key, now, tz, False)
            if count >= REMINDER_DAILY_CAP:
                self._defer(person_key, item, _next_midnight(now, tz), stats)
                return
        text = self._compose(item, age, tz)
        attempts = int(item.get("attempts", 0)) + 1
        self.store.update_item(person_key, item["id"], state="sending", attempts=attempts)   # BEFORE the send
        try:
            raw = await asyncio.wait_for(self.sender(person_key, text, item["key"]), timeout=SEND_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                        # noqa: BLE001 - may have been delivered: never repeat blindly
            log.warning("proactive send outcome unknown: %s", type(exc).__name__)
            self.store.update_item(person_key, item["id"], state="unknown")
            stats["unknown"] += 1
            return
        result = _normalize_result(raw)
        if result == "sent":
            self.store.update_item(person_key, item["id"], state="sent", sent_at=now)
            self.store.log_sent(person_key, now, kind)
            stats["sent"] += 1
        elif result == "retry":
            if attempts >= MAX_ATTEMPTS:
                self.store.update_item(person_key, item["id"], state="failed")
                stats["failed"] += 1
            else:
                self.store.update_item(person_key, item["id"], state="pending",
                                       due=now + RETRY_BACKOFF_S * 2 ** (attempts - 1))
                stats["retried"] += 1
        elif result == "undeliverable":
            self.store.update_item(person_key, item["id"], state="missed")
            stats["missed"] += 1
        else:
            self.store.update_item(person_key, item["id"], state="unknown")
            stats["unknown"] += 1

    @staticmethod
    def _compose(item: dict[str, Any], age: float, tz: int) -> str:
        text = item["text"].strip()
        if item["kind"] == "reminder":
            body = f"Напоминание: {text}" if text else "Напоминание"
            if age > LATE_AFTER_S:
                body = (f"Напоминание (опоздало, было в {_hhmm(item['due'], tz)}): {text}" if text
                        else f"Напоминание (опоздало, было в {_hhmm(item['due'], tz)})")
            return body
        return f"{text}\n\n{OPT_OUT_HINT}"


# ---------------------------------------------------------------------------------------------------------
# chat commands
# ---------------------------------------------------------------------------------------------------------
_CMD = {
    "optout": re.compile(r"^(?:пожалуйста\W+)?(?:стоп|хватит|прекрати|отключи|выключи)\s+(?:мне\s+)?(?:все\s+)?"
                         r"(?:напоминани\w*|напоминать|уведомлени\w*|проактивн\w+(?:\s+сообщени\w+)?)\W*$|"
                         r"^не\s+(?:напоминай|пиши(?:\s+мне)?\s+первым)\W*$"),
    "optin": re.compile(r"^(?:пожалуйста\W+)?(?:включи|верни|разреши)\s+(?:мне\s+)?(?:напоминани\w*|уведомлени\w*)\W*$|"
                        r"^(?:можешь\s+|пиши\s+мне\s+)(?:писать\s+)?первым\W*$"),
    "digest_on": re.compile(r"^(?:включи|присылай)\s+(?:мне\s+)?(?:(?:утренн\w+|ежедневн\w+)\s+)?(?:сводку|дайджест)\W*$"),
    "digest_off": re.compile(r"^(?:выключи|отключи|не\s+присылай)\s+(?:мне\s+)?(?:(?:утренн\w+|ежедневн\w+)\s+)?"
                             r"(?:сводку|дайджест)\W*$"),
    "list": re.compile(r"^(?:мои\s+напоминания|покажи\s+(?:мои\s+)?напоминания|список\s+напоминаний|"
                       r"какие\s+у\s+меня\s+напоминания)\W*$"),
    "cancel_all": re.compile(r"^(?:отмени|удали)\s+все\s+напоминания\W*$"),
    "cancel_n": re.compile(r"^(?:отмени|удали)\s+напоминание\s+(?:№\s*)?(\d{1,3})\W*$"),
    "tz": re.compile(r"^(?:мой\s+)?часовой\s+пояс\s*(?:это\s+|-\s*)?(utc|gmt|мск)\s*([+\-−]\s*\d{1,2})?\W*$"),
    "quiet": re.compile(r"^(?:тихие\s+часы|не\s+беспокой(?:\s+меня)?)\s+[сc]\s*(\d{1,2})(?::00)?\s*(?:до|по)\s*(\d{1,2})(?::00)?\W*$"),
    "quiet_off": re.compile(r"^(?:выключи|отключи)\s+тихие\s+часы\W*$"),
}


def classify_command(text: str) -> tuple[str, tuple[str, ...]] | None:
    norm = _norm(text).strip()
    if not norm or len(norm) > 90:
        return None
    for name, pattern in _CMD.items():
        m = pattern.match(norm)
        if m:
            return name, tuple(g for g in m.groups() if g is not None)
    return None


# ---------------------------------------------------------------------------------------------------------
# the module
# ---------------------------------------------------------------------------------------------------------
class ProactiveModule(BaseModule):
    name = "proactive"
    version = "1"
    order = 80

    def __init__(self, engine: ProactiveEngine, *, interval: float = TICK_SECONDS,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.engine = engine
        self.store = engine.store
        self._interval = interval
        self._sleep = sleep
        self._task: asyncio.Task | None = None

    # -- lifecycle ----------------------------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                if self.switched_on():          # owner switched «proactive» off: nothing is delivered or planned
                    await self.engine.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # noqa: BLE001 - the loop outlives any single fault
                log.warning("proactive loop tick failed: %s", type(exc).__name__)
            await self._sleep(self._interval)

    # -- hooks --------------------------------------------------------------------------------------------
    async def pre_route(self, ctx: TurnContext) -> Advice | None:
        text = ctx.text.strip()
        person = ctx.person_key
        command = classify_command(text)
        if command is not None:
            reply = self._run_command(person, *command)
            return Advice(reply=reply, tags=("proactive",)) if reply else None
        if _TRIGGER.match(text):
            return Advice(reply=self._reminder(ctx), tags=("proactive", "reminder"))
        prefs = self.store.prefs(person)
        if prefs["proactive"] and not prefs["opted_out"]:
            self._note_thread(ctx, prefs)
        return None

    async def post_reply(self, ctx: TurnContext, reply: str) -> str | None:
        """Reminders that could not be pushed (Jeff window) are shown once, on the next turn."""
        try:
            missed = self.store.items(ctx.person_key, states=("missed",), kinds=("reminder",))
        except ValueError:
            return None
        if not missed:
            return None
        lines = []
        for item in missed[:5]:
            lines.append(f"• {item['text'] or 'напоминание'}")
            self.store.update_item(ctx.person_key, item["id"], state="sent", ref="surfaced_next_turn")
        return reply + "\n\nКстати, пока меня не было, пропущено напоминание:\n" + "\n".join(lines)

    def status(self) -> dict[str, Any]:
        pending = 0
        keys = self.store.participants()
        for key in keys:
            pending += len(self.store.items(key, states=("pending",)))
        totals = self.engine.totals
        return {"name": self.name, "version": self.version, "pending": pending, "participants": len(keys),
                "sent": totals["sent"], "deferred": totals["deferred"], "failed": totals["failed"],
                "running": self._task is not None and not self._task.done(),
                "last_tick": self.engine.last_tick}

    # -- command handling -------------------------------------------------------------------------------
    def _run_command(self, person: str, name: str, args: tuple[str, ...]) -> str:
        engine, store = self.engine, self.store
        if name == "optout":
            engine.opt_out(person)
            return ("Хорошо, напоминания и сообщения по своей инициативе выключены, "
                    "всё запланированное отменено. Вернуть: «включи напоминания».")
        if name == "optin":
            engine.opt_in(person)
            return ("Включил. Могу напоминать и иногда писать первым: вопросы по задачам и итоги дня. "
                    "Не ночью и не чаще трёх раз в день. Выключить: «стоп напоминания».")
        if name == "digest_on":
            engine.store.set_prefs(person, digest=True, proactive=True, opted_out=False)
            return "Буду присылать короткую сводку утром, если будет что сказать. Выключить: «выключи сводку»."
        if name == "digest_off":
            store.set_prefs(person, digest=False)
            return "Сводку выключил."
        if name == "list":
            return self._listing(person)
        if name == "cancel_all":
            n = engine._cancel_pending(person, kinds=("reminder",))
            return f"Отменил напоминаний: {n}." if n else "Активных напоминаний нет."
        if name == "cancel_n":
            rows = self.store.items(person, states=("pending",), kinds=("reminder",))
            index = int(args[0])
            if not 1 <= index <= len(rows):
                return "Нет напоминания с таким номером. Посмотреть список: «мои напоминания»."
            store.update_item(person, rows[index - 1]["id"], state="cancelled")
            return f"Отменил напоминание {index}."
        if name == "tz":
            base = 3 if args[0] == "мск" else 0
            offset = 0
            if len(args) > 1:
                offset = int(args[1].replace("−", "-").replace(" ", ""))
            minutes = (base + offset) * 60
            if abs(minutes) > 14 * 60:
                return "Такой часовой пояс не подходит. Пример: «мой часовой пояс UTC+5»."
            store.set_prefs(person, tz_offset_min=minutes)
            return f"Запомнил часовой пояс: UTC{(minutes // 60):+d}."
        if name == "quiet":
            start, end = int(args[0]), int(args[1])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                return "Часы должны быть от 0 до 23."
            store.set_prefs(person, quiet_start=start, quiet_end=end)
            return f"Тихие часы: с {start:02d}:00 до {end:02d}:00. В это время сам не пишу (кроме твоих напоминаний)."
        if name == "quiet_off":
            store.set_prefs(person, quiet_start=0, quiet_end=0)
            return "Тихие часы выключены."
        return ""

    def _listing(self, person: str) -> str:
        rows = self.store.items(person, states=("pending",), kinds=("reminder",))
        if not rows:
            return "Активных напоминаний нет."
        prefs = self.store.prefs(person)
        tz, now = self.engine.tz_for(prefs), self.engine.clock()
        lines = [f"{n}. {format_when(item['due'], now, tz)} — {item['text'] or 'напоминание'}"
                 for n, item in enumerate(rows[:20], 1)]
        return "Твои напоминания:\n" + "\n".join(lines) + "\nОтменить: «отмени напоминание 1»."

    def _reminder(self, ctx: TurnContext) -> str:
        person = ctx.person_key
        prefs = self.store.prefs(person)
        if prefs["opted_out"]:
            return "Напоминания выключены. Напиши «включи напоминания», и я поставлю."
        now, tz = self.engine.clock(), self.engine.tz_for(prefs)
        parsed = parse_reminder(ctx.text, now, tz)
        if parsed is None:
            return ("Не понял, когда напомнить. Напиши время, например: «напомни завтра в 10 позвонить маме» "
                    "или «напомни через 2 часа выпить воду».")
        key = _key(person, "reminder", ctx.message_id, int(parsed.due), parsed.what)
        if not self.store.has_key(person, key):
            if len(self.store.items(person, states=("pending",))) >= MAX_PENDING:
                return "У тебя уже много напоминаний. Отмени лишние: «мои напоминания»."
            self.store.add_item(person, "reminder", parsed.what, parsed.due, key, source=f"msg:{ctx.message_id}",
                                now=now)
        what = f": «{parsed.what}»" if parsed.what else ""
        return f"Хорошо, напомню {parsed.when_text}{what}."

    def _note_thread(self, ctx: TurnContext, prefs: dict[str, Any]) -> None:
        now, tz = self.engine.clock(), self.engine.tz_for(prefs)
        found = detect_thread(ctx.text, now, tz)
        if found is None:
            return
        key = _key(ctx.person_key, "thread", ctx.message_id, found.topic)
        self.store.add_item(ctx.person_key, "followup", f"Ты упоминал: «{found.topic}». Как всё прошло?",
                            found.due, key, source=f"thread:{ctx.message_id}", now=now)


def create(runtime: Any) -> ProactiveModule:
    vault = getattr(runtime, "vault", None)
    if vault is None:
        raise ValueError("proactive needs a runtime with a persona vault")
    store = ProactiveStore(vault.root)
    tasks = None
    home = getattr(runtime, "home", None)
    if home is not None:
        from ..tasks import TaskStore
        tasks = task_store_provider(TaskStore(Path(home)))
    data_dir = getattr(vault, "data_dir", None)

    def access(person_key: str) -> bool:
        if data_dir is None:
            return True
        from .. import participant_profile
        return not participant_profile.is_revoked(data_dir, person_key)

    try:
        tz = int(os.environ.get("BOSSMAN_JEFF_TZ_MIN", DEFAULT_TZ_MIN))
    except ValueError:
        tz = DEFAULT_TZ_MIN
    engine = ProactiveEngine(store, sender=TelegramSender(runtime), tasks=tasks, access=access,
                             default_tz_min=tz)
    return ProactiveModule(engine)
