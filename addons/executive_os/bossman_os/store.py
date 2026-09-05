"""Host-only local orchestration state; never a provider or paid-budget ledger.

The runtime authenticates callers, authorizes actions and independently verifies
proofs before calling this store. Receipt-shaped JSON is not authentication.
Keep this database outside agent mounts; exposing these methods as model tools
would bypass that host trust boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import time


class StoreError(ValueError):
    pass


class Conflict(StoreError):
    pass


class CapacityExceeded(StoreError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _name(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise StoreError(f"invalid {label}")
    return value


def _integer(value, label):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise StoreError(f"invalid {label}")


def _vector(value):
    if not isinstance(value, dict):
        raise StoreError("resource vector must be a dictionary")
    for key, amount in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise StoreError("invalid resource name")
        if any(word in key for word in ("usd", "cost", "budget", "money", "dollar", "paid")):
            raise StoreError("paid resources are not admitted")
        _integer(amount, "resource amount")
    return dict(value)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_limits(name TEXT PRIMARY KEY, capacity INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0));
CREATE TABLE IF NOT EXISTS configuration(id INTEGER PRIMARY KEY CHECK(id=1), limits_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, contract TEXT NOT NULL, digest TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS steps(mission TEXT NOT NULL REFERENCES missions(id), id TEXT NOT NULL, ordinal INTEGER NOT NULL, effect_digest TEXT NOT NULL, state TEXT NOT NULL, actor TEXT, fence INTEGER NOT NULL DEFAULT 0, resources TEXT NOT NULL DEFAULT '{}', receipt TEXT, PRIMARY KEY(mission,id));
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, mission TEXT NOT NULL REFERENCES missions(id), version INTEGER NOT NULL, step_id TEXT, kind TEXT NOT NULL, before_state TEXT, after_state TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL);
"""


class Store:
    def __init__(self, path, limits=None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        supplied = None if limits is None else _vector(limits)
        with self._connection() as db:
            db.executescript(_SCHEMA)
            with self._transaction(db):
                old = db.execute("SELECT limits_json FROM configuration WHERE id=1").fetchone()
                if old is None:
                    configured = supplied or {}
                    db.execute("INSERT INTO configuration VALUES(1,?)", (canonical(configured),))
                    db.executemany("INSERT INTO resource_limits(name,capacity) VALUES(?,?)", configured.items())
                elif supplied is not None and json.loads(old[0]) != supplied:
                    previous = json.loads(old[0])
                    if set(supplied) != set(previous) or any(supplied[k] > previous[k] for k in previous):
                        raise Conflict("protected limits may only decrease on reopen")
                    db.execute("UPDATE configuration SET limits_json=? WHERE id=1", (canonical(supplied),))
                    db.executemany("UPDATE resource_limits SET capacity=? WHERE name=?",
                                   [(amount, name) for name, amount in supplied.items()])

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    @staticmethod
    @contextmanager
    def _transaction(db):
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    @staticmethod
    def _mission(db, mission_id):
        row = db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if row is None:
            raise StoreError("unknown mission")
        return row

    @staticmethod
    def _step(db, mission_id, step_id):
        row = db.execute("SELECT * FROM steps WHERE mission=? AND id=?", (mission_id, step_id)).fetchone()
        if row is None:
            raise StoreError("unknown step")
        return row

    @staticmethod
    def _event(db, mission_id, version, step_id, kind, before, after, payload):
        db.execute("INSERT INTO events(mission,version,step_id,kind,before_state,after_state,at,payload) VALUES(?,?,?,?,?,?,?,?)",
                   (mission_id, version, step_id, kind, before, after, time(), canonical(payload)))

    def create(self, mission_id, contract):
        _name(mission_id, "mission id")
        if not isinstance(contract, dict) or contract.get("local_only") is not True:
            raise StoreError("host-approved local-only contract required")
        if type(contract.get("cost_microusd", 0)) is not int or contract.get("cost_microusd", 0) != 0:
            raise StoreError("only zero-cost missions are admitted")
        steps = contract.get("steps")
        if not isinstance(steps, list) or not steps:
            raise StoreError("nonempty declared steps required")
        ids = []
        for step in steps:
            if not isinstance(step, dict):
                raise StoreError("invalid declared step")
            ids.append(_name(step.get("id"), "step id"))
            if not isinstance(step.get("effect_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", step["effect_digest"]):
                raise StoreError("host effect digest required")
        if len(ids) != len(set(ids)):
            raise StoreError("duplicate step id")
        payload = canonical(contract)
        fingerprint = digest(contract)
        with self._connection() as db, self._transaction(db):
            old = db.execute("SELECT digest FROM missions WHERE id=?", (mission_id,)).fetchone()
            if old is not None:
                if old[0] != fingerprint:
                    raise Conflict("mission contract is immutable")
            else:
                db.execute("INSERT INTO missions(id,contract,digest,created_at) VALUES(?,?,?,?)",
                           (mission_id, payload, fingerprint, time()))
                db.executemany("INSERT INTO steps(mission,id,ordinal,effect_digest,state) VALUES(?,?,?,?,?)",
                               [(mission_id, s["id"], i, s["effect_digest"], "ready") for i, s in enumerate(steps)])
                self._event(db, mission_id, 0, None, "created", None, "ready", {"contract_digest": fingerprint})
            return self._snapshot(db, mission_id)

    @staticmethod
    def _resources(db):
        rows = db.execute("SELECT name,capacity,used FROM resource_limits ORDER BY name").fetchall()
        return {r["name"]: {"limit": r["capacity"], "used": r["used"], "available": r["capacity"]-r["used"]} for r in rows}

    def _snapshot(self, db, mission_id):
        mission = self._mission(db, mission_id)
        steps = []
        for row in db.execute("SELECT * FROM steps WHERE mission=? ORDER BY ordinal", (mission_id,)):
            steps.append({"id": row["id"], "state": row["state"], "actor": row["actor"], "fence": row["fence"],
                          "effect_digest": row["effect_digest"], "resources": json.loads(row["resources"]),
                          "receipt": json.loads(row["receipt"]) if row["receipt"] is not None else None})
        states = {s["state"] for s in steps}
        status = ("verified" if states == {"verified"} else "unknown" if "unknown" in states
                  else "failed" if "failed" in states else "running" if "running" in states else "ready")
        events = []
        for row in db.execute("SELECT * FROM events WHERE mission=? ORDER BY seq", (mission_id,)):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            events.append(item)
        return {"id": mission_id, "contract": json.loads(mission["contract"]), "contract_digest": mission["digest"],
                "version": mission["version"], "status": status, "steps": steps, "events": events,
                "resource_usage": self._resources(db)}

    def snapshot(self, mission_id):
        with self._connection() as db:
            db.execute("BEGIN")
            try:
                return self._snapshot(db, mission_id)
            finally:
                db.execute("ROLLBACK")

    def list(self):
        with self._connection() as db:
            db.execute("BEGIN")
            try:
                ids = [row[0] for row in db.execute("SELECT id FROM missions ORDER BY created_at,id")]
                return [self._snapshot(db, mission_id) for mission_id in ids]
            finally:
                db.execute("ROLLBACK")

    list_snapshots = list

    def claim(self, mission_id, step_id, actor, expected_version, resources, limits=None):
        _name(actor, "actor")
        _integer(expected_version, "expected version")
        resources = {k: v for k, v in _vector(resources).items() if v}
        requested_limits = None if limits is None else _vector(limits)
        with self._connection() as db, self._transaction(db):
            mission = self._mission(db, mission_id)
            if mission["version"] != expected_version:
                raise Conflict("stale mission version")
            declared_actor = json.loads(mission["contract"]).get("actor")
            if declared_actor is not None and actor != declared_actor:
                raise Conflict("actor differs from host contract")
            step = self._step(db, mission_id, step_id)
            if step["state"] != "ready":
                raise Conflict("step requires host reconciliation, never ordinary retry")
            capacities = {r["name"]: r for r in db.execute("SELECT * FROM resource_limits")}
            if requested_limits is not None and any(k not in capacities or v > capacities[k]["capacity"] for k, v in requested_limits.items()):
                raise CapacityExceeded("per-call limits cannot expand protected limits")
            for key, amount in resources.items():
                if key not in capacities:
                    raise CapacityExceeded("undeclared resource")
                cap = min(capacities[key]["capacity"], requested_limits.get(key, capacities[key]["capacity"]) if requested_limits is not None else capacities[key]["capacity"])
                if capacities[key]["used"] + amount > cap:
                    raise CapacityExceeded("local resource capacity exhausted")
            for key, amount in resources.items():
                db.execute("UPDATE resource_limits SET used=used+? WHERE name=?", (amount, key))
            fence, version = step["fence"]+1, mission["version"]+1
            db.execute("UPDATE steps SET state='running',actor=?,fence=?,resources=? WHERE mission=? AND id=?",
                       (actor, fence, canonical(resources), mission_id, step_id))
            db.execute("UPDATE missions SET version=? WHERE id=?", (version, mission_id))
            self._event(db, mission_id, version, step_id, "claimed", "ready", "running", {"actor": actor, "fence": fence, "resources": resources})
            result = self._snapshot(db, mission_id)
            result["fence"] = fence
            result["claim"] = {"step_id": step_id, "actor": actor, "fence": fence,
                               "dispatch_binding": digest([mission["digest"], step_id, fence])}
            return result

    @staticmethod
    def _owned(step, actor, fence):
        _name(actor, "actor")
        _integer(fence, "fence")
        if (step["actor"], step["fence"]) != (actor, fence):
            raise Conflict("stale actor or fence")

    def confirm(self, mission_id, step_id, actor, fence, receipt):
        """Runtime must first verify independent host proof; this checks binding.

        Only confirmation releases physical resource reservations. This method
        never performs an action, emits a payment or auto-promotes a model claim.
        """
        if not isinstance(receipt, dict):
            raise StoreError("host-verified receipt required")
        payload = canonical(receipt)
        with self._connection() as db, self._transaction(db):
            mission, step = self._mission(db, mission_id), self._step(db, mission_id, step_id)
            self._owned(step, actor, fence)
            binding = {"mission_id": mission_id, "contract_digest": mission["digest"], "step_id": step_id, "effect_digest": step["effect_digest"],
                       "actor": actor, "fence": fence, "dispatch_binding": digest([mission["digest"], step_id, fence])}
            if any(receipt.get(key) != value or type(receipt.get(key)) is not type(value) for key, value in binding.items()):
                raise Conflict("receipt does not bind the declared effect and attempt")
            if step["state"] == "verified":
                if step["receipt"] != payload:
                    raise Conflict("confirmed receipt is immutable")
                return self._snapshot(db, mission_id)
            if step["state"] not in ("running", "unknown"):
                raise Conflict("confirmation requires a running or unresolved attempt")
            for key, amount in json.loads(step["resources"]).items():
                db.execute("UPDATE resource_limits SET used=used-? WHERE name=?", (amount, key))
            version = mission["version"]+1
            db.execute("UPDATE steps SET state='verified',receipt=? WHERE mission=? AND id=?", (payload, mission_id, step_id))
            db.execute("UPDATE missions SET version=? WHERE id=?", (version, mission_id))
            self._event(db, mission_id, version, step_id, "confirmed", step["state"], "verified", {"receipt_digest": digest(receipt)})
            return self._snapshot(db, mission_id)

    def _unresolved(self, mission_id, step_id, actor, fence, state):
        with self._connection() as db, self._transaction(db):
            mission, step = self._mission(db, mission_id), self._step(db, mission_id, step_id)
            self._owned(step, actor, fence)
            if step["state"] == state:
                return self._snapshot(db, mission_id)
            if step["state"] != "running":
                raise Conflict("only an owned running attempt can become unresolved")
            version = mission["version"]+1
            db.execute("UPDATE steps SET state=? WHERE mission=? AND id=?", (state, mission_id, step_id))
            db.execute("UPDATE missions SET version=? WHERE id=?", (version, mission_id))
            self._event(db, mission_id, version, step_id, "uncertain" if state == "unknown" else "failed", "running", state,
                        {"actor": actor, "fence": fence, "resources_retained": True})
            return self._snapshot(db, mission_id)

    def uncertain(self, mission_id, step_id, actor, fence):
        return self._unresolved(mission_id, step_id, actor, fence, "unknown")

    def fail(self, mission_id, step_id, actor, fence):
        return self._unresolved(mission_id, step_id, actor, fence, "failed")

    def recover_read(self, mission_id=None):
        """Read unresolved attempts after restart; never release or retry them."""
        with self._connection() as db:
            sql = "SELECT * FROM steps WHERE state IN ('running','unknown','failed')"
            rows = db.execute(sql + (" AND mission=?" if mission_id is not None else "") + " ORDER BY mission,ordinal",
                              (mission_id,) if mission_id is not None else ()).fetchall()
            return [{"mission_id": r["mission"], "step_id": r["id"], "state": r["state"], "actor": r["actor"],
                     "fence": r["fence"], "resources": json.loads(r["resources"])} for r in rows]
