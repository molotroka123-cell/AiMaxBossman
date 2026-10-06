"""Frame -> screen coordinates with DPI, preview scale and window position. Pure functions, unit-tested; no platform calls."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float: return self.x + self.w / 2
    @property
    def cy(self) -> float: return self.y + self.h / 2
    @property
    def r(self) -> float: return self.x + self.w
    @property
    def b(self) -> float: return self.y + self.h

    def contains(self, px: float, py: float, margin: float = 0.0) -> bool:
        return self.x + margin <= px <= self.r - margin and self.y + margin <= py <= self.b - margin

    def inter_area(self, o: "Rect") -> float:
        w = min(self.r, o.r) - max(self.x, o.x); h = min(self.b, o.b) - max(self.y, o.y)
        return max(w, 0.0) * max(h, 0.0)

    def iou(self, o: "Rect") -> float:
        i = self.inter_area(o); u = self.w * self.h + o.w * o.h - i
        return i / u if u > 0 else 0.0


class GeometryError(ValueError):
    pass


@dataclass(frozen=True)
class ScreenMapper:
    """``window`` is the captured window's rectangle in PHYSICAL screen pixels, read at the same moment the frame was captured.
    ``frame_w/h`` are the pixels of the captured frame. ``pointer_scale`` converts physical pixels to the units of the pointer API
    (1.0 for a DPI-aware pointer; 1/dpi for a DPI-unaware one)."""
    frame_w: int
    frame_h: int
    window: Rect
    pointer_scale: float = 1.0
    max_aspect_error: float = 0.02

    def __post_init__(self):
        if self.frame_w <= 0 or self.frame_h <= 0 or self.window.w <= 0 or self.window.h <= 0:
            raise GeometryError("empty frame or window")
        a_f, a_w = self.frame_w / self.frame_h, self.window.w / self.window.h
        if abs(a_f / a_w - 1) > self.max_aspect_error:
            raise GeometryError(f"frame {self.frame_w}x{self.frame_h} does not match window {self.window.w:.0f}x{self.window.h:.0f}: stale frame or resized window")

    @property
    def scale(self) -> float:
        return self.window.w / self.frame_w

    def frame_to_screen(self, fx: float, fy: float) -> tuple[float, float]:
        return self.window.x + fx * self.window.w / self.frame_w, self.window.y + fy * self.window.h / self.frame_h

    def screen_to_pointer(self, sx: float, sy: float) -> tuple[float, float]:
        return sx * self.pointer_scale, sy * self.pointer_scale

    def target_point(self, box: Rect) -> tuple[float, float]:
        """Centre of the box, in pointer units; refuses a box that is not inside the frame/window."""
        if box.w <= 0 or box.h <= 0 or box.x < 0 or box.y < 0 or box.r > self.frame_w + 1 or box.b > self.frame_h + 1:
            raise GeometryError("target box outside the frame")
        sx, sy = self.frame_to_screen(box.cx, box.cy)
        if not self.window.contains(sx, sy):
            raise GeometryError("mapped point outside the window")
        return self.screen_to_pointer(sx, sy)


def preview_to_frame(px: float, py: float, preview_w: float, preview_h: float, frame_w: int, frame_h: int) -> tuple[float, float]:
    """A point on the Bossman preview (CSS px of the <img>/<canvas>) -> frame pixels. Refuses points outside the preview."""
    if not (0 <= px <= preview_w and 0 <= py <= preview_h) or preview_w <= 0 or preview_h <= 0:
        raise GeometryError("point outside the preview")
    return px * frame_w / preview_w, py * frame_h / preview_h
