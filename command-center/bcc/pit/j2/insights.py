"""Jeff 2.0 module ``insights`` (order 95): the owner's overview of Jeff.

It reads what already exists (the persona vault, the Master Parser narratives, the heartbeat files, the route log,
the task store and the proactive / quality stores) and answers five owner questions:

* who talks to Jeff (participants table: consent flags, counts, last activity, tasks, reminders, quality);
* what Master Parser 2.0 wrote about them (narratives: PATHS in lists; the text only through ``narrative_text``,
  which the owner-only API exposes);
* is Jeff healthy (heartbeats, queue, model_guard status when present, module breakers, recent error kinds);
* how is it trending (daily series: replies, success rate, latency, quality, tasks done/failed);
* what happened this week (a short Russian digest, delivered to the "Pult" once per ISO week).

No second database: the only files it writes are derived caches under ``<pit>/insights`` and the module status
snapshot ``<pit>/j2-status.json``.

Privacy stance: every list and number that leaves this module is a count, a label or a path. Labels come from the
owner API (display names live only in owner-only responses); the weekly digest uses neutral ``#a1b2c3`` labels. No
message text, no fact text, no narrative text, no Telegram ids and no absolute paths appear in any list, status
or digest. The module has no turn hooks: it never sees or changes a conversation.

Status keys: ``name, version, running, errors, last_snapshot_at, last_digest_week``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..identity import validate_person_key
from ..vault import _append_jsonl, _atomic_json
from .contract import BaseModule
from .proactive import ProactiveStore
from .quality_lab import QualityStore

log = logging.getLogger("bcc.pit.j2.insights")

SCHEMA = "jeff.insights/1"
STATUS_FILE = "j2-status.json"
STATUS_SCHEMA = "jeff.j2-status/1"
STALE_AFTER_S = 60
QUEUE_WARN = 10
MAX_PARTICIPANTS = 200
MAX_TREND_DAYS = 90
DEFAULT_TREND_DAYS = 14
TAIL_BYTES = 3_000_000
DIGEST_WEEKDAY = 0                      # Monday
DIGEST_HOUR = 9
DAY = 86400.0
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря")
PultSender = Callable[[str], Awaitable[Any]]


# ---------------------------------------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tail_rows(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            raw = handle.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", "replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]                               # the first line may be cut in half
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _count_lines(path: Path, cap: int = 200_000) -> int:
    n = 0
    try:
        with path.open("rb") as handle:
            for _ in handle:
                n += 1
                if n >= cap:
                    break
    except OSError:
        return 0
    return n


def _parse_iso(value: Any) -> float | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _ru_date(ts: float) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _num(value: float | None) -> str:
    return "нет данных" if value is None else f"{value:.2f}".replace(".", ",")


def _sec(ms: float | None) -> str:
    return "нет данных" if ms is None else f"{ms / 1000:.1f}".replace(".", ",") + " с"


def _scalars(row: dict[str, Any]) -> dict[str, Any]:
    """Only short scalars survive: statuses must stay counts and flags, never text or nested payloads."""
    out: dict[str, Any] = {}
    for name, value in row.items():
        if isinstance(value, dict) and name == "calls":
            for k, v in value.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[f"calls_{k}"] = v
        elif isinstance(value, bool) or (isinstance(value, (int, float))):
            out[name] = value
        elif isinstance(value, str) and len(value) <= 40:
            out[name] = value
    return out


def write_status_snapshot(pit_home: Path | str, modules: list[dict[str, Any]], now: float) -> None:
    """Persist the Jeff process' module statuses so the Command Center process can show them."""
    payload = {"schema": STATUS_SCHEMA, "at": float(now), "modules": [_scalars(dict(m)) for m in modules]}
    _atomic_json(Path(pit_home) / STATUS_FILE, payload)


def read_status_snapshot(pit_home: Path | str) -> dict[str, Any] | None:
    data = _read_json(Path(pit_home) / STATUS_FILE)
    return data if isinstance(data, dict) and isinstance(data.get("modules"), list) else None


# ---------------------------------------------------------------------------------------------------------
# collector
# ---------------------------------------------------------------------------------------------------------
class InsightsCollector:
    def __init__(self, pit_home: Path | str, *, clock: Callable[[], float] = time.time):
        self.pit = Path(pit_home)
        self.clock = clock

    # -- participants ------------------------------------------------------------------------------------
    @property
    def _people_root(self) -> Path:
        return self.pit / "personalities"

    def _keys(self) -> list[str]:
        try:
            names = sorted(p.name for p in self._people_root.iterdir() if p.is_dir())
        except OSError:
            return []
        keys = []
        for name in names:
            try:
                keys.append(validate_person_key(name))
            except ValueError:
                continue
        return keys

    @staticmethod
    def _label(key: str, labels: dict[str, str] | None) -> str:
        return (labels or {}).get(key) or f"Участник {key[:6]}"

    def _narrative_path(self, key: str) -> Path:
        return self.pit / "passport-checkpoints" / "narratives" / f"{key}.json"

    def participants(self, labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
        now = self.clock()
        tasks = None
        with contextlib.suppress(Exception):
            from ..tasks import TaskStore
            tasks = TaskStore(self.pit)
        proactive = ProactiveStore(self._people_root)
        quality = QualityStore(self._people_root)
        rows = []
        for key in self._keys()[:MAX_PARTICIPANTS]:
            base = self._people_root / key
            consent = _read_json(base / "consent.json") or {}
            raw = base / "raw" / "events.jsonl"
            facts = base / "facts.jsonl"
            last = None
            for path in (raw, facts):                   # the newest conversation file wins, facts are the fallback
                with contextlib.suppress(OSError):
                    last = path.stat().st_mtime
                    break
            counts: dict[str, int] = {}
            if tasks is not None:
                with contextlib.suppress(Exception):
                    for task in tasks.list(key):
                        counts[task["state"]] = counts.get(task["state"], 0) + 1
            reminders = 0
            with contextlib.suppress(Exception):
                reminders = len(proactive.items(key, states=("pending",), kinds=("reminder",)))
            q = {"n": 0, "overall": None}
            with contextlib.suppress(Exception):
                summary = quality.summary(key)
                q = {"n": summary["n"], "overall": summary["overall"]}
            access = "active"
            with contextlib.suppress(Exception):
                from .. import participant_profile
                access = participant_profile.read_profile(self.pit.parent, key)[0]["access"]
            rows.append({
                "key": key, "label": self._label(key, labels), "access": access,
                "consent": {"memory": bool(consent.get("memory_enabled")),
                            "raw_history": bool(consent.get("raw_history_enabled")),
                            "personalization": bool(consent.get("personalization_enabled", True))},
                "facts": _count_lines(facts), "messages": _count_lines(raw),
                "last_activity": _iso(last), "active_7d": bool(last is not None and now - last <= 7 * DAY),
                "tasks": counts, "reminders_pending": reminders, "quality": q,
                "narrative": self._narrative_path(key).is_file()})
        rows.sort(key=lambda r: (r["last_activity"] or "", r["key"]), reverse=True)
        return rows

    # -- narratives ------------------------------------------------------------------------------------------
    def narratives(self, labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
        folder = self.pit / "passport-checkpoints" / "narratives"
        try:
            names = sorted(p.name for p in folder.glob("*.json"))
        except OSError:
            return []
        rows = []
        for name in names[:MAX_PARTICIPANTS]:
            try:
                key = validate_person_key(name[:-5])
            except ValueError:
                continue
            data = _read_json(folder / name)
            if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), dict):
                continue
            rows.append({"person_key": key, "label": self._label(key, labels),
                         "path": f"passport-checkpoints/narratives/{key}.json",
                         "created_at": data.get("created_at"), "run_id": str(data.get("run_id", ""))[:40],
                         "model": str(data.get("model", ""))[:60], "status": str(data.get("status", ""))[:20]})
        return rows

    def narrative_text(self, person_key: str) -> dict[str, Any] | None:
        """The narrative text itself. Owner-only callers only: never put this into a list, status or digest."""
        key = validate_person_key(person_key)
        data = _read_json(self._narrative_path(key))
        if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), dict):
            return None
        paragraphs = data["paragraphs"]
        return {"person_key": key, "context": str(paragraphs.get("context", "")),
                "personality": str(paragraphs.get("personality", "")), "created_at": data.get("created_at"),
                "model": str(data.get("model", ""))[:60], "run_id": str(data.get("run_id", ""))[:40]}

    # -- health ------------------------------------------------------------------------------------------------
    def _heartbeat(self, path: Path, now: float) -> dict[str, Any]:
        data = _read_json(path)
        if not isinstance(data, dict):
            return {"availability": "absent"}
        age = max(0, int(now - float(data.get("at_epoch") or 0)))
        state = str(data.get("state", ""))
        availability = "stopped" if state == "stopped" else ("up" if age <= STALE_AFTER_S else "stale")
        out = {"availability": availability, "age_s": age, "state": state[:20]}
        for name in ("replies_ok", "replies_failed", "avg_latency_ms", "last_reply_latency_ms", "jeff_version"):
            if name in data and isinstance(data[name], (int, float, str)):
                out[name] = data[name]
        return out

    def _queue(self) -> int:
        db_path = self.pit / "companion.sqlite3"
        if not db_path.is_file():
            return 0
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            try:
                return int(db.execute("SELECT count(*) FROM inbox WHERE phase IN ('pending','processing')")
                           .fetchone()[0])
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            return -1

    def health(self) -> dict[str, Any]:
        now = self.clock()
        heartbeat = {"telegram": self._heartbeat(self.pit / "heartbeat.json", now),
                     "window": self._heartbeat(self.pit / "web" / "heartbeat.json", now)}
        queue = self._queue()
        snapshot = read_status_snapshot(self.pit)
        modules, guard = [], {"present": False}
        snapshot_age = None
        if snapshot:
            snapshot_age = max(0, int(now - float(snapshot.get("at", now))))
            for row in snapshot["modules"]:
                if not isinstance(row, dict) or "name" not in row:
                    continue
                modules.append({"name": row["name"], "breaker_open": bool(row.get("breaker_open")),
                                "errors": row.get("calls_error", 0), "timeouts": row.get("calls_timeout", 0)})
                if row["name"] == "model_guard":
                    state = str(row.get("state", ""))
                    healthy = not row.get("breaker_open") and state not in ("degraded", "down", "restarting", "failed")
                    guard = {"present": True, "healthy": healthy, "status": row}
        errors = _tail_rows(self.pit / "logs" / "runtime_error.jsonl", 300_000)[-200:]
        kinds: dict[str, int] = {}
        for row in errors:
            kind = str(row.get("kind", "unknown"))[:40]
            kinds[kind] = kinds.get(kind, 0) + 1
        task_counts: dict[str, int] = {}
        with contextlib.suppress(Exception):
            from ..tasks import TaskStore
            task_counts = {k: v for k, v in TaskStore(self.pit).summary().items() if v}
        ok = (heartbeat["telegram"]["availability"] == "up" and 0 <= queue <= QUEUE_WARN
              and (not guard["present"] or bool(guard["healthy"])))
        return {"heartbeat": heartbeat, "queue": queue, "model_guard": guard, "modules": modules,
                "snapshot_age_s": snapshot_age, "tasks": task_counts,
                "recent_errors": {"count": len(errors), "kinds": kinds}, "ok": ok}

    # -- trends --------------------------------------------------------------------------------------------------
    def trends(self, days: int = DEFAULT_TREND_DAYS) -> dict[str, Any]:
        days = max(1, min(MAX_TREND_DAYS, int(days)))
        now = self.clock()
        today = datetime.fromtimestamp(now, timezone.utc).date()
        labels = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
        index = {d: i for i, d in enumerate(labels)}
        replies = [[0, 0, []] for _ in labels]           # rows, ok, ok latencies
        quality: list[list[float]] = [[] for _ in labels]
        done = [0] * len(labels)
        failed = [0] * len(labels)

        def slot(ts: float | None) -> int | None:
            return None if ts is None else index.get(datetime.fromtimestamp(ts, timezone.utc).date().isoformat())

        for row in _tail_rows(self.pit / "logs" / "route_log.jsonl"):
            i = slot(_parse_iso(row.get("at")))
            if i is None:
                continue
            replies[i][0] += 1
            if row.get("ok"):
                replies[i][1] += 1
                latency = row.get("latency_ms")
                if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0:
                    replies[i][2].append(latency)
        for key in QualityStore(self._people_root).participants():
            for row in QualityStore(self._people_root).rows(key):
                i = slot(row.get("ts") if isinstance(row.get("ts"), (int, float)) else None)
                if i is not None and row.get("overall") is not None:
                    quality[i].append(row["overall"])
        tasks_root = self.pit / "tasks"
        with contextlib.suppress(OSError):
            for owner in sorted(p for p in tasks_root.iterdir() if p.is_dir()):
                for row in _tail_rows(owner / "events.jsonl", 500_000):
                    if row.get("action") != "state":
                        continue
                    i = slot(_parse_iso(row.get("at")))
                    if i is None:
                        continue
                    if row.get("state") == "DONE":
                        done[i] += 1
                    elif row.get("state") == "FAILED":
                        failed[i] += 1
        series = {
            "replies": [r[0] or None for r in replies],
            "ok_rate": [round(r[1] / r[0], 4) if r[0] else None for r in replies],
            "avg_latency_ms": [round(sum(r[2]) / len(r[2])) if r[2] else None for r in replies],
            "quality": [_mean(q) for q in quality],
            "tasks_done": [n or None for n in done],
            "tasks_failed": [n or None for n in failed]}
        return {"days": labels, "series": series}

    # -- overview -------------------------------------------------------------------------------------------------
    def overview(self, labels: dict[str, str] | None = None) -> dict[str, Any]:
        return {"schema": SCHEMA, "generated_at": _iso(self.clock()), "participants": self.participants(labels),
                "narratives": self.narratives(labels), "health": self.health(), "trends": self.trends()}

    # -- weekly digest -----------------------------------------------------------------------------------------------
    def _week_label(self, now: float) -> str:
        iso = datetime.fromtimestamp(now, timezone.utc).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def _route_window(self, start: float, end: float) -> tuple[int, float | None, float | None]:
        rows = ok = 0
        lat: list[float] = []
        for row in _tail_rows(self.pit / "logs" / "route_log.jsonl"):
            ts = _parse_iso(row.get("at"))
            if ts is None or not start < ts <= end:
                continue
            rows += 1
            if row.get("ok"):
                ok += 1
                latency = row.get("latency_ms")
                if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0:
                    lat.append(latency)
        return rows, (round(ok / rows, 4) if rows else None), (round(sum(lat) / len(lat)) if lat else None)

    def weekly_digest(self) -> dict[str, Any]:
        now = self.clock()
        week_start, prev_start = now - 7 * DAY, now - 14 * DAY
        people = self.participants()
        r_now, ok_now, lat_now = self._route_window(week_start, now)
        r_prev, ok_prev, lat_prev = self._route_window(prev_start, week_start)
        q_now: list[float] = []
        q_prev: list[float] = []
        store = QualityStore(self._people_root)
        for key in store.participants():
            for row in store.rows(key):
                ts, overall = row.get("ts"), row.get("overall")
                if not isinstance(ts, (int, float)) or overall is None:
                    continue
                if week_start < ts <= now:
                    q_now.append(overall)
                elif prev_start < ts <= week_start:
                    q_prev.append(overall)
        trend = self.trends(7)
        done = sum(n or 0 for n in trend["series"]["tasks_done"])
        failed = sum(n or 0 for n in trend["series"]["tasks_failed"])
        health = self.health()
        waiting = sum(v for k, v in health["tasks"].items() if k in ("WAITING_INPUT", "WAITING_APPROVAL",
                                                                     "UNKNOWN_OUTCOME"))
        facts = {"participants": len(people), "active_7d": sum(1 for p in people if p["active_7d"]),
                 "replies": {"this": r_now, "prev": r_prev}, "ok_rate": {"this": ok_now, "prev": ok_prev},
                 "avg_latency_ms": {"this": lat_now, "prev": lat_prev},
                 "quality": {"this": _mean(q_now), "prev": _mean(q_prev), "n": len(q_now)},
                 "tasks": {"done": done, "failed": failed, "waiting": waiting}, "queue": health["queue"]}
        attention = []
        tg = health["heartbeat"]["telegram"]
        if tg["availability"] != "up":
            attention.append(f"нет свежего heartbeat Telegram-Jeff (состояние: {tg['availability']})")
        if health["queue"] < 0:
            attention.append("очередь сообщений не читается")
        elif health["queue"] > QUEUE_WARN:
            attention.append(f"очередь ожидания: {health['queue']}")
        if health["model_guard"]["present"] and not health["model_guard"]["healthy"]:
            attention.append("model_guard сообщает о проблеме с локальной моделью")
        if health["recent_errors"]["count"]:
            attention.append(f"ошибок в журнале: {health['recent_errors']['count']}")
        for person in people:
            if person["quality"]["n"] >= 5 and (person["quality"]["overall"] or 1) < 0.5:
                attention.append(f"низкое качество у участника #{person['key'][:6]}: "
                                 f"{_num(person['quality']['overall'])}")
        week = self._week_label(now)
        lines = [f"Jeff · неделя {week} ({_ru_date(week_start)} — {_ru_date(now)})"]
        if not facts["participants"] and not r_now and not r_prev:
            lines.append("Данных пока нет: участников и ответов не найдено.")
        else:
            lines.append(f"Участников: {facts['participants']}, активных за 7 дней: {facts['active_7d']}")
            pct = "нет данных" if ok_now is None else f"{round(ok_now * 100)}%"
            lines.append(f"Ответов за неделю: {r_now} (неделей раньше: {r_prev}); успешных: {pct}")
            lines.append(f"Средняя задержка: {_sec(lat_now)} (неделей раньше: {_sec(lat_prev)})")
            lines.append(f"Качество ответов (проверки): {_num(facts['quality']['this'])} по {len(q_now)} ответам "
                         f"(неделей раньше: {_num(facts['quality']['prev'])})")
            lines.append(f"Задачи: выполнено {done}, не удалось {failed}; ждут ответа или подтверждения: {waiting}")
        lines.append(f"Здоровье: Telegram — {tg['availability']}; очередь: {health['queue']}")
        if attention:
            lines.append("Внимание:")
            lines += [f"- {item}" for item in attention]
        return {"week": week, "text": "\n".join(lines), "facts": facts}

    # -- delivery to the Pult --------------------------------------------------------------------------------------------
    def _state_path(self) -> Path:
        return self.pit / "insights" / "state.json"

    def last_digest_week(self) -> str | None:
        data = _read_json(self._state_path())
        return data.get("last_digest_week") if isinstance(data, dict) else None

    async def deliver_digest(self, pult_sender: PultSender | None = None, *, force: bool = False) -> dict[str, Any]:
        digest = self.weekly_digest()
        week, text = digest["week"], digest["text"]
        if not force and self.last_digest_week() == week:
            return {"week": week, "delivered": False, "already": True}
        folder = self.pit / "insights"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "weekly").mkdir(exist_ok=True)
        (folder / "weekly" / f"{week}.txt").write_text(text + "\n", encoding="utf-8", newline="\n")
        error = None
        try:
            if pult_sender is None:
                _append_jsonl(folder / "pult-outbox.jsonl", {"at": _iso(self.clock()), "kind": "weekly_digest",
                                                             "week": week, "text": text})
                delivered = True
            else:
                delivered = bool(await pult_sender(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:                        # noqa: BLE001 - a Pult fault leaves the digest pending
            delivered, error = False, type(exc).__name__
        if delivered:
            _atomic_json(self._state_path(), {"last_digest_week": week, "delivered_at": _iso(self.clock())})
        result: dict[str, Any] = {"week": week, "delivered": delivered, "already": False}
        if error:
            result["error"] = error
        return result


# ---------------------------------------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------------------------------------
class InsightsModule(BaseModule):
    name = "insights"
    version = "1"
    order = 95

    def __init__(self, collector: InsightsCollector, *, status_provider: Callable[[], dict[str, Any]] | None = None,
                 pult_sender: PultSender | None = None, clock: Callable[[], float] = time.time,
                 tz_offset_min: int = 180, interval: float = 60.0,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.collector = collector
        self._provider = status_provider
        self._pult = pult_sender
        self._clock = clock
        self._tz = tz_offset_min
        self._interval = interval
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self.errors = 0
        self.last_snapshot_at: float | None = None

    def _digest_due(self, now: float) -> bool:
        local = datetime.fromtimestamp(now, timezone(timedelta(minutes=self._tz)))
        return (local.weekday() == DIGEST_WEEKDAY and local.hour >= DIGEST_HOUR
                and self.collector.last_digest_week() != self.collector._week_label(now))

    async def tick(self) -> None:
        now = self._clock()
        if self._provider is not None:
            try:
                write_status_snapshot(self.collector.pit, list(self._provider().get("modules", [])), now)
                self.last_snapshot_at = now
            except Exception as exc:                    # noqa: BLE001
                self.errors += 1
                log.warning("insights snapshot failed: %s", type(exc).__name__)
        try:
            if self._digest_due(now):
                await self.collector.deliver_digest(self._pult)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                        # noqa: BLE001
            self.errors += 1
            log.warning("insights digest failed: %s", type(exc).__name__)

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
            await self.tick()
            await self._sleep(self._interval)

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "running": self._task is not None and not self._task.done(), "errors": self.errors,
                "last_snapshot_at": self.last_snapshot_at, "last_digest_week": self.collector.last_digest_week()}


def create(runtime: Any) -> InsightsModule:
    home = getattr(runtime, "home", None)
    if home is None:
        raise ValueError("insights needs a runtime with a PIT home")
    provider = (lambda: runtime.j2.status()) if hasattr(type(runtime), "j2") else None
    try:
        tz = int(os.environ.get("BOSSMAN_JEFF_TZ_MIN", 180))
    except ValueError:
        tz = 180
    return InsightsModule(InsightsCollector(Path(home)), status_provider=provider, tz_offset_min=tz)
