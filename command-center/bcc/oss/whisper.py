"""Offline speech recognition using the optional upstream faster-whisper engine.

Only a host-configured, already installed CTranslate2 model is accepted. The
caller supplies WAV bytes, never a server filesystem path or a remote URL.
The synchronous entry point belongs in a worker thread, behind owner auth.
"""
from __future__ import annotations

import importlib.util
import io
import math
import os
from pathlib import Path
import re
import struct
import threading
import wave

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
MAX_SEGMENTS = 6000
MAX_TEXT_CHARS = 200_000
_work_lock = threading.Lock()


class WhisperError(ValueError):
    """A safe, user-facing error, without local paths or audio contents."""


def _model_directory() -> Path:
    configured = os.environ.get("BOSSMAN_WHISPER_MODEL_PATH", "").strip()
    if not configured:
        raise WhisperError("Configure BOSSMAN_WHISPER_MODEL_PATH with an installed local model directory")
    path = Path(configured)
    if not path.is_absolute() or not path.is_dir():
        raise WhisperError("Whisper model must be an existing absolute local directory")
    # Upstream can fetch a fallback tokenizer even with local_files_only=True.
    # Requiring tokenizer.json prevents that path as well as model downloads.
    if not all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
        raise WhisperError("Whisper model is incomplete: model.bin, config.json and tokenizer.json are required")
    return path.resolve()


def status() -> dict:
    """Probe configuration without loading an engine or downloading weights."""
    installed = importlib.util.find_spec("faster_whisper") is not None
    reason = None
    try:
        _model_directory()
        configured = True
    except (WhisperError, OSError) as exc:
        configured = False
        reason = str(exc) if isinstance(exc, WhisperError) else "Local model directory is inaccessible"
    if not installed:
        reason = "Install the speech extra: bossman-command-center[speech]"
    return {"provider": "faster-whisper", "package_installed": installed,
            "model_configured": configured, "status": "configured" if installed and configured else "unavailable",
            "inference_verified": False, "reason": reason, "device": "cpu", "compute_type": "int8",
            "cloud_used": False, "download_on_request": False,
            "formats": ["wav"], "max_audio_bytes": MAX_AUDIO_BYTES,
            "max_audio_seconds": MAX_AUDIO_SECONDS}


def _validated_wav(audio: bytes) -> tuple[bytes, float]:
    if not isinstance(audio, bytes) or not 0 < len(audio) <= MAX_AUDIO_BYTES:
        raise WhisperError("Audio must be nonempty WAV bytes, at most 32 MiB")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels, width, rate, frames, compression, _ = source.getparams()
            if compression != "NONE" or channels not in (1, 2) or width != 2 or not 8000 <= rate <= 48000:
                raise WhisperError("Use PCM16 WAV, mono or stereo, at 8–48 kHz")
            duration = frames / rate
            if not 0 < duration <= MAX_AUDIO_SECONDS:
                raise WhisperError("Audio duration must be between zero and ten minutes")
            payload = source.readframes(frames)
            if len(payload) != frames * channels * width:
                raise WhisperError("WAV audio is truncated")
    except (wave.Error, EOFError, RuntimeError, struct.error) as exc:
        raise WhisperError("Unsupported audio: supply a PCM16 WAV recording") from exc
    # Re-encode the validated PCM container to exclude hidden streams/metadata
    # before the native decoder sees it. No media URLs can reach the engine.
    output = io.BytesIO()
    with wave.open(output, "wb") as clean:
        clean.setnchannels(channels)
        clean.setsampwidth(width)
        clean.setframerate(rate)
        clean.writeframes(payload)
    return output.getvalue(), duration


def transcribe_audio(audio: bytes, *, language: str = "auto") -> dict:
    """Transcribe a bounded uploaded WAV, CPU/int8, without network fallback.

    Only one transcription is admitted per process. The lock covers lazy
    generator consumption as well as model construction; errors release it.
    Models are released after a request to avoid retaining unified GPU/RAM
    needed by the user's LLM. No claim of wall-clock cancellation is made.
    """
    if not isinstance(language, str) or (language != "auto" and not re.fullmatch(r"[a-z]{2,3}", language)):
        raise WhisperError("Use auto or a two/three-letter language code")
    clean_audio, duration = _validated_wav(audio)
    try:
        model_path = _model_directory()
    except OSError as exc:
        raise WhisperError("Local model directory is inaccessible") from exc
    if not _work_lock.acquire(blocking=False):
        raise WhisperError("Speech recognition is busy; retry after the current recording")
    try:
        try:
            from faster_whisper import WhisperModel
        except (ImportError, OSError) as exc:
            raise WhisperError("Install or repair the speech extra: bossman-command-center[speech]") from exc
        try:
            model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                                 cpu_threads=4, num_workers=1, local_files_only=True)
            segments, info = model.transcribe(io.BytesIO(clean_audio),
                language=None if language == "auto" else language,
                beam_size=5, vad_filter=False, condition_on_previous_text=False)
            result = []
            text_chars = 0
            for segment in segments:
                start, end, text = float(segment.start), float(segment.end), str(segment.text).strip()
                if not all(map(math.isfinite, (start, end))) or not 0 <= start <= end <= duration + 1:
                    raise WhisperError("Speech engine returned invalid segment timing")
                text_chars += len(text)
                if len(result) >= MAX_SEGMENTS or text_chars > MAX_TEXT_CHARS:
                    raise WhisperError("Speech engine output exceeded the transcript limit")
                result.append({"start": start, "end": end, "text": text})
            detected = str(info.language)
            probability = float(info.language_probability)
            if not re.fullmatch(r"[a-z]{2,3}", detected) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise WhisperError("Speech engine returned invalid language metadata")
            return {"provider": "faster-whisper", "text": " ".join(row["text"] for row in result),
                    "segments": result, "language": detected, "language_probability": probability,
                    "duration_seconds": duration, "device": "cpu", "compute_type": "int8",
                    "cloud_used": False}
        except WhisperError:
            raise
        except Exception as exc:
            # Native engine exceptions may contain model paths or source data.
            raise WhisperError("Local speech recognition failed; check the installed model and speech runtime") from exc
    finally:
        _work_lock.release()
