"""Streaming utterance segmenter: PCM16 mono frames in, finished utterances out (pure, no dependencies).

A call is a continuous stream; ASR wants short phrases. Energy VAD over 30 ms frames with hysteresis:
an utterance starts after `start_frames` voiced frames, ends after `silence_ms` of silence or at `max_ms`
(long monologues are cut so subtitles keep flowing). Pieces shorter than `min_ms` are dropped as noise.
Pre-roll keeps the first syllable that triggered the start.
"""
from __future__ import annotations

import io
import math
import wave
from array import array
from dataclasses import dataclass

RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = RATE * FRAME_MS // 1000  # 480


@dataclass(frozen=True)
class Utterance:
    start_ms: int
    end_ms: int
    pcm: bytes          # PCM16 LE mono 16 kHz

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def frame_dbfs(frame: array) -> float:
    if not frame:
        return -120.0
    mean_sq = sum(s * s for s in frame) / len(frame)
    if mean_sq <= 0:
        return -120.0
    return 10 * math.log10(mean_sq / (32768.0 * 32768.0))


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class Segmenter:
    def __init__(self, *, threshold_dbfs: float = -45.0, start_frames: int = 3, silence_ms: int = 600,
                 min_ms: int = 400, max_ms: int = 12000, preroll_frames: int = 5):
        self.threshold = threshold_dbfs
        self.start_frames = start_frames
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        self.min_ms = min_ms
        self.max_frames = max(1, max_ms // FRAME_MS)
        self.preroll = preroll_frames
        self._buf = array("h")
        self._frame_idx = 0            # frames consumed since the stream start
        self._history: list[array] = []
        self._voiced_run = 0
        self._active: list[array] | None = None
        self._active_start = 0
        self._silence_run = 0

    @property
    def position_ms(self) -> int:
        return self._frame_idx * FRAME_MS

    def feed(self, pcm: bytes) -> list[Utterance]:
        if len(pcm) % 2:
            raise ValueError("PCM16 needs an even byte count")
        chunk = array("h")
        chunk.frombytes(pcm)
        self._buf.extend(chunk)
        out: list[Utterance] = []
        while len(self._buf) >= FRAME_SAMPLES:
            frame = self._buf[:FRAME_SAMPLES]
            del self._buf[:FRAME_SAMPLES]
            done = self._frame(frame)
            if done:
                out.append(done)
        return out

    def flush(self) -> list[Utterance]:
        done = self._close(self._frame_idx) if self._active is not None else None
        return [done] if done else []

    def _frame(self, frame: array) -> Utterance | None:
        voiced = frame_dbfs(frame) > self.threshold
        idx = self._frame_idx
        self._frame_idx += 1
        if self._active is None:
            self._history.append(frame)
            if len(self._history) > self.preroll + self.start_frames:
                self._history.pop(0)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= self.start_frames:
                self._active = list(self._history)
                self._active_start = idx + 1 - len(self._history)
                self._history = []
                self._silence_run = 0
            return None
        self._active.append(frame)
        self._silence_run = 0 if voiced else self._silence_run + 1
        if self._silence_run >= self.silence_frames:
            return self._close(idx + 1 - self._silence_run)
        if len(self._active) >= self.max_frames:
            return self._close(idx + 1)
        return None

    def _close(self, end_frame: int) -> Utterance | None:
        frames = self._active or []
        keep = max(0, end_frame - self._active_start)
        frames = frames[:keep]
        self._active = None
        self._voiced_run = 0
        self._silence_run = 0
        start_ms = self._active_start * FRAME_MS
        end_ms = start_ms + len(frames) * FRAME_MS
        if end_ms - start_ms < self.min_ms:
            return None
        pcm = b"".join(f.tobytes() for f in frames)
        return Utterance(start_ms, end_ms, pcm)
