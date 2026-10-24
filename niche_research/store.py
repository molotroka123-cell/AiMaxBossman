"""Durable SQLite queue + run state.

Tasks: pending -> running -> done | failed, with attempts and a lease. A running task whose lease expired (the
process died, the PC restarted) is put back to pending by ``recover_stale`` at the start of every tick, or failed
once it used ``max_attempts``. ``claim_next`` holds an IMMEDIATE transaction, so at most ``MAX_RUNNING_TASKS``
tasks are running across every process that shares the DB.

Only the orchestrator may create tasks: ``enqueue`` requires the key from ``issue_orchestrator_key``, which a
Store hands out exactly once. Agent code never receives a Store (see roles.AgentContext).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from niche_research import MAX_RUNNING_TASKS
from niche_research.pii import strip_pii

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    day INTEGER NOT NULL,
    week INTEGER NOT NULL,
    niche TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    depends_on INTEGER,
    payload TEXT NOT NULL DEFAULT '{}',
    lease_until REAL,
    worker TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, day, id);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    niche TEXT NOT NULL,
    role TEXT NOT NULL,
    models TEXT NOT NULL,
    tools TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, event TEXT NOT NULL,
    detail TEXT);
CREATE TABLE IF NOT EXISTS spend_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_id INTEGER,
    role TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    paid INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    note TEXT
);
CREATE TABLE IF NOT EXISTS error_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, task_id INTEGER,
    source TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_id INTEGER,
    url TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('observation','conclusion','assumption','unknown')),
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, last_fetch REAL NOT NULL);
"""


class EnqueueKey:
    """Capability object; only the orchestrator holds one."""

    __slots__ = ("_store_id",)

    def __init__(self, store_id: int):
        self._store_id = store_id


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(SCHEMA)
        self._key_issued = False

    def close(self) -> None:
        self._conn.close()

    # ---- capability ------------------------------------------------------------------------------------------
    def issue_orchestrator_key(self) -> EnqueueKey:
        if self._key_issued:
            raise PermissionError("orchestrator key already issued for this store")
        self._key_issued = True
        return EnqueueKey(id(self))

    def _check_key(self, key) -> None:
        if not isinstance(key, EnqueueKey) or key._store_id != id(self):
            raise PermissionError("only the orchestrator may enqueue tasks")

    # ---- meta ------------------------------------------------------------------------------------------------
    def get_meta(self, key: str, default=None):
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value) -> None:
        self._conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, json.dumps(value, ensure_ascii=False)))

    def set_meta_if_absent(self, key: str, value) -> bool:
        cur = self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                                 (key, json.dumps(value, ensure_ascii=False)))
        return cur.rowcount == 1

    def acquire_lease(self, name: str, now: float, seconds: float) -> bool:
        """Mutex across processes (one tick at a time)."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
            if row and float(json.loads(row["value"])) > now:
                self._conn.execute("ROLLBACK")
                return False
            self._conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET "
                               "value=excluded.value", (name, json.dumps(now + seconds)))
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def release_lease(self, name: str) -> None:
        self._conn.execute("DELETE FROM meta WHERE key=?", (name,))

    # ---- queue -----------------------------------------------------------------------------------------------
    def enqueue(self, key: EnqueueKey, *, task_key: str, day: int, week: int, niche: str, role: str,
                depends_on: int | None = None, payload: dict | None = None, now: float | None = None) -> int:
        """Idempotent by ``task_key``; returns the task id."""
        self._check_key(key)
        now = time.time() if now is None else now
        self._conn.execute(
            "INSERT OR IGNORE INTO tasks(key, day, week, niche, role, depends_on, payload, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (task_key, day, week, niche, role, depends_on, json.dumps(payload or {}, ensure_ascii=False), now))
        return int(self._conn.execute("SELECT id FROM tasks WHERE key=?", (task_key,)).fetchone()["id"])

    def claim_next(self, worker: str, now: float, lease_seconds: float, cap: int = MAX_RUNNING_TASKS) -> dict | None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            running = self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
            if running >= min(cap, MAX_RUNNING_TASKS):
                self._conn.execute("ROLLBACK")
                return None
            row = self._conn.execute(
                "SELECT t.* FROM tasks t LEFT JOIN tasks d ON d.id = t.depends_on "
                "WHERE t.status='pending' AND (t.depends_on IS NULL OR d.status='done') "
                "ORDER BY t.day, t.id LIMIT 1").fetchone()
            if row is None:
                self._conn.execute("ROLLBACK")
                return None
            self._conn.execute(
                "UPDATE tasks SET status='running', attempts=attempts+1, lease_until=?, worker=?, started_at=? "
                "WHERE id=?", (now + lease_seconds, worker, now, row["id"]))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get_task(row["id"])

    def get_task(self, task_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        task = dict(row)
        task["payload"] = json.loads(task["payload"] or "{}")
        return task

    def tasks(self, status: str | None = None, day: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            sql += " AND status=?"
            args.append(status)
        if day is not None:
            sql += " AND day=?"
            args.append(day)
        out = []
        for row in self._conn.execute(sql + " ORDER BY day, id", args).fetchall():
            t = dict(row)
            t["payload"] = json.loads(t["payload"] or "{}")
            out.append(t)
        return out

    def counts(self) -> dict[str, int]:
        out = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        for row in self._conn.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status"):
            out[row["status"]] = row["n"]
        return out

    def finish_task(self, task_id: int, now: float) -> None:
        self._conn.execute("UPDATE tasks SET status='done', finished_at=?, lease_until=NULL, error=NULL WHERE id=?",
                           (now, task_id))

    def release_task(self, task_id: int) -> None:
        """Back to pending without consuming the attempt (e.g. no model available right now)."""
        self._conn.execute("UPDATE tasks SET status='pending', attempts=MAX(attempts-1, 0), lease_until=NULL, "
                           "worker=NULL WHERE id=? AND status='running'", (task_id,))

    def fail_task(self, task_id: int, error: str, now: float, max_attempts: int) -> str:
        """Retry (back to pending) until max_attempts, then failed + dependents failed. Returns the new status."""
        task = self.get_task(task_id)
        if task is None:
            return "missing"
        status = "pending" if task["attempts"] < max_attempts else "failed"
        self._conn.execute("UPDATE tasks SET status=?, error=?, lease_until=NULL, finished_at=? WHERE id=?",
                           (status, error[:2000], now if status == "failed" else None, task_id))
        if status == "failed":
            self._cascade_fail(task_id, now)
        return status

    def _cascade_fail(self, task_id: int, now: float) -> None:
        children = [r["id"] for r in self._conn.execute(
            "SELECT id FROM tasks WHERE depends_on=? AND status='pending'", (task_id,))]
        for child in children:
            self._conn.execute("UPDATE tasks SET status='failed', error=?, finished_at=? WHERE id=?",
                               (f"dependency {task_id} failed", now, child))
            self._cascade_fail(child, now)

    def recover_stale(self, now: float, max_attempts: int) -> int:
        """Running tasks with an expired lease (crash / PC restart) -> pending, or failed if out of attempts."""
        rows = self._conn.execute("SELECT id, attempts FROM tasks WHERE status='running' AND "
                                  "(lease_until IS NULL OR lease_until < ?)", (now,)).fetchall()
        for row in rows:
            if row["attempts"] >= max_attempts:
                self._conn.execute("UPDATE tasks SET status='failed', error='lease expired, attempts exhausted', "
                                   "lease_until=NULL, finished_at=? WHERE id=?", (now, row["id"]))
                self._cascade_fail(row["id"], now)
            else:
                self._conn.execute("UPDATE tasks SET status='pending', lease_until=NULL, worker=NULL WHERE id=?",
                                   (row["id"],))
        return len(rows)

    # ---- results / logs --------------------------------------------------------------------------------------
    def add_result(self, task: dict, models: list[str], tools: list[str], content: dict, now: float) -> int:
        cur = self._conn.execute(
            "INSERT INTO results(task_id, day, niche, role, models, tools, content, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (task["id"], task["day"], task["niche"], task["role"], json.dumps(models), json.dumps(tools),
             json.dumps(content, ensure_ascii=False), now))
        return int(cur.lastrowid)

    def results(self, day: int | None = None, role: str | None = None, week: int | None = None) -> list[dict]:
        sql, args = "SELECT r.* FROM results r JOIN tasks t ON t.id = r.task_id WHERE 1=1", []
        if day is not None:
            sql += " AND r.day=?"
            args.append(day)
        if role:
            sql += " AND r.role=?"
            args.append(role)
        if week is not None:
            sql += " AND t.week=?"
            args.append(week)
        out = []
        for row in self._conn.execute(sql + " ORDER BY r.day, r.id", args):
            r = dict(row)
            r["models"] = json.loads(r["models"])
            r["tools"] = json.loads(r["tools"])
            r["content"] = json.loads(r["content"])
            out.append(r)
        return out

    def log(self, event: str, detail="", now: float | None = None) -> None:
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        self._conn.execute("INSERT INTO run_log(ts, event, detail) VALUES(?,?,?)",
                           (time.time() if now is None else now, event, detail[:4000]))

    def run_log(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,))]

    def log_error(self, source: str, message: str, task_id: int | None = None, now: float | None = None) -> None:
        self._conn.execute("INSERT INTO error_log(ts, task_id, source, message) VALUES(?,?,?,?)",
                           (time.time() if now is None else now, task_id, source, strip_pii(message)[:4000]))

    def errors(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM error_log ORDER BY id DESC LIMIT ?", (limit,))]

    def log_spend(self, *, provider: str, model: str, tokens_in: int, tokens_out: int, cost_usd: float, paid: bool,
                  ok: bool, task_id: int | None = None, role: str | None = None, note: str = "",
                  now: float | None = None) -> None:
        self._conn.execute(
            "INSERT INTO spend_log(ts, task_id, role, provider, model, tokens_in, tokens_out, cost_usd, paid, ok, note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time() if now is None else now, task_id, role, provider, model, int(tokens_in), int(tokens_out),
             float(cost_usd), 1 if paid else 0, 1 if ok else 0, note[:500]))

    def spend_rows(self, since: float | None = None, until: float | None = None,
                   task_ids: list[int] | None = None) -> list[dict]:
        sql, args = "SELECT * FROM spend_log WHERE 1=1", []
        if since is not None:
            sql += " AND ts>=?"
            args.append(since)
        if until is not None:
            sql += " AND ts<?"
            args.append(until)
        if task_ids is not None:
            if not task_ids:
                return []
            sql += f" AND task_id IN ({','.join('?' * len(task_ids))})"
            args.extend(task_ids)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY id", args)]

    def paid_spend(self, since: float | None = None) -> float:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM spend_log WHERE paid=1", []
        if since is not None:
            sql += " AND ts>=?"
            args.append(since)
        return float(self._conn.execute(sql, args).fetchone()[0])

    def total_spend(self) -> float:
        return float(self._conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend_log").fetchone()[0])

    def free_calls_since(self, since: float) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM spend_log WHERE paid=0 AND provider NOT IN "
                                      "('ollama','fake') AND ts>=?", (since,)).fetchone()[0])

    def add_learning(self, *, url: str, kind: str, text: str, task_id: int | None, now: float) -> None:
        self._conn.execute("INSERT INTO learning(ts, task_id, url, kind, text) VALUES(?,?,?,?,?)",
                           (now, task_id, url, kind, strip_pii(text)[:1000]))

    def learning(self, task_ids: list[int] | None = None) -> list[dict]:
        if task_ids is not None and not task_ids:
            return []
        sql, args = "SELECT * FROM learning", []
        if task_ids is not None:
            sql += f" WHERE task_id IN ({','.join('?' * len(task_ids))})"
            args = list(task_ids)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY id", args)]

    def host_last_fetch(self, host: str) -> float | None:
        row = self._conn.execute("SELECT last_fetch FROM hosts WHERE host=?", (host,)).fetchone()
        return float(row["last_fetch"]) if row else None

    def set_host_last_fetch(self, host: str, ts: float) -> None:
        self._conn.execute("INSERT INTO hosts(host, last_fetch) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET "
                           "last_fetch=excluded.last_fetch", (host, ts))
