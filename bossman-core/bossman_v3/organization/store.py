"""Durable organization state (§17, §19) — stdlib SQLite, без сервера и шины.

Что хранится: отделы, агенты, миссии, команды, контракты, результаты, улики,
конверты казначейства, статистика обучения, знания по скоупам, принятые события.
Чего НЕ хранится: секретов и скрытых рассуждений моделей — только операционные
метаданные и ссылки на улики (SECURITY §6).

Одна база на организацию; `path=":memory:"` для тестов недопустим, т.к. каждое
соединение SQLite in-memory — своя база; тесты используют tmp_path.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import DelegationContract
from .models import AgentProfile, Department, MissionState, Resources, TaskState, WorkResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_departments (
  department_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_agents (
  agent_id TEXT PRIMARY KEY, department_id TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_missions (
  mission_id TEXT PRIMARY KEY, title TEXT NOT NULL, department_id TEXT NOT NULL, state TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_teams (
  team_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL, dissolved INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_work (
  work_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, department_id TEXT NOT NULL, digest TEXT NOT NULL,
  state TEXT NOT NULL, assigned TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
  payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_results (
  work_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, verified INTEGER NOT NULL, payload TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_treasury (
  scope TEXT PRIMARY KEY, limit_json TEXT NOT NULL, spent_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_learning (
  agent_id TEXT NOT NULL, capability TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (agent_id, capability));
CREATE TABLE IF NOT EXISTS org_knowledge (
  fact_id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS org_knowledge_scope ON org_knowledge(scope);
CREATE TABLE IF NOT EXISTS org_events (
  event_key TEXT PRIMARY KEY, kind TEXT NOT NULL, outcome TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, mission_id TEXT NOT NULL DEFAULT '',
  work_id TEXT NOT NULL DEFAULT '', event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


class OrganizationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use a file path: sqlite :memory: is per-connection and cannot survive restart")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _rows(self, sql: str, args: tuple = ()) -> Iterator[sqlite3.Row]:
        with self._connect() as con:
            yield from con.execute(sql, args).fetchall()

    # -------------------------------------------------------- departments

    def save_department(self, d: Department) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_departments(department_id,payload,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(department_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                        (d.department_id, _dumps(d.to_dict()), _now()))

    def departments(self) -> list[Department]:
        return [Department.from_dict(json.loads(r["payload"]))
                for r in self._rows("SELECT payload FROM org_departments ORDER BY department_id")]

    # ------------------------------------------------------------- agents

    def save_agent(self, a: AgentProfile) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_agents(agent_id,department_id,payload,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(agent_id) DO UPDATE SET department_id=excluded.department_id, "
                        "payload=excluded.payload, updated_at=excluded.updated_at",
                        (a.agent_id, a.department_id, _dumps(a.to_dict()), _now()))

    def agents(self) -> list[AgentProfile]:
        return [AgentProfile.from_dict(json.loads(r["payload"]))
                for r in self._rows("SELECT payload FROM org_agents ORDER BY agent_id")]

    # ----------------------------------------------------------- missions

    def save_mission(self, mission_id: str, *, title: str, department_id: str, state: MissionState,
                     source: str = "", payload: dict[str, Any] | None = None) -> None:
        now = _now()
        with self._connect() as con:
            con.execute("INSERT INTO org_missions(mission_id,title,department_id,state,source,payload,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(mission_id) DO UPDATE SET title=excluded.title, "
                        "department_id=excluded.department_id, state=excluded.state, source=excluded.source, "
                        "payload=excluded.payload, updated_at=excluded.updated_at",
                        (mission_id, title, department_id, state.value, source, _dumps(payload or {}), now, now))

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        for r in self._rows("SELECT * FROM org_missions WHERE mission_id=?", (mission_id,)):
            return _mission_row(r)
        return None

    def missions(self, *, state: MissionState | None = None) -> list[dict[str, Any]]:
        if state is None:
            rows = self._rows("SELECT * FROM org_missions ORDER BY created_at, mission_id")
        else:
            rows = self._rows("SELECT * FROM org_missions WHERE state=? ORDER BY created_at, mission_id", (state.value,))
        return [_mission_row(r) for r in rows]

    # -------------------------------------------------------------- teams

    def save_team(self, team_id: str, mission_id: str, payload: dict[str, Any], *, dissolved: bool = False) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_teams(team_id,mission_id,payload,dissolved,updated_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(team_id) DO UPDATE SET payload=excluded.payload, dissolved=excluded.dissolved, "
                        "updated_at=excluded.updated_at",
                        (team_id, mission_id, _dumps(payload), int(dissolved), _now()))

    def teams(self, mission_id: str | None = None, *, include_dissolved: bool = True) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM org_teams", []
        cond = []
        if mission_id is not None:
            cond.append("mission_id=?"); args.append(mission_id)
        if not include_dissolved:
            cond.append("dissolved=0")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        return [{"team_id": r["team_id"], "mission_id": r["mission_id"], "dissolved": bool(r["dissolved"]),
                 **json.loads(r["payload"])} for r in self._rows(sql + " ORDER BY team_id", tuple(args))]

    # --------------------------------------------------------------- work

    def save_work(self, c: DelegationContract, *, state: TaskState, assigned: list[str] | None = None,
                  attempts: int | None = None) -> None:
        with self._connect() as con:
            cur = con.execute("SELECT assigned, attempts FROM org_work WHERE work_id=?", (c.work_id,)).fetchone()
            prev_assigned = json.loads(cur["assigned"]) if cur else []
            prev_attempts = int(cur["attempts"]) if cur else 0
            con.execute("INSERT INTO org_work(work_id,mission_id,department_id,digest,state,assigned,attempts,payload,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET state=excluded.state, "
                        "assigned=excluded.assigned, attempts=excluded.attempts, payload=excluded.payload, "
                        "digest=excluded.digest, updated_at=excluded.updated_at",
                        (c.work_id, c.mission_id, c.department_id, c.digest(), state.value,
                         _dumps(assigned if assigned is not None else prev_assigned),
                         attempts if attempts is not None else prev_attempts, _dumps(c.to_dict()), _now()))

    def work(self, work_id: str) -> dict[str, Any] | None:
        for r in self._rows("SELECT * FROM org_work WHERE work_id=?", (work_id,)):
            return _work_row(r)
        return None

    def works(self, mission_id: str | None = None, *, state: TaskState | None = None) -> list[dict[str, Any]]:
        sql, args, cond = "SELECT * FROM org_work", [], []
        if mission_id is not None:
            cond.append("mission_id=?"); args.append(mission_id)
        if state is not None:
            cond.append("state=?"); args.append(state.value)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        return [_work_row(r) for r in self._rows(sql + " ORDER BY work_id", tuple(args))]

    # ------------------------------------------------------------ results

    def save_result(self, r: WorkResult, mission_id: str) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_results(work_id,mission_id,verified,payload,updated_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(work_id) DO UPDATE SET verified=excluded.verified, payload=excluded.payload, "
                        "updated_at=excluded.updated_at",
                        (r.work_id, mission_id, int(r.verified), _dumps(r.to_dict()), _now()))

    def result(self, work_id: str) -> WorkResult | None:
        for r in self._rows("SELECT payload FROM org_results WHERE work_id=?", (work_id,)):
            return WorkResult.from_dict(json.loads(r["payload"]))
        return None

    def results(self, mission_id: str | None = None) -> list[WorkResult]:
        if mission_id is None:
            rows = self._rows("SELECT payload FROM org_results ORDER BY work_id")
        else:
            rows = self._rows("SELECT payload FROM org_results WHERE mission_id=? ORDER BY work_id", (mission_id,))
        return [WorkResult.from_dict(json.loads(r["payload"])) for r in rows]

    # ----------------------------------------------------------- treasury

    def save_envelope(self, scope: str, *, limit: Resources, spent: Resources) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_treasury(scope,limit_json,spent_json,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(scope) DO UPDATE SET limit_json=excluded.limit_json, spent_json=excluded.spent_json, "
                        "updated_at=excluded.updated_at",
                        (scope, _dumps(limit.to_dict()), _dumps(spent.to_dict()), _now()))

    def envelopes(self) -> dict[str, tuple[Resources, Resources]]:
        return {r["scope"]: (Resources.from_dict(json.loads(r["limit_json"])),
                             Resources.from_dict(json.loads(r["spent_json"])))
                for r in self._rows("SELECT * FROM org_treasury ORDER BY scope")}

    # ----------------------------------------------------------- learning

    def save_learning(self, agent_id: str, capability: str, payload: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_learning(agent_id,capability,payload,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(agent_id,capability) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                        (agent_id, capability, _dumps(payload), _now()))

    def learning(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [(r["agent_id"], r["capability"], json.loads(r["payload"]))
                for r in self._rows("SELECT * FROM org_learning ORDER BY agent_id, capability")]

    # ---------------------------------------------------------- knowledge

    def save_fact(self, fact_id: str, scope: str, kind: str, payload: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO org_knowledge(fact_id,scope,kind,payload,created_at) VALUES(?,?,?,?,?)",
                        (fact_id, scope, kind, _dumps(payload), _now()))

    def facts(self, scope: str) -> list[dict[str, Any]]:
        return [{"fact_id": r["fact_id"], "scope": r["scope"], "kind": r["kind"], **json.loads(r["payload"])}
                for r in self._rows("SELECT * FROM org_knowledge WHERE scope=? ORDER BY created_at, fact_id", (scope,))]

    # ------------------------------------------------------------- events

    def record_event(self, event_key: str, kind: str, outcome: str, payload: dict[str, Any]) -> bool:
        """True — событие новое и записано; False — такой ключ уже принят (дедуп)."""
        with self._connect() as con:
            try:
                con.execute("INSERT INTO org_events(event_key,kind,outcome,payload,created_at) VALUES(?,?,?,?,?)",
                            (event_key, kind, outcome, _dumps(payload), _now()))
            except sqlite3.IntegrityError:
                return False
        return True

    def event(self, event_key: str) -> dict[str, Any] | None:
        for r in self._rows("SELECT * FROM org_events WHERE event_key=?", (event_key,)):
            return {"event_key": r["event_key"], "kind": r["kind"], "outcome": r["outcome"],
                    "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
        return None

    # ---------------------------------------------------------------- log

    def log(self, event: str, *, mission_id: str = "", work_id: str = "", detail: str = "") -> None:
        with self._connect() as con:
            con.execute("INSERT INTO org_log(at,mission_id,work_id,event,detail) VALUES(?,?,?,?,?)",
                        (_now(), mission_id, work_id, event, detail[:2000]))

    def tail(self, limit: int = 50, *, mission_id: str | None = None) -> list[dict[str, Any]]:
        if mission_id is None:
            rows = self._rows("SELECT * FROM org_log ORDER BY seq DESC LIMIT ?", (limit,))
        else:
            rows = self._rows("SELECT * FROM org_log WHERE mission_id=? ORDER BY seq DESC LIMIT ?", (mission_id, limit))
        return [dict(r) for r in rows][::-1]


def _mission_row(r: sqlite3.Row) -> dict[str, Any]:
    return {"mission_id": r["mission_id"], "title": r["title"], "department_id": r["department_id"],
            "state": r["state"], "source": r["source"], "payload": json.loads(r["payload"]),
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _work_row(r: sqlite3.Row) -> dict[str, Any]:
    return {"work_id": r["work_id"], "mission_id": r["mission_id"], "department_id": r["department_id"],
            "digest": r["digest"], "state": r["state"], "assigned": json.loads(r["assigned"]),
            "attempts": int(r["attempts"]), "contract": DelegationContract.from_dict(json.loads(r["payload"])),
            "updated_at": r["updated_at"]}
