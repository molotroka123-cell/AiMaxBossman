"""PCM helpers: resampling keeps the tone and the length, framing is exact, clipping never wraps."""
from __future__ import annotations

import numpy as np
import pytest

from bcc.telegram_calls.audio.pcm import FrameSlicer, StreamResampler, dbfs, rms, silence, to_float, to_pcm


def tone(freq, rate, seconds, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return to_pcm(amp * np.sin(2 * np.pi * freq * t))


def peak_hz(pcm, rate):
    x = to_float(pcm)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[spec.argmax()])


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("rates", [(48000, 16000), (16000, 48000), (22050, 48000), (24000, 48000)])
def test_resampler_preserves_tone_and_duration_across_odd_chunks(rates, fallback):
    a, b = rates
    r = StreamResampler(a, b, force_fallback=fallback)
    src = tone(1000, a, 1.0)
    chunks = [src[i:i + 1234] for i in range(0, len(src), 1234)]
    out = b"".join(r.process(c, last=(i == len(chunks) - 1)) for i, c in enumerate(chunks))
    assert abs(peak_hz(out, b) - 1000) < 15
    assert abs(len(out) // 2 - b) < 0.03 * b            # duration within 3 %
    assert 0.30 < rms(out) < 0.40                        # amplitude kept (0.5 sine = 0.354 rms)


def test_resampler_passthrough_when_rates_equal():
    r = StreamResampler(16000, 16000)
    assert r.backend == "passthrough" and r.process(b"\x01\x00\x02\x00") == b"\x01\x00\x02\x00"


def test_resampler_rejects_bad_rates():
    with pytest.raises(ValueError):
        StreamResampler(0, 16000)


def test_frame_slicer_yields_exact_frames_and_keeps_remainder():
    s = FrameSlicer(640)
    frames = s.push(b"\x01\x00" * 500) + s.push(b"\x02\x00" * 500)
    assert [len(f) for f in frames] == [640, 640, 640] and s.pending == 2000 - 1920 == 80
    assert len(s.flush(pad=True)) == 640 and s.pending == 0


def test_frame_slicer_rejects_odd_size():
    with pytest.raises(ValueError):
        FrameSlicer(3)


def test_to_pcm_clips_instead_of_wrapping():
    out = to_float(to_pcm(np.array([2.0, -2.0, 0.5], dtype=np.float32)))
    assert out[0] > 0.99 and out[1] < -0.99 and abs(out[2] - 0.5) < 1e-3


def test_levels_and_silence():
    assert rms(silence(100, 16000)) == 0.0 and dbfs(0.0) == -120.0
    assert dbfs(rms(tone(440, 16000, 0.5, 0.5))) == pytest.approx(-9.0, abs=0.3)
    assert to_float(b"\x01").size == 0                   # odd trailing byte ignored
