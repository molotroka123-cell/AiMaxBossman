"""Инструменты вечерней приёмки обязаны быть исправны ДО вечера.

Доктор и харнесс — единственное, что владелец запустит первым. Если они врут
или падают, вечер потерян ещё до первого сценария.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Регистрация в sys.modules обязательна: без неё @dataclass внутри скрипта
    не находит свой модуль и падает на ровном месте."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_ACCEPTANCE_ROOT", str(tmp_path / "acceptance"))
    return _load("evening_acceptance")


# ------------------------------------------------------------------- доктор

def test_the_doctor_runs_and_never_crashes_itself():
    """Доктор — диагностический инструмент: он обязан ДОЛОЖИТЬ о проблеме,
    а не упасть с трассировкой вместо диагноза."""
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "bossman_doctor.py"), "--json"],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode in (0, 1), out.stderr
    report = json.loads(out.stdout)
    assert report["schema_version"] == 1
    assert report["checks"], "доктор без проверок бесполезен"
    assert all(c["status"] in ("PASS", "WARN", "BLOCKED") for c in report["checks"])


def test_the_doctor_separates_pass_warn_and_blocked():
    doctor = _load("bossman_doctor")
    assert (doctor.PASS, doctor.WARN, doctor.BLOCKED) == ("PASS", "WARN", "BLOCKED")
    results = doctor.run_checks(port=0)
    names = {r.name for r in results}
    # Каждая из этих проверок — причина реального сорванного вечера.
    for required in ("python", "ffmpeg", "state-dir", "browser", "model-endpoint",
                     "hardware", "port", "evidence-key", "journal-anchor", "telemetry"):
        assert required in names, f"доктор не проверяет {required}"
    for r in results:
        if r.status != doctor.PASS:
            assert r.remedy, f"{r.name}: сказано «плохо» без указания, что делать"


def test_a_blocked_check_makes_the_doctor_exit_nonzero(monkeypatch, tmp_path):
    """Выход 0 при BLOCKED означал бы, что стартовый скрипт запустит систему
    на машине, где она гарантированно сломается."""
    doctor = _load("bossman_doctor")
    monkeypatch.setattr(doctor, "CHECKS", [
        lambda: doctor.Check("synthetic", doctor.BLOCKED, "искусственный блокер", "почините")])
    assert doctor.main(["--json-out", str(tmp_path / "d.json")]) == 1
    report = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))
    # Не точное число: порт на этой машине может быть занят чужим сервером, и
    # тогда блокеров честно два. Утверждается наличие нашего, а не арифметика.
    assert report["blocked"] >= 1
    assert any(c["name"] == "synthetic" and c["status"] == doctor.BLOCKED
               for c in report["checks"])


def test_a_failing_check_does_not_take_the_doctor_down_with_it(monkeypatch):
    def boom() -> None:
        raise RuntimeError("проверка сломалась")
    doctor = _load("bossman_doctor")
    monkeypatch.setattr(doctor, "CHECKS", [boom])
    results = doctor.run_checks(port=0)
    assert any(r.status == doctor.BLOCKED and "проверка упала" in r.detail for r in results)


# ------------------------------------------------------------------ харнесс

def test_the_harness_and_the_document_cannot_drift(acceptance):
    """Документ и код — один корпус. Разошлись — это дефект, а не мелочь."""
    assert acceptance.cmd_verify(object()) == 0


def test_the_corpus_covers_every_required_owner_workflow(acceptance):
    idents = [s.ident for s in acceptance.CORPUS]
    assert idents == [f"T{i:02d}" for i in range(1, 13)]
    titles = " ".join(s.title.lower() for s in acceptance.CORPUS)
    for topic in ("чат", "веб", "компьютер", "код", "web studio", "video studio",
                  "файл", "восстановление", "эффект", "телеметрия", "параллельность",
                  "интерфейс"):
        assert topic in titles, f"в корпусе нет сценария про {topic}"


def test_scenarios_needing_absent_tools_are_marked_blocked_not_failed(acceptance):
    """Невозможность проверить — не провал продукта. Записывать её как FAIL так же
    нечестно, как записывать как PASS."""
    needs = {n for s in acceptance.CORPUS for n in s.needs}
    assert needs <= {"ffmpeg", "browser"}
    video = next(s for s in acceptance.CORPUS if s.ident == "T06")
    assert "ffmpeg" in video.needs


def test_an_incomplete_run_is_never_reported_as_pass(acceptance, tmp_path):
    state = {"results": {"T01": {"ident": "T01", "verdict": "PASS", "blocks_release": True}}}
    assert acceptance._gates(state)["acceptance_verdict"] == "INCOMPLETE"


def test_one_blocking_failure_fails_the_whole_acceptance(acceptance):
    results = {s.ident: {"ident": s.ident, "verdict": "PASS", "blocks_release": s.blocks_release}
               for s in acceptance.CORPUS}
    assert acceptance._gates({"results": results})["acceptance_verdict"] == "PASS"
    results["T09"]["verdict"] = "FAIL"
    gates = acceptance._gates({"results": results})
    assert gates["acceptance_verdict"] == "FAIL" and gates["blocking_failures"] == ["T09"]


def test_a_non_blocking_failure_does_not_fail_the_release(acceptance):
    results = {s.ident: {"ident": s.ident, "verdict": "PASS", "blocks_release": s.blocks_release}
               for s in acceptance.CORPUS}
    results["T11"]["verdict"] = "FAIL"          # параллельность не блокирует релиз
    assert acceptance._gates({"results": results})["acceptance_verdict"] == "PASS"


def test_claimed_tasks_absent_from_the_corpus_are_named(acceptance):
    """Слово владельца сверяется с телеметрией. Расхождение называется вслух."""
    state = {"results": {"T01": {"ident": "T01", "verdict": "PASS", "task_id": "task-ghost"}}}
    telemetry = {"available": True, "task_ids": ["task-real"], "statuses": {"passed": 1, "failed": 0, "blocked": 0}}
    notes = acceptance._cross_check(state, telemetry)
    assert any("task-ghost" in n for n in notes)


def test_a_claimed_failure_with_an_all_green_corpus_is_named(acceptance):
    state = {"results": {"T06": {"ident": "T06", "verdict": "FAIL", "task_id": ""}}}
    telemetry = {"available": True, "task_ids": [], "statuses": {"passed": 3, "failed": 0, "blocked": 0}}
    notes = acceptance._cross_check(state, telemetry)
    assert any("failed/blocked" in n for n in notes)


def test_a_missing_corpus_blocks_the_telemetry_scenario(acceptance):
    notes = acceptance._cross_check({"results": {}}, {"available": False, "reason": "пусто"})
    assert any("TEST 10" in n for n in notes)


def test_the_run_state_survives_a_reload(acceptance):
    state = {"schema_version": 1, "results": {"T01": {"ident": "T01", "verdict": "PASS"}}}
    acceptance.save_state(state)
    assert acceptance.load_state()["results"]["T01"]["verdict"] == "PASS"


def test_a_corrupt_state_file_does_not_destroy_the_evening(acceptance):
    acceptance.state_path().write_text("{ не json", encoding="utf-8")
    reloaded = acceptance.load_state()
    assert reloaded["results"] == {} and reloaded.get("corrupt_previous_state") is True


# ------------------------------------------------------------- стартовый путь

def test_a_single_canonical_entrypoint_exists_for_each_platform():
    """Одна точка входа на платформу. Второй способ запуска — это второй способ
    сломаться."""
    assert (REPO / "start-bossman.ps1").exists()
    assert (REPO / "start-bossman.sh").exists()
    assert os.access(REPO / "start-bossman.sh", os.X_OK)


def test_the_launcher_refuses_to_start_when_the_doctor_blocks():
    for script in ("start-bossman.ps1", "start-bossman.sh"):
        body = (REPO / script).read_text(encoding="utf-8")
        assert "bossman_doctor.py" in body, f"{script} не вызывает доктора"
        assert "exit" in body.lower()
    ps1 = (REPO / "start-bossman.ps1").read_text(encoding="utf-8")
    assert "$DoctorCode -ne 0" in ps1 and "exit $DoctorCode" in ps1
    sh = (REPO / "start-bossman.sh").read_text(encoding="utf-8")
    assert "if ! \"$VENV_PY\" scripts/bossman_doctor.py" in sh


def test_the_shell_launcher_is_syntactically_valid():
    import shutil
    from pathlib import Path as _P
    candidates = [shutil.which("bash"), _P(r"C:\Program Files\Git\bin\bash.exe"),
                  _P(r"C:\Program Files\Git\usr\bin\bash.exe")]
    bash = next((str(b) for b in candidates if b and _P(b).exists()
                 and "system32" not in str(b).lower()), None)  # system32\bash.exe — WSL-заглушка без дистрибутива
    if bash is None:
        pytest.skip("POSIX-оболочка недоступна — синтаксис start-bossman.sh проверяется на POSIX/CI")
    assert subprocess.run([bash, "-n", str(REPO / "start-bossman.sh")]).returncode == 0


# ------------------------------------------- вечерняя приёмка: точный SHA

def test_both_launchers_run_the_exact_sha_owner_entrypoint():
    """Вечерний тест владельца обязан идти через обёртку по точному SHA.

    evening_acceptance.py собирает улики без привязки к коммиту: по ним нельзя
    отличить прогон на этом дереве от прогона на другом. Обёртка проверяет
    ветку, чистое дерево, совпадение с живым origin и раскладывает улики по
    каталогу SHA.
    """
    for script in ("start-bossman.ps1", "start-bossman.sh"):
        body = (REPO / script).read_text(encoding="utf-8")
        evening = body[body.index("EveningTest" if script.endswith(".ps1") else 'EVENING" -eq 1'):]
        assert "evening_owner_run.py" in evening, f"{script}: вечерний путь не вызывает обёртку"
        assert "evening_acceptance.py run" not in evening, (
            f"{script}: остался прямой запуск старого харнесса — это тихий откат"
        )


def test_the_evening_path_has_no_silent_fallback():
    """Нет обёртки — честный отказ, а не тихий запуск старого харнесса."""
    ps1 = (REPO / "start-bossman.ps1").read_text(encoding="utf-8")
    sh = (REPO / "start-bossman.sh").read_text(encoding="utf-8")
    assert "Test-Path $OwnerRun" in ps1 and "exit 1" in ps1
    assert "! -f scripts/evening_owner_run.py" in sh


def test_doctor_only_and_normal_start_are_unchanged():
    """Правка вечернего пути не имеет права трогать обычный запуск."""
    ps1 = (REPO / "start-bossman.ps1").read_text(encoding="utf-8")
    assert "if ($DoctorOnly) { exit 0 }" in ps1
    assert '$LaunchArgs = @("-m", "bcc.desktop")' in ps1
    sh = (REPO / "start-bossman.sh").read_text(encoding="utf-8")
    assert '[ "$DOCTOR_ONLY" -eq 0 ] || exit 0' in sh
    assert "ARGS=(-m bcc.desktop)" in sh


def test_the_powershell_launcher_is_syntactically_valid():
    import shutil
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell недоступен — синтаксис start-bossman.ps1 проверяется на Windows/CI")
    script = (
        "$e=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{REPO / 'start-bossman.ps1'}',"
        "[ref]$null,[ref]$e);"
        "if($e -and $e.Count){$e|%{Write-Error $_.Message};exit 1};exit 0"
    )
    assert subprocess.run([pwsh, "-NoProfile", "-Command", script]).returncode == 0


@pytest.mark.parametrize("script", ["evening_acceptance.py", "owner_breaker.py",
                                    "evening_owner_run.py"])
def test_the_harness_prints_on_a_legacy_windows_console(script):
    """Русский вывод не должен убивать проверку на консоли в cp1252.

    На windows-latest первый же print падал с UnicodeEncodeError, и владелец
    видел не вердикт, а трейсбек. Лаунчеры экспортировали PYTHONUTF8 и этим
    маскировали дефект; прямой запуск и CI — нет.
    """
    argv = ["--ci", "preflight"] if script == "evening_owner_run.py" else ["verify"]
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / script), *argv],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=180,
    )
    assert "UnicodeEncodeError" not in (done.stdout + done.stderr), done.stderr[-400:]
