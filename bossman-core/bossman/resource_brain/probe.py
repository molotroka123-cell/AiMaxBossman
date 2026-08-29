"""Пробы ресурсов: измеряют единый пул хоста. Absent-safe и подключаемые.

На этой Linux-коробке GPU может отсутствовать — тогда работает CPU-only проба
через `os.sysconf` (SC_PHYS_PAGES/SC_AVPHYS_PAGES) + `shutil.disk_usage`, без
каких-либо внешних зависимостей.

Будущий AMD-адаптер (Ryzen AI Max 395, unified 128 ГБ) читает amdgpu sysfs /
rocm-smi и сообщает VRAM как ПРЕТЕНЗИЮ (`gpu_memory_used`) к тому же пулу
`ram_total`, НИКОГДА не прибавляя VRAM к пулу. Он подключается после
runtime-детекта через общий протокол `ProbeAdapter`; сама детекция и все чтения
sysfs обёрнуты в try/except, чтобы отсутствие GPU/rocm не роняло пробу.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..obs import get_logger
from .models import ResourceSnapshot

_log = get_logger("bossman.resource_brain")

_ROOT_PATH = "/"


@runtime_checkable
class ProbeAdapter(Protocol):
    """Контракт адаптера пробы. Реализация обязана быть absent-safe: `available()`
    не бросает, `snapshot()` при частичном сбое отдаёт хотя бы CPU-часть."""

    name: str

    def available(self) -> bool: ...

    def snapshot(self, model_resident: tuple[str, ...] = ()) -> ResourceSnapshot: ...


def _cpu_pool() -> tuple[int, int]:
    """(ram_total, ram_available) единого пула в байтах через sysconf. Absent-safe:
    на платформах без sysconf вернёт нули, а не упадёт."""
    try:
        page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        pages = os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
        avail_pages = os.sysconf("SC_AVPHYS_PAGES") if hasattr(os, "sysconf") else pages
    except (ValueError, OSError):  # неизвестное имя sysconf на экзотической ОС
        return 0, 0
    return page * pages, page * avail_pages


def _disk(path: str = _ROOT_PATH) -> tuple[int, int]:
    try:
        du = shutil.disk_usage(path)
        return du.total, du.free
    except OSError:
        return 0, 0


class CpuProbe:
    """CPU-only проба единого пула. Работает всюду; VRAM-претензии не сообщает."""

    name = "cpu"

    def available(self) -> bool:
        return True

    def snapshot(self, model_resident: tuple[str, ...] = ()) -> ResourceSnapshot:
        ram_total, ram_available = _cpu_pool()
        disk_total, disk_free = _disk()
        return ResourceSnapshot(
            ram_total=ram_total,
            ram_available=ram_available,
            disk_total=disk_total,
            disk_free=disk_free,
            unified=True,
            model_resident=model_resident,
            probe=self.name,
            ts=time.time(),
        )


class AmdUnifiedProbe:
    """Проба для Ryzen AI Max (amdgpu, единая память).

    ram_total остаётся физическим единым пулом (sysconf), а VRAM сообщается как
    `gpu_memory_used` — претензия к ЭТОМУ ЖЕ пулу, не отдельный ресурс. Все
    чтения sysfs обёрнуты; при любой проблеме проба откатывается к CPU-части.
    """

    name = "amd-unified"
    _DRM_GLOB = "/sys/class/drm/card*/device"

    def _vram_nodes(self) -> list[Path]:
        try:
            return sorted(Path("/sys/class/drm").glob("card*/device"))
        except OSError:
            return []

    def available(self) -> bool:
        try:
            for dev in self._vram_nodes():
                if (dev / "mem_info_vram_total").exists():
                    return True
        except OSError:
            return False
        return False

    def _read_int(self, p: Path) -> int | None:
        try:
            return int(p.read_text().strip())
        except (OSError, ValueError):
            return None

    def snapshot(self, model_resident: tuple[str, ...] = ()) -> ResourceSnapshot:
        ram_total, ram_available = _cpu_pool()
        disk_total, disk_free = _disk()
        vram_used: int | None = None
        vram_total: int | None = None
        for dev in self._vram_nodes():
            total = self._read_int(dev / "mem_info_vram_total")
            used = self._read_int(dev / "mem_info_vram_used")
            if total is not None:
                vram_total = (vram_total or 0) + total
            if used is not None:
                vram_used = (vram_used or 0) + used
        return ResourceSnapshot(
            ram_total=ram_total,
            ram_available=ram_available,
            disk_total=disk_total,
            disk_free=disk_free,
            gpu_memory_used=vram_used,     # претензия к единому пулу
            gpu_memory_total=vram_total,   # справочно, в пул НЕ входит
            unified=True,
            model_resident=model_resident,
            probe=self.name,
            ts=time.time(),
        )


def detect_probe() -> ProbeAdapter:
    """Выбрать адаптер по runtime-детекту: AMD-unified если доступен, иначе CPU.
    Никогда не бросает — при любой ошибке детекта возвращает CpuProbe."""
    try:
        amd = AmdUnifiedProbe()
        if amd.available():
            _log.info("resource probe: amd-unified detected")
            return amd
    except Exception as exc:  # noqa: BLE001 — детект не должен ронять старт
        _log.warning("amd probe detect failed, falling back to cpu: %s", exc)
    return CpuProbe()


def snapshot(model_resident: tuple[str, ...] = ()) -> ResourceSnapshot:
    """Удобная обёртка: снять снимок актуальным адаптером."""
    return detect_probe().snapshot(model_resident)
