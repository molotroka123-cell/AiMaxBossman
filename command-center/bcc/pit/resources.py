"""Local capacity guard: Bossman 1.6 owns the machine first.

The participant runtime prefers local models, but the same machine also runs
Bossman 1.6 workloads (BossNet/Game/Studio training and acceptance). A Jeff
turn must never contend with them: before a local route is offered this guard
measures free capacity and, when the owner-configured headroom is not
available, demotes local endpoints for that turn so the router falls back to a
runtime-confirmed FREE cloud model. Nothing is ever unloaded, killed or
throttled: the guard only decides what Jeff is allowed to use right now.

Two real telemetry sources, in priority order:

* NVIDIA hosts: a bounded nvidia-smi subprocess measures dedicated VRAM.
* AMD APU hosts such as the owner's Ryzen AI Max+ 395 (Strix Halo): there is
  no discrete VRAM to query, so the guard uses Windows' own memory counters
  (GlobalMemoryStatusEx) over the unified memory pool local models actually
  allocate from. No NVIDIA VRAM semantics are invented; if the counters
  cannot be read, capacity stays unknown and the safe demoted fallback is
  kept.
"""
from __future__ import annotations

import asyncio
import os
import sys
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
# A local model on unified memory competes with the OS and 1.6 workloads for
# the whole pool, so the no-owner-override headroom is larger than the
# dedicated-VRAM default.
_UNIFIED_MIN_FREE_MB_DEFAULT = 8000


def _env_min_free_mb() -> int | None:
    """Owner override in MiB, or None when the env var is unset/invalid."""
    raw = os.environ.get("BOSSMAN_PIT_VRAM_FREE_MIN_MB", "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _vram_free_mb_env() -> int:
    value = _env_min_free_mb()
    return _MIN_FREE_MB_DEFAULT if value is None else value


def _measure_local_capacity() -> tuple[int | None, str]:
    """(free MiB, probe kind); (None, "nvidia-smi") when nothing can measure."""
    nvidia_mb = _read_free_vram_mb()
    if nvidia_mb is not None:
        return nvidia_mb, "nvidia-smi"
    unified_mb = _read_amd_unified_free_mb()
    if unified_mb is not None:
        return unified_mb, "amd-unified"
    return None, "nvidia-smi"


def _has_amd_gpu() -> bool:
    """True when a display adapter reports an AMD/Radeon driver."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return False
    # Display adapters device class.
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not subkey.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        desc = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                except OSError:
                    continue
                lowered = desc.lower()
                if "amd" in lowered or "radeon" in lowered:
                    return True
    except OSError:
        return False
    return False


def _win_available_memory_mb() -> int | None:
    """Available physical memory in MiB from Windows, or None when unreadable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return int(stat.ullAvailPhys // (1024 * 1024))
    except Exception:  # noqa: BLE001 — telemetry must never crash the chat loop
        return None


def _read_amd_unified_free_mb() -> int | None:
    """Free unified memory (MiB) on AMD AI Max class hosts.

    The owner's Ryzen AI Max+ 395 / Radeon 8060S has no discrete VRAM: local
    models allocate from the same physical memory pool Windows reports. The
    probe is read-only, bounded and never unloads or kills another workload;
    foreground owner tasks keep priority because a loaded machine simply
    reports less available memory. When the GPU cannot be recognised or the
    OS counters are unreadable the answer is None (unknown capacity) so the
    caller keeps the safe demoted fallback.
    """
    if not _has_amd_gpu():
        return None
    return _win_available_memory_mb()


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
        env_min = _env_min_free_mb()
        if min_free_mb is None:
            self.min_free_mb = (
                env_min if env_min is not None else _MIN_FREE_MB_DEFAULT)
        else:
            self.min_free_mb = min_free_mb
        # Unified memory: honour an owner override or an explicit arg, otherwise
        # require more headroom than a dedicated-VRAM card because the model
        # shares the pool with the OS and the 1.6 workload.
        self._min_free_mb_unified = (
            env_min if env_min is not None
            else (min_free_mb if min_free_mb is not None
                  else _UNIFIED_MIN_FREE_MB_DEFAULT))
        self.ttl_seconds = ttl_seconds
        self._cached: bool | None = None
        self._checked_at: float = 0.0
        self.last_reason: str = ""

    async def local_allowed(self) -> bool:
        """True when local inference may take capacity this turn."""
        now = time.monotonic()
        if self._cached is not None and now - self._checked_at < self.ttl_seconds:
            return self._cached
        loop = asyncio.get_running_loop()
        try:
            free_mb, kind = await loop.run_in_executor(None, _measure_local_capacity)
        except Exception:  # noqa: BLE001 — a broken probe must not block chat
            free_mb, kind = None, "nvidia-smi"
        min_mb = (self._min_free_mb_unified if kind == "amd-unified"
                  else self.min_free_mb)
        label = "unified" if kind == "amd-unified" else "vram"
        if free_mb is None:
            self._cached, self._checked_at = False, now
            self.last_reason = "vram-unmeasured-1.6-priority"
            return False
        if free_mb >= min_mb:
            self._cached, self._checked_at = True, now
            self.last_reason = f"{label}-free-{free_mb}mb"
            return True
        self._cached, self._checked_at = False, now
        self.last_reason = (
            f"{label}-low-{free_mb}mb-min-{min_mb}mb-1.6-priority")
        return False

    def reset(self) -> None:
        """Force the next turn to re-measure (used by tests and doctor)."""
        self._cached = None
        self._checked_at = 0.0
