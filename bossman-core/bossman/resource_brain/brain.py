"""Resource Brain: измеряет единый пул, принимает/отклоняет нагрузку и ранжирует
модели. Ничего не запускает сам и НЕ ходит к моделям/в сеть — только измеряет и
допускает (граница cloud_policy неприкосновенна).

Два уровня приёма:
- `admit(snap, req)` — чистая stateless-проверка (быстрый предикат, его же
  используют приёмочные тесты). Ничего не резервирует.
- `acquire(req)` / `release(id)` — РЕАЛЬНЫЙ гейт через реестр аренд: резервирует
  ёмкость ДО дорогой работы, чтобы не устроить OOM. Именно его должен звать
  runner перед запуском задачи.
"""
from __future__ import annotations

from typing import Any

from .. import events
from ..errors import ResourceExhausted
from .ledger import LeaseLedger
from .models import (
    AdmissionDecision,
    ModelResidency,
    PressureLevel,
    ResourceLease,
    ResourceSnapshot,
    WorkloadRequest,
)

# Дефолты продовые: ~88% давления как потолок и 20 ГиБ резерва диска.
_DEFAULT_MAX_PRESSURE = 0.88
_DEFAULT_DISK_RESERVE = 20 * 1024 ** 3
_DEFAULT_LEASE_TTL = 300.0  # сек: страховка от зависшей брони упавшего держателя


class ResourceBrain:
    """Ядро Этапа 4. Держит последний снимок, реестр аренд и учёт резидентности.

    Конструктор совместим с прототипом: `ResourceBrain(max_ram_pressure=,
    disk_reserve=)` — эти имена завязаны в приёмочном тесте."""

    def __init__(
        self,
        *,
        max_ram_pressure: float = _DEFAULT_MAX_PRESSURE,
        disk_reserve: int = _DEFAULT_DISK_RESERVE,
        default_lease_ttl: float = _DEFAULT_LEASE_TTL,
        ledger: LeaseLedger | None = None,
        residency: ModelResidency | None = None,
    ) -> None:
        self.max_ram_pressure = max_ram_pressure
        self.disk_reserve = disk_reserve
        self.default_lease_ttl = default_lease_ttl
        self.ledger = ledger or LeaseLedger()
        self.residency = residency or ModelResidency()
        self._snapshot: ResourceSnapshot | None = None

    # --- снимок --------------------------------------------------------------

    def set_snapshot(self, snap: ResourceSnapshot) -> None:
        """Обновить живой снимок (зовёт фоновый цикл пробы)."""
        self._snapshot = snap

    @property
    def current_snapshot(self) -> ResourceSnapshot | None:
        return self._snapshot

    def pressure_level(self, snap: ResourceSnapshot | None = None) -> PressureLevel:
        s = snap or self._snapshot
        if s is None:
            return PressureLevel.NOMINAL
        return s.pressure_level

    # --- stateless-приём (контракт прототипа; ничего не резервирует) ---------

    def admit(self, snap: ResourceSnapshot, req: WorkloadRequest) -> AdmissionDecision:
        """Быстрый предикат: влезет ли заявка в снимок ПРЯМО СЕЙЧАС, без учёта
        уже выданных аренд. Использует единый пул (`unified_available`), поэтому
        VRAM не задваивается. Реальный гейт с бронью — `acquire()`.
        """
        # Диск: единый резерв.
        if snap.disk_free - req.estimated_disk < self.disk_reserve:
            projected = self._projected_pressure(snap, req)
            return AdmissionDecision(False, "disk_reserve", projected)
        # RAM: давление на единый пул после гипотетического приёма.
        projected = self._projected_pressure(snap, req)
        if projected > self.max_ram_pressure:
            return AdmissionDecision(False, "ram_pressure", projected)
        return AdmissionDecision(True, "ok", projected, req.model)

    def _projected_pressure(self, snap: ResourceSnapshot, req: WorkloadRequest) -> float:
        if not snap.ram_total:
            return 0.0
        return 1.0 - ((snap.unified_available - req.estimated_ram) / snap.ram_total)

    # --- stateful-гейт с бронью (устранение OOM-гонки) -----------------------

    def acquire(
        self,
        req: WorkloadRequest,
        snap: ResourceSnapshot | None = None,
        *,
        ttl: float | None = None,
        kind: str | None = None,
    ) -> ResourceLease:
        """Зарезервировать ёмкость под заявку через реестр аренд или бросить
        `ResourceExhausted`. Если снимок не передан — берётся живой снимок пробы;
        его отсутствие трактуется консервативно (отказ), а не как «всё свободно».
        """
        s = snap or self._snapshot
        if s is None:
            raise ResourceExhausted(
                "no resource snapshot yet; refusing to admit blindly",
                extra={"kind": kind or req.kind},
            )
        lease = self.ledger.acquire(
            s,
            req,
            max_ram_pressure=self.max_ram_pressure,
            disk_reserve=self.disk_reserve,
            ttl=ttl if ttl is not None else self.default_lease_ttl,
            kind=kind,
        )
        events.emit(
            "resource.lease_acquired",
            lease_id=lease.id,
            lease_kind=lease.kind,
            ram=lease.ram,
            disk=lease.disk,
            ttl=lease.ttl,
        )
        return lease

    def release(self, lease_id: str) -> bool:
        """Освободить бронь. Идемпотентно (повторный вызов вернёт False)."""
        lease = self.ledger.release(lease_id)
        if lease is None:
            return False
        events.emit(
            "resource.lease_released",
            lease_id=lease.id,
            lease_kind=lease.kind,
            ram=lease.ram,
            disk=lease.disk,
        )
        return True

    def sweep(self) -> list[ResourceLease]:
        """Снять протухшие брони (зовёт фоновый цикл). Эмитит по одному событию
        на реквизированную бронь."""
        reclaimed = self.ledger.sweep()
        for lease in reclaimed:
            events.emit(
                "resource.lease_expired",
                lease_id=lease.id,
                lease_kind=lease.kind,
                ram=lease.ram,
                disk=lease.disk,
                ttl=lease.ttl,
            )
        return reclaimed

    def leases(self) -> list[ResourceLease]:
        return self.ledger.active()

    def held(self) -> tuple[int, int]:
        return self.ledger.held()

    # --- скорер моделей (переиспользует роутер gateway) ----------------------

    def rank_models(
        self, snap: ResourceSnapshot, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ранжировать модели-кандидаты под снимок. Ключ сортировки (по убыванию):

            (fits, health, resident, -ram, -latency)

        то есть влезающая по памяти важнее здоровой, здоровая — важнее уже
        поднятой, дальше меньше памяти и меньше латентность. Резидентность берём
        и из снимка (`model_resident`), и из учёта `self.residency`."""
        resident = set(snap.model_resident) | set(self.residency.resident)
        avail = snap.unified_available

        def score(m: dict[str, Any]) -> tuple[int, int, int, int, float]:
            health = 1 if str(m.get("health", "healthy")) == "healthy" else 0
            is_resident = 1 if m.get("id") in resident else 0
            ram = int(m.get("ram_estimate", 0) or 0)
            fits = 1 if ram <= avail else 0
            latency = float(m.get("latency_ms", 0) or 0)
            return (fits, health, is_resident, -ram, -latency)

        return sorted(candidates, key=score, reverse=True)
