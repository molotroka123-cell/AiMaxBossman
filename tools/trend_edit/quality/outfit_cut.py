"""Ad (c) as REAL VIDEO: a beat-cut between real / face-swapped clips of the same woman in different outfits.

No clothes are repainted. The "outfit change" is a cut between clips that really show other clothes:
  tee    = ref-clip-01 (grey t-shirt), face swapped with the approved FaceFusion preset (v2 pass, already rendered)
  uni04 / uni05 = her OWN video circles (white shirt, red tie, vest): real footage, no swap
  pilot  = ref-clip-02 (black blazer + cap), face swapped (v2 pass, already rendered)
  dress  = ref-clip-03 (black dress), face swapped (v2 pass, already rendered)
Every cut is a MATCH CUT: the window of the incoming shot is chosen where head pose (yaw / pitch / roll from the 5 landmarks)
and head motion are closest to the last frame of the outgoing shot, and every shot is framed so the head lands on the same
canvas point at the same size (so the cut does not jump). Source spans are only taken where arcface >= 0.62 to her photos.

  <facefusion>/.venv/Scripts/python.exe quality/outfit_cut.py plan   -> writes plan.json (windows) and the SR job list
  <facefusion>/.venv/Scripts/python.exe quality/outfit_cut.py render -> master + final
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_ads as ba  # noqa: E402
import fx  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
Q = VID / "ads-20261011" / "quality"
GC = Q / "gates_c"
TEST = Path.home() / "Bossman" / "video-testset-private"
W, H, FPS = 1080, 1920, 30
P = ba.GRID.period
FACE_W = 380.0               # head width on the canvas for every shot (px): same size => the cut does not jump
HEAD_CX, HEAD_CY = 540.0, 760.0
ID_MIN = 0.62

CLIPS = {
    "tee": dict(path=VID / "v2-20261010" / "final-v2-clip1.mp4", crop=(280, 0, 720, 720), track="track_clip1.json", sr="sr_swap_f", srx=3,
                circle=False, tranges=[(2.45, 4.0), (4.78, 6.2)], valid=(32, 683)),
    "uni04": dict(path=TEST / "ref-clip-04.mp4", crop=(0, 0, 400, 400), track="track_ref04.json", sr="c_sr/uni04", srx=4, circle=True),
    "uni05": dict(path=TEST / "ref-clip-05.mp4", crop=(0, 0, 400, 400), track="track_ref05.json", sr="c_sr/uni05", srx=4, circle=True),
    "pilot": dict(path=VID / "v2-20261010" / "final-v2-clip2.mp4", crop=(0, 0, 1320, 1108), track="track_clip2.json", sr=None, srx=1,
                  circle=False, trange=(10.0, 15.0)),
    "btee": dict(path=VID / "v2-20261010" / "final-v2-clip2.mp4", crop=(0, 0, 1320, 1108), track="track_clip2.json", sr=None, srx=1,
                 circle=False, trange=(0.0, 9.6)),
    "dress": dict(path=VID / "v2-20261010" / "final-v2-pair.mp4", crop=(0, 0, 720, 1280), track="track_pair.json", sr=None, srx=1,
                  circle=False),
}


# ------------------------------------------------------------------ tracks
@dataclass
class Track:
    fps: float
    idx: np.ndarray            # tracked frame indices (sorted)
    cx: np.ndarray
    cy: np.ndarray
    fw: np.ndarray             # face box width
    yaw: np.ndarray
    pitch: np.ndarray
    roll: np.ndarray
    ident: np.ndarray

    @staticmethod
    def load(path: Path) -> "Track":
        d = json.loads(path.read_text(encoding="utf-8"))
        ks = sorted(int(k) for k in d["frames"])
        rows = [d["frames"][str(k)] for k in ks]
        box = np.array([r["box"] for r in rows], np.float32)
        lm = np.array([r["lm5"] for r in rows], np.float32)          # re, le, nose, rm, lm
        eye_c = 0.5 * (lm[:, 0] + lm[:, 1])
        eye_d = np.maximum(1.0, np.hypot(*(lm[:, 1] - lm[:, 0]).T))
        yaw = (lm[:, 2, 0] - eye_c[:, 0]) / eye_d
        pitch = (lm[:, 2, 1] - eye_c[:, 1]) / eye_d
        roll = np.arctan2(lm[:, 1, 1] - lm[:, 0, 1], lm[:, 1, 0] - lm[:, 0, 0])
        t = Track(d["fps"], np.array(ks), 0.5 * (box[:, 0] + box[:, 2]), 0.5 * (box[:, 1] + box[:, 3]), box[:, 2] - box[:, 0],
                  yaw, pitch, roll, np.array([r["id"] for r in rows], np.float32))
        return t

    def at(self, f: float) -> dict:
        def itp(a):
            return float(np.interp(f, self.idx, a))
        return {"cx": itp(self.cx), "cy": itp(self.cy), "fw": itp(self.fw), "yaw": itp(self.yaw), "pitch": itp(self.pitch),
                "roll": itp(self.roll)}

    def smooth_at(self, f: float, half: float = 2.0) -> dict:
        """Average of the track over +-half frames (removes detector jitter before framing)."""
        fs = np.linspace(f - half, f + half, 5)
        rows = [self.at(x) for x in fs]
        return {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}

    def good_runs(self, thr: float = ID_MIN, gap: float = 0.12, min_len: float = 0.5) -> list[tuple[float, float]]:
        step = float(np.median(np.diff(self.idx))) if len(self.idx) > 1 else 1.0
        ok = self.ident >= thr
        runs, start, last = [], None, None
        for i, f in enumerate(self.idx):
            if ok[i]:
                if start is None:
                    start = f
                elif (f - last) / self.fps > gap + step / self.fps:
                    runs.append((start, last))
                    start = f
                last = f
            elif start is not None and (f - last) / self.fps > gap + step / self.fps:
                runs.append((start, last))
                start = None
        if start is not None:
            runs.append((start, last))
        return [(a / self.fps, b / self.fps) for a, b in runs if (b - a) / self.fps >= min_len]


# ------------------------------------------------------------------ plan
@dataclass
class ShotPlan:
    b0: float
    nb: float
    clip: str
    kn: list
    z0: float = 1.0
    z1: float = 1.06
    hit: float = 1.0
    t0: float = 0.0           # chosen source start (s), filled by plan()
    cost: float = 0.0
    id_min: float = 0.0
    id_mean: float = 0.0


def shot_list() -> list[ShotPlan]:
    kn = ba.knots_ramp
    s = []

    def add(b0, nb, clip, fast=1.3, slow=0.7, z=(1.0, 1.07), hit=1.0, const=None):
        d = nb * P
        k = [(0.0, const), (d, const)] if const else kn(d, fast, slow)
        s.append(ShotPlan(b0, nb, clip, k, z[0], z[1], hit))
    add(0, 2, "tee", 1.2, 0.8, (1.0, 1.06), 0.6)
    add(2, 2, "uni04", 1.0, 0.9, (1.06, 1.0), 1.0)
    add(4, 1, "btee", 1.1, 0.9, (1.0, 1.08), 1.0)
    add(5, 2, "pilot", 1.2, 0.8, (1.08, 1.0), 1.0)
    add(7, 1, "uni05", 1.0, 0.9, (1.0, 1.08), 1.0)
    add(8, 2, "dress", 1.2, 0.8, (1.08, 1.0), 1.0)
    add(10, 1, "btee", 1.1, 0.9, (1.0, 1.08), 1.0)
    add(11, 2, "uni04", 1.0, 0.9, (1.08, 1.0), 1.0)
    add(13, 1, "pilot", 1.1, 0.9, (1.0, 1.08), 1.0)
    add(14, 1, "dress", 1.1, 0.9, (1.08, 1.0), 1.0)
    add(15, 1, "uni05", 1.0, 0.9, (1.0, 1.08), 1.0)
    add(16, 2, "tee", 1.2, 0.8, (1.08, 1.0), 1.0)
    add(18, 2, "pilot", 1.2, 0.8, (1.0, 1.08), 1.0)
    add(20, 4, "uni05", const=0.72, z=(1.0, 1.12), hit=2.0)            # the drop: slow-mo hero shot, real video of her
    add(24, 2, "tee", 1.1, 0.8, (1.1, 1.0), 0.8)
    add(26, 2, "dress", 1.0, 0.9, (1.0, 1.04), 0.6)
    return s


_CAPS: dict = {}
_FS: dict = {}


def face_sharp(clip: str, tr: "Track", t: float) -> float:
    """Laplacian variance of the face region at source time t (motion blur / crossfade detector)."""
    f = int(round(t * tr.fps))
    key = (clip, f)
    if key not in _FS:
        c = CLIPS[clip]
        cap = _CAPS.setdefault(clip, cv2.VideoCapture(str(c["path"])))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, im = cap.read()
        v = 0.0
        if ok:
            x, y, w, h = c["crop"]
            im = im[y:y + h, x:x + w]
            a = tr.at(f)
            r = 0.6 * a["fw"]
            x0, x1 = int(max(0, a["cx"] - r)), int(min(im.shape[1], a["cx"] + r))
            y0, y1 = int(max(0, a["cy"] - r)), int(min(im.shape[0], a["cy"] + r))
            if x1 - x0 > 8 and y1 - y0 > 8:
                g = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
                g = cv2.resize(g, (96, 96), interpolation=cv2.INTER_AREA)
                v = float(cv2.Laplacian(g, cv2.CV_32F).var())
        _FS[key] = v
    return _FS[key]


def pose_cost(a: dict, b: dict) -> float:
    return 2.0 * abs(a["yaw"] - b["yaw"]) + 1.5 * abs(a["pitch"] - b["pitch"]) + abs(a["roll"] - b["roll"]) / 0.35


def consume(sp: ShotPlan) -> float:
    return fx.ramp_source_time(sp.nb * P, sp.kn) - fx.ramp_source_time(0.0, sp.kn)


def plan(shots: list[ShotPlan] | None = None) -> list[ShotPlan]:
    tracks = {k: Track.load(GC / c["track"]) for k, c in CLIPS.items() if (GC / c["track"]).exists()}
    tr_range = {k: (c.get("tranges") or [c.get("trange", (0.0, 1e9))]) for k, c in CLIPS.items()}
    shots = shots if shots is not None else shot_list()
    used: dict[str, list[tuple[float, float]]] = {k: [] for k in tracks}
    clip_ref: dict[str, float] = {}
    for k, tr_ in tracks.items():
        vals = []
        for a0, b0 in tr_.good_runs():
            for t_ in np.arange(a0, b0, 0.25):
                vals.append(face_sharp(k, tr_, float(t_)))
        clip_ref[k] = float(np.percentile(vals, 60)) if vals else 1.0
    prev_pose = None
    for sp in shots:
        tr = tracks[sp.clip]
        need = consume(sp)
        best = None
        cand = [(max(a0, r0), min(b0, r1)) for a0, b0 in tr.good_runs() for r0, r1 in tr_range[sp.clip]]
        for (a, b) in cand:
            if b - a < need:
                continue
            t = a
            while t + need <= b + 1e-6:
                cost = 0.0
                if prev_pose is not None:
                    cost += pose_cost(prev_pose, tr.smooth_at(t * tr.fps))
                ov = sum(max(0.0, min(t + need, e) - max(t, s0)) for s0, e in used[sp.clip]) / need
                cost += 5.0 * ov                      # prefer not to reuse a window
                fs_ = [face_sharp(sp.clip, tr, t + need * q) for q in (0.0, 0.25, 0.5, 0.75, 1.0)]
                cost += 8.0 * max(0.0, 1.0 - min(fs_) / max(1e-6, clip_ref[sp.clip]))
                ids = np.interp(np.linspace(t, t + need, 10) * tr.fps, tr.idx, tr.ident)
                cost += 12.0 * max(0.0, 0.64 - float(ids.min()))
                if best is None or cost < best[0]:
                    best = (cost, t)
                t += 0.1
        if best is None:
            raise RuntimeError(f"no good window for {sp.clip} ({need:.2f}s): runs={tr.good_runs()}")
        sp.cost, sp.t0 = best[0], best[1]
        used[sp.clip].append((sp.t0, sp.t0 + need))
        ts = np.linspace(sp.t0, sp.t0 + need, 12) * tr.fps
        ids = np.interp(ts, tr.idx, tr.ident)
        sp.id_min, sp.id_mean = float(ids.min()), float(ids.mean())
        end_t = (sp.t0 + need) * tr.fps
        prev_pose = tr.smooth_at(end_t)
    return shots


# ------------------------------------------------------------------ pixels
class Src:
    def __init__(self, key: str, cfg: dict | None = None):
        c = cfg or CLIPS[key]
        self.key = key
        self.c = c
        self.track = Track.load(GC / c["track"])
        self.fps = self.track.fps
        x, y, w, h = c["crop"]
        self.crop = (x, y, w, h)
        self.cap = cv2.VideoCapture(str(c["path"]))
        self.cache: dict[int, np.ndarray] = {}
        self.srdir = (Q / c["sr"]) if c["sr"] else None
        self.srx = c["srx"]
        self.sr_origin = c.get("sr_origin", (0, 0))   # crop-space origin of the SR image
        self.wins = []                                  # per-window SR crops of c_sr/<key>/meta.json
        meta = Q / "c_sr" / key / "meta.json"
        if c["sr"] is None and meta.exists():
            self.wins = json.loads(meta.read_text(encoding="utf-8"))

    def raw(self, i: int) -> np.ndarray:
        if i not in self.cache:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, f = self.cap.read()
            if not ok:
                f = np.zeros((self.crop[3], self.crop[2], 3), np.uint8)
            x, y, w, h = self.crop
            self.cache[i] = np.ascontiguousarray(f[y:y + h, x:x + w])
            if len(self.cache) > 90:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[i]

    def image(self, i: int):
        """Returns (img, k, (ox, oy)): crop-space point (x, y) is at ((x - ox) * k, (y - oy) * k) in img."""
        for wn in self.wins:
            for j in (i, i - 1, i + 1):
                if wn["i0"] <= j <= wn["i1"]:
                    p = Q / "c_sr" / self.key / wn["dir"] / f"{j:05d}.png"
                    if p.exists():
                        return cv2.imread(str(p)), 4.0, (wn["ox"], wn["oy"])
        if self.srdir is not None:
            for j in (i, i - 1, i + 1):
                p = self.srdir / f"{j:05d}.png"
                if p.exists():
                    return cv2.imread(str(p)), float(self.srx), self.sr_origin
        return self.raw(i), 1.0, (0, 0)


def blurred_cover(img: np.ndarray) -> np.ndarray:
    """Background for the circular clips: colour field of the picture (4x4 mean colours, smooth upscale), dimmed. No face shape."""
    h, w = img.shape[:2]
    inner = img[int(0.1 * h):int(0.9 * h), int(0.1 * w):int(0.9 * w)]
    tiny = cv2.resize(inner, (3, 4), interpolation=cv2.INTER_AREA)
    big = cv2.resize(tiny, (W, H), interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 40)
    ys = np.linspace(0.85, 1.05, H, dtype=np.float32)[:, None, None]
    return np.clip(big.astype(np.float32) * 0.55 * ys, 0, 255).astype(np.uint8)


def draw_shot(src: Src, s_t: float, z: float) -> tuple[np.ndarray, tuple[float, float]]:
    """One canvas frame: head at (HEAD_CX, HEAD_CY), head width FACE_W * z. Returns (frame, head centre)."""
    f = s_t * src.fps
    i0 = int(math.floor(f))
    w = f - i0
    fa = src.track.smooth_at(f, 2.0)
    i0 = i0 + (1 if w >= 0.5 else 0)                    # nearest source frame: blending two frames of a moving head ghosts the face
    img, k, (ox, oy) = src.image(i0)
    sc = FACE_W * z / max(8.0, fa["fw"])              # output px per crop px
    cw, ch = src.crop[2], src.crop[3]
    vx0, vx1 = src.c.get("valid", (0, cw))              # real picture inside the (blur-filled) frame, crop coords
    if not src.c["circle"]:
        sc = max(sc, W / (vx1 - vx0), H / ch)         # never show beyond the picture
    tx = HEAD_CX - sc * fa["cx"]
    ty = HEAD_CY - sc * fa["cy"]
    if not src.c["circle"]:
        tx = min(-sc * vx0, max(W - sc * vx1, tx))
        ty = min(0.0, max(H - sc * ch, ty))
    if k != 1.0 and not src.c["circle"]:               # restored window must cover the framing, else use the raw frame
        nx0, nx1, ny0, ny1 = -tx / sc, (W - tx) / sc, -ty / sc, (H - ty) / sc
        if not (ox <= nx0 + 1 and ox + img.shape[1] / k >= nx1 - 1 and oy <= ny0 + 1 and oy + img.shape[0] / k >= ny1 - 1):
            img, k, (ox, oy) = src.raw(i0), 1.0, (0, 0)
    m = np.array([[sc / k, 0, tx + sc * ox], [0, sc / k, ty + sc * oy]], np.float32)
    flags = cv2.INTER_AREA if sc / k < 1.0 else cv2.INTER_CUBIC
    if src.c["circle"]:
        base = blurred_cover(src.raw(i0))
        warped = cv2.warpAffine(img, m, (W, H), flags=flags, borderMode=cv2.BORDER_CONSTANT)
        mask = np.zeros((H, W), np.float32)
        ccx, ccy = tx + sc * cw / 2, ty + sc * ch / 2
        cv2.circle(mask, (int(ccx), int(ccy)), int(sc * cw / 2 - 3), 1.0, -1, cv2.LINE_AA)
        mask = cv2.GaussianBlur(mask, (0, 0), 1.2)[:, :, None]
        frame = (warped.astype(np.float32) * mask + base.astype(np.float32) * (1 - mask)).astype(np.uint8)
    else:
        frame = cv2.warpAffine(img, m, (W, H), flags=flags, borderMode=cv2.BORDER_REPLICATE)
    hx, hy = tx + sc * fa["cx"], ty + sc * fa["cy"]
    return ba.sharpen(frame, 0.25), (hx, hy)


def inset(frame: np.ndarray, still: np.ndarray, t: float) -> np.ndarray:
    """0.5 s reference-photo inset (photo, labelled by the drawtext pass): right-bottom corner, slides in."""
    w, h = 300, 400
    im = ba._cover(still, w, h, (0.5, 0.35), 1.0)
    p = fx.ease_out_cubic(min(1.0, t / 0.12))
    x0 = int(W - w - 40 + (1 - p) * (w + 60))
    y0 = H - h - 560
    out = frame.copy()
    vis = max(0, min(w, W - x0))
    if vis > 0:
        out[y0:y0 + h, x0:x0 + vis] = im[:, :vis]
        cv2.rectangle(out, (x0 - 3, y0 - 3), (min(W - 1, x0 + w + 2), y0 + h + 2), (255, 255, 255), 3)
    return out


def render_master(shots: list[ShotPlan], out_video: Path) -> dict:
    srcs = {k: Src(k) for k in {s.clip for s in shots}}
    still = ba.Extras().still("navy", 1.0, 1.0)
    still = cv2.resize(still, (540, 960), interpolation=cv2.INTER_AREA)
    n = int(round(30 * P * FPS))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
                             "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "6", "-pix_fmt", "yuv420p", str(out_video)],
                            stdin=subprocess.PIPE)
    prev_head = (HEAD_CX, HEAD_CY)
    rows = []
    for k in range(n):
        tau = k / FPS
        beat = tau / P
        sh = next((s for s in shots if s.b0 <= beat < s.b0 + s.nb), None)
        if sh is None:                                              # end card
            fr = np.zeros((H, W, 3), np.uint8)
            ys = np.linspace(0, 1, H, dtype=np.float32)[:, None]
            fr[:, :, 0] = (28 + 30 * ys).astype(np.uint8)
            fr[:, :, 1] = (14 + 10 * ys).astype(np.uint8)
            fr[:, :, 2] = (20 + 14 * ys).astype(np.uint8)
            proc.stdin.write(fr.tobytes())
            continue
        t_in = tau - sh.b0 * P
        d = sh.nb * P
        s_t = sh.t0 + fx.ramp_source_time(t_in, sh.kn)
        z = (sh.z0 + (sh.z1 - sh.z0) * min(1.0, t_in / d)) * fx.punch_scale(t_in, 0.0, peak=0.05 + 0.07 * sh.hit, decay=0.14)
        fr, head = draw_shot(srcs[sh.clip], s_t, z)
        if t_in < 0.30 and sh.hit > 0:
            dx = int(round(14 * sh.hit * math.exp(-t_in / 0.07)))
            if dx >= 1:
                fr = ba.face_protect(fx.rgb_split(fr, dx, 0), fr, head)
            fl = (0.26 * math.exp(-t_in / 0.045)) if sh.hit >= 1 else 0.0
            if sh.hit >= 2:
                fl = 0.5 * math.exp(-t_in / 0.07)
            fr = ba.face_protect(fx.flash(fr, fl), fr, head)
        if 25.2 * P <= tau - 0.0 < 25.2 * P + 0.5:                  # 0.5 s reference photo inset
            fr = inset(fr, still, tau - 25.2 * P)
        fr = ba.grade(fr)
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    proc.wait()
    return {"frames": n}


def spec_texts() -> ba.Spec:
    T: list = []
    _T = ba._T
    ba._common_text(T)
    _T(T, "NEW LOOK?", 0, 1.95, size=110, y="h-470", box=True)
    _T(T, "SAME FACE", 2, 7.9, y="h-470", color="0xffd23c", box=True)
    _T(T, "REAL CLIPS", 8, 15.9, y="h-470", box=True)
    _T(T, "OTHER CLOTHES", 16, 20, y="h-470", color="0xffd23c", box=True)
    _T(T, "HER FACE", 20, 24, y="h-470", size=118, box=True)
    _T(T, "NEXT LOOK", 24, 26, size=80, y="h-470", color="0xffd23c", box=True)
    _T(T, "reference photo", 25.2, 25.2 + 0.5 / P, size=36, x="w-360", y="h-1010", border=3, color="white", pop=0.04, fade_out=0.04,
       box=True)
    for i, (t0, text, size, col) in enumerate([(28.0, "ОБРАЗ ПОМЕНЯЛСЯ.", 70, "white"), (28.3, "ЛИЦО ТО ЖЕ.", 70, "0xffd23c")]):
        ba._T(T, text, t0, 30, size=size, y=f"h*0.26+{i * 110}", border=0, pop=0.2, color=col)
    lines = ["смена образа = подмена лица в клипах", "с другой одеждой, а не перерисовка", "одежды на её видео",
             "подмена одежды: пробный режим"]
    for i, ln in enumerate(lines):
        ba._T(T, ln, 28.4, 30, size=36, y=f"h*0.52+{i * 56}", color="white" if i < 3 else "0xffd23c", border=0, pop=0.2)
    ba._T(T, "лицо: FaceFusion  |  часть клипов - её живые видео", 28.5, 30, size=30, y="h*0.52+300", color="0xe0e0e0", border=0, pop=0.2)
    return ba.Spec([], T, 30.0, "outfit_cut")


def main(argv=None) -> int:
    mode = (argv or sys.argv[1:] or ["plan"])[0]
    shots = plan()
    Q.joinpath("c_plan.json").write_text(json.dumps([s.__dict__ for s in shots], indent=1, default=str), encoding="utf-8")
    for s in shots:
        print(f"beat {s.b0:5.1f}+{s.nb:<3} {s.clip:6s} t0={s.t0:6.2f} cost={s.cost:5.2f} id_min={s.id_min:.2f} id_mean={s.id_mean:.2f}")
    if mode == "plan":
        return 0
    if mode == "sr":
        sr_jobs(shots)
        return 0
    if mode == "sr_circles":
        sr_jobs(shots, circles_only=True)
        return 0
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Q / "hq" / "ad-c-outfit-cut_hq.mp4"
    work = out.parent / "work_c"
    work.mkdir(parents=True, exist_ok=True)
    master = work / "outfit_cut_master.mp4"
    render_master(shots, master)
    sharp = master.with_name("outfit_cut_master_sharp.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-vf", "unsharp=5:5:0.45:5:5:0", "-c:v", "libx264", "-crf", "4",
                    "-preset", "fast", "-pix_fmt", "yuv420p", str(sharp)], check=True)
    ba.finish(sharp, spec_texts(), out, crf=15)
    print("done", out)
    return 0




def sr_jobs(shots: list[ShotPlan], circles_only: bool = False) -> None:
    """Restore the exact source windows of the plan with Real-ESRGAN x4plus (ncnn-vulkan). Circles: the whole 400x400 picture;
    other clips: the source rectangle the 9:16 framing needs around the head (so the cost stays at ~0.5 MP per frame)."""
    exe = Q / "tools" / "rg" / "realesrgan-ncnn-vulkan.exe"
    models = Q / "tools" / "rg" / "models"
    metas: dict[str, list] = {}
    old_meta: dict[str, list] = {}
    for k in CLIPS:
        mp = Q / "c_sr" / k / "meta.json"
        if mp.exists():
            old_meta[k] = json.loads(mp.read_text(encoding="utf-8"))
    for n, sp in enumerate(shots):
        c = CLIPS[sp.clip]
        if circles_only != bool(c["circle"]) or (c["sr"] is not None and not c["circle"]):
            continue
        tr = Track.load(GC / c["track"])
        need = consume(sp)
        fps = tr.fps
        i0, i1 = int(math.floor(sp.t0 * fps)) - 1, int(math.ceil((sp.t0 + need) * fps)) + 2
        cx0, cy0, cw, ch = c["crop"]
        fa = tr.smooth_at((i0 + i1) / 2)
        if c["circle"]:
            ox, oy, ww, hh = 0, 0, cw, ch
        else:
            sc = max(FACE_W * 1.15 / max(8.0, fa["fw"]), W / cw, H / ch)
            ww, hh = min(cw, int(W / sc) + 40), min(ch, int(H / sc) + 40)
            ox = int(min(max(0, fa["cx"] - ww / 2), cw - ww))
            oy = int(min(max(0, fa["cy"] - hh * (HEAD_CY / H)), ch - hh))
        if not c["circle"]:
            have = [m for m in metas.get(sp.clip, [])]
            old = [m for m in old_meta.get(sp.clip, []) if m["i0"] <= max(0, i0) and m["i1"] >= i1]
            if old:
                metas.setdefault(sp.clip, []).append(old[0])
                print("reuse", sp.clip, n, flush=True)
                continue
        d = (Q / c["sr"]) if c["circle"] else (Q / "c_sr" / sp.clip / f"x{max(0, i0):05d}")
        src_d = Q / "c_sr_src" / sp.clip / f"x{max(0, i0):05d}"
        src_d.mkdir(parents=True, exist_ok=True)
        d.mkdir(parents=True, exist_ok=True)
        step = 2 if fps > 45 else 1
        cap = cv2.VideoCapture(str(c["path"]))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, i0))
        for i in range(max(0, i0), i1 + 1):
            ok, f = cap.read()
            if not ok:
                break
            if (i - max(0, i0)) % step == 0 and not (d / f"{i:05d}.png").exists():
                cv2.imwrite(str(src_d / f"{i:05d}.png"), f[cy0 + oy:cy0 + oy + hh, cx0 + ox:cx0 + ox + ww])
        subprocess.run([str(exe), "-i", str(src_d), "-o", str(d), "-n", "realesrgan-x4plus", "-s", "4", "-m", str(models),
                        "-f", "png", "-j", "1:2:2"], check=True, capture_output=True)
        if not c["circle"]:
            metas.setdefault(sp.clip, []).append({"i0": max(0, i0), "i1": i1, "ox": ox, "oy": oy, "dir": f"x{max(0, i0):05d}"})
        print("sr", sp.clip, n, i0, i1, (ox, oy, ww, hh), flush=True)
    for k, v in metas.items():
        v = v + [m for m in old_meta.get(k, []) if m not in v]        # keep earlier windows (unused ones are harmless)
        (Q / "c_sr" / k).mkdir(parents=True, exist_ok=True)
        (Q / "c_sr" / k / "meta.json").write_text(json.dumps(v), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
