"""Credential Broker (§19) — авторизация без секретов.

Флот хранит ТОЛЬКО грант: secret_id, узел, способность, скоуп, срок, кто выдал.
Значение секрета живёт в доверенном хранилище проекта (V2 `bcc.secrets.Vault`,
Fernet at rest) и внедряется рантаймом через `SecretProvider` в момент
исполнения — и только если грант действителен. Плоский ключ никогда не
попадает ни в fleet-store, ни в журнал, ни в объяснения размещения.

Выдающий — типизированный доверенный principal (human:* / policy:*), как в
bossman.company: модель/агент грант выдать не может.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from bossman.company.runtime import untrusted_approver_reason

from .models import CredentialGrant
from .store import FleetStore


class SecretProvider(Protocol):
    def resolve(self, secret_id: str) -> str: ...


class GrantDenied(PermissionError):
    pass


class CredentialBroker:
    def __init__(self, store: FleetStore, provider: SecretProvider | None = None) -> None:
        self.store = store
        self.provider = provider

    def grant(self, *, secret_id: str, node_id: str, capability: str, scope: str, expires_ts: float,
              granted_by: str) -> CredentialGrant:
        why = untrusted_approver_reason(granted_by)
        if why:
            raise GrantDenied(why)
        g = CredentialGrant(str(uuid.uuid4()), secret_id, node_id, capability, scope, expires_ts, granted_by)
        self.store.save_grant(g)
        return g

    def revoke(self, grant_id: str) -> bool:
        for g in self.store.grants():
            if g.grant_id == grant_id and not g.revoked:
                self.store.save_grant(CredentialGrant(**{**g.to_dict(), "revoked": True}))
                return True
        return False

    def authorized(self, *, secret_id: str, node_id: str, capability: str, scope: str, now: float) -> CredentialGrant | None:
        for g in self.store.grants():
            if (g.secret_id, g.node_id, g.capability) == (secret_id, node_id, capability) and not g.revoked \
                    and g.expires_ts > now and _scope_covers(g.scope, scope):
                return g
        return None

    def resolve(self, *, secret_id: str, node_id: str, capability: str, scope: str, now: float) -> str:
        """Единственный путь к значению секрета — и он проходит через грант."""
        if self.provider is None:
            raise GrantDenied("no secret provider attached to the fleet broker")
        if self.authorized(secret_id=secret_id, node_id=node_id, capability=capability, scope=scope, now=now) is None:
            raise GrantDenied(f"node {node_id!r} has no live grant for {secret_id!r}/{capability!r} in {scope!r}")
        return self.provider.resolve(secret_id)


def _scope_covers(granted: str, requested: str) -> bool:
    if granted == "organization":
        return True
    return granted == requested
