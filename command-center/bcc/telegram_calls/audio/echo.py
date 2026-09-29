"""Echo handling for a phone call where the far end may play our voice out loud.

We know EXACTLY what we transmit, so the guard works from that reference instead of guessing:

1. ``tx(level, t)`` records the energy envelope of what we send, ``rx(level, t)`` the energy of what comes back,
   both in 32 ms slots on the same monotonic clock.
2. While we speak, ``update()`` cross-correlates the two envelopes over plausible round-trip delays
   (``min_delay_ms``..``max_delay_ms``). A clear peak means an echo path exists; a least-squares fit
   gives its gain and delay.
3. ``assess(n_slots)`` answers "is the recent incoming audio REAL speech (barge-in allowed) or just our
   own echo?": real speech means the incoming level clearly exceeds what the echo path predicts
   (double talk). With no calibrated echo path but our own audio still recent, the guard is
   conservative and demands a much louder/longer input; when we have been silent long enough that no
   echo can exist, any speech is real.

``is_text_echo`` is the second line of defence at transcript level: a final transcript that is (nearly)
our own last sentences is dropped, never answered.

This is envelope-level detection, not a waveform AEC: it decides who is talking, it does not clean audio.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

import numpy as np

SLOT_MS = 32


@dataclass
class EchoPath:
    delay_slots: int
    gain: float
    floor: float
    confidence: float


@dataclass
class Verdict:
    real_speech: bool
    reason: str      # "no_tx_recent" | "double_talk" | "echo" | "uncalibrated_loud" | "uncalibrated_quiet"


class EchoGuard:
    def __init__(self, *, min_delay_ms: int = 40, max_delay_ms: int = 900, history_s: float = 8.0,
                 fit_window_s: float = 2.0, min_confidence: float = 0.5, excess_ratio: float = 1.8,
                 margin: float = 0.01, strict_level: float = 0.08):
        self.min_lag = max(0, min_delay_ms // SLOT_MS)
        self.max_lag = max(self.min_lag + 1, max_delay_ms // SLOT_MS)
        self.history_slots = int(history_s * 1000 / SLOT_MS)
        self._t0: float | None = None
        self._tx_acc: dict[int, list[float]] = {}      # slot -> levels of what we transmitted
        self._rx_acc: dict[int, list[float]] = {}      # slot -> levels of what came back
        self.fit_slots = int(fit_window_s * 1000 / SLOT_MS)
        self.min_confidence, self.excess_ratio, self.margin, self.strict_level = min_confidence, excess_ratio, margin, strict_level
        self.min_fit_slots = 16                       # >= 512 ms of our own audio inside the fit window
        self.gain_range = (0.02, 1.5)
        self.path: EchoPath | None = None
        self.last_tx_slot = -10**9
        self.tx_energy_floor = 0.004

    # ------------------------------------------------------------ feeding (real time, monotonic seconds)
    def _slot(self, t: float) -> int:
        if self._t0 is None:
            self._t0 = t
        return int((t - self._t0) * 1000 // SLOT_MS)

    def tx(self, level: float, t: float) -> None:
        slot = self._slot(t)
        self._tx_acc.setdefault(slot, []).append(level)
        if level > self.tx_energy_floor:
            self.last_tx_slot = max(self.last_tx_slot, slot)
        self._trim(self._tx_acc, slot)

    def rx(self, level: float, t: float) -> None:
        slot = self._slot(t)
        self._rx_acc.setdefault(slot, []).append(level)
        self._trim(self._rx_acc, slot)

    def _trim(self, store: dict[int, list[float]], slot: int) -> None:
        horizon = slot - self.history_slots
        if len(store) > self.history_slots * 2:
            for key in [k for k in store if k < horizon]:
                del store[key]

    # ------------------------------------------------------------ analysis helpers
    def _series(self, store: dict[int, list[float]], end_slot: int, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        for i in range(n):
            vals = store.get(end_slot - n + 1 + i)
            if vals:
                out[i] = float(np.sqrt(np.mean(np.square(vals))))
        return out

    def current_slot(self) -> int:
        return max(self._rx_acc) if self._rx_acc else 0

    def update(self) -> EchoPath | None:
        """Re-estimate the echo path from the last ``fit_window_s`` seconds. Cheap; call ~2x/second while speaking.

        Only slots from our first audible transmission onwards are used (leading zeros in both series
        would correlate spuriously), at least ``min_fit_slots`` of them, and only physically plausible
        gains are accepted (an echo cannot be much louder than what we sent).
        """
        end = self.current_slot()
        n = self.fit_slots
        rx_all = self._series(self._rx_acc, end, n)
        best: tuple[float, int, float, float] | None = None
        for lag in range(self.min_lag, self.max_lag + 1):
            tx_all = self._series(self._tx_acc, end - lag, n)
            active = np.nonzero(tx_all > self.tx_energy_floor)[0]
            if active.size == 0:
                continue
            tx, rx = tx_all[active[0]:], rx_all[active[0]:]
            if len(tx) < self.min_fit_slots or tx.std() < 1e-4 or rx.std() < 1e-6:
                continue
            c = float(np.corrcoef(rx, tx)[0, 1])
            if np.isnan(c):
                continue
            if best is None or c > best[0]:
                var = float(tx.var())
                g = float(np.cov(rx, tx, bias=True)[0, 1] / var) if var > 0 else 0.0
                best = (c, lag, g, float(rx.mean() - g * tx.mean()))
        if best and best[0] >= self.min_confidence and self.gain_range[0] <= best[2] <= self.gain_range[1]:
            c, lag, g, b = best
            if self.path is None:
                self.path = EchoPath(lag, g, max(b, 0.0), c)
            else:   # smooth: echo paths drift slowly
                self.path = EchoPath(lag if c >= self.path.confidence else self.path.delay_slots,
                                     0.7 * self.path.gain + 0.3 * g, max(0.7 * self.path.floor + 0.3 * b, 0.0),
                                     max(c, 0.9 * self.path.confidence))
        return self.path

    def predicted(self, slot: int) -> float | None:
        if self.path is None:
            return None
        vals = self._tx_acc.get(slot - self.path.delay_slots)
        tx = float(np.sqrt(np.mean(np.square(vals)))) if vals else 0.0
        return self.path.gain * tx + self.path.floor

    def tx_recent(self, slot: int) -> bool:
        return slot - self.last_tx_slot <= self.max_lag + 2

    # ------------------------------------------------------------ verdict
    def assess(self, n_slots: int) -> Verdict:
        """Judge the last ``n_slots`` incoming slots (a candidate barge-in run)."""
        end = self.current_slot()
        if not self.tx_recent(end - n_slots + 1):
            return Verdict(True, "no_tx_recent")
        rx = self._series(self._rx_acc, end, n_slots)
        if self.path is None:
            self.update()
        if self.path is None:
            # No echo path yet. An echo can never be louder than the source, so demand input that is both
            # absolutely loud and a clear fraction of our own recent output level.
            tx_recent = self._series(self._tx_acc, end, n_slots + self.max_lag)
            tx_ref = float(tx_recent[tx_recent > self.tx_energy_floor].mean()) if (tx_recent > self.tx_energy_floor).any() else 0.0
            loud = float(rx.mean()) >= max(self.strict_level, 0.6 * tx_ref)
            return Verdict(loud, "uncalibrated_loud" if loud else "uncalibrated_quiet")
        pred = np.array([self.predicted(end - n_slots + 1 + i) or 0.0 for i in range(n_slots)])
        over = rx > self.excess_ratio * pred + self.margin
        if float(over.mean()) >= 0.6:
            return Verdict(True, "double_talk")
        return Verdict(False, "echo")


# ---------------------------------------------------------------- text level

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("ё", "е"))


def is_text_echo(transcript: str, recent_spoken: list[str], *, threshold: float = 0.8, min_tokens: int = 3) -> bool:
    """True when ``transcript`` is (almost) something we just said ourselves.

    Containment of the transcript's tokens in the recent spoken tokens: short utterances such as
    «да» / «нет» are never dropped (``min_tokens``) — the owner answering our yes/no question is real speech.
    """
    words = _tokens(transcript)
    if len(words) < min_tokens:
        return False
    spoken = set()
    for sentence in recent_spoken:
        spoken.update(_tokens(sentence))
    if not spoken:
        return False
    hit = sum(1 for w in words if w in spoken)
    return hit / len(words) >= threshold
