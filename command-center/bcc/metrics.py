"""Метрики железа (psutil): CPU/RAM/диск + GPU по возможности.

Сэмпл раз в 10 с в system_metrics, событие system.metrics — в живую ленту.
GPU определяется best effort (nvidia-smi / sysfs); нет GPU — просто null.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import psutil
import sqlalchemy as sa

from .db import Database, rows_dicts, system_metrics as metrics_t, utcnow
from .events import EventBus

SAMPLE_SECONDS = 10.0
RETENTION_HOURS = 24


class MetricsSampler:
    def __init__(self, db: Database, bus: EventBus, *, interval: float = SAMPLE_SECONDS,
                 retention_hours: int = RETENTION_HOURS, disk_path: str = "/"):
        self.db = db
        self.bus = bus
        self.interval = interval
        self.retention_hours = retention_hours
        self.disk_path = disk_path
        self.last_tick: float = 0.0
        self.last_sample: dict | None = None

    def read(self) -> dict:
        """Мгновенный снимок (без обращения к БД)."""
        vm = psutil.virtual_memory()
        try:
            du = psutil.disk_usage(self.disk_path)
            disk_used_gb = round(du.used / 1024 ** 3, 2)
            disk_total_gb = round(du.total / 1024 ** 3, 2)
        except OSError:
            disk_used_gb = disk_total_gb = None
        return {
            "ts": utcnow(),
            "cpu_pct": psutil.cpu_percent(interval=None),
            "ram_used_mb": round((vm.total - vm.available) / 1024 ** 2, 1),
            "ram_total_mb": round(vm.total / 1024 ** 2, 1),
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "gpu": gpu_info(),
        }

    async def sample(self) -> dict:
        """Снять метрики, записать в БД, отдать в шину."""
        self.last_tick = time.monotonic()
        data = self.read()
        self.last_sample = data
        async with self.db.session() as s:
            await s.execute(sa.insert(metrics_t).values(**data))
            await s.execute(sa.delete(metrics_t).where(
                metrics_t.c.ts < utcnow() - timedelta(hours=self.retention_hours)))
            await s.commit()
        await self.bus.emit("system.metrics", **{k: (v.isoformat() if k == "ts" else v)
                                                 for k, v in data.items()})
        return data

    async def history(self, minutes: int = 15) -> list[dict]:
        async with self.db.session() as s:
            res = await s.execute(sa.select(metrics_t).where(
                metrics_t.c.ts >= utcnow() - timedelta(minutes=minutes)).order_by(metrics_t.c.ts))
            return rows_dicts(res.fetchall())

    async def loop(self) -> None:
        psutil.cpu_percent(interval=None)   # первый вызов psutil всегда 0.0 — прогреваем
        while True:
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.bus.emit("metrics.error", message=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(self.interval)


_gpu_cache: tuple[float, list[dict] | None] = (0.0, None)
GPU_CACHE_TTL = 30.0


def gpu_info() -> list[dict] | None:
    """GPU best effort: nvidia-smi, иначе amdgpu через sysfs, иначе None."""
    global _gpu_cache
    now = time.monotonic()
    if now - _gpu_cache[0] < GPU_CACHE_TTL:
        return _gpu_cache[1]
    info = _nvidia() or _sysfs_drm()
    _gpu_cache = (now, info)
    return info


def _nvidia() -> list[dict] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    query = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    try:
        out = subprocess.run([exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append({"name": parts[0], "util_pct": _num(parts[1]), "vram_used_mb": _num(parts[2]),
                     "vram_total_mb": _num(parts[3]), "temp_c": _num(parts[4])})
    return gpus or None


def _sysfs_drm() -> list[dict] | None:
    """Встроенная/AMD карта: имя из sysfs, загрузка — если ядро её отдаёт."""
    root = Path("/sys/class/drm")
    if not root.exists():
        return None
    gpus = []
    for card in sorted(root.glob("card[0-9]")):
        device = card / "device"
        name = _read(device / "product_name") or _read(device / "uevent_name") or card.name
        busy = _read(device / "gpu_busy_percent")
        vram_used = _read(device / "mem_info_vram_used")
        vram_total = _read(device / "mem_info_vram_total")
        gpus.append({
            "name": name,
            "util_pct": _num(busy),
            "vram_used_mb": round(int(vram_used) / 1024 ** 2, 1) if (vram_used or "").isdigit() else None,
            "vram_total_mb": round(int(vram_total) / 1024 ** 2, 1) if (vram_total or "").isdigit() else None,
            "temp_c": None,
        })
    return gpus or None


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _num(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
