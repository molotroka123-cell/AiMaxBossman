"""Durable Fleet state — stdlib SQLite. Может жить в том же файле, что и
OrganizationStore (таблицы с префиксом fleet_): одна база, два пространства
таблиц, ноль вторых серверов.

Секреты сюда не пишутся: `fleet_credential_grants` хранит только авторизацию.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import CredentialGrant, FlightRecord, Lease, NodeState

SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_nodes (
  node_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_leases (
  lease_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, work_id TEXT NOT NULL, resource_class TEXT NOT NULL,
  exclusive INTEGER NOT NULL, acquired_ts REAL NOT NULL, expires_ts REAL NOT NULL, fence INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS fleet_leases_node ON fleet_leases(node_id, resource_class);
CREATE TABLE IF NOT EXISTS fleet_memory_reservations (
  lease_id TEXT PRIMARY KEY, host_gb REAL NOT NULL, gpu_gb REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_fences (
  node_id TEXT NOT NULL, resource_class TEXT NOT NULL, fence INTEGER NOT NULL,
  PRIMARY KEY (node_id, resource_class));
CREATE TABLE IF NOT EXISTS fleet_flights (
  work_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, state TEXT NOT NULL, node_id TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL, updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_verified_mutations (
  mutation_key TEXT PRIMARY KEY, mission_id TEXT NOT NULL, work_id TEXT NOT NULL, step_id TEXT NOT NULL,
  node_id TEXT NOT NULL, evidence_ref TEXT NOT NULL, created_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_work_queue (
  work_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, priority INTEGER NOT NULL, requirement TEXT NOT NULL,
  payload TEXT NOT NULL, claimed_by TEXT, claimed_ts REAL, claim_fence INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0, enqueued_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_dead_letter (
  work_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, reason TEXT NOT NULL, failure_class TEXT NOT NULL,
  attempts INTEGER NOT NULL, payload TEXT NOT NULL, created_ts REAL NOT NULL, requeued INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS fleet_credential_grants (
  grant_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fleet_events (
  event_id TEXT PRIMARY KEY, type TEXT NOT NULL, ts REAL NOT NULL, mission_id TEXT NOT NULL DEFAULT '',
  work_id TEXT NOT NULL DEFAULT '', node_id TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fleet_events_ts ON fleet_events(ts);
CREATE TABLE IF NOT EXISTS fleet_node_stats (
  node_id TEXT NOT NULL, capability TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (node_id, capability));
CREATE TABLE IF NOT EXISTS fleet_artifacts (
  sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL);
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


class FleetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use a file path: sqlite :memory: is per-connection and cannot survive restart")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(fleet_work_queue)")}
            if "queue_state" not in cols:
                con.execute("ALTER TABLE fleet_work_queue ADD COLUMN queue_state TEXT NOT NULL DEFAULT 'ready'")
            if "not_before" not in cols:
                con.execute("ALTER TABLE fleet_work_queue ADD COLUMN not_before REAL NOT NULL DEFAULT 0")

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)   # autocommit; транзакции — явно
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    # -------------------------------------------------------------- nodes

    def save_node(self, n: NodeState) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO fleet_nodes(node_id,payload,updated_ts) VALUES(?,?,?) "
                        "ON CONFLICT(node_id) DO UPDATE SET payload=excluded.payload, updated_ts=excluded.updated_ts",
                        (n.node_id, _dumps(n.to_dict()), time.time()))

    def node(self, node_id: str) -> NodeState | None:
        with self.connect() as con:
            row = con.execute("SELECT payload FROM fleet_nodes WHERE node_id=?", (node_id,)).fetchone()
        return NodeState.from_dict(json.loads(row["payload"])) if row else None

    def nodes(self) -> list[NodeState]:
        with self.connect() as con:
            rows = con.execute("SELECT payload FROM fleet_nodes ORDER BY node_id").fetchall()
        return [NodeState.from_dict(json.loads(r["payload"])) for r in rows]

    # ------------------------------------------------------------- leases

    def next_fence(self, con: sqlite3.Connection, node_id: str, resource_class: str) -> int:
        con.execute("INSERT INTO fleet_fences(node_id,resource_class,fence) VALUES(?,?,0) "
                    "ON CONFLICT(node_id,resource_class) DO NOTHING", (node_id, resource_class))
        con.execute("UPDATE fleet_fences SET fence=fence+1 WHERE node_id=? AND resource_class=?", (node_id, resource_class))
        return int(con.execute("SELECT fence FROM fleet_fences WHERE node_id=? AND resource_class=?",
                               (node_id, resource_class)).fetchone()[0])

    def save_lease(self, con: sqlite3.Connection, lease: Lease) -> None:
        con.execute("INSERT INTO fleet_leases VALUES(?,?,?,?,?,?,?,?)",
                    (lease.lease_id, lease.node_id, lease.work_id, lease.resource_class, int(lease.exclusive),
                     lease.acquired_ts, lease.expires_ts, lease.fence))

    def update_lease_expiry(self, lease_id: str, expires_ts: float) -> bool:
        with self.connect() as con:
            cur = con.execute("UPDATE fleet_leases SET expires_ts=? WHERE lease_id=?", (expires_ts, lease_id))
            return cur.rowcount == 1

    def delete_lease(self, lease_id: str) -> bool:
        with self.connect() as con:
            return con.execute("DELETE FROM fleet_leases WHERE lease_id=?", (lease_id,)).rowcount == 1

    def leases(self, *, node_id: str | None = None, work_id: str | None = None) -> list[Lease]:
        sql, args, cond = "SELECT * FROM fleet_leases", [], []
        if node_id is not None:
            cond.append("node_id=?"); args.append(node_id)
        if work_id is not None:
            cond.append("work_id=?"); args.append(work_id)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY acquired_ts, lease_id", tuple(args)).fetchall()
        return [_lease(r) for r in rows]

    def expired_leases(self, now: float) -> list[Lease]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM fleet_leases WHERE expires_ts<=? ORDER BY expires_ts", (now,)).fetchall()
        return [_lease(r) for r in rows]

    # ------------------------------------------------------------ flights

    def save_flight(self, f: FlightRecord) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO fleet_flights(work_id,mission_id,state,node_id,payload,updated_ts) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(work_id) DO UPDATE SET state=excluded.state, node_id=excluded.node_id, "
                        "payload=excluded.payload, updated_ts=excluded.updated_ts",
                        (f.work_id, f.mission_id, f.state.value, f.node_id, _dumps(f.to_dict()), f.updated_ts))

    def flight(self, work_id: str) -> FlightRecord | None:
        with self.connect() as con:
            row = con.execute("SELECT payload FROM fleet_flights WHERE work_id=?", (work_id,)).fetchone()
        return FlightRecord.from_dict(json.loads(row["payload"])) if row else None

    def flights(self, *, mission_id: str | None = None, node_id: str | None = None,
                states: tuple[str, ...] = ()) -> list[FlightRecord]:
        sql, args, cond = "SELECT payload FROM fleet_flights", [], []
        if mission_id is not None:
            cond.append("mission_id=?"); args.append(mission_id)
        if node_id is not None:
            cond.append("node_id=?"); args.append(node_id)
        if states:
            cond.append("state IN (%s)" % ",".join("?" * len(states))); args.extend(states)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY work_id", tuple(args)).fetchall()
        return [FlightRecord.from_dict(json.loads(r["payload"])) for r in rows]

    def record_verified_mutation(self, key: str, *, mission_id: str, work_id: str, step_id: str,
                                 node_id: str, evidence_ref: str) -> bool:
        """True — новая подтверждённая мутация; False — ключ уже есть (дубликат)."""
        with self.connect() as con:
            try:
                con.execute("INSERT INTO fleet_verified_mutations VALUES(?,?,?,?,?,?,?)",
                            (key, mission_id, work_id, step_id, node_id, evidence_ref, time.time()))
            except sqlite3.IntegrityError:
                return False
        return True

    def verified_mutations(self, *, mission_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM fleet_verified_mutations", ()
        if mission_id is not None:
            sql, args = sql + " WHERE mission_id=?", (mission_id,)
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY created_ts, mutation_key", args).fetchall()]

    # ------------------------------------------------------------- queue

    def enqueue(self, work_id: str, mission_id: str, priority: int, requirement: dict, payload: dict) -> bool:
        with self.connect() as con:
            try:
                con.execute("INSERT INTO fleet_work_queue(work_id,mission_id,priority,requirement,payload,enqueued_ts) "
                            "VALUES(?,?,?,?,?,?)", (work_id, mission_id, priority, _dumps(requirement), _dumps(payload), time.time()))
            except sqlite3.IntegrityError:
                return False
        return True

    def queue(self, *, unclaimed_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM fleet_work_queue" + (" WHERE claimed_by IS NULL" if unclaimed_only else "")
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY priority ASC, enqueued_ts ASC, work_id").fetchall()
        return [_queue_row(r) for r in rows]

    def claim(self, work_id: str, node_id: str, now: float) -> int | None:
        """Атомарный CAS: занять можно только незанятую строку. Возвращает
        claim_fence победителю и None проигравшему — второго владельца нет."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute("UPDATE fleet_work_queue SET claimed_by=?, claimed_ts=?, claim_fence=claim_fence+1, "
                                  "attempts=attempts+1 WHERE work_id=? AND claimed_by IS NULL "
                                  "AND queue_state='ready' AND not_before<=?", (node_id, now, work_id, now))
                if cur.rowcount != 1:
                    con.execute("ROLLBACK")
                    return None
                fence = int(con.execute("SELECT claim_fence FROM fleet_work_queue WHERE work_id=?", (work_id,)).fetchone()[0])
                con.execute("COMMIT")
                return fence
            except Exception:
                con.execute("ROLLBACK")
                raise

    def release_claim(self, work_id: str, node_id: str, claim_fence: int) -> bool:
        with self.connect() as con:
            return con.execute("UPDATE fleet_work_queue SET claimed_by=NULL, claimed_ts=NULL "
                               "WHERE work_id=? AND claimed_by=? AND claim_fence=?",
                               (work_id, node_id, claim_fence)).rowcount == 1

    def dequeue(self, work_id: str, node_id: str, claim_fence: int) -> bool:
        with self.connect() as con:
            return con.execute("DELETE FROM fleet_work_queue WHERE work_id=? AND claimed_by=? AND claim_fence=?",
                               (work_id, node_id, claim_fence)).rowcount == 1

    # -------------------------------------------------------- dead letter

    def dead_letter(self, work_id: str, mission_id: str, *, reason: str, failure_class: str, attempts: int,
                    payload: dict) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO fleet_dead_letter VALUES(?,?,?,?,?,?,?,0)",
                        (work_id, mission_id, reason[:2000], failure_class, attempts, _dumps(payload), time.time()))

    def dead_letters(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM fleet_dead_letter WHERE requeued=0 ORDER BY created_ts, work_id").fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def mark_requeued(self, work_id: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE fleet_dead_letter SET requeued=1 WHERE work_id=?", (work_id,))

    # -------------------------------------------------------- credentials

    def save_grant(self, g: CredentialGrant) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO fleet_credential_grants(grant_id,payload) VALUES(?,?) "
                        "ON CONFLICT(grant_id) DO UPDATE SET payload=excluded.payload", (g.grant_id, _dumps(g.to_dict())))

    def grants(self) -> list[CredentialGrant]:
        with self.connect() as con:
            rows = con.execute("SELECT payload FROM fleet_credential_grants ORDER BY grant_id").fetchall()
        return [CredentialGrant(**json.loads(r["payload"])) for r in rows]

    # ------------------------------------------------------------- events

    def append_event(self, event_id: str, type_: str, ts: float, payload: dict, *, mission_id: str = "",
                     work_id: str = "", node_id: str = "") -> bool:
        with self.connect() as con:
            try:
                con.execute("INSERT INTO fleet_events VALUES(?,?,?,?,?,?,?)",
                            (event_id, type_, ts, mission_id, work_id, node_id, _dumps(payload)))
            except sqlite3.IntegrityError:
                return False
        return True

    def events(self, *, mission_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM fleet_events", []
        if mission_id is not None:
            sql += " WHERE mission_id=?"; args.append(mission_id)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY ts, event_id LIMIT ?", (*args, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # -------------------------------------------------------- node stats

    def save_node_stats(self, node_id: str, capability: str, payload: dict) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO fleet_node_stats VALUES(?,?,?) ON CONFLICT(node_id,capability) DO UPDATE SET payload=excluded.payload",
                        (node_id, capability, _dumps(payload)))

    def node_stats(self) -> list[tuple[str, str, dict]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM fleet_node_stats ORDER BY node_id, capability").fetchall()
        return [(r["node_id"], r["capability"], json.loads(r["payload"])) for r in rows]

    # ---------------------------------------------------------- artifacts

    def save_artifact(self, sha256: str, payload: dict) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO fleet_artifacts VALUES(?,?)", (sha256, _dumps(payload)))

    def artifact(self, sha256: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT payload FROM fleet_artifacts WHERE sha256=?", (sha256,)).fetchone()
        return json.loads(row["payload"]) if row else None


def _lease(r: sqlite3.Row) -> Lease:
    return Lease(r["lease_id"], r["node_id"], r["work_id"], r["resource_class"], bool(r["exclusive"]),
                 float(r["acquired_ts"]), float(r["expires_ts"]), int(r["fence"]))


def _queue_row(r: sqlite3.Row) -> dict[str, Any]:
    return {"work_id": r["work_id"], "mission_id": r["mission_id"], "priority": int(r["priority"]),
            "requirement": json.loads(r["requirement"]), "payload": json.loads(r["payload"]),
            "claimed_by": r["claimed_by"], "claimed_ts": r["claimed_ts"], "claim_fence": int(r["claim_fence"]),
            "attempts": int(r["attempts"]), "enqueued_ts": float(r["enqueued_ts"])}
