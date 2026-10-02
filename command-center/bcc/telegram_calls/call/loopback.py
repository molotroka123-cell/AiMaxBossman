"""Loopback transport: a call engine that is NOT Telegram.

Used for (a) unit tests, (b) the offline self-test of the whole audio path
(``bossman call selftest`` / the dashboard button), with a synthetic interlocutor driven from Python.
Every record produced with it carries ``transport="loopback"`` and the UI labels it
«ТЕСТ БЕЗ TELEGRAM»: it proves plumbing and latency of OUR path, never a real Telegram call.

Failure injection (``dial_error``, ``peer_hangup``, ``drop_media``) covers busy / declined / no answer /
lost connection without any external call.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable

import numpy as np

from ..audio.pcm import StreamResampler, to_float, to_pcm
from ..types import AudioFormat, CallError, PeerRef, TransportEvent, TransportEventKind


class LoopbackTransport:
    name = "loopback"

    def __init__(self, *, sample_rate: int = 48000, rx_sample_rate: int = 16000, frame_ms: int = 10,
                 ring_s: float = 0.0, dial_error: CallError | None = None,
                 echo_delay_ms: int = 0, echo_gain: float = 0.0, echo_noise: float = 0.0,
                 clock: Callable[[], float] = time.monotonic):
        if sample_rate % 100:
            raise ValueError("the call engine needs a sample rate that is a multiple of 100 Hz")
        self.audio_format = AudioFormat(sample_rate=sample_rate)
        self.rx_sample_rate, self.frame_ms = rx_sample_rate, frame_ms
        self._echo_rs = StreamResampler(sample_rate, rx_sample_rate) if sample_rate != rx_sample_rate else None
        self.ring_s, self.dial_error = ring_s, dial_error
        self.echo_delay_ms, self.echo_gain, self.echo_noise = echo_delay_ms, echo_gain, echo_noise
        self._t0 = 0.0
        self._clock = clock
        self._audio_cb: Callable[[bytes], None] | None = None
        self._event_cb: Callable[[TransportEvent], None] | None = None
        self.sent: list[tuple[float, bytes]] = []       # (monotonic time, PCM) actually handed to the "line"
        self.dial_calls = 0
        self.hangup_calls = 0
        self.clear_calls = 0
        self.started = False
        self.closed = False
        self.ended = False
        self.media_up = False
        self._rng = np.random.default_rng(7)
        self._rx_frames: deque[bytes] = deque()          # driver audio waiting for its 20 ms slot
        self._echo_ticks: dict[int, np.ndarray] = {}     # tick index -> echo samples to mix in
        self._line_task: asyncio.Task | None = None
        self._tick_ms = frame_ms

    # ------------------------------------------------------------ CallTransport
    async def start(self) -> None:
        self.started = True

    async def dial(self, peer: PeerRef, *, ring_timeout: float) -> None:
        self.dial_calls += 1
        self._emit(TransportEventKind.RINGING)
        if self.ring_s:
            await asyncio.sleep(min(self.ring_s, ring_timeout))
        if self.dial_error is not None:
            raise self.dial_error
        if self.ring_s > ring_timeout:
            raise CallError("CALL_NO_ANSWER")
        self.media_up = True
        self._t0 = self._clock()
        self._line_task = asyncio.get_running_loop().create_task(self._line(), name="loopback-line")
        self._emit(TransportEventKind.CONNECTED)

    async def _line(self) -> None:
        """The far end's microphone line: one 20 ms frame per tick = driver audio (or silence) + delayed echo."""
        n = self.rx_sample_rate * self._tick_ms // 1000
        tick = 0
        while not self.ended:
            frame = np.zeros(n, dtype=np.float32)
            if self._rx_frames:
                chunk = to_float(self._rx_frames.popleft())
                frame[: len(chunk)] = chunk[:n]
            echo = self._echo_ticks.pop(tick, None)
            if echo is not None:
                frame[: len(echo)] += echo[:n]
            if self._audio_cb is not None and self.media_up:
                self._audio_cb(to_pcm(frame))
            tick += 1
            wait = self._t0 + tick * self._tick_ms / 1000.0 - self._clock()
            await asyncio.sleep(max(wait, 0.0) if wait > 0 else 0)

    def set_audio_callback(self, cb: Callable[[bytes], None]) -> None:
        self._audio_cb = cb

    def set_event_callback(self, cb: Callable[[TransportEvent], None]) -> None:
        self._event_cb = cb

    async def send_audio(self, pcm: bytes) -> None:
        if self.ended or not self.media_up:
            return
        if len(pcm) != self.audio_format.frame_bytes(self.frame_ms):
            raise ValueError("send_audio must receive exactly one frame (the real engine over-reads short data)")
        now = self._clock()
        self.sent.append((now, pcm))
        if self.echo_gain > 0 and self.echo_delay_ms > 0:
            x = to_float(self._echo_rs.process(pcm) if self._echo_rs is not None else pcm) * self.echo_gain
            if self.echo_noise:
                x = x + self._rng.normal(0, self.echo_noise, x.shape).astype(np.float32)
            k = int(round(((now - self._t0) * 1000.0 + self.echo_delay_ms) / self._tick_ms))
            prev = self._echo_ticks.get(k)
            self._echo_ticks[k] = x if prev is None else prev[: len(x)] + x[: len(prev)]

    async def clear_outgoing(self) -> None:
        self.clear_calls += 1

    async def hangup(self, reason: str = "local") -> None:
        self.hangup_calls += 1
        if not self.ended:
            self.ended = True
            self.media_up = False
            self._emit(TransportEventKind.ENDED, "local_hangup")

    async def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------ driver side (the synthetic interlocutor)
    def inject(self, pcm: bytes) -> None:
        """Audio from the far end's microphone; delivered on the line clock, mixed with any echo."""
        step = self.rx_sample_rate * self._tick_ms // 1000 * 2
        for i in range(0, len(pcm), step):
            self._rx_frames.append(pcm[i:i + step])

    async def feed_realtime(self, pcm: bytes, rate: int, *, pace: float = 1.0) -> None:
        """Play ``pcm`` (PCM16 mono at ``rate``) into the call at real-time speed; returns when it has been delivered."""
        if rate != self.rx_sample_rate:
            pcm = StreamResampler(rate, self.rx_sample_rate).process(pcm, last=True)
        self.inject(pcm)
        while self._rx_frames and not self.ended:
            await asyncio.sleep(0.005)

    def peer_hangup(self) -> None:
        if not self.ended:
            self.ended = True
            self.media_up = False
            self._emit(TransportEventKind.ENDED, "peer_hangup")

    def drop_media(self) -> None:
        self.media_up = False
        self._emit(TransportEventKind.DISCONNECTED)

    def restore_media(self) -> None:
        if not self.ended:
            self.media_up = True
            self._emit(TransportEventKind.RECONNECTED)

    def sent_ms(self) -> float:
        return sum(len(p) for _, p in self.sent) / 2 / self.audio_format.sample_rate * 1000.0

    def _emit(self, kind: TransportEventKind, reason: str = "") -> None:
        if self._event_cb is not None:
            self._event_cb(TransportEvent(kind, reason, self._clock()))
