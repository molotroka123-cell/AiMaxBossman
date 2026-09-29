"""Echo guard: the far end plays our voice back. Barge-in must fire for the owner talking over us, never for our echo.

Signals are envelopes at 32 ms slots on a shared clock. Negative controls are as important as positives:
a guard that says "echo" for everything (never barge-in) or "real speech" for everything (self-interruption)
would pass one half of the test alone.
"""
from __future__ import annotations

import numpy as np
import pytest

from bcc.telegram_calls.audio.echo import EchoGuard, SLOT_MS, is_text_echo

STEP = SLOT_MS / 1000.0


def speaking_envelope(n: int, seed: int = 3) -> np.ndarray:
    """Syllable-like on/off bursts of varying level (what TTS output looks like in energy)."""
    rng = np.random.default_rng(seed)
    out, i = np.zeros(n), 0
    while i < n:
        on = int(rng.integers(6, 16))
        level = float(rng.uniform(0.08, 0.25))
        out[i:i + on] = level * (0.7 + 0.3 * np.abs(np.sin(np.linspace(0, 3, min(on, n - i)))))
        i += on + int(rng.integers(2, 6))
    return out


def feed(guard, tx, rx, start=0):
    for n in range(len(tx)):
        t = (start + n) * STEP
        guard.tx(float(tx[n]), t)
        guard.rx(float(rx[n]), t)


def echo_of(tx, delay, gain, noise=0.002, seed=9):
    rng = np.random.default_rng(seed)
    rx = np.zeros_like(tx)
    rx[delay:] = gain * tx[:-delay]
    return rx + np.abs(rng.normal(0, noise, len(tx)))


def test_estimates_the_echo_path_from_our_own_reference():
    tx = speaking_envelope(160)
    g = EchoGuard()
    feed(g, tx, echo_of(tx, delay=8, gain=0.35))
    path = g.update()
    assert path is not None and abs(path.delay_slots - 8) <= 1
    assert path.gain == pytest.approx(0.35, rel=0.25) and path.confidence > 0.8


def test_pure_echo_is_not_real_speech():
    tx = speaking_envelope(160)
    g = EchoGuard()
    feed(g, tx, echo_of(tx, 8, 0.35))
    g.update()
    assert g.assess(8).real_speech is False and g.assess(8).reason == "echo"


def test_owner_talking_over_us_is_real_speech_even_though_echo_is_present():
    tx = speaking_envelope(220)
    rx = echo_of(tx, 8, 0.35)
    g = EchoGuard()
    feed(g, tx[:160], rx[:160])
    g.update()
    talk = 0.22 * (0.8 + 0.2 * np.sin(np.linspace(0, 20, 60)))
    rx2 = rx[160:220] + talk
    feed(g, tx[160:220], rx2, start=160)
    v = g.assess(8)
    assert v.real_speech is True and v.reason == "double_talk"


def test_speech_while_we_are_silent_is_always_real():
    g = EchoGuard()
    tx = np.zeros(200)
    rx = np.concatenate([np.full(100, 0.002), 0.2 * np.abs(np.sin(np.linspace(0, 30, 100)))])
    feed(g, tx, rx)
    v = g.assess(8)
    assert v.real_speech is True and v.reason == "no_tx_recent"


def test_uncalibrated_guard_is_conservative_while_we_speak():
    g = EchoGuard()
    tx = speaking_envelope(20)
    feed(g, tx, np.full(20, 0.03))                      # too little history to calibrate, faint input
    assert g.assess(6).real_speech is False
    g2 = EchoGuard()
    feed(g2, tx, np.full(20, 0.3))                      # loud input: could only be a person
    assert g2.assess(6).real_speech is True


def test_echo_gain_change_is_followed():
    tx = speaking_envelope(320, seed=5)
    g = EchoGuard()
    feed(g, tx[:160], echo_of(tx[:160], 6, 0.2))
    g.update()
    feed(g, tx[160:], echo_of(tx, 6, 0.5)[160:], start=160)
    for _ in range(4):
        g.update()
    assert g.path.gain > 0.3


def test_text_echo_detection_and_its_negative_control():
    said = ["Сегодня в Праге облачно, около двенадцати градусов."]
    assert is_text_echo("сегодня в праге облачно около двенадцати градусов", said) is True
    assert is_text_echo("а завтра будет дождь?", said) is False          # new content, must be answered
    assert is_text_echo("да", ["Да, конечно, сейчас сделаю."]) is False   # short answers are never dropped
    assert is_text_echo("сегодня облачно", []) is False
