"""Titles never go over a face: pure geometry guard (no OpenCV, no GPU).

Owner defect (v1 promos): titles over the face. The convention «titles live in the lower third» existed only in a
docstring; this module makes it a check. Rectangles are (x0, y0, x1, y1) in canvas pixels; faces come from any
detector (the project's gate uses yolo_face / FaceFusion boxes).

  overlaps(rect, face, margin)        True when the rect touches the face box grown by `margin`
  place_title(W, H, w, h, faces)      a rect of size w x h that clears every face, lower third preferred; None if impossible
  check_titles(titles, faces_at, step) frames-in-time violations of already placed titles (for a built timeline)
"""
from __future__ import annotations

from typing import Callable, Iterable, Sequence

Rect = tuple[float, float, float, float]

DEFAULT_MARGIN = 24


def overlaps(rect: Rect, face: Rect, margin: float = 0) -> bool:
    ax0, ay0, ax1, ay1 = rect
    bx0, by0, bx1, by1 = face
    return not (ax1 <= bx0 - margin or ax0 >= bx1 + margin or ay1 <= by0 - margin or ay0 >= by1 + margin)


def _clear(rect: Rect, faces: Iterable[Rect], margin: float) -> bool:
    return not any(overlaps(rect, f, margin) for f in faces)


def place_title(width: int, height: int, title_w: int, title_h: int, faces: Sequence[Rect] = (),
                margin: float = DEFAULT_MARGIN, edge: int = 40) -> Rect | None:
    """First clear position, scanned lower third (bottom-up) then the rest of the frame (bottom-up); centred on x,
    then pushed left/right. None when no position clears all faces: the caller must drop or shrink the title."""
    if title_w > width or title_h > height:
        return None
    xs = [(width - title_w) // 2, edge, width - edge - title_w]
    lower_start = height * 2 // 3
    y_bottom = height - edge - title_h
    step = max(8, title_h // 2)
    ys: list[int] = []
    y = y_bottom
    while y >= lower_start:                      # lower third first
        ys.append(y)
        y -= step
    y = lower_start - step
    while y >= edge:                             # then the rest, still bottom-up
        ys.append(y)
        y -= step
    faces = list(faces)
    for y in ys:
        for x in xs:
            x = max(0, min(width - title_w, x))
            rect = (x, y, x + title_w, y + title_h)
            if _clear(rect, faces, margin):
                return rect
    return None


def check_titles(titles: Sequence[dict], faces_at: Callable[[float], Sequence[Rect]], step: float = 0.25,
                 margin: float = 0) -> list[dict]:
    """Every title is sampled each `step` seconds over [t0, t1); a violation is reported once per title with the
    first offending time. titles: {"t0", "t1", "rect"}; faces_at(t) returns the face boxes visible at t."""
    bad: list[dict] = []
    for index, title in enumerate(titles):
        t = float(title["t0"])
        end = float(title["t1"])
        while t < end - 1e-9:
            hit = next((f for f in faces_at(t) if overlaps(title["rect"], f, margin)), None)
            if hit is not None:
                bad.append({"index": index, "t": round(t, 3), "face": tuple(hit), "rect": tuple(title["rect"])})
                break
            t += step
    return bad
