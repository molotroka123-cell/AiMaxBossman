"""EchoGuard must not lock onto a wrong echo path from the first 512 ms of audio.

Root cause of the flaky offline self-test ``test_scenario_passes_offline[echo]`` (a false barge-in, ~1 run in 15 on a quiet
machine, more on a loaded CI runner): the first fit was accepted with only 16 slots (512 ms) of our own audio. Sixteen points
correlate at 0.5-0.6 by chance, so the guard picked a delay of 1-2 slots (32-64 ms) instead of the real 250 ms; its prediction of
the echo was then wrong, later echo frames "exceeded" the prediction and counted as double talk, i.e. the guard interrupted
our own voice. The fixtures are the real envelopes captured at the moment the old code produced a wrong first path.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bcc.telegram_calls.audio.echo import SLOT_MS, EchoGuard

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "echo_early_fit.json").read_text(encoding="utf-8"))
TRUE_DELAY_SLOTS = 250 // SLOT_MS      # the self-test's loopback echo is 250 ms, gain 0.35


def _guard_from(snapshot: dict) -> EchoGuard:
    g = EchoGuard()
    g._t0 = snapshot["t0"]
    g._tx_acc = {int(k): list(v) for k, v in snapshot["tx"].items()}
    g._rx_acc = {int(k): list(v) for k, v in snapshot["rx"].items()}
    audible = [s for s, vals in g._tx_acc.items() if max(vals) > g.tx_energy_floor]
    g.last_tx_slot = max(audible, default=-10**9)          # tx() maintains this in production; the snapshot only holds the envelopes
    return g


@pytest.mark.parametrize("i", range(len(FIXTURES)))
def test_a_short_first_window_never_yields_a_wrong_echo_path(i):
    g = _guard_from(FIXTURES[i])
    assert FIXTURES[i]["old_code_path"][0] not in (TRUE_DELAY_SLOTS - 1, TRUE_DELAY_SLOTS, TRUE_DELAY_SLOTS + 1)   # the fixture really is a bad fit
    path = g.update()
    assert path is None or abs(path.delay_slots - TRUE_DELAY_SLOTS) <= 1, path


def test_without_a_calibrated_path_the_verdict_stays_conservative_on_our_own_echo():
    """No path yet -> an echo-level return must not count as the person speaking."""
    g = _guard_from(FIXTURES[0])
    g.update()
    v = g.assess(8)
    assert not v.real_speech, v


def test_positive_control_a_longer_window_does_find_the_true_delay():
    """The fix must not disable calibration: with ~1.5 s of our audio the real 250 ms path is found."""
    rng = np.random.default_rng(3)
    g = EchoGuard()
    level = np.abs(np.sin(np.arange(200) / 7.0)) * (0.2 + 0.1 * rng.random(200))      # speech-like envelope, 6.4 s
    gain, noise = 0.35, 0.002
    t = 1000.0
    for k in range(60):                                                            # 60 slots = 1.9 s of audio
        g.tx(float(level[k]), t + k * SLOT_MS / 1000)
        rx = gain * level[k - TRUE_DELAY_SLOTS] if k >= TRUE_DELAY_SLOTS else 0.0
        g.rx(float(rx + abs(rng.normal(0, noise))), t + k * SLOT_MS / 1000)
    path = g.update()
    assert path is not None and abs(path.delay_slots - TRUE_DELAY_SLOTS) <= 1 and 0.2 <= path.gain <= 0.5, path
