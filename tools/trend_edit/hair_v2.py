"""Hair recolour v2: soft alpha, skin excluded, Lab tone-map that keeps texture + highlights, temporal smoothing.

Thresholds are fixed in docs/owner/HAIR_MASK_V2_20261010.md (written before the first run).
Run with FaceFusion's interpreter (onnxruntime-directml + opencv):

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_v2.py --src clip.mp4 --out out.mp4 --color platinum \
        [--first 0 --count 120] [--cache DIR] [--report report.json]

Library use (builders): `HairV2.masks_for_clip(...)` caches alpha/skin/person per frame, `recolor(frame, alpha, color, state)`.
SegFormer-B2 clothes parser is non-commercial licence, RVM is GPL-3.0: personal edits only (same flags as parse_masks.py).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center" / "bcc" / "direct_gen"))
sys.path.insert(0, str(ROOT / "tools" / "genjutsu"))
import parsing_worker as pw  # noqa: E402

SEG_MODEL = Path.home() / "Bossman" / "models" / "genjutsu" / "segformer_b2_clothes" / "model.onnx"

#: target hair colours (sRGB hex). platinum is a light neutral blond; measured against its Lab in the report.
COLORS = {
    "chocolate": "#4a2c1a", "chestnut": "#7a4a2e", "copper": "#b4532a", "platinum": "#c6c3ba",
    "blue": "#1f5fd1", "red": "#b3131b", "green": "#1f9d55", "pink": "#e0559b", "purple": "#7a3fd0",
    "black": "#0d0d10", "burgundy": "#6d1a2e", "blonde": "#cfae6a", "teal": "#16a6a6",
}


def hex_lab(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    bgr = np.array([[[int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)]]], np.float32) / 255.0
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)[0, 0]
    return float(lab[0]), float(lab[1]), float(lab[2])


def smoothstep(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def to_lab(bgr_u8: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr_u8.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)


# ------------------------------------------------------------------ alpha
class AlphaMaker:
    """Per-frame hair alpha = SegFormer hair, guided to the image, inside the RVM person, minus skin / skin-coloured pixels."""

    def __init__(self, provider: str = "directml"):
        from background_swap import Matting
        self.parser = pw.Parser(SEG_MODEL, provider)
        self.rvm = Matting(device="dml" if provider == "directml" else "cpu")
        self.prev: np.ndarray | None = None
        self.box = None
        self.rvm_head = Matting(device="dml" if provider == "directml" else "cpu")   # second recurrent state: head crop, no downsampling
        self.head_c = None

    def person_alpha(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if self.box is None:                      # fixed per clip: RVM's recurrent state needs a constant size
            self.box = pw.picture_box(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        t, b, l, r = self.box
        pic = frame[t:b, l:r]
        ph, pw_ = pic.shape[:2]
        ratio = min(1.0, 512.0 / max(ph, pw_))
        _fgr, pha = self.rvm(cv2.cvtColor(pic, cv2.COLOR_BGR2RGB), ratio)
        out = np.zeros((h, w), np.float32)
        out[t:b, l:r] = pha
        return out

    def __call__(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        masks, _ = pw.parse(frame, self.parser)
        hair = masks["hair"].astype(np.float32) / 255.0
        skin = masks["skin"].astype(np.float32) / 255.0
        person = self.person_alpha(frame)
        # stay inside the person (RVM is temporally stable); dilate so that thin strands are not cut
        p = cv2.dilate(person, np.ones((5, 5), np.uint8))
        a = hair * smoothstep(0.05, 0.45, p)
        # drop skin: SegFormer skin probability (face / neck / ears) ...
        lab = to_lab(frame)
        skin_s = cv2.GaussianBlur(skin, (0, 0), 1.2)
        core = (hair > 0.6) & (skin_s < 0.2)
        h_l = float(np.median(lab[:, :, 0][core])) if core.sum() > 200 else 15.0
        dark = 1.0 - smoothstep(h_l + 6.0, h_l + 16.0, lab[:, :, 0])        # 1 = as dark as the hair
        # hairline guard: ~0.06 x face side (8 px at 720p) around the face class, but only BRIGHT pixels are kept out -
        # dark hair pixels inside the band are still hair (leaving them black drew a dark outline in the first version)
        guard = max(3, int(round(0.011 * frame.shape[0])))
        skin_g = cv2.dilate(skin_s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * guard + 1, 2 * guard + 1)))
        skin_ex = np.maximum(smoothstep(0.08, 0.45, skin_s), smoothstep(0.25, 0.6, skin_g) * (1.0 - dark))
        a *= 1.0 - skin_ex
        # ... and skin-coloured, bright pixels (forehead / temple that the hair class bled onto)
        sk = skin > 0.8
        if core.sum() > 200 and sk.sum() > 200:
            s_ab = np.median(lab[:, :, 1:][sk], axis=0)
            dab = np.sqrt(((lab[:, :, 1:] - s_ab) ** 2).sum(axis=2))
            skin_like = smoothstep(32.0, 16.0, dab) * smoothstep(h_l + 10.0, h_l + 24.0, lab[:, :, 0])
            a *= 1.0 - skin_like
        # snap the edge to the image's own edge (3 px guided filter, colour-luma guide)
        guide = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        a = pw.guided_filter(guide, a.astype(np.float32), 3, 1e-4)
        a = smoothstep(0.3, 0.8, a)
        # temporal EMA, but never lag far behind a fast head turn: take the max of EMA and 0.8 * new
        if self.prev is not None and self.prev.shape == a.shape:
            a = np.maximum(0.55 * a + 0.45 * self.prev, 0.8 * a)
        self.prev = a
        # hard gates AFTER the temporal filter, so a lagging EMA can never leave the person or step on skin
        gate = smoothstep(0.35, 0.7, cv2.dilate(person, np.ones((3, 3), np.uint8)))
        a = a * gate * (1.0 - skin_ex)
        # outer fringe: old dark hair pixels just outside the matte (2 px) belong to the hair too
        grow = cv2.dilate(a, np.ones((5, 5), np.uint8))
        a = np.maximum(a, 0.9 * grow * dark * gate * (1.0 - skin_ex))
        return {"alpha": a, "skin": skin, "person": person, "head": self.head_alpha(frame, a)}

    HEAD = 448

    def head_alpha(self, frame: np.ndarray, a: np.ndarray) -> np.ndarray:
        """RVM at full resolution (downsample_ratio 1.0) on a fixed-size crop around the hair: soft strands the whole-frame pass
        (ratio ~0.5) smooths away. Returned on the full frame canvas (zeros outside the crop)."""
        h, w = frame.shape[:2]
        ys, xs = np.where(a > 0.5)
        if len(ys) > 50:
            c = np.array([xs.mean(), ys.mean()], np.float32)
            self.head_c = c if self.head_c is None else 0.7 * self.head_c + 0.3 * c
        if self.head_c is None:
            return np.zeros((h, w), np.float32)
        s_ = self.HEAD
        x0 = int(min(max(self.head_c[0] - s_ / 2, 0), max(0, w - s_)))
        y0 = int(min(max(self.head_c[1] - s_ / 2, 0), max(0, h - s_)))
        crop = frame[y0:y0 + s_, x0:x0 + s_]
        if crop.shape[0] != s_ or crop.shape[1] != s_:
            return np.zeros((h, w), np.float32)
        _f, pha = self.rvm_head(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), 1.0)
        out = np.zeros((h, w), np.float32)
        out[y0:y0 + s_, x0:x0 + s_] = pha
        return out


def masks_for_clip(src: Path, cache: Path, first: int = 0, count: int = 0, provider: str = "directml") -> list[Path]:
    """Writes cache/%04d.npz (alpha, skin, person as uint8) for each frame; returns the paths. Reuses existing files."""
    cache.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(src))
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    n = count or (n_total - first)
    paths = [cache / f"{first + i:05d}.npz" for i in range(n)]
    if all(p.exists() for p in paths):
        return paths
    maker = AlphaMaker(provider)
    if first:
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    t0 = time.time()
    for i, p in enumerate(paths):
        ok, frame = cap.read()
        if not ok:
            break
        m = maker(frame)
        np.savez_compressed(p, alpha=np.round(m["alpha"] * 255).astype(np.uint8),
                            skin=np.round(m["skin"] * 255).astype(np.uint8),
                            person=np.round(m["person"] * 255).astype(np.uint8),
                            head=np.round(m["head"] * 255).astype(np.uint8))
        if i % 30 == 29:
            print(f"masks {i + 1}/{n} {time.time() - t0:.0f}s", flush=True)
    return paths


# ------------------------------------------------------------------ recolour
class RecolorState:
    """Temporal smoothing of the per-frame mean/std of hair L (so the offset does not flicker)."""

    def __init__(self):
        self.mu = None
        self.sd = None


def recolor(frame: np.ndarray, alpha: np.ndarray, color: str, state: RecolorState | None = None,
            strength: float = 1.0) -> np.ndarray:
    """Lab tone-map: L' = Lt + k (L - mu) with k >= 1 lifts the micro-contrast so the std of L is kept (>= 0.8x);
    a, b are pulled to the target (a bit of the original chroma kept for natural variation); the top highlights stay."""
    hexv = COLORS.get(color, color)
    lt, at, bt = hex_lab(hexv)
    lab = to_lab(frame)
    L = lab[:, :, 0]
    # grow the matte by ~1 px: the soft edge pixels still carry the old dark hair colour and read as a dark outline
    w = cv2.GaussianBlur(cv2.dilate(alpha.astype(np.float32), np.ones((3, 3), np.uint8)), (0, 0), 0.8)
    core = w > 0.5
    if core.sum() < 50:
        return frame
    mu = float(L[core].mean())
    sd = float(L[core].std()) + 1e-3
    if state is not None:
        state.mu = mu if state.mu is None else 0.6 * mu + 0.4 * state.mu
        state.sd = sd if state.sd is None else 0.6 * sd + 0.4 * state.sd
        mu, sd = state.mu, state.sd
    # Large-scale shading (roots darker, volume) and fine detail (strands) are scaled separately: a black source has almost no
    # contrast, so a plain "L + offset" gives a flat white shape - the detail band is amplified, never invented.
    lift = max(0.0, lt - mu)
    if lt < 62.0:                                   # dyed-from-black look: keep the original luminance structure, add chroma
        lift = 0.55 * lift
    kb = 1.0 + min(1.0, lift / 60.0) * 1.6          # 1.0 (no lift) .. 2.6 (black -> platinum)
    kd = 1.0 + min(1.0, lift / 60.0) * 2.2          # 1.0 .. 3.2
    # masked (normalised) blur: the dark background / skin outside the hair must not darken the hair rim
    wsoft = np.clip(w * 1.5, 0, 1)
    lb = cv2.GaussianBlur(L * wsoft, (0, 0), 5.0) / (cv2.GaussianBlur(wsoft, (0, 0), 5.0) + 1e-3)
    lb = np.where(wsoft > 0.02, lb, L)
    ld = cv2.GaussianBlur(L - lb, (0, 0), 1.1)       # sub-1.5 px detail is mostly codec blocks, not strands
    # local-contrast-normalised detail: strands in a black source are low amplitude; normalise then scale to a target amplitude
    env = cv2.GaussianBlur(np.abs(ld), (0, 0), 4.0) + 0.6
    lcn = np.clip(ld / env, -2.5, 2.5)
    amp = min(1.0, lift / 60.0) * 6.0
    # ambient occlusion: hair darkens toward its own boundary (depth), and a mean slightly under the target leaves room for sheen
    dist = cv2.distanceTransform((w > 0.5).astype(np.uint8), cv2.DIST_L2, 3)
    ao = 11.0 * np.exp(-dist / 9.0) * min(1.0, lift / 40.0)
    newL = lt - 4.0 * min(1.0, lift / 60.0) + kb * (lb - mu) + 0.55 * kd * ld + amp * lcn - ao
    hi = min(94.0, lt + 12.0)
    newL = np.where(newL > hi, hi + (newL - hi) * 0.3, newL)
    newL = np.clip(newL, 2.0, 100.0)
    hl = smoothstep(mu + 1.6 * sd, mu + 3.2 * sd, L)
    newL = newL + hl * (hi + 4.0 - newL).clip(0, None) * 0.35
    spec = hl                                       # specular: highlights go neutral (less chroma), like real glossy hair
    keep_chroma = 0.12
    sat = 1.0 - 0.65 * spec
    new_a = at * sat + (lab[:, :, 1] * keep_chroma)
    new_b = bt * sat + (lab[:, :, 2] * keep_chroma)
    # dark targets: the original chroma is small anyway; light targets: scale chroma with L so a pale colour stays pale
    new = np.dstack([newL, new_a, new_b]).astype(np.float32)
    rgb = np.clip(cv2.cvtColor(new, cv2.COLOR_Lab2BGR), 0, 1) * 255.0
    ww = (smoothstep(0.05, 0.6, w) * strength)[:, :, None]
    out = frame.astype(np.float32) * (1 - ww) + rgb * ww
    return np.clip(out, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ metrics (definitions: HAIR_MASK_V2 doc)
def frame_metrics(src: np.ndarray, out: np.ndarray, alpha: np.ndarray, skin: np.ndarray, person: np.ndarray,
                  target_lab: tuple[float, float, float]) -> dict:
    sl, ol = to_lab(src), to_lab(out)
    de = np.sqrt(((sl - ol) ** 2).sum(axis=2))
    changed = de > 4.0
    n_ch = int(changed.sum())
    pdil = cv2.dilate((person > 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    leak = float((changed & ~pdil).sum()) / n_ch if n_ch else 0.0
    sk = cv2.erode((skin > 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    skin_ch = float((changed & sk).sum()) / max(1, int(sk.sum()))
    core = alpha > 0.5
    std_ratio = float(ol[:, :, 0][core].std() / max(1e-3, sl[:, :, 0][core].std())) if core.sum() > 50 else float("nan")
    mean_lab = ol[core].mean(axis=0) if core.sum() > 50 else np.array([np.nan] * 3)
    d_target = float(np.sqrt(((mean_lab - np.array(target_lab)) ** 2).sum()))
    # edge: distance from the alpha=0.5 contour to the nearest source edge
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (0, 0), 1.0), 40, 110) > 0
    dist = cv2.distanceTransform((~edges).astype(np.uint8), cv2.DIST_L2, 3)
    contour = (cv2.morphologyEx(core.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0)
    d = dist[contour]
    near = d[d <= 8.0]
    edge = float(near.mean()) if near.size else float("nan")
    return {"edge_px": edge, "edge_covered": float(near.size) / max(1, d.size), "leak": leak, "skin": skin_ch,
            "std_ratio": std_ratio, "dE_target": d_target, "mean_L": float(mean_lab[0]),
            "mean_a": float(mean_lab[1]), "mean_b": float(mean_lab[2]), "hair_px": int(core.sum())}


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["hair_px"] > 300 and not np.isnan(r["std_ratio"])]
    if not ok:
        return {"frames": 0}
    g = lambda k: float(np.nanmean([r[k] for r in ok]))  # noqa: E731
    means = np.array([[r["mean_L"], r["mean_a"], r["mean_b"]] for r in ok])
    flick = float(np.abs(np.diff(means, axis=0)).mean()) if len(ok) > 1 else 0.0
    s = {"frames": len(ok), "edge_px": g("edge_px"), "edge_covered": g("edge_covered"), "leak": g("leak"), "skin": g("skin"),
         "std_ratio": g("std_ratio"), "dE_target": g("dE_target"), "flicker_dLab_per_frame": flick,
         "worst_leak": float(max(r["leak"] for r in ok)), "worst_skin": float(max(r["skin"] for r in ok))}
    s["pass"] = {"edge<=2": s["edge_px"] <= 2.0, "leak<=1%": s["leak"] <= 0.01, "skin<=0.5%": s["skin"] <= 0.005,
                 "std>=0.8": s["std_ratio"] >= 0.8, "dE<=15": s["dE_target"] <= 15.0}
    return s


# ------------------------------------------------------------------ cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--color", default="platinum")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--cache", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--fps", type=float, default=0.0)
    a = ap.parse_args(argv)
    src = Path(a.src)
    cache = Path(a.cache) if a.cache else Path(a.out).with_suffix("") .parent / (src.stem + "_hairv2")
    paths = masks_for_clip(src, cache, a.first, a.count)
    cap = cv2.VideoCapture(str(src))
    fps = a.fps or cap.get(cv2.CAP_PROP_FPS)
    if a.first:
        cap.set(cv2.CAP_PROP_POS_FRAMES, a.first)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
                           "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", a.out], stdin=subprocess.PIPE)
    st = RecolorState()
    tl = hex_lab(COLORS.get(a.color, a.color))
    rows = []
    for p in paths:
        ok, frame = cap.read()
        if not ok:
            break
        z = np.load(p)
        al, sk, pe = (z[k].astype(np.float32) / 255.0 for k in ("alpha", "skin", "person"))
        out = recolor(frame, al, a.color, st)
        rows.append(frame_metrics(frame, out, al, sk, pe, tl))
        ff.stdin.write(out.tobytes())
    ff.stdin.close()
    ff.wait()
    summ = summarize(rows)
    summ["target_lab"] = tl
    print(json.dumps(summ, indent=1))
    if a.report:
        Path(a.report).write_text(json.dumps({"summary": summ, "frames": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def bright_gate(a: np.ndarray, lab_l: np.ndarray, core_mask: np.ndarray | None = None) -> np.ndarray:
    """Remove pixels far brighter than the hair itself (shirt / shoulder / skin the parser took for hair; the bright rim that
    made the 'sticker outline'). Reference: p97 of L over the confident hair core of the SAME frame."""
    core = (a > 0.8) if core_mask is None else core_mask
    if core.sum() < 150:
        return a
    p97 = float(np.percentile(lab_l[core], 97))
    p50 = float(np.percentile(lab_l[core], 50))
    top = max(p97 + 6.0, p50 + 22.0)
    return a * (1.0 - smoothstep(top, top + 12.0, cv2.GaussianBlur(lab_l, (0, 0), 1.0)))


def refine_alpha(a_seg: np.ndarray, head: np.ndarray, skin: np.ndarray, frame: np.ndarray) -> np.ndarray:
    """Soft strand alpha: the high-res RVM head matte inside the (dilated) SegFormer hair region, skin kept out, holes of
    dark hair pixels closed. The SegFormer mask only says WHERE hair is; the matte says how opaque each edge pixel is."""
    region = cv2.GaussianBlur(cv2.dilate(a_seg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))), (0, 0), 2.0)
    skin_s = cv2.GaussianBlur(skin, (0, 0), 1.2)
    guard = max(3, int(round(0.011 * frame.shape[0])))
    skin_g = cv2.dilate(skin_s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * guard + 1, 2 * guard + 1)))
    lab = to_lab(frame)
    core = a_seg > 0.7
    h_l = float(np.median(lab[:, :, 0][core])) if core.sum() > 150 else 15.0
    dark = 1.0 - smoothstep(h_l + 6.0, h_l + 16.0, lab[:, :, 0])
    # a pixel as dark as the hair is hair even where the face class bleeds 1-2 px over the hairline (the black inner outline)
    skin_ex = np.maximum(smoothstep(0.08, 0.45, skin_s) * (1.0 - 0.92 * dark), smoothstep(0.25, 0.6, skin_g) * (1.0 - dark))
    a = np.clip(region, 0, 1) * head * (1.0 - skin_ex)
    solid = cv2.erode((a_seg > 0.92).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    a = np.maximum(a, solid * (1.0 - skin_ex))
    # holes: dark pixels enclosed by hair (temples, between strands) are hair
    closed = cv2.morphologyEx(a, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    a = np.maximum(a, np.minimum(closed, dark) * (1.0 - skin_ex))
    a = bright_gate(a, lab[:, :, 0], a_seg > 0.8)
    return np.clip(a, 0, 1)


# ====================================================================== reference-guided look (owner 11.10)
class RefLook:
    """Reference-guided hair colour transfer (not a fill).

    From a reference photo of her WITH that hair colour: the hair pixels (SegFormer) give, along the luminance rank p (0..1,
    root-dark to highlight): L_ref(p), a_ref(p), b_ref(p). For a source frame the hair pixels of THAT frame are ranked by
    luminance, the rank is mapped to the reference's (L, a, b) at the same rank (histogram matching of L, per-rank chroma),
    and the source's own fine strand detail (L minus its 1 px blur) is added on top. The rank function is smoothed over time.
    """

    NQ = 129

    def __init__(self, ref_bgr: np.ndarray, ref_alpha: np.ndarray, name: str = ""):
        self.name = name
        lab = to_lab(ref_bgr)
        m = ref_alpha > 0.5
        L, a, b = lab[:, :, 0][m], lab[:, :, 1][m], lab[:, :, 2][m]
        # parser leaks (skin, shirt, hoodie) have a different chroma from the hair: drop the 15 % farthest from the median
        dist = np.hypot(a - np.median(a), b - np.median(b))
        keep = dist <= np.percentile(dist, 85)
        L, a, b = L[keep], a[keep], b[keep]
        order = np.argsort(L)
        L, a, b = L[order], a[order], b[order]
        qs = np.linspace(0, 1, self.NQ)
        # the darkest 7 % of a reference hair mask are mostly neighbours (hoodie, shadow) the parser took for hair: not used
        idx = np.minimum(((0.07 + 0.93 * qs) * (len(L) - 1)).astype(int), len(L) - 1)
        self.L = L[idx]
        # chroma per rank: mean of a, b in a +-3 % rank window (smooth, keeps the colour variation with light)
        w = max(1, int(0.03 * len(L)))
        self.a = np.array([a[max(0, i - w):i + w + 1].mean() for i in idx], np.float32)
        self.b = np.array([b[max(0, i - w):i + w + 1].mean() for i in idx], np.float32)
        self.L = self.L.astype(np.float32)
        self.stats = {"L_mean": float(L.mean()), "L_p5": float(np.percentile(L, 5)), "L_p95": float(np.percentile(L, 95)),
                      "a_mean": float(a.mean()), "b_mean": float(b.mean()), "px": int(m.sum())}
        self._src_q: np.ndarray | None = None

    def save(self, path) -> None:
        np.savez(path, L=self.L, a=self.a, b=self.b, stats=json.dumps(self.stats))

    @classmethod
    def load(cls, path, name: str = "") -> "RefLook":
        z = np.load(path, allow_pickle=False)
        o = cls.__new__(cls)
        o.name, o.L, o.a, o.b = name, z["L"], z["a"], z["b"]
        o.stats = json.loads(str(z["stats"]))
        o._src_q = None
        return o

    def apply(self, frame: np.ndarray, alpha: np.ndarray, detail_gain: float = 1.0, wrap: float = 0.0,
              temporal: bool = True) -> np.ndarray:
        """out = I + alpha * (T(F) - F): F = the hair colour with the old background removed (foreground estimation), T = the
        reference-guided map. Background pixels are never touched, so there is no sticker halo."""
        I = frame.astype(np.float32)
        al = np.clip(alpha.astype(np.float32), 0, 1)
        a3 = al[:, :, None]
        # background estimate: normalised blur of the pixels that are clearly not hair
        bgw = (al < 0.04).astype(np.float32)
        bgw = cv2.erode(bgw, np.ones((3, 3), np.uint8))
        num = cv2.GaussianBlur(I * bgw[:, :, None], (0, 0), 7)
        den = cv2.GaussianBlur(bgw, (0, 0), 7)[:, :, None]
        Bg = np.where(den > 0.02, num / np.maximum(den, 1e-3), I)
        F = np.where(a3 > 0.25, (I - (1 - a3) * Bg) / np.maximum(a3, 0.25), I)
        F = np.clip(F, 0, 255)
        lab = to_lab(F.astype(np.uint8))
        L = lab[:, :, 0]
        m = al > 0.6
        if m.sum() < 80:
            return frame
        L = np.minimum(L, float(np.percentile(L[m], 97)) + 4.0)    # a bright rim (skin mixed into F) must not read as a highlight
        Ls = cv2.GaussianBlur(L, (0, 0), 1.8)
        samp = np.sort(Ls[m])
        qs = np.linspace(0, 1, self.NQ)
        q = samp[np.minimum((qs * (len(samp) - 1)).astype(int), len(samp) - 1)]
        if temporal and self._src_q is not None:
            q = 0.6 * q + 0.4 * self._src_q
        self._src_q = q
        q_u = q + np.arange(self.NQ) * 1e-5
        p = np.interp(Ls, q_u, qs)
        pc = np.maximum(p, 0.3)
        l30 = float(np.interp(0.3, qs, self.L))
        light = self.stats["L_mean"] > 60
        floor = l30 - (12.0 if light else 22.0)
        Lr = np.maximum(np.interp(p, qs, self.L), floor)
        Lr = cv2.GaussianBlur(Lr, (0, 0), 1.2)            # the rank map is smooth: dark blotches come from noise, not from hair
        ar = np.interp(pc, qs, self.a)
        br = np.interp(pc, qs, self.b)
        det = L - cv2.GaussianBlur(L, (0, 0), 0.9)
        rng_src = max(1.0, float(q[-8] - q[8]))
        rng_ref = max(1.0, float(self.L[-8] - self.L[8]))
        scale = min(2.2, rng_ref / rng_src) * 0.6
        newL = np.clip(Lr + 1.6 * detail_gain * scale * det, 0, 100)
        new = np.dstack([newL, ar, br]).astype(np.float32)
        Fn = np.clip(cv2.cvtColor(new, cv2.COLOR_Lab2BGR), 0, 1) * 255.0
        out = I + a3 * (Fn - F)
        return np.clip(out, 0, 255).astype(np.uint8)
