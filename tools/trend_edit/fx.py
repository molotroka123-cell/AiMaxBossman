"""Short-form edit effects that ffmpeg/numpy can render — the 2026 "trend" vocabulary mapped to code.

Frames are HxWx3 uint8 BGR numpy arrays (OpenCV order). Timing helpers are pure (no numpy needed) and unit-tested.

  zoom punch-in   punch_scale() + zoom_punch()     speed ramp / velocity   ramp_source_time() + velocity_knots()
  RGB split       rgb_split()                      flash frame             flash()
  mask / wipe     wipe()                           whip-pan                whip_blur()
  kinetic text    drawtext()  -> ffmpeg drawtext filter strings (fontfile arial.ttf, colons escaped)
  framing         blur_fill() keeps the source frame and fills the rest with its own blurred copy (no stretching)
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

FONT = "C:/Windows/Fonts/arial.ttf"
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"


# ------------------------------------------------------------------ timing (pure)
def ease_out_cubic(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def punch_scale(t: float, t_cut: float, peak: float = 0.14, decay: float = 0.16) -> float:
    """Zoom factor of a punch-in that starts at the cut and relaxes exponentially: 1+peak at t_cut -> 1."""
    if t < t_cut:
        return 1.0
    return 1.0 + peak * math.exp(-(t - t_cut) / decay)


def ramp_source_time(t_out: float, knots: Sequence[tuple[float, float]], src_start: float = 0.0) -> float:
    """Source time reached after t_out seconds of output, for a piecewise-LINEAR speed profile.

    knots = [(t_out, speed), ...] sorted by t_out; speed is held before the first and after the last knot.
    The integral of a linear speed segment is the trapezoid, so this is exact (no sampling)."""
    if not knots:
        return src_start + t_out
    ks = sorted(knots)
    src = src_start
    # before the first knot: constant speed ks[0][1]
    first_t, first_s = ks[0]
    if t_out <= first_t:
        return src + t_out * first_s
    src += first_t * first_s
    for (ta, sa), (tb, sb) in zip(ks, ks[1:]):
        if t_out <= tb:
            u = t_out - ta
            sx = sa + (sb - sa) * (u / (tb - ta) if tb > ta else 0.0)
            return src + u * (sa + sx) / 2
        src += (tb - ta) * (sa + sb) / 2
    return src + (t_out - ks[-1][0]) * ks[-1][1]


def velocity_knots(t_hit: float, lead: float = 0.30, hold: float = 0.10, tail: float = 0.25,
                   slow: float = 0.35, fast: float = 2.4) -> list[tuple[float, float]]:
    """The classic velocity-edit curve: cruise at 1x, ease down to `slow` before the beat, snap up to `fast` ON the beat
    (t_hit), settle back to 1x. Returned as ramp_source_time() knots in OUTPUT seconds."""
    return [(0.0, 1.0), (t_hit - lead, 1.0), (t_hit - hold, slow), (t_hit, slow), (t_hit + 1e-3, fast),
            (t_hit + tail, 1.0)]


def velocity_knots_balanced(t_hit: float, lead: float = 0.30, hold: float = 0.10, tail: float = 0.30,
                            slow: float = 0.35) -> list[tuple[float, float]]:
    """Velocity curve whose source consumption equals its output time again at t_hit + tail, so footage stays in sync
    with the (un-ramped) music: the speed-up on the beat pays back exactly what the slow-down borrowed."""
    deficit = (lead - hold) * (1 + slow) / 2 - (lead - hold)             # source - output during the ease-down (<= 0)
    deficit += hold * slow - hold                                        # the hold at `slow`
    fast = 1.0 + 2.0 * (-deficit) / tail                                 # trapezoid over `tail` from fast down to 1
    return [(0.0, 1.0), (t_hit - lead, 1.0), (t_hit - hold, slow), (t_hit, slow), (t_hit + 1e-3, fast),
            (t_hit + tail, 1.0)]


def ramp_out_duration_for(src_len: float, knots: Sequence[tuple[float, float]], step: float = 1 / 240) -> float:
    """Output seconds needed to consume src_len source seconds (numeric inverse; monotone because speed > 0)."""
    t = 0.0
    while ramp_source_time(t, knots) < src_len and t < 3600:
        t += step
    return t


def frame_pick(t: float, fps: float, n_frames: int) -> tuple[int, int, float]:
    """(i0, i1, w) for a source time: blend frames i0 and i1 with weight w on i1 (sub-frame sampling for slow-mo)."""
    x = max(0.0, t * fps)
    i0 = min(n_frames - 1, int(math.floor(x)))
    i1 = min(n_frames - 1, i0 + 1)
    return i0, i1, float(x - math.floor(x)) if i0 != i1 else 0.0


def alternating_cuts(t0: float, t1: float, step: float) -> list[tuple[float, float, int]]:
    """[(start, end, which)] hard cuts every `step` seconds; which toggles 0,1,0,1 (original / swapped)."""
    out, t, w = [], t0, 0
    while t < t1 - 1e-9:
        e = min(t1, t + step)
        out.append((round(t, 6), round(e, 6), w))
        t, w = e, 1 - w
    return out


# ------------------------------------------------------------------ pixels
def _cv2():
    import cv2
    return cv2


def zoom_punch(frame: np.ndarray, scale: float, center: tuple[float, float] = (0.5, 0.5)) -> np.ndarray:
    """Scale up around `center` (fractions of w, h) and crop back to the frame size."""
    if scale <= 1.0005:
        return frame
    cv2 = _cv2()
    h, w = frame.shape[:2]
    cw, ch = w / scale, h / scale
    x0 = min(max(center[0] * w - cw / 2, 0), w - cw)
    y0 = min(max(center[1] * h - ch / 2, 0), h - ch)
    m = np.array([[scale, 0, -x0 * scale], [0, scale, -y0 * scale]], np.float32)
    return cv2.warpAffine(frame, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def rgb_split(frame: np.ndarray, dx: int, dy: int = 0) -> np.ndarray:
    """Chromatic aberration: red moves by (+dx,+dy), blue by (-dx,-dy), green stays (BGR order)."""
    if dx == 0 and dy == 0:
        return frame
    out = frame.copy()
    out[:, :, 2] = np.roll(frame[:, :, 2], (dy, dx), axis=(0, 1))
    out[:, :, 0] = np.roll(frame[:, :, 0], (-dy, -dx), axis=(0, 1))
    return out


def flash(frame: np.ndarray, amount: float, color: tuple[int, int, int] = (255, 255, 255)) -> np.ndarray:
    """Flash frame: blend toward `color` by amount 0..1."""
    a = float(min(1.0, max(0.0, amount)))
    if a <= 0:
        return frame
    c = np.array(color, np.float32)
    return (frame.astype(np.float32) * (1 - a) + c * a).astype(np.uint8)


def wipe_mask(h: int, w: int, p: float, kind: str = "h", soft: float = 0.03, angle: float = 0.35) -> np.ndarray:
    """Float mask 0..1 (1 = shows B) for progress p in 0..1; edge softness `soft` as a fraction of the frame."""
    p = min(1.0, max(0.0, p))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = xs / max(1, w - 1), ys / max(1, h - 1)
    if kind == "h":
        d = nx
    elif kind == "v":
        d = ny
    elif kind == "diag":
        d = (nx + angle * ny) / (1 + angle)
    elif kind == "circle":
        d = np.hypot((nx - 0.5) * w / h, ny - 0.5) / (0.5 * math.hypot(w / h, 1.0))
    else:
        raise ValueError(kind)
    edge = p * (1 + 2 * soft) - soft
    return np.clip((edge - d) / max(1e-6, soft) * 0.5 + 0.5, 0.0, 1.0)


def wipe(a: np.ndarray, b: np.ndarray, p: float, kind: str = "h", soft: float = 0.03) -> np.ndarray:
    """A -> B reveal. p=0 is A, p=1 is B."""
    if p <= 0:
        return a
    if p >= 1:
        return b
    m = wipe_mask(a.shape[0], a.shape[1], p, kind, soft)[:, :, None]
    return (a.astype(np.float32) * (1 - m) + b.astype(np.float32) * m).astype(np.uint8)


def whip_blur(frame: np.ndarray, strength: float, horizontal: bool = True) -> np.ndarray:
    """Whip-pan smear: directional box blur (strength = fraction of the frame side, 0..0.3)."""
    k = int(max(1, round(strength * (frame.shape[1] if horizontal else frame.shape[0]))))
    if k <= 1:
        return frame
    cv2 = _cv2()
    return cv2.blur(frame, (k, 1) if horizontal else (1, k))


def split_before_after(before: np.ndarray, after: np.ndarray, p: float, line: int = 4) -> np.ndarray:
    """Vertical before/after split wipe with a bright divider; left = before, right = after, p = divider position."""
    h, w = before.shape[:2]
    x = int(round(min(1.0, max(0.0, p)) * w))
    out = before.copy()
    out[:, x:] = after[:, x:]
    if 0 < x < w:
        out[:, max(0, x - line // 2): x + line // 2 + 1] = 255
    return out


def blur_fill(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    """Place `frame` into w x h WITHOUT stretching: scale to fit, fill the rest with a blurred, darkened cover copy
    (the same look the reference edit itself uses on its side bars)."""
    cv2 = _cv2()
    fh, fw = frame.shape[:2]
    if (fw, fh) == (w, h):
        return frame
    s_cover = max(w / fw, h / fh)
    cover = cv2.resize(frame, (max(w, int(round(fw * s_cover))), max(h, int(round(fh * s_cover)))), interpolation=cv2.INTER_AREA)
    y0, x0 = (cover.shape[0] - h) // 2, (cover.shape[1] - w) // 2
    bg = cv2.GaussianBlur(cover[y0:y0 + h, x0:x0 + w], (0, 0), max(6, w // 60))
    bg = (bg.astype(np.float32) * 0.55).astype(np.uint8)
    s = min(w / fw, h / fh)
    nw, nh = int(round(fw * s)), int(round(fh * s))
    fg = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x, y = (w - nw) // 2, (h - nh) // 2
    bg[y:y + nh, x:x + nw] = fg
    return bg


def vignette_grade(frame: np.ndarray, tint: tuple[float, float, float] = (1.0, 1.0, 1.0), gain: float = 1.0,
                   vignette: float = 0.0) -> np.ndarray:
    """Light colour grade: per-channel BGR multipliers, global gain, optional vignette 0..1."""
    f = frame.astype(np.float32) * np.array(tint, np.float32) * gain
    if vignette > 0:
        h, w = f.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.hypot((xs / w - 0.5), (ys / h - 0.5)) / 0.7071
        f *= (1 - vignette * r ** 2)[:, :, None]
    return np.clip(f, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ kinetic text (ffmpeg drawtext)
def escape_drawtext(text: str) -> str:
    """Escape for a drawtext `text='...'` value inside a -vf / filter_complex string: backslash, colon, quote, percent."""
    return (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\\\%")
            .replace(",", "\\,").replace(";", "\\;").replace("[", "\\[").replace("]", "\\]"))


def drawtext(text: str, t0: float, t1: float, *, size: int = 96, x: str = "(w-text_w)/2", y: str = "(h-text_h)/2",
             color: str = "white", box: bool = False, border: int = 4, font: str = FONT_BOLD, pop: float = 0.18,
             rise: int = 0, fade_out: float = 0.12) -> str:
    """One drawtext filter: pops in (font grows from 60% to 100% over `pop` s with an overshoot-free ease), optional
    vertical rise, fades out over `fade_out` s. Everything is an ffmpeg expression of t, so one pass renders it."""
    d = max(1e-3, t1 - t0)
    grow = f"min(1,(t-{t0:.4f})/{pop:.4f})"
    size_expr = f"{size}*(0.6+0.4*(1-pow(1-{grow},3)))"
    alpha = f"if(lt(t,{t0 + pop:.4f}),(t-{t0:.4f})/{pop:.4f},if(gt(t,{t1 - fade_out:.4f}),({t1:.4f}-t)/{fade_out:.4f},1))"
    y_expr = y if not rise else f"({y})+{rise}*pow(1-{grow},2)"
    parts = [
        f"fontfile='{font.replace(':', chr(92) + ':')}'", f"text='{escape_drawtext(text)}'", f"fontsize='{size_expr}'", f"fontcolor={color}",
        f"alpha='{alpha}'", f"x={x}", f"y='{y_expr}'", f"borderw={border}", "bordercolor=black@0.85",
        f"enable='between(t,{t0:.4f},{t1:.4f})'",
    ]
    if box:
        parts += ["box=1", "boxcolor=black@0.45", "boxborderw=18"]
    del d
    return "drawtext=" + ":".join(parts)
