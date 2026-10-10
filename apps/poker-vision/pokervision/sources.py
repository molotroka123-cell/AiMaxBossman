"""Frame sources. Replay is always allowed; the browser source is loopback-only; screen capture observes an owner-selected
window and can never be combined with acting. mss / Playwright are optional dependencies imported lazily."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import cv2

from .adapters.base import Frame

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CHROME_CANDIDATES = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"]
# The trainer formats numbers with the browser locale: a ru-RU Chromium (the owner's PC) renders "1 000", en-US "1,000".
# Profiles and the DOM truth are calibrated on en-US formatting, so every browser context we open pins it.
CONTEXT_LOCALE = "en-US"


class NotLoopback(ValueError):
    pass


def assert_loopback(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK_HOSTS or u.username or u.password:
        raise NotLoopback(f"{url!r} is not a loopback URL: acting/automation is limited to the owner's own trainer")
    return url


class DirSource:
    """Recorded frames: <dir>/*.png (+ optional labels.jsonl giving t_ms). Never stale by construction (replay)."""
    kind = "replay"

    def __init__(self, path: str | Path, interval_ms: int = 250):
        self.dir = Path(path)
        self.files = sorted(p for p in self.dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        self.t = {}
        lab = self.dir / "labels.jsonl"
        if lab.exists():
            for line in lab.read_text(encoding="utf-8").splitlines():
                r = json.loads(line); self.t[r["frame"]] = r["t_ms"]
        self.i = 0
        self.interval = interval_ms

    def __len__(self) -> int:
        return len(self.files)

    def read(self) -> Frame | None:
        if self.i >= len(self.files):
            return None
        p = self.files[self.i]; self.i += 1
        img = cv2.imread(str(p))
        return Frame(img, self.t.get(p.name, (self.i - 1) * self.interval), f"replay:{self.dir.name}", f"{self.dir.name}/{p.name}")

    def close(self) -> None:
        pass


class VideoSource:
    kind = "replay"

    def __init__(self, path: str | Path):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise FileNotFoundError(path)
        self.name = Path(path).name

    def read(self) -> Frame | None:
        ok, img = self.cap.read()
        if not ok:
            return None
        t = int(self.cap.get(cv2.CAP_PROP_POS_MSEC))
        return Frame(img, t, f"replay:{self.name}", f"{self.name}@{t}")

    def close(self) -> None:
        self.cap.release()


class LoopbackBrowserSource:
    """Opens the owner's own trainer (loopback only) with Playwright and screenshots it. Also exposes the page to the actuator."""
    kind = "live"

    def __init__(self, url: str, viewport=(520, 900), dpr: float = 1.0, chrome: str | None = None, headless: bool = True, bootstrap: str | None = None):
        self.url = assert_loopback(url)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = chrome or next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
        self.browser = self._pw.chromium.launch(executable_path=exe, headless=headless) if exe else self._pw.chromium.launch(headless=headless)
        self.ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=dpr, locale=CONTEXT_LOCALE)
        self.ctx.route("**/*", lambda route: route.continue_() if (urlparse(route.request.url).hostname in LOOPBACK_HOSTS or route.request.url.startswith(("data:", "blob:"))) else route.abort())
        self.page = self.ctx.new_page()
        self.page.goto(self.url)
        if bootstrap:
            self._bootstrap(bootstrap)
        self.t0 = time.monotonic()
        self.n = 0

    def _bootstrap(self, what: str) -> None:
        """Walk the owner's own trainer to a cash table (creates a throw-away profile in the trainer's own localStorage)."""
        stakes = {"cash_nl2": "NL2 (1c/2c)", "cash_nl5": "NL5 (2c/5c)", "cash_nl10": "NL10 (5c/10c)", "cash_nl25": "NL25 (10c/25c)"}
        if what not in stakes:
            raise ValueError(f"unknown bootstrap {what!r}")
        pg = self.page
        pg.wait_for_timeout(400)
        pg.click("text=+"); pg.fill("input", "VisionTest"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
        pg.click("text=VisionTest"); pg.wait_for_timeout(500)
        pg.click(f"text={stakes[what]}"); pg.wait_for_timeout(1500)

    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def alive(self) -> bool:
        """Liveness probe of the page itself: an idle UI waiting for the hero is NOT a frozen capture."""
        try:
            return bool(self.page.evaluate("() => performance.now() > 0"))
        except Exception:
            return False

    def read(self) -> Frame | None:
        import numpy as np
        png = self.page.screenshot()
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        self.n += 1
        return Frame(img, int((time.monotonic() - self.t0) * 1000), "live:trainer", f"trainer#{self.n}")

    def close(self) -> None:
        for c in (self.ctx, self.browser):
            try: c.close()
            except Exception: pass
        try: self._pw.stop()
        except Exception: pass


def list_windows() -> list[dict]:
    """Top-level visible windows (Windows only). Elsewhere returns [] with the reason in ``window_support``."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1); user32.GetWindowTextW(hwnd, buf, n + 1)
                r = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 100 and r.bottom - r.top > 100:
                    out.append({"hwnd": int(hwnd), "title": buf.value, "rect": [r.left, r.top, r.right - r.left, r.bottom - r.top]})
        return True
    user32.EnumWindows(cb, 0)
    return out


def window_support() -> dict:
    return {"platform": sys.platform, "enumeration": sys.platform == "win32",
            "note": "window enumeration and mss capture are implemented for Windows/desktop sessions; NOT exercised in the Linux build container"}


class ScreenWindowSource:
    """Observe ONE owner-selected window rect via mss. Observation only; the service refuses act=True for this source."""
    kind = "live"
    observe_only = True

    def __init__(self, rect: list[int], label: str = "window"):
        import mss
        self.sct = mss.mss()
        self.rect = {"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}
        self.t0 = time.monotonic(); self.n = 0; self.label = label

    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def read(self) -> Frame | None:
        import numpy as np
        shot = self.sct.grab(self.rect)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        self.n += 1
        return Frame(img, int((time.monotonic() - self.t0) * 1000), f"live:window:{self.label}", f"{self.label}#{self.n}")

    def close(self) -> None:
        self.sct.close()
