"""Scheduled Work (V2.6, раздел 21) — исполнитель AgentSpec.schedule.

Поле schedule у агента давно парсится (agents.py: `schedule=cfg.get("schedule")`),
но не исполнялось НИЧЕМ. Формат — реальный формат поля, 5-полевой cron, как в
agents/fresh-vibes/agent.yaml: `"*/15 8-20 * * 1-6"` (минута час день-месяца
месяц день-недели; день недели 0/7 = воскресенье). Новый формат не изобретаем.

Дисциплина ядра:
- по умолчанию ВЫКЛЮЧЕНО: без BOSSMAN_SCHEDULES_ENABLED=1 loop() выходит сразу,
  поведение ядра не меняется;
- срабатывание = существующий путь задач: INSERT в tasks (source='schedule')
  через bossman.db + bossman.runner.enqueue — никакой второй очереди;
- ограничено и подконтрольно владельцу: max_fires_per_day на агента (default
  24), stop(), счётчики сбоев в памяти;
- без перекрытий: если предыдущая schedule-задача агента ещё
  queued/running/waiting_approval — пропуск, не наслоение;
- каждый тик в try/except: один сбой не убивает петлю.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from . import obs

_log = obs.get_logger("bossman.schedule_runner")

ENABLE_ENV = "BOSSMAN_SCHEDULES_ENABLED"

# ---------------------------------------------------------------- cron

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_FIELD_NAMES = ("минута", "час", "день месяца", "месяц", "день недели")


@dataclass(frozen=True)
class Cron:
    """Разобранное 5-полевое cron-выражение."""
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    dows: frozenset       # 0..6, воскресенье = 0 (7 нормализуется в 0)
    day_star: bool        # '*' в поле дня месяца
    dow_star: bool        # '*' в поле дня недели


def _parse_field(spec: str, lo: int, hi: int, name: str) -> frozenset:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"cron: шаг должен быть > 0 в поле «{name}»")
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron: значение вне диапазона {lo}-{hi} в поле «{name}»: {part!r}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse_cron(expr: str) -> Cron:
    """Разобрать `"*/15 8-20 * * 1-6"` → Cron; кривое выражение → ValueError."""
    fields = (expr or "").split()
    if len(fields) != 5:
        raise ValueError(f"cron: ожидается 5 полей, получено {len(fields)}: {expr!r}")
    parsed = [_parse_field(f, lo, hi, n)
              for f, (lo, hi), n in zip(fields, _FIELD_RANGES, _FIELD_NAMES)]
    dows = frozenset(d % 7 for d in parsed[4])  # 7 → 0 (воскресенье)
    return Cron(minutes=parsed[0], hours=parsed[1], days=parsed[2],
                months=parsed[3], dows=dows,
                day_star=fields[2].strip() == "*", dow_star=fields[4].strip() == "*")


def cron_matches(cron: Cron | str, dt: datetime) -> bool:
    """Совпадает ли минута dt с расписанием. Семантика dom/dow — как у cron:
    оба поля ограничены → достаточно совпадения ЛЮБОГО из них."""
    c = parse_cron(cron) if isinstance(cron, str) else cron
    if dt.minute not in c.minutes or dt.hour not in c.hours:
        return False
    return _day_matches(c, dt.date())  # python: пн=0 → cron: пн=1, вс=0


def _day_matches(c: Cron, d: date) -> bool:
    """Подходит ли календарный день (месяц + cron-семантика dom/dow)."""
    if d.month not in c.months:
        return False
    day_ok = d.day in c.days
    dow_ok = ((d.weekday() + 1) % 7) in c.dows
    if c.day_star and c.dow_star:
        return True
    if c.day_star:
        return dow_ok
    if c.dow_star:
        return day_ok
    return day_ok or dow_ok


def next_fire(cron: Cron | str, now: datetime) -> datetime | None:
    """Ближайший момент срабатывания СТРОГО после now (детерминированно).

    Перебор по дням/часам/минутам из разобранных множеств — не по всем минутам
    года. None — если в ближайшие ~4 года совпадения нет (вырожденное
    выражение вроде 30 февраля)."""
    c = parse_cron(cron) if isinstance(cron, str) else cron
    minutes, hours = sorted(c.minutes), sorted(c.hours)
    day0 = now.date()
    for offset in range(366 * 4 + 1):
        d = day0 + timedelta(days=offset)
        if not _day_matches(c, d):
            continue
        for h in hours:
            for m in minutes:
                cand = datetime(d.year, d.month, d.day, h, m)
                if cand > now:
                    return cand
    return None


# ---------------------------------------------------------------- runner

_OVERLAP_SQL = ("SELECT id FROM tasks WHERE agent=$1 AND source='schedule' "
                "AND status IN ('queued','running','waiting_approval') LIMIT 1")
_INSERT_SQL = ("INSERT INTO tasks (agent, source, text) "
               "VALUES ($1, 'schedule', $2) RETURNING id")


async def _db_fetchrow(sql: str, *args: Any):
    from . import db
    return await db.fetchrow(sql, *args)


async def _runner_enqueue(task_id: int) -> None:
    from .runner import enqueue
    await enqueue(task_id)


def _load_agents_default() -> dict:
    from .agents import load_all
    return load_all()


class ScheduleRunner:
    """Петля расписаний: тик каждые ~30с, всё существующими входами ядра."""

    def __init__(self,
                 load_agents: Callable[[], dict] | None = None, *,
                 max_fires_per_day: int = 24,
                 per_agent_max: dict[str, int] | None = None,
                 tick_interval: float = 30.0,
                 fetchrow: Callable[..., Awaitable[Any]] | None = None,
                 enqueue: Callable[[int], Awaitable[None]] | None = None) -> None:
        self._load_agents = load_agents or _load_agents_default
        self.max_fires_per_day = int(max_fires_per_day)
        self.per_agent_max = dict(per_agent_max or {})   # владелец может ужесточить точечно
        self.tick_interval = tick_interval
        self._fetchrow = fetchrow or _db_fetchrow
        self._enqueue = enqueue or _runner_enqueue
        self._stopped = False
        self._last_fire: dict[str, datetime] = {}        # минута последнего срабатывания
        self._fires_day: dict[str, date] = {}
        self._fires_count: dict[str, int] = {}
        self.failures: dict[str, int] = {}               # сбои по агентам, в памяти
        self.loop_failures = 0                           # сбои целых итераций петли

    @staticmethod
    def enabled() -> bool:
        """Default OFF: расписания исполняются только по явной воле владельца."""
        return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes")

    def stop(self) -> None:
        self._stopped = True

    def _max_for(self, name: str) -> int:
        return int(self.per_agent_max.get(name, self.max_fires_per_day))

    async def tick(self, now: datetime | None = None) -> list[int]:
        """Один проход по агентам; возвращает id поставленных задач.
        Сбой одного агента (кривой cron, недоступная БД) считается и НЕ
        мешает остальным."""
        now = now or datetime.now()
        fired: list[int] = []
        try:
            agents = self._load_agents()
        except Exception as exc:  # noqa: BLE001 — без агентов тик просто пуст
            self.loop_failures += 1
            _log.warning("расписания: агенты не загрузились: %s", exc)
            return fired
        for name, spec in agents.items():
            if not getattr(spec, "schedule", None):
                continue
            try:
                task_id = await self._maybe_fire(name, spec, now)
                if task_id is not None:
                    fired.append(task_id)
            except Exception as exc:  # noqa: BLE001 — один агент не роняет тик
                self.failures[name] = self.failures.get(name, 0) + 1
                _log.warning("расписание %s: сбой: %s", name, exc)
        return fired

    async def _maybe_fire(self, name: str, spec: Any, now: datetime) -> int | None:
        cron = parse_cron(spec.schedule)
        minute = now.replace(second=0, microsecond=0)
        if self._last_fire.get(name) == minute:
            return None                       # эта минута уже отработана
        if not cron_matches(cron, now):
            return None
        # суточный потолок — bounded by design, а не «сколько получится»
        if self._fires_day.get(name) != now.date():
            self._fires_day[name] = now.date()
            self._fires_count[name] = 0
        if self._fires_count.get(name, 0) >= self._max_for(name):
            _log.warning("расписание %s: достигнут max_fires_per_day=%s — пропуск",
                         name, self._max_for(name))
            return None
        # без перекрытий: предыдущая schedule-задача ещё в работе → пропуск
        if await self._fetchrow(_OVERLAP_SQL, name):
            _log.info("расписание %s: предыдущая задача ещё не завершена — пропуск", name)
            self._last_fire[name] = minute    # эту минуту не переигрываем
            return None
        # у AgentSpec нет отдельного текста плановой задачи — описанием служит
        # само расписание (реальный формат поля, см. agents.py)
        text = f"[schedule] {name}: плановый запуск по расписанию '{spec.schedule}'"
        row = await self._fetchrow(_INSERT_SQL, name, text)
        task_id = int(row["id"])
        self._last_fire[name] = minute
        self._fires_count[name] = self._fires_count.get(name, 0) + 1
        await self._enqueue(task_id)
        _log.info("расписание %s: задача #%s поставлена в очередь", name, task_id)
        return task_id

    async def loop(self) -> None:
        """Фоновая петля. Выключено (default) → немедленный выход, ядро как
        раньше. Каждая итерация в try/except: один сбой не убивает петлю."""
        if not self.enabled():
            _log.info("расписания выключены (%s не задан) — петля не запускается", ENABLE_ENV)
            return
        self._stopped = False
        while not self._stopped:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 — петля живёт дальше
                self.loop_failures += 1
                _log.warning("расписания: сбой итерации: %s", exc)
            await asyncio.sleep(self.tick_interval)
