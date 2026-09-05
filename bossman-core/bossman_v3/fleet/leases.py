"""Reservations / Leasing с TTL, продлением и fencing-токенами (§13, инновация 1).

Fencing: на пару (узел, класс ресурса) держится монотонный счётчик; каждая
новая аренда получает следующий номер. Исполнитель, чья аренда истекла,
предъявляет устаревший номер — `valid()` его отвергает, и он не получает
власти над общим ресурсом только потому, что его процесс ещё жив.
"""
from __future__ import annotations

import uuid
import math
import time
from contextlib import contextmanager

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
                resource_class: str = "default", exclusive: bool = True, requirement=None) -> Lease:
        if not math.isfinite(now) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("lease time and TTL must be finite; TTL must be positive")
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                # истёкшие аренды на этом узле/классе снимаются перед проверкой
                con.execute("DELETE FROM fleet_leases WHERE node_id=? AND resource_class=? AND expires_ts<=?",
                            (node_id, resource_class, now))
                live = con.execute("SELECT * FROM fleet_leases WHERE node_id=? AND resource_class=?",
                                   (node_id, resource_class)).fetchall()
                if live and (exclusive or any(r["exclusive"] for r in live)):
                    raise LeaseConflict(f"{resource_class!r} has an incompatible live lease on {node_id}")
                if requirement is not None:
                    node = self.store.node(node_id)
                    if node is None:
                        raise LeaseConflict("node unavailable")
                    from .scheduler import FleetScheduler
                    from dataclasses import replace
                    reservations = con.execute(
                        "SELECT COALESCE(SUM(r.host_gb),0),COALESCE(SUM(r.gpu_gb),0),COUNT(*) "
                        "FROM fleet_leases l LEFT JOIN fleet_memory_reservations r ON l.lease_id=r.lease_id "
                        "WHERE l.node_id=? AND l.expires_ts>?", (node_id, now)).fetchone()
                    host, gpu, count = reservations
                    if node.unified_memory:
                        available = max(0, min(node.ram_free_gb, (node.gpu_free_gb if node.gpu_memory_gb > 0 else node.ram_free_gb)) - host - gpu)
                        effective = replace(node, ram_used_gb=node.ram_gb-available,
                                            gpu_memory_used_gb=max(0, node.gpu_memory_gb-available),
                                            active_work=max(count, node.active_work))
                    else:
                        effective = replace(node, ram_used_gb=node.ram_used_gb+host,
                                            gpu_memory_used_gb=node.gpu_memory_used_gb+gpu,
                                            active_work=max(count, node.active_work))
                    rejected = FleetScheduler().reject_reasons(effective, requirement)
                    if rejected:
                        raise LeaseConflict("atomic admission rejected: " + ";".join(rejected))
                fence = self.store.next_fence(con, node_id, resource_class)
                lease = Lease(str(uuid.uuid4()), node_id, work_id, resource_class, exclusive, now, now + ttl_seconds, fence)
                self.store.save_lease(con, lease)
                if requirement is not None:
                    con.execute("INSERT INTO fleet_memory_reservations VALUES(?,?,?)",
                                (lease.lease_id, requirement.min_ram_gb, requirement.min_gpu_memory_gb))
                con.execute("COMMIT")
                return lease
            except Exception:
                con.execute("ROLLBACK")
                raise

    def renew(self, lease: Lease, *, now: float, ttl_seconds: float) -> Lease:
        if not math.isfinite(now) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("invalid renewal time or TTL")
        if not lease.alive(now):
            raise StaleLease("expired capability cannot be renewed")
        with self.store.connect() as con:
            changed = con.execute(
                "UPDATE fleet_leases SET expires_ts=? WHERE lease_id=? AND node_id=? "
                "AND work_id=? AND fence=? AND expires_ts>?",
                (now + ttl_seconds, lease.lease_id, lease.node_id, lease.work_id, lease.fence, now)).rowcount
            if changed != 1:
                raise StaleLease("expired, reclaimed or mismatched lease")
        return Lease(lease.lease_id, lease.node_id, lease.work_id, lease.resource_class, lease.exclusive,
                     lease.acquired_ts, now + ttl_seconds, lease.fence)

    def release(self, lease: Lease) -> bool:
        return self.store.delete_lease(lease.lease_id)

    def valid(self, lease: Lease, *, now: float) -> tuple[bool, str]:
        """Check this persisted lease capability; concurrent shared leases remain valid."""
        if not lease.alive(now):
            return False, "expired"
        current = [l for l in self.store.leases(node_id=lease.node_id) if l.resource_class == lease.resource_class]
        if not any(l.lease_id == lease.lease_id for l in current):
            return False, "reclaimed"
        persisted = next(l for l in current if l.lease_id == lease.lease_id)
        if (persisted.node_id, persisted.work_id, persisted.fence, persisted.exclusive) != (
                lease.node_id, lease.work_id, lease.fence, lease.exclusive):
            return False, "lease capability mismatch"
        if not persisted.alive(now):
            return False, "expired"
        return True, "valid"

    @contextmanager
    def mutation_guard(self, lease: Lease):
        """Serialize lease replacement against local mutations at the effect boundary."""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT * FROM fleet_leases WHERE lease_id=?", (lease.lease_id,)).fetchone()
                if (row is None or row["fence"] != lease.fence or row["work_id"] != lease.work_id
                        or row["node_id"] != lease.node_id or row["expires_ts"] <= time.time()):
                    raise StaleLease("execution lease is stale; effect refused")
                yield
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

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
