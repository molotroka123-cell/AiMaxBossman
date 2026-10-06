"""Evidence frames are de-identified: everything except the recognised fields and buttons (+ a margin) is blurred, so seat names,
chat and window chrome do not leave the machine in evidence files."""
from __future__ import annotations

import cv2
import numpy as np


def anonymise(bgr: np.ndarray, state, margin: int = 6) -> np.ndarray:
    keep = np.zeros(bgr.shape[:2], np.uint8)
    for b in (state.quality or {}).get("boxes", []):
        cv2.rectangle(keep, (max(int(b["x"]) - margin, 0), max(int(b["y"]) - margin, 0)), (int(b["x"] + b["w"]) + margin, int(b["y"] + b["h"]) + margin), 255, -1)
    blurred = cv2.GaussianBlur(cv2.resize(bgr, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA), (0, 0), 3)
    blurred = cv2.resize(blurred, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
    return np.where(keep[:, :, None] > 0, bgr, blurred)
