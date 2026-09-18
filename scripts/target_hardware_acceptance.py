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


def archive_home() -> Path | None:
    """The downloaded archive this file ships in, or None in a checkout.

    Shipped as ``app-support/target_hardware_acceptance.py`` beside the
    archive's MANIFEST.json (OA-04). There the three on-target checks are the
    archive's own: the installed-product verifier bound to the manifest SHA,
    the shipped doctor and the shipped evening acceptance. No ``scripts/`` of
    a foreign clone, no cwd assumption, no invented git HEAD.
    """
    home = Path(__file__).resolve().parent.parent
    if Path(__file__).resolve().parent.name == "app-support" and (home / "MANIFEST.json").is_file():
        return home
    return None


def source_identity() -> dict[str, Any]:
    """What this run is about: the manifest's SHA in an archive, the checkout otherwise."""
    home = archive_home()
    if home is not None:
        manifest = json.loads((home / "MANIFEST.json").read_text(encoding="utf-8"))
        return {"origin": "downloaded_archive", "source_sha": manifest.get("source_sha"),
                "artifact": manifest.get("artifact"), "home": str(home)}
    return {"origin": "checkout", "repo": str(REPO)}

# Опознаётся КЛАСС машины, а не одна строка модели: у Strix Halo несколько
# торговых имён, и требовать точного совпадения значит проваливать приёмку на
# той самой машине, ради которой она написана.
TARGET_CPU_PATTERNS = (
    re.compile(r"ryzen\s*ai\s*max\+?\s*39[05]", re.I),
    re.compile(r"strix\s*halo", re.I),
)
TARGET_GPU_PATTERNS = (
    # Между брендом и номером Windows вставляет знак торговой марки: CIM и
    # WMIC отдают «AMD Radeon(TM) 8060S Graphics», а не «Radeon 8060S».
    # Прежний шаблон требовал только пробелы и на настоящей машине владельца
    # целевую видеокарту не опознал бы даже с живым WMIC — тот же молчаливый
    # пропуск проверок, что и при отсутствии WMIC.
    re.compile(r"radeon\b[^0-9]{0,12}80[0-9]0s", re.I),
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


# --- Определение железа на Windows -------------------------------------------
#
# WMIC удалён из Windows 11 24H2/25H2 (Microsoft, август 2026), а у владельца
# как раз новая Windows 11. Пока всё определение шло через wmic, его отсутствие
# превращало целевую машину в «нецелевую»: CPU падал в platform.processor(),
# GPU в ['unknown'], RAM в None, и приёмка молча пропускала все проверки с
# кодом 0. Поэтому порядок такой: CIM через PowerShell (поддерживаемый путь и
# машинно-читаемый JSON) → legacy wmic там, где он ещё есть → psutil как
# независимая сверка памяти. И отдельно различаются «железо другое» и «железо
# не определено»: это разные ответы, и второй не имеет права выглядеть успехом.

CIM_TIMEOUT = 20.0          # определение железа не ждёт владельца дольше
CHILD_TIMEOUT = 1800.0      # дочерняя проверка на целевой машине


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _cim(query: str) -> Any | None:
    """Ответ CIM как разобранный JSON или None, если доверять нечему."""
    shell = _powershell()
    if not shell:
        return None
    text = _run([shell, "-NoProfile", "-NonInteractive", "-Command", query],
                timeout=CIM_TIMEOUT).strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None          # мусор вместо JSON — это «не определено», не падение


def _psutil_ram_gib() -> float | None:
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001 — psutil необязателен в архиве
        return None
    try:
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:  # noqa: BLE001
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def cpu_model() -> str:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if sys.platform == "win32":
        named = _as_list(_cim("Get-CimInstance Win32_Processor | "
                              "Select-Object -First 1 -ExpandProperty Name | ConvertTo-Json -Compress"))
        if named:
            return named[0]
        if shutil.which("wmic"):
            text = _run(["wmic", "cpu", "get", "name"])
            lines = [x.strip() for x in text.splitlines() if x.strip() and "Name" not in x]
            if lines:
                return lines[0]
        return ""            # не определили — и не притворяемся, что определили
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
        named = _as_list(_cim("Get-CimInstance Win32_VideoController | "
                              "Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"))
        if named:
            found += named
        elif shutil.which("wmic"):
            text = _run(["wmic", "path", "win32_VideoController", "get", "name"])
            found += [x.strip() for x in text.splitlines() if x.strip() and "Name" not in x]
    return found


def total_ram_gib() -> float | None:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024 ** 2), 1)
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "win32":
        total = _cim("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory | ConvertTo-Json -Compress")
        if isinstance(total, (int, float)) and total > 0:
            return round(float(total) / (1024 ** 3), 1)
        if isinstance(total, str) and total.strip().isdigit():
            return round(int(total.strip()) / (1024 ** 3), 1)
        if shutil.which("wmic"):
            text = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
            digits = [x.strip() for x in text.splitlines() if x.strip().isdigit()]
            if digits:
                return round(int(digits[0]) / (1024 ** 3), 1)
        return _psutil_ram_gib()   # независимая сверка, а не догадка
    if sys.platform == "darwin":
        text = _run(["sysctl", "-n", "hw.memsize"]).strip()
        if text.isdigit():
            return round(int(text) / (1024 ** 3), 1)
    return None


def probe() -> dict[str, Any]:
    """Три различимых ответа: целевое, достоверно другое, не определено.

    Раньше ответов было два, и «не смогли определить» сливалось с «машина
    другая». На Windows без WMIC это означало, что целевая машина владельца
    молча объявлялась нецелевой, все проверки пропускались, а код выхода был
    нулевым. Неопознанная машина больше не выглядит успехом.
    """
    cpu, gpus, ram = cpu_model(), gpu_models(), total_ram_gib()
    determined = {"cpu": bool(cpu.strip()), "gpu": bool(gpus), "ram": ram is not None}
    cpu_ok = determined["cpu"] and any(p.search(cpu) for p in TARGET_CPU_PATTERNS)
    gpu_ok = determined["gpu"] and any(p.search(g) for p in TARGET_GPU_PATTERNS for g in gpus)
    ram_ok = determined["ram"] and ram >= TARGET_MIN_RAM_GIB
    mismatch = []
    if determined["cpu"] and not cpu_ok:
        mismatch.append(f"CPU не опознан как целевой: {cpu!r}")
    if determined["gpu"] and not gpu_ok:
        mismatch.append(f"GPU не опознан как целевой: {gpus!r}")
    if determined["ram"] and not ram_ok:
        mismatch.append(f"памяти {ram} ГиБ, требуется не меньше {TARGET_MIN_RAM_GIB} ГиБ")
    unknown = [name for name, ok in determined.items() if not ok]
    on_target = cpu_ok and gpu_ok and ram_ok
    if on_target:
        state = "target"
    elif mismatch:
        state = "different"      # определили и оно другое — это ответ, а не пробел
    else:
        state = "undetermined"   # определить не удалось: сказать прямо
    return {"platform": platform.platform(), "cpu": cpu, "gpu": gpus or ["unknown"],
            "ram_gib": ram, "cpu_match": cpu_ok, "gpu_match": gpu_ok,
            "ram_match": ram_ok, "on_target": on_target,
            "determined": determined, "undetermined": unknown,
            "hardware_state": state, "mismatch": mismatch}


def on_target_checks() -> tuple[Path, list[tuple[str, list[str], str]]]:
    """Where the checks live and what they are, for a checkout or an archive."""
    home = archive_home()
    if home is None:
        return REPO, list(ON_TARGET_CHECKS)
    manifest = json.loads((home / "MANIFEST.json").read_text(encoding="utf-8"))
    support = "app-support"
    return home, [
        ("studio", [sys.executable, f"{support}/studio_live_owner.py", "--expected-sha", str(manifest.get("source_sha")), "--provider", "comfyui", "--execute", "--output", str(home / "studio-hardware.json")],
         "локальная Studio на ComfyUI; без настроенного checkpoint OWNER_REQUIRED"),
        ("clean-install", [sys.executable, f"{support}/verify_installed_product.py",
                           "--expected-sha", str(manifest.get("source_sha"))],
         "установленный продукт этого архива и владельческие сценарии по HTTP"),
        ("doctor", [sys.executable, f"{support}/bossman_doctor.py"],
         "готовность подсистем на реальном железе"),
        ("evening-acceptance", [sys.executable, f"{support}/bundle_evening_test.py"],
         "вечерняя приёмка владельца для этого архива"),
    ]


def run_on_target() -> list[dict[str, Any]]:
    results = []
    root, checks = on_target_checks()
    for name, argv, what in checks:
        script = root / argv[1]
        if not script.is_file():
            results.append({"check": name, "status": "MISSING", "what": what,
                            "detail": f"нет файла {argv[1]}"})
            continue
        try:
            proc = subprocess.run([argv[0], str(script), *argv[2:]], cwd=root,
                                  capture_output=True, text=True, check=False,
                                  timeout=CHILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Зависшая проверка — это отказ с названной причиной, а не вечное
            # ожидание владельца у экрана.
            results.append({"check": name, "what": what, "status": "FAIL",
                            "returncode": None,
                            "tail": f"проверка не уложилась в {CHILD_TIMEOUT:.0f} с"})
            continue
        results.append({"check": name, "what": what,
                        "status": "PASS" if proc.returncode == 0 else "OWNER_REQUIRED" if name == "studio" and proc.returncode == 2 else "FAIL",
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
                              "schema": 1, "host": host, "source": source_identity(),
                              "target": {"cpu": "AMD Ryzen AI Max+ 395",
                                         "gpu": "Radeon 8060S",
                                         "ram_gib": 128,
                                         "min_ram_gib": TARGET_MIN_RAM_GIB}}
    state = host.get("hardware_state") or ("target" if host.get("on_target") else "different")
    if state == "undetermined":
        # Нечем определить железо — это отдельный ответ. Выдать его за
        # пройденную программную приёмку значит соврать владельцу ровно там,
        # где он больше всего полагается на честность.
        report["verdict"] = "TARGET_HARDWARE_UNDETERMINED"
        report["undetermined"] = host.get("undetermined", [])
        report["why"] = ("железо не определено: " + ", ".join(host.get("undetermined", []) or ["-"])
                         + ". На Windows 11 24H2/25H2 WMIC удалён; нужен доступный "
                           "PowerShell для CIM. Проверки целевого железа НЕ запускались")
        exit_code = 3
    elif state != "target":
        report["verdict"] = "SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN"
        report["pending_on_target"] = [{"check": n, "command": " ".join(c), "what": w}
                                       for n, c, w in on_target_checks()[1]]
        report["why"] = ("прогон выполнен НЕ на целевой машине; объявлять готовность "
                         "целевого железа по такому прогону запрещено")
        exit_code = 0
    else:
        report["checks"] = run_on_target()
        failed = [c for c in report["checks"] if c["status"] not in ("PASS", "OWNER_REQUIRED")]
        pending = any(c["status"] == "OWNER_REQUIRED" for c in report["checks"])
        report["verdict"] = "TARGET_HARDWARE_FAILED" if failed else "TARGET_HARDWARE_OWNER_REQUIRED" if pending else "TARGET_HARDWARE_READY"
        exit_code = 1 if failed else 2 if pending else 0

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
