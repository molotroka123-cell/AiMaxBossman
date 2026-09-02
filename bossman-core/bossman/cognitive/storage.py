"""Общее durable-хранилище когнитивного пакета: SQLite + часы + хэши.

Почему отдельный SQLite, а не сразу Postgres:
- когнитивный контур должен тестироваться в CI без инфры (pure stdlib);
- проводка к каноничному Postgres (`bossman.db`, `ContextStore`) делается
  адаптером в `runtime.py` после аудита, а не скрытой зависимостью;
- формат записей совместим: те же JSON-поля, тот же `content_hash=sha256`.

Таблицы:
- memories10   — типизированные записи памяти (все поля из ТЗ).
- tombstones   — удалённое/отозванное (memory_id → deleted_at, reason).
- conflicts    — история разрешения противоречий.
- ledger_facts — critical-fact ledger контекста.
- journal      — durable task journal (по одному ряду на шаг).
- checkpoints  — checkpoint после каждого важного шага.
- thoughts     — structured thought states (reasoning).
- metric_events— события VerifiedSuccess / cost / verifier для verify.py.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS memories10 (
  memory_id TEXT PRIMARY KEY,
  tier TEXT NOT NULL,
  text TEXT NOT NULL,
  owner_id TEXT NOT NULL DEFAULT '',
  principal_id TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  task_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL DEFAULT '',
  corpus_id TEXT NOT NULL DEFAULT '',
  domain_id TEXT NOT NULL DEFAULT '',
  head_sha TEXT NOT NULL DEFAULT '',
  environment_digest TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  collected_at TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  verifier_id TEXT NOT NULL DEFAULT '',
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  allowed_consumers TEXT NOT NULL DEFAULT '[]',
  contradictions TEXT NOT NULL DEFAULT '[]',
  supersedes TEXT NOT NULL DEFAULT '[]',
  schema_version INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT NOT NULL DEFAULT '',
  extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mem10_owner_proj ON memories10(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_mem10_tier ON memories10(tier);
CREATE INDEX IF NOT EXISTS idx_mem10_status ON memories10(verification_status);
CREATE TABLE IF NOT EXISTS tombstones (
  memory_id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL DEFAULT '',
  deleted_at TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conflicts (
  conflict_id TEXT PRIMARY KEY,
  memory_a TEXT NOT NULL,
  memory_b TEXT NOT NULL,
  detected_at TEXT NOT NULL DEFAULT '',
  resolution TEXT NOT NULL DEFAULT 'open',
  winner_id TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '[]',
  history TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ledger_facts (
  fact_id TEXT PRIMARY KEY,
  normalized_fact TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  source TEXT NOT NULL DEFAULT '',
  verification TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL DEFAULT '',
  must_preserve INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal (
  task_id TEXT NOT NULL,
  run_id TEXT NOT NULL DEFAULT '',
  step_id TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  constraints_text TEXT NOT NULL DEFAULT '',
  plan_version INTEGER NOT NULL DEFAULT 1,
  dependencies TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'PENDING',
  attempt INTEGER NOT NULL DEFAULT 0,
  input_hash TEXT NOT NULL DEFAULT '',
  output_hash TEXT NOT NULL DEFAULT '',
  effect_id TEXT NOT NULL DEFAULT '',
  receipt TEXT NOT NULL DEFAULT '',
  verification TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  completed_at TEXT NOT NULL DEFAULT '',
  checkpoint_ref TEXT NOT NULL DEFAULT '',
  next_action TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (task_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_journal_task_state ON journal(task_id, state);
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  run_id TEXT NOT NULL DEFAULT '',
  plan_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS thoughts (
  thought_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'STANDARD',
  created_at TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS metric_events (
  event_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  task_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}'
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


class Clock(Protocol):
    def now_iso(self) -> str: ...
    def now_ts(self) -> float: ...


@dataclass(frozen=True)
class SystemClock:
    def now_iso(self) -> str:
        return utcnow_iso()

    def now_ts(self) -> float:
        return time.time()


@dataclass
class FixedClock:
    """Детерминированные часы для тестов stale/future evidence.

    Если переданные iso/ts расходятся больше чем на час (типичная ошибка
    ручного подбора timestamp) — доверяем iso (парсим его), чтобы stale/future
    проверки не давали ложных REJECT.
    """

    iso: str
    ts: float

    def __post_init__(self) -> None:
        try:
            parsed = parse_ts(self.iso)
            if parsed and abs(parsed - float(self.ts)) > 3600:
                object.__setattr__(self, "ts", parsed)
        except Exception:
            pass

    def now_iso(self) -> str:
        return self.iso

    def now_ts(self) -> float:
        return self.ts

    @classmethod
    def at(cls, iso: str) -> "FixedClock":
        return cls(iso=iso, ts=parse_ts(iso))


def parse_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CognitiveStore:
    """Тонкая SQLite-обёртка. Один файл — весь durable-контур.

    Для production wiring подменить путями/адаптером Postgres (см. runtime.py),
    формат строк при этом не меняется.
    """

    def __init__(self, path: str | Path, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.clock: Clock = clock or SystemClock()
        # Простейший in-memory retrieval cache. Ключ — (owner, project, query-hash).
        # Инвалидируется при write/delete/GC — иначе удалённая запись могла бы
        # "воскреснуть" из кэша (требование DeletionResidual = 0).
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def close(self) -> None:
        self.db.close()

    # -- generic helpers -------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.db.execute(sql, params)

    def commit(self) -> None:
        self.db.commit()

    def invalidate_cache(self) -> None:
        self._cache.clear()

    def cache_get(self, key: str) -> list[dict[str, Any]] | None:
        return self._cache.get(key)

    def cache_put(self, key: str, rows: list[dict[str, Any]]) -> None:
        # Bounded: не даём кэшу расти бесконечно на длинных задачах.
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[key] = rows

    # -- tombstone helpers ------------------------------------------------
    def is_tombstoned(self, memory_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM tombstones WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return row is not None

    def tombstone_hashes(self) -> set[str]:
        return {
            r[0]
            for r in self.db.execute("SELECT content_hash FROM tombstones").fetchall()
        }
