"""Locators answer one question: WHERE is the element named ``label``? They never choose the label or the amount.

VisionLocator uses the adapter's own detected button boxes (re-detected on every fresh frame, never cached coordinates).
ModelLocator wraps an external UI-grounding model and accepts its answer ONLY if it agrees with what vision itself sees:
a model can refine a box, it cannot redirect a click to another element, another label or outside the window."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .geometry import Rect


@dataclass(frozen=True)
class Target:
    label: str
    box: Rect
    confidence: float
    source: str
    amount: float | None = None


@dataclass(frozen=True)
class Located:
    target: Target | None
    reason: str = ""


def _vision_buttons(state) -> list[dict]:
    return list((state.quality or {}).get("buttons") or [])


class VisionLocator:
    name = "vision"

    def locate(self, frame, state, label: str) -> Located:
        hits = [b for b in _vision_buttons(state) if b["label"] == label]
        if not hits:
            return Located(None, f"no {label!r} button detected on the fresh frame")
        if len(hits) > 1:
            return Located(None, f"{len(hits)} {label!r} buttons detected: ambiguous")
        b = hits[0]
        return Located(Target(label, Rect(b["x"], b["y"], b["w"], b["h"]), float(b.get("conf", 0.0)), self.name, b.get("amount")))


class GroundingClient(Protocol):
    def propose(self, bgr, instruction: str) -> dict | None: ...      # {"label": str, "box": [x, y, w, h], "confidence": float}


class ModelLocator:
    """A UI-grounding model proposes a box for the instruction 'click <label>'. The proposal is used only when
    (1) it names the SAME label, (2) its box is inside the frame, (3) it overlaps the vision-detected button of that label (IoU >= min_iou).
    Where vision detects nothing there is nothing to cross-check against, so the model is refused (acting needs verified perception)."""
    def __init__(self, client: GroundingClient, name: str = "model", min_iou: float = 0.30):
        self.client, self.name, self.min_iou = client, name, min_iou
        self._vision = VisionLocator()

    def locate(self, frame, state, label: str) -> Located:
        ref = self._vision.locate(frame, state, label)
        if ref.target is None:
            return Located(None, f"model refused: vision cannot confirm {label!r} ({ref.reason})")
        try:
            p = self.client.propose(frame.bgr, f"click the {label} button")
        except Exception as exc:  # noqa: BLE001
            return Located(None, f"model error: {type(exc).__name__}")
        if not p or p.get("label") != label:
            return Located(None, f"model named a different element ({(p or {}).get('label')!r}) than the decided {label!r}")
        x, y, w, h = p["box"]
        box = Rect(float(x), float(y), float(w), float(h))
        H, W = frame.bgr.shape[:2]
        if w <= 0 or h <= 0 or box.x < 0 or box.y < 0 or box.r > W or box.b > H:
            return Located(None, "model box outside the frame")
        if box.iou(ref.target.box) < self.min_iou:
            return Located(None, "model box does not overlap the button vision sees (possible wrong element)")
        return Located(Target(label, box, float(p.get("confidence", 0.0)), self.name, ref.target.amount))
