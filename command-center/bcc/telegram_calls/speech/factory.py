"""Builds the real speech engines for a call and reports their availability (``bossman call doctor``).

``build_engines(config) -> (stt, tts, brain, vad_factory)`` is what ``call.worker`` imports. ``config`` may be:
a ``Path``/``str`` (the Command Center data dir, what the worker passes), a ``SpeechConfig``, a mapping with the same
field names, or ``None`` (everything from the environment / defaults).

Model locations (no network download anywhere; explicit > environment > add-on models dir):
* Whisper: ``whisper_model_dir`` | ``BOSSMAN_WHISPER_MODEL_PATH`` | ``<models_dir>/whisper[/<name>]``
* Piper:   ``piper_voice_path`` | ``BOSSMAN_PIPER_VOICE_PATH`` | ``<models_dir>/piper/*.onnx`` (``ru*`` first)
* Brain:   the Telegram companion's own config (``BOSSMAN_TG_COMPANION_CONFIG`` or its default location)
* ``models_dir`` defaults to ``<data_dir>/addons/telegram-calls/models``.

Real engines only. A missing engine is a ``CallError`` (STT_UNAVAILABLE / TTS_UNAVAILABLE / BRAIN_NOT_CONFIGURED),
never a silent substitute; the only fallback is the flagged energy VAD when Silero is not installed.
"""
from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Mapping

from ..audio.vad import VAD, make_vad
from ..types import ERRORS, CallError
from .brain import CompanionBrain, load_companion_brain
from .stt import ENV_MODEL, FasterWhisperSTT, validate_model_dir
from .tts import ENV_VOICE, PiperTTS, validate_voice

ADDON_SUBDIR = ("addons", "telegram-calls", "models")


@dataclass(frozen=True)
class SpeechConfig:
    data_dir: Path | None = None
    models_dir: Path | None = None
    whisper_model_dir: Path | None = None
    whisper_compute_type: str = "int8"
    whisper_threads: int = 4
    piper_voice_path: Path | None = None
    companion_config: Path | None = None
    brain_route: str | None = None            # "main" | "fast" | None = fast when configured
    vad: str = "auto"                         # "auto" | "silero" | "energy"
    preload: bool = True                      # load STT/TTS models while the worker builds, not on the first turn

    @classmethod
    def coerce(cls, config: Any) -> "SpeechConfig":
        if config is None:
            return cls()
        if isinstance(config, cls):
            return config
        names = {f.name for f in fields(cls)}
        if isinstance(config, (str, os.PathLike)):
            return cls(data_dir=Path(config))
        if isinstance(config, Mapping):
            raw = {k: v for k, v in config.items() if k in names}
        else:
            raw = {k: getattr(config, k) for k in names if hasattr(config, k)}
        for k in ("data_dir", "models_dir", "whisper_model_dir", "piper_voice_path", "companion_config"):
            if raw.get(k):
                raw[k] = Path(raw[k])
            else:
                raw.pop(k, None)
        return cls(**raw)

    @property
    def models(self) -> Path | None:
        if self.models_dir:
            return self.models_dir
        return Path(self.data_dir).joinpath(*ADDON_SUBDIR) if self.data_dir else None


def pick_whisper_dir(cfg: SpeechConfig) -> Path | None:
    """Explicit path, else None when the environment variable is set (the engine reads it), else discovery."""
    if cfg.whisper_model_dir:
        return cfg.whisper_model_dir
    if os.environ.get(ENV_MODEL, "").strip():
        return None
    base = cfg.models / "whisper" if cfg.models else None
    if base is None or not base.is_dir():
        return None
    for cand in [base, *sorted(p for p in base.iterdir() if p.is_dir())]:
        try:
            return validate_model_dir(cand.resolve())
        except (ValueError, OSError):
            continue
    return None


def pick_piper_voice(cfg: SpeechConfig) -> Path | None:
    if cfg.piper_voice_path:
        return cfg.piper_voice_path
    if os.environ.get(ENV_VOICE, "").strip():
        return None
    base = cfg.models / "piper" if cfg.models else None
    if base is None or not base.is_dir():
        return None
    for onnx in sorted(base.glob("*.onnx"), key=lambda p: (not p.name.lower().startswith("ru"), p.name)):
        try:
            validate_voice(onnx.resolve())
            return onnx.resolve()
        except (ValueError, OSError):
            continue
    return None


def _make_stt(cfg: SpeechConfig) -> FasterWhisperSTT:
    return FasterWhisperSTT(pick_whisper_dir(cfg), compute_type=cfg.whisper_compute_type, cpu_threads=cfg.whisper_threads)


def _make_tts(cfg: SpeechConfig) -> PiperTTS:
    return PiperTTS(pick_piper_voice(cfg))


def _make_brain(cfg: SpeechConfig, *, with_context: bool = True) -> CompanionBrain:
    return load_companion_brain(cfg.companion_config, route=cfg.brain_route, with_context=with_context)


def build_engines(config: Any = None, *, stt: Any = None, tts: Any = None, brain: Any = None) -> tuple[Any, Any, Any, Callable[[], VAD]]:
    """Real engines or a ``CallError``. Runs on a worker thread: preloads the models (no event loop needed).

    ``stt`` / ``tts`` / ``brain`` are dependency-injection points for tests and the offline self-test."""
    cfg = SpeechConfig.coerce(config)
    stt = stt if stt is not None else _make_stt(cfg)
    tts = tts if tts is not None else _make_tts(cfg)
    if not stt.status().get("ok"):
        raise CallError("STT_UNAVAILABLE", detail=str(stt.status().get("reason") or "")[:80] or None)
    if not tts.status().get("ok"):
        raise CallError("TTS_UNAVAILABLE", detail=str(tts.status().get("reason") or "")[:80] or None)
    brain = brain if brain is not None else _make_brain(cfg)
    if cfg.preload:
        for eng in (stt, tts):
            loader = getattr(eng, "_load_sync", None)
            if callable(loader):
                loader()                               # raises CallError(STT_/TTS_UNAVAILABLE) with a class-name detail
    kind = cfg.vad

    def vad_factory() -> VAD:
        return make_vad(kind)

    return stt, tts, brain, vad_factory


# ---------------------------------------------------------------------- doctor
def _item(name: str, ok: bool, level_if_bad: str, code: str, info: dict[str, Any], *, warn: str | None = None) -> dict[str, Any]:
    msg, hint = ERRORS.get(code, ("", ""))
    level = "PASS" if ok and not warn else ("WARN" if ok else level_if_bad)
    return {"name": name, "level": level, "ok": ok, "message": warn or ("" if ok else msg),
            "remedy": "" if level == "PASS" else hint, "code": None if ok else code, "info": info}


def _vad_status(kind: str) -> dict[str, Any]:
    try:
        v = make_vad(kind)
        return {"engine": v.name, "degraded": bool(v.degraded)}
    except CallError as exc:
        return {"engine": None, "degraded": True, "code": exc.code}


def doctor(config: Any = None, *, probe_brain: bool = False) -> dict[str, Any]:
    """Per-engine availability WITHOUT loading models or placing a call. ``probe_brain`` also GETs /models on the
    LOCAL routes (loopback only). Shape: ``{"ok", "verdict", "engines": {stt,tts,brain,vad,transport}, "items"}``."""
    cfg = SpeechConfig.coerce(config)
    stt_s = _make_stt(cfg).status()
    tts_s = _make_tts(cfg).status()
    try:
        brain = _make_brain(cfg, with_context=False)
        brain_s = brain.status()
        if probe_brain:
            brain_s.update(_run_probe(brain))
            brain_s["ok"] = bool(brain_s.get("reachable"))
    except CallError as exc:
        brain_s = {"ok": False, "engine": "companion-local-llm", "code": exc.code, "reason": exc.detail,
                   "cloud_used": False}
    vad_s = _vad_status(cfg.vad)
    try:
        from ..call.pytgcalls_transport import dependencies_status
        transport_s = dependencies_status()
    except Exception as exc:  # noqa: BLE001
        transport_s = {"ok": False, "error": type(exc).__name__}
    items = [
        _item("stt", bool(stt_s.get("ok")), "BLOCKED", "STT_UNAVAILABLE", stt_s),
        _item("tts", bool(tts_s.get("ok")), "BLOCKED", "TTS_UNAVAILABLE", tts_s, warn=tts_s.get("warning") if tts_s.get("ok") else None),
        _item("brain", bool(brain_s.get("ok")), "BLOCKED", brain_s.get("code") or "BRAIN_UNAVAILABLE", brain_s),
        _item("vad", vad_s.get("engine") is not None and not vad_s.get("degraded"), "WARN", "VAD_UNAVAILABLE", vad_s,
              warn=("Silero VAD не установлен: используется упрощённый детектор речи." if vad_s.get("degraded") and vad_s.get("engine") else None)),
        _item("transport", bool(transport_s.get("ok")), "BLOCKED", "DEPENDENCIES_MISSING", transport_s),
    ]
    levels = {i["level"] for i in items}
    verdict = "BLOCKED" if "BLOCKED" in levels else ("WARN" if "WARN" in levels else "PASS")
    return {"ok": verdict != "BLOCKED", "verdict": verdict, "items": items,
            "engines": {"stt": stt_s, "tts": tts_s, "brain": brain_s, "vad": vad_s, "transport": transport_s}}


def _run_probe(brain: CompanionBrain) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def run() -> None:
        async def go() -> dict[str, Any]:
            try:
                return await brain.probe()
            finally:
                await brain.aclose()
        out.update(asyncio.run(go()))

    t = threading.Thread(target=run, name="brain-probe", daemon=True)
    t.start()
    t.join(15)
    return out or {"reachable": False, "routes": []}
