"""PCM helpers: int16 <-> float, streaming resampling, exact-size framing, levels.

Everything is PCM16 little-endian mono. ``StreamResampler`` prefers ``soxr``
(LGPL, Windows wheels, high quality, streaming) and falls back to a small numpy
windowed-sinc/linear implementation so the audio path and its tests run wherever
numpy does. Both keep state across calls, so chunk boundaries do not click.
"""
from __future__ import annotations

import math

import numpy as np

try:  # optional, better quality and speed
    import soxr as _soxr
except Exception:  # noqa: BLE001 - any import problem means "use the fallback"
    _soxr = None

INT16_MAX = 32767.0


def to_float(pcm: bytes | bytearray | memoryview) -> np.ndarray:
    """PCM16 bytes -> float32 in [-1, 1]. Odd trailing byte is ignored."""
    n = len(pcm) // 2
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(bytes(pcm[: n * 2]), dtype="<i2").astype(np.float32) / 32768.0


def to_pcm(samples: np.ndarray) -> bytes:
    """float -> PCM16 bytes with clipping (never wraps)."""
    if samples.size == 0:
        return b""
    return (np.clip(samples, -1.0, 1.0) * INT16_MAX).astype("<i2").tobytes()


def rms(pcm: bytes | np.ndarray) -> float:
    """RMS level in [0, 1] of PCM16 bytes or a float array."""
    x = to_float(pcm) if isinstance(pcm, (bytes, bytearray, memoryview)) else pcm
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def dbfs(level: float) -> float:
    return -120.0 if level <= 1e-6 else max(-120.0, 20.0 * math.log10(level))


def silence(ms: int, rate: int) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)


class StreamResampler:
    """Stateful mono resampler ``in_rate`` -> ``out_rate`` (PCM16 in, PCM16 out)."""

    def __init__(self, in_rate: int, out_rate: int, *, force_fallback: bool = False, quality: str = "HQ"):
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.in_rate, self.out_rate = in_rate, out_rate
        self.backend = "passthrough"
        self._stream = None
        if in_rate == out_rate:
            return
        if _soxr is not None and not force_fallback:
            self._stream = _soxr.ResampleStream(in_rate, out_rate, 1, dtype="float32", quality=quality)
            self.backend = "soxr"
            return
        self.backend = "numpy"
        self._step = in_rate / out_rate                    # input samples per output sample
        self._fir = None
        self._tail = np.zeros(0, dtype=np.float64)
        if out_rate < in_rate:                             # anti-alias before decimating
            taps = 63
            n = np.arange(taps) - (taps - 1) / 2
            cutoff = 0.46 * out_rate / in_rate             # cycles/sample, a bit under the output Nyquist
            h = np.sinc(2 * cutoff * n) * np.hamming(taps)
            self._fir = h / h.sum()
            self._tail = np.zeros(taps - 1, dtype=np.float64)
        self._carry = np.zeros(0, dtype=np.float64)        # unconsumed (filtered) input samples
        self._pos = 0.0                                    # read position of the next output, relative to _carry[0]

    def process(self, pcm: bytes, *, last: bool = False) -> bytes:
        if self.backend == "passthrough":
            return bytes(pcm)
        x = to_float(pcm)
        if self.backend == "soxr":
            return to_pcm(self._stream.resample_chunk(x, last=last))
        return to_pcm(self._numpy_process(x.astype(np.float64)))

    def _numpy_process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self._fir is not None:
            buf = np.concatenate([self._tail, x])
            y = np.convolve(buf, self._fir, mode="valid")
            self._tail = buf[-(len(self._fir) - 1):]
        else:
            y = x
        buf = np.concatenate([self._carry, y])
        last_index = len(buf) - 1
        if self._pos > last_index:                          # still skipping samples (large decimation step)
            self._carry, self._pos = np.zeros(0, dtype=np.float64), self._pos - len(buf)
            return np.zeros(0, dtype=np.float32)
        count = int((last_index - self._pos) // self._step) + 1
        idx = self._pos + self._step * np.arange(count)
        i0 = np.floor(idx).astype(np.int64)
        frac = idx - i0
        i1 = np.minimum(i0 + 1, last_index)
        out = buf[i0] * (1.0 - frac) + buf[i1] * frac
        new_pos = self._pos + self._step * count
        k = min(int(new_pos), len(buf))
        self._carry, self._pos = buf[k:], new_pos - k
        return out.astype(np.float32)


class FrameSlicer:
    """Accumulate bytes, hand out exact ``frame_bytes`` frames."""

    def __init__(self, frame_bytes: int):
        if frame_bytes <= 0 or frame_bytes % 2:
            raise ValueError("frame size must be a positive even number of bytes")
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        out = []
        n = self.frame_bytes
        while len(self._buf) >= n:
            out.append(bytes(self._buf[:n]))
            del self._buf[:n]
        return out

    def flush(self, *, pad: bool = False) -> bytes:
        rest = bytes(self._buf)
        self._buf.clear()
        if pad and rest:
            rest += b"\x00" * (self.frame_bytes - len(rest))
        return rest

    @property
    def pending(self) -> int:
        return len(self._buf)
