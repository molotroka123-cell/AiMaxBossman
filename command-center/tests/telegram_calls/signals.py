"""Deterministic synthetic audio for tests (no models, no network)."""
from __future__ import annotations

import numpy as np

from bcc.telegram_calls.audio.pcm import to_pcm


def burst(ms: int, rate: int = 16000, *, amp: float = 0.3, f0: float = 140.0, seed: int = 1, syllable_hz: float = 4.0) -> bytes:
    """Speech-like burst: harmonic stack with a syllable-rate amplitude envelope."""
    rng = np.random.default_rng(seed)
    n = rate * ms // 1000
    t = np.arange(n) / rate
    sig = sum(np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28)) / k for k in range(1, 12))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * syllable_hz * t) ** 2
    x = sig * env
    x = x / max(1e-9, np.abs(x).max()) * amp
    return to_pcm(x)


def quiet(ms: int, rate: int = 16000, *, amp: float = 0.002, seed: int = 2) -> bytes:
    rng = np.random.default_rng(seed)
    return to_pcm(rng.normal(0, amp, rate * ms // 1000))
