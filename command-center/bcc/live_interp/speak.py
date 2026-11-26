"""Stage 2 — interpreting the OWNER's voice into the call (Cicada's «hold ⌥ and speak», on Bossman's local stack).

Hold-to-talk: begin() starts recording the owner's microphone, finish() stops it and runs
    owner's speech -> local Whisper -> translation -> local Piper voice -> virtual microphone (VB-Cable) -> the call app.
Paths that exist locally today:
    * any language -> English: Whisper's own task="translate" on the audio (one step, no text model);
    * English -> Russian: the pinned Marian (Router), then the Russian Piper voice;
    * anything else: UNSUPPORTED_PAIR — nothing is spoken, nothing invented.
Rules: owner-only (pult route, no agent tool); refused under the global STOP and stopped mid-phrase by it; the
translated text passes the same egress guard as Jeff's call voice before it is spoken (fail closed); no audio is stored.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .segmenter import RATE, to_wav
from .session import NoSpeech
from .translate import Router

MAX_SECONDS = 30
MIN_SECONDS = 0.4
VOICE_ENV = {"ru": "BOSSMAN_PIT_TTS_MODEL_PATH", "en": "BOSSMAN_PIT_TTS_MODEL_PATH_EN"}
PIPER_LANG = {"ru": ("ru_RU",), "en": ("en_US", "en_GB")}


class SpeakError(Exception):
    pass


def default_tts(text: str, language: str, stopped: Callable[[], bool]) -> tuple[bytes, int]:
    """Local Piper with the owner's voice for that language (Russian: BOSSMAN_PIT_TTS_MODEL_PATH, English: ..._EN)."""
    from bcc.oss import piper
    model = os.environ.get(VOICE_ENV.get(language, ""), "")
    exe = os.environ.get("BOSSMAN_PIT_TTS_EXECUTABLE", "")
    if not model or not exe:
        raise SpeakError("VOICE_NOT_INSTALLED_" + language.upper())
    return piper.synthesize_pcm(text, piper_executable=exe, model_path=model, stopped=stopped,
                                allowed_languages=PIPER_LANG[language])


def default_guard(text: str) -> bool:
    from bcc.telegram_calls.speech.jeff_engines import _egress_guard  # the same guard as Jeff's call voice
    return _egress_guard(text)


@dataclass
class Speaker:
    target: str                                      # language the other side hears: "en" | "ru"
    asr: Callable[..., dict]                         # asr(wav, task=...) -> {text, language}
    router: Router
    tts: Callable[[str, str, Callable[[], bool]], tuple[bytes, int]] = default_tts
    play: Callable[[bytes, int, Callable[[], bool]], str] | None = None
    guard: Callable[[str], bool] = default_guard
    stop_check: Callable[[], bool] = lambda: False
    clock: Callable[[], float] = time.monotonic
    _buf: bytearray = field(default_factory=bytearray)
    _recording: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def recording(self) -> bool:
        return self._recording

    def begin(self) -> None:
        if self.stop_check():
            raise SpeakError("OWNER_STOP")
        if self.target not in VOICE_ENV:
            raise SpeakError("UNSUPPORTED_TARGET")
        with self._lock:
            if self._recording:
                raise SpeakError("ALREADY_RECORDING")
            self._buf = bytearray()
            self._recording = True

    def feed(self, pcm: bytes) -> bool:
        """Microphone chunk while the button is held. False once the 30 s cap is reached (recording stops)."""
        with self._lock:
            if not self._recording:
                return False
            room = MAX_SECONDS * RATE * 2 - len(self._buf)
            self._buf += pcm[:max(0, room)]
            if room <= len(pcm):
                self._recording = False
                return False
            return True

    def cancel(self) -> None:
        with self._lock:
            self._recording = False
            self._buf = bytearray()

    def finish(self) -> dict:
        with self._lock:
            pcm, self._buf, self._recording = bytes(self._buf), bytearray(), False
        started = self.clock()
        seconds = len(pcm) / (RATE * 2)
        out = {"seconds": round(seconds, 2), "heard": "", "translation": "", "language": "", "spoken": False,
               "quality": "", "reason": ""}
        if self.stop_check():
            return {**out, "reason": "OWNER_STOP"}
        if seconds < MIN_SECONDS:
            return {**out, "reason": "TOO_SHORT"}
        wav = to_wav(pcm)
        try:
            heard = self.asr(wav, task="transcribe")
        except NoSpeech:
            return {**out, "reason": "NO_SPEECH"}
        except Exception as exc:  # noqa: BLE001
            return {**out, "reason": "ASR_FAILED:" + (str(exc)[:40] or type(exc).__name__)}
        text, lang = str(heard.get("text") or "").strip(), str(heard.get("language") or "")[:2].lower()
        out.update(heard=text, language=lang)
        if not text:
            return {**out, "reason": "NO_SPEECH"}

        def english_of():
            try:
                return str(self.asr(wav, task="translate").get("text") or "").strip() or None
            except Exception:  # noqa: BLE001
                return None

        tr = self.router.translate(text, source=lang, target=self.target, english_of=english_of)
        out.update(translation=tr.text, quality=tr.quality)
        if tr.quality in ("unsupported", "failed"):
            return {**out, "reason": "UNSUPPORTED_PAIR" if tr.quality == "unsupported" else "TRANSLATION_FAILED"}
        if not self.guard(tr.text):
            return {**out, "reason": "EGRESS_GUARD"}           # e.g. a secret said aloud is not put into the call
        if self.stop_check():
            return {**out, "reason": "OWNER_STOP"}
        try:
            audio, rate = self.tts(tr.text, self.target, self.stop_check)
        except Exception as exc:  # noqa: BLE001
            return {**out, "reason": "TTS_FAILED:" + (str(exc)[:40] or type(exc).__name__)}
        if self.play is None:
            return {**out, "reason": "NO_OUTPUT_DEVICE"}
        try:
            out["device"] = self.play(audio, rate, self.stop_check)
        except Exception as exc:  # noqa: BLE001
            return {**out, "reason": "PLAYBACK_FAILED:" + type(exc).__name__}
        out.update(spoken=not self.stop_check(), latency_ms=int((self.clock() - started) * 1000))
        if not out["spoken"]:
            out["reason"] = "OWNER_STOP"
        return out
