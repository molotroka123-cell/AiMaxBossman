"""Compute mode and resource reporting.

The rule is honesty: the app states whether it is running on CPU or GPU based
on what it can actually detect, and says how it decided.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Accelerator:
    mode: str            # "cpu" | "gpu"
    detected: list[str]
    reason: str
    used_by_pipeline: str

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "detected": list(self.detected),
            "reason": self.reason,
            "used_by_pipeline": self.used_by_pipeline,
        }


def detect_accelerator(*, probe_nvidia: bool = True) -> Accelerator:
    detected: list[str] = []
    reason_parts: list[str] = []

    if os.path.exists("/proc/driver/nvidia/version"):
        detected.append("nvidia-driver")
    smi = shutil.which("nvidia-smi")
    if smi and probe_nvidia:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=8, check=False,
            )
            if out.returncode == 0:
                names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
                detected.extend(names)
                reason_parts.append(f"nvidia-smi listed {len(names)} device(s)")
            else:
                reason_parts.append("nvidia-smi present but returned an error")
        except Exception:  # pragma: no cover - defensive
            reason_parts.append("nvidia-smi present but could not be queried")
    elif not smi:
        reason_parts.append("nvidia-smi not found")

    if not detected:
        reason_parts.append("no GPU devices detected")

    # The analysis path is integer numpy arithmetic; it does not use a GPU even
    # when one exists. Saying otherwise would be a lie.
    return Accelerator(
        mode="gpu" if detected and any(d != "nvidia-driver" for d in detected) else "cpu",
        detected=detected,
        reason="; ".join(reason_parts) or "cpu only",
        used_by_pipeline="cpu",
    )


def resource_snapshot() -> dict:
    data: dict = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "load_average": None,
        "process": {},
    }
    try:
        data["load_average"] = [round(v, 2) for v in os.getloadavg()]
    except OSError:  # pragma: no cover - platform without loadavg
        pass
    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        with proc.oneshot():
            data["process"] = {
                "rss_bytes": proc.memory_info().rss,
                "num_threads": proc.num_threads(),
                "num_fds": getattr(proc, "num_fds", lambda: None)(),
                "children": len(proc.children()),
            }
        data["memory"] = {
            "total_bytes": psutil.virtual_memory().total,
            "available_bytes": psutil.virtual_memory().available,
        }
        data["process_source"] = "psutil"
    except Exception:
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as handle:
                fields = handle.read().split()
            data["process"] = {"rss_bytes": int(fields[1]) * os.sysconf("SC_PAGE_SIZE")}
            data["process_source"] = "/proc/self/statm"
        except Exception:  # pragma: no cover - non-linux
            data["process_source"] = "unavailable"
    return data
