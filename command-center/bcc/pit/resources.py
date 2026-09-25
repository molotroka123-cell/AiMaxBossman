"""Local capacity guard: Bossman 1.6 owns the GPU first.

The participant runtime prefers local models, but the same machine also runs
Bossman 1.6 workloads (BossNet/Game/Studio training and acceptance). A Jeff
turn must never contend with them: before a local route is offered this guard
measures free VRAM and, when the owner-configured headroom is not available,
demotes local endpoints for that turn so the router falls back to a
runtime-confirmed FREE cloud model. Nothing is ever unloaded, killed or
throttled: the guard only decides what Jeff is allowed to use right now.

Measurement is a bounded subprocess (nvidia-smi). When capacity cannot be
measured, Jeff yields to a verified free cloud route so an unmeasured AMD GPU
cannot contend with the owner's 1.6 workload.
"""
from __future__ import annotations

import asyncio
import os
import time

_NVIDIA_SMI_CANDIDATES = (
    "nvidia-smi",
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
)
_QUERY = ("--query-gpu=memory.free,memory.total",
          "--format=csv,noheader,nounits")
_CACHE_SECONDS = 20.0
_MIN_FREE_MB_DEFAULT = 2000


def _vram_free_mb_env() -> int:
    raw = os.environ.get("BOSSMAN_PIT_VRAM_FREE_MIN_MB", "").strip()
    if not raw:
        return _MIN_FREE_MB_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _MIN_FREE_MB_DEFAULT
    return max(0, value)


def _read_free_vram_mb() -> int | None:
    """Sum of free VRAM across GPUs, in MiB; None when it cannot be measured."""
    import subprocess
    for candidate in _NVIDIA_SMI_CANDIDATES:
        try:
            proc = subprocess.run(
                [candidate, *_QUERY],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        total_free = 0
        seen_gpu = False
        for line in proc.stdout.splitlines():
            parts = [part.strip().lower().replace(",", "") for part in line.split(",")]
            if len(parts) < 2 or not parts[0].endswith("mib"):
                continue
            try:
                total_free += int(float(parts[0].removesuffix("mib")))
                seen_gpu = True
            except ValueError:
                continue
        if seen_gpu:
            return total_free
    return None


class LocalCapacityGuard:
    """Turn-scoped gate for local model routes, cached across rapid turns."""

    def __init__(self, min_free_mb: int | None = None, ttl_seconds: float = _CACHE_SECONDS):
        self.min_free_mb = _vram_free_mb_env() if min_free_mb is None else min_free_mb
        self.ttl_seconds = ttl_seconds
        self._checked_at: float = 0.0
        self._cached: bool | None = None
        self.last_reason: str = ""

    async def local_allowed(self) -> bool:
        """True when local inference may take VRAM this turn."""
        now = time.monotonic()
        if self._cached is not None and now - self._checked_at < self.ttl_seconds:
            return self._cached
        loop = asyncio.get_running_loop()
        try:
            free_mb = await loop.run_in_executor(None, _read_free_vram_mb)
        except Exception:  # noqa: BLE001 — a broken probe must not block chat
            free_mb = None
        if free_mb is None:
            self._cached, self._checked_at = False, now
            self.last_reason = "vram-unmeasured-1.6-priority"
            return False
        if free_mb >= self.min_free_mb:
            self._cached, self._checked_at = True, now
            self.last_reason = f"vram-free-{free_mb}mb"
            return True
        self._cached, self._checked_at = False, now
        self.last_reason = f"vram-low-{free_mb}mb-min-{self.min_free_mb}mb-1.6-priority"
        return False

    def reset(self) -> None:
        """Force the next turn to re-measure (used by tests and doctor)."""
        self._cached = None
        self._checked_at = 0.0
