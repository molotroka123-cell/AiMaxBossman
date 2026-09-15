"""Installation checks must describe the executable that BCC will launch.

The fixture provides metadata and ordinary files, never a live Chromium proof.
"""
from __future__ import annotations

import importlib.machinery
import json
import os
import sys
import types
from pathlib import Path

import pytest

from bcc.features import browser as feature
from bcc import browser_runtime as runtime
from bcc.v2 import browser_control
from bcc.v2.browser_control import BrowserManager, BrowserPolicy, BrowserUnavailable


@pytest.fixture
def installed_metadata(tmp_path, monkeypatch):
    package = tmp_path / "playwright" / "driver" / "package"
    package.mkdir(parents=True)
    (package / "browsers.json").write_text(json.dumps({"browsers": [
        {"name": "chromium", "revision": "1234"},
        {"name": "chromium-headless-shell", "revision": "1234"},
    ]}), encoding="utf-8")
    playwright = types.ModuleType("playwright")
    playwright.__file__ = str(package.parents[1] / "__init__.py")
    playwright.__path__ = [str(package.parents[1])]
    playwright.__spec__ = importlib.machinery.ModuleSpec("playwright", loader=None, is_package=True)
    async_api = types.ModuleType("playwright.async_api")

    def never_start_driver():
        raise AssertionError("readiness must not start a Playwright driver")

    async_api.async_playwright = never_start_driver
    playwright.async_api = async_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    monkeypatch.setattr(feature, "CHROMIUM", str(tmp_path / "no-preinstalled-chromium"))
    monkeypatch.setattr(browser_control, "PREINSTALLED_CHROMIUM", feature.CHROMIUM)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "missing-browsers"))
    monkeypatch.setattr(runtime, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(runtime, "platform", types.SimpleNamespace(machine=lambda: "x86_64"))
    return package


async def test_missing_browser_directory_does_not_grant_capability(env, installed_metadata):
    result = (await env.client.get("/api/capabilities")).json()
    assert result["probes"]["chromium"] is False


def test_importable_adapter_without_browser_is_unavailable(tmp_path, installed_metadata):
    assert BrowserManager(tmp_path / "session-data").available is False


async def test_browser_health_does_not_report_a_missing_executable(env, installed_metadata):
    result = (await env.client.get("/api/browser/health")).json()
    assert result["available"] is False
    assert result["active_sessions"] == 0


async def test_a_browser_directory_is_not_an_executable(env, installed_metadata, tmp_path, monkeypatch):
    directory = tmp_path / "directory-named-chromium"
    directory.mkdir()
    monkeypatch.setattr(feature, "CHROMIUM", str(directory))
    result = (await env.client.get("/api/capabilities")).json()
    assert result["probes"]["chromium"] is False


def _binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture executable; not a live browser")
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("kind", ["empty", "wrong-revision", "wrong-os", "permission-denied"])
def test_populated_cache_is_not_sufficient(tmp_path, installed_metadata, monkeypatch, kind):
    root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    root.mkdir()
    if kind == "wrong-revision":
        _binary(root / "chromium-1000" / "chrome-linux64" / "chrome")
    elif kind == "wrong-os":
        _binary(root / "chromium-1234" / "chrome-win64" / "chrome.exe")
    elif kind == "permission-denied":
        binary = _binary(root / "chromium-1234" / "chrome-linux64" / "chrome")
        # Windows requires an ACL fixture to deny execution natively. Simulate
        # only that OS result, not the resolver or its file checks.
        real_access = os.access
        monkeypatch.setattr(runtime, "os", types.SimpleNamespace(
            environ=os.environ, X_OK=os.X_OK,
            access=lambda path, mode: False if Path(path) == binary else real_access(path, mode)))
    assert BrowserManager(tmp_path / "session-data").available is False


@pytest.mark.parametrize("os_name,machine,relative", [
    ("linux", "x86_64", "chrome-linux64/chrome"),
    ("linux", "x86_64", "chrome-linux/chrome"),
    ("linux", "aarch64", "chrome-linux/chrome"),
    ("win32", "AMD64", "chrome-win64/chrome.exe"),
    ("win32", "AMD64", "chrome-win/chrome.exe"),
    ("darwin", "arm64", "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
    ("darwin", "x86_64", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
])
def test_current_and_supported_legacy_layouts(tmp_path, installed_metadata, monkeypatch,
                                            os_name, machine, relative):
    monkeypatch.setattr(runtime, "sys", types.SimpleNamespace(platform=os_name))
    monkeypatch.setattr(runtime, "platform", types.SimpleNamespace(machine=lambda: machine))
    binary = _binary(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium-1234" / relative)
    mgr = BrowserManager(tmp_path / "session-data")
    assert mgr.available is True
    assert mgr.executable_path() == str(binary)


@pytest.mark.parametrize("mode", ["linux-default", "windows-default", "mac-default", "hermetic", "relative"])
def test_installed_cache_location_matches_playwright(tmp_path, installed_metadata, monkeypatch, mode):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    relative = "chrome-linux64/chrome"
    if mode == "hermetic":
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
        root = installed_metadata / ".local-browsers"
    elif mode == "relative":
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "owner-browser-cache")
        monkeypatch.setenv("INIT_CWD", str(tmp_path))
        root = tmp_path / "owner-browser-cache"
    elif mode == "windows-default":
        monkeypatch.setattr(runtime, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        root = tmp_path / "local" / "ms-playwright"
        relative = "chrome-win64/chrome.exe"
    elif mode == "mac-default":
        monkeypatch.setattr(runtime, "sys", types.SimpleNamespace(platform="darwin"))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        root = tmp_path / "Library" / "Caches" / "ms-playwright"
        relative = "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        root = tmp_path / "cache" / "ms-playwright"
    binary = _binary(root / "chromium-1234" / relative)
    assert BrowserManager(tmp_path / "session-data").executable_path() == str(binary)


async def test_install_and_remove_are_visible_without_restart(env, installed_metadata):
    root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    assert (await env.client.get("/api/browser/health")).json()["available"] is False
    binary = _binary(root / "chromium-1234" / "chrome-linux64" / "chrome")
    assert (await env.client.get("/api/capabilities")).json()["probes"]["chromium"] is True
    assert (await env.client.get("/api/browser/health")).json()["available"] is True
    # Installation does not assert that the OS successfully launched anything.
    assert (await env.client.get("/health")).json()["components"]["browser"]["status"] == "UNKNOWN"
    binary.unlink()
    assert (await env.client.get("/api/capabilities")).json()["probes"]["chromium"] is False
    assert (await env.client.get("/health")).json()["components"]["browser"]["status"] == "UNHEALTHY"


async def test_missing_dependency_denies_even_valid_binary(env, installed_metadata, tmp_path, monkeypatch):
    monkeypatch.setattr(feature, "CHROMIUM", str(_binary(tmp_path / "chromium")))
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    assert (await env.client.get("/api/browser/health")).json()["available"] is False
    assert (await env.client.get("/api/capabilities")).json()["probes"]["chromium"] is False
    response = await env.client.post("/api/browser/sessions", json={})
    assert response.status_code == 503
    assert "playwright install chromium" in response.text


async def test_repeated_probes_do_not_create_drivers_or_sessions(env, installed_metadata, monkeypatch):
    import subprocess
    import asyncio
    # `env` builds its BrowserManager BEFORE `installed_metadata` neutralises the
    # preinstalled path, and the manager copies that constant into
    # `preinstalled_executable` at construction. On a host where
    # /opt/pw-browsers/chromium really exists — the development container, and
    # any machine with the browsers preinstalled — the manager therefore kept
    # answering the real path and this test failed on setup rather than on its
    # subject. Point the existing manager at the fixture's absent path, the same
    # substitution the fixture already makes for the two module constants.
    monkeypatch.setattr(env.svc.browser, "preinstalled_executable", feature.CHROMIUM)
    binary = _binary(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium-1234" / "chrome-linux64" / "chrome")
    def no_child(*args, **kwargs):
        raise AssertionError("health probe started a subprocess")
    monkeypatch.setattr(subprocess, "Popen", no_child)
    before = set(asyncio.all_tasks())
    for _ in range(100):
        assert env.svc.browser.executable_path() == str(binary)
        assert (await env.client.get("/api/browser/health")).json()["available"] is True
    assert set(asyncio.all_tasks()) == before
    assert env.svc.browser._pw is None
    assert env.svc.browser._sessions == {}


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("preinstalled", [False, True])
async def test_launch_uses_the_measured_path(tmp_path, installed_metadata, monkeypatch, persistent, preinstalled):
    binary = _binary(tmp_path / "owner-chromium" if preinstalled else
                     Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium-1234" / "chrome-linux64" / "chrome")
    mgr = BrowserManager(tmp_path / "session-data")
    if preinstalled:
        monkeypatch.setattr(feature, "CHROMIUM", str(binary))
        feature._patch_executable(mgr)
    calls = []
    page = types.SimpleNamespace()
    context = types.SimpleNamespace(pages=[page])

    async def new_page():
        return page

    context.new_page = new_page

    async def new_context(**kwargs):
        return context

    async def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return context if persistent else types.SimpleNamespace(new_context=new_context)

    mgr._pw = types.SimpleNamespace(chromium=types.SimpleNamespace(
        launch=launch, launch_persistent_context=launch))

    async def status(session_id):
        return {"id": session_id}

    monkeypatch.setattr(mgr, "status", status)
    policy = BrowserPolicy.from_dict({"persistent_profile": persistent})
    assert mgr.executable_path() == str(binary)
    assert await mgr.start(7, policy) == {"id": 7}
    assert len(calls) == 1 and calls[0][1]["executable_path"] == str(binary)
    assert calls[0][1]["headless"] is True


async def test_headless_only_install_does_not_claim_visible_browser(tmp_path, installed_metadata):
    binary = _binary(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium_headless_shell-1234" /
                     "chrome-headless-shell-linux64" / "chrome-headless-shell")
    mgr = BrowserManager(tmp_path / "session-data")
    assert mgr.available is True
    assert mgr.executable_path() == str(binary)
    assert mgr.executable_path(headless=False) is None
    with pytest.raises(BrowserUnavailable, match="Chromium"):
        await mgr.start(8, BrowserPolicy.from_dict({}), headless=False)
    assert mgr._pw is None and mgr._sessions == {}


def test_collection_probe_never_starts_a_driver(installed_metadata, monkeypatch):
    from . import browser_support
    started = []
    sync_api = types.ModuleType("playwright.sync_api")
    def forbidden_driver():
        started.append(True)
        raise AssertionError("collection started a browser driver")
    sync_api.sync_playwright = forbidden_driver
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(browser_support, "PREINSTALLED", Path(feature.CHROMIUM))
    assert browser_support.chromium_available() is False
    assert started == []
    _binary(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium-1234" / "chrome-linux64" / "chrome")
    assert browser_support.chromium_available() is True
    assert started == []


def test_shared_ui_launch_uses_the_probed_executable(installed_metadata, monkeypatch):
    from . import browser_support
    from .test_ux2_thinking_pane import _launch
    binary = _binary(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium-1234" / "chrome-linux64" / "chrome")
    monkeypatch.setattr(browser_support, "PREINSTALLED", Path(feature.CHROMIUM))
    calls = []
    def launch(**kwargs):
        calls.append(kwargs)
        return "launched"
    assert _launch(types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launch))) == "launched"
    assert calls == [{"executable_path": str(binary)}]
