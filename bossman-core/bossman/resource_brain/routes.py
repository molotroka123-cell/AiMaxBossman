"""Read-only HTTP-эндпоинты Resource Brain.

Только чтение: снимок пула, активные брони, уровень давления. НИКАКИХ мутаций —
выдача/снятие аренд идёт из runner через `brain.acquire/release`, а не через
внешний HTTP (иначе эндпоинт стал бы вектором злоупотребления ёмкостью).
Ошибки поднимаются как BossmanError и рендерятся глобальным обработчиком.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..obs import get_logger
from .probe import detect_probe

_log = get_logger("bossman.resource_brain")

router = APIRouter(prefix="/resource", tags=["resource"])


def _brain():
    # Ленивый доступ к синглтону пакета — избегаем циклического импорта с __init__.
    from . import BRAIN
    return BRAIN


def _live_snapshot():
    """Живой снимок из фонового цикла, либо разовая absent-safe проба (fallback,
    если цикл ещё не поднялся — например, до startup)."""
    brain = _brain()
    snap = brain.current_snapshot
    if snap is None:
        snap = detect_probe().snapshot(brain.residency.as_tuple())
        brain.set_snapshot(snap)
    return snap


@router.get("/snapshot")
async def get_snapshot() -> dict:
    """Текущий снимок единого пула (числа, без секретов)."""
    return _live_snapshot().to_event()


@router.get("/leases")
async def get_leases() -> dict:
    """Активные брони и суммарно удержанная ёмкость."""
    brain = _brain()
    held_ram, held_disk = brain.held()
    return {
        "leases": [l.to_public() for l in brain.leases()],
        "held_ram": held_ram,
        "held_disk": held_disk,
    }


@router.get("/pressure")
async def get_pressure() -> dict:
    """Уровень давления единого пула с учётом VRAM-претензии и удержанных броней."""
    brain = _brain()
    snap = _live_snapshot()
    held_ram, held_disk = brain.held()
    return {
        "probe": snap.probe,
        "unified": snap.unified,
        "ram_pressure": round(snap.ram_pressure, 4),
        "pressure_level": snap.pressure_level.value,
        "unified_available": snap.unified_available,
        "pool_total": snap.pool_total,
        "held_ram": held_ram,
        "held_disk": held_disk,
        "max_ram_pressure": brain.max_ram_pressure,
        "disk_reserve": brain.disk_reserve,
    }
