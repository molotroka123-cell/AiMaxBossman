"""Opt-in streaming primitives for the local Genjutsu E composite.

All inputs are caller-provided, time-aligned frames in BGR order. ``boxes`` use
coordinates in the already-aligned base frame. No paths, media, model downloads,
or permissions are inferred. The caller must exhaust the iterator before
promoting a temporary output: a late frame-count mismatch raises ``ValueError``.
This module does not preserve or remux audio; the caller owns that operation.
"""

from __future__ import annotations

from itertools import zip_longest
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

import cv2
import numpy as np


def _frame(value: np.ndarray, name: str, *, uint8: bool = True) -> np.ndarray:
    if not isinstance(value, np.ndarray) or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError(f"{name} geometry must be H×W×3 BGR")
    if not value.shape[0] or not value.shape[1] or not np.issubdtype(value.dtype, np.number):
        raise ValueError(f"{name} geometry or dtype is invalid")
    if uint8 and value.dtype != np.uint8:
        raise ValueError(f"{name} must be uint8")
    if not np.isfinite(value).all() or np.any(value < 0) or np.any(value > 255):
        raise ValueError(f"{name} values must be finite and between 0 and 255")
    return value


def _alpha(value: np.ndarray, shape: tuple[int, int], name: str) -> np.ndarray:
    if not isinstance(value, np.ndarray) or value.ndim != 2 or value.shape != shape:
        raise ValueError(f"{name} geometry must match its frame")
    if not np.issubdtype(value.dtype, np.number) or not np.isfinite(value).all():
        raise ValueError(f"{name} values must be finite")
    alpha = value.astype(np.float32)
    if value.dtype == np.uint8 and alpha.size and alpha.max() > 1:
        alpha /= 255.0
    if np.any(alpha < 0) or np.any(alpha > 1):
        raise ValueError(f"{name} values must be between 0 and 1")
    return alpha


def _box(box: Mapping[str, int], width: int, height: int) -> tuple[int, int, int]:
    if not isinstance(box, Mapping):
        raise ValueError("box must contain integer x, y and s")
    vals = [box.get(k) for k in ("x", "y", "s")]
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer)) for v in vals):
        raise ValueError("box must contain integer x, y and s")
    x, y, s = (int(v) for v in vals)
    if x < 0 or y < 0 or s <= 0 or x + s > width or y + s > height:
        raise ValueError("box must be entirely within the aligned frame")
    return x, y, s


def composite_head_frame(
    base: np.ndarray,
    foreground: np.ndarray,
    alpha: np.ndarray,
    box: Mapping[str, int],
    *,
    feather: float = 0.12,
    bottom_fade: tuple[float, float] | None = (0.74, 0.84),
    match_color: bool = True,
) -> np.ndarray:
    """Composite the RVM foreground into one aligned base frame without mutation."""
    _frame(base, "base")
    _frame(foreground, "foreground", uint8=False)
    mask = _alpha(alpha, foreground.shape[:2], "alpha")
    x, y, size = _box(box, base.shape[1], base.shape[0])
    if not np.isfinite(feather) or not 0 <= feather <= 0.5:
        raise ValueError("feather must be in [0, 0.5]")
    if bottom_fade is not None:
        if len(bottom_fade) != 2 or not all(np.isfinite(v) for v in bottom_fade):
            raise ValueError("bottom_fade must be a finite start/end pair")
        if not 0 <= bottom_fade[0] < bottom_fade[1] <= 1:
            raise ValueError("bottom_fade must satisfy 0 <= start < end <= 1")

    head = cv2.resize(foreground.astype(np.float32), (size, size), interpolation=cv2.INTER_AREA)
    a = cv2.resize(mask, (size, size), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    if feather:
        a *= np.clip(np.minimum.reduce([xx, 1 - xx, yy, 1 - yy]) / feather, 0, 1)
    if bottom_fade is not None:
        a *= np.clip((bottom_fade[1] - yy) / (bottom_fade[1] - bottom_fade[0]), 0, 1)

    roi = base[y:y + size, x:x + size].astype(np.float32)
    selected = a > 0.6
    if match_color and selected.sum() > 200:
        for channel in range(3):
            hm, hs = head[..., channel][selected].mean(), head[..., channel][selected].std() + 1e-6
            rm, rs = roi[..., channel][selected].mean(), roi[..., channel][selected].std() + 1e-6
            head[..., channel] = (head[..., channel] - hm) * np.clip(rs / hs, 0.77, 1.3) + 0.6 * rm + 0.4 * hm
    out = base.copy()
    mixed = roi * (1 - a[..., None]) + np.clip(head, 0, 255) * a[..., None]
    out[y:y + size, x:x + size] = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    return out


def restore_background_frame(
    generated: np.ndarray,
    original: np.ndarray,
    person_mask: np.ndarray,
    *,
    dilate_px: int = 31,
    blur_sigma: float = 6.0,
) -> np.ndarray:
    """Restore source pixels outside a caller-aligned person mask."""
    _frame(generated, "generated")
    _frame(original, "original")
    if generated.shape != original.shape:
        raise ValueError("generated and original geometry must match exactly")
    mask = _alpha(person_mask, generated.shape[:2], "person mask")
    if isinstance(dilate_px, bool) or not isinstance(dilate_px, int) or dilate_px < 0:
        raise ValueError("dilate_px must be a non-negative integer")
    if not np.isfinite(blur_sigma) or blur_sigma < 0:
        raise ValueError("blur_sigma must be finite and non-negative")
    if dilate_px:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px, dilate_px))
        mask = cv2.dilate(mask, kernel)
    if blur_sigma:
        mask = cv2.GaussianBlur(mask, (0, 0), blur_sigma)
    mask = np.clip(mask, 0, 1)[..., None]
    mixed = generated.astype(np.float32) * mask + original.astype(np.float32) * (1 - mask)
    return np.clip(np.rint(mixed), 0, 255).astype(np.uint8)


class RVMForeground:
    """A CPU RVM session with recurrent state retained across successive frames."""

    def __init__(self, model_path: str | Path | None = None, *, session=None, downsample_ratio: float = 0.5):
        if not np.isfinite(downsample_ratio) or not 0 < downsample_ratio <= 1:
            raise ValueError("downsample_ratio must be in (0, 1]")
        if session is None:
            if model_path is None or not Path(model_path).is_file():
                raise FileNotFoundError("an existing local RVM model path is required")
            import onnxruntime as ort

            session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.session = session
        self.ratio = np.array([downsample_ratio], dtype=np.float32)
        self.states = [np.zeros((1, 1, 1, 1), dtype=np.float32) for _ in range(4)]

    def __call__(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        _frame(frame, "head")
        src = frame[..., ::-1].astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        inputs = {"src": src, "downsample_ratio": self.ratio}
        inputs.update({f"r{i + 1}i": state for i, state in enumerate(self.states)})
        outputs = self.session.run(None, inputs)
        if len(outputs) != 6:
            raise ValueError("RVM must return foreground, alpha and four recurrent states")
        foreground, alpha, *states = outputs
        height, width = frame.shape[:2]
        if np.shape(foreground) != (1, 3, height, width) or np.shape(alpha) != (1, 1, height, width):
            raise ValueError("RVM output geometry does not match the head frame")
        if not np.isfinite(foreground).all() or not np.isfinite(alpha).all() or any(not np.isfinite(s).all() for s in states):
            raise ValueError("RVM output contains non-finite values")
        self.states = states
        bgr = np.clip(foreground[0].transpose(1, 2, 0)[..., ::-1] * 255, 0, 255).astype(np.float32)
        return bgr, np.clip(alpha[0, 0], 0, 1).astype(np.float32)


def iter_composite_e(
    base_frames: Iterable[np.ndarray],
    head_frames: Iterable[np.ndarray],
    source_frames: Iterable[np.ndarray],
    person_masks: Iterable[np.ndarray],
    boxes: Iterable[Mapping[str, int]],
    *,
    matte: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]],
    dilate_px: int = 31,
    blur_sigma: float = 6.0,
) -> Iterator[np.ndarray]:
    """Yield aligned E frames; never silently truncate unequal input sequences."""
    sentinel = object()
    geometry = None
    head_geometry = None
    count = 0
    for count, (base, head, source, mask, box) in enumerate(
        zip_longest(base_frames, head_frames, source_frames, person_masks, boxes, fillvalue=sentinel), start=1
    ):
        if any(item is sentinel for item in (base, head, source, mask, box)):
            raise ValueError(f"frame count mismatch at frame {count - 1}")
        _frame(base, "base")
        if geometry is None:
            geometry = base.shape
        elif base.shape != geometry:
            raise ValueError("base frame geometry changed during stream")
        _frame(head, "head")
        if head_geometry is None:
            head_geometry = head.shape
        elif head.shape != head_geometry:
            raise ValueError("head frame geometry changed during stream")
        if (not isinstance(box, Mapping) or isinstance(box.get("i"), (bool, np.bool_))
                or not isinstance(box.get("i"), (int, np.integer)) or box.get("i") != count - 1):
            raise ValueError(f"box index does not match frame {count - 1}")
        _box(box, base.shape[1], base.shape[0])
        _frame(source, "source")
        if source.shape != base.shape:
            raise ValueError("source and base geometry must match exactly")
        _alpha(mask, base.shape[:2], "person mask")
        foreground, alpha = matte(head)
        merged = composite_head_frame(base, foreground, alpha, box)
        yield restore_background_frame(merged, source, mask, dilate_px=dilate_px, blur_sigma=blur_sigma)
    if count == 0:
        raise ValueError("frame count is zero")
