"""Locate the browser we will launch, without starting a Playwright driver.

Installation is a prerequisite, not evidence that Chromium can start or browse.
Readiness stays UNKNOWN until a live context has actually been observed.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import sys
from pathlib import Path

PREINSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"
INSTALL_HINT = (
    "Playwright или исполняемый файл Chromium недоступен. "
    "В окружении BCC выполните `python -m pip install playwright`, затем "
    "`python -m playwright install chromium`. Проверьте PLAYWRIGHT_BROWSERS_PATH."
)


def _executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _registry_root(package: Path) -> Path | None:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override == "0":
        return package / ".local-browsers"
    if override:
        root = Path(override)
    elif sys.platform == "linux":
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        return None
    # Playwright resolves relative cache paths against INIT_CWD, when supplied.
    return root if root.is_absolute() else Path(os.environ.get("INIT_CWD") or Path.cwd()) / root


def _relative_executables(headless_shell: bool) -> tuple[str, ...]:
    arm = platform.machine().lower() in {"arm64", "aarch64"}
    if sys.platform == "linux":
        if headless_shell:
            return ("chrome-linux/headless_shell",) if arm else (
                "chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux/headless_shell")
        return ("chrome-linux/chrome",) if arm else ("chrome-linux64/chrome", "chrome-linux/chrome")
    if sys.platform == "win32":
        return (("chrome-headless-shell-win64/chrome-headless-shell.exe", "chrome-win/headless_shell.exe")
                if headless_shell else ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"))
    if sys.platform == "darwin":
        arch = "arm64" if arm else "x64"
        return ((f"chrome-headless-shell-mac-{arch}/chrome-headless-shell", "chrome-mac/headless_shell")
                if headless_shell else (
                    f"chrome-mac-{arch}/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                    "chrome-mac/Chromium.app/Contents/MacOS/Chromium"))
    return ()


def chromium_executable(*, preinstalled: str = PREINSTALLED_CHROMIUM,
                        headless: bool = True) -> str | None:
    """Return one measured, compatible-layout path; no stale-cache globbing.

    Both ephemeral and persistent sessions pass this exact path to Playwright.
    Metadata and file existence are re-read, so install/removal is visible on the
    next poll. There are no background tasks, subprocesses or growing caches.
    """
    try:
        # Import the real adapter, not only the top-level package/spec: missing
        # optional Python dependencies must not count as an installed runtime.
        importlib.import_module("playwright.async_api")
        playwright = importlib.import_module("playwright")
        if _executable(Path(preinstalled)):
            return str(Path(preinstalled).absolute())
        package = Path(playwright.__file__).resolve().parent / "driver" / "package"
        root = _registry_root(package)
        if root is None:
            return None
        with (package / "browsers.json").open(encoding="utf-8") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            return None
        descriptors = json.loads(raw)["browsers"]
        if not isinstance(descriptors, list) or len(descriptors) > 64:
            return None
        names = ("chromium-headless-shell", "chromium") if headless else ("chromium",)
        for name in names:
            row = next((item for item in descriptors if isinstance(item, dict) and item.get("name") == name), None)
            revision = row.get("revision") if row else None
            if not isinstance(revision, str) or not revision.isascii() or not revision.isdigit() or len(revision) > 20:
                continue
            directory = root / f"{name.replace('-', '_')}-{revision}"
            for relative in _relative_executables(name == "chromium-headless-shell"):
                path = directory / relative
                if _executable(path):
                    return str(path.absolute())
    except (ImportError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return None
