"""Shared machinery of the trend-edit builders: clips in memory, soft parsing masks, Lab recolour (a numpy port of the
Genjutsu constructor's `recolor`), a frame writer and the final audio + kinetic-text pass.

Everything is local (ffmpeg + numpy + OpenCV). Interpreter: the gate venv (opencv, librosa) is enough.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx  # noqa: E402

CANVAS_W, CANVAS_H, FPS = 1488, 1128, 24


def cv2():
    import cv2 as _cv2
    return _cv2


# ------------------------------------------------------------------ colour presets (genjutsu_core.js PRESETS)
HAIR = {
    "blue": {"color": "#1f5fd1", "strength": 85, "lightness": 45},
    "green": {"color": "#1f9d55", "strength": 85, "lightness": 45},
    "white": {"color": "#f2f0ea", "strength": 80, "lightness": 70},
    "black": {"color": "#0d0d10", "strength": 90, "lightness": 30},
    "copper": {"color": "#c46e3d", "strength": 75, "lightness": 35},
    "platinum": {"color": "#ebe6da", "strength": 70, "lightness": 55},
}
OUTFIT = {   # region -> setting; 'clothes' = the whole outfit minus the more specific top / bottom
    "uniform": {"top": {"color": "#f1f0ec", "strength": 85, "lightness": 72},
                "bottom": {"color": "#2a2b30", "strength": 85, "lightness": 35}},
    "denim": {"top": {"color": "#3d5a86", "strength": 80, "lightness": 50},
              "bottom": {"color": "#9fb2c9", "strength": 70, "lightness": 60}},
    "emerald": {"clothes": {"color": "#2f6b4a", "strength": 80, "lightness": 45}},
    "office": {"top": {"color": "#1f3354", "strength": 85, "lightness": 60},
               "bottom": {"color": "#7d8083", "strength": 80, "lightness": 50}},
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hex_to_lab(h: str) -> tuple[float, float, float]:
    r, g, b = hex_to_rgb(h)
    px = np.array([[[b, g, r]]], np.float32) / 255.0
    lab = cv2().cvtColor(px, cv2().COLOR_BGR2Lab)[0, 0]
    return float(lab[0]), float(lab[1]), float(lab[2])


def recolor(frame: np.ndarray, regions: dict[str, tuple[np.ndarray, dict]]) -> np.ndarray:
    """Texture-preserving recolour in Lab, like genjutsu_core.js recolor():
    L' = L + (targetL - meanL(region)) * lightness%, (a,b)' = (a,b) + (target(a,b) - (a,b)) * strength%,
    blended with the soft mask. `regions` = {name: (mask uint8 HxW, {"color","strength","lightness"})}.
    The general 'clothes' region yields to a more specific 'top' / 'bottom' that is also on."""
    if not regions:
        return frame
    c = cv2()
    lab = c.cvtColor(frame.astype(np.float32) / 255.0, c.COLOR_BGR2Lab)
    out = frame.astype(np.float32)
    masks = {k: v[0].astype(np.float32) / 255.0 for k, v in regions.items()}
    for name, (_m, s) in regions.items():
        w = masks[name].copy()
        if name == "clothes":
            for sub in ("top", "bottom"):
                if sub in masks:
                    w *= 1.0 - masks[sub]
        tot = float(w.sum())
        if tot < 1.0:
            continue
        mean_l = float((w * lab[:, :, 0]).sum() / tot)
        tl, ta, tb = hex_to_lab(s["color"])
        k = max(0.0, min(100.0, s["strength"])) / 100.0
        dl = (tl - mean_l) * max(0.0, min(100.0, s["lightness"])) / 100.0
        new = np.empty_like(lab)
        new[:, :, 0] = np.clip(lab[:, :, 0] + dl, 0, 100)
        new[:, :, 1] = lab[:, :, 1] + (ta - lab[:, :, 1]) * k
        new[:, :, 2] = lab[:, :, 2] + (tb - lab[:, :, 2]) * k
        rgb = np.clip(c.cvtColor(new, c.COLOR_Lab2BGR), 0, 1) * 255.0
        out = out + (rgb - out) * w[:, :, None]
    return np.clip(out, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ clips / masks
def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate,nb_frames", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    num, _, den = s["avg_frame_rate"].partition("/")
    return {"w": int(s["width"]), "h": int(s["height"]), "fps": float(num) / float(den or 1)}


class Clip:
    """A whole video decoded to memory (BGR uint8), optional crop (x, y, w, h) applied at load."""

    def __init__(self, path: Path | str, crop: tuple[int, int, int, int] | None = None, max_frames: int | None = None):
        self.path = Path(path)
        p = probe(self.path)
        self.fps = p["fps"]
        w, h = p["w"], p["h"]
        cmd = ["ffmpeg", "-v", "error", "-i", str(self.path), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        size = w * h * 3
        self.frames: list[np.ndarray] = []
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            f = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            if crop:
                x, y, cw, ch = crop
                f = f[y:y + ch, x:x + cw]
            self.frames.append(np.ascontiguousarray(f))
            if max_frames and len(self.frames) >= max_frames:
                break
        proc.stdout.close()
        proc.kill()
        self.n = len(self.frames)
        self.h, self.w = self.frames[0].shape[:2]

    @property
    def duration(self) -> float:
        return self.n / self.fps

    def index(self, t: float) -> int:
        return min(self.n - 1, max(0, int(t * self.fps + 1e-6)))

    def get(self, t: float, canvas=(CANVAS_W, CANVAS_H), blend: bool = True) -> np.ndarray:
        """Frame at source time t (clamped), blended between neighbours when t falls between frames (slow-mo)."""
        i0, i1, w = fx.frame_pick(t, self.fps, self.n)
        f = self.frames[i0]
        if blend and w > 0.05 and i1 != i0:
            f = cv2().addWeighted(f, 1 - w, self.frames[i1], w, 0)
        return fit(f, canvas)


def fit(frame: np.ndarray, canvas=(CANVAS_W, CANVAS_H)) -> np.ndarray:
    w, h = canvas
    fh, fw = frame.shape[:2]
    if (fw, fh) == (w, h):
        return frame
    if abs(fw / fh - w / h) < 0.02:               # same aspect: plain resize, no fill
        return cv2().resize(frame, (w, h), interpolation=cv2().INTER_CUBIC if fw < w else cv2().INTER_AREA)
    return fx.blur_fill(frame, w, h)


class MaskSeq:
    """Soft masks per source frame: <dir>/<region>/%04d.png (1-based), loaded lazily."""

    def __init__(self, root: Path | str, regions=("hair", "top", "bottom", "clothes", "skin")):
        self.root = Path(root)
        self.regions = regions
        self._cache: dict[tuple[str, int], np.ndarray] = {}

    def get(self, region: str, idx0: int, canvas=(CANVAS_W, CANVAS_H)) -> np.ndarray:
        key = (region, idx0)
        if key not in self._cache:
            m = cv2().imread(str(self.root / region / f"{idx0 + 1:04d}.png"), cv2().IMREAD_GRAYSCALE)
            if m is None:
                raise FileNotFoundError(self.root / region / f"{idx0 + 1:04d}.png")
            if (m.shape[1], m.shape[0]) != canvas:
                m = cv2().resize(m, canvas, interpolation=cv2().INTER_LINEAR)
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = m
        return self._cache[key]

    def center(self, idx0: int, region: str = "hair", canvas=(CANVAS_W, CANVAS_H)) -> tuple[float, float]:
        """Centroid of a region as fractions of the frame (head centre for 'hair' with the face under it)."""
        m = self.get(region, idx0, canvas).astype(np.float32)
        s = float(m.sum())
        if s < 1:
            return 0.5, 0.45
        ys, xs = np.mgrid[0:m.shape[0], 0:m.shape[1]]
        return float((m * xs).sum() / s / m.shape[1]), float((m * ys).sum() / s / m.shape[0])


def hit_fx(f: np.ndarray, tau: float, hits: list[tuple[float, float]], S: float = 1.0,
           center: tuple[float, float] = (0.5, 0.5), window: float = 0.7) -> np.ndarray:
    """The latest hit (time, strength) within `window` seconds drives a zoom punch, an RGB split and a flash frame.
    strength 0.5 = soft punch, 1 = punch + split + flash, 2 = the drop (big flash)."""
    last = None
    for th, st in hits:
        if th <= tau + 1e-9 < th + window:
            last = (th, st)
    if last is None:
        return f
    th, st = last
    dt = tau - th
    sc = fx.punch_scale(tau, th, peak=0.045 + 0.05 * st, decay=0.16)
    dx = int(round(26 * S * st * np.exp(-dt / 0.07)))
    fl = 0.5 * np.exp(-dt / 0.045) if st >= 1.0 else 0.0
    if st >= 2.0:
        fl = 0.8 * np.exp(-dt / 0.06)
    f = fx.zoom_punch(f, sc, center)
    f = fx.rgb_split(f, dx, 0)
    return fx.flash(f, float(fl))


# ------------------------------------------------------------------ writer + final pass
class Writer:
    """Pipes BGR frames to ffmpeg (H.264 crf 14, fast) -> an intermediate file without audio or text."""

    def __init__(self, out: Path, w=CANVAS_W, h=CANVAS_H, fps=FPS, crf=14):
        self.out, self.w, self.h = Path(out), w, h
        self.p = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-pix_fmt", "yuv420p", str(self.out)],
            stdin=subprocess.PIPE)
        self.count = 0

    def write(self, frame: np.ndarray) -> None:
        assert frame.shape == (self.h, self.w, 3) and frame.dtype == np.uint8, frame.shape
        self.p.stdin.write(frame.tobytes())
        self.count += 1

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait()


def build_audio(track: Path, segs: list[tuple[float, float]], out: Path, fade_out: float = 0.6, edge: float = 0.006) -> float:
    """Concatenate beat-aligned segments (start, dur) of the track's audio into out (wav), tiny edge fades, end fade."""
    parts, labels = [], []
    total = sum(d for _s, d in segs)
    for i, (s, d) in enumerate(segs):
        fo = max(0.0, d - edge)
        parts.append(f"[0:a]atrim=start={s:.5f}:duration={d:.5f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={edge}:st=0,afade=t=out:d={edge}:st={fo:.5f}[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=0:a=1,afade=t=out:d={fade_out}:st={max(0.0, total - fade_out):.4f}[o]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(track), "-filter_complex", graph, "-map", "[o]",
                    "-ar", "44100", "-ac", "2", str(out)], check=True)
    return total


@dataclass
class Text:
    text: str
    t0: float
    t1: float
    kw: dict = field(default_factory=dict)


def final_pass(video: Path, audio: Path | None, texts: list[Text], out: Path, crf: int = 21, maxrate: str = "9M",
               duration: float | None = None, scale: tuple[int, int] | None = None) -> None:
    """Kinetic text (ffmpeg drawtext) + audio + the delivery encode (H.264 yuv420p, AAC)."""
    vf = [fx.drawtext(t.text, t.t0, t.t1, **t.kw) for t in texts]
    if scale:
        vf.append(f"scale={scale[0]}:{scale[1]}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-maxrate", maxrate, "-bufsize", "18M",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-map", "0:v", "-map", "1:a"]
    if duration:
        cmd += ["-t", f"{duration:.4f}"]
    cmd += [str(out)]
    subprocess.run(cmd, check=True)
