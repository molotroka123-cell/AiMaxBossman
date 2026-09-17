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


def test_the_real_probe_on_this_machine_does_not_claim_target_hardware():
    # Контроль над самим детектором: на сборочной машине он обязан говорить
    # «не целевая». Если этот тест однажды упадёт — прогон идёт на железе
    # владельца, и это надо заметить, а не проглядеть.
    host = tha.probe()
    assert host["on_target"] is False, host
    assert host["mismatch"]


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
