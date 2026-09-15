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


@pytest.mark.parametrize("name", ["Start-Bossman.cmd", "Evening-Test.cmd"])
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
