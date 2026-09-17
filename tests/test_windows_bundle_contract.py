"""The one-download Windows archive must keep its promise, not just its name.

These run everywhere, including Linux CI: they check the parts of the contract
that are decided by the builder's own logic and file layout rather than by a
Windows runner. Whether the archive actually boots is decided by
``tools/verify_windows_bundle.py`` on windows-latest — this file makes sure the
claim printed in the manifest cannot drift away from what is packaged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import re

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import build_windows_bundle as bundle  # noqa: E402


def test_the_archive_is_named_after_the_exact_commit() -> None:
    name = bundle.bundle_name("0123456789abcdef0123456789abcdef01234567")
    assert name == "BOSSMAN-Windows-x64-0123456789ab"
    assert " " not in name


def test_one_download_means_one_download() -> None:
    """The count the owner is quoted must be derived, never hand-written."""
    complete = bundle.manifest_for(sha="a" * 40, when="now", files=[], contents={},
                                   checks={}, required_downloads=[])
    assert complete["downloads_required_for_bossman_app"] == 1
    assert complete["repository_clone_required"] is False
    assert complete["system_python_required"] is False

    # Anything the first run still has to fetch is counted and named, so a
    # partial bundle cannot be reported as a complete one.
    partial = bundle.manifest_for(
        sha="a" * 40, when="now", files=[], contents={}, checks={},
        required_downloads=[{"component": "ffmpeg", "reason": "not supplied",
                             "acquired_by": "first run"}])
    assert partial["downloads_required_for_bossman_app"] == 2
    assert partial["required_downloads"][0]["component"] == "ffmpeg"


def test_every_support_script_the_bundle_ships_exists_in_the_checkout() -> None:
    for origin, name in bundle.SUPPORT_SCRIPTS:
        assert origin.exists(), f"{name} would be missing from the archive: {origin}"


def test_the_bundle_ships_the_canonical_icon_set() -> None:
    sys.path.insert(0, str(REPO / "tools"))
    import app_icons
    icons = REPO / "command-center" / "ui" / "icons"
    for name in bundle.ICON_FILES:
        assert (icons / name).exists(), f"{name} is packaged but not in the checkout"
    assert app_icons.MASTER in bundle.ICON_FILES
    assert app_icons.ICO_NAME in bundle.ICON_FILES
    for size in app_icons.PNG_SIZES:
        assert f"icon-{size}.png" in bundle.ICON_FILES, size


# Список выводится из самой сборки, а не переписывается руками: launcher,
# добавленный завтра, попадает под тот же контракт без чьей-либо памяти.
ROOT_LAUNCHERS = sorted(n for n in bundle.launcher_files()
                        if n.endswith(".cmd") and "/" not in n)


def test_every_root_launcher_is_covered_by_the_contract() -> None:
    """Иначе новый .cmd тихо остаётся непроверенным."""
    assert len(ROOT_LAUNCHERS) >= 3, ROOT_LAUNCHERS
    assert "Machine-Report.cmd" in ROOT_LAUNCHERS


@pytest.mark.parametrize("name", ROOT_LAUNCHERS)
def test_a_launcher_only_starts_a_script_the_archive_actually_ships(name: str) -> None:
    """BL-036 в другом обличье: кнопка, ведущая в файл, которого в поставке нет.

    Проверяется не текст, а поставка: каждое имя `app-support\\<файл>.py` из
    launcher обязано присутствовать в SUPPORT_SCRIPTS.
    """
    body = bundle.launcher_files()[name]
    shipped = {shipped_name for _, shipped_name in bundle.SUPPORT_SCRIPTS}
    referenced = set(re.findall(r"app-support\\([A-Za-z0-9_.-]+\.py)", body))
    assert referenced or name == "Start-Bossman.cmd", (
        f"{name}: разбор не нашёл ни одного .py — проверка стала пустой")
    assert referenced <= shipped, f"{name} запускает то, чего нет в архиве: {referenced - shipped}"


@pytest.mark.parametrize("name", ROOT_LAUNCHERS)
def test_the_launchers_use_only_what_is_beside_them(name: str) -> None:
    body = bundle.launcher_files()[name]
    assert '%BOSSMAN_HOME%' in body
    assert 'call "%BOSSMAN_HOME%app-support\\_env.cmd"' in body
    # Paths are quoted: the owner is entitled to unzip into "C:\Program Files".
    assert '"%BOSSMAN_HOME%runtime\\python.exe"' in body
    # A launcher that reaches into a checkout is not a downloadable product.
    for forbidden in ("git ", "pip install", "\\command-center\\", "..\\"):
        assert forbidden not in body, f"{name} reaches outside the archive: {forbidden}"


def test_the_environment_points_at_the_bundled_prerequisites() -> None:
    env = bundle.launcher_files()["app-support/_env.cmd"]
    assert 'set "PLAYWRIGHT_BROWSERS_PATH=%BOSSMAN_HOME%browser"' in env
    assert "%BOSSMAN_HOME%media;%PATH%" in env, (
        "the bundled ffmpeg/ffprobe must win over anything on the machine"
    )
    assert 'set "PYTHONUTF8=1"' in env
    # A missing runtime is an honest error, not a fall-through to system Python.
    assert 'if not exist "%BOSSMAN_HOME%runtime\\python.exe"' in env
    assert "exit /b 1" in env


def test_the_layout_has_a_place_for_every_prerequisite() -> None:
    for required in ("runtime", "browser", "media", "icons", "LICENSES", "app-support"):
        assert required in bundle.BUNDLE_DIRS


def test_the_builder_refuses_to_pretend_on_a_non_windows_host() -> None:
    """Building elsewhere must say so, not emit a half bundle labelled Windows."""
    import os
    if os.name == "nt":
        pytest.skip("this host IS Windows; the refusal path is for Linux/macOS builders")
    assert bundle.main(["--out", "/tmp/does-not-matter"]) == 2


def test_the_bundle_writes_console_entry_points_that_survive_relocation() -> None:
    """pip --target leaves wrappers bound to the BUILD machine's interpreter.

    The archive's acceptance failed on a missing ``runtime\\Scripts\\bossman.exe``:
    the commands the product declares were simply not there. The shims the
    builder writes instead must resolve the runtime beside themselves, so the
    owner may unzip anywhere, including a path with spaces.
    """
    shim = bundle.console_shim("bcc.cli:main")
    assert '"%~dp0..\\python.exe"' in shim, "the shim must not hard-code a build path"
    assert "from bcc.cli import main as obj" in shim
    assert "sys.exit(obj())" in shim
    assert "%*" in shim, "arguments must reach the command"
    assert shim.endswith("exit /b %ERRORLEVEL%\r\n"), "the exit code must be the command's"

    # Dotted entry points are a real spelling in the wild and must not be
    # silently turned into a call on the wrong object.
    assert "sys.exit(obj.run())" in bundle.console_shim("bossman.cli:app.run")


def test_console_entry_points_are_collected_from_what_is_installed(tmp_path) -> None:
    site = tmp_path / "site-packages"
    (site / "demo-1.0.dist-info").mkdir(parents=True)
    (site / "demo-1.0.dist-info" / "entry_points.txt").write_text(
        "[console_scripts]\nbossman = bossman.cli:main\n\n[gui_scripts]\nx = y:z\n",
        encoding="utf-8")
    found = bundle.console_scripts(site)
    assert found == {"bossman": "bossman.cli:main"}, found


def test_the_bundled_commands_are_on_the_launcher_path() -> None:
    env = bundle.launcher_files()["app-support/_env.cmd"]
    assert 'set "PATH=%BOSSMAN_HOME%runtime\\Scripts;%BOSSMAN_HOME%media;%PATH%"' in env


def test_the_windows_automation_packages_are_part_of_the_build(tmp_path) -> None:
    """The archive installs them; the owner is never sent to run pip.

    A complete archive still reported the doctor's ``computer-operator`` check
    BLOCKED, because pywinauto/pywin32/pyautogui/Pillow live in the ``windows``
    extra of bossman-core and a bare wheel install leaves them out.
    """
    wheels = [tmp_path / "bossman_core-1.0-py3-none-any.whl",
              tmp_path / "bossman_shared-1.0-py3-none-any.whl"]
    requested = bundle.pip_requirements(wheels)
    assert requested[0].endswith("bossman_core-1.0-py3-none-any.whl[runtime]")
    assert requested[1].endswith("bossman_shared-1.0-py3-none-any.whl"), (
        "only the distribution that declares the extra may carry it"
    )

    extras = (REPO / "bossman-core" / "pyproject.toml").read_text(encoding="utf-8")
    for package in ("pywinauto", "pywin32", "pyautogui", "pillow"):
        assert package in extras, f"{package} is no longer in the windows extra"


def test_media_archive_retains_distinct_upstream_licenses(tmp_path, monkeypatch) -> None:
    import zipfile
    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, content in {
            "ffmpeg/bin/ffmpeg.exe": b"ffmpeg",
            "ffmpeg/bin/ffprobe.exe": b"ffprobe",
            "ffmpeg/LICENSE.txt": b"GPL",
            "ffmpeg/licenses/x264/LICENSE": b"x264 notice",
            "ffmpeg/licenses/other/LICENSE": b"other notice",
        }.items():
            zf.writestr(name, content)
    monkeypatch.setattr(bundle, "fetch", lambda *_: archive)
    contents, required = bundle.install_media(tmp_path / "product/media", tmp_path, "https://example.test/media.zip")
    assert not required
    assert len(contents["license_files"]) == 3
    assert (tmp_path / "product/LICENSES/ffmpeg/ffmpeg/licenses/x264/LICENSE").read_bytes() == b"x264 notice"
    assert (tmp_path / "product/LICENSES/ffmpeg/ffmpeg/licenses/other/LICENSE").read_bytes() == b"other notice"


def test_bundle_media_rejects_missing_binaries(tmp_path) -> None:
    import verify_windows_bundle as verify
    problems, details = verify.check_media(tmp_path, {})
    assert len(problems) == 2
    assert details == {"ffmpeg": "NOT_BUNDLED", "ffprobe": "NOT_BUNDLED"}


@pytest.mark.parametrize("failure", ["encoder", "probe", "decode", None])
def test_bundle_media_requires_export_probe_and_full_decode(tmp_path, monkeypatch, failure) -> None:
    """Printing a valid version is insufficient (the old LGPL false green)."""
    import json
    import os
    import subprocess
    import verify_windows_bundle as verify
    (tmp_path / "media").mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (tmp_path / "media" / (name + (".exe" if os.name == "nt" else ""))).touch()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert Path(argv[0]).parent == tmp_path / "media", "never borrow PATH binaries"
        if "-version" in argv:
            return subprocess.CompletedProcess(argv, 0, "ffmpeg version test\n", "")
        if "-c:v" in argv:
            assert argv[argv.index("-c:v") + 1] == "libx264"
            if failure == "encoder":
                return subprocess.CompletedProcess(argv, 1, "", "Unknown encoder libx264")
            Path(argv[-1]).write_bytes(b"encoded media")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "-show_streams" in argv:
            streams = [{"codec_type": "video", "codec_name": "h264"}]
            if failure != "probe":
                streams.append({"codec_type": "audio", "codec_name": "aac"})
            return subprocess.CompletedProcess(argv, 0, json.dumps({"streams": streams}), "")
        assert "-xerror" in argv and "0:v" in argv and "0:a" in argv
        return subprocess.CompletedProcess(argv, int(failure == "decode"), "", "decode error")

    monkeypatch.setattr(verify, "_run", run)
    problems, details = verify.check_media(tmp_path, {})
    assert bool(problems) == bool(failure)
    if failure:
        assert details["default_export"] == "FAILED"
    else:
        assert details["default_export"]["fully_decoded"] is True
        assert len(calls) == 5


# ------------------------------------------------ OA-01 on the extracted archive

def _evening_stdout(evidence: Path, result: dict | None) -> str:
    if result is not None:
        (evidence / "OWNER_EVENING_RESULT.json").write_text(json.dumps(result), encoding="utf-8")
    return f"header\nOWNER_EVENING_EVIDENCE={evidence}\nOWNER_EVENING_RESULT={(result or {}).get('verdict')}\n"


import json  # noqa: E402


def test_the_verifier_reads_the_structured_evening_result_not_only_the_exit_code(tmp_path) -> None:
    import verify_windows_bundle as verify
    evidence = tmp_path / "run"
    evidence.mkdir()
    stdout = _evening_stdout(evidence, {"verdict": "PASS", "run_id": "r1", "reasons": []})
    assert verify._evening_result(stdout) == {"verdict": "PASS", "run_id": "r1", "reasons": []}
    assert verify._evening_result("no pointer at all") is None
    (evidence / "OWNER_EVENING_RESULT.json").write_text("{broken", encoding="utf-8")
    assert verify._evening_result(stdout) is None


@pytest.mark.parametrize("rc,verdict,control,expected_status", [
    (0, "PASS", "PASS", "PASS"),
    (2, "OWNER_REQUIRED", "PASS", "OWNER_REQUIRED"),
    (1, "FAIL", "PASS", "FAIL"),
    (3, "PARTIAL", "PASS", "FAIL"),
    (0, "FAIL", "PASS", "FAIL"),       # exit code and verdict disagree
    (2, "PASS", "PASS", "FAIL"),
    (0, None, "PASS", "FAIL"),         # no readable result at all
    (0, "PASS", "FAILED: exit 0, verdict 'PASS', reasons []", "FAIL"),  # the negative control passed
])
def test_the_archive_verdict_needs_a_consistent_evening_result_and_its_negative_control(
        tmp_path, monkeypatch, rc, verdict, control, expected_status) -> None:
    import subprocess
    import zipfile
    import verify_windows_bundle as verify

    home_name = "BOSSMAN-Windows-x64-synthetic"
    archive = tmp_path / f"{home_name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{home_name}/MANIFEST.json", json.dumps({"source_sha": "a" * 40, "files": []}))
        zf.writestr(f"{home_name}/app-support/bossman_doctor.py", "# doctor\n")
        zf.writestr(f"{home_name}/app-support/bundle_evening_test.py", "# evening\n")
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    def fake_evening(home, env):
        result = None if verdict is None else {"verdict": verdict, "run_id": "r1",
                                               "reasons": [] if verdict == "PASS" else [{"code": "x"}]}
        stdout = _evening_stdout(evidence, result)
        return subprocess.CompletedProcess(["evening"], rc, stdout, ""), verify._evening_result(stdout)

    monkeypatch.setattr(verify, "run_evening", fake_evening)
    monkeypatch.setattr(verify, "evening_negative_control", lambda home, env: control)
    monkeypatch.setattr(verify, "check_icons", lambda home: [])
    monkeypatch.setattr(verify, "check_no_repo_dependency", lambda home, env: [])
    monkeypatch.setattr(verify, "check_media", lambda home, env: ([], {"ffmpeg": "synthetic"}))
    monkeypatch.setattr(verify, "check_browser", lambda home, env: ([], {"chromium": "synthetic"}))
    monkeypatch.setattr(verify, "_harness_sha", lambda: "a" * 40)
    out = tmp_path / "bundle-acceptance.json"
    code = verify.main(["--archive", str(archive), "--expected-sha", "a" * 40, "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == expected_status, report["problems"]
    assert code == {"PASS": 0, "OWNER_REQUIRED": 2}.get(expected_status, 1)
    assert report["details"]["evening_verdict"] == (verdict or "NO_RESULT")
    assert report["details"]["evening_negative_control"] == control
    binding = report["binding"]
    assert binding["source_sha"] == "a" * 40 and binding["harness_sha"] == "a" * 40
    assert binding["archive_sha256"] == report["details"]["archive_sha256"]
    assert isinstance(binding["run_id"], str) and binding["run_id"]  # GITHUB_RUN_ID in CI, "local" elsewhere


def test_the_negative_control_hides_the_doctor_and_restores_it(tmp_path, monkeypatch) -> None:
    import subprocess
    import verify_windows_bundle as verify
    home = tmp_path / "home"
    (home / "app-support").mkdir(parents=True)
    doctor = home / "app-support" / "bossman_doctor.py"
    doctor.write_text("# doctor\n", encoding="utf-8")
    seen = {}

    def fake_evening(home_, env):
        seen["doctor_present_during_control"] = doctor.exists()
        evidence = tmp_path / "control"
        evidence.mkdir(exist_ok=True)
        stdout = _evening_stdout(evidence, {"verdict": "FAIL", "run_id": "c", "reasons": [{"code": "doctor_not_shipped"}]})
        return subprocess.CompletedProcess(["evening"], 1, stdout, ""), verify._evening_result(stdout)

    monkeypatch.setattr(verify, "run_evening", fake_evening)
    assert verify.evening_negative_control(home, {}) == "PASS"
    assert seen["doctor_present_during_control"] is False
    assert doctor.exists() and doctor.read_text(encoding="utf-8") == "# doctor\n"

    def accepting(home_, env):
        evidence = tmp_path / "control2"
        evidence.mkdir(exist_ok=True)
        stdout = _evening_stdout(evidence, {"verdict": "PASS", "run_id": "c", "reasons": []})
        return subprocess.CompletedProcess(["evening"], 0, stdout, ""), verify._evening_result(stdout)

    monkeypatch.setattr(verify, "run_evening", accepting)
    assert verify.evening_negative_control(home, {}).startswith("FAILED: exit 0")
    assert doctor.exists()


# ------------------------------------------------ OA-04: owner runners ship

def test_the_owner_runners_ci_drives_are_shipped_in_the_archive() -> None:
    shipped = {name for _, name in bundle.SUPPORT_SCRIPTS}
    for name in ("installed_ui_sweep.py", "ui_acceptance_sweep.py", "live_openrouter_owner.py",
                 "target_hardware_acceptance.py", "bundle_evening_test.py", "bossman_doctor.py",
                 "verify_installed_product.py", "owner_machine_report.py"):
        assert name in shipped, name


def test_the_eight_oss_directions_are_stated_per_archive_and_never_verified_by_the_build() -> None:
    packages = [{"name": "docling-slim", "version": "2.127.0"}, {"name": "qdrant_client", "version": "1.15.1"},
                {"name": "bossman-command-center", "version": "0.1.0"}]
    rows = bundle.oss_directions_for(packages)
    assert [row["direction"] for row in rows] == ["llama.cpp", "Docling", "Qdrant", "faster-whisper",
                                                  "SearXNG", "ComfyUI", "UI-TARS", "GrapesJS"]
    by_name = {row["direction"]: row for row in rows}
    assert by_name["Docling"]["BUNDLED"] is True and by_name["Docling"]["bundled_distribution"] == "docling-slim==2.127.0"
    assert by_name["faster-whisper"]["BUNDLED"] is False, "not installed in this synthetic runtime"
    assert by_name["faster-whisper"]["MODEL_REQUIRED"] is True
    assert by_name["Qdrant"]["EXTERNAL_SERVICE_REQUIRED"] is True and by_name["Qdrant"]["BUNDLED"] is True
    assert by_name["llama.cpp"]["BUNDLED"] is False and by_name["llama.cpp"]["EXTERNAL_SERVICE_REQUIRED"] is True
    assert by_name["GrapesJS"]["BUNDLED"] is True
    assert all(row["VERIFIED"] is False and row["verify_by"] for row in rows)
    assert (REPO / "command-center" / "ui" / "vendor" / "grapesjs" / "grapes.min.js").is_file()
