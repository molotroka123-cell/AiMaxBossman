"""Endpointer state machine on scripted VAD probabilities (deterministic), plus the energy VAD on real signals."""
from __future__ import annotations

import pytest

from bcc.telegram_calls.audio.endpointer import Endpointer, EndpointConfig
from bcc.telegram_calls.audio.vad import EnergyVAD, WINDOW_BYTES, WINDOW_MS, make_vad

from .signals import burst, quiet


class ScriptedVAD:
    name, degraded = "scripted", False

    def __init__(self, probs):
        self.probs, self.i = list(probs), 0

    def prob(self, window):
        p = self.probs[self.i] if self.i < len(self.probs) else 0.0
        self.i += 1
        return p

    def reset(self):
        pass


def run(probs, cfg=None):
    ep = Endpointer(ScriptedVAD(probs), cfg or EndpointConfig())
    events = []
    for _ in probs:
        events += ep.push(b"\x10\x00" * (WINDOW_BYTES // 2))
    return ep, events


def kinds(events):
    return [e.kind for e in events if e.kind != "audio"]


def test_speech_then_silence_gives_start_then_end():
    ep, ev = run([0.0] * 5 + [0.9] * 20 + [0.0] * 30)
    assert kinds(ev) == ["start", "end"]
    end = next(e for e in ev if e.kind == "end")
    assert 19 * WINDOW_MS <= end.speech_ms <= 21 * WINDOW_MS      # speech excludes the 500 ms hangover
    assert not ep.in_speech


def test_start_carries_preroll_so_the_first_syllable_is_kept():
    ep, ev = run([0.0] * 10 + [0.9] * 10 + [0.0] * 30)
    start = next(e for e in ev if e.kind == "start")
    assert len(start.pcm) >= (3 + 3) * WINDOW_BYTES              # confirm windows + pre-roll


def test_short_pause_inside_speech_does_not_split():
    ep, ev = run([0.9] * 10 + [0.0] * 8 + [0.9] * 10 + [0.0] * 30)   # 256 ms pause < 500 ms hangover
    assert kinds(ev) == ["start", "end"]


def test_long_pause_splits_into_two_utterances():
    ep, ev = run([0.9] * 10 + [0.0] * 20 + [0.9] * 10 + [0.0] * 20)
    assert kinds(ev) == ["start", "end", "start", "end"]


def test_click_shorter_than_min_speech_never_starts():
    ep, ev = run([0.0] * 5 + [0.9] * 2 + [0.0] * 40)             # 64 ms < 96 ms
    assert kinds(ev) == []


def test_one_word_answer_is_kept():                              # «да» must survive (regression: was discarded at 224 ms)
    ep, ev = run([0.0] * 5 + [0.9] * 7 + [0.0] * 30)
    assert kinds(ev) == ["start", "end"]


def test_too_short_utterance_is_discarded_not_answered():
    cfg = EndpointConfig(min_speech_ms=64, min_utterance_ms=200)
    ep, ev = run([0.9] * 3 + [0.0] * 30, cfg)
    assert kinds(ev) == ["start", "discard"]


def test_hysteresis_keeps_speech_between_off_and_on_thresholds():
    ep, ev = run([0.9] * 6 + [0.5] * 40 + [0.0] * 30)            # 0.5 is above off (0.35): still speech
    assert kinds(ev) == ["start", "end"]
    assert next(e for e in ev if e.kind == "end").speech_ms >= 40 * WINDOW_MS


def test_max_utterance_forces_an_end():
    cfg = EndpointConfig(max_utterance_s=1.0)
    ep, ev = run([0.9] * 60, cfg)
    end = next(e for e in ev if e.kind == "end")
    assert end.forced


def test_speech_run_stats_track_barge_in_candidates():
    ep, _ = run([0.0] * 3 + [0.9] * 8)
    assert ep.run.windows == 8 and ep.run.ms == 8 * WINDOW_MS and ep.run.mean_prob == pytest.approx(0.9)
    ep2, _ = run([0.9] * 8 + [0.0])
    assert ep2.run.windows == 0


def test_energy_vad_separates_speechlike_burst_from_room_noise():
    vad = EnergyVAD()
    noise = [vad.prob(quiet(WINDOW_MS, seed=i)) for i in range(40)]
    assert max(noise) < 0.5
    loud = [vad.prob(burst(WINDOW_MS, seed=i)) for i in range(10)]
    assert min(loud) > 0.6 and vad.degraded is True


def test_energy_vad_endpointing_end_to_end_on_signal():
    ep = Endpointer(EnergyVAD())
    sig = quiet(800) + burst(1500) + quiet(900, seed=5)
    kinds_seen = []
    for i in range(0, len(sig), 1000):
        kinds_seen += [e.kind for e in ep.push(sig[i:i + 1000]) if e.kind != "audio"]
    assert kinds_seen == ["start", "end"]


def test_make_vad_falls_back_and_says_so():
    vad = make_vad("energy")
    assert vad.degraded and vad.name == "energy-fallback"
