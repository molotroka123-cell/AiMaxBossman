"""Voice activity detection on 16 kHz PCM16 mono, 512-sample (32 ms) windows.

* ``SileroVAD`` — Silero VAD v6 through ``pysilero-vad`` (MIT, ONNX/ggml model bundled in the
  wheel, no torch). The real detector.
* ``EnergyVAD`` — adaptive noise-floor energy detector. Honest fallback and test double; its
  ``degraded`` flag is surfaced in status so the owner sees that the real detector is missing.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import ANALYSIS_RATE, CallError
from .pcm import dbfs, rms

WINDOW_SAMPLES = 512
WINDOW_BYTES = WINDOW_SAMPLES * 2
WINDOW_MS = WINDOW_SAMPLES * 1000 // ANALYSIS_RATE      # 32


@runtime_checkable
class VAD(Protocol):
    name: str
    degraded: bool

    def prob(self, window: bytes) -> float: ...
    def reset(self) -> None: ...


class SileroVAD:
    name = "silero-vad-v6"
    degraded = False

    def __init__(self) -> None:
        try:
            from pysilero_vad import SileroVoiceActivityDetector
        except Exception as exc:  # noqa: BLE001
            raise CallError("VAD_UNAVAILABLE", detail=type(exc).__name__) from None
        self._vad = SileroVoiceActivityDetector()

    def prob(self, window: bytes) -> float:
        if len(window) != WINDOW_BYTES:
            raise ValueError("VAD window must be exactly 512 samples of PCM16")
        return float(self._vad.process_chunk(window))

    def reset(self) -> None:
        self._vad.reset()


class EnergyVAD:
    """Level above a slowly adapting noise floor. Crude on purpose; never the default when Silero works."""

    name = "energy-fallback"
    degraded = True

    def __init__(self, margin_db: float = 8.0, span_db: float = 12.0, rise_db_per_window: float = 0.05):
        self.margin_db, self.span_db, self.rise = margin_db, span_db, rise_db_per_window
        self.reset()

    def reset(self) -> None:
        self._floor = -60.0

    def prob(self, window: bytes) -> float:
        level = dbfs(rms(window))
        if level < self._floor:
            self._floor = 0.7 * self._floor + 0.3 * level
        else:
            self._floor += min(self.rise, level - self._floor)
        self._floor = max(self._floor, -80.0)
        return max(0.0, min(1.0, (level - self._floor - self.margin_db) / self.span_db + 0.5))


def make_vad(kind: str = "auto") -> VAD:
    """``silero`` | ``energy`` | ``auto`` (Silero, else the flagged energy fallback)."""
    if kind == "energy":
        return EnergyVAD()
    if kind == "silero":
        return SileroVAD()
    try:
        return SileroVAD()
    except CallError:
        return EnergyVAD()
