"""Low-resolution object-detection geometry, with no model or capture side effects.

This is deliberately only the safe, deterministic half of an optional object
detector.  It knows how to map a detector box from a small letterboxed image
back to the source image, but it neither captures high-resolution camera
frames nor loads a model.  Those operations require separate privacy, licence
and hardware acceptance.

Keeping this module free of ``opencv``, ``torch`` and ``ultralytics`` makes it
safe to import in the normal 160x90 privacy-preserving pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class ImageSize:
    """Pixel dimensions with explicit validation."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("image width and height must both be >= 1")


@dataclass(frozen=True)
class BoundingBox:
    """A half-open pixel box in ``x1, y1, x2, y2`` form."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if not all(isfinite(value) for value in values):
            raise ValueError("bounding-box coordinates must be finite")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bounding box must satisfy x1 < x2 and y1 < y2")


@dataclass(frozen=True)
class Detection:
    """Model-neutral object-detection result.

    ``box`` is always expressed in the detector's inference image.  The
    caller maps it through :class:`LetterboxTransform` before returning an
    event to any API or consumer.
    """

    label: str
    confidence: float
    box: BoundingBox

    def __post_init__(self) -> None:
        if not self.label or len(self.label) > 80:
            raise ValueError("detection label must be 1..80 characters")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("detection confidence must be within 0..1")


@dataclass(frozen=True)
class LetterboxTransform:
    """Lossless bookkeeping for aspect-ratio-preserving detector input.

    A detector sees an ``inference`` canvas.  The source image is scaled to
    fit it and surrounded by symmetric padding when their aspect ratios differ.
    Correctly removing that padding is what prevents boxes drifting when they
    are drawn on a high-resolution display frame.
    """

    source: ImageSize
    inference: ImageSize
    scale_x: float
    scale_y: float
    scaled: ImageSize
    pad_left: float
    pad_top: float

    @classmethod
    def fit(cls, source: ImageSize, inference: ImageSize) -> "LetterboxTransform":
        scale = min(inference.width / source.width, inference.height / source.height)
        scaled = ImageSize(
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        # The detector sees integer pixels.  Rounding a resized image can make
        # its effective x/y scale differ by a fraction of a pixel, so retain
        # both instead of pretending one ideal floating-point scale exists.
        return cls(
            source=source,
            inference=inference,
            scale_x=scaled.width / source.width,
            scale_y=scaled.height / source.height,
            scaled=scaled,
            pad_left=(inference.width - scaled.width) / 2.0,
            pad_top=(inference.height - scaled.height) / 2.0,
        )

    def source_to_inference(self, box: BoundingBox) -> BoundingBox:
        return BoundingBox(
            x1=box.x1 * self.scale_x + self.pad_left,
            y1=box.y1 * self.scale_y + self.pad_top,
            x2=box.x2 * self.scale_x + self.pad_left,
            y2=box.y2 * self.scale_y + self.pad_top,
        )

    def inference_to_source(self, box: BoundingBox) -> BoundingBox | None:
        """Map and clip a detector box back to the source image.

        ``None`` means the box only covered letterbox padding, so it must not
        become a false object event.
        """
        x1 = (box.x1 - self.pad_left) / self.scale_x
        y1 = (box.y1 - self.pad_top) / self.scale_y
        x2 = (box.x2 - self.pad_left) / self.scale_x
        y2 = (box.y2 - self.pad_top) / self.scale_y
        x1, x2 = max(0.0, x1), min(float(self.source.width), x2)
        y1, y2 = max(0.0, y1), min(float(self.source.height), y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

    def map_detection_to_source(self, detection: Detection) -> Detection | None:
        mapped = self.inference_to_source(detection.box)
        if mapped is None:
            return None
        return Detection(label=detection.label, confidence=detection.confidence, box=mapped)


def should_sample_for_detection(*, sequence: int, enabled: bool, every_n_frames: int) -> bool:
    """Cost gate for an optional detector; sequence numbering begins at one."""
    if every_n_frames < 1:
        raise ValueError("every_n_frames must be >= 1")
    if sequence < 1:
        raise ValueError("sequence must be >= 1")
    return enabled and (sequence - 1) % every_n_frames == 0
