"""Local streaming TTS on Piper (ONNX), Russian voice, one sentence at a time. No network, no cloud.

* voice = an existing ABSOLUTE ``*.onnx`` file with its sibling ``*.onnx.json`` (explicit ``voice_path`` or
  ``BOSSMAN_PIPER_VOICE_PATH``). The voice is never downloaded here.
* ``sample_rate`` is read from the sibling JSON at construction (cheap, no model load), so the session knows the
  PCM rate before the first sentence. The ONNX model itself loads lazily on the first ``synthesize`` (worker thread).
* ``synthesize(text, cancel)`` is an async generator of PCM16 mono chunks (~``chunk_ms`` each). Piper works per
  sentence, so the first chunk arrives as soon as the first sentence is synthesised, later chunks follow while
  the caller plays. ``CancelToken`` is honoured between chunks AND inside the worker thread between piper sentences.
* Piper's Python API differs between releases; both are supported by feature detection (NOT verified against an
  installed piper here - the package is optional): ``PiperVoice.synthesize_stream_raw(text)`` (piper-tts 1.2.x,
  raw int16 bytes) and ``PiperVoice.synthesize(text)`` yielding chunks with ``audio_int16_bytes`` (1.3+).
* missing package / voice -> ``CallError("TTS_UNAVAILABLE")``; ``status()`` never loads and never raises.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..types import CallError, CancelToken

ENV_VOICE = "BOSSMAN_PIPER_VOICE_PATH"
MAX_TEXT_CHARS = 1000
_DONE = object()


def validate_voice(path: str | Path) -> tuple[Path, Path, dict]:
    """(onnx, json, parsed config). Raises ``ValueError`` without echoing paths."""
    onnx = Path(path)
    if not onnx.is_absolute() or onnx.suffix != ".onnx" or not onnx.is_file():
        raise ValueError("piper voice must be an existing absolute .onnx file")
    cfg = onnx.with_name(onnx.name + ".json")
    if not cfg.is_file():
        raise ValueError("piper voice config (.onnx.json) is missing next to the model")
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        rate = int(data["audio"]["sample_rate"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("piper voice config is unreadable") from None
    if not 8000 <= rate <= 48000:
        raise ValueError("piper voice sample rate out of range")
    return onnx.resolve(), cfg.resolve(), data


def resolve_voice(explicit: str | Path | None = None) -> tuple[Path, Path, dict]:
    if explicit:
        return validate_voice(explicit)
    configured = os.environ.get(ENV_VOICE, "").strip()
    if not configured:
        raise ValueError(f"configure {ENV_VOICE} with an installed local piper voice (.onnx)")
    return validate_voice(configured)


def _default_voice_factory(onnx: Path, cfg: Path) -> Any:
    from piper import PiperVoice           # ImportError handled by the caller
    return PiperVoice.load(str(onnx), config_path=str(cfg), use_cuda=False)


def iter_pcm(voice: Any, text: str):
    """Yield raw int16 PCM ``bytes`` per synthesised sentence for either Piper API generation."""
    if hasattr(voice, "synthesize_stream_raw"):
        for chunk in voice.synthesize_stream_raw(text):
            yield bytes(chunk)
        return
    for chunk in voice.synthesize(text):
        data = getattr(chunk, "audio_int16_bytes", chunk)
        yield bytes(data)


class PiperTTS:
    name = "piper"

    def __init__(self, voice_path: str | Path | None = None, *, chunk_ms: int = 100,
                 voice_factory: Callable[[Path, Path], Any] | None = None):
        self._explicit = voice_path
        self.chunk_ms = max(20, int(chunk_ms))
        self._factory = voice_factory or _default_voice_factory
        self._voice: Any = None
        self._load_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._load_error: str | None = None
        self.language: str | None = None
        try:
            onnx, _, cfg = resolve_voice(voice_path)
            self.voice = onnx.name.removesuffix(".onnx")
            self.sample_rate = int(cfg["audio"]["sample_rate"])
            lang = cfg.get("language")
            self.language = (lang.get("code") if isinstance(lang, dict) else None) or None
        except ValueError:
            self.voice, self.sample_rate = "unconfigured", 22050

    # ------------------------------------------------------------------ availability
    def _package_installed(self) -> bool:
        if self._factory is not _default_voice_factory:
            return True
        return importlib.util.find_spec("piper") is not None

    def status(self) -> dict[str, Any]:
        reason, configured = None, True
        try:
            resolve_voice(self._explicit)
        except ValueError as exc:
            configured, reason = False, str(exc)
        installed = self._package_installed()
        if not installed:
            reason = "install piper-tts (pip install piper-tts) via bossman call install"
        warning = None
        if configured and self.language and not self.language.lower().startswith("ru"):
            warning = f"voice language is {self.language}, not Russian"
        ok = installed and configured and self._load_error is None
        return {"ok": ok, "engine": self.name, "voice": self.voice, "sample_rate": self.sample_rate,
                "language": self.language, "package_installed": installed, "voice_configured": configured,
                "loaded": self._voice is not None, "cloud_used": False, "download_on_request": False,
                "reason": reason or self._load_error, "warning": warning}

    def _load_sync(self) -> Any:
        with self._load_lock:
            if self._voice is not None:
                return self._voice
            try:
                onnx, cfg, _ = resolve_voice(self._explicit)
            except ValueError:
                raise CallError("TTS_UNAVAILABLE", detail="voice") from None
            try:
                voice = self._factory(onnx, cfg)
            except ImportError:
                self._load_error = "package_missing"
                raise CallError("TTS_UNAVAILABLE", detail="package_missing") from None
            except Exception as exc:  # noqa: BLE001
                self._load_error = type(exc).__name__
                raise CallError("TTS_UNAVAILABLE", detail=type(exc).__name__) from None
            self._voice, self._load_error = voice, None
            return voice

    async def ensure_ready(self) -> None:
        await asyncio.get_running_loop().run_in_executor(self._executor, self._load_sync)

    # ------------------------------------------------------------------ synthesis
    def _produce(self, text: str, cancel: CancelToken, stop: threading.Event, loop: asyncio.AbstractEventLoop,
                 q: asyncio.Queue) -> None:
        """Worker thread: piper sentence -> queue. Errors travel through the queue as exception objects."""
        try:
            voice = self._load_sync()
            for pcm in iter_pcm(voice, text):
                if cancel.cancelled or stop.is_set():
                    break
                loop.call_soon_threadsafe(q.put_nowait, pcm)
        except CallError as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, CallError("TTS_UNAVAILABLE", detail=type(exc).__name__))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, _DONE)

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[bytes]:
        text = " ".join(str(text or "").split())[:MAX_TEXT_CHARS]
        if not text or cancel.cancelled:
            return
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        loop.run_in_executor(self._executor, self._produce, text, cancel, stop, loop, q)
        step = max(2, self.sample_rate * self.chunk_ms // 1000 * 2)
        try:
            while True:
                item = await q.get()
                if item is _DONE:
                    break
                if isinstance(item, CallError):
                    raise item
                for i in range(0, len(item), step):
                    if cancel.cancelled:
                        return
                    piece = item[i:i + step]
                    if len(piece) % 2:
                        piece = piece[:-1]
                    if piece:
                        yield piece
                if cancel.cancelled:
                    return
        finally:
            stop.set()          # abandoned or cancelled: the worker thread stops at its next sentence boundary
