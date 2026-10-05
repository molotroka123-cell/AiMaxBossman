"""Real engines for a call, built on Jeff's EXISTING voice tract, context, memory and rules (no parallel voice stack).

* STT  = ``bcc.pit.speech.transcribe_wav`` (Jeff's cached faster-whisper model, CPU int8, confidence, no-speech filter) on one
         VAD-segmented utterance at a time, in a worker thread; a speculative decode may start when the speaker pauses.
* TTS  = the same Piper executable and ru_RU model Jeff speaks with (``BOSSMAN_PIT_TTS_*`` / ``<data>/voice``), through the
         thin ``bcc.oss.piper.synthesize_pcm`` sibling of ``synthesize_ogg``; every sentence passes Jeff's egress guard first.
* Brain= ``bcc.pit.call_surface.CallParticipantRuntime`` (Jeff on a call): public guard, per-participant consent/memory, local-only.

Everything is local. Nothing here downloads a model, starts a server or falls back to a paid/cloud route: a missing piece is a
clear ``CallError`` that the doctor explains.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import time
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..audio.vad import make_vad
from ..settings import CallSettings, data_dir
from ..types import (CallError, CallSummary, CancelToken, STTResult, Turn)
from .factory import Engines

log = logging.getLogger("bcc.telegram_calls.jeff_engines")

MAX_UTTERANCE_S = 60
SPECULATIVE_TOLERANCE_BYTES = 16000 * 2 // 2          # extra audio (0.5 s) after which a speculative decode is stale
BUSY_RETRIES = 40                                      # VOICE_BUSY = another decode (a Telegram voice note) is running
BUSY_SLEEP_S = 0.15
STT_BEAM = 1                                           # latency over the last percent of accuracy on a live call
TTS_CHUNK_MS = 100


def _wav(pcm16k: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16k)
    return out.getvalue()


# ================================================================== STT

class _UtteranceStream:
    def __init__(self, engine: "JeffSTT"):
        self._e = engine
        self._buf = bytearray()
        self._spec: asyncio.Task | None = None
        self._spec_len = 0
        self._cancelled = False

    def feed(self, pcm16k: bytes) -> None:
        if not self._cancelled and len(self._buf) < MAX_UTTERANCE_S * 32000:
            self._buf.extend(pcm16k)

    async def partial(self) -> str:
        """Honest: Whisper has no partial hypotheses. This starts a speculative decode of what has been heard so far, so that
        ``finalize`` is instant when the speaker really was finished; it returns "" (never invented text)."""
        if self._spec is None and not self._cancelled and len(self._buf) > 3200:
            snapshot = bytes(self._buf)
            self._spec_len = len(snapshot)
            self._spec = asyncio.get_running_loop().create_task(self._e._decode(snapshot))
        return ""

    async def finalize(self) -> STTResult:
        audio = bytes(self._buf)
        if self._spec is not None and len(audio) - self._spec_len <= SPECULATIVE_TOLERANCE_BYTES:
            try:
                return await self._spec                      # only silence was added since the speculative decode began
            except (asyncio.CancelledError, CallError):
                raise
            except Exception:  # noqa: BLE001 - fall through to a fresh decode
                pass
        elif self._spec is not None:
            self._spec.cancel()
        return await self._e._decode(audio)

    def cancel(self) -> None:
        self._cancelled = True
        self._buf.clear()
        if self._spec is not None and not self._spec.done():
            self._spec.cancel()


class JeffSTT:
    name = "jeff-whisper"

    def __init__(self, *, transcribe: Callable[..., dict], stopped: Callable[[], bool], model: str = "", status_fn=None,
                 beam_size: int = STT_BEAM):
        self._transcribe, self._stopped, self.model = transcribe, stopped, model or "local"
        self._status_fn, self._beam = status_fn, beam_size
        self._lock = asyncio.Lock()                          # one decode at a time inside this process

    def new_stream(self, *, language: str = "ru") -> _UtteranceStream:
        return _UtteranceStream(self)

    def status(self) -> dict[str, Any]:
        return self._status_fn() if self._status_fn else {"available": True}

    async def _decode(self, pcm16k: bytes) -> STTResult:
        started = time.monotonic()
        wav = _wav(pcm16k)
        async with self._lock:
            for attempt in range(BUSY_RETRIES):
                try:
                    res = await asyncio.to_thread(self._transcribe, wav, language="ru", stopped=self._stopped, beam_size=self._beam)
                    break
                except Exception as exc:  # noqa: BLE001 - SpeechError carries a stable code, everything else is generic
                    code = str(exc)
                    if code == "VOICE_NO_SPEECH":
                        return STTResult(text="", duration_s=len(pcm16k) / 32000, decode_ms=(time.monotonic() - started) * 1000)
                    if code == "VOICE_BUSY" and attempt + 1 < BUSY_RETRIES:
                        await asyncio.sleep(BUSY_SLEEP_S)
                        continue
                    if code == "VOICE_STOPPED":
                        raise asyncio.CancelledError from None
                    raise CallError("STT_UNAVAILABLE", detail=code[:40] if code.isupper() else type(exc).__name__) from None
        return STTResult(text=str(res.get("text", "")).strip(), duration_s=float(res.get("duration_seconds", 0.0)),
                         confidence=float(res.get("confidence", 0.0)) if res.get("confidence") is not None else None,
                         decode_ms=round((time.monotonic() - started) * 1000, 1))

    async def warmup(self) -> None:
        """Load the cached model now (during the ring), not in the middle of the first reply."""
        with_silence = _wav(b"\x00\x00" * 8000)
        try:
            await asyncio.to_thread(self._transcribe, with_silence, language="ru", stopped=lambda: False, beam_size=1)
        except Exception:  # noqa: BLE001 - VOICE_NO_SPEECH is the expected outcome; the model is loaded either way
            pass


# ================================================================== TTS

class JeffTTS:
    name = "jeff-piper"

    def __init__(self, *, synthesize_pcm: Callable[..., tuple[bytes, int]], exe: str, model: str, egress_guard: Callable[[str], bool],
                 stopped: Callable[[], bool], sample_rate: int = 22050, audit: Callable[[str], None] | None = None):
        self._synth, self._exe, self._model = synthesize_pcm, exe, model
        self._egress_ok, self._stopped = egress_guard, stopped
        self._audit = audit                                   # the pre-TTS audit of Jeff's other voice paths (hash only on a call)
        self.voice = Path(model).stem
        self.sample_rate = sample_rate

    def status(self) -> dict[str, Any]:
        return {"available": Path(self._exe).is_file() and Path(self._model).is_file(), "voice": self.voice}

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        if not self._egress_ok(text):                        # call audio leaves the host through Telegram: same guard as a voice note
            raise CallError("TTS_UNAVAILABLE", detail="egress_guard")
        if self._audit is not None:
            self._audit(text)                                # raises CallError when it cannot be made durable or the text is a threat
        try:
            pcm, rate = await asyncio.to_thread(
                self._synth, text.strip(), piper_executable=self._exe, model_path=self._model,
                stopped=lambda: cancel.cancelled or self._stopped())
        except Exception as exc:  # noqa: BLE001
            if cancel.cancelled or str(exc) == "VOICE_STOPPED":
                return
            raise CallError("TTS_UNAVAILABLE", detail=str(exc)[:40] if str(exc).isupper() else type(exc).__name__) from None
        self.sample_rate = rate
        step = rate * TTS_CHUNK_MS // 1000 * 2
        for i in range(0, len(pcm), step):
            if cancel.cancelled:
                return
            yield pcm[i:i + step]
            await asyncio.sleep(0)


# ================================================================== Brain

_SUMMARY_SYSTEM = (
    "Ты кратко подводишь итог телефонного разговора ассистента с собеседником. Верни ТОЛЬКО JSON вида "
    '{"summary": "...", "agreed_tasks": ["..."]}. summary — 1-3 предложения в третьем лице о том, о чём говорили, '
    "без цитат, без имён третьих лиц и без инструкций кому-либо. agreed_tasks — только то, что собеседники ЯВНО договорились "
    "сделать; если такого нет, верни пустой список. Не выдумывай.")


class JeffBrain:
    """Jeff on a call, seen as the session's ``Brain``."""

    route = "jeff"

    def __init__(self, runtime: Any, peer_user_id: int, *, model: str = "local", stopped: Callable[[], bool] = lambda: False):
        self.runtime, self.peer, self.model, self._stopped = runtime, peer_user_id, model, stopped
        self._last_update: int | None = None
        self._call_id = ""

    # hooks the session calls when they exist
    def bind_call(self, call_id: str) -> None:
        self._call_id = call_id

    def turn_finished(self, spoken: bool) -> None:
        if self._last_update is not None:
            update, self._last_update = self._last_update, None
            if spoken:
                self.runtime.commit_turn(update, spoken=True)
            else:
                self.runtime.discard_turn(update)

    def status(self) -> dict[str, Any]:
        return {"available": True, "route": self.route}

    async def reply(self, history: list[Turn], user_text: str, cancel: CancelToken) -> AsyncIterator[str]:
        if self._last_update is not None:                    # a previous turn that never reported back: drop, never leak
            self.runtime.discard_turn(self._last_update)
            self._last_update = None
        if self._stopped():
            return
        event = asyncio.Event()
        watcher = asyncio.get_running_loop().create_task(_relay_cancel(cancel, event))
        try:
            session = [{"role": t.role, "content": t.text} for t in history if t.text.strip()]
            result = await self.runtime.reply(self.peer, user_text, session_history=session, cancel=event)
        finally:
            watcher.cancel()
        if result.kind == "cancelled" or cancel.cancelled:
            if result.update_id is not None:
                self.runtime.discard_turn(result.update_id)
            return
        if result.kind in ("error", "refused"):
            raise CallError("PEER_NOT_ALLOWED" if result.code in ("BLOCKED", "REVOKED") else "BRAIN_UNAVAILABLE", detail=result.code[:40])
        self._last_update = result.update_id
        yield result.text

    async def summarize(self, turns: list[Turn]) -> CallSummary:
        if self._stopped():
            raise CallError("BRAIN_UNAVAILABLE", detail="stopped")
        transcript = "\n".join(("Собеседник: " if t.role == "user" else "Ассистент: ") + t.text for t in turns if t.text.strip())[:6000]
        adapter, model = self.runtime.local_adapter, self.model
        if adapter is None or not transcript:
            raise CallError("BRAIN_UNAVAILABLE", detail="no_local_model")
        result = await asyncio.wait_for(adapter.chat(
            model, [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": transcript}],
            max_tokens=260, timeout=20), timeout=22)
        text = _strip_fences(result.text)
        try:
            data = json.loads(text)
            summary = _clean_line(str(data.get("summary", "")), 400)
            tasks = [_clean_line(str(t), 240) for t in (data.get("agreed_tasks") or [])[:5] if str(t).strip()]
        except (ValueError, AttributeError, TypeError):
            raise CallError("BRAIN_UNAVAILABLE", detail="summary_not_json") from None
        if not summary:
            raise CallError("BRAIN_UNAVAILABLE", detail="empty_summary")
        stored = self.runtime.finish_call(self.peer, self._call_id or "unknown", summary)
        return CallSummary(text=summary, agreed_tasks=[t for t in tasks if t], generated_by=f"jeff-local{'+memory' if stored else ''}")


async def _relay_cancel(token: CancelToken, event: asyncio.Event) -> None:
    await token.wait()
    event.set()


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    if "{" in t and "}" in t:
        t = t[t.index("{"):t.rindex("}") + 1]
    return t


def _clean_line(text: str, limit: int) -> str:
    from bcc.pit.secret_filter import redact_secrets
    value, _ = redact_secrets(" ".join((text or "").split()))
    return value[:limit]


# ================================================================== assembly

def _egress_guard(text: str) -> bool:
    """True when the text may leave the host as speech (unchanged by Jeff's egress guard and scrubber)."""
    try:
        from bcc.telegram_companion.adapters import scrub
        from bossman.notifications.telegram_transport import _egress_guard_text
        return _egress_guard_text(scrub(text, ())) == text
    except ImportError:
        return False                                         # fail CLOSED: the guard lives in bossman-core; without it nothing is spoken on a call


def make_call_audit(audit_dir: Path) -> Callable[[str], None]:
    """Pre-TTS audit for the call voice path, the same categories and file as Jeff's Telegram / window voice (``speech_audit``),
    but HASH-ONLY: no copy of the spoken text is written (nothing of the conversation is stored on a call).

    A security-sensitive sentence gets one durable row (time, category, SHA-256, length) BEFORE the engine runs; when the row
    cannot be written the sentence is not spoken (fail closed). A threat is audited and then refused, like in Jeff's window."""
    from bcc.pit import speech_audit

    def audit(text: str) -> None:
        category = speech_audit.security_category(text)
        if not category:
            return
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "schema": speech_audit.PRE_TTS_AUDIT_SCHEMA,
               "surface": "call", "category": category, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
               "chars": len(text), "redacted": ""}
        try:
            speech_audit._append_durable(speech_audit.audit_path(audit_dir), row)
        except OSError:
            raise CallError("TTS_UNAVAILABLE", detail="audit_failed") from None
        if speech_audit.is_threat(text):
            raise CallError("TTS_UNAVAILABLE", detail="threat_refused")
    return audit


def _read_sample_rate(model: str) -> int:
    try:
        cfg = json.loads(Path(model + ".json").read_text(encoding="utf-8"))
        return int(cfg.get("audio", {}).get("sample_rate", 22050))
    except (OSError, ValueError, TypeError):
        return 22050


async def build_jeff_engines(settings: CallSettings, *, pit_settings: Any | None = None, transcribe: Callable | None = None,
                             synthesize_pcm: Callable | None = None, env: dict | None = None,
                             stopped: Callable[[], bool] = lambda: False) -> Engines:
    """Assemble the real engines. Raises ``CallError`` with a code the doctor explains; never substitutes a cloud engine."""
    from bcc.pit import config as pit_config, speech
    from bcc.pit.call_surface import CallParticipantRuntime
    from bcc.oss import piper
    from bcc import jeff_desktop

    dd = data_dir()
    environ = env if env is not None else os.environ
    jeff_desktop.default_voice_env(dd, environ)              # the SAME defaults Jeff's window uses; no third resolver
    if settings.peer_user_id is None:
        raise CallError("PEER_NOT_SELECTED")
    if pit_settings is None:
        try:
            pit_settings = pit_config.load(pit_config.config_path(dd))
        except (OSError, ValueError):
            raise CallError("BRAIN_NOT_CONFIGURED", detail="pit_not_configured") from None
    if not pit_settings.local_models:
        raise CallError("BRAIN_NOT_CONFIGURED", detail="no_local_model")
    exe, model = environ.get("BOSSMAN_PIT_TTS_EXECUTABLE", ""), environ.get("BOSSMAN_PIT_TTS_MODEL_PATH", "")
    if not (exe and model and Path(exe).is_file() and Path(model).is_file() and Path(model + ".json").is_file()):
        raise CallError("TTS_UNAVAILABLE", detail="voice_not_configured")
    stt_ready = speech.asr_status() if transcribe is None else {"available": True}
    if not stt_ready.get("available"):
        raise CallError("STT_UNAVAILABLE", detail=str(stt_ready.get("reason_code") or "not_configured"))

    runtime = CallParticipantRuntime(pit_settings)           # created on the worker's event-loop thread (SQLite is thread-bound)
    if runtime._blocked(settings.peer_user_id, settings.peer_user_id):
        await runtime.close()
        raise CallError("PEER_NOT_ALLOWED", detail="blocked")
    stt = JeffSTT(transcribe=transcribe or speech.transcribe_wav, stopped=stopped,
                  model=Path(environ.get("BOSSMAN_WHISPER_MODEL_PATH", "") or "whisper").name, status_fn=speech.asr_status)
    tts = JeffTTS(synthesize_pcm=synthesize_pcm or piper.synthesize_pcm, exe=exe, model=model, egress_guard=_egress_guard,
                  stopped=stopped, sample_rate=_read_sample_rate(model), audit=make_call_audit(runtime.home / "logs"))
    brain = JeffBrain(runtime, settings.peer_user_id, model=pit_settings.local_models[0], stopped=stopped)
    asyncio.get_running_loop().create_task(stt.warmup())
    return Engines(stt=stt, tts=tts, brain=brain, vad=make_vad(settings.vad),
                   notes={"engines": "jeff (local Whisper / Piper / local model)", "stt": stt.model, "tts": tts.voice,
                          "llm": pit_settings.local_models[0], "surface": "call"})
