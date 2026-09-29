"""Start/kill/restart one Command Center backend from THIS checkout on an isolated data dir."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

REPO = Path(__file__).resolve().parents[2]
OWNER_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"


class Backend:
    def __init__(self, port: int, data_dir: Path, log_dir: Path, *, extra_env: dict | None = None):
        data_dir = Path(data_dir).resolve()
        if OWNER_ROOT.exists() and (data_dir == OWNER_ROOT.resolve()
                                    or OWNER_ROOT.resolve() in data_dir.parents):
            raise SystemExit(f"refusing the owner data root: {data_dir}")
        self.port = port
        self.data_dir = data_dir
        self.log_dir = Path(log_dir)
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.starts = 0
        self.extra_env = dict(extra_env or {})

    # --------------------------------------------------------------- lifecycle
    def env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "command-center"), str(REPO / "bossman-core"), str(REPO)])
        env["BCC_DATA_DIR"] = str(self.data_dir)
        env["BCC_PORT"] = str(self.port)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(self.extra_env)
        return env

    def start(self, timeout: float = 90.0) -> float:
        """Start and wait for /api/health; returns seconds to ready."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.starts += 1
        log = open(self.log_dir / f"backend-{self.starts:02d}.log", "wb")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "bcc", "--port", str(self.port)],
            cwd=str(self.data_dir), env=self.env(), stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags)
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited rc={self.proc.returncode}; see {log.name}")
            try:
                r = httpx.get(self.base + "/api/health", timeout=2)
                if r.status_code < 500:
                    return time.monotonic() - t0
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        raise RuntimeError("backend did not become ready")

    def kill(self) -> None:
        """Hard kill (what a crash / closed console / power loss looks like)."""
        if self.proc is None:
            return
        try:
            p = psutil.Process(self.proc.pid)
            for child in p.children(recursive=True):
                child.kill()
            p.kill()
        except psutil.NoSuchProcess:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        self.proc = None
        # the port must be free before the next start
        for _ in range(50):
            if not any(c.laddr and c.laddr.port == self.port and c.status == psutil.CONN_LISTEN
                       for c in psutil.net_connections("tcp")):
                break
            time.sleep(0.2)

    def restart(self) -> float:
        self.kill()
        return self.start()

    def rss_mb(self) -> float | None:
        if self.proc is None:
            return None
        try:
            p = psutil.Process(self.proc.pid)
            total = p.memory_info().rss
            for child in p.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except psutil.Error:
                    pass
            return total / 1048576.0
        except psutil.Error:
            return None

    @property
    def token(self) -> str:
        return (self.data_dir / "token").read_text(encoding="utf-8").strip()


class DesktopBackend(Backend):
    """The owner's shortcut: ``python -m bcc.desktop`` = server in-process + an ``--app``
    window of the system Chromium/Edge (``find_browser()``, as for the owner), but with a
    throwaway window profile and a CDP port so the harness can drive THAT window.

    ``kill()`` = the console window closed / the process crashed: the server dies, the
    browser window stays. ``start()`` = the owner double-clicks the shortcut again.
    """

    def __init__(self, port: int, data_dir: Path, log_dir: Path, *, profile: Path, cdp_port: int,
                 browser: str | None = None, extra_env: dict | None = None):
        super().__init__(port, data_dir, log_dir, extra_env=extra_env)
        self.profile = Path(profile)
        self.cdp_port = cdp_port
        self.browser = browser

    def start(self, timeout: float = 120.0) -> float:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.starts += 1
        log = open(self.log_dir / f"desktop-{self.starts:02d}.log", "wb")
        argv = [sys.executable, "-m", "bcc.desktop", "--port", str(self.port), "--profile", str(self.profile),
                f"--browser-arg=--remote-debugging-port={self.cdp_port}",
                "--browser-arg=--disable-background-timer-throttling",
                "--browser-arg=--disable-renderer-backgrounding",
                "--browser-arg=--disable-backgrounding-occluded-windows",
                "--browser-arg=--mute-audio"]            # never make sound on the owner's machine
        if self.browser:
            argv += ["--browser", self.browser]
        flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        t0 = time.monotonic()
        self.proc = subprocess.Popen(argv, cwd=str(self.data_dir), env=self.env(), stdin=subprocess.PIPE,
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        try:  # "нажмите Enter…" pauses must not hang an unattended run
            self.proc.stdin.write(b"\n" * 8)
            self.proc.stdin.flush()
        except OSError:
            pass
        while time.monotonic() - t0 < timeout:
            try:
                if httpx.get(self.base + "/api/health", timeout=2).status_code < 500 and \
                        httpx.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=2).status_code == 200:
                    return time.monotonic() - t0
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None and not self._port_up():
                raise RuntimeError(f"launcher exited rc={self.proc.returncode}; see {log.name}")
            time.sleep(0.4)
        raise RuntimeError("desktop launcher did not become ready")

    def _port_up(self) -> bool:
        try:
            return httpx.get(self.base + "/api/health", timeout=1).status_code < 500
        except httpx.HTTPError:
            return False

    def kill(self) -> None:
        """Only the launcher (python, with the in-process server) — never the window."""
        if self.proc is None:
            return
        try:
            psutil.Process(self.proc.pid).kill()
        except psutil.NoSuchProcess:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        self.proc = None
        for _ in range(50):
            if not self._port_up():
                break
            time.sleep(0.2)

    def rss_mb(self) -> float | None:
        if self.proc is None:
            return None
        try:
            return psutil.Process(self.proc.pid).memory_info().rss / 1048576.0
        except psutil.Error:
            return None

    def window_pids(self) -> list[int]:
        out = []
        for pr in psutil.process_iter(["cmdline"]):
            try:
                if str(self.profile) in " ".join(pr.info.get("cmdline") or []):
                    out.append(pr.pid)
            except psutil.Error:
                pass
        return out

    def click_close(self) -> int:
        """What the owner's click on the window's X does: WM_CLOSE to the visible top-level
        windows of THIS profile's browser only (never any other window)."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        pids = set(self.window_pids())
        hwnds: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(h, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(h):
                hwnds.append(h)
            return True

        user32.EnumWindows(each, 0)
        for h in hwnds:
            user32.PostMessageW(h, 0x0010, 0, 0)          # WM_CLOSE
        return len(hwnds)

    def close_windows(self) -> None:
        for pid in self.window_pids():
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
