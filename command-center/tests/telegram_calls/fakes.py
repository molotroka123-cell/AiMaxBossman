"""Scripted engines for control-flow tests. They prove wiring, never model quality."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from bcc.telegram_calls.audio.pcm import to_pcm
from bcc.telegram_calls.types import CallError, CallSummary, CancelToken, STTResult, Turn


class ScriptedSTT:
    name, model = "scripted-stt", "fixture"

    def __init__(self, texts, *, decode_ms=0.0, fail=False):
        self.texts, self.decode_ms, self.fail = list(texts), decode_ms, fail
        self.streams: list["ScriptedSTTStream"] = []

    def new_stream(self, *, language="ru"):
        s = ScriptedSTTStream(self)
        self.streams.append(s)
        return s

    def status(self):
        return {"ok": True}


class ScriptedSTTStream:
    def __init__(self, engine):
        self.engine, self.fed, self.cancelled, self.finalized = engine, 0, False, False

    def feed(self, pcm):
        self.fed += len(pcm)

    async def partial(self):
        return ""

    async def finalize(self):
        if self.engine.fail:
            raise RuntimeError("stt boom")
        self.finalized = True
        if self.engine.decode_ms:
            await asyncio.sleep(self.engine.decode_ms / 1000)
        text = self.engine.texts.pop(0) if self.engine.texts else ""
        return STTResult(text=text, duration_s=self.fed / 32000, decode_ms=self.engine.decode_ms)

    def cancel(self):
        self.cancelled = True


class ToneTTS:
    """Speech-like audio whose length follows the text, yielded faster than real time like a real engine."""
    name, voice, sample_rate = "tone-tts", "fixture", 22050

    def __init__(self, ms_per_char=55, chunk_ms=100, amp=0.25, first_delay=0.0, fail=False):
        self.ms_per_char, self.chunk_ms, self.amp, self.first_delay, self.fail = ms_per_char, chunk_ms, amp, first_delay, fail
        self.calls: list[str] = []
        self.cancelled_calls = 0

    async def synthesize(self, text, cancel: CancelToken):
        self.calls.append(text)
        if self.fail:
            raise CallError("TTS_UNAVAILABLE")
        if self.first_delay:
            await asyncio.sleep(self.first_delay)
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
                self.cancelled_calls += 1
                return
            yield pcm[i:i + step]
            await asyncio.sleep(0)

    def status(self):
        return {"ok": True}


class ScriptedBrain:
    route, model = "fast", "fixture-llm"

    def __init__(self, replies, *, first_delay=0.0, token_delay=0.0, piece=6, fail_times=0):
        self.replies, self.first_delay, self.token_delay, self.piece, self.fail_times = list(replies), first_delay, token_delay, piece, fail_times
        self.calls: list[dict] = []
        self.summarize_calls = 0
        self.cancelled_replies = 0

    async def reply(self, history, user_text, cancel: CancelToken):
        self.calls.append({"history": [(t.role, t.text, t.interrupted) for t in history], "user": user_text, "at": time.monotonic()})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CallError("BRAIN_UNAVAILABLE")
        text = self.replies.pop(0) if self.replies else "Хорошо."
        if self.first_delay:
            await asyncio.sleep(self.first_delay)
        for i in range(0, len(text), self.piece):
            if cancel.cancelled:
                self.cancelled_replies += 1
                return
            yield text[i:i + self.piece]
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            else:
                await asyncio.sleep(0)

    async def summarize(self, turns):
        self.summarize_calls += 1
        return CallSummary(text=f"Итог: {len(turns)} реплик.", agreed_tasks=["Проверить отчёт"], generated_by="fixture-llm")

    def status(self):
        return {"ok": True}
