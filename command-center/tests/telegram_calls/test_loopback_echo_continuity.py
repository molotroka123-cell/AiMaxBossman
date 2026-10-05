"""The loopback echo is a continuous delayed copy of the played audio, whatever the scheduler jitter (py3.12 CI: echo self-test)."""
from __future__ import annotations

import asyncio

import numpy as np

from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.audio.pcm import to_pcm


def _frame(transport, value):
    n = transport.audio_format.sample_rate * transport.frame_ms // 1000
    return to_pcm(np.full(n, value, dtype=np.float32))


def _ticks_after(sent_at_offsets_ms):
    clock = {"t": 100.0}
    t = LoopbackTransport(sample_rate=16000, rx_sample_rate=16000, frame_ms=10, echo_delay_ms=250, echo_gain=0.5,
                          clock=lambda: clock["t"])
    t.media_up = True
    t._t0 = 100.0

    async def run():
        for i, off in enumerate(sent_at_offsets_ms):
            clock["t"] = 100.0 + off / 1000.0
            await t.send_audio(_frame(t, 0.1 + i * 0.01))
    asyncio.run(run())
    return sorted(t._echo_ticks)


def test_jittered_hand_over_times_still_give_consecutive_echo_ticks():
    # frames handed over with +-8 ms scheduler jitter: wall-clock placement would collide or leave gaps
    offsets = [0, 12, 19, 33, 38, 52, 61, 70, 83, 88]
    ticks = _ticks_after(offsets)
    assert len(ticks) == len(offsets), ticks                      # no two frames share a tick
    assert ticks == list(range(ticks[0], ticks[0] + len(offsets)))  # and no gaps


def test_a_real_pause_in_the_stream_re_anchors_the_echo_to_the_clock():
    ticks = _ticks_after([0, 10, 20, 30, 500, 510])               # 470 ms silence in the played audio
    assert ticks[:4] == list(range(ticks[0], ticks[0] + 4))
    assert ticks[4] - ticks[3] > 30 and ticks[5] == ticks[4] + 1  # the second burst lands where the clock says
