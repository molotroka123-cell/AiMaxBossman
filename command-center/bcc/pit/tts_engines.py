"""Pluggable local TTS for Jeff: Piper is the default, CosyVoice 3 is a CANDIDATE slot.

Nothing here downloads, installs or starts anything. An engine is *available* only
when the owner has put its files on the host and, for the candidate, has also
switched a feature flag on and recorded the license/voice consent checklist
(``docs/pit/JEFF_1_8_VOICE_CHECKLIST.md``). A candidate is never the default and
never becomes one by itself: with the flag off, with missing files or with a failed
synthesis Jeff speaks with Piper.

Environment (all optional):

* ``BOSSMAN_JEFF_TTS_ENGINE``        ``piper`` (default) or ``cosyvoice``
* ``BOSSMAN_JEFF_TTS_COSYVOICE``     ``1`` enables the candidate slot (feature flag)
* ``BOSSMAN_JEFF_COSYVOICE_CMD``     absolute path of the owner-verified synthesis command
* ``BOSSMAN_JEFF_COSYVOICE_CONSENT`` absolute path of the checklist JSON (all items true)

Candidate command contract (no shell): ``<cmd> --text-file <utf-8 txt> --out <mono PCM16 wav>``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from bcc.oss import piper as piper_mod
from bcc.oss.piper import PiperError
from bcc.pit import voice_language

ENGINE_ENV = "BOSSMAN_JEFF_TTS_ENGINE"
FLAG_ENV = "BOSSMAN_JEFF_TTS_COSYVOICE"
CMD_ENV = "BOSSMAN_JEFF_COSYVOICE_CMD"
CONSENT_ENV = "BOSSMAN_JEFF_COSYVOICE_CONSENT"

#: Every item must be ``true`` in the owner's consent file before the candidate may run.
CONSENT_ITEMS = ("weights_license_checked", "voice_prompt_is_own_or_licensed",
                 "no_third_party_voice_imitation")


class TTSEngine(Protocol):
    name: str
    candidate: bool

    def status(self) -> dict: ...

    def synthesize(self, text: str, *, stopped: Callable[[], bool]) -> bytes: ...


def _flag_on() -> bool:
    return os.environ.get(FLAG_ENV, "").strip().lower() in {"1", "true", "yes"}


def _is_file(path: str) -> bool:
    return bool(path) and os.path.isabs(path) and Path(path).is_file()


class PiperEngine:
    name = "piper"
    candidate = False

    def __init__(self, synth: Callable[..., bytes] | None = None):
        self._synth = synth or piper_mod.synthesize_ogg

    @staticmethod
    def paths() -> tuple[str, str, str]:
        return (os.environ.get("BOSSMAN_PIT_TTS_EXECUTABLE", ""),
                os.environ.get("BOSSMAN_PIT_TTS_MODEL_PATH", ""),
                shutil.which("ffmpeg") or "")

    def status(self) -> dict:
        ok = all(_is_file(value) for value in self.paths())
        return {"engine": self.name, "candidate": False, "available": ok,
                "reason_code": None if ok else "VOICE_ENGINE_UNAVAILABLE",
                "languages": {"ru": ok, "en": ok and bool(voice_language.english_model())}}

    def synthesize(self, text: str, *, stopped: Callable[[], bool] = lambda: False) -> bytes:
        exe, model, ffmpeg = self.paths()
        model, _spoken = voice_language.pick_model(text, model)      # English text -> the owner's English voice when it is installed
        return self._synth(text, piper_executable=exe, model_path=model,
                           ffmpeg_executable=ffmpeg, stopped=stopped)


def consent_state(path: str | None = None) -> str:
    """'' when the owner's checklist file is present and every item is true, else a code."""
    path = os.environ.get(CONSENT_ENV, "") if path is None else path
    if not _is_file(path):
        return "CANDIDATE_CONSENT_MISSING"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "CANDIDATE_CONSENT_INVALID"
    if not isinstance(data, dict) or any(data.get(item) is not True for item in CONSENT_ITEMS):
        return "CANDIDATE_CONSENT_INCOMPLETE"
    return ""


class CosyVoiceCandidate:
    """CosyVoice 3 slot. Off unless flag + command + consent checklist are all present."""

    name = "cosyvoice3"
    candidate = True

    def __init__(self, runner: Callable[..., None] | None = None):
        self._run = runner or piper_mod._run

    def status(self) -> dict:
        if not _flag_on():
            code = "CANDIDATE_FLAG_OFF"
        elif not _is_file(os.environ.get(CMD_ENV, "")):
            code = "CANDIDATE_NOT_INSTALLED"
        elif not _is_file(shutil.which("ffmpeg") or ""):
            code = "VOICE_ENGINE_UNAVAILABLE"
        else:
            code = consent_state()
        return {"engine": self.name, "candidate": True, "enabled": _flag_on(),
                "available": not code, "reason_code": code or None}

    def synthesize(self, text: str, *, stopped: Callable[[], bool] = lambda: False) -> bytes:
        state = self.status()
        if not state["available"]:
            raise PiperError(state["reason_code"])
        if not 0 < len(text.strip()) <= piper_mod.MAX_TEXT_CHARS or "\x00" in text:
            raise PiperError("VOICE_TEXT_INVALID")
        command = os.environ[CMD_ENV]
        ffmpeg = shutil.which("ffmpeg") or ""
        with tempfile.TemporaryDirectory(prefix="bossman-voice-") as directory:
            source, wav, ogg = (Path(directory) / n for n in ("in.txt", "out.wav", "out.ogg"))
            source.write_text(text, encoding="utf-8")
            self._run([command, "--text-file", str(source), "--out", str(wav)],
                      stdin=None, stopped=stopped, timeout=120)
            piper_mod._validate_wav(wav)
            self._run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-i", str(wav), "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
                       "-f", "ogg", str(ogg)], stdin=None, stopped=stopped, timeout=30)
            if stopped():
                raise PiperError("VOICE_STOPPED")
            if not ogg.is_file() or not 32 < ogg.stat().st_size <= piper_mod.MAX_OGG_BYTES:
                raise PiperError("VOICE_AUDIO_INVALID")
            data = ogg.read_bytes()
        if not data.startswith(b"OggS"):
            raise PiperError("VOICE_AUDIO_INVALID")
        return data


def all_engines() -> list[TTSEngine]:
    return [PiperEngine(), CosyVoiceCandidate()]


def selected_engine_name() -> str:
    return "cosyvoice" if os.environ.get(ENGINE_ENV, "").strip().lower() in {
        "cosyvoice", "cosyvoice3"} else "piper"


def engine_chain(piper_synth: Callable[..., bytes] | None = None, *,
                 allow_candidate: bool = True) -> list[TTSEngine]:
    """The engine to try first, then Piper. The candidate is first only when the owner
    selected it AND it is available AND the caller allows it (Telegram guests never do);
    otherwise Piper is the only engine."""
    piper = PiperEngine(piper_synth)
    if allow_candidate and selected_engine_name() == "cosyvoice":
        candidate = CosyVoiceCandidate()
        if candidate.status()["available"]:
            return [candidate, piper]
    return [piper]
