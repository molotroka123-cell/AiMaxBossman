"""Порт хранилища устройств Stage 6 + две реализации.

Одна и та же бизнес-логика (`DeviceService`) работает поверх любого хранилища,
реализующего протокол `DeviceStore`:
  * `InMemoryDeviceStore` — асинхронный фейк для юнит-тестов и dev-режима без
    Postgres (тот же контракт, что и боевой путь, но без сети);
  * `PostgresDeviceStore` — боевой путь через `bossman.db` (asyncpg).

Таблицы (создаются в subsystem.validate() через CREATE TABLE IF NOT EXISTS, НЕ в
db/schema.sql):
  remote_devices              — паспорт устройства (name, revoked, locked);
  remote_device_scopes        — по строке на скоуп (устройство × право);
  remote_device_credentials   — ТОЛЬКО sha256 секрета enrollment (сырого нет);
  remote_device_sessions      — выданные сессии (token_sha256, revoked, last_seen).

Секрет нигде не хранится в открытом виде: и в креденшлах, и в сессиях лежит
только sha256. Поиск по хэшу; финальное подтверждение совпадения — в сервисе
через compare_digest.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .auth import CredentialMatch, DeviceRecord, SessionRecord, normalize_scopes


@runtime_checkable
class DeviceStore(Protocol):
    # --- устройства ---
    async def add_device(self, record: DeviceRecord, token_sha256: str) -> None: ...
    async def get_device(self, device_id: str) -> DeviceRecord | None: ...
    async def set_device_revoked(self, device_id: str, revoked: bool) -> bool: ...
    async def set_device_locked(self, device_id: str, locked: bool) -> bool: ...
    async def set_all_locked(self, locked: bool) -> int: ...
    async def is_global_lock(self) -> bool: ...
    async def find_credential(self, token_sha256: str) -> CredentialMatch | None: ...

    # --- сессии ---
    async def add_session(self, session: SessionRecord, token_sha256: str) -> None: ...
    async def find_session(self, token_sha256: str) -> CredentialMatch | None: ...
    async def get_session(self, session_id: str) -> SessionRecord | None: ...
    async def touch_session(self, session_id: str) -> None: ...
    async def set_session_revoked(self, session_id: str, revoked: bool) -> bool: ...


class InMemoryDeviceStore:
    """Асинхронный in-memory фейк. Совпадает по контракту с Postgres-версией.
    Матчинг по хэшу выполняется через compare_digest (в auth), поэтому сравнение
    load-bearing, а не декоративное."""

    def __init__(self) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._credentials: dict[str, str] = {}          # device_id -> token_sha256
        self._sessions: dict[str, SessionRecord] = {}
        self._session_tokens: dict[str, str] = {}       # session_id -> token_sha256
        self._global_lock = False

    async def add_device(self, record: DeviceRecord, token_sha256: str) -> None:
        self._devices[record.id] = record
        self._credentials[record.id] = token_sha256

    async def get_device(self, device_id: str) -> DeviceRecord | None:
        return self._devices.get(device_id)

    async def set_device_revoked(self, device_id: str, revoked: bool) -> bool:
        d = self._devices.get(device_id)
        if d is None:
            return False
        d.revoked = revoked
        return True

    async def set_device_locked(self, device_id: str, locked: bool) -> bool:
        d = self._devices.get(device_id)
        if d is None:
            return False
        d.locked = locked
        return True

    async def set_all_locked(self, locked: bool) -> int:
        self._global_lock = locked
        for d in self._devices.values():
            d.locked = locked
        return len(self._devices)

    async def is_global_lock(self) -> bool:
        return self._global_lock

    async def find_credential(self, token_sha256: str) -> CredentialMatch | None:
        from .auth import constant_time_eq
        for device_id, stored in self._credentials.items():
            if constant_time_eq(stored, token_sha256):
                return CredentialMatch(device_id=device_id, token_sha256=stored)
        return None

    async def add_session(self, session: SessionRecord, token_sha256: str) -> None:
        self._sessions[session.id] = session
        self._session_tokens[session.id] = token_sha256

    async def find_session(self, token_sha256: str) -> CredentialMatch | None:
        from .auth import constant_time_eq
        for session_id, stored in self._session_tokens.items():
            if constant_time_eq(stored, token_sha256):
                s = self._sessions[session_id]
                return CredentialMatch(device_id=s.device_id, token_sha256=stored,
                                       session_id=session_id)
        return None

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def touch_session(self, session_id: str) -> None:
        import time
        s = self._sessions.get(session_id)
        if s is not None:
            s.last_seen = time.time()

    async def set_session_revoked(self, session_id: str, revoked: bool) -> bool:
        s = self._sessions.get(session_id)
        if s is None:
            return False
        s.revoked = revoked
        return True


# Флаг глобальной блокировки в Postgres храним одной строкой в служебной таблице.
_GLOBAL_LOCK_KEY = "global_lock"

DDL = """
CREATE TABLE IF NOT EXISTS remote_devices (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked     boolean NOT NULL DEFAULT false,
    locked      boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS remote_device_scopes (
    device_id   text NOT NULL REFERENCES remote_devices(id) ON DELETE CASCADE,
    scope       text NOT NULL,
    PRIMARY KEY (device_id, scope)
);
CREATE TABLE IF NOT EXISTS remote_device_credentials (
    device_id    text NOT NULL REFERENCES remote_devices(id) ON DELETE CASCADE,
    token_sha256 text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, token_sha256)
);
CREATE UNIQUE INDEX IF NOT EXISTS remote_device_credentials_hash
    ON remote_device_credentials (token_sha256);
CREATE TABLE IF NOT EXISTS remote_device_sessions (
    id           text PRIMARY KEY,
    device_id    text NOT NULL REFERENCES remote_devices(id) ON DELETE CASCADE,
    token_sha256 text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    revoked      boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS remote_device_sessions_hash
    ON remote_device_sessions (token_sha256);
CREATE TABLE IF NOT EXISTS remote_client_flags (
    key   text PRIMARY KEY,
    value boolean NOT NULL DEFAULT false
);
"""


class PostgresDeviceStore:
    """Боевое хранилище через bossman.db (asyncpg). Импорт db — ленивый в методах,
    чтобы модуль импортировался без живого Postgres."""

    async def _scopes(self, db, device_id: str) -> frozenset[str]:
        rows = await db.fetch("SELECT scope FROM remote_device_scopes WHERE device_id=$1", device_id)
        return normalize_scopes(r["scope"] for r in rows)

    def _to_record(self, row: dict, scopes: frozenset[str]) -> DeviceRecord:
        return DeviceRecord(
            id=row["id"], name=row["name"], scopes=scopes,
            revoked=row["revoked"], locked=row["locked"],
            created_at=row["created_at"].timestamp() if row.get("created_at") else 0.0)

    async def add_device(self, record: DeviceRecord, token_sha256: str) -> None:
        from .. import db
        async with (await db.pool()).acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO remote_devices (id, name, revoked, locked) VALUES ($1,$2,$3,$4)",
                    record.id, record.name, record.revoked, record.locked)
                for scope in record.scopes:
                    await conn.execute(
                        "INSERT INTO remote_device_scopes (device_id, scope) VALUES ($1,$2)"
                        " ON CONFLICT DO NOTHING", record.id, scope)
                await conn.execute(
                    "INSERT INTO remote_device_credentials (device_id, token_sha256) VALUES ($1,$2)",
                    record.id, token_sha256)

    async def get_device(self, device_id: str) -> DeviceRecord | None:
        from .. import db
        row = await db.fetchrow("SELECT * FROM remote_devices WHERE id=$1", device_id)
        if row is None:
            return None
        return self._to_record(row, await self._scopes(db, device_id))

    async def set_device_revoked(self, device_id: str, revoked: bool) -> bool:
        from .. import db
        res = await db.execute("UPDATE remote_devices SET revoked=$2 WHERE id=$1", device_id, revoked)
        return res.endswith("1")

    async def set_device_locked(self, device_id: str, locked: bool) -> bool:
        from .. import db
        res = await db.execute("UPDATE remote_devices SET locked=$2 WHERE id=$1", device_id, locked)
        return res.endswith("1")

    async def set_all_locked(self, locked: bool) -> int:
        from .. import db
        await db.execute(
            "INSERT INTO remote_client_flags (key, value) VALUES ($1,$2)"
            " ON CONFLICT (key) DO UPDATE SET value=excluded.value", _GLOBAL_LOCK_KEY, locked)
        res = await db.execute("UPDATE remote_devices SET locked=$1", locked)
        try:
            return int(res.rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def is_global_lock(self) -> bool:
        from .. import db
        val = await db.fetchval("SELECT value FROM remote_client_flags WHERE key=$1", _GLOBAL_LOCK_KEY)
        return bool(val)

    async def find_credential(self, token_sha256: str) -> CredentialMatch | None:
        from .. import db
        row = await db.fetchrow(
            "SELECT device_id, token_sha256 FROM remote_device_credentials WHERE token_sha256=$1",
            token_sha256)
        if row is None:
            return None
        return CredentialMatch(device_id=row["device_id"], token_sha256=row["token_sha256"])

    async def add_session(self, session: SessionRecord, token_sha256: str) -> None:
        from .. import db
        await db.execute(
            "INSERT INTO remote_device_sessions (id, device_id, token_sha256, revoked)"
            " VALUES ($1,$2,$3,$4)",
            session.id, session.device_id, token_sha256, session.revoked)

    async def find_session(self, token_sha256: str) -> CredentialMatch | None:
        from .. import db
        row = await db.fetchrow(
            "SELECT id, device_id, token_sha256 FROM remote_device_sessions WHERE token_sha256=$1",
            token_sha256)
        if row is None:
            return None
        return CredentialMatch(device_id=row["device_id"], token_sha256=row["token_sha256"],
                               session_id=row["id"])

    async def get_session(self, session_id: str) -> SessionRecord | None:
        from .. import db
        row = await db.fetchrow("SELECT * FROM remote_device_sessions WHERE id=$1", session_id)
        if row is None:
            return None
        return SessionRecord(
            id=row["id"], device_id=row["device_id"], revoked=row["revoked"],
            created_at=row["created_at"].timestamp() if row.get("created_at") else 0.0,
            last_seen=row["last_seen"].timestamp() if row.get("last_seen") else 0.0)

    async def touch_session(self, session_id: str) -> None:
        from .. import db
        await db.execute("UPDATE remote_device_sessions SET last_seen=now() WHERE id=$1", session_id)

    async def set_session_revoked(self, session_id: str, revoked: bool) -> bool:
        from .. import db
        res = await db.execute(
            "UPDATE remote_device_sessions SET revoked=$2 WHERE id=$1", session_id, revoked)
        return res.endswith("1")
