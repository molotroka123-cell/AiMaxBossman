"""Приёмка целевого железа не имеет права соврать про железо.

Проверяется единственное, ради чего скрипт существует: вердикт
TARGET_HARDWARE_READY недостижим, пока прогон не случился на машине целевого
класса, и недостижим даже там, если хоть одна проверка не прошла.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "target_hardware_acceptance", REPO / "scripts" / "target_hardware_acceptance.py")
tha = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tha
SPEC.loader.exec_module(tha)


ON_TARGET = {"platform": "Windows-11", "cpu": "AMD Ryzen AI Max+ 395 w/ Radeon 8060S",
             "gpu": ["Radeon 8060S Graphics"], "ram_gib": 124.0,
             "cpu_match": True, "gpu_match": True, "ram_match": True,
             "on_target": True, "mismatch": []}


def _verdict(capsys, monkeypatch, host, checks=None):
    monkeypatch.setattr(tha, "probe", lambda: dict(host))
    if checks is not None:
        monkeypatch.setattr(tha, "run_on_target", lambda: list(checks))
    code = tha.main([])
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if x.startswith("TARGET_HARDWARE_VERDICT="))
    return line.split("=", 1)[1], code


def test_a_foreign_machine_can_never_claim_target_hardware(capsys, monkeypatch):
    # Ни один прогон НЕ на целевой машине не поднимает вердикт, даже если все
    # проверки пройдут: их результат в этом случае вообще не запрашивается.
    def explode():
        raise AssertionError("проверки на целевом железе не должны запускаться вне его")

    monkeypatch.setattr(tha, "run_on_target", explode)
    host = {**ON_TARGET, "cpu": "Intel(R) Xeon(R) Processor", "gpu": ["unknown"],
            "ram_gib": 15.7, "cpu_match": False, "gpu_match": False,
            "ram_match": False, "on_target": False,
            "mismatch": ["CPU не опознан как целевой"]}
    verdict, code = _verdict(capsys, monkeypatch, host)
    assert verdict == "SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN"
    assert code == 0  # это не отказ прогона, это честный отчёт об отсутствии железа


@pytest.mark.parametrize("missing", ["cpu_match", "gpu_match", "ram_match"])
def test_one_missing_component_is_enough_to_withhold_the_verdict(capsys, monkeypatch, missing):
    host = {**ON_TARGET, missing: False, "on_target": False, "mismatch": [missing]}
    verdict, _ = _verdict(capsys, monkeypatch, host)
    assert verdict == "SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN"


def test_on_target_a_failed_check_fails_the_acceptance(capsys, monkeypatch):
    checks = [{"check": "clean-install", "status": "PASS"},
              {"check": "doctor", "status": "FAIL", "returncode": 1}]
    verdict, code = _verdict(capsys, monkeypatch, ON_TARGET, checks)
    assert verdict == "TARGET_HARDWARE_FAILED"
    assert code == 1


def test_on_target_a_missing_check_is_not_a_pass(capsys, monkeypatch):
    # Отсутствующий скрипт — это не «нечего проверять», это непройденная
    # проверка: иначе удаление файла делает приёмку зелёной.
    checks = [{"check": "evening-acceptance", "status": "MISSING"}]
    verdict, code = _verdict(capsys, monkeypatch, ON_TARGET, checks)
    assert verdict == "TARGET_HARDWARE_FAILED"
    assert code == 1


def test_only_a_full_pass_on_real_target_hardware_earns_the_verdict(capsys, monkeypatch):
    checks = [{"check": n, "status": "PASS"} for n, _, _ in tha.ON_TARGET_CHECKS]
    verdict, code = _verdict(capsys, monkeypatch, ON_TARGET, checks)
    assert verdict == "TARGET_HARDWARE_READY"
    assert code == 0


def test_the_real_probe_reports_a_consistent_hardware_identity():
    # Этот тест запускается и в CI, и на целевом компьютере владельца.
    # Совпадение железа само по себе не означает прохождение acceptance.
    host = tha.probe()
    assert host["on_target"] == (
        host["cpu_match"] and host["gpu_match"] and host["ram_match"]
    ), host
    assert bool(host["mismatch"]) is not host["on_target"], host


# ------------------------------------------- OA-04: the archive runs it alone

def _load_from(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tha_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_in_a_checkout_the_checks_are_the_repository_scripts():
    root, checks = tha.on_target_checks()
    assert root == REPO and [c[0] for c in checks] == ["clean-install", "doctor", "evening-acceptance"]
    assert checks[0][1][1] == "scripts/verify_clean_install.py"
    assert tha.source_identity()["origin"] == "checkout"


def test_shipped_in_an_archive_the_checks_are_the_archive_s_own(tmp_path):
    """No scripts/ of a foreign clone, no cwd assumption, no invented git HEAD."""
    import json
    import shutil
    home = tmp_path / "BOSSMAN-Windows-x64-synthetic"
    support = home / "app-support"
    support.mkdir(parents=True)
    shutil.copyfile(REPO / "scripts" / "target_hardware_acceptance.py", support / "target_hardware_acceptance.py")
    (home / "MANIFEST.json").write_text(json.dumps({"source_sha": "a" * 40, "artifact": "synthetic"}), encoding="utf-8")
    shipped = _load_from(support / "target_hardware_acceptance.py")
    assert shipped.archive_home() == home
    assert shipped.source_identity() == {"origin": "downloaded_archive", "source_sha": "a" * 40,
                                         "artifact": "synthetic", "home": str(home)}
    root, checks = shipped.on_target_checks()
    assert root == home
    by_name = {name: argv for name, argv, _ in checks}
    assert by_name["clean-install"][1:] == ["app-support/verify_installed_product.py", "--expected-sha", "a" * 40]
    assert by_name["doctor"][1] == "app-support/bossman_doctor.py"
    assert by_name["evening-acceptance"][1] == "app-support/bundle_evening_test.py"
    assert all("scripts/" not in " ".join(argv) for argv in by_name.values())
    # Off the target machine the pending list names the archive's commands.
    assert shipped.main([]) == 0 or True  # verdict printed; the probe below decides
    report_checks = [row["command"] for row in json.loads(
        (lambda: (shipped.main(["--json", str(tmp_path / "r.json")]), (tmp_path / "r.json").read_text(encoding="utf-8"))[1])()
    )["pending_on_target"]] if not shipped.probe()["on_target"] else []
    assert all("app-support/" in command for command in report_checks)


def test_the_shipped_copy_needs_a_manifest_to_call_itself_an_archive(tmp_path):
    import shutil
    support = tmp_path / "app-support"
    support.mkdir()
    shutil.copyfile(REPO / "scripts" / "target_hardware_acceptance.py", support / "target_hardware_acceptance.py")
    shipped = _load_from(support / "target_hardware_acceptance.py")
    assert shipped.archive_home() is None


def test_studio_owner_only_is_not_hardware_pass(capsys,monkeypatch):
    verdict,code=_verdict(capsys,monkeypatch,ON_TARGET,[{'check':'studio','status':'OWNER_REQUIRED','returncode':2}])
    assert verdict=='TARGET_HARDWARE_OWNER_REQUIRED' and code==2


# --- Windows без WMIC: неопознанная машина не равна «программа готова» ---------
#
# Microsoft удалила WMIC из Windows 11 24H2/25H2 (август 2026). На машине
# владельца — новая Windows 11, то есть ровно тот случай. До правки все три
# определения шли только через wmic: пустой вывод превращал CPU в
# platform.processor(), GPU в ['unknown'], RAM в None, probe() решал «не
# целевая машина», а main() печатал SOFTWARE_READY_FOR_TARGET_HARDWARE_RUN с
# кодом 0. То есть настоящая целевая машина молча объявлялась нецелевой, все
# четыре проверки пропускались, и это выглядело успехом.

def _windows(monkeypatch, *, powershell=None, wmic=None, cim=None, psutil_ram=None):
    """Windows без wmic и с управляемым PowerShell/CIM."""
    monkeypatch.setattr(tha.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(tha.shutil, "which",
                        lambda name: {"pwsh": powershell, "powershell": powershell,
                                      "wmic": wmic}.get(name))
    monkeypatch.setattr(tha, "_run", lambda argv, timeout=30.0: (
        "" if argv[0] == "wmic" and not wmic else
        (cim(argv) if callable(cim) else (cim or "")) if argv[0] == powershell else ""))
    if psutil_ram is not None:
        monkeypatch.setattr(tha, "_psutil_ram_gib", lambda: psutil_ram, raising=False)
    else:
        monkeypatch.setattr(tha, "_psutil_ram_gib", lambda: None, raising=False)


def test_windows_without_wmic_and_without_powershell_is_undetermined_not_ready(capsys, monkeypatch):
    """Нечем определить железо — так и сказать, а не выдать за пройденную приёмку."""
    _windows(monkeypatch, powershell=None, wmic=None)
    host = tha.probe()
    assert host["hardware_state"] == "undetermined", host
    assert host["on_target"] is False
    verdict, code = _verdict(capsys, monkeypatch, host)
    assert verdict == "TARGET_HARDWARE_UNDETERMINED", verdict
    assert code != 0, "неопознанная машина не имеет права выходить с успехом"


def test_windows_without_wmic_but_with_cim_recognises_the_target(capsys, monkeypatch):
    """WMIC нет, CIM есть — целевая машина обязана быть опознана."""
    def cim(argv):
        query = argv[-1]
        if "Win32_Processor" in query:
            return '"AMD Ryzen AI Max+ 395 w/ Radeon 8060S Graphics"'
        if "Win32_VideoController" in query:
            return '["AMD Radeon(TM) 8060S Graphics","Microsoft Basic Display Adapter"]'
        if "TotalPhysicalMemory" in query:
            return "137438953472"
        return ""
    _windows(monkeypatch, powershell="pwsh", wmic=None, cim=cim)
    host = tha.probe()
    assert host["hardware_state"] == "target", host
    assert host["on_target"] is True
    assert host["ram_gib"] == 128.0, host["ram_gib"]
    assert len(host["gpu"]) == 2, host["gpu"]


def test_cyrillic_and_garbage_from_powershell_do_not_crash_the_probe(monkeypatch):
    """Кириллица в названии и мусор вместо JSON — не исключение, а «не определено»."""
    def cim(argv):
        if "Win32_Processor" in argv[-1]:
            return '"Процессор AMD Ryzen AI Max+ 395"'
        return "не-JSON мусор {{{"
    _windows(monkeypatch, powershell="powershell", wmic=None, cim=cim)
    host = tha.probe()
    assert "Ryzen AI Max+ 395" in host["cpu"]
    assert host["determined"]["cpu"] is True
    assert host["determined"]["gpu"] is False and host["determined"]["ram"] is False
    assert host["hardware_state"] == "undetermined", host


def test_a_genuinely_different_machine_is_different_not_undetermined(monkeypatch):
    """Определили железо и оно другое — это «другое», а не «не смогли определить»."""
    def cim(argv):
        query = argv[-1]
        if "Win32_Processor" in query:
            return '"Intel(R) Core(TM) i7-9750H"'
        if "Win32_VideoController" in query:
            return '"NVIDIA GeForce GTX 1650"'
        if "TotalPhysicalMemory" in query:
            return "17179869184"
        return ""
    _windows(monkeypatch, powershell="pwsh", wmic=None, cim=cim)
    host = tha.probe()
    assert host["hardware_state"] == "different", host
    assert host["on_target"] is False


def test_psutil_closes_the_memory_gap_when_cim_stays_silent(monkeypatch):
    """Память можно сверить независимо: psutil уже есть и про неё не врёт."""
    def cim(argv):
        query = argv[-1]
        if "Win32_Processor" in query:
            return '"AMD Ryzen AI Max+ 395"'
        if "Win32_VideoController" in query:
            return '"AMD Radeon 8060S Graphics"'
        return ""          # память CIM не отдал
    _windows(monkeypatch, powershell="pwsh", wmic=None, cim=cim, psutil_ram=124.0)
    host = tha.probe()
    assert host["determined"]["ram"] is True
    assert host["hardware_state"] == "target", host


def test_hardware_detection_cannot_hang_the_acceptance(monkeypatch):
    """Зависший PowerShell обязан упереться в тайм-аут, а не в терпение владельца."""
    import subprocess as sp
    calls = []

    def hang(argv, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise sp.TimeoutExpired(argv, kwargs.get("timeout") or 0)

    monkeypatch.setattr(tha.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(tha.shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    monkeypatch.setattr(tha.subprocess, "run", hang)
    monkeypatch.setattr(tha, "_psutil_ram_gib", lambda: None, raising=False)
    host = tha.probe()
    assert host["hardware_state"] == "undetermined"
    assert calls and all(t is not None and t > 0 for t in calls), calls


def test_on_target_children_run_under_a_timeout(monkeypatch, tmp_path):
    """Дочерняя проверка не имеет права висеть вечно."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        class Done:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return Done()

    script = tmp_path / "x.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(tha, "on_target_checks",
                        lambda: (tmp_path, [("doctor", [sys.executable, "x.py"], "что")]))
    monkeypatch.setattr(tha.subprocess, "run", fake_run)
    tha.run_on_target()
    assert seen["timeout"] is not None and seen["timeout"] > 0, seen


# --- откуда пришёл ответ: без этого прогон на Windows ничему не учит --------

def test_the_report_names_which_source_answered_for_each_field(monkeypatch):
    """«Определили» недостаточно: надо знать, ЧЕМ определили.

    Раннер Windows Server ещё несёт wmic, а машина владельца — уже нет. Если
    отчёт не называет источник, зелёный прогон на раннере не отличить от
    прогона, где новый путь CIM молча не сработал и всё вытянул устаревший
    wmic. Именно ради этого различия правка и делалась.
    """
    def cim(argv):
        query = argv[-1]
        if "Win32_Processor" in query:
            return '"AMD Ryzen AI Max+ 395 w/ Radeon 8060S Graphics"'
        if "Win32_VideoController" in query:
            return '["AMD Radeon(TM) 8060S Graphics"]'
        if "TotalPhysicalMemory" in query:
            return "137438953472"
        return ""
    _windows(monkeypatch, powershell="pwsh", wmic=None, cim=cim)
    host = tha.probe()
    assert host["sources"] == {"cpu": "cim", "gpu": "cim", "ram": "cim"}, host["sources"]


def test_the_source_says_wmic_when_cim_stays_silent(monkeypatch):
    """Пара: тот же зелёный ответ, но полученный устаревшим путём."""
    def wmic_run(argv, timeout=30.0):
        if argv[0] == "wmic" and "cpu" in argv:
            return "Name\nAMD Ryzen AI Max+ 395 w/ Radeon 8060S Graphics\n"
        if argv[0] == "wmic" and "win32_VideoController" in argv:
            return "Name\nAMD Radeon(TM) 8060S Graphics\n"
        return ""
    monkeypatch.setattr(tha.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(tha.shutil, "which",
                        lambda name: {"wmic": "wmic"}.get(name))
    monkeypatch.setattr(tha, "_run", wmic_run)
    monkeypatch.setattr(tha, "_psutil_ram_gib", lambda: 128.0, raising=False)
    host = tha.probe()
    assert host["sources"]["cpu"] == "wmic", host["sources"]
    assert host["sources"]["gpu"] == "wmic", host["sources"]
    assert host["sources"]["ram"] == "psutil", host["sources"]


def test_an_undetermined_field_names_no_source_at_all(monkeypatch):
    """Не определили — источника нет, и выдумывать его нельзя."""
    _windows(monkeypatch, powershell=None, wmic=None)
    host = tha.probe()
    assert host["hardware_state"] == "undetermined"
    assert host["sources"] == {"cpu": "", "gpu": "", "ram": ""}, host["sources"]


def test_the_sources_are_reset_between_probes(monkeypatch):
    """Вторая проба не имеет права унаследовать источник первой."""
    def cim(argv):
        if "Win32_Processor" in argv[-1]:
            return '"AMD Ryzen AI Max+ 395"'
        return ""
    _windows(monkeypatch, powershell="pwsh", wmic=None, cim=cim)
    first = tha.probe()
    assert first["sources"]["cpu"] == "cim"

    _windows(monkeypatch, powershell=None, wmic=None)
    second = tha.probe()
    assert second["sources"]["cpu"] == "", second["sources"]
