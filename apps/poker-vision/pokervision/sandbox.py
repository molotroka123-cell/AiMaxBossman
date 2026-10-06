"""SandboxDesk: a fake desktop (a Chromium page) that hosts the owner's OWN trainer in a movable, resizable, coverable, closable "window".

It is a TEST DOUBLE for operating-system window behaviour (move, resize, overlap, minimise, close, reopen, DPI), so the capture ->
identity guard -> coordinate mapping -> pointer click -> verification chain can be exercised and measured in a container without a
desktop. It proves the pipeline logic, NOT Windows behaviour. Loopback only. Playwright objects are thread-bound: create and use
this class only inside the service loop thread (other threads submit commands through ``SandboxSource.submit``)."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from .adapters.base import Frame
from .control.geometry import Rect
from .control.identity import WindowIdentity, WindowState
from .sources import CHROME_CANDIDATES, LOOPBACK_HOSTS, assert_loopback

HOST_HTML = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#1d2733;overflow:hidden;width:100vw;height:100vh">
<div id=desk style="position:absolute;inset:0"></div>
<script>
window.mkWin=(id,x,y,w,h,z,src,color)=>{const d=document.createElement('div');d.id='w_'+id;
 d.style.cssText=`position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;overflow:hidden;z-index:${z};background:${color||'#000'}`;
 if(src){const f=document.createElement('iframe');f.src=src;f.style.cssText='border:0;width:100%;height:100%;display:block';d.appendChild(f)}
 document.getElementById('desk').appendChild(d)};
window.setWin=(id,x,y,w,h)=>{const d=document.getElementById('w_'+id);if(d){d.style.left=x+'px';d.style.top=y+'px';d.style.width=w+'px';d.style.height=h+'px'}};
window.showWin=(id,on)=>{const d=document.getElementById('w_'+id);if(d)d.style.display=on?'block':'none'};
window.rmWin=(id)=>{const d=document.getElementById('w_'+id);if(d)d.remove()};
</script></body>"""


class SandboxDesk:
    HOST_PATH = "/__sandbox_desk"

    def __init__(self, url: str, bootstrap: str | None = "cash_nl10", screen=(1280, 900), win=(520, 900), dpr: float = 1.0, chrome: str | None = None):
        self.url = assert_loopback(url)
        self.dpr, self.screen = dpr, screen
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = chrome or next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
        self.browser = self._pw.chromium.launch(executable_path=exe) if exe else self._pw.chromium.launch()
        self.ctx = self.browser.new_context(viewport={"width": screen[0], "height": screen[1]}, device_scale_factor=dpr)
        origin = f"{urlparse(self.url).scheme}://{urlparse(self.url).netloc}"
        self.host_url = origin + self.HOST_PATH

        def route(r):
            u = r.request.url
            if u.startswith(self.host_url):
                return r.fulfill(status=200, content_type="text/html", body=HOST_HTML)
            if urlparse(u).hostname in LOOPBACK_HOSTS or u.startswith(("data:", "blob:")):
                return r.continue_()
            return r.abort()
        self.ctx.route("**/*", route)
        self.page = self.ctx.new_page()
        self.page.goto(self.host_url)
        self.t0 = time.monotonic()
        self.windows: dict[str, dict] = {}
        self._pid = 41000
        self._z = 10
        self.bootstrap = bootstrap
        self.open_trainer("w1", Rect(40, 0, *win))
        self.n = 0

    # ------------------------------------------------------------ "OS" operations
    def _new_pid(self) -> int:
        self._pid += 1
        return self._pid

    def open_trainer(self, wid: str, rect: Rect, bootstrap: bool = True) -> None:
        self._z += 1
        self.page.evaluate("([id,x,y,w,h,z,src])=>mkWin(id,x,y,w,h,z,src)", [wid, rect.x, rect.y, rect.w, rect.h, self._z, self.url])
        self.windows[wid] = {"kind": "app", "rect": rect, "pid": self._new_pid(), "started": time.time(), "minimized": False, "closed": False, "title": "Poker Train"}
        self.page.wait_for_timeout(500)
        if self.bootstrap and bootstrap:
            self._bootstrap(wid)

    def _frame(self, wid: str):
        sel = self.page.frame_locator(f"#w_{wid} iframe")
        for f in self.page.frames:
            if f != self.page.main_frame and f.parent_frame == self.page.main_frame:
                el = f.frame_element()
                if el.get_attribute("src") is not None and el.evaluate("e => e.parentElement.id") == f"w_{wid}":
                    return f
        raise RuntimeError(f"no frame for window {wid}")

    def _bootstrap(self, wid: str) -> None:
        stakes = {"cash_nl2": "NL2 (1c/2c)", "cash_nl5": "NL5 (2c/5c)", "cash_nl10": "NL10 (5c/10c)", "cash_nl25": "NL25 (10c/25c)"}
        f = self._frame(wid)
        f.wait_for_timeout(400)
        f.click("text=+"); f.fill("input", "VisionTest"); self.page.keyboard.press("Enter"); f.wait_for_timeout(300)
        f.click("text=VisionTest"); f.wait_for_timeout(500)
        f.click(f"text={stakes[self.bootstrap]}"); f.wait_for_timeout(1500)

    def move(self, wid, x, y):
        w = self.windows[wid]; r = w["rect"]; w["rect"] = Rect(x, y, r.w, r.h)
        self.page.evaluate("([id,x,y,w,h])=>setWin(id,x,y,w,h)", [wid, x, y, r.w, r.h])

    def resize(self, wid, w_, h_):
        w = self.windows[wid]; r = w["rect"]; w["rect"] = Rect(r.x, r.y, w_, h_)
        self.page.evaluate("([id,x,y,w,h])=>setWin(id,x,y,w,h)", [wid, r.x, r.y, w_, h_])

    def minimize(self, wid, on=True):
        self.windows[wid]["minimized"] = on
        self.page.evaluate("([id,on])=>showWin(id,!on)", [wid, on])

    def cover(self, cid, rect: Rect):
        self._z += 5
        self.page.evaluate("([id,x,y,w,h,z])=>mkWin(id,x,y,w,h,z,null,'#c0392b')", [cid, rect.x, rect.y, rect.w, rect.h, self._z])
        self.windows[cid] = {"kind": "cover", "rect": rect, "pid": self._new_pid(), "started": time.time(), "minimized": False, "closed": False, "title": "Other app"}

    def uncover(self, cid):
        self.page.evaluate("(id)=>rmWin(id)", cid); self.windows.pop(cid, None)

    def close(self, wid):
        self.page.evaluate("(id)=>rmWin(id)", wid)
        self.windows[wid]["closed"] = True

    def reopen(self, wid, rect: Rect | None = None):
        r = rect or self.windows[wid]["rect"]
        self.open_trainer(wid, r, bootstrap=False)     # a re-launched app starts at its lobby, as a new process

    def deal(self, wid="w1") -> bool:
        """TEST-HARNESS assist, not part of the executor: starts the next hand in the owner's trainer through its DOM."""
        try:
            f = self._frame(wid)
            b = [x for x in f.query_selector_all("button") if x.inner_text().strip() == "DEAL"]
            if len(b) == 1:
                b[0].click(); return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------ capture / pointer / identity
    def identity(self, wid="w1") -> WindowIdentity:
        w = self.windows[wid]
        return WindowIdentity("virtual", int(wid[1:] or 0) if wid[1:].isdigit() else abs(hash(wid)) % 10_000, w["pid"], "poker-train(sandbox)", w["started"], w["title"])

    def rect_phys(self, wid="w1") -> Rect:
        r = self.windows[wid]["rect"]
        return Rect(r.x * self.dpr, r.y * self.dpr, r.w * self.dpr, r.h * self.dpr)

    def grab(self, wid="w1") -> tuple[np.ndarray, Rect]:
        """Region capture (like mss): whatever is painted in the window's rectangle, including windows on top of it."""
        rect = self.rect_phys(wid)
        r = self.windows[wid]["rect"]
        png = self.page.screenshot(clip={"x": r.x, "y": r.y, "width": r.w, "height": r.h})
        return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR), rect

    def click_phys(self, px: float, py: float) -> None:
        self.page.mouse.click(px / self.dpr, py / self.dpr)

    def element_at(self, px: float, py: float, wid: str = "w1") -> str | None:
        """Ground truth for the bench ONLY: label of the trainer button under a physical screen point (None: not a button / other window)."""
        if self._topmost(px, py) != wid:
            return "<other window>"
        r = self.windows[wid]["rect"]
        try:
            return self._frame(wid).evaluate("([x,y])=>{const e=document.elementFromPoint(x,y);const b=e&&e.closest('button');return b?b.innerText.trim().split('\\n')[0]:null}",
                                              [px / self.dpr - r.x, py / self.dpr - r.y])
        except Exception:
            return None

    def dom_button_box(self, label: str, wid: str = "w1") -> Rect | None:
        """DOM-oracle locator input for the bench ONLY: the button box in frame (physical) pixels relative to the window."""
        f = self._frame(wid)
        r = f.evaluate("(l)=>{const b=[...document.querySelectorAll('button')].filter(x=>x.innerText.trim().split('\\n')[0]===l);if(b.length!==1)return null;const q=b[0].getBoundingClientRect();return [q.x,q.y,q.width,q.height]}", label)
        return None if r is None else Rect(r[0] * self.dpr, r[1] * self.dpr, r[2] * self.dpr, r[3] * self.dpr)

    def dom_click(self, label: str, wid: str = "w1") -> bool:
        """Harness-only: the owner reviewing a halt and unblocking the trainer by hand (e.g. closing a half-open raise panel with FOLD)."""
        try:
            b = [x for x in self._frame(wid).query_selector_all("button") if x.inner_text().strip().split("\n")[0] == label]
            if len(b) == 1:
                b[0].click(); return True
        except Exception:
            pass
        return False

    def cpu_throttle(self, rate: float) -> None:
        self.ctx.new_cdp_session(self.page).send("Emulation.setCPUThrottlingRate", {"rate": rate})

    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def alive(self) -> bool:
        try:
            return bool(self.page.evaluate("() => performance.now() > 0"))
        except Exception:
            return False

    def shutdown(self) -> None:
        for c in (self.ctx, self.browser):
            try: c.close()
            except Exception: pass
        try: self._pw.stop()
        except Exception: pass

    # WindowProbe
    def snapshot(self, ident: WindowIdentity) -> WindowState:
        for wid, w in self.windows.items():
            if self.identity(wid).handle == ident.handle and w["kind"] == "app":
                if w["closed"]:
                    return WindowState(None)
                cur = self.identity(wid)
                rect = self.rect_phys(wid)
                occ = self._occluded(wid, rect)
                return WindowState(cur, rect, 96.0 * self.dpr, w["minimized"], not w["minimized"], occ, self.now_ms())
        return WindowState(None)

    def _topmost(self, sx: float, sy: float) -> str | None:
        best, bz = None, -1
        order = list(self.windows.items())
        for i, (wid, w) in enumerate(order):
            if w["closed"] or w["minimized"]:
                continue
            r = w["rect"]
            if r.contains(sx / self.dpr, sy / self.dpr) and (i + (1000 if w["kind"] == "cover" else 0)) >= bz:
                best, bz = wid, i + (1000 if w["kind"] == "cover" else 0)
        return best

    def owner_at(self, sx: float, sy: float) -> int | None:
        wid = self._topmost(sx, sy)
        return None if wid is None else self.identity(wid).handle

    def _occluded(self, wid: str, rect: Rect) -> float:
        n = bad = 0
        for fx in (0.1, 0.3, 0.5, 0.7, 0.9):
            for fy in (0.1, 0.3, 0.5, 0.7, 0.9):
                n += 1
                top = self._topmost(rect.x + fx * rect.w, rect.y + fy * rect.h)
                if top is not None and top != wid:
                    bad += 1
        return bad / n


class SourceLost(RuntimeError):
    pass


class SandboxSource:
    """Service source over a SandboxDesk window. Commands from other threads are queued and applied by the loop thread."""
    kind = "live"
    sandbox = True

    def __init__(self, url: str, bootstrap: str | None = "cash_nl10", dpr: float = 1.0, screen=(1280, 900), win=(520, 900), wid: str = "w1"):
        self.desk = SandboxDesk(url, bootstrap, screen, win, dpr)
        self.wid = wid
        self.ident = self.desk.identity(wid)
        self.probe = self.desk
        self.q: queue.Queue = queue.Queue()
        self.n = 0
        self.pointer_scale = 1.0 / dpr

    def submit(self, cmd: str, **kw) -> dict:
        ev = threading.Event(); box: dict = {}
        self.q.put((cmd, kw, ev, box))
        if not ev.wait(15):
            return {"ok": False, "error": "sandbox command timed out"}
        return box

    def pump(self) -> None:
        while True:
            try:
                cmd, kw, ev, box = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                fn = getattr(self.desk, cmd)
                if cmd in ("move", "resize", "minimize", "close", "reopen", "deal"):
                    kw = {"wid": self.wid, **kw}
                if cmd == "cover":
                    kw["rect"] = Rect(*kw["rect"])
                if cmd == "reopen" and "rect" in kw and kw["rect"] is not None:
                    kw["rect"] = Rect(*kw["rect"])
                box["result"] = fn(**kw); box["ok"] = True
            except Exception as exc:  # noqa: BLE001
                box["ok"] = False; box["error"] = f"{type(exc).__name__}: {exc}"
            ev.set()

    def now_ms(self) -> int: return self.desk.now_ms()
    def alive(self) -> bool: return self.desk.alive()

    def read(self) -> Frame | None:
        self.pump()
        st = self.desk.windows.get(self.wid)
        if st is None or st["closed"]:
            raise SourceLost("window closed")
        if self.desk.identity(self.wid).key != self.ident.key:
            raise SourceLost("the window was reopened as a different process: not the source the owner picked")
        if st["minimized"]:
            raise SourceLost("window minimised")
        bgr, rect = self.desk.grab(self.wid)
        self.n += 1
        return Frame(bgr, self.desk.now_ms(), "live:sandbox-window", f"sandbox#{self.n}", meta={"rect": [rect.x, rect.y, rect.w, rect.h]})

    def click_phys(self, px, py): self.desk.click_phys(px, py)
    def close(self): self.desk.shutdown()
