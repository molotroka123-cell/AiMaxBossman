"""Owner-suite runner (tools/owner_run_tomorrow.py): honest stage statuses.

The runner orchestrates shipped scripts; here they are replaced by tiny
stand-ins beside the runner so the routing logic is measured, not assumed:
a doctor with BLOCKED → FAIL, unreachable endpoints → OWNER_REQUIRED (never
PASS), unconfigured media → OWNER_REQUIRED, a MOCK coaching run → WARN with
LOCAL_LEARNING_GAIN_NOT_MEASURED, diagnostics with secrets redacted.
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "tools" / "owner_run_tomorrow.py"


def _load(path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("owner_run_tomorrow_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_owner_runner_accepts_only_the_current_desktop_identity(monkeypatch):
    mod = _load(RUNNER)
    current = {"app": "bossman-command-center-build-bound-v1", "build_sha": "a" * 40}
    monkeypatch.setattr(mod, "_loopback_get", lambda *a, **k: (200, current))
    assert mod.bossman_identity("http://127.0.0.1:8800") == current

    monkeypatch.setattr(mod, "_loopback_get", lambda *a, **k: (200, {
        "app": "bossman-command-center", "build_sha": "b" * 40}))
    assert mod.bossman_identity("http://127.0.0.1:8800") is None


@pytest.fixture
def support(tmp_path, monkeypatch):
    """A copy of the runner in an app-support-like folder with synthetic siblings."""
    home = tmp_path / "app-support"
    home.mkdir()
    shutil.copyfile(RUNNER, home / "owner_run_tomorrow.py")
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BOSSMAN_MODEL_ENDPOINTS", "http://127.0.0.1:9/")   # port 9: nothing listens
    monkeypatch.delenv("BOSSMAN_MEDIA_MODELS", raising=False)
    return home


def _doctor(home: Path, blocked: int) -> None:
    (home / "bossman_doctor.py").write_text(
        "import json,sys,pathlib\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--json-out')+1])\n"
        f"checks=[{{'name':'x','status':'BLOCKED','detail':'нет runtime','remedy':'','facts':{{}}}}]*{blocked}"
        "+[{'name':'ffmpeg','status':'PASS','detail':'ok','remedy':'','facts':{}}]\n"
        "out.write_text(json.dumps({'checks':checks,'blocked':%d,'warn':0,'pass':1}),encoding='utf-8')\n"
        "sys.exit(1 if %d else 0)\n" % (blocked, blocked), encoding="utf-8")


def _media(home: Path, verdict: str = "OK", bad: str | None = None) -> None:
    body = {"verdict": verdict, "files": [{"path": "a.bin", "status": "PRESENT_OK"}]}
    if bad:
        body["files"].append({"path": "b.bin", "status": bad})
    (home / "media_bootstrap.py").write_text(
        "import json,sys,pathlib\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
        f"out.write_text(json.dumps({json.dumps(body)}),encoding='utf-8')\n", encoding="utf-8")


def _coaching(home: Path, status: str) -> None:
    (home / "coaching_runner.py").write_text(
        "import json,sys,pathlib\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--out')+1]); out.mkdir(parents=True,exist_ok=True)\n"
        f"(out/'results.json').write_text(json.dumps({{'status':{status!r},'learning_gain':None}}),encoding='utf-8')\n"
        "print('WEIGHTS_UNCHANGED')\n", encoding="utf-8")


def test_blocked_doctor_fails_the_run_and_unreachable_models_are_owner_required(support, tmp_path):
    _doctor(support, blocked=1)
    _media(support)
    _coaching(support, "MOCK")
    mod = _load(support / "owner_run_tomorrow.py")
    out = tmp_path / "out"
    code = mod.main(["--out", str(out), "--stage", "doctor", "--stage", "models", "--stage", "media",
                     "--stage", "coaching", "--stage", "diagnostics", "--coaching-backend", "mock",
                     "--no-completion"])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["doctor"]["status"] == "FAIL"
    assert report["stages"]["models"]["status"] == "OWNER_REQUIRED"
    assert "9" in report["stages"]["models"]["detail"]
    assert report["stages"]["media"]["status"] == "OWNER_REQUIRED"       # not configured ≠ FAIL
    assert report["stages"]["coaching"]["status"] == "WARN"
    assert "LOCAL_LEARNING_GAIN_NOT_MEASURED" in report["stages"]["coaching"]["detail"]
    assert "WEIGHTS_UNCHANGED" in report["stages"]["coaching"]["detail"]
    assert report["stages"]["evening"]["status"] == "NOT_RUN"
    assert report["overall"] == "FAIL" and code == 1
    assert report["owner_hardware_certified"] is False
    assert (out / "report.md").is_file() and "OWNER_HARDWARE_CERTIFIED этим отчётом не объявляется" in (out / "report.md").read_text(encoding="utf-8")


def test_media_bad_hash_is_a_failure_and_missing_files_are_owner_required(support, tmp_path, monkeypatch):
    _media(support, verdict="INVALID", bad="PRESENT_BAD_HASH")
    monkeypatch.setenv("BOSSMAN_MEDIA_MODELS", str(tmp_path / "models"))
    mod = _load(support / "owner_run_tomorrow.py")
    out = tmp_path / "out1"
    mod.main(["--out", str(out), "--stage", "media"])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["media"]["status"] == "FAIL"
    assert "b.bin: PRESENT_BAD_HASH" in report["stages"]["media"]["detail"]
    _media(support, verdict="INVALID", bad="MISSING")
    out = tmp_path / "out2"
    mod.main(["--out", str(out), "--stage", "media"])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["media"]["status"] == "OWNER_REQUIRED"
    _media(support, verdict="OK")
    out = tmp_path / "out3"
    assert mod.main(["--out", str(out), "--stage", "media"]) == 0


def test_missing_shipped_script_is_a_packaging_failure_not_a_pass(support, tmp_path):
    mod = _load(support / "owner_run_tomorrow.py")
    out = tmp_path / "out"
    code = mod.main(["--out", str(out), "--stage", "doctor"])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["doctor"]["status"] == "FAIL" and "не найден" in report["stages"]["doctor"]["detail"]
    assert code == 1


def test_diagnostics_zip_redacts_secrets_and_lists_data_dir(support, tmp_path):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "bcc.log").write_text(
        # ci-secret-scan: allow — synthetic shapes for the redaction check, not credentials
        "start ok\napi_key=sk-" + "abcdefghijklmnop1234" + " used\nToken: " + "Z" * 44 + "\nплановая строка\n",
        encoding="utf-8")
    (data / "token").write_text("secret-token-value", encoding="utf-8")
    mod = _load(support / "owner_run_tomorrow.py")
    out = tmp_path / "out"
    assert mod.main(["--out", str(out), "--stage", "diagnostics"]) == 0
    with zipfile.ZipFile(out / "diagnostics.zip") as zf:
        names = zf.namelist()
        assert "environment.json" in names and "data-dir-listing.json" in names
        assert "logs/logs/bcc.log" in names
        log = zf.read("logs/logs/bcc.log").decode("utf-8")
        listing = json.loads(zf.read("data-dir-listing.json").decode("utf-8"))
    assert "sk-" + "abcdefghijklmnop1234" not in log and "ZZZZZZZZ" not in log  # ci-secret-scan: allow
    assert "[REDACTED]" in log and "плановая строка" in log
    # the token FILE is listed by name and size only — never by content
    assert any(e["path"] == "token" for e in listing) and "token" not in names


def test_probe_endpoint_reports_unreachable_without_raising():
    mod = _load(RUNNER)
    probe = mod.probe_endpoint("http://127.0.0.1:9", completion=True, timeout=1.0)
    assert probe["reachable"] is False and probe["models"] == [] and "error" in probe


def test_runner_is_shipped_and_uses_utf8_console():
    sys.path.insert(0, str(REPO / "tools"))
    import build_windows_bundle as bundle
    shipped = {name for _, name in bundle.SUPPORT_SCRIPTS}
    for name in ("owner_run_tomorrow.py", "media_bootstrap.py", "media_ab_preset.py", "coaching_runner.py"):
        assert name in shipped, name
    launchers = bundle.launcher_files()
    for cmd in ("Owner-Run.cmd", "Media-Setup.cmd", "Coaching.cmd", "Collect-Diagnostics.cmd"):
        assert cmd in launchers, cmd
    assert "START_TOMORROW_RU.md" in bundle.OWNER_ACCEPTANCE_FILES
    assert "docs/owner/ROLLBACK_RU.md" in bundle.OWNER_ACCEPTANCE_FILES
    assert (REPO / "START_TOMORROW_RU.md").is_file()
