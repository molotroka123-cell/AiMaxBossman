"""Реестр аренд (lease ledger) — устранение P0-гонки OOM.

Проблема прототипа: `admit(snap, req)` НИЧЕГО не резервирует. N параллельных
`admit()` против одного и того же снимка все проходят (каждый по отдельности
влезает) и вместе кладут хост в OOM.

Решение: перед дорогой работой держатель берёт `ResourceLease` — бронь RAM/диска
в общем реестре. `acquire()` проверяет заявку против пула МИНУС уже удержанные
брони, поэтому второй параллельный вызов видит бронь первого и получает отказ
(`errors.ResourceExhausted`) вместо того, чтобы устроить OOM.

Потокобезопасность и корутинобезопасность: критическая секция защищена
`threading.Lock` и НЕ содержит `await`. В однопоточном asyncio это значит, что
две сгруппированные корутины не могут «переплестись» внутри проверки-и-вставки —
`acquire()` выполняется атомарно до конца. Часы (`clock`) инъектируются ради
детерминированных тестов TTL без реальных пауз.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .. import correlation
from ..errors import ResourceExhausted
from .models import ResourceLease, ResourceSnapshot, WorkloadRequest


def _new_lease_id() -> str:
    return correlation.new_id("lease_")


class LeaseLedger:
    """Внутрипроцессный реестр аренд. Эфемерен: восстанавливается пустым на
    рестарте (никакого нового durable-хранилища — это требование Этапа 4)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._leases: dict[str, ResourceLease] = {}
        self._lock = threading.Lock()

    # --- служебное (под замком) ---------------------------------------------

    def _sweep_locked(self) -> list[ResourceLease]:
        now = self._clock()
        dead = [lid for lid, lease in self._leases.items() if lease.expired(now)]
        return [self._leases.pop(lid) for lid in dead]

    def _held_locked(self) -> tuple[int, int]:
        ram = sum(l.ram for l in self._leases.values())
        disk = sum(l.disk for l in self._leases.values())
        return ram, disk

    # --- публичное API -------------------------------------------------------

    def sweep(self) -> list[ResourceLease]:
        """Снять протухшие брони. Возвращает освобождённые записи (для событий)."""
        with self._lock:
            return self._sweep_locked()

    def held(self) -> tuple[int, int]:
        """(зарезервированный ram, зарезервированный disk) после подметания."""
        with self._lock:
            self._sweep_locked()
            return self._held_locked()

    def active(self) -> list[ResourceLease]:
        """Копия активных аренд (после подметания)."""
        with self._lock:
            self._sweep_locked()
            return list(self._leases.values())

    def get(self, lease_id: str) -> ResourceLease | None:
        with self._lock:
            return self._leases.get(lease_id)

    def acquire(
        self,
        snap: ResourceSnapshot,
        req: WorkloadRequest,
        *,
        max_ram_pressure: float,
        disk_reserve: int,
        ttl: float,
        kind: str | None = None,
    ) -> ResourceLease:
        """Зарезервировать ёмкость под заявку или бросить `ResourceExhausted`.

        Проверка идёт против ПРОЕКЦИИ: доступное единого пула минус уже
        удержанные брони минус сама заявка. Это и закрывает гонку — бронь второго
        конкурента учитывает бронь первого. Проверка и вставка атомарны под
        замком без `await` внутри.
        """
        with self._lock:
            self._sweep_locked()
            held_ram, held_disk = self._held_locked()

            # Диск: единый резерв; учитываем и удержанные брони.
            disk_after = snap.disk_free - held_disk - req.estimated_disk
            if disk_after < disk_reserve:
                raise ResourceExhausted(
                    "disk reserve would be breached",
                    extra={
                        "kind": kind or req.kind,
                        "held_disk": held_disk,
                        "disk_free": snap.disk_free,
                        "requested_disk": req.estimated_disk,
                        "disk_reserve": disk_reserve,
                    },
                )

            # RAM: единый пул с учётом VRAM-претензии и всех броней.
            projected_available = snap.unified_available - held_ram - req.estimated_ram
            projected = (
                1.0 - (projected_available / snap.ram_total) if snap.ram_total else 0.0
            )
            if projected > max_ram_pressure:
                raise ResourceExhausted(
                    "ram pressure would exceed limit",
                    extra={
                        "kind": kind or req.kind,
                        "held_ram": held_ram,
                        "unified_available": snap.unified_available,
                        "requested_ram": req.estimated_ram,
                        "projected_pressure": round(projected, 4),
                        "max_ram_pressure": max_ram_pressure,
                    },
                )

            lease = ResourceLease(
                id=_new_lease_id(),
                kind=kind or req.kind,
                ram=req.estimated_ram,
                disk=req.estimated_disk,
                ttl=ttl,
                created_at=self._clock(),
                cid=correlation.current(),
            )
            self._leases[lease.id] = lease
            return lease

    def release(self, lease_id: str) -> ResourceLease | None:
        """Освободить бронь. Идемпотентно: повторный release вернёт None."""
        with self._lock:
            return self._leases.pop(lease_id, None)

    def clear(self) -> None:
        """Только для тестов: очистить реестр."""
        with self._lock:
            self._leases.clear()
