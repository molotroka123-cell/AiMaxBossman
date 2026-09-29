"""Offline self-test of the call pipeline: ``bossman call selftest`` / dashboard button.

Every result is labelled «ТЕСТ БЕЗ TELEGRAM»: the far end is a synthetic peer on the loopback transport, no Telegram
account, network or credential is involved, and the verdict never claims a real call.

Two modes, always stated in the result:
* real engines (Whisper + Piper + VAD present): the peer speaks known Russian phrases through Piper, the call
  pipeline hears them through its VAD/endpointer and Whisper; WER (recognised vs spoken) and the response latency
  (end of speech -> first outgoing frame) are computed over the turns. The brain is ALWAYS a scripted one here, so
  the number is the audio path, not an LLM;
* scripted engines (speech/testing.py: scripted STT + tone TTS + energy VAD) when no real models are installed:
  this proves wiring and timing overhead of OUR code only. WER is not measured (the scripted STT does not listen)
  and the latency is plumbing, not speech performance.

``run(data_dir) -> dict``; never raises, never touches settings, STOP, credentials or a running call.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..audio.vad import EnergyVAD
from ..speech.testing import ScriptedBrain, ScriptedSTT, ToneTTS
from ..types import CallState, CancelToken, PeerRef, Phase, Turn, latency_stats
from .loopback import LoopbackTransport
from .session import CallSession, SessionConfig

LABEL = "ТЕСТ БЕЗ TELEGRAM"
MIN_TURNS_FOR_PERCENTILES = 10
PHRASES = (
    "Привет, как слышно?", "Расскажи, что у нас на сегодня.", "Какая сейчас погода в Москве?",
    "Напомни мне позвонить маме вечером.", "Сколько времени осталось до встречи?", "Открой список моих задач.",
    "Спасибо, это очень помогло.", "Давай проверим ещё одну фразу.", "Повтори, пожалуйста, последний ответ.",
    "Скажи что-нибудь про завтрашний день.", "Хорошо, я тебя понял.", "Теперь можно заканчивать проверку.",
)
_WORDS = re.compile(r"[a-zа-я0-9]+")


def _norm(text: str) -> list[str]:
    return _WORDS.findall(text.lower().replace("ё", "е"))


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (Levenshtein over words) of ``hypothesis`` against ``reference``; 1.0 for an empty hypothesis."""
    ref, hyp = _norm(reference), _norm(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return round(prev[-1] / len(ref), 4)


class _RecordingBrain(ScriptedBrain):
    """Scripted brain that remembers what the call pipeline heard (used for WER)."""

    def __init__(self, replies):
        super().__init__(replies)
        self.heard: list[str] = []

    async def reply(self, history: list[Turn], user_text: str, cancel: CancelToken):
        self.heard.append(user_text)
        async for piece in super().reply(history, user_text, cancel):
            yield piece


def _try_real_engines(data_dir: Path, brain: Any):
    """(stt, tts, vad_factory) when real Whisper + Piper are present and load, else None. Never uses the network."""
    from ..speech import factory
    try:
        stt, tts, _b, vad_factory = factory.build_engines(data_dir, brain=brain)
        return stt, tts, vad_factory
    except Exception:  # noqa: BLE001 - CallError (models missing) or any load failure: fall back, and say so
        return None


async def _peer_audio(tts: Any, text: str) -> bytes:
    cancel = CancelToken()
    chunks = [c async for c in tts.synthesize(text, cancel)]
    return b"".join(chunks)


async def _wait(cond, timeout: float, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def run_async(data_dir: Path | str, *, turns: int = MIN_TURNS_FOR_PERCENTILES, engines: tuple | None = None,
                    fast: bool | None = None, turn_timeout_s: float | None = None) -> dict[str, Any]:
    """``engines`` = (stt, tts, vad_factory) injects engines (tests); ``fast`` shortens line time (scripted only)."""
    data_dir = Path(data_dir)
    turns = max(1, min(int(turns), len(PHRASES)))
    phrases = list(PHRASES[:turns])
    brain = _RecordingBrain(["Понял вас."] * turns)
    real = False
    if engines is not None:
        stt, tts, vad_factory = engines
        real = not getattr(stt, "synthetic", False) and not str(getattr(stt, "name", "")).startswith("scripted")
    else:
        found = await asyncio.to_thread(_try_real_engines, data_dir, brain)
        if found is not None:
            (stt, tts, vad_factory), real = found, True
        else:
            stt = ScriptedSTT(list(phrases))
            tts = ToneTTS()
            vad_factory = EnergyVAD
    if fast is None:
        fast = not real
    vad = vad_factory()
    transport = LoopbackTransport()
    if fast:
        transport._tick_ms = 8                          # scripted plumbing test only: the line runs ~2.5x faster
    cfg = SessionConfig(greeting="", greet_wait_s=0.2, idle_prompt_s=600, idle_hangup_s=900, max_call_s=900,
                        pace=0.0 if fast else 1.0, drain_timeout_s=30)
    session = CallSession(call_id="selftest", transport=transport, peer=PeerRef(1, "synthetic"), stt=stt, tts=tts,
                          brain=brain, vad=vad, cfg=cfg)
    per_turn: list[dict[str, Any]] = []
    checks: list[dict[str, str]] = []
    task = asyncio.create_task(session.run())
    try:
        if not await _wait(lambda: session.record.state == CallState.ACTIVE, 10.0):
            raise RuntimeError("loopback call did not become active")
        rate = getattr(tts, "sample_rate", 22050)
        limit = turn_timeout_s if turn_timeout_s is not None else (8.0 if fast else 40.0)
        for i, phrase in enumerate(phrases):
            before = len(brain.heard)
            pcm = await _peer_audio(tts, phrase)
            await transport.feed_realtime(pcm, rate)
            await transport.feed_realtime(b"\x00\x00" * (transport.audio_format.sample_rate * 11 // 10), transport.audio_format.sample_rate)
            answered = await _wait(lambda: len(brain.heard) > before, limit)
            if answered:
                await _wait(lambda: session.phase == Phase.LISTENING and not session.playout.busy, limit)
            per_turn.append({"turn": i + 1, "answered": answered,
                             "wer": wer(phrase, brain.heard[before]) if answered and real else None})
        session.hangup("selftest_done")
    except Exception as exc:  # noqa: BLE001 - reported, never raised to the owner surface
        checks.append({"id": "run", "status": "BLOCKED", "message": f"Самопроверка прервана ({type(exc).__name__})."})
        session.hangup("selftest_error")
    try:
        record = await asyncio.wait_for(task, 30)
    except Exception:  # noqa: BLE001
        task.cancel()
        record = session.record
    latencies = [t.response_latency_ms for t in record.turns if t.response_latency_ms is not None]
    stats = latency_stats(latencies)
    answered_n = sum(1 for t in per_turn if t["answered"])
    wers = [t["wer"] for t in per_turn if t["wer"] is not None]
    dropped = record.counters.get("rx_dropped", 0)
    engines_used = {"stt": f"{stt.name}:{stt.model}", "tts": f"{tts.name}:{tts.voice}", "vad": getattr(vad, "name", "?"),
                    "brain": "scripted (в самопроверке модель разговора не вызывается)", "transport": "loopback",
                    "real_speech_models": real}
    checks += [
        {"id": "loopback_call", "status": "PASS" if record.state == CallState.ENDED and transport.dial_calls == 1 else "BLOCKED",
         "message": "Звонок по loopback-транспорту создан и завершён; набор выполнен ровно один раз."},
        {"id": "responses", "status": "PASS" if answered_n == turns else "BLOCKED",
         "message": f"Ответов получено: {answered_n} из {turns}."},
        {"id": "latency_sample", "status": "PASS" if stats["n"] >= MIN_TURNS_FOR_PERCENTILES else "WARN",
         "message": f"Измерено реплик: {stats['n']} (для p50/p95 нужно не меньше {MIN_TURNS_FOR_PERCENTILES})."},
        {"id": "no_dropped_audio", "status": "PASS" if dropped == 0 else "WARN",
         "message": f"Потеряно входящих кадров: {dropped}."},
    ]
    failed = any(c["status"] == "BLOCKED" for c in checks)
    if failed:
        status = "BLOCKED"
    elif not real or fast:
        status = "WARN"
    else:
        status = "PASS" if stats["n"] >= MIN_TURNS_FOR_PERCENTILES and dropped == 0 else "WARN"
    if fast and real:
        mode = "реальные движки речи, но линия ускорена (не реальное время): числа задержки не показательны"
    elif not real:
        mode = ("сценарные движки (ScriptedSTT + тональный TTS + энергетический VAD): проверена только проводка и накладные "
                "расходы нашего кода; WER не измеряется, задержка — не показатель речи. Реальные модели Whisper/Piper не найдены")
    else:
        mode = f"реальные движки речи ({engines_used['stt']}, {engines_used['tts']}); модель разговора подменена сценарием"
    verdict = (f"{LABEL}: {'провал' if failed else 'пройдено'} — {mode}. Настоящий звонок Telegram НЕ выполнялся "
               "и не проверен; он остаётся за владельцем.")
    return {"status": status, "ok": status != "BLOCKED", "code": "SELFTEST", "message": verdict, "verdict": verdict,
            "label": LABEL, "real_call": False, "transport": "loopback", "engines": engines_used,
            "turns_requested": turns, "turns_answered": answered_n, "latency_ms": stats,
            "latency_meaning": ("аппаратно-программная задержка нашего аудиотракта (конец речи → первый исходящий кадр), "
                                "без Telegram и без LLM" if real else "только проводка: не измерение речи"),
            "wer": {"measured": bool(wers), "mean": round(sum(wers) / len(wers), 4) if wers else None, "n": len(wers),
                    "reason": None if wers else "не измеряется: нет реальной модели распознавания"},
            "fast_line": bool(fast), "counters": dict(record.counters), "per_turn": per_turn, "checks": checks}


def run(data_dir: Path | str, **kw: Any) -> dict[str, Any]:
    """Synchronous entry point (CLI / API thread). Safe to call from inside a running event loop."""
    out: dict[str, Any] = {}

    def go() -> None:
        try:
            out.update(asyncio.run(run_async(data_dir, **kw)))
        except Exception as exc:  # noqa: BLE001
            out.update({"status": "BLOCKED", "ok": False, "code": "SELFTEST_ERROR", "label": LABEL, "real_call": False,
                        "message": f"{LABEL}: самопроверка не выполнена ({type(exc).__name__}). Настоящий звонок не выполнялся.",
                        "checks": []})

    t = threading.Thread(target=go, name="calls-selftest", daemon=True)
    t.start()
    t.join()
    return out
