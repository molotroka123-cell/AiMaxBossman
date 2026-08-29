"""Stage 8 — Secret Broker.

Сырые продовые секреты НИКОГДА не попадают в песочницу. Брокер выдаёт
короткоживущий scoped grant, привязанный к конкретной песочнице. Материал
секрета резолвит control plane в момент брокерируемого запроса (redeem) —
песочница получает только grant-id, не сам секрет. grant/scope/TTL/revoke/
binding. In-memory backend для тестов; интерфейс готов под persistent backend.
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
