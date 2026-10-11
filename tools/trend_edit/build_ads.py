"""Ad-style 9:16 hype edits (12-15 s) built on the beat grid of a reference track. Local only (ffmpeg + OpenCV + numpy).

Presets live here as plain functions returning a `Spec` (shots, hits, texts); `render(spec)` is shared:
  * every cut / colour change is on a beat of the track's comb-fitted grid (beat_grid.py),
  * zoom punch on every hit, velocity ramp (fast -> slow) inside every shot, RGB split + flash on hits,
  * the RGB split is masked off the face (an ellipse around the head) so identity is not damaged,
  * kinetic type is drawn AWAY from the face (top strip / bottom third),
  * hair recolour = hair_v2 (soft alpha, skin excluded, Lab tone-map) from cached masks.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/build_ads.py hair_cycle --out ad_b.mp4
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beat_grid as bg  # noqa: E402
import fx  # noqa: E402
import hair_v2 as hv  # noqa: E402
import hair_fused as hf  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
BOSS = Path.home() / "Bossman"
OUT_W, OUT_H, FPS = 1080, 1920, 30
SRC_SWAP = VID / "v2-20261010" / "final-v2-clip1.mp4"      # ref-clip-01 with her face (FaceFusion quality pass), 30 fps
SRC_ORIG = BOSS / "video-testset-private" / "ref-clip-01.mp4"   # same frames, original face (clean before)
FUSED = VID / "ads-20261011" / "prep" / "fused"
USE_FUSED = bool(int(__import__("os").environ.get("BUILD_ADS_FUSED", "0")))
LOOKS = VID / "ads-20261011" / "prep" / "accept" / "looks"
CACHE = VID / "ads-20261011" / "prep" / "c1cache_f"
TRACK = SRC_ORIG                                           # music: the reference's own track (personal use only)
PIC_X0, PIC_X1 = 310, 965                                  # real picture inside the blur-filled 1280x720 frame (x range)
CROP_W = 405.0                                             # 9:16 window width at the native 720 px height
GRID = bg.BeatGrid(period=0.4597551020408164, phase=0.052680272108843545)


# ------------------------------------------------------------------ spec
@dataclass
class Shot:
    beat0: float
    beat1: float
    src_t: float                    # source time (s) at the shot start
    knots: list                     # speed knots in SHOT-relative seconds: [(t, speed), ...]
    color: str | None = None        # recolour to this hair colour (None = natural)
    prev_color: str | None = None   # colour shown before the wipe at the start of the shot
    wipe: str = "diag"
    zoom0: float = 1.0
    zoom1: float = 1.06
    hit: float = 1.0                # hit strength at the shot start
    split: bool = False             # before/after divider line sliding (clean explanation shot)
    dim: float = 1.0
    card: bool = False              # end card (dark, no footage)
    seq: list | None = None         # [(beat_offset, {face, color, bg, still})]
    split_orig: bool = False        # the split's left half shows the ORIGINAL face instead of the natural swapped one
    video: str | None = None        # a rendered 1080x1920 clip played as the shot (screencast)
    video_t0: float = 0.0           # its start time (s) at the shot start
    vzoom: tuple = (1.0, 1.0)       # uniform zoom of the clip over the shot (centre crop, never stretched)


@dataclass
class Spec:
    shots: list[Shot]
    texts: list[tuple[str, float, float, dict]] = field(default_factory=list)   # (text, beat0, beat1, drawtext kwargs)
    total_beats: float = 30.0
    name: str = "ad"
    audio_loop: tuple | None = None   # (intro_beats, loop_beats): the track's intro once, then the drop block looped


def knots_ramp(d: float, fast: float = 1.5, slow: float = 0.5) -> list[tuple[float, float]]:
    """Fast start, glide to 1x, anticipation slow-down into the next beat (the velocity-edit feel)."""
    return [(0.0, fast), (0.45 * d, 1.0), (d, slow)]


def consume(d: float, knots) -> float:
    return fx.ramp_source_time(d, knots) - fx.ramp_source_time(0.0, knots)


# ------------------------------------------------------------------ source access
class Source:
    def __init__(self):
        def load(path):
            cap = cv2.VideoCapture(str(path))
            out = []
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                out.append(np.ascontiguousarray(f[:, 280:1000]))   # 720 x 720: the picture + a little bar
            return out
        self.swap = load(SRC_SWAP)
        self.orig = load(SRC_ORIG)
        self.n = min(len(self.swap), len(self.orig))
        self.fps = 30.0
        self._alpha: dict[int, np.ndarray] = {}
        self._cx: dict[int, tuple[float, float]] = {}
        self.state = hv.RecolorState()
        # reference-guided looks fitted on the owner's reference photos (accept_ref.py --save): the hero colours
        self.looks = {n: hv.RefLook.load(LOOKS / f"{n}.npz", n) for n in ("pink", "platinum", "blue", "red")
                      if (LOOKS / f"{n}.npz").exists()}

    def alpha(self, i: int) -> np.ndarray:
        if i not in self._alpha:
            fz = FUSED / f"{i:05d}.npy"
            if USE_FUSED and fz.exists():          # best-effort fused matte (hair_fused.py), already cropped to 720x720
                self._alpha[i] = np.load(fz).astype(np.float32) / 255.0
                return self._alpha[i]
            z = np.load(CACHE / f"{i:05d}.npz")
            a_seg = z["alpha"][:, 280:1000].astype(np.float32) / 255.0
            if "head" in z.files:
                self._alpha[i] = hv.refine_alpha(a_seg, z["head"][:, 280:1000].astype(np.float32) / 255.0,
                                                 z["skin"][:, 280:1000].astype(np.float32) / 255.0, self.swap[i])
            else:
                self._alpha[i] = a_seg
        return self._alpha[i]

    def head(self, i: int) -> tuple[float, float]:
        """Head centre (x, y) in the 720x720 crop: centroid of the hair alpha; sticks to the previous value on empty alpha."""
        if i not in self._cx:
            a = self.alpha(i)
            s = float(a.sum())
            if s < 50:
                self._cx[i] = self._cx.get(i - 1, (360.0, 300.0))
            else:
                ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]]
                self._cx[i] = (float((a * xs).sum() / s), float((a * ys).sum() / s))
        return self._cx[i]

    def frame(self, t: float, which: str = "swap", blend: bool = True):
        x = min(max(0.0, t * self.fps), self.n - 1.001)
        i0 = int(math.floor(x))
        w = x - i0
        arr = self.swap if which == "swap" else self.orig
        f = arr[i0]
        if blend and w > 0.08:
            f = cv2.addWeighted(f, 1 - w, arr[i0 + 1], w, 0)
        return f, i0


# ------------------------------------------------------------------ pixels
def sharpen(img: np.ndarray, amount: float = 0.6) -> np.ndarray:
    b = cv2.GaussianBlur(img, (0, 0), 1.6)
    return cv2.addWeighted(img, 1 + amount, b, -amount, 0)


def crop_to_canvas(img720: np.ndarray, head: tuple[float, float], zoom: float, head_y_frac: float = 0.36) -> tuple[np.ndarray, tuple[float, float]]:
    """9:16 window of the 720x720 picture around the head -> 1080x1920. Returns (frame, head centre in output px)."""
    ch = 720.0 / zoom
    cw = CROP_W / zoom
    cx = min(max(head[0], PIC_X0 - 280 + cw / 2), PIC_X1 - 280 - cw / 2)
    y0 = min(max(head[1] - head_y_frac * ch, 0.0), 720.0 - ch)
    x0 = cx - cw / 2
    m = np.array([[OUT_W / cw, 0, -x0 * OUT_W / cw], [0, OUT_H / ch, -y0 * OUT_H / ch]], np.float32)
    out = cv2.warpAffine(img720, m, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    hx = (head[0] - x0) * OUT_W / cw
    hy = (head[1] - y0) * OUT_H / ch
    return sharpen(out, 0.5), (hx, hy)


def face_protect(split_img: np.ndarray, clean: np.ndarray, head: tuple[float, float]) -> np.ndarray:
    ys, xs = np.mgrid[0:OUT_H, 0:OUT_W].astype(np.float32)
    d = ((xs - head[0]) / (0.34 * OUT_W)) ** 2 + ((ys - head[1]) / (0.20 * OUT_H)) ** 2
    m = np.clip((1.25 - d) / 0.5, 0, 1)[:, :, None]
    return (split_img.astype(np.float32) * (1 - m) + clean.astype(np.float32) * m).astype(np.uint8)


def grade(img: np.ndarray) -> np.ndarray:
    """One consistent look for every shot: gentle S-curve, a touch of warmth in the lights, slight vignette."""
    f = img.astype(np.float32) / 255.0
    f = f + 0.18 * (f - 0.5) * (1 - np.abs(2 * f - 1))          # soft S
    h, w = f.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(xs / w - 0.5, ys / h - 0.5) / 0.7071
    f *= (1 - 0.22 * r ** 2)[:, :, None]
    return np.clip(f * 255.0, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ render
PLATE_NEON = VID / "promo-bg-20261010" / "plate-night-neon.png"
STILLS = VID / "gf-train-refs-20261010"
STILL_FILES = {
    "navy": "media-generation-gf-blazer-navy-0-7841965f-0f8e-46ab-8262-df035e244041.webp",
    "bob": "media-generation-gf-blazer-bob-0-d398be49-4d5a-4c74-8210-2c92332a65f2.webp",
    "burgundy": "media-generation-gf-blazer-burgundy-0-fda097a2-01c7-4c44-8b63-2c9880b9d253.webp",
    "ponytail": "media-generation-gf-blazer-ponytail-0-6c0fb00a-de53-4f07-a274-56058116126c.webp",
    "wavy": "media-generation-gf-blazer-wavy-0-3415599f-a285-4cef-9dea-90d21d9d5a99.webp",
    "bun": "media-generation-gf-blazer-bun-0-56d39fb6-05f4-4036-8c00-1045cfa17ed8.webp",
    "gray": "media-generation-gf-blazer-gray-0-8a652b33-5d3f-4a7a-a8c2-da6f74538641.webp",
    "loose": "media-generation-gf-blazer-loose-hair-0-6c02d75e-fa01-443a-ab40-1b859352cd13.webp",
}


def _cover(img: np.ndarray, w: int, h: int, focus: tuple[float, float] = (0.5, 0.33), zoom: float = 1.0) -> np.ndarray:
    """Uniform-scale cover crop (never stretches): focus = (x, y) fractions of the source that stay centred."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih) * zoom
    cw, ch = w / s, h / s
    x0 = min(max(focus[0] * iw - cw / 2, 0), iw - cw)
    y0 = min(max(focus[1] * ih - ch / 2, 0), ih - ch)
    m = np.array([[s, 0, -x0 * s], [0, s, -y0 * s]], np.float32)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


class Extras:
    """Plates (backgrounds) and reference stills, loaded lazily."""

    def __init__(self):
        self._plates: dict[str, np.ndarray] = {}
        self._stills: dict[str, np.ndarray] = {}
        self._caps: dict[str, cv2.VideoCapture] = {}

    def cap(self, path: str) -> cv2.VideoCapture:
        if path not in self._caps:
            self._caps[path] = cv2.VideoCapture(path)
        return self._caps[path]

    def plate(self, key: str, t: float) -> np.ndarray:
        if key not in self._plates:
            if key in ("neon", "neon2"):
                im = cv2.imread(str(PLATE_NEON))
                s = 840.0 / im.shape[0]
                im = cv2.resize(im, (int(im.shape[1] * s), 840), interpolation=cv2.INTER_CUBIC)
                if key == "neon2":
                    im = im[:, ::-1].copy()
                    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
                    hsv[:, :, 0] = (hsv[:, :, 0].astype(int) + 70) % 180
                    im = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                self._plates[key] = im
            elif key == "studio":
                ys, xs = np.mgrid[0:760, 0:900].astype(np.float32)
                r = np.hypot((xs / 900 - 0.5) * 1.2, ys / 760 - 0.30)
                v = np.clip(215 - 330 * r ** 1.4, 22, 230)
                im = np.dstack([v * 0.94, v * 0.97, v * 1.0]).astype(np.uint8)
                im[600:, :] = (im[600:, :] * 0.82).astype(np.uint8)
                self._plates[key] = im
        im = self._plates[key]
        h, w = im.shape[:2]
        pan = 0.5 + 0.5 * math.sin(t * 0.7)
        x0 = int((w - 720) * pan)
        y0 = max(0, (h - 720) // 2)
        return np.ascontiguousarray(im[y0:y0 + 720, x0:x0 + 720])

    def still(self, key: str, t_in: float, d: float) -> np.ndarray:
        if key not in self._stills:
            from PIL import Image
            im = np.array(Image.open(STILLS / STILL_FILES[key]).convert("RGB"))[:, :, ::-1].copy()
            self._stills[key] = im
        im = self._stills[key]
        z = 1.0 + 0.10 * min(1.0, t_in / max(d, 1e-3))                  # slow push-in, uniform scale
        out = _cover(im, OUT_W, OUT_H, (0.5, 0.40), z)
        return sharpen(out, 0.25)


def composite_bg(img: np.ndarray, al_person: np.ndarray, plate: np.ndarray) -> np.ndarray:
    """RVM person alpha over a plate: colour-matched light (subject pulled 14 % toward the plate's palette), a rim light of
    the plate colour on the soft edge. No contact shadow: the framing has no floor (said in the report)."""
    a2 = hv.smoothstep(0.08, 0.92, np.clip(al_person, 0, 1))
    a = a2[:, :, None]
    pm = plate.reshape(-1, 3).mean(0) / max(1.0, float(plate.mean()))
    subj = img.astype(np.float32) * (0.86 + 0.14 * pm[None, None, :])
    edge = cv2.GaussianBlur(np.clip(4.0 * a2 * (1.0 - a2), 0, 1), (0, 0), 3.0)[:, :, None]
    rim = cv2.GaussianBlur(plate, (0, 0), 9).astype(np.float32) * (0.55 * edge)
    out = subj * a + plate.astype(np.float32) * (1 - a) + rim
    return np.clip(out, 0, 255).astype(np.uint8)


def compose(src: Source, ex: Extras, st: dict, t_abs: float, al_hair: np.ndarray, idx: int, img_swap, img_orig):
    """One state -> a 720x720 live frame (face / hair colour / background)."""
    img = img_orig if st.get("face") == "orig" else img_swap
    col = st.get("color")
    if col and col != "natural":
        if col in src.looks:
            img = src.looks[col].apply(img, al_hair)
        else:
            img = hv.recolor(img, al_hair, col, src.state)
    bgk = st.get("bg")
    if bgk and bgk != "real":
        z = np.load(CACHE / f"{idx:05d}.npz")
        person = z["person"][:, 280:1000].astype(np.float32) / 255.0
        img = composite_bg(img, person, ex.plate(bgk, t_abs))
    return img


def render(spec: Spec, src: Source, out_video: Path, clean: bool = False, metrics: bool = True) -> dict:
    """Writes the video-only master (no type, no audio). clean=True: no punch / flash / split fx / grade (before-after files).
    Shot.seq = [(beat_offset, state)] with state keys face (orig|swap), color, bg, still; every change inside a shot is a
    0.24 s wipe on the same frame (hair colour sweep, face swap reveal, background teleport, outfit-reference reveal)."""
    ex = Extras()
    total = spec.total_beats * GRID.period
    n = int(round(total * FPS))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "10",
                             "-pix_fmt", "yuv420p", str(out_video)], stdin=subprocess.PIPE)
    shots = spec.shots
    log: dict = {"frames": n, "hair_rows": []}
    prev_head = None
    for k in range(n):
        tau = k / FPS
        beat = tau / GRID.period
        sh = next((s for s in shots if s.beat0 <= beat < s.beat1), shots[-1])
        t_in = tau - sh.beat0 * GRID.period
        d = (sh.beat1 - sh.beat0) * GRID.period
        if sh.card:
            frame = np.zeros((OUT_H, OUT_W, 3), np.uint8)
            ys = np.linspace(0, 1, OUT_H, dtype=np.float32)[:, None]
            frame[:, :, 0] = (28 + 30 * ys).astype(np.uint8)
            frame[:, :, 1] = (14 + 10 * ys).astype(np.uint8)
            frame[:, :, 2] = (20 + 14 * ys).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
            continue
        if sh.video:
            cap = ex.cap(sh.video)
            fi = int(min(cap.get(cv2.CAP_PROP_FRAME_COUNT) - 1, max(0, (sh.video_t0 + t_in) * FPS)))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            okv, vf = cap.read()
            vz = sh.vzoom[0] + (sh.vzoom[1] - sh.vzoom[0]) * min(1.0, t_in / d)
            if okv and vz > 1.001:
                vf = _cover(vf, OUT_W, OUT_H, (0.5, 0.45), vz)
            frame = vf if okv else np.zeros((OUT_H, OUT_W, 3), np.uint8)
            if not clean and t_in < 0.2:
                frame = fx.flash(frame, 0.25 * math.exp(-t_in / 0.05))
            proc.stdin.write(frame.tobytes())
            continue
        s_t = sh.src_t + fx.ramp_source_time(t_in, sh.knots)
        img_swap, idx = src.frame(s_t, "swap")
        img_orig, _ = src.frame(s_t, "orig")
        head = src.head(idx)
        head = head if prev_head is None else (0.55 * head[0] + 0.45 * prev_head[0], 0.55 * head[1] + 0.45 * prev_head[1])
        prev_head = head
        al = src.alpha(idx)
        zoom = sh.zoom0 + (sh.zoom1 - sh.zoom0) * min(1.0, t_in / d)
        if not clean:
            zoom *= fx.punch_scale(t_in, 0.0, peak=0.05 + 0.07 * sh.hit, decay=0.14)
        seq = sh.seq
        if seq is None:                                           # legacy: colour (+ wipe from prev_color) on the swapped face
            seq = [(0.0, {"color": sh.color})]
            if sh.prev_color is not None:
                seq = [(0.0, {"color": sh.prev_color}), (0.0001, {"color": sh.color})]
        cur_i = max(i for i, (bo, _s) in enumerate(seq) if bo * GRID.period <= t_in + 1e-9)
        cur = seq[cur_i][1]
        t_sw = t_in - seq[cur_i][0] * GRID.period
        prev = seq[cur_i - 1][1] if cur_i > 0 else None

        def live_or_still(st):
            if st.get("still"):
                return ex.still(st["still"], t_in, d), True
            im720 = compose(src, ex, st, tau, al, idx, img_swap, img_orig)
            return crop_to_canvas(im720, head, zoom)[0], False

        frame, is_still = live_or_still(cur)
        hpos = crop_to_canvas(img_swap, head, zoom)[1]
        wp = 1.0 if prev is None else t_sw / 0.24
        if prev is not None and wp < 1.0:
            a_fr, _ = live_or_still(prev)
            kind = ["diag", "circle", "h", "v"][int(sh.beat0 + cur_i) % 4]
            m = fx.wipe_mask(OUT_H, OUT_W, fx.ease_in_out(wp), kind, soft=0.06)[:, :, None]
            frame = (a_fr.astype(np.float32) * (1 - m) + frame.astype(np.float32) * m).astype(np.uint8)
        if sh.split:
            before, _ = crop_to_canvas(img_swap if not sh.split_orig else img_orig, head, zoom)
            p = 1.0 if clean else fx.ease_out_cubic(min(1.0, t_in / 0.25))
            frame = fx.split_before_after(before, frame, 1.0 - 0.5 * p, line=0 if clean else 6)
        if metrics and not clean and not is_still and cur.get("color") in src.looks and wp >= 1.0 and k % 3 == 0:
            col = cur["color"]
            im_ = compose(src, ex, {"color": col, "face": cur.get("face")}, tau, al, idx, img_swap, img_orig)
            z = np.load(CACHE / f"{idx:05d}.npz")
            sk = z["skin"][:, 280:1000].astype(np.float32) / 255.0
            pe = z["person"][:, 280:1000].astype(np.float32) / 255.0
            st_ = src.looks[col].stats
            fm = hv.frame_metrics(img_swap, im_, al, sk, pe, (st_["L_mean"], st_["a_mean"], st_["b_mean"]))
            fm["color"] = col
            log["hair_rows"].append(fm)
        if not clean:
            if t_in < 0.30 and sh.hit > 0 and not is_still:
                st = sh.hit
                dx = int(round(14 * st * math.exp(-t_in / 0.07)))
                if dx >= 1:
                    frame = face_protect(fx.rgb_split(frame, dx, 0), frame, hpos)
                fl = (0.26 * math.exp(-t_in / 0.045)) if st >= 1 else 0.0
                if st >= 2:
                    fl = 0.5 * math.exp(-t_in / 0.07)
                frame = face_protect(fx.flash(frame, fl), frame, hpos)       # the flash never washes out the face
            elif t_in < 0.30 and is_still:
                frame = fx.flash(frame, 0.3 * math.exp(-t_in / 0.05))
            frame = grade(frame)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    return log


def texts_to_filters(spec: Spec) -> list:
    import edit_core as ec
    out = []
    for text, b0, b1, kw in spec.texts:
        out.append(ec.Text(text, round(b0 * GRID.period, 4), round(b1 * GRID.period, 4), kw))
    return out


def finish(video: Path, spec: Spec, out: Path, crf: int = 12) -> None:
    """Kinetic type + the track's audio from beat 0 (the drop lands where the grid says) + delivery encode."""
    import edit_core as ec
    total = spec.total_beats * GRID.period
    audio = out.with_suffix(".wav")
    if spec.audio_loop:
        segs = bg.audio_loop_plan(GRID, total, spec.audio_loop[0], spec.audio_loop[1])
        ec.build_audio(TRACK, segs, audio, fade_out=0.8)
    else:
        ec.build_audio(TRACK, [(GRID.phase, total)], audio, fade_out=0.5)
    ec.final_pass(video, audio, texts_to_filters(spec), out, crf=crf, maxrate="30M", duration=total)
    audio.unlink(missing_ok=True)


# ------------------------------------------------------------------ presets
#: good source spans of final-v2-clip1 (arcface >= 0.66 to her photos at every sampled frame; gates/base-clip1.json)
SPANS = {"A": (2.47, 3.93), "B": (4.80, 6.13), "C": (7.80, 8.80), "D": (0.53, 1.27)}   # C = flame overlay on the face: not used


def window(span: str, dur: float, knots, offset: float = 0.0) -> float:
    a, b = SPANS[span]
    need = consume(dur, knots)
    s0 = a + offset
    if s0 + need > b:
        s0 = max(a, b - need)
    return s0


def hair_cycle() -> Spec:
    """(b) hair-colour cycle on the beat: natural 4 beats, 8 colours x 1 beat, 4 x 2 beats, platinum hero on the drop,
    before | after, end card. Only source spans A and B (clean light, no flame overlay on the face) are used."""
    P = GRID.period
    shots: list[Shot] = []
    wipes = ["diag", "circle", "h", "v"]

    def add(b0, nb, c, span, off, prev, hit=1.0, z=(1.0, 1.07), fast=1.5, slow=0.55, const=None):
        d = nb * P
        kn = [(0.0, const), (d, const)] if const else knots_ramp(d, fast, slow)
        shots.append(Shot(b0, b0 + nb, window(span, d, kn, off), kn, c, prev_color=prev, wipe=wipes[int(b0) % 4],
                          zoom0=z[0], zoom1=z[1], hit=hit))

    add(0, 2, None, "A", 0.0, None, 0.6, (1.0, 1.06))
    add(2, 2, None, "B", 0.0, None, 0.8, (1.1, 1.0))
    quick = ["pink", "platinum", "blue", "red", "pink", "platinum", "blue", "red"]
    spans = [("A", 0.0), ("B", 0.2), ("A", 0.5), ("B", 0.55), ("A", 0.25), ("B", 0.0), ("A", 0.75), ("B", 0.35)]
    prev, beat = "natural", 4
    for c, (sp, off) in zip(quick, spans):
        add(beat, 1, c, sp, off, prev, 1.0, (1.0, 1.09) if beat % 2 == 0 else (1.13, 1.03), 1.3, 0.7)
        prev, beat = c, beat + 1
    mid = [("pink", "A", 0.1), ("platinum", "B", 0.15), ("blue", "A", 0.4), ("red", "B", 0.3)]
    for c, sp, off in mid:
        add(beat, 2, c, sp, off, prev, 1.0, (1.0, 1.08) if beat % 4 == 0 else (1.12, 1.0))
        prev, beat = c, beat + 2
    add(20, 4, "platinum", "A", 0.0, prev, 2.0, (1.0, 1.12), const=0.72)       # the drop: slow-mo hero shot
    add(24, 2, "natural", "B", 0.1, "platinum", 1.0, (1.1, 1.0))
    kn1 = [(0.0, 1.0), (2 * P, 1.0)]
    shots.append(Shot(26, 28, window("A", 2 * P, kn1, 0.3), kn1, "platinum", split=True, zoom0=1.0, zoom1=1.0, hit=0.6))
    shots.append(Shot(28, 30, 0.0, kn1, None, card=True))
    T = []
    bold = {"font": fx.FONT_BOLD}
    T.append(("GENJUTSU LIVE", 0, 26, dict(size=44, x="(w-text_w)/2", y="70", color="white", border=3, pop=0.2, **bold)))
    T.append(("HAIR COLOUR", 0.0, 3.9, dict(size=96, x="(w-text_w)/2", y="h-520", color="white", border=5, pop=0.18, **bold)))
    T.append(("ON THE BEAT", 1.0, 3.9, dict(size=96, x="(w-text_w)/2", y="h-410", color="0xffd23c", border=5, pop=0.18, **bold)))
    names = {"copper": ("COPPER", "0xe8793b"), "blue": ("BLUE", "0x4d8cff"), "pink": ("PINK", "0xff6fb5"), "violet": ("VIOLET", "0xa87bff"),
             "red": ("RED", "0xff3b3b"), "teal": ("TEAL", "0x2fe0d0"), "blonde": ("BLONDE", "0xffd98a"),
             "burgundy": ("BURGUNDY", "0xd23a5e"), "chocolate": ("CHOCOLATE", "0xc08a5a"), "green": ("GREEN", "0x3be07a"),
             "platinum": ("PLATINUM", "0xf2f0ea")}
    for sh in shots:
        c = sh.color
        if not c or sh.beat0 < 4 or sh.beat0 >= 24:
            continue
        key = {"purple": "violet"}.get(c, c)
        label, col = names.get(key, (key.upper(), "white"))
        T.append((label, sh.beat0 + 0.03, sh.beat1 - 0.04, dict(size=118 if len(label) < 8 else 92, x="(w-text_w)/2", y="h-470",
                                                              color=col, border=6, pop=0.04, fade_out=0.02, box=True, **bold)))
    T.append(("ORIGINAL", 24.0, 26.0, dict(size=96, x="(w-text_w)/2", y="h-470", color="white", border=6, pop=0.12, **bold)))
    T.append(("BEFORE", 26.1, 27.9, dict(size=54, x="w*0.25-text_w/2", y="h-330", color="white", border=4, pop=0.1, **bold)))
    T.append(("AFTER", 26.1, 27.9, dict(size=54, x="w*0.75-text_w/2", y="h-330", color="0xffd23c", border=4, pop=0.1, **bold)))
    T.append(("GENJUTSU LIVE", 28.0, 30, dict(size=92, x="(w-text_w)/2", y="h*0.40", color="white", border=0, pop=0.2, **bold)))
    T.append(("hair colour: any shade, same take", 28.2, 30, dict(size=44, x="(w-text_w)/2", y="h*0.40+120", color="0xffd23c", border=0, pop=0.2, **bold)))
    T.append(("face: FaceFusion  |  hair: soft-mask Lab recolour  |  rendered locally", 28.2, 30,
              dict(size=30, x="(w-text_w)/2", y="h*0.86", color="0xcfcfcf", border=0, pop=0.2, **bold)))
    return Spec(shots, T, 30.0, "hair_cycle")


def hv_name(c: str) -> str:
    return "purple" if c == "violet" else c


def _T(T, text, b0, b1, size=96, x="(w-text_w)/2", y="h-470", color="white", border=5, pop=0.12, **kw):
    T.append((text, b0, b1, dict(size=size, x=x, y=y, color=color, border=border, pop=pop, font=fx.FONT_BOLD, **kw)))


def _common_text(T, name="GENJUTSU LIVE", end=26):
    _T(T, name, 0, end, size=44, y="70", border=3, pop=0.2)


def _end_card(T, line1, line2, honest):
    _T(T, "GENJUTSU LIVE", 28.0, 30, size=92, y="h*0.40", border=0, pop=0.2)
    _T(T, line1, 28.2, 30, size=44, y="h*0.40+120", color="0xffd23c", border=0, pop=0.2)
    _T(T, honest, 28.2, 30, size=30, y="h*0.86", color="0xcfcfcf", border=0, pop=0.2)


def identity_swap() -> Spec:
    """(a) one-take identity swap: the original face wipes into hers on the SAME take (slow-mo), glitch-compare on the beats,
    hero shot on the drop, clean original | swapped split."""
    P = GRID.period
    S: list[Shot] = []
    c = [(0.0, 0.40), (4 * P, 0.40)]
    a0, a1 = SPANS["A"]
    S.append(Shot(0, 4, a0, c, seq=[(0, {"face": "orig"})], zoom0=1.0, zoom1=1.08, hit=0.6))
    S.append(Shot(4, 8, a0 + 4 * P * 0.40, c, seq=[(0, {"face": "orig"}), (0.0001, {"face": "swap"})],
                  zoom0=1.08, zoom1=1.14, hit=1.5))
    kn = knots_ramp(2 * P, 1.3, 0.7)
    for i, (beat, off, f0, f1) in enumerate([(8, 0.0, "orig", "swap"), (10, 0.3, "swap", "orig"), (12, 0.1, "orig", "swap"),
                                             (14, 0.4, "swap", "orig")]):
        S.append(Shot(beat, beat + 2, window("B", 2 * P, kn, off), kn, seq=[(0, {"face": f0}), (1, {"face": f1})],
                      zoom0=1.0 if i % 2 == 0 else 1.12, zoom1=1.1 if i % 2 == 0 else 1.02, hit=1.0))
    S.append(Shot(16, 18, window("A", 2 * P, kn, 0.0), kn, seq=[(0, {"face": "swap"})], zoom0=1.05, zoom1=1.12, hit=1.0))
    S.append(Shot(18, 20, window("B", 2 * P, kn, 0.2), kn, seq=[(0, {"face": "orig"}), (0.0001, {"face": "swap"})],
                  zoom0=1.12, zoom1=1.0, hit=1.0))
    S.append(Shot(20, 24, a0, [(0.0, 0.72), (4 * P, 0.72)], seq=[(0, {"face": "swap"})], zoom0=1.0, zoom1=1.14, hit=2.0))
    S.append(Shot(24, 26, window("B", 2 * P, kn, 0.1), kn, seq=[(0, {"face": "swap"})], zoom0=1.1, zoom1=1.0, hit=0.8))
    k1 = [(0.0, 1.0), (2 * P, 1.0)]
    S.append(Shot(26, 28, window("A", 2 * P, k1, 0.3), k1, seq=[(0, {"face": "swap"})], split=True, split_orig=True,
                  zoom0=1.0, zoom1=1.0, hit=0.6))
    S.append(Shot(28, 30, 0.0, k1, card=True))
    T: list = []
    _common_text(T)
    _T(T, "ONE TAKE", 0, 3.9, y="h-520")
    _T(T, "ORIGINAL FACE", 0.1, 3.9, y="h-410", color="0xffd23c", size=70)
    _T(T, "NOW: HER FACE", 4.0, 7.9, y="h-470", color="0xffd23c")
    _T(T, "SAME MOVES", 8, 16, y="h-470")
    _T(T, "ANY FACE", 16, 20, y="h-470", color="0xffd23c")
    _T(T, "HER FACE", 20, 24, y="h-470", size=118)
    _T(T, "ORIGINAL", 26.1, 27.9, y="h-330", size=54, border=4, x="w*0.25-text_w/2")
    _T(T, "SWAPPED", 26.1, 27.9, y="h-330", size=54, border=4, x="w*0.75-text_w/2", color="0xffd23c")
    _end_card(T, "same video, any face", "face: FaceFusion  |  rendered locally", "")
    return Spec(S, T, 30.0, "identity_swap")


def bg_teleport() -> Spec:
    """(d) background teleport: RVM person matte over local plates (neon city, studio, graded neon), wipe on every beat pair."""
    P = GRID.period
    S: list[Shot] = []
    kn = knots_ramp(2 * P, 1.3, 0.7)
    S.append(Shot(0, 2, window("A", 2 * P, kn, 0.0), kn, seq=[(0, {"bg": "real"})], zoom0=1.0, zoom1=1.07, hit=0.6))
    S.append(Shot(2, 4, window("B", 2 * P, kn, 0.0), kn, seq=[(0, {"bg": "real"})], zoom0=1.1, zoom1=1.0, hit=0.8))
    order = ["neon", "studio", "neon2", "real", "neon", "studio", "neon2", "real"]
    spans = [("A", 0.0), ("B", 0.3), ("A", 0.4), ("B", 0.1), ("A", 0.2), ("B", 0.5), ("A", 0.45), ("B", 0.0)]
    prev, beat = "real", 4
    for i, (bgk, (sp, off)) in enumerate(zip(order, spans)):
        S.append(Shot(beat, beat + 2, window(sp, 2 * P, kn, off), kn, seq=[(0, {"bg": prev}), (0.0001, {"bg": bgk})],
                      zoom0=1.0 if i % 2 == 0 else 1.12, zoom1=1.1 if i % 2 == 0 else 1.02, hit=1.0))
        prev, beat = bgk, beat + 2
    S.append(Shot(20, 24, SPANS["A"][0], [(0.0, 0.72), (4 * P, 0.72)], seq=[(0, {"bg": "real"}), (0.0001, {"bg": "neon"})],
                  zoom0=1.0, zoom1=1.14, hit=2.0))
    S.append(Shot(24, 26, window("B", 2 * P, kn, 0.1), kn, seq=[(0, {"bg": "studio"})], zoom0=1.1, zoom1=1.0, hit=0.8))
    k1 = [(0.0, 1.0), (2 * P, 1.0)]
    S.append(Shot(26, 28, window("A", 2 * P, k1, 0.3), k1, seq=[(0, {"bg": "neon"})], split=True, zoom0=1.0, zoom1=1.0, hit=0.6))
    S.append(Shot(28, 30, 0.0, k1, card=True))
    T: list = []
    _common_text(T)
    _T(T, "WHERE TO?", 0, 3.9, y="h-470", size=110)
    lab = {"neon": ("NEON CITY", "0x39e8ff"), "studio": ("STUDIO", "0xffffff"), "neon2": ("NEON 2", "0xff5fd2"),
           "real": ("REAL ROOM", "0xffd23c")}
    beat = 4
    for bgk in order:
        _T(T, lab[bgk][0], beat + 0.03, beat + 1.96, color=lab[bgk][1], box=True, pop=0.05, fade_out=0.02)
        beat += 2
    _T(T, "NEON CITY", 20, 24, size=118, color="0x39e8ff", box=True)
    _T(T, "STUDIO", 24, 26, color="white", box=True)
    _T(T, "BEFORE", 26.1, 27.9, y="h-330", size=54, border=4, x="w*0.25-text_w/2")
    _T(T, "AFTER", 26.1, 27.9, y="h-330", size=54, border=4, x="w*0.75-text_w/2", color="0xffd23c")
    _end_card(T, "background teleport", "person matte: RVM  |  plates: local  |  rendered locally", "")
    return Spec(S, T, 30.0, "bg_teleport")


def outfit_reveal() -> Spec:
    """(c) outfit-change reveal. No clean VACE run exists (stage 2 failed T3/T6, face regenerated), so the outfits are shown as
    labelled REFERENCE LOOKS (generated stills of her) revealed by mask wipes against the live take."""
    P = GRID.period
    S: list[Shot] = []
    kn = knots_ramp(2 * P, 1.3, 0.7)
    S.append(Shot(0, 2, window("A", 2 * P, kn, 0.0), kn, seq=[(0, {})], zoom0=1.0, zoom1=1.07, hit=0.6))
    S.append(Shot(2, 4, window("B", 2 * P, kn, 0.0), kn, seq=[(0, {})], zoom0=1.1, zoom1=1.0, hit=0.8))
    looks = ["navy", "burgundy", "bob", "ponytail", "gray", "wavy"]
    beat = 4
    live = [("A", 0.2), ("B", 0.3), ("A", 0.4), ("B", 0.1), ("A", 0.5), ("B", 0.45)]
    names = []
    for i, k in enumerate(looks[:4]):
        S.append(Shot(beat, beat + 2, window(*live[i][:1], 2 * P, kn, live[i][1]), kn, seq=[(0, {}), (0.0001, {"still": k})],
                      zoom0=1.0, zoom1=1.0, hit=1.0))
        names.append((k, beat))
        beat += 2
        S.append(Shot(beat, beat + 1, window(*live[(i + 2) % 6][:1], P, [(0.0, 1.2), (P, 1.0)], live[(i + 2) % 6][1]),
                      [(0.0, 1.2), (P, 1.0)], seq=[(0, {})], zoom0=1.0, zoom1=1.08, hit=0.7))
        beat += 1
    # beats 4..16 used: 4 x (2+1) = 12 -> beat is 16
    S.append(Shot(beat, beat + 2, window("B", 2 * P, kn, 0.2), kn, seq=[(0, {})], zoom0=1.12, zoom1=1.0, hit=1.0))
    beat += 2                                                                  # 18
    S.append(Shot(beat, beat + 2, window("A", 2 * P, kn, 0.0), kn, seq=[(0, {}), (0.0001, {"still": "gray"})],
                  zoom0=1.0, zoom1=1.0, hit=1.0))
    names.append(("gray", beat))
    S.append(Shot(20, 24, SPANS["A"][0], [(0.0, 0.72), (4 * P, 0.72)], seq=[(0, {}), (0.0001, {"still": "navy"})],
                  zoom0=1.0, zoom1=1.0, hit=2.0))
    S.append(Shot(24, 26, window("B", 2 * P, kn, 0.1), kn, seq=[(0, {"still": "navy"}), (0.0001, {})], zoom0=1.1, zoom1=1.0, hit=0.8))
    k1 = [(0.0, 1.0), (2 * P, 1.0)]
    S.append(Shot(26, 28, window("A", 2 * P, k1, 0.3), k1, seq=[(0, {})], zoom0=1.0, zoom1=1.0, hit=0.6))
    S.append(Shot(28, 30, 0.0, k1, card=True))
    T: list = []
    _common_text(T)
    _T(T, "NEW OUTFIT?", 0, 3.9, size=110)
    nm = {"navy": "NAVY BLAZER", "burgundy": "BURGUNDY", "bob": "BOB + BLAZER", "ponytail": "PONYTAIL", "gray": "GRAY BLAZER"}
    for k, b in names:
        _T(T, nm.get(k, k.upper()), b + 0.2, b + 1.95, size=84, color="0xffd23c", box=True, pop=0.05, fade_out=0.02)
        _T(T, "reference look (generated still)", b + 0.2, b + 1.95, size=30, y="h-330", color="0xe0e0e0", border=3, pop=0.05,
           fade_out=0.02)
    _T(T, "NAVY BLAZER", 20.2, 24, size=100, color="0xffd23c", box=True)
    _T(T, "reference look (generated still)", 20.2, 24, size=30, y="h-330", color="0xe0e0e0", border=3)
    _T(T, "CLOTHES: TRIAL", 24, 26, size=70, color="0xffd23c", box=True)
    _end_card(T, "outfit change", "outfits shown: reference looks  |  video clothes swap: trial", "")
    return Spec(S, T, 30.0, "outfit_reveal")


def clean_before_after(color_cycle=("pink", "blue", "red", "platinum")) -> Spec:
    """Clean file: no effects, no type - left half = natural, right half = recoloured, same frames, 4 colours."""
    P = GRID.period
    S: list[Shot] = []
    k1 = [(0.0, 1.0), (1.4, 1.0)]
    beat = 0.0
    wins = [("A", 0.0), ("B", 0.0), ("A", 0.0), ("B", 0.0)]
    for c, (sp, off) in zip(color_cycle, wins):
        a, b = SPANS[sp]
        dur = (b - a) - 0.12
        nb = dur / P
        S.append(Shot(beat, beat + nb, a + off, [(0.0, 1.0), (dur, 1.0)], seq=[(0, {"color": c})], split=True,
                      zoom0=1.0, zoom1=1.0, hit=0.0))
        beat += nb
    return Spec(S, [], round(beat + 0.01, 3), "clean_before_after")


def studio_promo() -> Spec:
    """VIDEO 6 (45 s, 9:16) by the owner's plan: hook -> hair -> outfit -> hairstyle -> how it works (constructor
    screencast) -> CTA. 98 beats of the track's grid. Outfits / hairstyles are REFERENCE stills, labelled as such."""
    P = GRID.period
    S: list[Shot] = []
    kn = knots_ramp(2 * P, 1.3, 0.7)
    A0, B0 = SPANS["A"][0], SPANS["B"][0]
    # hook, beats 0-6: three quick transformations
    S.append(Shot(0, 2, window("A", 2 * P, kn, 0.0), kn, seq=[(0, {}), (0.8, {"color": "pink"})], zoom0=1.0, zoom1=1.08, hit=1.0))
    S.append(Shot(2, 4, window("B", 2 * P, kn, 0.0), kn, seq=[(0, {}), (0.8, {"color": "blue"})], zoom0=1.1, zoom1=1.0, hit=1.0))
    S.append(Shot(4, 6, window("A", 2 * P, kn, 0.3), kn, seq=[(0, {}), (0.8, {"still": "burgundy"})], zoom0=1.0, zoom1=1.0, hit=1.5))
    # hair, beats 6-26: split original | colour, 4 x 5 beats, slow-mo on the take
    prev = None
    for i, (c, sp) in enumerate([("pink", "A"), ("platinum", "B"), ("blue", "A"), ("red", "B")]):
        b0 = 6 + 5 * i
        d = 5 * P
        sp0 = SPANS[sp][0]
        spd = 0.58 if sp == "A" else 0.52
        kk = [(0.0, spd), (d, spd)]
        sq = [(0, {"color": prev}), (0.0001, {"color": c})] if prev else [(0, {"color": c})]
        S.append(Shot(b0, b0 + 5, sp0, kk, seq=sq, split=True, zoom0=1.0 if i % 2 == 0 else 1.1, zoom1=1.1 if i % 2 == 0 else 1.0,
                      hit=1.0))
        prev = c
    # outfit, beats 26-44: live take (2 beats) -> reference still (4 beats), x3
    for i, k in enumerate(["navy", "gray", "burgundy"]):
        b0 = 26 + 6 * i
        sp = "A" if i % 2 == 0 else "B"
        S.append(Shot(b0, b0 + 2, window(sp, 2 * P, kn, 0.1 * i), kn, seq=[(0, {})], zoom0=1.0, zoom1=1.08, hit=1.0))
        S.append(Shot(b0 + 2, b0 + 6, window(sp, 2 * P, kn, 0.1 * i), kn, seq=[(0, {}), (0.0001, {"still": k})], hit=1.0))
    # hairstyle, beats 44-55: five reference stills
    for i, (k, nb) in enumerate([("loose", 2), ("ponytail", 2), ("bun", 2), ("bob", 2), ("wavy", 3)]):
        b0 = 44 + 2 * i
        S.append(Shot(b0, b0 + nb, A0, kn, seq=[(0, {"still": k})], hit=1.0))
    # how it works, beats 55-77: the constructor screencast (3 parts, different zoom)
    vid = str(VID / "ads-20261011" / "prep" / "screencast" / "screencast.mp4")
    d1, d2 = 7, 7
    S.append(Shot(55, 55 + d1, 0, kn, video=vid, video_t0=0.0, vzoom=(1.0, 1.0), hit=0.8))
    S.append(Shot(62, 62 + d2, 0, kn, video=vid, video_t0=7 * P, vzoom=(1.0, 1.0), hit=0.8))
    S.append(Shot(69, 77, 0, kn, video=vid, video_t0=14 * P, vzoom=(1.0, 1.0), hit=0.8))
    # CTA, beats 77-98: her take in the neon city, 4 looks, end card
    cta = [("neon", "pink", "A", 0.0), ("neon2", "blue", "B", 0.0), ("neon", "red", "A", 0.4), ("studio", "platinum", "B", 0.3)]
    pb, pc = "real", None
    for i, (bgk, c, sp, off) in enumerate(cta):
        b0 = 77 + 4 * i
        k4 = [(0.0, 0.62), (4 * P, 0.62)]
        S.append(Shot(b0, b0 + 4, window(sp, 4 * P, k4, off), k4,
                      seq=[(0, {"bg": pb, "color": pc}), (0.0001, {"bg": bgk, "color": c})],
                      zoom0=1.0 if i % 2 == 0 else 1.1, zoom1=1.1 if i % 2 == 0 else 1.0, hit=1.5 if i == 0 else 1.0))
        pb, pc = bgk, c
    k1 = [(0.0, 1.0), (4 * P, 1.0)]
    S.append(Shot(93, 98, 0.0, k1, card=True))
    T: list = []
    _T(T, "GENJUTSU STUDIO", 0, 55, size=44, y="70", border=3, pop=0.2)
    _T(T, "GENJUTSU STUDIO", 77, 93, size=44, y="70", border=3, pop=0.2)
    _T(T, "ОДНО ВИДЕО.", 0.0, 6, y="h-560", size=100)
    _T(T, "ЛЮБОЙ ОБРАЗ.", 1.0, 6, y="h-450", size=100, color="0xffd23c")
    _T(T, "ЦВЕТ ВОЛОС. ДВИЖЕНИЕ ТО ЖЕ.", 6, 26, size=54, y="150", border=4)
    nmh = {"pink": "РОЗОВЫЙ", "platinum": "ПЛАТИНА", "blue": "СИНИЙ", "red": "КРАСНЫЙ"}
    for i, c in enumerate(["pink", "platinum", "blue", "red"]):
        b0 = 6 + 5 * i
        _T(T, nmh[c], b0 + 0.1, (b0 + 5) - 0.1, size=104, y="h-470", box=True, pop=0.06, fade_out=0.03,
           color={"pink": "0xff6fb5", "platinum": "0xf2f0ea", "blue": "0x4d8cff", "red": "0xff3b3b"}[c])
    _T(T, "ОРИГИНАЛ", 6, 26, size=44, y="h-300", border=4, x="w*0.25-text_w/2")
    _T(T, "РЕЗУЛЬТАТ", 6, 26, size=44, y="h-300", border=4, x="w*0.75-text_w/2", color="0xffd23c")
    _T(T, "ОДЕЖДА. ЛИЦО НЕ МЕНЯЕТСЯ.", 26, 44, size=54, y="150", border=4)
    for i, k in enumerate(["ТЁМНО-СИНИЙ", "СЕРЫЙ", "БОРДО"]):
        b0 = 28 + 6 * i
        _T(T, k, b0 + 0.1, (b0 + 4) - 0.1, size=96, y="h-470", box=True, color="0xffd23c", pop=0.06, fade_out=0.03)
        _T(T, "референс: кадр, а не рендер этого видео", b0 + 0.1, (b0 + 4) - 0.1, size=30, y="h-330",
           color="0xe0e0e0", border=3, pop=0.06, fade_out=0.03)
    _T(T, "ПРИЧЁСКА ЗА СЕКУНДУ.", 44, 55, size=60, y="150", border=4)
    _T(T, "референсы (сгенерированные кадры)", 44, 55, size=32, y="h-330", color="0xe0e0e0", border=3)
    _T(T, "4 КЛИКА. БЕЗ МОКАПА.", 55, 77, size=60, y="h-170", border=4, color="0xffd23c")
    _T(T, "конструктор Genjutsu, считается на этом ПК", 55, 77, size=28, y="h-100", color="0xcfcfcf", border=3)
    cn = ["НЕОН", "НЕОН-2", "НЕОН", "СТУДИЯ"]
    for i, k in enumerate(cn):
        b0 = 77 + 4 * i
        _T(T, k, b0 + 0.1, (b0 + 4) - 0.1, size=100, y="h-470", box=True, pop=0.06, fade_out=0.03)
    _T(T, "ТВОЙ ОБРАЗ.", 93, 98, y="h*0.34", size=96)
    _T(T, "ТВОЁ ЛИЦО.", 94, 98, y="h*0.34+120", size=96, color="0xffd23c")
    _T(T, "GENJUTSU STUDIO", 93.5, 98, y="h*0.34+300", size=70, border=0)
    _T(T, "лицо: FaceFusion  |  обучение моделей на арендованной видеокарте", 94, 98, y="h*0.80", size=30,
       color="0xcfcfcf", border=0)
    _T(T, "видео рендерится локально  |  одежда в видео: пробный", 94, 98, y="h*0.80+50", size=30,
       color="0xcfcfcf", border=0)
    return Spec(S, T, 98.0, "studio_promo", audio_loop=(20, 8))


def showreel_spec() -> Spec:
    T: list = []
    _T(T, "FOUR TRICKS", 28.0, 30, size=96, y="h*0.36", border=0, pop=0.2)
    _T(T, "ONE TAKE", 28.3, 30, size=96, y="h*0.36+120", color="0xffd23c", border=0, pop=0.2)
    _T(T, "face: FaceFusion  |  rendered locally  |  clothes: reference looks", 28.4, 30, size=34, y="h*0.80",
       color="0xcfcfcf", border=0, pop=0.2)
    return Spec([], T, 30.0, "showreel")


def render_showreel(parts: dict, out_video: Path) -> dict:
    """(e) showreel: beat-grid cuts through the four finished ads with speed ramps, punch-ins, RGB split (face protected),
    whip smear and flash on every cut. `parts` = {key: mp4 path}; segments reference them by key and source second."""
    P = GRID.period
    plan = [  # (beat0, beats, key, source second, fast, slow, zoom0, zoom1)
        (0, 2, "a", 1.2, 1.4, 0.6, 1.0, 1.08), (2, 2, "b", 2.0, 1.3, 0.7, 1.1, 1.0), (4, 2, "d", 2.0, 1.3, 0.7, 1.0, 1.08),
        (6, 2, "c", 2.0, 1.3, 0.7, 1.1, 1.0), (8, 1, "a", 5.6, 1.2, 0.8, 1.0, 1.1), (9, 1, "b", 3.4, 1.2, 0.8, 1.12, 1.0),
        (10, 1, "d", 6.0, 1.2, 0.8, 1.0, 1.1), (11, 1, "c", 5.0, 1.2, 0.8, 1.12, 1.0), (12, 1, "b", 4.4, 1.2, 0.8, 1.0, 1.1),
        (13, 1, "d", 8.3, 1.2, 0.8, 1.12, 1.0), (14, 1, "a", 8.3, 1.2, 0.8, 1.0, 1.1), (15, 1, "c", 7.2, 1.2, 0.8, 1.12, 1.0),
        (16, 1, "b", 2.8, 1.1, 0.9, 1.0, 1.1), (17, 1, "d", 4.6, 1.1, 0.9, 1.1, 1.0), (18, 1, "a", 6.0, 1.1, 0.9, 1.0, 1.1),
        (19, 1, "c", 9.0, 1.1, 0.9, 1.1, 1.0), (20, 4, "b", 9.0, 0.72, 0.72, 1.0, 1.1), (24, 2, "d", 9.4, 1.0, 0.7, 1.08, 1.0),
        (26, 2, "a", 10.4, 1.0, 0.8, 1.0, 1.0),
    ]
    caps = {k: cv2.VideoCapture(str(v)) for k, v in parts.items()}
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "10",
                             "-pix_fmt", "yuv420p", str(out_video)], stdin=subprocess.PIPE)
    n = int(round(30 * P * FPS))
    for k in range(n):
        tau = k / FPS
        beat = tau / P
        seg = next((x for x in plan if x[0] <= beat < x[0] + x[1]), None)
        if seg is None:                                           # end card
            frame = np.zeros((OUT_H, OUT_W, 3), np.uint8)
            ys = np.linspace(0, 1, OUT_H, dtype=np.float32)[:, None]
            frame[:, :, 0] = (28 + 30 * ys).astype(np.uint8)
            frame[:, :, 1] = (14 + 10 * ys).astype(np.uint8)
            frame[:, :, 2] = (20 + 14 * ys).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
            continue
        b0, nb, key, s0, fast, slow, z0, z1 = seg
        d = nb * P
        t_in = tau - b0 * P
        kn = [(0.0, fast), (0.45 * d, 1.0), (d, slow)] if abs(fast - slow) > 1e-6 else [(0.0, fast), (d, fast)]
        st = s0 + fx.ramp_source_time(t_in, kn)
        cap = caps[key]
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(min(cap.get(cv2.CAP_PROP_FRAME_COUNT) - 1, max(0, st * FPS))))
        ok, fr = cap.read()
        if not ok:
            fr = np.zeros((OUT_H, OUT_W, 3), np.uint8)
        z = (z0 + (z1 - z0) * min(1.0, t_in / d)) * fx.punch_scale(t_in, 0.0, peak=0.08, decay=0.14)
        fr = _cover(fr, OUT_W, OUT_H, (0.5, 0.45), z)
        if t_in < 0.30:
            dx = int(round(16 * math.exp(-t_in / 0.07)))
            if dx >= 1:
                fr = face_protect(fx.rgb_split(fr, dx, 0), fr, (OUT_W / 2, OUT_H * 0.30))
            if t_in < 0.07:
                fr = fx.whip_blur(fr, 0.05)
            fr = face_protect(fx.flash(fr, 0.30 * math.exp(-t_in / 0.045)), fr, (OUT_W / 2, OUT_H * 0.30))
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    proc.wait()
    return {"frames": n}


PRESETS = {"studio_promo": studio_promo, "hair_cycle": hair_cycle, "identity_swap": identity_swap, "bg_teleport": bg_teleport,
           "outfit_reveal": outfit_reveal, "clean_ba": clean_before_after}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", choices=sorted(PRESETS) + ["showreel"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default="")
    ap.add_argument("--parts", default="", help="showreel: a=...,b=...,c=...,d=... finished ad files")
    a = ap.parse_args(argv)
    if USE_FUSED:
        hf.low_priority()
    if a.preset == "showreel":
        spec = showreel_spec()
        out = Path(a.out)
        work = Path(a.work) if a.work else out.parent / "work"
        work.mkdir(parents=True, exist_ok=True)
        parts = dict(kv.split("=", 1) for kv in a.parts.split(","))
        render_showreel(parts, work / "showreel_master.mp4")
        finish(work / "showreel_master.mp4", spec, out)
        print("done", out)
        return 0
    spec = PRESETS[a.preset]()
    src = Source()
    out = Path(a.out)
    work = Path(a.work) if a.work else out.parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    master = work / f"{a.preset}_master.mp4"
    log = render(spec, src, master, clean=(a.preset == "clean_ba"))
    if a.preset == "clean_ba":                                   # no type, no audio: a plain H.264 copy
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out)], check=True)
    else:
        finish(master, spec, out)
    (work / f"{a.preset}_spec.json").write_text(json.dumps(
        {"name": spec.name, "grid": {"period": GRID.period, "phase": GRID.phase},
         "shots": [s.__dict__ for s in spec.shots], "hair_summary": hv.summarize(log["hair_rows"]) if log["hair_rows"] else None},
        indent=1, default=str), encoding="utf-8")
    print("done", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
