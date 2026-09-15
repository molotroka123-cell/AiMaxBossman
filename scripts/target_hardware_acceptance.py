#!/usr/bin/env python3
"""Приёмка на ЦЕЛЕВОМ железе — и честный отказ, когда железа нет.

Целевая машина владельца:

    CPU  AMD Ryzen AI Max+ 395 (Strix Halo)
    GPU  Radeon 8060S (iGPU, общая память)
    RAM  128 ГБ единой памяти

Скрипт отвечает на ОДИН вопрос: выполняются ли владельческие сценарии на
машине этого класса. Поэтому он сначала измеряет, ГДЕ он запущен, и только
потом решает, что вправе сказать:

* железо опознано как целевое и прогон прошёл   -> TARGET_HARDWARE_READY
* железо не целевое                             -> SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN
* железо целевое, но прогон не прошёл           -> TARGET_HARDWARE_FAILED

Второй вариант — не недоработка, а единственный допустимый ответ, когда
машины нет: раздел 29 задания прямо запрещает объявлять готовность целевого
железа по прогону, который на нём не происходил. Поэтому здесь нет ни флага
«считать железо целевым», ни переменной окружения, которая поднимает вердикт:
поднять его может только само железо.

    python scripts/target_hardware_acceptance.py
    python scripts/target_hardware_acceptance.py --json out.json

Коды выхода: 0 — вердикт получен (любой из трёх), 1 — прогон на целевом
железе провалился.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# Опознаётся КЛАСС машины, а не одна строка модели: у Strix Halo несколько
# торговых имён, и требовать точного совпадения значит проваливать приёмку на
# той самой машине, ради которой она написана.
TARGET_CPU_PATTERNS = (
    re.compile(r"ryzen\s*ai\s*max\+?\s*39[05]", re.I),
    re.compile(r"strix\s*halo", re.I),
)
TARGET_GPU_PATTERNS = (
    re.compile(r"radeon\s*80[0-9]0s", re.I),
    re.compile(r"gfx115[0-9]", re.I),   # RDNA 3.5 iGPU Strix Halo
)
# 128 ГБ единой памяти; берём с запасом вниз — часть памяти удерживает
# прошивка и она не видна ОС.
TARGET_MIN_RAM_GIB = 96

# Что обязано быть проверено ИМЕННО на целевой машине и не может быть
# проверено здесь. Список — не пожелание, а вход для прогона на железе.
ON_TARGET_CHECKS = [
    ("clean-install", [sys.executable, "scripts/verify_clean_install.py"],
     "чистая установка и владельческие сценарии по HTTP"),
    ("doctor", [sys.executable, "scripts/bossman_doctor.py"],
     "готовность подсистем на реальном железе"),
    ("evening-acceptance", [sys.executable, "scripts/evening_acceptance.py"],
     "вечерний владельческий прогон"),
]


def _run(argv: list[str], timeout: float = 30.0) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "") + (out.stderr or "")


def cpu_model() -> str:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if sys.platform == "win32":
        text = _run(["wmic", "cpu", "get", "name"])
        lines = [x.strip() for x in text.splitlines() if x.strip() and "Name" not in x]
        if lines:
            return lines[0]
    if sys.platform == "darwin":
        text = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if text:
            return text
    return platform.processor() or platform.machine() or "unknown"


def gpu_models() -> list[str]:
    found: list[str] = []
    if shutil.which("rocminfo"):
        for line in _run(["rocminfo"]).splitlines():
            m = re.search(r"(gfx\d+)", line)
            if m and m.group(1) not in found:
                found.append(m.group(1))
    if sys.platform.startswith("linux") and shutil.which("lspci"):
        for line in _run(["lspci"]).splitlines():
            if re.search(r"\bvga\b|\b3d controller\b|\bdisplay controller\b", line, re.I):
                found.append(line.split(":", 2)[-1].strip())
    if sys.platform == "win32":
        text = _run(["wmic", "path", "win32_VideoController", "get", "name"])
        found += [x.strip() for x in text.splitlines() if x.strip() and "Name" not in x]
    return found or ["unknown"]


def total_ram_gib() -> float | None:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024 ** 2), 1)
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "win32":
        text = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
        digits = [x.strip() for x in text.splitlines() if x.strip().isdigit()]
        if digits:
            return round(int(digits[0]) / (1024 ** 3), 1)
    if sys.platform == "darwin":
        text = _run(["sysctl", "-n", "hw.memsize"]).strip()
        if text.isdigit():
            return round(int(text) / (1024 ** 3), 1)
    return None


def probe() -> dict[str, Any]:
    cpu, gpus, ram = cpu_model(), gpu_models(), total_ram_gib()
    cpu_ok = any(p.search(cpu) for p in TARGET_CPU_PATTERNS)
    gpu_ok = any(p.search(g) for p in TARGET_GPU_PATTERNS for g in gpus)
    ram_ok = ram is not None and ram >= TARGET_MIN_RAM_GIB
    mismatch = []
    if not cpu_ok:
        mismatch.append(f"CPU не опознан как целевой: {cpu!r}")
    if not gpu_ok:
        mismatch.append(f"GPU не опознан как целевой: {gpus!r}")
    if not ram_ok:
        mismatch.append(f"памяти {ram} ГиБ, требуется не меньше {TARGET_MIN_RAM_GIB} ГиБ")
    return {"platform": platform.platform(), "cpu": cpu, "gpu": gpus,
            "ram_gib": ram, "cpu_match": cpu_ok, "gpu_match": gpu_ok,
            "ram_match": ram_ok, "on_target": cpu_ok and gpu_ok and ram_ok,
            "mismatch": mismatch}


def run_on_target() -> list[dict[str, Any]]:
    results = []
    for name, argv, what in ON_TARGET_CHECKS:
        script = REPO / argv[1]
        if not script.is_file():
            results.append({"check": name, "status": "MISSING", "what": what,
                            "detail": f"нет файла {argv[1]}"})
            continue
        proc = subprocess.run([argv[0], str(script), *argv[2:]], cwd=REPO,
                              capture_output=True, text=True, check=False)
        results.append({"check": name, "what": what,
                        "status": "PASS" if proc.returncode == 0 else "FAIL",
                        "returncode": proc.returncode,
                        "tail": (proc.stdout or proc.stderr or "")[-2000:]})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, help="куда положить отчёт")
    args = parser.parse_args(argv)

    host = probe()
    report: dict[str, Any] = {"type": "bossman.target_hardware_acceptance",
                              "schema": 1, "host": host,
                              "target": {"cpu": "AMD Ryzen AI Max+ 395",
                                         "gpu": "Radeon 8060S",
                                         "ram_gib": 128,
                                         "min_ram_gib": TARGET_MIN_RAM_GIB}}
    if not host["on_target"]:
        report["verdict"] = "SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN"
        report["pending_on_target"] = [{"check": n, "command": " ".join(c), "what": w}
                                       for n, c, w in ON_TARGET_CHECKS]
        report["why"] = ("прогон выполнен НЕ на целевой машине; объявлять готовность "
                         "целевого железа по такому прогону запрещено")
        exit_code = 0
    else:
        report["checks"] = run_on_target()
        failed = [c for c in report["checks"] if c["status"] != "PASS"]
        report["verdict"] = "TARGET_HARDWARE_FAILED" if failed else "TARGET_HARDWARE_READY"
        exit_code = 1 if failed else 0

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nTARGET_HARDWARE_VERDICT={report['verdict']}")
    for line in host["mismatch"]:
        print(f"  не совпало: {line}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
