"""Playout queue: paced frames, generation-tagged flush, marker callbacks, late chunks rejected."""
from __future__ import annotations

import asyncio
import time

from bcc.telegram_calls.audio.playout import Playout

RATE = 16000
FRAME = RATE * 20 // 1000 * 2


class Sink:
    def __init__(self):
        self.frames, self.cleared, self.times = [], 0, []

    async def send(self, pcm):
        self.frames.append(pcm)
        self.times.append(time.monotonic())

    async def clear(self):
        self.cleared += 1


def mk(pace=0.0, **kw):
    s = Sink()
    return Playout(s.send, s.clear, sample_rate=RATE, pace=pace, **kw), s


def pcm(frames, value=1):
    return (value.to_bytes(2, "little", signed=True)) * (FRAME // 2) * frames


async def test_frames_are_exact_and_drain_is_reported():
    p, s = mk()
    p.start()
    gen = p.generation
    assert p.enqueue(pcm(3) + b"\x01\x00" * 10, gen)
    p.end(gen)
    assert await p.wait_drained(gen, 1.0)
    assert [len(f) for f in s.frames] == [FRAME] * 4       # tail padded to a full frame
    await p.close()


async def test_real_time_pacing_is_20ms_per_frame():
    p, s = mk(pace=1.0)
    p.start()
    gen = p.generation
    p.enqueue(pcm(10), gen)
    p.end(gen)
    await p.wait_drained(gen, 2.0)
    gaps = [b - a for a, b in zip(s.times, s.times[1:])]
    assert 0.015 < sum(gaps) / len(gaps) < 0.030 and len(s.frames) == 10
    await p.close()


async def test_flush_drops_queued_audio_and_rejects_late_chunks():
    p, s = mk(pace=1.0)
    p.start()
    gen = p.generation
    p.enqueue(pcm(100), gen)                               # 2 s of audio
    await asyncio.sleep(0.09)                              # ~4 frames on the wire
    sent = len(s.frames)
    new_gen = await p.flush("barge_in")
    assert new_gen == gen + 1 and s.cleared == 1
    assert p.enqueue(pcm(5), gen) is False                 # late chunk from the cancelled synthesis
    await asyncio.sleep(0.15)
    assert len(s.frames) == sent, "nothing from the old generation may be sent after flush"
    assert p.enqueue(pcm(2), new_gen) and await p.wait_drained(new_gen, 0.5) is False   # not ended yet
    p.end(new_gen)
    assert await p.wait_drained(new_gen, 1.0)
    await p.close()


async def test_first_frame_and_marker_callbacks():
    p, s = mk()
    seen = {"first": [], "markers": [], "sent": 0}
    p.on_first_frame = lambda g, t: seen["first"].append(g)
    p.on_marker_started = lambda g, m: seen["markers"].append(m)
    p.on_frame_sent = lambda f, t: seen.__setitem__("sent", seen["sent"] + 1)
    p.start()
    gen = p.generation
    p.enqueue(pcm(2), gen, marker=0)
    p.enqueue(pcm(2), gen, marker=1)
    p.end(gen)
    await p.wait_drained(gen, 1.0)
    assert seen["first"] == [gen] and seen["markers"] == [0, 1] and seen["sent"] == 4
    await p.close()


async def test_transport_failure_does_not_wedge_the_queue():
    calls = {"n": 0}

    async def send(_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    async def clear():
        pass
    p = Playout(send, clear, sample_rate=RATE, pace=0.0)
    p.start()
    gen = p.generation
    p.enqueue(pcm(3), gen)
    p.end(gen)
    assert await p.wait_drained(gen, 1.0) and calls["n"] == 3
    await p.close()
