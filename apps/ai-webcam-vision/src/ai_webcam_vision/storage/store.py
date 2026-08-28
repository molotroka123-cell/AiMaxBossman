"""SQLite timeline: observations, jobs and artifacts.

Connections are opened and closed per operation (``with closing(...)``): the
legacy pack leaked one connection per call because ``with conn`` only manages a
transaction.

Nothing written here may contain a credential. The only free-text columns are
state reasons, artifact kinds and scrubbed error strings.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ..crm.base import CrmContext
from ..pipeline.analysis import Evidence
from ..pipeline.classifier import OCCUPIED_STATES, Classification, State
from ..secretstore import scrub

SCHEMA_VERSION = 1

#: Gap longer than this between two samples is not counted as continuous time.
MAX_GAP_SECONDS = 120.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    room_id TEXT NOT NULL,
    state TEXT NOT NULL,
    debounced_state TEXT NOT NULL,
    confidence REAL NOT NULL,
    room_change REAL NOT NULL,
    chair_change REAL NOT NULL,
    work_motion REAL NOT NULL,
    motion_gate INTEGER NOT NULL,
    source_kind TEXT NOT NULL,
    source_is_mock INTEGER NOT NULL,
    crm_available INTEGER NOT NULL,
    crm_is_mock INTEGER NOT NULL,
    crm_source TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    appointment_id TEXT NOT NULL,
    procedure_label TEXT NOT NULL,
    procedure_confidence REAL NOT NULL,
    procedure_provenance TEXT NOT NULL,
    analyzer TEXT NOT NULL,
    reasons TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_observations_room_ts ON observations(room_id, ts);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    params TEXT NOT NULL,
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_created ON jobs(created_at);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    kind TEXT NOT NULL,
    path TEXT,
    bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_artifacts_job ON artifacts(job_id);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Clinic operational data: the directory and the database stay
        # owner-only, which also covers the -wal/-shm sidecars SQLite creates.
        try:
            self.path.parent.chmod(0o700)
        except OSError:  # pragma: no cover - unusual filesystem
            pass
        self.initialise()
        try:
            self.path.chmod(0o600)
        except OSError:  # pragma: no cover - unusual filesystem
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            with closing(conn):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                yield conn
                conn.commit()
        finally:
            pass

    def initialise(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    # -------------------------------------------------------- observations
    def add_observation(
        self,
        *,
        room_id: str,
        evidence: Evidence,
        crm: CrmContext,
        classification: Classification,
        debounced_state: State,
        source_kind: str,
        source_is_mock: bool,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO observations (
                    ts, room_id, state, debounced_state, confidence,
                    room_change, chair_change, work_motion, motion_gate,
                    source_kind, source_is_mock,
                    crm_available, crm_is_mock, crm_source,
                    employee_id, clinician_id, appointment_id,
                    procedure_label, procedure_confidence, procedure_provenance,
                    analyzer, reasons
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence.ts.isoformat(),
                    room_id,
                    classification.state.value,
                    debounced_state.value,
                    classification.confidence,
                    evidence.room_change,
                    evidence.chair_change,
                    evidence.work_motion,
                    int(evidence.motion_gate),
                    source_kind,
                    int(source_is_mock),
                    int(crm.available),
                    int(crm.is_mock),
                    crm.source,
                    crm.employee_id,
                    crm.clinician_id,
                    crm.appointment_id,
                    classification.procedure,
                    classification.procedure_confidence,
                    classification.procedure_provenance,
                    classification.analyzer,
                    json.dumps(classification.reasons, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def latest_observation(self, room_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM observations WHERE room_id=? ORDER BY id DESC LIMIT 1",
                (room_id,),
            ).fetchone()
        return dict(row) if row else None

    def count_observations(self, room_id: str | None = None) -> int:
        with self.connect() as conn:
            if room_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM observations WHERE room_id=?", (room_id,)
                ).fetchone()
        return int(row["n"])

    def metrics(self, room_id: str, start: datetime, end: datetime) -> dict:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT ts, debounced_state FROM observations "
                "WHERE room_id=? AND ts>=? AND ts<? ORDER BY ts",
                (room_id, start.isoformat(), end.isoformat()),
            ).fetchall()

        seconds: dict[str, float] = {}
        counted = 0
        skipped_gaps = 0
        for current, following in zip(rows, rows[1:]):
            delta = (
                datetime.fromisoformat(following["ts"]) - datetime.fromisoformat(current["ts"])
            ).total_seconds()
            if delta <= 0:
                continue
            if delta > MAX_GAP_SECONDS:
                skipped_gaps += 1
                continue
            seconds[current["debounced_state"]] = seconds.get(current["debounced_state"], 0.0) + delta
            counted += 1

        clinical = seconds.get(State.CLINICAL_WORK.value, 0.0)
        occupied = sum(seconds.get(s.value, 0.0) for s in OCCUPIED_STATES)
        window = (end - start).total_seconds()
        return {
            "room_id": room_id,
            "window": {"start": start.isoformat(), "end": end.isoformat(), "seconds": window},
            "samples": len(rows),
            "counted_intervals": counted,
            "skipped_gaps": skipped_gaps,
            "max_gap_seconds": MAX_GAP_SECONDS,
            "seconds_by_state": {k: round(v, 2) for k, v in sorted(seconds.items())},
            "clinical_seconds": round(clinical, 2),
            "occupied_seconds": round(occupied, 2),
            "utilisation": round(clinical / window, 4) if window > 0 else 0.0,
        }

    def today_bounds(self, at: datetime | None = None) -> tuple[datetime, datetime]:
        now = at or datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    # ----------------------------------------------------------------- jobs
    def upsert_job(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, type, status, created_at, started_at, finished_at,
                                  params, result, error)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    result=excluded.result,
                    error=excluded.error
                """,
                (
                    record["id"],
                    record["type"],
                    record["status"],
                    record["created_at"],
                    record.get("started_at"),
                    record.get("finished_at"),
                    json.dumps(record.get("params", {}), ensure_ascii=False),
                    json.dumps(record["result"], ensure_ascii=False) if record.get("result") is not None else None,
                    scrub(record["error"]) if record.get("error") else None,
                ),
            )

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ artifacts
    def add_artifact(
        self,
        *,
        artifact_id: str,
        job_id: str | None,
        kind: str,
        path: str | None,
        size_bytes: int,
        created_at: datetime,
        meta: dict | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, job_id, kind, path, bytes, created_at, meta) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    job_id,
                    kind,
                    path,
                    int(size_bytes),
                    created_at.isoformat(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )

    def list_artifacts(self, job_id: str | None = None, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE job_id=? ORDER BY created_at DESC LIMIT ?",
                    (job_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["meta"] = json.loads(item.get("meta") or "{}")
            out.append(item)
        return out
