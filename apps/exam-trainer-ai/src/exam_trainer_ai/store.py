
from __future__ import annotations
import json, os, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class SQLiteStore:
    """Small durable app-local store. No Bossman imports."""
    def __init__(self, app_id: str):
        base = Path(os.getenv("BOSSMAN_APPS_DATA", str(Path.home() / ".bossman-apps")))
        self.dir = base / app_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "app.db"
        self._lock = threading.RLock()
        self._init()

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _init(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, type TEXT NOT NULL, params TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              result TEXT, error TEXT, idempotency_key TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
              event TEXT NOT NULL, subject TEXT, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv(
              namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(namespace,key)
            );
            """)
        return self

    def audit(self, event: str, subject: str | None = None, data: dict | None = None):
        with self.connect() as c:
            c.execute("INSERT INTO audit(ts,event,subject,data) VALUES(?,?,?,?)",
                      (now_iso(), event, subject, json.dumps(data or {}, ensure_ascii=False)))

    def audit_list(self, limit: int = 100):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(max(limit,1),500),)).fetchall()
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def job_create(self, typ: str, params: dict, idem: str | None = None):
        jid = str(uuid.uuid4())
        ts = now_iso()
        with self.connect() as c:
            if idem:
                existing = c.execute("SELECT * FROM jobs WHERE idempotency_key=?", (idem,)).fetchone()
                if existing:
                    return self._job_row(existing)
            c.execute("""INSERT INTO jobs(id,type,params,status,created_at,updated_at,idempotency_key)
                         VALUES(?,?,?,?,?,?,?)""",
                      (jid, typ, json.dumps(params,ensure_ascii=False), "queued", ts, ts, idem))
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        self.audit("job.created", jid, {"type": typ})
        return self._job_row(row)

    def job_get(self, jid: str):
        with self.connect() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r: raise KeyError(jid)
        return self._job_row(r)

    def job_list(self):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._job_row(r) for r in rows]

    def job_update(self, jid: str, status: str, result=None, error=None):
        with self.connect() as c:
            c.execute("UPDATE jobs SET status=?,updated_at=?,result=?,error=? WHERE id=?",
                      (status, now_iso(),
                       json.dumps(result,ensure_ascii=False) if result is not None else None,
                       error, jid))
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r: raise KeyError(jid)
        self.audit("job."+status, jid, {"error": error} if error else {})
        return self._job_row(r)

    def job_cancel(self, jid: str):
        j = self.job_get(jid)
        if j["status"] in ("completed","failed","cancelled"):
            return j
        return self.job_update(jid, "cancelled")

    def _job_row(self, r):
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def kv_put(self, ns: str, key: str, value: Any):
        with self.connect() as c:
            c.execute("""INSERT INTO kv(namespace,key,value,updated_at) VALUES(?,?,?,?)
                         ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                      (ns,key,json.dumps(value,ensure_ascii=False),now_iso()))
        return value

    def kv_get(self, ns: str, key: str):
        with self.connect() as c:
            r = c.execute("SELECT value FROM kv WHERE namespace=? AND key=?", (ns,key)).fetchone()
        if not r: raise KeyError(key)
        return json.loads(r["value"])

    def kv_list(self, ns: str):
        with self.connect() as c:
            rows = c.execute("SELECT key,value,updated_at FROM kv WHERE namespace=? ORDER BY updated_at DESC",(ns,)).fetchall()
        return [{"key":r["key"],"value":json.loads(r["value"]),"updated_at":r["updated_at"]} for r in rows]

    def metrics(self):
        with self.connect() as c:
            rows = c.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status").fetchall()
        m = {"jobs_total":0,"queued":0,"running":0,"completed":0,"failed":0,"cancelled":0}
        for r in rows:
            m[r["status"]] = r["n"]; m["jobs_total"] += r["n"]
        return m
