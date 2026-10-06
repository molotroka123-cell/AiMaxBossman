"""Window identity = handle + process id + process start time (+ title for humans), never the title alone.
A guard refuses control on any change that could mean "this is no longer the window the owner picked"; it never switches to a lookalike."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Protocol

from .geometry import Rect


@dataclass(frozen=True)
class WindowIdentity:
    kind: str                     # window | screen | virtual (sandbox/test surface)
    handle: int
    pid: int
    process: str
    started_at: float             # process creation time (epoch s); a reopened app gets a new value
    title: str = ""
    @property
    def key(self) -> tuple: return (self.kind, self.handle, self.pid, round(self.started_at, 2))
    def to_dict(self) -> dict:
        return {"kind": self.kind, "handle": self.handle, "pid": self.pid, "process": self.process, "started_at": self.started_at, "title": self.title}


@dataclass
class WindowState:
    identity: WindowIdentity | None        # who owns this handle NOW (None: handle no longer exists)
    rect: Rect | None = None               # physical screen pixels
    dpi: float = 96.0
    minimized: bool = False
    visible: bool = True
    occluded_frac: float = 0.0             # share of the window covered by other windows (0..1)
    t_ms: int = 0


class WindowProbe(Protocol):
    def snapshot(self, ident: WindowIdentity) -> WindowState: ...
    def owner_at(self, sx: float, sy: float) -> int | None: ...      # handle of the topmost window at a physical screen point


@dataclass
class GuardConfig:
    max_frame_age_ms: int = 1200
    max_occluded: float = 0.02
    size_tol: float = 0.01                 # relative size change vs the verified size
    max_title_free_changes: int = 0


class IdentityGuard:
    def __init__(self, bound: WindowIdentity, verified_size: tuple[float, float] | None = None, cfg: GuardConfig | None = None):
        self.bound, self.cfg = bound, cfg or GuardConfig()
        self.verified_size = verified_size            # window size the profile was last verified at (None: not yet)

    def reasons(self, snap: WindowState, frame_age_ms: float | None) -> list[str]:
        out: list[str] = []
        if snap.identity is None:
            return ["SOURCE_CLOSED"]
        if snap.identity.key != self.bound.key:
            out.append("SOURCE_REOPENED_OR_REPLACED")      # same handle, other process / new start time: a different window
        if snap.minimized or not snap.visible:
            out.append("SOURCE_MINIMIZED_OR_HIDDEN")
        if snap.rect is None or snap.rect.w <= 0 or snap.rect.h <= 0:
            out.append("SOURCE_NO_RECT")
        if snap.occluded_frac > self.cfg.max_occluded:
            out.append(f"SOURCE_OCCLUDED ({snap.occluded_frac:.0%})")
        if frame_age_ms is None or frame_age_ms > self.cfg.max_frame_age_ms:
            out.append("FRAME_STALE")
        if snap.rect is not None and self.verified_size is not None:
            vw, vh = self.verified_size
            if abs(snap.rect.w / vw - 1) > self.cfg.size_tol or abs(snap.rect.h / vh - 1) > self.cfg.size_tol:
                out.append("SOURCE_RESIZED_REVERIFY_PROFILE")
        return out

    def rebind_size(self, size: tuple[float, float]) -> None:
        """Called only after the profile was re-verified at the new size."""
        self.verified_size = size


@dataclass
class StaticProbe:
    """Test double: states are set by the test."""
    state: WindowState
    top_handle: int | None = None
    def snapshot(self, ident): return self.state
    def owner_at(self, sx, sy):
        if self.top_handle is not None:
            return self.top_handle
        return self.state.identity.handle if self.state.identity and self.state.rect and self.state.rect.contains(sx, sy) else None


class WindowsProbe:
    """Win32 (ctypes) probe. NOT_RUN in the Linux sandbox: reviewed against the Win32 API, to be run on the owner's Windows PC."""
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WindowsProbe works only on Windows")
        import ctypes
        from ctypes import wintypes
        self.c, self.w = ctypes, wintypes
        self.u = ctypes.windll.user32; self.k = ctypes.windll.kernel32

    def _proc(self, pid: int) -> tuple[str, float]:
        c, w = self.c, self.w
        h = self.k.OpenProcess(0x1000, False, pid)                   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "", 0.0
        try:
            buf = c.create_unicode_buffer(1024); n = w.DWORD(1024)
            name = buf.value if self.k.QueryFullProcessImageNameW(h, 0, buf, c.byref(n)) else ""
            ft = [w.FILETIME() for _ in range(4)]
            self.k.GetProcessTimes(h, *[c.byref(f) for f in ft])
            t = ((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime) / 1e7 - 11644473600
            return name.rsplit("\\", 1)[-1].lower(), t
        finally:
            self.k.CloseHandle(h)

    def identity_of(self, hwnd: int, kind: str = "window") -> WindowIdentity | None:
        c, w = self.c, self.w
        if not self.u.IsWindow(hwnd):
            return None
        pid = w.DWORD(); self.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        name, t = self._proc(pid.value)
        n = self.u.GetWindowTextLengthW(hwnd); buf = c.create_unicode_buffer(n + 1); self.u.GetWindowTextW(hwnd, buf, n + 1)
        return WindowIdentity(kind, int(hwnd), int(pid.value), name, t, buf.value)

    def snapshot(self, ident: WindowIdentity) -> WindowState:
        c, w = self.c, self.w
        now = self.identity_of(ident.handle, ident.kind)
        if now is None:
            return WindowState(None)
        r = w.RECT()
        self.u.GetWindowRect(ident.handle, c.byref(r))                # DPI-aware process required: manifest of the backend sets it
        rect = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
        dpi = float(self.u.GetDpiForWindow(ident.handle)) if hasattr(self.u, "GetDpiForWindow") else 96.0
        occ = self._occluded(ident.handle, rect)
        return WindowState(now, rect, dpi, bool(self.u.IsIconic(ident.handle)), bool(self.u.IsWindowVisible(ident.handle)), occ)

    def _occluded(self, hwnd: int, rect: Rect) -> float:
        n = bad = 0
        for fx in (0.1, 0.5, 0.9):
            for fy in (0.1, 0.5, 0.9):
                n += 1
                top = self.owner_at(rect.x + fx * rect.w, rect.y + fy * rect.h)
                if top is not None and top != hwnd and self.u.GetAncestor(top, 2) != hwnd:   # GA_ROOT=2
                    bad += 1
        return bad / n

    def owner_at(self, sx: float, sy: float) -> int | None:
        pt = self.w.POINT(int(sx), int(sy))
        h = self.u.WindowFromPoint(pt)
        return int(self.u.GetAncestor(h, 2)) if h else None
