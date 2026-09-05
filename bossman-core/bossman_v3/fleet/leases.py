"""Reservations / Leasing с TTL, продлением и fencing-токенами (§13, инновация 1).

Fencing: на пару (узел, класс ресурса) держится монотонный счётчик; каждая
новая аренда получает следующий номер. Исполнитель, чья аренда истекла,
предъявляет устаревший номер — `valid()` его отвергает, и он не получает
власти над общим ресурсом только потому, что его процесс ещё жив.
"""
from __future__ import annotations

import uuid

from .models import Lease
from .store import FleetStore


class LeaseConflict(RuntimeError):
    pass


class StaleLease(PermissionError):
    pass


class LeaseManager:
    def __init__(self, store: FleetStore) -> None:
        self.store = store

    def acquire(self, *, node_id: str, work_id: str, now: float, ttl_seconds: float,
                resource_class: str = "default", exclusive: bool = True) -> Lease:
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                # истёкшие аренды на этом узле/классе снимаются перед проверкой
                con.execute("DELETE FROM fleet_leases WHERE node_id=? AND resource_class=? AND expires_ts<=?",
                            (node_id, resource_class, now))
                if exclusive:
                    live = con.execute("SELECT lease_id FROM fleet_leases WHERE node_id=? AND resource_class=? AND exclusive=1",
                                       (node_id, resource_class)).fetchone()
                    if live:
                        con.execute("ROLLBACK")
                        raise LeaseConflict(f"{resource_class!r} already exclusively leased on {node_id}")
                fence = self.store.next_fence(con, node_id, resource_class)
                lease = Lease(str(uuid.uuid4()), node_id, work_id, resource_class, exclusive, now, now + ttl_seconds, fence)
                self.store.save_lease(con, lease)
                con.execute("COMMIT")
                return lease
            except LeaseConflict:
                raise
            except Exception:
                con.execute("ROLLBACK")
                raise

    def renew(self, lease: Lease, *, now: float, ttl_seconds: float) -> Lease:
        if not lease.alive(now):
            raise StaleLease(f"lease {lease.lease_id} expired at {lease.expires_ts}; renewal refused")
        if not self.store.update_lease_expiry(lease.lease_id, now + ttl_seconds):
            raise StaleLease(f"lease {lease.lease_id} no longer exists (reclaimed)")
        return Lease(lease.lease_id, lease.node_id, lease.work_id, lease.resource_class, lease.exclusive,
                     lease.acquired_ts, now + ttl_seconds, lease.fence)

    def release(self, lease: Lease) -> bool:
        return self.store.delete_lease(lease.lease_id)

    def valid(self, lease: Lease, *, now: float) -> tuple[bool, str]:
        """Аренда действительна ⇔ не истекла, существует, и её fence — текущий
        максимум на (узел, класс). Более новая аренда делает старую stale."""
        if not lease.alive(now):
            return False, "expired"
        current = [l for l in self.store.leases(node_id=lease.node_id) if l.resource_class == lease.resource_class]
        if not any(l.lease_id == lease.lease_id for l in current):
            return False, "reclaimed"
        top = max(l.fence for l in current)
        if lease.fence < top:
            return False, f"fenced: token {lease.fence} < current {top}"
        return True, "valid"

    def expire(self, now: float) -> list[Lease]:
        gone = self.store.expired_leases(now)
        for l in gone:
            self.store.delete_lease(l.lease_id)
        return gone

    def reclaim_node(self, node_id: str) -> list[Lease]:
        """Узел потерян/уходит в OFFLINE — все его аренды снимаются."""
        gone = self.store.leases(node_id=node_id)
        for l in gone:
            self.store.delete_lease(l.lease_id)
        return gone
