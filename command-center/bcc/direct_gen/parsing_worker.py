"""Human parsing masks for the Genjutsu live constructor (SegFormer-B2 «clothes», ONNX).

Standalone: numpy + opencv + onnxruntime only (no `bcc`, no torch). Bossman runs it with FaceFusion's interpreter
(`<facefusion>/.venv`, onnxruntime-directml already there) so nothing is installed into either venv:
    python parsing_worker.py --model <model.onnx> --image <frame.png> --out <dir> [--provider directml|cpu]
Writes <region>.png (8-bit SOFT masks, 0..255 = probability of the region) and masks.json (areas, timing, model).

Model: mattmdjaga/segformer_b2_clothes (ATR labels), revision 584abc1e, onnx/model.onnx sha256 a93a8dac…
LICENCE: NVIDIA Source Code License for SegFormer, section 3.3 — NON-COMMERCIAL use only ("research or evaluation
purposes only"). The masks.json of every run says so; a commercial product must switch to the FaceFusion engine
(masks_worker.py) or a permissively licensed parser.

Quality steps (measured on 3 owner frames 10.10, see the PR report):
  1. black screen-recording bars are cut away before inference (more pixels for the person);
  2. pass 1 on the whole picture finds the person; pass 2 re-runs on the padded person box (finer hair/tie edges);
  3. class probabilities (softmax) are summed per region, upsampled, then refined with a guided filter that follows
     the image's own edges (hair strands, collar), so masks are soft — recolour blends instead of a hard cut-out.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

LABELS = ("background", "hat", "hair", "sunglasses", "upper_clothes", "skirt", "pants", "dress", "belt",
          "left_shoe", "right_shoe", "face", "left_leg", "right_leg", "left_arm", "right_arm", "bag", "scarf")
#: region -> ATR class ids. top/bottom/dress come straight from the model (no "split at the middle" guess).
REGIONS: dict[str, tuple[int, ...]] = {
    "hair": (2,),
    "top": (4, 7, 17),              # upper clothes + dress + scarf: a dark T-shirt is often half "dress" (10.10)
    "bottom": (5, 6, 8),            # skirt, pants, belt
    "dress": (7,),
    "clothes": (4, 5, 6, 7, 8, 17),
    "skin": (11, 12, 13, 14, 15),
    "person": tuple(range(1, 18)),
}
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
SIZE = 512
LICENSE = {"id": "NVIDIA-SegFormer-NC", "commercial": False,
           "text": "NVIDIA Source Code License for SegFormer §3.3: non-commercial (research or evaluation) use only",
           "url": "https://github.com/NVlabs/SegFormer/blob/master/LICENSE"}
SOURCE = {"repo": "mattmdjaga/segformer_b2_clothes", "revision": "584abc1e1d260e23c0fc627c5217a09b2b461046",
          "file": "onnx/model.onnx", "sha256": "a93a8dac171b5c1fcc53632a8bfc180bfd9759ea69a3e207451bb07f76add54f"}


def picture_box(gray: np.ndarray, threshold: int = 18, share: float = 0.02):
    """(top, bottom, left, right) of the lit picture inside black bars; the whole frame when nothing is found."""
    lit = gray > threshold
    rows = np.where(lit.mean(axis=1) > share)[0]
    cols = np.where(lit.mean(axis=0) > share)[0]
    if len(rows) < 20 or len(cols) < 20:
        return 0, gray.shape[0], 0, gray.shape[1]
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """He et al. guided filter (grey guide): edges of `src` snap to edges of `guide`. Both float32 in 0..1."""
    k = (2 * radius + 1, 2 * radius + 1)
    mean = lambda x: cv2.boxFilter(x, cv2.CV_32F, k)  # noqa: E731
    m_i, m_p = mean(guide), mean(src)
    cov = mean(guide * src) - m_i * m_p
    var = mean(guide * guide) - m_i * m_i
    a = cov / (var + eps)
    b = m_p - a * m_i
    return np.clip(mean(a) * guide + mean(b), 0.0, 1.0)


class Parser:
    def __init__(self, model: Path, provider: str = "directml"):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"] if provider == "directml" else ["CPUExecutionProvider"]
        available = set(ort.get_available_providers())
        self.session = ort.InferenceSession(str(model), opts, providers=[p for p in providers if p in available])
        self.input = self.session.get_inputs()[0].name
        self.providers = self.session.get_providers()

    def probs(self, bgr: np.ndarray, size: tuple[int, int] = (SIZE, SIZE)) -> np.ndarray:
        """(18, H/4, W/4) softmax probabilities for a BGR crop resized to `size` (w, h); 512x512 = the HF processor."""
        x = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.0
        x = ((x - MEAN) / STD).transpose(2, 0, 1)[None]
        logits = self.session.run(None, {self.input: np.ascontiguousarray(x)})[0][0].astype(np.float32)
        logits -= logits.max(axis=0, keepdims=True)
        e = np.exp(logits)
        return e / e.sum(axis=0, keepdims=True)


def region_maps(probs: np.ndarray, width: int, height: int) -> dict[str, np.ndarray]:
    out = {}
    for name, ids in REGIONS.items():
        p = probs[list(ids)].sum(axis=0)
        out[name] = cv2.resize(p, (width, height), interpolation=cv2.INTER_LINEAR)
    return out


def fine_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    """Aspect-preserving inference size, sides multiples of 32, never upscaling a small crop past 512."""
    s = min(1.0, long_side / max(width, height)) if max(width, height) > SIZE else SIZE / max(width, height)
    return max(32, int(round(width * s / 32)) * 32), max(32, int(round(height * s / 32)) * 32)


def main_person(person: np.ndarray) -> np.ndarray:
    """Hard mask of the person(s) in front: components at least 25% of the largest one (drops coats on a rack)."""
    hard = (person > 0.5).astype(np.uint8)
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(hard, 8)
    if n <= 1:
        return hard
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + np.where(areas >= 0.25 * areas.max())[0]
    return np.isin(lbl, keep).astype(np.uint8)


CLOTHING = ("top", "bottom", "dress", "clothes")


def soft_keep(prob: np.ndarray, skin: np.ndarray, share: float = 0.15) -> np.ndarray:
    """Soft 0..1 gate for a clothing region: keep what is WORN, drop clothes hanging behind the person.

    Thin bridges (a strand of hair over a coat on a rack) are cut by an erosion first; then the largest blob is kept,
    plus any blob that is >= `share` of it AND touches the person's skin (neck, arms, legs). 10.10 f02: the coats on a
    rack right of the head were joined to the T-shirt through the hair and got recoloured with it."""
    hard = (prob > 0.5).astype(np.uint8)
    k = max(3, min(prob.shape) // 80)
    ball = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    core = cv2.erode(hard, ball)
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(core, 8)
    if n <= 2:
        return np.ones_like(prob)
    areas = stats[1:, cv2.CC_STAT_AREA]
    big = int(np.argmax(areas)) + 1
    near_skin = cv2.dilate((skin > 0.5).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3 * k, 3 * k)))
    touching = set(np.unique(lbl[(near_skin > 0) & (core > 0)]).tolist())
    keep = [big] + [i for i in range(1, n) if i != big and areas[i - 1] >= share * areas.max() and i in touching]
    kept = cv2.dilate(np.isin(lbl, keep).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    return cv2.GaussianBlur(kept.astype(np.float32), (0, 0), max(2, k // 2))


def smoothstep(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def parse(frame: np.ndarray, parser: Parser, *, refine: bool = True, long_side: int = 768) -> tuple[dict[str, np.ndarray], dict]:
    h, w = frame.shape[:2]
    t, b, l, r = picture_box(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    pic = frame[t:b, l:r]
    ph, pw = pic.shape[:2]
    maps = region_maps(parser.probs(pic), pw, ph)
    info = {"picture_box": [t, b, l, r], "passes": 1}
    ys, xs = np.where(maps["person"] > 0.5)
    if refine and len(ys) > 0.01 * ph * pw:
        # pass 2: the padded person box at a finer, aspect-true resolution (hair strands, tie, collar)
        y1, y2, x1, x2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        py, px = int((y2 - y1) * 0.06) + 8, int((x2 - x1) * 0.06) + 8
        y1, y2, x1, x2 = max(0, y1 - py), min(ph, y2 + py), max(0, x1 - px), min(pw, x2 + px)
        crop = pic[y1:y2, x1:x2]
        size = fine_size(x2 - x1, y2 - y1, long_side)
        fine = region_maps(parser.probs(crop, size), x2 - x1, y2 - y1)
        for name in maps:
            # inside the box: mean of the coarse (whole-context) and fine passes — context fixes the fine pass's
            # top/dress confusions, the fine pass sharpens edges
            maps[name][y1:y2, x1:x2] = 0.5 * maps[name][y1:y2, x1:x2] + 0.5 * fine[name]
        info.update(passes=2, person_box=[int(y1 + t), int(y2 + t), int(x1 + l), int(x2 + l)], fine_size=list(size))
    keep = cv2.dilate(main_person(maps["person"]), np.ones((9, 9), np.uint8)).astype(np.float32)
    keep = cv2.GaussianBlur(keep, (0, 0), 3)
    for name in maps:
        maps[name] = maps[name] * keep
        if name in CLOTHING:                                   # 10.10 f02: coats on a rack behind the hair
            maps[name] = maps[name] * soft_keep(maps[name], maps["skin"])
    guide = cv2.cvtColor(pic, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    radius = max(2, int(round(min(ph, pw) / 160)))
    full = {}
    for name, m in maps.items():
        soft = guided_filter(guide, m.astype(np.float32), radius, 1e-3)
        soft = smoothstep(0.2, 0.8, soft)                      # crisp but still anti-aliased edge band
        canvas = np.zeros((h, w), np.uint8)
        canvas[t:b, l:r] = np.round(soft * 255).astype(np.uint8)
        full[name] = canvas
    return full, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--provider", default="directml", choices=("directml", "cpu"))
    a = ap.parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if frame is None:
        print(json.dumps({"error": "image_unreadable"}))
        return 2
    t0 = time.perf_counter()
    parser = Parser(Path(a.model), a.provider)
    t1 = time.perf_counter()
    masks, info = parse(frame, parser)
    t2 = time.perf_counter()
    for name, m in masks.items():
        cv2.imwrite(str(out_dir / f"{name}.png"), m)
    result = {
        "engine": "segformer_b2_clothes", "size": [int(frame.shape[1]), int(frame.shape[0])],
        "regions": list(masks), "soft": True,
        "area": {k: round(float((v >= 128).mean()), 4) for k, v in masks.items()},
        "seconds": {"load": round(t1 - t0, 3), "parse": round(t2 - t1, 3)},
        "providers": parser.providers, "license": LICENSE, "source": SOURCE, **info,
    }
    (out_dir / "masks.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
