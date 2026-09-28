"""Local speech for the Jeff web window: ASR with confidence, Piper TTS.

Reuse contract: WAV validation and the model directory come from
``bcc.oss.whisper`` (faster-whisper, CPU int8, local files only), synthesis is
``bcc.oss.piper.synthesize_ogg``. This module only adds what the voice loop
needs on top: a per-process cached recogniser, a confidence estimate so a
doubtful transcript is confirmed with the participant before it is sent, and
stable error codes. Audio stays in memory; nothing is downloaded or logged.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

from bcc.oss import whisper
from bcc.oss.piper import PiperError, synthesize_ogg

from .presentation import spoken_reply_text

#: Below this estimated confidence Jeff asks "I heard: «…» — send?" first.
CONFIRM_BELOW = 0.55
#: A segment this likely to be non-speech is treated as silence/noise.
NO_SPEECH_ABOVE = 0.6
MAX_TTS_CHARS = 900

_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}


class SpeechError(ValueError):
    """Stable code only; never audio, transcript or host paths."""


def asr_status() -> dict:
    status = whisper.status()
    return {"available": status.get("status") == "configured", "engine": "local",
            "reason_code": None if status.get("status") == "configured" else "VOICE_STT_UNAVAILABLE"}


def tts_paths() -> tuple[str, str, str]:
    return (os.environ.get("BOSSMAN_PIT_TTS_EXECUTABLE", ""),
            os.environ.get("BOSSMAN_PIT_TTS_MODEL_PATH", ""),
            shutil.which("ffmpeg") or "")


def tts_status() -> dict:
    exe, model, ffmpeg = tts_paths()
    ok = all(value and Path(value).is_file() for value in (exe, model, ffmpeg))
    return {"available": ok, "engine": "local",
            "reason_code": None if ok else "VOICE_ENGINE_UNAVAILABLE"}


def _recogniser(model_path: Path):
    key = str(model_path)
    with _model_lock:
        model = _model_cache.get(key)
        if model is None:
            try:
                from faster_whisper import WhisperModel
            except (ImportError, OSError) as exc:
                raise SpeechError("VOICE_STT_UNAVAILABLE") from exc
            model = WhisperModel(key, device="cpu", compute_type="int8", cpu_threads=4,
                                 num_workers=1, local_files_only=True)
            _model_cache.clear()
            _model_cache[key] = model
        return model


def transcribe_wav(audio: bytes, *, language: str = "ru",
                   stopped: Callable[[], bool] = lambda: False) -> dict:
    """PCM16 WAV -> {text, confidence, needs_confirm, ...}. Raises SpeechError."""
    try:
        clean, duration = whisper._validated_wav(audio)
        model_path = whisper._model_directory()
    except whisper.WhisperError as exc:
        message = str(exc)
        code = ("VOICE_STT_UNAVAILABLE" if "model" in message.lower() or "configure" in message.lower()
                else "VOICE_FORMAT_UNSUPPORTED")
        raise SpeechError(code) from exc
    except OSError as exc:
        raise SpeechError("VOICE_STT_UNAVAILABLE") from exc
    if stopped():
        raise SpeechError("VOICE_STOPPED")
    model = _recogniser(model_path)
    if not whisper._work_lock.acquire(blocking=False):
        raise SpeechError("VOICE_BUSY")
    try:
        segments, info = model.transcribe(
            io.BytesIO(clean), language=language, beam_size=5, vad_filter=True,
            condition_on_previous_text=False)
        rows = []
        for segment in segments:
            if stopped():
                raise SpeechError("VOICE_STOPPED")
            rows.append(segment)
            if len(rows) > 2000:
                raise SpeechError("VOICE_TOO_LONG")
    except SpeechError:
        raise
    except Exception as exc:  # noqa: BLE001 — native errors can carry paths
        raise SpeechError("VOICE_STT_FAILED") from exc
    finally:
        whisper._work_lock.release()
    speech = [row for row in rows if float(row.no_speech_prob) < NO_SPEECH_ABOVE
              and str(row.text).strip()]
    text = " ".join(str(row.text).strip() for row in speech).strip()
    if not text:
        raise SpeechError("VOICE_NO_SPEECH")
    weights = [max(0.01, float(row.end) - float(row.start)) for row in speech]
    mean_logprob = sum(float(row.avg_logprob) * w for row, w in zip(speech, weights)) / sum(weights)
    confidence = max(0.0, min(1.0, math.exp(mean_logprob)))
    return {"text": text[:4000], "confidence": round(confidence, 3),
            "needs_confirm": confidence < CONFIRM_BELOW,
            "no_speech_prob": round(max(float(row.no_speech_prob) for row in rows), 3),
            "duration_seconds": round(float(duration), 2),
            "language": str(getattr(info, "language", language))[:3]}


def tts_text(answer: str) -> str:
    """What Jeff speaks: visible reply without markup, cut at a sentence."""
    value = spoken_reply_text(answer)
    if "Источники:" in value:
        value = value.split("Источники:", 1)[0].strip()
    if len(value) <= MAX_TTS_CHARS:
        return value
    cut = value[:MAX_TTS_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    return (cut[:end + 1] if end > 200 else cut).strip()


def synthesize(answer: str, *, stopped: Callable[[], bool] = lambda: False) -> bytes:
    """Local Russian OGG/Opus for one reply. Raises SpeechError with a stable code."""
    text = tts_text(answer)
    if not text:
        raise SpeechError("VOICE_TEXT_INVALID")
    exe, model, ffmpeg = tts_paths()
    try:
        return synthesize_ogg(text, piper_executable=exe, model_path=model,
                              ffmpeg_executable=ffmpeg, stopped=stopped)
    except PiperError as exc:
        raise SpeechError(str(exc)) from exc
