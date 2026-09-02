"""Stage 8 — Secret Broker.

Сырые продовые секреты НИКОГДА не попадают в песочницу. Брокер выдаёт
короткоживущий scoped grant, привязанный к конкретной песочнице. Материал
секрета резолвит control plane в момент брокерируемого запроса (redeem) —
песочница получает только grant-id, не сам секрет. grant/scope/TTL/revoke/
binding. In-memory backend для тестов; интерфейс готов под persistent backend.

F-018 disposition: GATED_NON_PROTECTIVE (не удалять — Stage 8 тесты и контракт).
Хук для брокера в менеджере ЕСТЬ (`SandboxManager(..., broker=...)`,
`grant_secret()`), но продакшн-инстанс в `sandbox/subsystem.py` создаётся БЕЗ
брокера: `grant_secret()` при `broker is None` → `SecretDenied("no secret broker
configured")`, т.е. путь fail-closed — секреты в песочницу не выдаются вовсе.
Этот модуль сам по себе ничего не защищает, пока оператор не передаст
`PostgresSecretBroker(resolver)`/`InMemorySecretBroker(resolver)` в менеджер.
"""
from __future__ import annotations

import time
from typing import Callable, Protocol, runtime_checkable

from .. import errors
from .models import SecretGrant, new_id


@runtime_checkable
class SecretMaterialResolver(Protocol):
    """Control-plane резолвер: по scope возвращает сырой материал. В проде —
    обёртка над персистентным хранилищем/HSM. Никогда не отдаётся в песочницу."""
    def __call__(self, scope: str) -> str | None: ...


class SecretBrokerBackend(Protocol):
    def grant(self, sandbox_id: str, scope: str, ttl_seconds: float) -> SecretGrant: ...
    def revoke(self, grant_id: str) -> bool: ...
    def revoke_sandbox(self, sandbox_id: str) -> int: ...
    def redeem(self, grant_id: str, sandbox_id: str) -> str: ...


class InMemorySecretBroker:
    """Тестовый/дев-backend. Продовый persistent backend реализует тот же контракт."""

    def __init__(self, resolver: SecretMaterialResolver, *, allowed_scopes: frozenset[str] | None = None) -> None:
        self._resolver = resolver
        self._allowed = allowed_scopes  # None = любой scope, который резолвер знает
        self._grants: dict[str, SecretGrant] = {}

    def grant(self, sandbox_id: str, scope: str, ttl_seconds: float) -> SecretGrant:
        if ttl_seconds <= 0:
            raise errors.SecretDenied("grant TTL must be positive")
        if self._allowed is not None and scope not in self._allowed:
            raise errors.SecretDenied(f"scope '{scope}' not permitted", extra={"scope": scope})
        # Не выдаём grant на неизвестный секрет (иначе redeem всегда пустой).
        if self._resolver(scope) is None:
            raise errors.SecretDenied(f"no secret material for scope '{scope}'")
        g = SecretGrant(id=new_id("sg"), sandbox_id=sandbox_id, scope=scope,
                        issued_at=time.time(), ttl_seconds=float(ttl_seconds))
        self._grants[g.id] = g
        return g

    def revoke(self, grant_id: str) -> bool:
        g = self._grants.get(grant_id)
        if not g or g.revoked:
            return False
        g.revoked = True
        return True

    def revoke_sandbox(self, sandbox_id: str) -> int:
        n = 0
        for g in self._grants.values():
            if g.sandbox_id == sandbox_id and not g.revoked:
                g.revoked = True
                n += 1
        return n

    def redeem(self, grant_id: str, sandbox_id: str) -> str:
        """Только control plane. Проверяет привязку/TTL/revocation, затем
        возвращает материал. Песочница этот вызов не делает."""
        g = self._grants.get(grant_id)
        if g is None:
            raise errors.SecretDenied("unknown grant")
        if g.sandbox_id != sandbox_id:
            raise errors.SecretDenied("grant not bound to this sandbox")
        if not g.is_valid():
            raise errors.SecretDenied("grant revoked or expired")
        material = self._resolver(g.scope)
        if material is None:
            raise errors.SecretDenied("secret material unavailable")
        return material

    def active_grants(self, sandbox_id: str) -> list[SecretGrant]:
        return [g for g in self._grants.values() if g.sandbox_id == sandbox_id and g.is_valid()]


# --------------------------------------------------------------------------
# Персистентный backend (Postgres) — тот же контракт SecretBrokerBackend
# --------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS sandbox_secret_grants (
    id           text PRIMARY KEY,
    sandbox_id   text NOT NULL,
    scope        text NOT NULL,
    issued_at    double precision NOT NULL,
    ttl_seconds  double precision NOT NULL,
    revoked      boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS sandbox_secret_grants_sandbox
    ON sandbox_secret_grants (sandbox_id) WHERE revoked = false;
"""


class PostgresSecretBroker:
    """Гранты живут в Postgres; МАТЕРИАЛ СЕКРЕТА В БАЗЕ НЕ ХРАНИТСЯ — строка
    гранта несёт только scope, а сам секрет резолвится control-plane'ом в момент
    redeem. Утечка таблицы грантов не раскрывает ни одного секрета.

    `db` — модуль/объект с async execute/fetchrow (по умолчанию bossman.db),
    что позволяет тестировать против фейка без живого Postgres.
    """

    def __init__(self, resolver: SecretMaterialResolver, *, db=None,
                 allowed_scopes: frozenset[str] | None = None) -> None:
        self._resolver = resolver
        self._allowed = allowed_scopes
        if db is None:
            from .. import db as _db
            db = _db
        self._db = db

    async def ensure_schema(self) -> None:
        await self._db.execute(DDL)

    async def grant(self, sandbox_id: str, scope: str, ttl_seconds: float) -> SecretGrant:
        if ttl_seconds <= 0:
            raise errors.SecretDenied("grant TTL must be positive")
        if self._allowed is not None and scope not in self._allowed:
            raise errors.SecretDenied(f"scope '{scope}' not permitted", extra={"scope": scope})
        if self._resolver(scope) is None:
            raise errors.SecretDenied(f"no secret material for scope '{scope}'")
        g = SecretGrant(id=new_id("sg"), sandbox_id=sandbox_id, scope=scope,
                        issued_at=time.time(), ttl_seconds=float(ttl_seconds))
        await self._db.execute(
            "INSERT INTO sandbox_secret_grants (id, sandbox_id, scope, issued_at, ttl_seconds, revoked)"
            " VALUES ($1,$2,$3,$4,$5,false)",
            g.id, g.sandbox_id, g.scope, g.issued_at, g.ttl_seconds)
        return g

    async def revoke(self, grant_id: str) -> bool:
        row = await self._db.fetchrow(
            "UPDATE sandbox_secret_grants SET revoked=true"
            " WHERE id=$1 AND revoked=false RETURNING id", grant_id)
        return row is not None

    async def revoke_sandbox(self, sandbox_id: str) -> int:
        rows = await self._db.fetch(
            "UPDATE sandbox_secret_grants SET revoked=true"
            " WHERE sandbox_id=$1 AND revoked=false RETURNING id", sandbox_id)
        return len(rows or [])

    async def redeem(self, grant_id: str, sandbox_id: str) -> str:
        row = await self._db.fetchrow(
            "SELECT id, sandbox_id, scope, issued_at, ttl_seconds, revoked"
            " FROM sandbox_secret_grants WHERE id=$1", grant_id)
        if row is None:
            raise errors.SecretDenied("unknown grant")
        if row["sandbox_id"] != sandbox_id:
            raise errors.SecretDenied("grant not bound to this sandbox")
        g = SecretGrant(id=row["id"], sandbox_id=row["sandbox_id"], scope=row["scope"],
                        issued_at=float(row["issued_at"]), ttl_seconds=float(row["ttl_seconds"]),
                        revoked=bool(row["revoked"]))
        if not g.is_valid():
            raise errors.SecretDenied("grant revoked or expired")
        material = self._resolver(g.scope)
        if material is None:
            raise errors.SecretDenied("secret material unavailable")
        return material
