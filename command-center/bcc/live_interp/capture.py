"""System-audio capture on Windows (WASAPI loopback) -> PCM16 mono 16 kHz chunks.

Cicada reads macOS system audio; on the owner's Windows PC the same thing is WASAPI loopback of the default
output device (whatever Zoom/Telegram/Meet/YouTube plays). Optional dependency `PyAudioWPatch`
(extra: bossman-command-center[live-interp], i.e. PyAudioWPatch). Missing -> a precise BLOCKED status, never a crash. Nothing is written to disk.
"""
from __future__ import annotations

import sys
from array import array
from typing import Callable, Iterator

from .segmenter import RATE

CHUNK_MS = 200


def capture_status() -> dict:
    if sys.platform != "win32":
        return {"status": "BLOCKED", "reason": "WASAPI loopback is Windows-only; on this host feed PCM via the API"}
    try:
        import pyaudiowpatch  # noqa: F401
    except ImportError:
        return {"status": "BLOCKED", "reason": "pip install bossman-command-center[live-interp] (PyAudioWPatch: system-audio capture is not installed)"}
    return {"status": "READY", "backend": "wasapi-loopback"}


def downmix_resample(raw: bytes, channels: int, rate: int, carry: list) -> bytes:
    """Interleaved PCM16 at `rate` with `channels` -> mono PCM16 16 kHz (linear resampling; `carry` keeps the phase)."""
    src = array("h")
    src.frombytes(raw[: len(raw) - len(raw) % (2 * channels)])
    mono = [sum(src[i:i + channels]) // channels for i in range(0, len(src), channels)]
    if rate == RATE:
        return array("h", mono).tobytes()
    step = rate / RATE
    pos = carry[0] if carry else 0.0
    out = array("h")
    while pos < len(mono) - 1:
        i = int(pos)
        frac = pos - i
        out.append(int(mono[i] * (1 - frac) + mono[i + 1] * frac))
        pos += step
    carry[:] = [pos - len(mono) + 1 if mono else 0.0]
    return out.tobytes()


def loopback_chunks(stopped: Callable[[], bool]) -> Iterator[bytes]:  # pragma: no cover - needs Windows audio
    import pyaudiowpatch as pa
    audio = pa.PyAudio()
    try:
        wasapi = audio.get_host_api_info_by_type(pa.paWASAPI)
        speakers = audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not speakers.get("isLoopbackDevice"):
            for dev in audio.get_loopback_device_info_generator():
                if speakers["name"] in dev["name"]:
                    speakers = dev
                    break
        channels = int(speakers["maxInputChannels"]) or 2
        rate = int(speakers["defaultSampleRate"])
        frames = rate * CHUNK_MS // 1000
        stream = audio.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=speakers["index"], frames_per_buffer=frames)
        carry: list = []
        try:
            while not stopped():
                yield downmix_resample(stream.read(frames, exception_on_overflow=False), channels, rate, carry)
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        audio.terminate()
