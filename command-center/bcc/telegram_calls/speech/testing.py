"""Deterministic scripted engines for the OFFLINE self-test (``bossman call selftest`` = «ТЕСТ БЕЗ TELEGRAM»).

They prove wiring and timing of the pipeline, never recognition / synthesis / model quality, and are never
returned by ``speech.factory.build_engines`` (real engines only, or a clear error).
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import numpy as np

from ..audio.pcm import to_pcm
from ..types import CallSummary, CancelToken, STTResult, Turn


class ScriptedSTT:
    """Returns the scripted texts one per utterance (empty when the script is exhausted)."""
    name, model = "scripted-stt", "selftest"

    def __init__(self, texts, *, decode_ms: float = 0.0):
        self.texts, self.decode_ms = list(texts), decode_ms

    def new_stream(self, *, language: str = "ru") -> "ScriptedSTTStream":
        return ScriptedSTTStream(self)

    def status(self) -> dict[str, Any]:
        return {"ok": True, "engine": self.name, "synthetic": True}


class ScriptedSTTStream:
    def __init__(self, engine: ScriptedSTT):
        self.engine, self._bytes, self._cancelled = engine, 0, False

    def feed(self, pcm16k: bytes) -> None:
        self._bytes += len(pcm16k)

    async def partial(self) -> str:
        return ""

    async def finalize(self) -> STTResult:
        if self.engine.decode_ms:
            await asyncio.sleep(self.engine.decode_ms / 1000)
        text = "" if self._cancelled or not self.engine.texts else self.engine.texts.pop(0)
        return STTResult(text=text, duration_s=self._bytes / 32000, decode_ms=self.engine.decode_ms)

    def cancel(self) -> None:
        self._cancelled = True


class ToneTTS:
    """Speech-like synthetic tone (voiced harmonics with a syllable envelope); length follows the text."""
    name, voice = "tone-tts", "selftest"

    def __init__(self, sample_rate: int = 22050, ms_per_char: int = 55, chunk_ms: int = 100, amp: float = 0.25):
        self.sample_rate, self.ms_per_char, self.chunk_ms, self.amp = sample_rate, ms_per_char, chunk_ms, amp

    def status(self) -> dict[str, Any]:
        return {"ok": True, "engine": self.name, "synthetic": True}

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[bytes]:
        total_ms = max(200, len(text) * self.ms_per_char)
        n = self.sample_rate * total_ms // 1000
        t = np.arange(n) / self.sample_rate
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t) ** 2
        x = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 10)) * env
        x = x / np.abs(x).max() * self.amp
        pcm = to_pcm(x)
        step = self.sample_rate * self.chunk_ms // 1000 * 2
        for i in range(0, len(pcm), step):
            if cancel.cancelled:
                return
            yield pcm[i:i + step]
            await asyncio.sleep(0)


class ScriptedBrain:
    route, model = "fast", "selftest"

    def __init__(self, replies, *, piece: int = 6):
        self.replies, self.piece = list(replies), piece

    async def reply(self, history: list[Turn], user_text: str, cancel: CancelToken) -> AsyncIterator[str]:
        text = self.replies.pop(0) if self.replies else "Понял вас."
        for i in range(0, len(text), self.piece):
            if cancel.cancelled:
                return
            yield text[i:i + self.piece]
            await asyncio.sleep(0)

    async def summarize(self, turns: list[Turn]) -> CallSummary:
        return CallSummary(text=f"Самотест: {len(turns)} реплик.", agreed_tasks=[], generated_by="selftest")

    def status(self) -> dict[str, Any]:
        return {"ok": True, "engine": "scripted-brain", "synthetic": True}
