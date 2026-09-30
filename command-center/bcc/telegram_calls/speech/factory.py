"""Engine assembly for a call: which STT / TTS / brain / VAD the worker uses.

Real calls use the SAME Jeff/Bossman voice tract and local model routes that already exist (see ``jeff_engines``);
scripted engines exist only for the offline test mode and are refused for a real call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..audio.vad import VAD
from ..settings import CallSettings
from ..types import Brain, CallError, STTEngine, TTSEngine


@dataclass
class Engines:
    stt: STTEngine
    tts: TTSEngine
    brain: Brain
    vad: VAD
    recorder: Any = None
    notes: dict | None = None                 # what was actually used (models, fallbacks) — shown by status/doctor


async def build_engines(settings: CallSettings, mode: str = "", stopped: Callable[[], bool] = lambda: False) -> Engines:
    if mode == "offline_test":
        from .scripted import ScriptedBrain, ScriptedSTT, ToneTTS
        from ..audio.vad import make_vad
        return Engines(stt=ScriptedSTT(), tts=ToneTTS(),
                       brain=ScriptedBrain(["Привет! Я тебя слышу.", "Я умею разговаривать голосом и запоминать итоги.", "До свидания! [конец]"]),
                       vad=make_vad(settings.vad), notes={"engines": "scripted (offline test mode)"})
    from .jeff_engines import build_jeff_engines
    return await build_jeff_engines(settings, stopped=stopped)
