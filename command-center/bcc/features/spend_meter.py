"""Spend Meter — учёт платных вызовов и предстартовая проверка, флаг OFF.

BOSSMAN_SPEND_METER_ENABLED=1 включает; иначе учёт не ведётся, допуск не
выдаётся, хук before_run — no-op (поведение системы ровно прежнее).

Почему не заводим свой журнал расхода. Расход уже пишет движок: одна строка
task_runs = один платный прогон, и в ней сразу есть все три оси —
model_alias (модель), tasks.mission_id (миссия), finished_at (сутки). Второй
учёт означал бы две суммы, которые рано или поздно разойдутся, поэтому
разрезы считаются из ОДНОЙ записи: строка попадает в бюджет миссии, модели и
дня одновременно, а не размножается тремя записями. Свой файл в data_dir
хранит только то, чего в схеме нет, — потолки владельца.

Чем этот модуль НЕ является. Это не жёсткий потолок, и называть его так было
бы неправдой. Он смотрит на уже потраченное и не даёт СТАРТОВАТЬ прогону,
если потолок выбран; денег внутри уже идущего прогона он не считает, поэтому
один прогон уходит за потолок ровно настолько, насколько успеет. Вдобавок его
потолки задаёт сам владелец через POST /spend/limit — ограничение, которое
снимается одним запросом, жёстким не бывает.

Жёсткий потолок платной работы Fable живёт отдельно и работает иначе: резерв
под худший случай снимается ДО сетевого вызова, в общем с bossman-core
durable-журнале, а сама величина (3.00 USD) — константа в коде, недоступная
ни этой ручке, ни переменной окружения, ни перезапуску. См. bcc/fable_cap.py.
Здешние потолки к ней не относятся и поднять её не могут: журнал потолка не
читает ни базу, ни spend_meter.json.

Что модуль всё-таки делает: считает расход по трём разрезам и отказывает в
старте на исчерпанном потолке (хук before_run возвращает {"fail": …}).

Потолок миссии по умолчанию берётся из существующего missions.cloud_budget_usd
(владелец уже задаёт его при создании миссии) — ручка нужна лишь чтобы
переопределить его или задать суточный, которого в схеме нет.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import missions as missions_t, task_runs as runs_t, tasks as tasks_t, utcnow
from . import Feature

FLAG = "BOSSMAN_SPEND_METER_ENABLED"
DAILY_ENV = "BOSSMAN_SPEND_METER_DAILY_USD"
MISSION_ENV = "BOSSMAN_SPEND_METER_MISSION_USD"
# Потолок без значения — не потолок, поэтому дефолты консервативные и ненулевые:
# включённый флаг обязан что-то ограничивать сам по себе.
DEFAULT_DAILY_USD = 10.0
DEFAULT_MISSION_USD = 5.0
STATE_FILE = "spend_meter.json"
# Деньги округляем до микроцента (как DirectApiBudget), сравниваем с допуском на
# двоичную погрешность: 0.30+0.30+0.30+0.10 обязано ровно упереться в 1.00.
EPS = 1e-9

router = APIRouter()


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


def _usd(value: Any) -> float:
    return round(float(value or 0.0), 6)


# ---------- потолки владельца (единственное состояние модуля) ----------

def _state_path(svc) -> Path:
    return Path(svc.settings.data_dir) / STATE_FILE


def _read_state(svc) -> dict:
    """Битый/отсутствующий файл — это дефолтные потолки, а не «без потолка»:
    порча состояния не имеет права ослабить ограничение."""
    path = _state_path(svc)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(svc, state: dict) -> None:
    path = _state_path(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)                    # подмена целиком: полуфайла не бывает


def _env_usd(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, round(float(raw), 6))
    except ValueError:
        return default


@dataclass
class Limits:
    """Потолки: суточный, дефолт на миссию и явные переопределения владельца."""
    daily_usd: float
    mission_default_usd: float
    per_mission: dict[int, float] = field(default_factory=dict)

    def for_mission(self, mission_id: int | None, cloud_budget_usd: float | None) -> float | None:
        if mission_id is None:
            return None                      # задача вне миссии ограничена только сутками
        if mission_id in self.per_mission:
            return self.per_mission[mission_id]
        budget = _usd(cloud_budget_usd)
        return budget if budget > 0 else self.mission_default_usd

    def payload(self) -> dict:
        return {"daily_usd": self.daily_usd, "mission_default_usd": self.mission_default_usd,
                "per_mission": {str(k): v for k, v in sorted(self.per_mission.items())}}


def _limits(svc) -> Limits:
    state = _read_state(svc)
    per_mission: dict[int, float] = {}
    for key, value in (state.get("per_mission") or {}).items():
        try:
            per_mission[int(key)] = max(0.0, _usd(value))
        except (TypeError, ValueError):
            continue                         # мусорный ключ игнорируем, дефолт остаётся в силе
    daily = state.get("daily_usd")
    mission_default = state.get("mission_default_usd")
    return Limits(
        daily_usd=max(0.0, _usd(daily)) if daily is not None
        else _env_usd(DAILY_ENV, DEFAULT_DAILY_USD),
        mission_default_usd=max(0.0, _usd(mission_default)) if mission_default is not None
        else _env_usd(MISSION_ENV, DEFAULT_MISSION_USD),
        per_mission=per_mission)


# ---------- разрезы расхода (читаются из task_runs) ----------

@dataclass
class Ledger:
    """Один и тот же прогон учтён во всех трёх разрезах, поэтому суммы по
    миссиям, моделям и дням сходятся между собой и с total_usd."""
    total_usd: float = 0.0
    entries: int = 0
    by_mission: dict[int | None, dict] = field(default_factory=dict)
    by_model: dict[str, dict] = field(default_factory=dict)
    by_day: dict[str, dict] = field(default_factory=dict)

    def add(self, mission_id: int | None, model: str, day: str, usd: float) -> None:
        for bucket, key in ((self.by_mission, mission_id), (self.by_model, model),
                            (self.by_day, day)):
            cell = bucket.setdefault(key, {"usd": 0.0, "entries": 0})
            cell["usd"] = round(cell["usd"] + usd, 6)
            cell["entries"] += 1
        self.total_usd = round(self.total_usd + usd, 6)
        self.entries += 1

    def mission_usd(self, mission_id: int | None) -> float:
        return _usd((self.by_mission.get(mission_id) or {}).get("usd"))

    def day_usd(self, day: str) -> float:
        return _usd((self.by_day.get(day) or {}).get("usd"))


def today() -> str:
    """Сутки — календарный день UTC: во всей системе время наивно-UTC (db.utcnow)."""
    return utcnow().date().isoformat()


async def _ledger(svc) -> Ledger:
    ledger = Ledger()
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(runs_t.c.cost_usd, runs_t.c.model_alias, runs_t.c.started_at,
                      runs_t.c.finished_at, tasks_t.c.mission_id)
            .join(tasks_t, tasks_t.c.id == runs_t.c.task_id)
            .where(runs_t.c.cost_usd > 0))).fetchall()
    for row in rows:
        r = row._mapping
        usd = _usd(r["cost_usd"])
        if usd <= 0:
            continue
        # незавершённый прогон уже стоил денег — относим его на день старта
        ts = r["finished_at"] or r["started_at"] or utcnow()
        ledger.add(r["mission_id"], r["model_alias"] or "unknown", ts.date().isoformat(), usd)
    return ledger


async def _mission_budgets(svc) -> dict[int, float]:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(missions_t.c.id,
                                          missions_t.c.cloud_budget_usd))).fetchall()
    return {int(r._mapping["id"]): _usd(r._mapping["cloud_budget_usd"]) for r in rows}


# ---------- допуск ----------

@dataclass
class LimitView:
    """Состояние одного потолка: сколько можно, сколько ушло, сколько осталось."""
    scope: str                               # daily | mission
    key: str
    limit_usd: float
    spent_usd: float

    @property
    def remaining_usd(self) -> float:
        # остаток может быть отрицательным: потолок могли понизить ниже потраченного,
        # и врать про «0» здесь нельзя — владелец должен видеть перерасход
        return round(self.limit_usd - self.spent_usd, 6)

    @property
    def exhausted(self) -> bool:
        return self.remaining_usd <= EPS

    @property
    def title(self) -> str:
        return "суточный потолок" if self.scope == "daily" else f"потолок миссии {self.key}"

    def payload(self, amount_usd: float = 0.0) -> dict:
        return {"scope": self.scope, "key": self.key, "limit_usd": self.limit_usd,
                "spent_usd": self.spent_usd, "remaining_usd": self.remaining_usd,
                "exhausted": self.exhausted,
                "remaining_after_usd": round(self.remaining_usd - amount_usd, 6)}


def views_for(ledger: Ledger, limits: Limits, *, mission_id: int | None,
              cloud_budget_usd: float | None, day: str | None = None) -> list[LimitView]:
    """Потолки, под которые попадает трата: сутки всегда, миссия — если известна."""
    day = day or today()
    out = [LimitView("daily", day, limits.daily_usd, ledger.day_usd(day))]
    mission_limit = limits.for_mission(mission_id, cloud_budget_usd)
    if mission_limit is not None:
        out.append(LimitView("mission", str(mission_id), mission_limit,
                             ledger.mission_usd(mission_id)))
    return out


def admit(views: list[LimitView], amount_usd: float) -> tuple[bool, LimitView | None, str]:
    """Ответ ДО траты. Блокирует первый потолок, которому не хватает остатка;
    «предупредить и пропустить» не предусмотрено — отказ окончателен."""
    for view in views:
        if view.remaining_usd + EPS < amount_usd:
            return False, view, (
                f"{view.title} исчерпан: лимит {view.limit_usd:.6f} USD, "
                f"потрачено {view.spent_usd:.6f}, остаток {view.remaining_usd:.6f}, "
                f"запрошено {amount_usd:.6f}")
    return True, None, "потолки позволяют трату"


def nearest(views: list[LimitView]) -> LimitView | None:
    return min(views, key=lambda v: v.remaining_usd) if views else None


# ---------- жёсткий стоп: прогон не стартует, если потолок исчерпан ----------

async def _before_run(svc):
    async def before_run(task, run):
        if not enabled():
            return None
        mission_id = task.get("mission_id")
        budgets = await _mission_budgets(svc)
        views = views_for(await _ledger(svc), _limits(svc), mission_id=mission_id,
                          cloud_budget_usd=budgets.get(mission_id))
        blocked = next((v for v in views if v.exhausted), None)
        if blocked is None:
            return None
        reason = (f"spend meter: {blocked.title} исчерпан "
                  f"({blocked.spent_usd:.6f} из {blocked.limit_usd:.6f} USD, "
                  f"остаток {blocked.remaining_usd:.6f}) — прогон не запускается")
        await svc.bus.emit("spend_meter.blocked", task_id=task["id"], scope=blocked.scope,
                           key=blocked.key, spent_usd=blocked.spent_usd,
                           limit_usd=blocked.limit_usd)
        return {"fail": reason}
    return before_run


# ---------- ручки ----------

def _off_payload() -> dict:
    return {"enabled": False, "spent": None, "limits": None,
            "note": f"учёт и допуск выключены: нет {FLAG}"}


@router.get("/spend")
async def spend(request: Request, mission_id: int | None = None):
    """Потрачено по каждому разрезу, потолки, остаток и ближайший к исчерпанию."""
    if not enabled():
        return _off_payload()
    svc = request.app.state.svc
    ledger, limits, budgets = await _ledger(svc), _limits(svc), await _mission_budgets(svc)
    day = today()

    by_mission = []
    known = set(ledger.by_mission) | set(limits.per_mission) | set(budgets)
    for mid in sorted(known, key=lambda x: (x is None, x if x is not None else 0)):
        cell = ledger.by_mission.get(mid) or {"usd": 0.0, "entries": 0}
        limit = limits.for_mission(mid, budgets.get(mid) if mid is not None else None)
        item = {"mission_id": mid, "spent_usd": _usd(cell["usd"]), "entries": cell["entries"],
                "limit_usd": limit, "remaining_usd": None}
        if limit is not None:
            item["remaining_usd"] = round(limit - _usd(cell["usd"]), 6)
        by_mission.append(item)

    # общий обзор: все суточные и миссийные потолки разом, чтобы найти ближайший
    all_views = [LimitView("daily", day, limits.daily_usd, ledger.day_usd(day))]
    all_views += [LimitView("mission", str(i["mission_id"]), i["limit_usd"], i["spent_usd"])
                  for i in by_mission if i["limit_usd"] is not None]
    focus = None
    if mission_id is not None:
        focus = [v.payload() for v in views_for(ledger, limits, mission_id=mission_id,
                                                cloud_budget_usd=budgets.get(mission_id),
                                                day=day)]
    closest = nearest(all_views)
    return {
        "enabled": True,
        "day": day,
        "source": "task_runs.cost_usd (учёт движка; второго журнала нет)",
        "limits": limits.payload(),
        "spent": {
            "total_usd": ledger.total_usd,
            "entries": ledger.entries,
            "today_usd": ledger.day_usd(day),
            "by_mission": by_mission,
            "by_model": [{"model": k, "spent_usd": _usd(v["usd"]), "entries": v["entries"]}
                         for k, v in sorted(ledger.by_model.items())],
            "by_day": [{"day": k, "spent_usd": _usd(v["usd"]), "entries": v["entries"]}
                       for k, v in sorted(ledger.by_day.items(), reverse=True)],
        },
        "limits_state": [v.payload() for v in all_views],
        "nearest_limit": closest.payload() if closest else None,
        "mission": focus,
    }


@router.post("/spend/limit")
async def set_limit(request: Request):
    """Изменение потолка — единственная операция, меняющая состояние модуля."""
    if not enabled():
        raise HTTPException(409, {"message": f"spend meter выключен: нет {FLAG}",
                                  "enabled": False})
    svc = request.app.state.svc
    body = await request.json()
    scope = str(body.get("scope") or "").strip()
    if scope not in ("daily", "mission"):
        raise HTTPException(422, {"message": "scope должен быть daily или mission"})
    if "limit_usd" not in body:
        raise HTTPException(422, {"message": "нужно limit_usd"})
    try:
        limit_usd = round(float(body["limit_usd"]), 6)
    except (TypeError, ValueError):
        raise HTTPException(422, {"message": "limit_usd должен быть числом"})
    if limit_usd < 0:
        raise HTTPException(422, {"message": "limit_usd не может быть отрицательным"})

    state = _read_state(svc)
    mission_id = body.get("mission_id")
    if scope == "daily":
        state["daily_usd"] = limit_usd
    elif mission_id is None:
        state["mission_default_usd"] = limit_usd
    else:
        try:
            mission_id = int(mission_id)
        except (TypeError, ValueError):
            raise HTTPException(422, {"message": "mission_id должен быть числом"})
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(missions_t.c.id)
                                   .where(missions_t.c.id == mission_id))).first()
        if row is None:
            raise HTTPException(404, {"message": f"миссия {mission_id} не найдена"})
        per = dict(state.get("per_mission") or {})
        per[str(mission_id)] = limit_usd
        state["per_mission"] = per
    state["updated_at"] = utcnow().isoformat()
    _write_state(svc, state)
    await svc.bus.emit("spend_meter.limit_changed", scope=scope, mission_id=mission_id,
                       limit_usd=limit_usd)
    return {"enabled": True, "scope": scope, "mission_id": mission_id,
            "limit_usd": limit_usd, "limits": _limits(svc).payload()}


@router.post("/spend/check")
async def check(request: Request):
    """Допуск на сумму ДО траты: ничего не списывает, только отвечает и прогнозирует."""
    if not enabled():
        # допуск не выдаётся при выключенном флаге: разрешать «на всякий случай» нельзя
        return {"enabled": False, "allowed": False, "reason": f"spend meter выключен: нет {FLAG}",
                "limits": None}
    svc = request.app.state.svc
    body = await request.json()
    try:
        amount_usd = round(float(body.get("amount_usd")), 6)
    except (TypeError, ValueError):
        raise HTTPException(422, {"message": "нужно amount_usd (число USD)"})
    if amount_usd <= 0:
        raise HTTPException(422, {"message": "amount_usd должен быть больше нуля"})
    mission_id = body.get("mission_id")
    try:
        mission_id = int(mission_id) if mission_id is not None else None
    except (TypeError, ValueError):
        raise HTTPException(422, {"message": "mission_id должен быть числом"})

    budgets = await _mission_budgets(svc)
    views = views_for(await _ledger(svc), _limits(svc), mission_id=mission_id,
                      cloud_budget_usd=budgets.get(mission_id))
    allowed, blocking, reason = admit(views, amount_usd)
    closest = nearest(views)
    return {"enabled": True, "allowed": allowed, "reason": reason,
            "amount_usd": amount_usd, "mission_id": mission_id,
            "limits": [v.payload(amount_usd) for v in views],
            "blocking": blocking.payload(amount_usd) if blocking else None,
            "nearest_limit": closest.payload(amount_usd) if closest else None}


async def _setup(svc):
    svc.engine.add_hook("before_run", await _before_run(svc))


FEATURE = Feature(name="spend_meter", router=router, setup=_setup)
