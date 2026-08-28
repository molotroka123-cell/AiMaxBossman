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
# Кэш обязан быть КОРОЧЕ шага сэмплирования (SAMPLE_SECONDS), иначе часть
# сэмплов запишет в БД одно и то же старое значение VRAM: график превращается в
# лестницу, а разница «до/после прогона» — в ноль или в мусор. 30 с при шаге 10 с
# давали два несвежих сэмпла из трёх.
GPU_CACHE_TTL = 5.0
NVIDIA_TIMEOUT = 5.0


def gpu_info() -> list[dict] | None:
    """GPU best effort: nvidia-smi, иначе amdgpu через sysfs, иначе None."""
    global _gpu_cache
    now = time.monotonic()
    if now - _gpu_cache[0] < GPU_CACHE_TTL:
        return _gpu_cache[1]
    info = _nvidia() or _sysfs_drm()
    _gpu_cache = (now, info)
    return info


def _smi(exe: str, query: str, kind: str) -> list[list[str]] | None:
    try:
        out = subprocess.run([exe, f"--query-{kind}={query}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=NVIDIA_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [[p.strip() for p in line.split(",")]
            for line in out.stdout.strip().splitlines() if line.strip()]


def _nvidia_procs(exe: str) -> dict[str, list[dict]]:
    """VRAM по процессам, сгруппированная по UUID карты.

    Без этого «использовано VRAM» — это вся карта разом: браузер, игра и наша
    модель в одной цифре. Списать её на модель нельзя, а именно так её и читали.
    """
    rows = _smi(exe, "gpu_uuid,pid,process_name,used_gpu_memory", "compute-apps")
    procs: dict[str, list[dict]] = {}
    for parts in rows or []:
        if len(parts) < 4:
            continue
        used = _num(parts[3])
        if used is None:
            continue          # «[N/A]» бывает в WSL и в контейнере без --gpus
        procs.setdefault(parts[0], []).append(
            {"pid": _num(parts[1]), "name": parts[2], "vram_used_mb": used})
    return procs


def _nvidia() -> list[dict] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    rows = _smi(exe, "uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu", "gpu")
    if rows is None:
        return None
    procs = _nvidia_procs(exe)
    gpus = []
    for parts in rows:
        if len(parts) < 6:
            continue
        uuid, mine = parts[0], procs.get(parts[0], [])
        used, total = _num(parts[3]), _num(parts[4])
        gpus.append({
            "name": parts[1],
            "util_pct": _num(parts[2]),
            # vram_used_mb — вся карта; vram_procs_mb — сумма по вычислительным
            # процессам. Именно вторая цифра отвечает на вопрос «сколько жрёт
            # модель», и путать их нельзя.
            "vram_used_mb": used,
            "vram_total_mb": total,
            "vram_free_mb": round(total - used, 1) if (used is not None and total is not None) else None,
            "vram_procs_mb": round(sum(p["vram_used_mb"] for p in mine), 1) if mine else 0.0,
            "procs": sorted(mine, key=lambda p: -p["vram_used_mb"])[:8],
            "uuid": uuid,
            "temp_c": _num(parts[5]),
        })
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
        # sysfs отдаёт байты; делим на 1024**2 — получаются МиБ, как у nvidia-smi
        used = _read(device / "mem_info_vram_used")
        total = _read(device / "mem_info_vram_total")
        used_mb = round(int(used) / 1024 ** 2, 1) if (used or "").isdigit() else None
        total_mb = round(int(total) / 1024 ** 2, 1) if (total or "").isdigit() else None
        gpus.append({
            "name": name,
            "util_pct": _num(busy),
            "vram_used_mb": used_mb,
            "vram_total_mb": total_mb,
            "vram_free_mb": round(total_mb - used_mb, 1)
            if (used_mb is not None and total_mb is not None) else None,
            # sysfs не разбивает VRAM по процессам — честнее отдать None, чем 0
            "vram_procs_mb": None,
            "procs": [],
            "temp_c": None,
        })
    return gpus or None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None


def _num(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
