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


# ---------------------------------------------------------------- stage 2: own voice in, translated voice out

MIC_DEVICE_ENV = "BOSSMAN_LIVE_INTERP_OUTPUT_DEVICE"   # the virtual microphone the call app listens to
DEFAULT_OUTPUT = "CABLE Input"                         # VB-Audio Virtual Cable (free); Zoom/Telegram pick "CABLE Output" as mic


def output_device_name() -> str:
    import os
    return os.environ.get(MIC_DEVICE_ENV, DEFAULT_OUTPUT).strip() or DEFAULT_OUTPUT


def speak_status() -> dict:
    base = capture_status()
    if base["status"] != "READY":
        return base
    try:  # pragma: no cover - needs Windows audio
        import pyaudiowpatch as pa
        audio = pa.PyAudio()
        try:
            want = output_device_name().lower()
            names = [audio.get_device_info_by_index(i)["name"] for i in range(audio.get_device_count())]
        finally:
            audio.terminate()
        if not any(want in n.lower() for n in names):
            return {"status": "BLOCKED", "reason": f"virtual microphone '{output_device_name()}' not found: install VB-Cable "
                                                   f"or set {MIC_DEVICE_ENV}"}
        return {"status": "READY", "output_device": output_device_name()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": type(exc).__name__}


def mic_chunks(stopped: Callable[[], bool]) -> Iterator[bytes]:  # pragma: no cover - needs Windows audio
    import pyaudiowpatch as pa
    audio = pa.PyAudio()
    try:
        dev = audio.get_default_input_device_info()
        channels = max(1, min(2, int(dev["maxInputChannels"]) or 1))
        rate = int(dev["defaultSampleRate"])
        frames = rate * CHUNK_MS // 1000
        stream = audio.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=frames)
        carry: list = []
        try:
            while not stopped():
                yield downmix_resample(stream.read(frames, exception_on_overflow=False), channels, rate, carry)
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        audio.terminate()


def play_to_device(pcm: bytes, rate: int, stopped: Callable[[], bool]) -> str:  # pragma: no cover - needs Windows audio
    """Mono PCM16 -> the virtual microphone. Returns the device name used. Stops mid-phrase on STOP."""
    import pyaudiowpatch as pa
    audio = pa.PyAudio()
    try:
        want = output_device_name().lower()
        dev = next(audio.get_device_info_by_index(i) for i in range(audio.get_device_count())
                   if want in audio.get_device_info_by_index(i)["name"].lower()
                   and audio.get_device_info_by_index(i)["maxOutputChannels"] > 0)
        stream = audio.open(format=pa.paInt16, channels=1, rate=rate, output=True, output_device_index=dev["index"])
        try:
            step = rate // 5 * 2
            for i in range(0, len(pcm), step):
                if stopped():
                    break
                stream.write(pcm[i:i + step])
        finally:
            stream.stop_stream()
            stream.close()
        return dev["name"]
    finally:
        audio.terminate()
