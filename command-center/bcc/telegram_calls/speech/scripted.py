"""Scripted engines for the OFFLINE self-test only (no models needed). They are NOT a fallback for a real call:
``factory.build_engines`` refuses them unless the worker runs in offline-test mode. Records that used them say so.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, AsyncIterator

import numpy as np

from ..audio.pcm import StreamResampler, to_pcm
from ..types import CallSummary, CancelToken, STTResult, Turn


class ScriptedSTT:
    """Returns the phrases the synthetic caller announces (``expect``), in order: proves plumbing, not recognition."""
    name, model = "scripted-stt", "offline-selftest"

    def __init__(self):
        self.queue: list[str] = []

    def expect(self, text: str) -> None:
        self.queue.append(text)

    def new_stream(self, *, language: str = "ru"):
        return _ScriptedStream(self)

    def status(self) -> dict[str, Any]:
        return {"ok": True, "scripted": True}


class _ScriptedStream:
    def __init__(self, engine: ScriptedSTT):
        self.engine, self.fed = engine, 0

    def feed(self, pcm: bytes) -> None:
        self.fed += len(pcm)

    async def partial(self) -> str:
        return ""

    async def finalize(self) -> STTResult:
        text = self.engine.queue.pop(0) if self.engine.queue else ""
        return STTResult(text=text, duration_s=self.fed / 32000.0)

    def cancel(self) -> None:
        return None


class ToneTTS:
    """Speech-length audio (harmonic tone with a syllable envelope), delivered faster than real time like a real engine."""
    name, voice, sample_rate = "tone-tts", "offline-selftest", 22050

    def __init__(self, ms_per_char: int = 55, chunk_ms: int = 100, amp: float = 0.25):
        self.ms_per_char, self.chunk_ms, self.amp = ms_per_char, chunk_ms, amp

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[bytes]:
        n = self.sample_rate * max(200, len(text) * self.ms_per_char) // 1000
        t = np.arange(n) / self.sample_rate
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t) ** 2
        x = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 10)) * env
        pcm = to_pcm((x / np.abs(x).max() * self.amp).astype(np.float32))
        step = self.sample_rate * self.chunk_ms // 1000 * 2
        for i in range(0, len(pcm), step):
            if cancel.cancelled:
                return
            yield pcm[i:i + step]
            await asyncio.sleep(0)

    def status(self) -> dict[str, Any]:
        return {"ok": True, "scripted": True}


class ScriptedBrain:
    route, model = "scripted", "offline-selftest"

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or [])
        self.history_seen: list[list[tuple[str, str, bool]]] = []

    async def reply(self, history: list[Turn], user_text: str, cancel: CancelToken):
        self.history_seen.append([(t.role, t.text, t.interrupted) for t in history])
        text = self.replies.pop(0) if self.replies else f"Понял: {user_text}."
        for i in range(0, len(text), 8):
            if cancel.cancelled:
                return
            yield text[i:i + 8]
            await asyncio.sleep(0)

    async def summarize(self, turns: list[Turn]) -> CallSummary:
        n = sum(1 for t in turns if t.role == "user")
        return CallSummary(text=f"Самотест без Telegram: {n} реплик собеседника.", agreed_tasks=[], generated_by="scripted")

    def status(self) -> dict[str, Any]:
        return {"ok": True, "scripted": True}


# ---------------------------------------------------------------- synthetic caller audio

def espeak_speech(text: str, *, rate_hz: int = 16000) -> bytes | None:
    """Real (robotic) Russian speech from a locally installed espeak-ng, or None when it is not installed."""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="calls-selftest-") as d:
        out = Path(d) / "s.wav"
        try:
            subprocess.run([exe, "-v", "ru", "-s", "150", "-w", str(out), text], check=True, timeout=20,  # noqa: S603
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with wave.open(str(out), "rb") as w:
                pcm, rate = w.readframes(w.getnframes()), w.getframerate()
        except (OSError, subprocess.SubprocessError, wave.Error):
            return None
    return StreamResampler(rate, rate_hz).process(pcm, last=True)


def tone_speech(ms: int, *, rate_hz: int = 16000, seed: int = 1, amp: float = 0.3) -> bytes:
    rng = np.random.default_rng(seed)
    n = rate_hz * ms // 1000
    t = np.arange(n) / rate_hz
    sig = sum(np.sin(2 * np.pi * 140 * k * t + rng.uniform(0, 6.28)) / k for k in range(1, 12))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t) ** 2
    x = sig * env
    return to_pcm((x / np.abs(x).max() * amp).astype(np.float32))
