"""Local streaming-style STT on faster-whisper (CTranslate2), CPU int8 by default. No network, no cloud.

* model directory convention = ``bcc.oss.whisper``: an existing ABSOLUTE local CTranslate2 directory holding
  ``model.bin``, ``config.json`` and ``tokenizer.json`` (``BOSSMAN_WHISPER_MODEL_PATH``), or an explicit
  ``model_dir``. The model is loaded with ``local_files_only=True``; this module never downloads anything.
* lazy: importing this module does not import faster_whisper; the model loads on the first decode
  (or ``await engine.ensure_ready()``), in the worker thread, never on the event loop.
* Whisper is not a streaming decoder: "partials" re-decode the audio collected so far at most every
  ``partial_interval_s`` (greedy, cheap) while the user is still talking; ``finalize`` decodes the whole
  utterance once with a small beam. Decoding is serialised on ONE worker thread (one CPU model), so
  a partial never runs concurrently with a final; ``partial`` skips instead of queueing when the thread is busy.
* missing package / model directory -> ``CallError("STT_UNAVAILABLE")`` at decode time; ``status()`` never
  loads anything and never raises.
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..audio.pcm import to_float
from ..types import ANALYSIS_RATE, CallError, STTResult

REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")
ENV_MODEL = "BOSSMAN_WHISPER_MODEL_PATH"
#: an utterance longer than this is decoded from its LAST seconds only (bounds latency and memory)
MAX_UTTERANCE_S = 30.0
#: below this a final decode is skipped (nothing to recognise)
MIN_DECODE_S = 0.12


def validate_model_dir(path: str | Path) -> Path:
    """Same rules as ``bcc.oss.whisper._model_directory``, for an explicit path. Raises ``ValueError`` (no path echo)."""
    p = Path(path)
    if not p.is_absolute() or not p.is_dir():
        raise ValueError("whisper model must be an existing absolute local directory")
    if not all((p / n).is_file() for n in REQUIRED_FILES):
        raise ValueError("whisper model is incomplete: model.bin, config.json and tokenizer.json are required")
    return p.resolve()


def resolve_model_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return validate_model_dir(explicit)
    configured = os.environ.get(ENV_MODEL, "").strip()
    if not configured:
        raise ValueError(f"configure {ENV_MODEL} with an installed local model directory")
    return validate_model_dir(configured)


def _default_model_factory(model_dir: Path, compute_type: str, cpu_threads: int) -> Any:
    from faster_whisper import WhisperModel      # ImportError -> handled by the caller
    return WhisperModel(str(model_dir), device="cpu", compute_type=compute_type, cpu_threads=cpu_threads,
                        num_workers=1, local_files_only=True)


class FasterWhisperSTT:
    name = "faster-whisper"

    def __init__(self, model_dir: str | Path | None = None, *, compute_type: str = "int8", cpu_threads: int = 4,
                 partial_interval_s: float = 1.2, final_beam: int = 3, partial_beam: int = 1,
                 model_factory: Callable[[Path, str, int], Any] | None = None):
        self._explicit_dir = model_dir
        self.compute_type, self.cpu_threads = compute_type, cpu_threads
        self.partial_interval_s, self.final_beam, self.partial_beam = partial_interval_s, final_beam, partial_beam
        self._factory = model_factory or _default_model_factory
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self._load_error: str | None = None
        try:
            self.model = resolve_model_dir(model_dir).name
        except ValueError:
            self.model = "unconfigured"

    # ------------------------------------------------------------------ availability
    def _package_installed(self) -> bool:
        if self._factory is not _default_model_factory:
            return True
        return importlib.util.find_spec("faster_whisper") is not None

    def status(self) -> dict[str, Any]:
        """Probe only (no model load, no download)."""
        reason = None
        configured = True
        try:
            resolve_model_dir(self._explicit_dir)
        except ValueError as exc:
            configured, reason = False, str(exc)
        installed = self._package_installed()
        if not installed:
            reason = "install the speech extra: bossman-command-center[speech]"
        ok = installed and configured and self._load_error is None
        return {"ok": ok, "engine": self.name, "model": self.model, "package_installed": installed,
                "model_configured": configured, "loaded": self._model is not None, "device": "cpu",
                "compute_type": self.compute_type, "cloud_used": False, "download_on_request": False,
                "reason": reason or self._load_error}

    def _load_sync(self) -> Any:
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                model_dir = resolve_model_dir(self._explicit_dir)
            except ValueError:
                raise CallError("STT_UNAVAILABLE", detail="model_dir") from None
            try:
                model = self._factory(model_dir, self.compute_type, self.cpu_threads)
            except ImportError:
                self._load_error = "package_missing"
                raise CallError("STT_UNAVAILABLE", detail="package_missing") from None
            except Exception as exc:  # noqa: BLE001 - native errors may contain paths: keep the class name only
                self._load_error = type(exc).__name__
                raise CallError("STT_UNAVAILABLE", detail=type(exc).__name__) from None
            self._model, self._load_error = model, None
            return model

    async def ensure_ready(self) -> None:
        """Preload (worker thread). Raises ``CallError(STT_UNAVAILABLE)``."""
        await asyncio.get_running_loop().run_in_executor(self._executor, self._load_sync)

    # ------------------------------------------------------------------ decoding
    def _decode_sync(self, audio: np.ndarray, language: str, beam: int) -> tuple[str, str, float | None]:
        model = self._load_sync()
        try:
            segments, info = model.transcribe(audio, language=language or None, beam_size=beam, vad_filter=False,
                                              condition_on_previous_text=False, without_timestamps=True)
            parts: list[str] = []
            logprobs: list[float] = []
            for seg in segments:                       # the generator does the real work: consume it here
                text = str(getattr(seg, "text", "")).strip()
                if text:
                    parts.append(text)
                    lp = getattr(seg, "avg_logprob", None)
                    if isinstance(lp, (int, float)) and math.isfinite(lp):
                        logprobs.append(float(lp))
        except CallError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CallError("STT_UNAVAILABLE", detail=type(exc).__name__) from None
        lang = str(getattr(info, "language", language) or language)
        conf = float(np.clip(np.exp(np.mean(logprobs)), 0.0, 1.0)) if logprobs else None
        return " ".join(parts).strip(), lang, conf

    def new_stream(self, *, language: str = "ru") -> "FasterWhisperStream":
        return FasterWhisperStream(self, language)


class FasterWhisperStream:
    def __init__(self, engine: FasterWhisperSTT, language: str):
        self.engine, self.language = engine, language
        self._buf = bytearray()
        self._cancelled = False
        self._last_partial = ""
        self._partial_len = 0
        self._partial_at = 0.0
        self._partial_running = False

    # never blocks: just appends (bounded)
    def feed(self, pcm16k: bytes) -> None:
        if self._cancelled or not pcm16k:
            return
        self._buf += pcm16k
        limit = int(MAX_UTTERANCE_S * ANALYSIS_RATE) * 2
        if len(self._buf) > limit:
            del self._buf[: len(self._buf) - limit]

    def _audio(self) -> np.ndarray:
        n = len(self._buf) // 2 * 2
        return to_float(bytes(self._buf[:n]))

    async def partial(self) -> str:
        eng = self.engine
        if self._cancelled or self._partial_running:
            return self._last_partial if not self._cancelled else ""
        now = time.monotonic()
        grew = len(self._buf) - self._partial_len >= ANALYSIS_RATE * 2 // 4       # >= 250 ms of new audio
        if not grew or now - self._partial_at < eng.partial_interval_s or len(self._buf) < int(MIN_DECODE_S * ANALYSIS_RATE) * 2:
            return self._last_partial
        self._partial_running = True
        self._partial_at, self._partial_len = now, len(self._buf)
        audio = self._audio()
        try:
            text, _, _ = await asyncio.get_running_loop().run_in_executor(
                eng._executor, eng._decode_sync, audio, self.language, eng.partial_beam)
            if not self._cancelled:
                self._last_partial = text
        except CallError:
            pass                                       # a failing partial is not fatal; the final reports it
        finally:
            self._partial_running = False
        return "" if self._cancelled else self._last_partial

    async def finalize(self) -> STTResult:
        eng = self.engine
        seconds = len(self._buf) / 2 / ANALYSIS_RATE
        if self._cancelled or seconds < MIN_DECODE_S:
            return STTResult(text="", duration_s=seconds, language=self.language, decode_ms=0.0)
        audio = self._audio()
        t0 = time.monotonic()
        text, lang, conf = await asyncio.get_running_loop().run_in_executor(
            eng._executor, eng._decode_sync, audio, self.language, eng.final_beam)
        if self._cancelled:
            return STTResult(text="", duration_s=seconds, language=self.language, decode_ms=0.0)
        return STTResult(text=text, duration_s=seconds, language=lang, confidence=conf,
                         decode_ms=round((time.monotonic() - t0) * 1000.0, 1))

    def cancel(self) -> None:
        self._cancelled = True
        self._buf.clear()
        self._last_partial = ""
