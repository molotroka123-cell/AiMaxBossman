"""Measurement for the local photo-upscale capability (Bossman).

Everything here is local and deterministic: image-quality metrics against a ground truth (GT), a procedural
synthetic corpus (so no personal photos are ever needed), the GT -> LR degradation and the benchmark runner that
scores every candidate model on the same corpus.

The point of the module is the owner's rule: *measure before claiming*.  An upscaler cannot create detail that the
low-resolution input does not contain; it can only reconstruct a plausible version.  The metrics therefore compare
every candidate against the *true* high-resolution image (full-reference: PSNR, SSIM, high-frequency error) and
against the Lanczos baseline (sharpness ratios).  "Sharper than GT" is reported explicitly because it is the
signature of hallucinated detail, not of recovered detail.

Metric definitions (all on 8-bit sRGB values, luma Y = 0.299R + 0.587G + 0.114B):

    psnr_rgb        10*log10(255^2 / MSE) over all channels
    psnr_y          same on luma
    ssim_y          Wang et al. SSIM, gaussian window 11x11 sigma 1.5, K1=0.01 K2=0.03, 5 px border cropped
    lap_var_ratio   var(Laplacian3x3(Y_out)) / var(Laplacian3x3(Y_gt))      (>1 = sharper than the truth)
    hf_energy_ratio mean(HP(Y_out)^2) / mean(HP(Y_gt)^2), HP = Y - gauss(Y, 2)   (>1 = more HF energy than truth)
    hf_corr         Pearson correlation between HP(Y_out) and HP(Y_gt)       (1 = same fine structure)
    hf_nmse         mean((HP_out - HP_gt)^2) / mean(HP_gt^2)                 (0 = perfect, ~1 = no HF recovered,
                                                                              >1 = invented/misplaced detail)
    gmsd            gradient-magnitude similarity deviation (Xue 2014), lower = closer to GT

CLI (run with cwd = tools):
    python -m photo_upscale.measure compare --gt GT.png --test OUT.png [--baseline LANCZOS.png]
    python -m photo_upscale.measure synth --out DIR [--size 2048]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:  # optional accelerators; everything has a pure numpy fallback
    import cv2  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    cv2 = None
try:
    from scipy import ndimage as _ndi  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    _ndi = None

METRIC_VERSION = "1"
HF_SIGMA = 2.0
SSIM_BORDER = 5

# --------------------------------------------------------------------------------------------------------------
# basic helpers
# --------------------------------------------------------------------------------------------------------------


def as_u8_rgb(img) -> np.ndarray:
    """Accept PIL image / ndarray, return HxWx3 uint8 (RGB)."""
    if isinstance(img, Image.Image):
        img = np.asarray(img.convert("RGB"))
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.stack([a] * 3, axis=-1)
    if a.shape[2] == 4:
        a = a[:, :, :3]
    if a.dtype != np.uint8:
        a = np.clip(np.rint(a), 0, 255).astype(np.uint8)
    return a


def luma(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.float32)
    return 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]


def _gauss_np(a: np.ndarray, sigma: float) -> np.ndarray:
    radius = int(math.ceil(3.5 * sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2 * sigma * sigma))
    k /= k.sum()
    p = np.pad(a, radius, mode="reflect")
    tmp = np.zeros_like(p)
    for i, w in enumerate(k):
        tmp += w * np.roll(p, i - radius, axis=1)
    out = np.zeros_like(p)
    for i, w in enumerate(k):
        out += w * np.roll(tmp, i - radius, axis=0)
    return out[radius:-radius, radius:-radius]


def gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur of a float32 2-D array with reflect borders."""
    a = a.astype(np.float32, copy=False)
    if cv2 is not None:
        return cv2.GaussianBlur(a, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
    if _ndi is not None:
        return _ndi.gaussian_filter(a, sigma, mode="reflect").astype(np.float32)
    return _gauss_np(a, sigma)


def _same_shape(a: np.ndarray, b: np.ndarray) -> None:
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch {a.shape} vs {b.shape}: compare images of identical size")


# --------------------------------------------------------------------------------------------------------------
# full-reference metrics
# --------------------------------------------------------------------------------------------------------------


def psnr(a, b) -> float:
    a = as_u8_rgb(a).astype(np.float64)
    b = as_u8_rgb(b).astype(np.float64)
    _same_shape(a, b)
    mse = float(np.mean((a - b) ** 2))
    if mse == 0.0:
        return 99.0
    return 10.0 * math.log10(255.0 * 255.0 / mse)


def psnr_y(a, b) -> float:
    ya, yb = luma(as_u8_rgb(a)), luma(as_u8_rgb(b))
    _same_shape(ya, yb)
    mse = float(np.mean((ya.astype(np.float64) - yb.astype(np.float64)) ** 2))
    if mse == 0.0:
        return 99.0
    return 10.0 * math.log10(255.0 * 255.0 / mse)


def ssim_y(a, b) -> float:
    """SSIM on luma (gaussian window, sigma 1.5).  Mean over the image without the 5 px border."""
    x = luma(as_u8_rgb(a))
    y = luma(as_u8_rgb(b))
    _same_shape(x, y)
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    mx, my = gaussian(x, 1.5), gaussian(y, 1.5)
    sxx = gaussian(x * x, 1.5) - mx * mx
    syy = gaussian(y * y, 1.5) - my * my
    sxy = gaussian(x * y, 1.5) - mx * my
    num = (2 * mx * my + c1) * (2 * sxy + c2)
    den = (mx * mx + my * my + c1) * (sxx + syy + c2)
    s = num / den
    b = SSIM_BORDER
    if s.shape[0] > 2 * b + 1 and s.shape[1] > 2 * b + 1:
        s = s[b:-b, b:-b]
    return float(np.mean(s))


def highpass(y: np.ndarray, sigma: float = HF_SIGMA) -> np.ndarray:
    return y - gaussian(y, sigma)


def laplacian_var(y: np.ndarray) -> float:
    """Variance of the 3x3 Laplacian of a float luma image (focus / sharpness measure)."""
    if cv2 is not None:
        lap = cv2.Laplacian(y.astype(np.float32), cv2.CV_32F, ksize=1)
    else:
        p = np.pad(y.astype(np.float32), 1, mode="reflect")
        lap = p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4.0 * p[1:-1, 1:-1]
    return float(np.var(lap))


def hf_energy(y: np.ndarray, sigma: float = HF_SIGMA) -> float:
    hp = highpass(y, sigma)
    return float(np.mean(hp * hp))


def sharpness_stats(rgb) -> Dict[str, float]:
    y = luma(as_u8_rgb(rgb))
    return {"lap_var": laplacian_var(y), "hf_energy": hf_energy(y)}


def sharpness_ratio(test, ref) -> Dict[str, float]:
    """Sharpness of ``test`` relative to ``ref`` (same size). Used for 'vs GT' and 'vs Lanczos'."""
    t, r = sharpness_stats(test), sharpness_stats(ref)
    eps = 1e-9
    return {"lap_var_ratio": t["lap_var"] / (r["lap_var"] + eps), "hf_energy_ratio": t["hf_energy"] / (r["hf_energy"] + eps)}


def hf_fidelity(test, gt) -> Dict[str, float]:
    yt, yg = luma(as_u8_rgb(test)), luma(as_u8_rgb(gt))
    _same_shape(yt, yg)
    ht, hg = highpass(yt), highpass(yg)
    ht64, hg64 = ht.astype(np.float64), hg.astype(np.float64)
    eg = float(np.mean(hg64 * hg64)) + 1e-9
    corr_den = math.sqrt(float(np.mean(ht64 * ht64)) * float(np.mean(hg64 * hg64))) + 1e-9
    return {
        "hf_corr": float(np.mean(ht64 * hg64)) / corr_den,
        "hf_nmse": float(np.mean((ht64 - hg64) ** 2)) / eg,
    }


def gmsd(test, gt) -> float:
    """Gradient Magnitude Similarity Deviation (Xue et al. 2014): lower = closer to GT, tracks perceived quality better
    than PSNR.  Luma, 2x2 average + stride-2 first, Prewitt gradients, c = 170."""
    def prep(rgb):
        y = luma(as_u8_rgb(rgb))
        h, w = y.shape[0] // 2 * 2, y.shape[1] // 2 * 2
        y = y[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
        return y

    def gradmag(y):
        p = np.pad(y, 1, mode="edge")
        gx = (p[:-2, 2:] + p[1:-1, 2:] + p[2:, 2:] - p[:-2, :-2] - p[1:-1, :-2] - p[2:, :-2]) / 3.0
        gy = (p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:] - p[:-2, :-2] - p[:-2, 1:-1] - p[:-2, 2:]) / 3.0
        return np.sqrt(gx * gx + gy * gy)

    a, b = prep(test), prep(gt)
    _same_shape(a, b)
    ga, gb = gradmag(a), gradmag(b)
    c = 170.0
    gms = (2 * ga * gb + c) / (ga * ga + gb * gb + c)
    return float(np.std(gms))


def full_report(test, gt) -> Dict[str, float]:
    """All full-reference metrics for ``test`` against ``gt`` (identical sizes)."""
    t, g = as_u8_rgb(test), as_u8_rgb(gt)
    rep: Dict[str, float] = {
        "psnr_rgb": psnr(t, g),
        "psnr_y": psnr_y(t, g),
        "ssim_y": ssim_y(t, g),
    }
    sr = sharpness_ratio(t, g)
    rep["lap_var_ratio"] = sr["lap_var_ratio"]
    rep["hf_energy_ratio"] = sr["hf_energy_ratio"]
    rep.update(hf_fidelity(t, g))
    rep["gmsd"] = gmsd(t, g)
    return rep


# --------------------------------------------------------------------------------------------------------------
# seam metric (tile boundaries)
# --------------------------------------------------------------------------------------------------------------


def seam_ratio(rgb, cols=(), rows=(), halfwidth: int = 4) -> float:
    """Mean ratio of the gradient *at* a seam line to the gradient on the lines around it.

    ``cols``/``rows`` are the output coordinates x (resp. y) where a tile boundary sits between pixel x-1 and x.
    Natural content gives ~1.0; a hard visible seam gives a clear excess (>1.3).  Returns 1.0 when there is nothing
    to measure.
    """
    y = luma(as_u8_rgb(rgb))
    h, w = y.shape
    ratios = []
    dx = np.abs(np.diff(y, axis=1))  # dx[:, i] = |y[:, i+1] - y[:, i]|  -> seam at x means index x-1
    dy = np.abs(np.diff(y, axis=0))
    for x in cols:
        i = x - 1
        if i - halfwidth < 0 or i + halfwidth >= dx.shape[1]:
            continue
        seam = float(dx[:, i].mean())
        ref = float(np.mean([dx[:, i + k].mean() for k in range(-halfwidth, halfwidth + 1) if k != 0]))
        ratios.append(seam / (ref + 1e-6))
    for yy in rows:
        i = yy - 1
        if i - halfwidth < 0 or i + halfwidth >= dy.shape[0]:
            continue
        seam = float(dy[i, :].mean())
        ref = float(np.mean([dy[i + k, :].mean() for k in range(-halfwidth, halfwidth + 1) if k != 0]))
        ratios.append(seam / (ref + 1e-6))
    return float(np.mean(ratios)) if ratios else 1.0


def seam_report(rgb, cols=(), rows=(), halfwidth: int = 4) -> Dict[str, float]:
    """Seam gradient ratio at tile seams, at control lines halfway between the seams, and the excess of the former.

    Content makes the raw ratio wander around 1; ``excess`` (seam - control) is what a seam actually adds.
    """
    cols, rows = sorted(cols), sorted(rows)

    def mids(v):
        if len(v) >= 2:
            return [(a + b) // 2 for a, b in zip(v, v[1:])]
        return [v[0] + 7] if v else []

    seam = seam_ratio(rgb, cols, rows, halfwidth)
    ctrl = seam_ratio(rgb, mids(cols), mids(rows), halfwidth)
    return {"seam": float(seam), "control": float(ctrl), "excess": float(seam - ctrl)}


# --------------------------------------------------------------------------------------------------------------
# degradation (GT -> LR)
# --------------------------------------------------------------------------------------------------------------


def degrade_area(gt, factor: int) -> np.ndarray:
    """Fixed-kernel downscale used by the benchmark: exact area average (BOX) by an integer factor."""
    img = gt if isinstance(gt, Image.Image) else Image.fromarray(as_u8_rgb(gt))
    w, h = img.size
    if w % factor or h % factor:
        raise ValueError(f"GT size {w}x{h} must be divisible by {factor}")
    return np.asarray(img.resize((w // factor, h // factor), Image.BOX))


def degrade(gt, factor: int, kind: str = "area") -> np.ndarray:
    """GT -> LR.  ``area``: exact area average.  ``area_jpegNN``: area average then JPEG quality NN (real-world-like)."""
    lr = degrade_area(gt, factor)
    if kind == "area":
        return lr
    if kind.startswith("area_jpeg"):
        import io
        q = int(kind[len("area_jpeg"):])
        buf = io.BytesIO()
        Image.fromarray(lr).save(buf, format="JPEG", quality=q, subsampling=2)  # 4:2:0 like phone / web JPEGs
        buf.seek(0)
        return np.asarray(Image.open(buf).convert("RGB"))
    raise ValueError(f"unknown degradation {kind!r}")


def lanczos_up(lr, size: Tuple[int, int]) -> np.ndarray:
    img = lr if isinstance(lr, Image.Image) else Image.fromarray(as_u8_rgb(lr))
    return np.asarray(img.resize(size, Image.LANCZOS))


def bicubic_up(lr, size: Tuple[int, int]) -> np.ndarray:
    img = lr if isinstance(lr, Image.Image) else Image.fromarray(as_u8_rgb(lr))
    return np.asarray(img.resize(size, Image.BICUBIC))


# --------------------------------------------------------------------------------------------------------------
# procedural corpus
# --------------------------------------------------------------------------------------------------------------

SYNTH_NAMES = ("text", "fabric", "hair", "foliage", "architecture", "chart")

_FONT_CANDIDATES = {
    "serif": ["times.ttf", "georgia.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"],
    "sans": ["arial.ttf", "verdana.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"],
}
_FONT_DIRS = [Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation")]


def _font(kind: str, size: int) -> ImageFont.ImageFont:
    for name in _FONT_CANDIDATES[kind]:
        for d in _FONT_DIRS:
            p = d / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:  # pragma: no cover
                    pass
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except Exception:  # pragma: no cover
        return ImageFont.load_default()


_LOREM = (
    "The quick brown fox jumps over the lazy dog while seven quartz sphinxes judge my vow. Pack my box with five dozen "
    "liquor jugs; how vexingly quick daft zebras jump. Sphinx of black quartz, judge my vow. Sixty zippers were quickly "
    "picked from the woven jute bag. 0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz "
    "Resolution cannot be invented: fine print below the sampling limit stays unreadable."
).split()


def _noise(rng: np.random.Generator, w: int, h: int, cells: int, octaves: int = 5, persistence: float = 0.55) -> np.ndarray:
    """Fractal value noise in [0,1], float32 (h, w)."""
    out = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        c = cells * (2 ** o)
        cw = max(2, int(round(c * w / max(h, w))))
        ch = max(2, int(round(c * h / max(h, w))))
        small = rng.random((ch, cw), dtype=np.float32)
        up = np.asarray(Image.fromarray(small).resize((w, h), Image.BICUBIC), dtype=np.float32)
        out += amp * up
        total += amp
        amp *= persistence
    out /= total
    lo, hi = float(out.min()), float(out.max())
    return (out - lo) / (hi - lo + 1e-9)


def _finish(arr: np.ndarray, rng: np.random.Generator, noise_sigma: float = 0.8) -> Image.Image:
    """Add a little sensor-like noise and quantise (GT is a 'photo-like' 8-bit image)."""
    a = arr.astype(np.float32)
    a += rng.normal(0.0, noise_sigma, a.shape).astype(np.float32)
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8))


def _scene_text(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    img = Image.new("RGB", (w, h), (244, 240, 230))
    d = ImageDraw.Draw(img)
    y = 40
    # headline + paragraphs in decreasing sizes
    plan = [("serif", 76, 1), ("sans", 44, 2), ("serif", 30, 6), ("sans", 22, 7), ("mono", 18, 8), ("serif", 15, 9)]
    for kind, size, lines in plan:
        f = _font(kind, size)
        for _ in range(lines):
            if y > h - size - 20:
                break
            x, line = 48, ""
            words = []
            start = int(rng.integers(0, len(_LOREM)))
            for k in range(60):
                words.append(_LOREM[(start + k) % len(_LOREM)])
            for wd in words:
                cand = (line + " " + wd).strip()
                if d.textlength(cand, font=f) > w - 96:
                    break
                line = cand
            d.text((x, y), line, font=f, fill=(22, 22, 26))
            y += int(size * 1.35)
        y += 18
    # table of thin ruled lines (1-2 px) and a barcode
    ty = min(y + 10, h - 260)
    for r in range(8):
        d.line([(48, ty + r * 26), (w - 48, ty + r * 26)], fill=(60, 60, 70), width=1 + (r % 2))
    for c in range(9):
        x = 48 + c * (w - 96) // 8
        d.line([(x, ty), (x, ty + 7 * 26)], fill=(60, 60, 70), width=1)
    f = _font("sans", 14)
    for r in range(7):
        for c in range(8):
            x = 52 + c * (w - 96) // 8
            d.text((x, ty + r * 26 + 5), f"{int(rng.integers(100, 9999))}.{int(rng.integers(10, 99))}", font=f, fill=(30, 30, 40))
    bx = 48
    by = min(ty + 7 * 26 + 24, h - 70)
    while bx < w // 2:
        bw = int(rng.integers(1, 5))
        d.rectangle([bx, by, bx + bw - 1, by + 52], fill=(10, 10, 10))
        bx += bw + int(rng.integers(1, 4))
    arr = np.asarray(img).astype(np.float32)
    paper = _noise(rng, w, h, 6, 4)[:, :, None] * 6.0
    return _finish(arr - paper, rng, 0.8)


def _scene_fabric(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    p = 14
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # slight rotation/warp of the weave so it is not perfectly grid aligned
    warp = (_noise(rng, w, h, 3, 3) - 0.5) * 18.0
    u = xx + warp
    v = yy - warp * 0.6
    i = np.floor(u / p).astype(np.int32)
    j = np.floor(v / p).astype(np.int32)
    fu = (u / p) - np.floor(u / p)
    fv = (v / p) - np.floor(v / p)
    top_is_warp = ((i + j) % 2) == 0
    # thread cross-section: bright ridge in the middle, dark gaps at the edges
    ridge_u = np.sin(np.pi * fu) ** 0.8
    ridge_v = np.sin(np.pi * fv) ** 0.8
    along_u = 0.75 + 0.25 * np.sin(np.pi * fv)  # warp thread dims where it dives under
    along_v = 0.75 + 0.25 * np.sin(np.pi * fu)
    shade = np.where(top_is_warp, ridge_u * along_u, ridge_v * along_v)
    shade = 0.25 + 0.75 * shade
    base = np.array([168, 62, 52], np.float32)  # dyed red-brown cotton
    tint = np.where(top_is_warp, 1.0, 0.82)[:, :, None]
    thread_var = (_noise(rng, w, h, 40, 2)[:, :, None] - 0.5) * 0.35 + 1.0
    img = base[None, None, :] * shade[:, :, None] * tint * thread_var
    fold = (_noise(rng, w, h, 3, 4) - 0.5) * 0.9 + 1.0
    img = img * fold[:, :, None]
    # fibre fuzz
    img += rng.normal(0, 5.0, (h, w, 1)).astype(np.float32)
    # a stitched seam across the cloth
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    sy = h // 2 + 40
    d.line([(0, sy), (w, sy)], fill=(30, 20, 20), width=3)
    for x in range(0, w, 22):
        d.line([(x, sy - 7), (x + 11, sy + 7)], fill=(225, 205, 160), width=2)
    return _finish(np.asarray(pil), rng, 0.8)


def _scene_hair(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    ss = 2
    W, H = w * ss, h * ss
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    bg = np.zeros((H, W, 3), np.float32)
    bg[:] = (np.array([212, 205, 196], np.float32) * (1 - yy) + np.array([120, 108, 100], np.float32) * yy)[:, None, :].reshape(H, 1, 3)
    img = Image.fromarray(bg.astype(np.uint8))
    d = ImageDraw.Draw(img)
    palette = [(34, 22, 16), (58, 38, 26), (82, 54, 36), (112, 78, 52), (150, 108, 70), (186, 146, 100), (24, 16, 12)]
    flow = _noise(rng, 64, 48, 3, 3)
    for s in range(1900):
        x = float(rng.uniform(-0.05, 1.05) * W)
        y = float(rng.uniform(-0.1, 0.35) * H)
        ang = math.pi / 2 + (rng.normal(0, 0.55))
        col = palette[int(rng.integers(0, len(palette)))]
        bright = float(rng.uniform(0.75, 1.15))
        col = tuple(int(min(255, c * bright)) for c in col)
        width = int(rng.choice([2, 2, 3, 3, 4]))
        pts = []
        steps = int(rng.integers(120, 420))
        curv = float(rng.normal(0, 0.012))
        for k in range(steps):
            pts.append((x, y))
            fx = int(np.clip(x / W * 63, 0, 63))
            fy = int(np.clip(y / H * 47, 0, 47))
            ang += curv + (flow[fy, fx] - 0.5) * 0.035 + float(rng.normal(0, 0.012))
            x += math.cos(ang) * 4.0
            y += math.sin(ang) * 4.0
            if y > H + 20:
                break
        if len(pts) > 2:
            d.line(pts, fill=col, width=width, joint="curve")
    img = img.resize((w, h), Image.BOX)
    img = img.filter(ImageFilter.GaussianBlur(0.35))
    return _finish(np.asarray(img), rng, 0.8)


def _scene_foliage(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    n = _noise(rng, w, h, 8, 6, 0.62)
    greens = np.array([[24, 52, 22], [52, 96, 36], [96, 138, 52], [150, 176, 80]], np.float32)
    idx = n * (len(greens) - 1)
    lo = np.clip(np.floor(idx).astype(np.int32), 0, len(greens) - 2)
    t = (idx - lo)[:, :, None]
    base = greens[lo] * (1 - t) + greens[lo + 1] * t
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    ss = Image.new("RGB", (w * 2, h * 2))
    ss.paste(img.resize((w * 2, h * 2), Image.BICUBIC))
    d = ImageDraw.Draw(ss)
    for _ in range(2600):  # leaves with veins
        cx, cy = rng.uniform(0, w * 2), rng.uniform(0, h * 2)
        L = rng.uniform(14, 60)
        a = rng.uniform(0, math.tau)
        wd = L * rng.uniform(0.25, 0.45)
        g = int(rng.integers(70, 190))
        col = (int(g * rng.uniform(0.35, 0.8)), g, int(g * rng.uniform(0.2, 0.5)))
        ca, sa = math.cos(a), math.sin(a)
        poly = []
        for t_ in np.linspace(-1, 1, 9):
            r = (1 - t_ * t_) * wd
            poly.append((cx + ca * t_ * L / 2 - sa * r, cy + sa * t_ * L / 2 + ca * r))
        for t_ in np.linspace(1, -1, 9):
            r = (1 - t_ * t_) * wd
            poly.append((cx + ca * t_ * L / 2 + sa * r, cy + sa * t_ * L / 2 - ca * r))
        d.polygon(poly, fill=col)
        d.line([(cx - ca * L / 2, cy - sa * L / 2), (cx + ca * L / 2, cy + sa * L / 2)], fill=(col[0] + 25, col[1] + 30, col[2] + 15), width=1)
    for _ in range(2200):  # grass blades in the lower third
        x0 = rng.uniform(0, w * 2)
        y0 = rng.uniform(h * 1.4, h * 2.0)
        ln = rng.uniform(30, 120)
        a = -math.pi / 2 + rng.normal(0, 0.35)
        bend = rng.normal(0, 0.004)
        pts, x, y = [], x0, y0
        for _k in range(14):
            pts.append((x, y))
            a += bend * ln
            x += math.cos(a) * ln / 14
            y += math.sin(a) * ln / 14
        g = int(rng.integers(90, 200))
        d.line(pts, fill=(int(g * 0.55), g, int(g * 0.3)), width=int(rng.choice([1, 2, 2, 3])))
    out = ss.resize((w, h), Image.BOX)
    return _finish(np.asarray(out), rng, 0.9)


def _scene_architecture(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    ss = 2
    W, H = w * ss, h * ss
    img = Image.new("RGB", (W, H), (150, 140, 130))
    d = ImageDraw.Draw(img)
    bh, bw, mortar = 22 * ss // 2 * 2, 52 * ss, 3
    row = 0
    for y in range(0, H, bh):
        off = (bw // 2) if row % 2 else 0
        for x in range(-off, W, bw):
            r = int(rng.integers(120, 172))
            col = (r, int(r * rng.uniform(0.45, 0.58)), int(r * rng.uniform(0.35, 0.45)))
            d.rectangle([x + mortar, y + mortar, x + bw - 1, y + bh - 1], fill=col)
        row += 1
    # windows with frames, mullions, glass reflections
    cols, rows_ = 5, 3
    wx, wy = W // (cols + 1), H // (rows_ + 2)
    for r_ in range(rows_):
        for c_ in range(cols):
            x0 = (c_ + 1) * W // (cols + 1) - wx // 3
            y0 = (r_ + 1) * H // (rows_ + 2)
            x1, y1 = x0 + int(wx * 0.62), y0 + int(wy * 0.82)
            d.rectangle([x0 - 10, y0 - 10, x1 + 10, y1 + 10], fill=(222, 218, 206))
            d.rectangle([x0, y0, x1, y1], fill=(36, 54, 74))
            for k in range(0, (y1 - y0), 5):  # diagonal glare lines
                d.line([(x0, y0 + k), (min(x1, x0 + k), y0)], fill=(70, 100, 130), width=1)
            d.line([((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)], fill=(226, 222, 210), width=4)
            for k in range(1, 4):
                d.line([(x0, y0 + k * (y1 - y0) // 4), (x1, y0 + k * (y1 - y0) // 4)], fill=(226, 222, 210), width=3)
    # shop sign with big and small lettering
    d.rectangle([W // 8, H - H // 5, W - W // 8, H - H // 8], fill=(18, 40, 34))
    d.text((W // 8 + 40, H - H // 5 + 12), "BOSSMAN HARDWARE & TOOLS", font=_font("sans", 96), fill=(235, 220, 160))
    d.text((W // 8 + 44, H - H // 5 + 130), "Fasteners, hinges, locks, paint, wire, glass, timber since 1927", font=_font("serif", 42), fill=(220, 215, 200))
    # fire escape: thin diagonal rails and ladder rungs
    fx = int(W * 0.86)
    for k in range(0, 5):
        yk = H // 6 + k * H // 6
        d.line([(fx - 160, yk), (fx + 120, yk)], fill=(20, 20, 20), width=4)
        d.line([(fx - 160, yk + 60), (fx + 120, yk + 60)], fill=(20, 20, 20), width=2)
        for xk in range(fx - 160, fx + 121, 14):
            d.line([(xk, yk), (xk, yk + 60)], fill=(30, 30, 30), width=2)
        d.line([(fx + 120, yk), (fx + 40, yk + H // 6)], fill=(25, 25, 25), width=3)
    # parallel hatch band (very fine regular detail)
    hy = H // 14
    for k in range(0, 300, 6):
        d.line([(W // 20, hy + k // 3), (W // 20 + 600, hy + k // 3)], fill=(40, 40, 40), width=1)
    out = img.resize((w, h), Image.BOX)
    arr = np.asarray(out).astype(np.float32)
    shade = (_noise(rng, w, h, 2, 3)[:, :, None] - 0.5) * 40.0
    return _finish(arr + shade, rng, 0.8)


def _scene_chart(w: int, h: int, rng: np.random.Generator) -> Image.Image:
    ss = 2
    W, H = w * ss, h * ss
    img = Image.new("RGB", (W, H), (128, 128, 128))
    d = ImageDraw.Draw(img)
    # siemens star
    cx, cy, R = int(W * 0.27), int(H * 0.40), int(min(W, H) * 0.30)
    spokes = 72
    for k in range(spokes):
        a0 = math.tau * k / spokes
        a1 = math.tau * (k + 0.5) / spokes
        d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=(240, 240, 240))
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(10, 10, 10), width=3)
    # zone plate
    zx0, zy0, zs = int(W * 0.55), int(H * 0.10), int(min(W, H) * 0.34)
    yy, xx = np.mgrid[0:zs, 0:zs].astype(np.float32)
    r2 = ((xx - zs / 2) ** 2 + (yy - zs / 2) ** 2) / (zs / 2)
    zp = (127.5 + 127.5 * np.cos(math.pi * r2 / 6.0)).astype(np.uint8)
    img.paste(Image.fromarray(zp).convert("RGB"), (zx0, zy0))
    # slanted-edge squares
    for k, ang in enumerate((5, 9, 14)):
        sq = Image.new("RGB", (int(zs * 0.30),) * 2, (230, 230, 230))
        dd = ImageDraw.Draw(sq)
        s = sq.size[0]
        dd.polygon([(0, 0), (s, 0), (s, s), (0, s)], fill=(235, 235, 235))
        t = math.tan(math.radians(ang))
        dd.polygon([(0, 0), (s / 2 + t * s / 2, 0), (s / 2 - t * s / 2, s), (0, s)], fill=(20, 20, 20))
        img.paste(sq, (zx0 + k * (s + 20), zy0 + zs + 40))
    # line-pair ladders (period 2..16 px at final size => 4..32 at 2x)
    ly = int(H * 0.72)
    x = int(W * 0.06)
    for period in (4, 6, 8, 10, 12, 16, 20, 28):
        for k in range(14):
            xx0 = x + k * period
            d.rectangle([xx0, ly, xx0 + period // 2 - 1, ly + 150], fill=(20, 20, 20))
        for k in range(14):
            yy0 = ly + 190 + k * period
            d.rectangle([x, yy0, x + 14 * period, yy0 + period // 2 - 1], fill=(20, 20, 20))
        x += 14 * period + 60
        if x > W * 0.9:
            break
    # ramp
    for k in range(0, int(W * 0.30)):
        v = int(255 * k / (W * 0.30))
        d.line([(int(W * 0.60) + k, int(H * 0.58)), (int(W * 0.60) + k, int(H * 0.58) + 60)], fill=(v, v, v))
    d.text((int(W * 0.60), int(H * 0.52)), "MTF CHART 0123456789", font=_font("mono", 46), fill=(10, 10, 10))
    out = img.resize((w, h), Image.BOX)
    return _finish(np.asarray(out), rng, 0.6)


_SCENES: Dict[str, Callable[[int, int, np.random.Generator], Image.Image]] = {
    "text": _scene_text,
    "fabric": _scene_fabric,
    "hair": _scene_hair,
    "foliage": _scene_foliage,
    "architecture": _scene_architecture,
    "chart": _scene_chart,
}


def synth_scene(name: str, width: int = 2048, height: int = 1536, seed: int = 20261012) -> np.ndarray:
    """Deterministic procedural RGB uint8 scene (``name`` in SYNTH_NAMES).  Same arguments -> identical pixels."""
    if name not in _SCENES:
        raise KeyError(f"unknown scene {name!r}; choose from {SYNTH_NAMES}")
    salt = sum(ord(c) * (i + 1) for i, c in enumerate(name))
    rng = np.random.default_rng(seed + salt)
    return np.asarray(_SCENES[name](width, height, rng))


# --------------------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------------------------
# benchmark: every candidate on the same GT corpus (GT -> area-downscale xF -> candidate -> compare with GT)
# --------------------------------------------------------------------------------------------------------------


def chain_plan(factor: int, scale: int) -> Tuple[list, int]:
    """Network applications needed to reach ``factor`` with a model of native ``scale``.

    Returns (steps, reduce) where each step multiplies by ``scale`` and the result is area-reduced by ``reduce``.
    """
    if scale >= factor:
        if scale % factor:
            raise ValueError(f"model scale {scale} is not a multiple of factor {factor}")
        return [scale], scale // factor
    n = int(math.ceil(math.log(factor) / math.log(scale) - 1e-9))
    reach = scale ** n
    if reach % factor:
        raise ValueError(f"model scale {scale} cannot reach factor {factor} exactly")
    return [scale] * n, reach // factor


def _tag(factor: int, kind: str = "area") -> str:
    return f"x{factor}" if kind == "area" else f"x{factor}_{kind}"


def _gt_images(gt_dir: Path, only: Optional[list] = None) -> list:
    files = sorted(p for p in gt_dir.glob("*.png"))
    if only:
        files = [p for p in files if p.stem in only]
    return files


def cmd_bench_worker(args) -> int:
    """Run ONE model over the corpus inside this process (so peak RAM / crashes are per model)."""
    import time
    from . import upscale as U

    gt_dir, out_dir = Path(args.gt_dir), Path(args.out_dir) / _tag(args.factor, args.degrade) / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = U.FRAME_MODELS[args.model]
    res: Dict = {"model": args.model, "factor": args.factor, "degrade": args.degrade, "images": {}}
    t0 = time.perf_counter()
    runner = U.OnnxRunner(U.model_path(spec), device=args.device)
    res["load_seconds"] = round(time.perf_counter() - t0, 2)
    res["execution_provider"] = runner.execution_provider
    res["providers"] = runner.providers
    res["ort_version"] = runner.ort_version
    steps, reduce_ = chain_plan(args.factor, spec.scale)
    tile = spec.tile if spec.fixed else args.tile
    # warm-up on one tile shape (DirectML compiles kernels for a new input shape on first run)
    t0 = time.perf_counter()
    try:
        dummy = np.zeros((1, 3, tile, tile), np.float32)
        runner(dummy)
        res["warmup_seconds"] = round(time.perf_counter() - t0, 2)
    except Exception as e:
        res["fatal"] = f"{type(e).__name__}: {e}"
        Path(args.json).write_text(json.dumps(res, indent=1))
        return 1
    for gp in _gt_images(gt_dir, args.only.split(",") if args.only else None):
        gt = as_u8_rgb(Image.open(gp))
        lr = degrade(gt, args.factor, args.degrade)
        rec: Dict = {"in_px": [lr.shape[1], lr.shape[0]], "out_px": [gt.shape[1], gt.shape[0]]}
        try:
            cur = lr
            t0 = time.perf_counter()
            inf = 0.0
            tiles = 0
            seams = []
            for _st in steps:
                st_stats = U.TileStats()
                cur = U.tiled_upscale(cur, runner, spec.scale, tile=tile, overlap=args.overlap,
                                      fixed_tile=spec.fixed, stats=st_stats)
                inf += st_stats.infer_seconds
                tiles += st_stats.tiles
                if st_stats.tiles > 1:
                    seams.append(seam_report(cur, st_stats.seam_cols, st_stats.seam_rows)["excess"])
            if reduce_ > 1:
                cur = U.box_reduce(cur, reduce_)
            dt = time.perf_counter() - t0
            if cur.shape != gt.shape:
                raise RuntimeError(f"output {cur.shape} != GT {gt.shape}")
            Image.fromarray(cur).save(out_dir / f"{gp.stem}.png", compress_level=1)
            rec.update({"seconds": round(dt, 3), "infer_seconds": round(inf, 3), "tiles": tiles,
                        "seconds_per_out_mp": round(dt / (gt.shape[0] * gt.shape[1] / 1e6), 3),
                        "seam_excess": round(float(np.mean(seams)), 4) if seams else None,
                        "const_output": bool(float(cur.std()) == 0.0)})
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        pk = U.peak_working_set_mb()
        rec["peak_working_set_mb"] = None if pk is None else round(pk, 1)
        res["images"][gp.stem] = rec
        print(args.model, gp.stem, rec.get("seconds"), rec.get("error", ""), flush=True)
        Path(args.json).write_text(json.dumps(res, indent=1))
    pk = U.peak_working_set_mb()
    res["peak_working_set_mb"] = None if pk is None else round(pk, 1)
    Path(args.json).write_text(json.dumps(res, indent=1))
    return 0


def cmd_bench_run(args) -> int:
    """Spawn one worker process per model (FaceFusion venv python), with a timeout, collecting crashes verbatim."""
    import os
    import subprocess
    import time
    from . import upscale as U

    py = U.facefusion_python()
    if py is None:
        print("ERROR: FaceFusion venv python not found", file=sys.stderr)
        return 1
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    models = args.models.split(",")
    env = dict(os.environ, PYTHONPATH=str(U.tools_dir()))
    sj = out_dir / f"_summary_{_tag(args.factor, args.degrade_kind)}.json"
    summary = json.loads(sj.read_text()) if sj.exists() else {}
    for m in models:
        jp = out_dir / f"_run_{m}_{_tag(args.factor, args.degrade_kind)}.json"
        cmd = [str(py), "-m", "photo_upscale.measure", "bench-worker", "--model", m, "--gt-dir", args.gt_dir,
               "--out-dir", str(out_dir), "--factor", str(args.factor), "--json", str(jp),
               "--tile", str(args.tile), "--overlap", str(args.overlap), "--device", args.device,
               "--degrade", args.degrade_kind]
        if args.only:
            cmd += ["--only", args.only]
        t0 = time.perf_counter()
        try:
            cp = subprocess.run(cmd, cwd=str(U.tools_dir()), env=env, capture_output=True, text=True, timeout=args.timeout)
            rc, tail = cp.returncode, (cp.stdout + "\n" + cp.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            rc, tail = -9, f"TIMEOUT after {args.timeout}s"
        entry = {"returncode": rc, "wall_seconds": round(time.perf_counter() - t0, 1)}
        if rc != 0:
            entry["failure_text"] = tail
        if jp.exists():
            try:
                entry["run"] = json.loads(jp.read_text())
            except Exception:
                pass
        summary[m] = entry
        print(f"[{m}] rc={rc} {entry['wall_seconds']}s", flush=True)
        sj.write_text(json.dumps(summary, indent=1))
    return 0


def _group(name: str) -> str:
    return "synthetic" if name.startswith("synth_") else "real"


def _mean(vals) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


def cmd_bench_score(args) -> int:
    """Score every model output (and the classical baselines) against GT; write results JSON + a text table."""
    import time

    gt_dir, out_dir = Path(args.gt_dir), Path(args.out_dir)
    factor = args.factor
    kind = args.degrade_kind
    gts = {p.stem: p for p in _gt_images(gt_dir, args.only.split(",") if args.only else None)}
    rows: Dict[str, Dict] = {}
    for label, fn in (("lanczos", lanczos_up), ("bicubic", bicubic_up)):  # classical baselines (timed here)
        per = {}
        for stem, gp in gts.items():
            gt = as_u8_rgb(Image.open(gp))
            lr = degrade(gt, factor, kind)
            t0 = time.perf_counter()
            up = fn(lr, (gt.shape[1], gt.shape[0]))
            dt = time.perf_counter() - t0
            rep = full_report(up, gt)
            rep["seconds"] = dt
            rep["seconds_per_out_mp"] = dt / (gt.shape[0] * gt.shape[1] / 1e6)
            per[stem] = rep
        rows[label] = {"images": per}
    if args.ibp:
        from . import upscale as U
        per = {}
        for stem, gp in gts.items():
            gt = as_u8_rgb(Image.open(gp))
            lr = degrade(gt, factor, kind)
            per[stem] = full_report(U.backproject(lanczos_up(lr, (gt.shape[1], gt.shape[0])), lr, factor, iters=2), gt)
        rows["lanczos+ibp"] = {"images": per}
    runs = {}
    sj = out_dir / f"_summary_{_tag(factor, kind)}.json"
    if sj.exists():
        runs = json.loads(sj.read_text())
    base = out_dir / _tag(factor, kind)
    only_models = [v for v in (args.models or "").split(",") if v]
    for model_dir in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        m = model_dir.name
        if only_models and m not in only_models:
            continue
        run = (runs.get(m) or {}).get("run") or {}
        per = {}
        for stem, gp in gts.items():
            op = model_dir / f"{stem}.png"
            if not op.exists():
                continue
            gt = as_u8_rgb(Image.open(gp))
            rep = full_report(as_u8_rgb(Image.open(op)), gt)
            meta = (run.get("images") or {}).get(stem, {})
            for k in ("seconds", "seconds_per_out_mp", "seam_excess", "peak_working_set_mb", "tiles"):
                rep[k] = meta.get(k)
            per[stem] = rep
        if per:
            rows[m] = {"images": per, "load_seconds": run.get("load_seconds"), "warmup_seconds": run.get("warmup_seconds"),
                       "peak_working_set_mb": run.get("peak_working_set_mb"), "provider": run.get("execution_provider"),
                       "returncode": (runs.get(m) or {}).get("returncode")}
            if args.ibp:  # same outputs after the data-consistency projection (no extra model run)
                from . import upscale as U
                mixes = [int(v) for v in args.mixes.split(",") if v]
                per_i, per_m = {}, {a: {} for a in mixes}
                for stem, gp in gts.items():
                    op = model_dir / f"{stem}.png"
                    if not op.exists():
                        continue
                    gt = as_u8_rgb(Image.open(gp))
                    lr = degrade(gt, factor, kind)
                    raw = as_u8_rgb(Image.open(op))
                    t0 = time.perf_counter()
                    fixed = U.backproject(raw, lr, factor, iters=2)
                    dt = time.perf_counter() - t0
                    spm = (per[stem].get("seconds_per_out_mp") or 0) + dt / (gt.shape[0] * gt.shape[1] / 1e6)
                    per_i[stem] = {**full_report(fixed, gt), "seconds_per_out_mp": spm,
                                   "peak_working_set_mb": per[stem].get("peak_working_set_mb")}
                    lz = U.backproject(lanczos_up(lr, (gt.shape[1], gt.shape[0])), lr, factor, iters=2).astype(np.float32)
                    for a in mixes:  # detail strength: lanczos+ibp + a * (model+ibp - lanczos+ibp)
                        mix = np.clip(np.rint(lz + (a / 100.0) * (fixed.astype(np.float32) - lz)), 0, 255).astype(np.uint8)
                        per_m[a][stem] = {**full_report(mix, gt), "seconds_per_out_mp": spm,
                                          "peak_working_set_mb": per[stem].get("peak_working_set_mb")}
                rows[m + "+ibp"] = {"images": per_i, "peak_working_set_mb": run.get("peak_working_set_mb")}
                for a in mixes:
                    rows[f"{m}+ibp+d{a}"] = {"images": per_m[a], "peak_working_set_mb": run.get("peak_working_set_mb")}
    failures = {m: {"returncode": e.get("returncode"), "failure_text": e.get("failure_text")}
                for m, e in runs.items() if e.get("returncode") != 0}
    keys = ["psnr_rgb", "psnr_y", "ssim_y", "gmsd", "lap_var_ratio", "hf_energy_ratio", "hf_corr", "hf_nmse", "seconds_per_out_mp"]
    table = []
    for m, r in rows.items():
        per = r["images"]
        agg = {"model": m, "n_images": len(per)}
        for g in ("all", "synthetic", "real"):
            sel = [v for s, v in per.items() if g == "all" or _group(s) == g]
            for k in keys:
                agg[f"{k}_{g}"] = _mean([v.get(k) for v in sel])
        agg["peak_working_set_mb"] = r.get("peak_working_set_mb")
        agg["seam_excess_mean"] = _mean([v.get("seam_excess") for v in per.values()])
        table.append(agg)
    table.sort(key=lambda a: -(a.get("psnr_rgb_all") or 0))
    out = {"factor": factor, "degrade": kind, "metric_version": METRIC_VERSION, "rows": rows, "table": table, "failures": failures}
    Path(args.json).write_text(json.dumps(out, indent=1))

    def f(a, k, d=2):
        return "-" if a.get(k) is None else f"{a[k]:.{d}f}"

    print(f"{'model':24s} {'n':>2s} {'PSNR':>6s} {'SSIM':>6s} {'GMSD':>6s} {'lapR':>6s} {'hfE':>6s} {'hfCor':>6s} {'hfNMSE':>6s} "
          f"{'s/MP':>6s} {'PSNRsyn':>7s} {'PSNRreal':>8s} {'seam':>5s} {'RAMmb':>6s}")
    for a in table:
        print(f"{a['model']:24s} {a['n_images']:2d} {f(a,'psnr_rgb_all'):>6s} {f(a,'ssim_y_all',3):>6s} {f(a,'gmsd_all',3):>6s} "
              f"{f(a,'lap_var_ratio_all'):>6s} {f(a,'hf_energy_ratio_all'):>6s} {f(a,'hf_corr_all',3):>6s} "
              f"{f(a,'hf_nmse_all',3):>6s} {f(a,'seconds_per_out_mp_all',2):>6s} {f(a,'psnr_rgb_synthetic'):>7s} "
              f"{f(a,'psnr_rgb_real'):>8s} {f(a,'seam_excess_mean',3):>5s} {f(a,'peak_working_set_mb',0):>6s}")
    for m, e in failures.items():
        print(f"FAILED {m}: rc={e['returncode']} {str(e['failure_text'])[:400]!r}")
    return 0


def _load(p: str) -> np.ndarray:
    return as_u8_rgb(Image.open(p))


def cmd_compare(args) -> int:
    gt, test = _load(args.gt), _load(args.test)
    if gt.shape != test.shape:
        print(f"ERROR: size mismatch GT {gt.shape[1]}x{gt.shape[0]} vs test {test.shape[1]}x{test.shape[0]}", file=sys.stderr)
        return 2
    rep = {"metric_version": METRIC_VERSION, "vs_gt": full_report(test, gt)}
    if args.baseline:
        base = _load(args.baseline)
        rep["baseline_vs_gt"] = full_report(base, gt)
        rep["sharpness_vs_baseline"] = sharpness_ratio(test, base)
    print(json.dumps(rep, indent=2))
    return 0


def cmd_synth(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w = args.size
    h = (w * 3 // 4) // 8 * 8
    for name in SYNTH_NAMES:
        arr = synth_scene(name, w, h)
        p = out / f"synth_{name}.png"
        Image.fromarray(arr).save(p)
        print(p, arr.shape)
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="photo_upscale.measure", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="full-reference metrics of TEST against GT (same size)")
    c.add_argument("--gt", required=True)
    c.add_argument("--test", required=True)
    c.add_argument("--baseline", help="optional Lanczos/bicubic image of the same size for sharpness ratios")
    c.set_defaults(fn=cmd_compare)
    for nm, fn, hlp in (("bench-worker", cmd_bench_worker, "(internal) run one model over the corpus"),
                        ("bench-run", cmd_bench_run, "run all models, one subprocess each"),
                        ("bench-score", cmd_bench_score, "score outputs against GT, write JSON + table")):
        b = sub.add_parser(nm, help=hlp)
        b.add_argument("--gt-dir", required=True)
        b.add_argument("--out-dir", required=True)
        b.add_argument("--factor", type=int, default=4)
        b.add_argument("--only", help="comma separated GT stems")
        if nm == "bench-worker":
            b.add_argument("--degrade", default="area")
        else:
            b.add_argument("--degrade-kind", default="area", help="area | area_jpegNN (LR degradation)")
        if nm != "bench-score":
            b.add_argument("--tile", type=int, default=256)
            b.add_argument("--overlap", type=int, default=32)
            b.add_argument("--device", default="auto")
        if nm == "bench-worker":
            b.add_argument("--model", required=True)
            b.add_argument("--json", required=True)
        if nm == "bench-run":
            b.add_argument("--models", required=True)
            b.add_argument("--timeout", type=int, default=3600)
        if nm == "bench-score":
            b.add_argument("--json", required=True)
            b.add_argument("--ibp", action="store_true", help="also score every output after back-projection (+ibp, +ibp+dNN)")
            b.add_argument("--mixes", default="50,75", help="detail strengths (percent) for the +ibp+dNN rows")
            b.add_argument("--models", default="", help="comma separated model dirs to score (default: all)")
        b.set_defaults(fn=fn)
    s = sub.add_parser("synth", help="write the procedural ground-truth corpus")
    s.add_argument("--out", required=True)
    s.add_argument("--size", type=int, default=2048)
    s.set_defaults(fn=cmd_synth)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
