"""Utterance segmentation: VAD windows -> speech start / audio / end events.

Pure state machine over 32 ms windows, no clocks and no I/O, so it is deterministic to test.
Hysteresis: speech starts after ``min_speech_ms`` of windows >= ``on_threshold`` and ends after
``hangover_ms`` of windows < ``off_threshold``. A pre-roll ring keeps the first syllable that
happened before the start was confirmed. ``speech_run_ms`` (consecutive speech-like windows even
while idle) is what barge-in gating reads.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .pcm import FrameSlicer, rms
from .vad import VAD, WINDOW_BYTES, WINDOW_MS


@dataclass
class EndpointConfig:
    on_threshold: float = 0.6
    off_threshold: float = 0.35
    min_speech_ms: int = 96
    hangover_ms: int = 500
    preroll_ms: int = 320
    min_utterance_ms: int = 128
    max_utterance_s: float = 25.0


@dataclass
class EPEvent:
    kind: str                # "start" | "audio" | "end" | "discard"
    pcm: bytes = b""
    speech_ms: int = 0       # for end/discard: speech duration excluding trailing hangover
    forced: bool = False     # end caused by max_utterance_s
    window_index: int = 0    # index of the window that produced the event (32 ms units)


@dataclass
class RunStats:
    """Consecutive speech-like windows right now (idle or in-utterance)."""
    windows: int = 0
    prob_sum: float = 0.0
    level_sum: float = 0.0

    @property
    def ms(self) -> int:
        return self.windows * WINDOW_MS

    @property
    def mean_prob(self) -> float:
        return self.prob_sum / self.windows if self.windows else 0.0

    @property
    def mean_level(self) -> float:
        return self.level_sum / self.windows if self.windows else 0.0


class Endpointer:
    def __init__(self, vad: VAD, config: EndpointConfig | None = None):
        self.vad = vad
        self.cfg = config or EndpointConfig()
        self._slicer = FrameSlicer(WINDOW_BYTES)
        self._preroll: deque[bytes] = deque(maxlen=max(1, self.cfg.preroll_ms // WINDOW_MS))
        self.reset()

    # ------------------------------------------------------------ state
    def reset(self) -> None:
        self.vad.reset()
        self._slicer = FrameSlicer(WINDOW_BYTES)
        self._preroll.clear()
        self.in_speech = False
        self.run = RunStats()
        self._speech_windows = 0        # windows counted as speech (excludes the trailing silence)
        self._silence_windows = 0
        self._total_windows = 0
        self._index = 0
        self.last_prob = 0.0

    @property
    def silence_ms(self) -> int:
        """Trailing silence inside the current utterance (0 when idle)."""
        return self._silence_windows * WINDOW_MS if self.in_speech else 0

    @property
    def utterance_ms(self) -> int:
        return self._total_windows * WINDOW_MS if self.in_speech else 0

    def abandon(self) -> None:
        """Forget any utterance in progress (keeps the speech-run statistics barge-in reads).

        Called for every window while incoming audio is gated (we are speaking): what arrives then is mostly our own
        echo, and its silences must not later be mistaken for the end of the interrupting person's utterance.
        """
        self.in_speech = False
        self._speech_windows = self._silence_windows = self._total_windows = 0

    def resume(self, seed_windows: int) -> None:
        """Barge-in confirmed: the utterance starts NOW with ``seed_windows`` windows of already-heard speech."""
        self.in_speech = True
        self._speech_windows = self._total_windows = max(1, seed_windows)
        self._silence_windows = 0

    # ------------------------------------------------------------ input
    def push(self, pcm16k: bytes) -> list[EPEvent]:
        events: list[EPEvent] = []
        for window in self._slicer.push(pcm16k):
            events.extend(self._window(window))
        return events

    def _window(self, window: bytes) -> list[EPEvent]:
        cfg = self.cfg
        self._index += 1
        prob = self.vad.prob(window)
        self.last_prob = prob
        speechlike = prob >= (cfg.off_threshold if self.in_speech else cfg.on_threshold)
        if prob >= cfg.on_threshold:
            self.run.windows += 1
            self.run.prob_sum += prob
            self.run.level_sum += rms(window)
        elif not (self.in_speech and prob >= cfg.off_threshold):
            self.run = RunStats()
        out: list[EPEvent] = []
        if not self.in_speech:
            self._preroll.append(window)
            if self.run.ms >= cfg.min_speech_ms:
                self.in_speech = True
                self._speech_windows = self.run.windows
                self._silence_windows = 0
                self._total_windows = len(self._preroll)
                out.append(EPEvent("start", b"".join(self._preroll), window_index=self._index))
                self._preroll.clear()
            return out
        # in speech
        self._total_windows += 1
        if speechlike:
            self._speech_windows += self._silence_windows + 1     # short pauses belong to the speech
            self._silence_windows = 0
        else:
            self._silence_windows += 1
        out.append(EPEvent("audio", window, window_index=self._index))
        limit_hit = self._total_windows * WINDOW_MS >= cfg.max_utterance_s * 1000
        if self._silence_windows * WINDOW_MS >= cfg.hangover_ms or limit_hit:
            speech_ms = self._speech_windows * WINDOW_MS
            kind = "end" if speech_ms >= cfg.min_utterance_ms else "discard"
            out.append(EPEvent(kind, speech_ms=speech_ms, forced=limit_hit and self._silence_windows * WINDOW_MS < cfg.hangover_ms,
                               window_index=self._index))
            self.in_speech = False
            self.run = RunStats()
            self._speech_windows = self._silence_windows = self._total_windows = 0
            self.vad.reset()
        return out
