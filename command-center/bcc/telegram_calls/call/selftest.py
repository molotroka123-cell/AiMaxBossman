"""Offline self-test of the whole audio contour WITHOUT Telegram (``bossman call selftest``, dashboard button).

The far end is a synthetic interlocutor on the loopback line; everything on OUR side is the production code path
(resampling, VAD, endpointing, echo guard, barge-in, paced playout, session state machine, STOP, no-redial).
Verdicts here have evidence level ``loopback``: they prove plumbing and measure latency of our path, and are never
reported as a real Telegram call. With ``real=True`` the configured Jeff/Bossman engines (Whisper, Piper, local LLM)
replace the scripted ones, which turns the run into a TTS -> VAD -> STT acoustic loopback that also yields a word
error rate; without ``real`` the STT/TTS/LLM are scripted and the run says so.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..audio.endpointer import EndpointConfig
from ..audio.pcm import StreamResampler
from ..audio.vad import VAD, make_vad
from ..settings import CallSettings
from ..speech import scripted
from ..types import CallError, CallState, CancelToken, Outcome, PeerRef
from .loopback import LoopbackLine, LoopbackTransport
from .session import CallSession, SessionConfig

PEER = PeerRef(222000222, "synthetic interlocutor")
SCENARIOS = ("basic", "barge_in", "echo", "stop", "no_redial")      # what "all" runs
EXTRA_SCENARIOS = ("answering_machine",)                              # run by name only (it takes ~20 s and has its own ring delay)
CALLER_RATE = 16000


# ---------------------------------------------------------------- helpers

def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (Levenshtein over normalised words). 0.0 = identical."""
    def words(s: str) -> list[str]:
        return re.findall(r"[a-zа-яё0-9]+", s.lower().replace("ё", "е"))
    r, h = words(reference), words(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return round(prev[-1] / len(r), 3)


@dataclass
class Rig:
    """One synthetic call: session + loopback + caller helper."""
    session: CallSession
    transport: LoopbackTransport
    stt: Any
    engines: Any
    speech_kind: str
    vad_name: str
    events: list = field(default_factory=list)

    async def speech(self, text: str, seed: int = 1, gain: float = 1.0) -> bytes:
        return await synth_caller_audio(self.engines, text, seed=seed, gain=gain)

    async def say(self, text: str, seed: int = 1, gain: float = 1.0) -> None:
        pcm = await self.speech(text, seed, gain)
        if isinstance(self.stt, scripted.ScriptedSTT):
            self.stt.expect(text)
        await self.transport.feed_realtime(pcm, CALLER_RATE)

    async def hush(self, ms: int) -> None:
        await self.transport.feed_realtime(b"\x00\x00" * (CALLER_RATE * ms // 1000), CALLER_RATE)

    async def wait_until(self, cond: Callable[[], bool], timeout: float = 15.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            await asyncio.sleep(0.01)
        return False

    async def wait_reply_done(self, quiet_ms: int = 1500, timeout: float = 40.0) -> bool:
        """Outgoing audio has started and then stayed silent for ``quiet_ms``."""
        started = await self.wait_until(lambda: self.transport.sent_ms() > 0 and len(self.transport.sent) > self._sent_mark, timeout)
        if not started:
            return False
        last, since = len(self.transport.sent), time.monotonic()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.05)
            n = len(self.transport.sent)
            if n != last:
                last, since = n, time.monotonic()
            elif (time.monotonic() - since) * 1000 >= quiet_ms:
                return True
        return False

    _sent_mark = 0

    def mark(self) -> None:
        self._sent_mark = len(self.transport.sent)


async def synth_caller_audio(engines: Any, text: str, *, seed: int = 1, gain: float = 1.0) -> bytes:
    """16 kHz speech for the synthetic caller: the configured TTS when it is a real engine, else espeak-ng, else tones."""
    tts = engines.tts
    if not isinstance(tts, scripted.ToneTTS):
        chunks: list[bytes] = []
        async for c in tts.synthesize(text, CancelToken()):
            chunks.append(c)
        return StreamResampler(tts.sample_rate, CALLER_RATE).process(b"".join(chunks), last=True)
    pcm = scripted.espeak_speech(text, rate_hz=CALLER_RATE)
    if pcm is not None:
        return pcm
    # ``gain``: a person cutting in is louder than the far-end echo of our own voice; without espeak-ng (Windows) the
    # synthetic caller is a tone as quiet as our own output, which the echo guard rightly refuses to call a barge-in.
    return scripted.tone_speech(max(700, min(4000, len(text) * 70)), rate_hz=CALLER_RATE, seed=seed,
                                amp=min(0.9, 0.3 * gain))


def _speech_kind(engines: Any) -> str:
    if not isinstance(engines.tts, scripted.ToneTTS):
        return "engine_tts"
    return "espeak" if scripted.espeak_speech("да") is not None else "tones"


def _default_engines(replies: list[str] | None = None) -> Any:
    from ..speech.factory import Engines
    kind = _speech_kind(type("E", (), {"tts": scripted.ToneTTS()})())
    vad = make_vad("auto" if kind != "tones" else "energy")
    return Engines(stt=scripted.ScriptedSTT(), tts=scripted.ToneTTS(), brain=scripted.ScriptedBrain(replies), vad=vad,
                   notes={"engines": "scripted (offline self-test)", "speech": kind})


async def make_rig(engines: Any, *, transport: LoopbackTransport | None = None, cfg: SessionConfig | None = None) -> Rig:
    transport = transport or LoopbackTransport()
    cfg = cfg or SessionConfig(greeting="", greet_wait_s=0.2, endpoint=EndpointConfig(hangover_ms=400), speculative_ms=200)
    session = CallSession(call_id="selftest-" + str(int(time.time())), transport=transport, peer=PEER, stt=engines.stt,
                          tts=engines.tts, brain=engines.brain, vad=engines.vad, cfg=cfg)
    rig = Rig(session=session, transport=transport, stt=engines.stt, engines=engines, speech_kind=_speech_kind(engines),
              vad_name=engines.vad.name)
    return rig


async def _start(rig: Rig) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(rig.session.run())
    ok = await rig.wait_until(lambda: rig.session.record.state == CallState.ACTIVE, 5)
    if not ok:
        task.cancel()
        raise CallError("INTERNAL", detail="selftest_call_did_not_connect")
    return task


def _verdict(name: str, checks: dict[str, bool], metrics: dict, rig: Rig | None, started: float, note: str = "") -> dict:
    ok = all(checks.values())
    return {"scenario": name, "verdict": "PASS" if ok else "FAIL", "evidence_level": "loopback", "checks": checks, "metrics": metrics,
            "duration_s": round(time.monotonic() - started, 2), "speech": rig.speech_kind if rig else None,
            "vad": rig.vad_name if rig else None, "note": note}


# ---------------------------------------------------------------- scenarios

async def scenario_basic(engines_factory: Callable[[list[str] | None], Any]) -> dict:
    t0 = time.monotonic()
    engines = engines_factory(["Привет! Я тебя слышу, чем могу помочь?", "Меня зовут Джефф."])
    rig = await make_rig(engines)
    task = await _start(rig)
    q1, q2 = "Привет. Ты меня слышишь?", "А как тебя зовут?"
    await rig.say(q1)
    await rig.hush(700)
    rig.mark()
    done1 = await rig.wait_reply_done()
    await rig.say(q2, seed=2)
    await rig.hush(700)
    rig.mark()
    done2 = await rig.wait_reply_done()
    rig.session.hangup()
    rec = await asyncio.wait_for(task, 10)
    lat = rec.as_dict()["latency_ms"]
    metrics: dict = {"latency_ms": lat, "turns": len(rec.turns), "models": rec.models, "counters": rec.counters}
    checks = {"first_reply_audio": done1, "second_reply_audio": done2, "two_turns_recorded": len(rec.turns) >= 2,
              "latency_measured": lat["n"] >= 2, "outcome_completed": rec.outcome == Outcome.COMPLETED}
    brain = engines.brain
    if hasattr(brain, "history_seen") and len(brain.history_seen) >= 2:
        checks["context_kept_in_second_turn"] = any(role == "assistant" for role, _, _ in brain.history_seen[1])
    real = not isinstance(engines.stt, scripted.ScriptedSTT)
    if real and rec.turns:
        metrics["stt_note"] = "real STT on the engine's own TTS voice (acoustic loopback)"
    return _verdict("basic", checks, metrics, rig, t0)


async def scenario_barge_in(engines_factory) -> dict:
    t0 = time.monotonic()
    long_reply = " ".join(f"Это предложение номер {i} моего очень длинного ответа." for i in range(1, 9))
    engines = engines_factory([long_reply, "Хорошо, коротко."])
    rig = await make_rig(engines)
    task = await _start(rig)
    await rig.say("Расскажи что-нибудь длинное, пожалуйста.")
    await rig.hush(700)
    speaking = await rig.wait_until(lambda: rig.session.playout.frames_sent > 30, 25)
    await asyncio.sleep(0.4)
    t_speak = time.monotonic()
    caller = asyncio.get_running_loop().create_task(rig.say("Стоп, скажи другое.", seed=5, gain=2.5))
    barged = await rig.wait_until(lambda: rig.session.record.counters["barge_ins"] >= 1, 4)
    cut_ms = round((time.monotonic() - t_speak) * 1000, 1)
    n_at_cut = len(rig.transport.sent)
    await asyncio.sleep(0.3)
    leaked_frames = len(rig.transport.sent) - n_at_cut
    await caller
    await rig.hush(700)
    second = await rig.wait_until(lambda: len(getattr(engines.brain, "history_seen", [])) >= 2, 10)
    rig.session.hangup()
    rec = await asyncio.wait_for(task, 10)
    interrupted_recorded = False
    if second and hasattr(engines.brain, "history_seen"):
        interrupted_recorded = any(role == "assistant" and intr for role, _, intr in engines.brain.history_seen[1])
    checks = {"was_speaking": speaking, "barge_in_detected": barged, "audio_stopped_within_2_frames": leaked_frames <= 2,
              "interrupted_turn_recorded": interrupted_recorded or not hasattr(engines.brain, "history_seen")}
    return _verdict("barge_in", checks, {"speech_start_to_barge_in_ms": cut_ms, "frames_after_cut": leaked_frames,
                                        "barge_ins": rec.counters["barge_ins"], "transport_clear_calls": rig.transport.clear_calls},
                    rig, t0)


async def scenario_echo(engines_factory) -> dict:
    t0 = time.monotonic()
    reply = " ".join(f"Предложение {i} ответа для проверки эха." for i in range(1, 7))
    engines = engines_factory([reply])
    rig = await make_rig(engines, transport=LoopbackTransport(echo_delay_ms=250, echo_gain=0.35, echo_noise=0.001))
    task = await _start(rig)
    await rig.say("Скажи что-нибудь подлиннее.")
    await rig.hush(700)
    await rig.wait_until(lambda: rig.session.playout.frames_sent > 30, 25)
    await asyncio.sleep(3.0)                               # only our own echo returns
    barge = rig.session.record.counters["barge_ins"]
    turns = len(rig.session.record.turns)
    rig.session.hangup()
    rec = await asyncio.wait_for(task, 10)
    checks = {"no_false_barge_in": barge == 0, "no_self_answer": turns == 1}
    return _verdict("echo", checks, {"echo_delay_ms": 250, "echo_gain": 0.35, "false_barge_ins": barge, "turns": turns,
                                    "echo_blocked_runs": rec.counters["echo_blocked"]}, rig, t0)


async def scenario_stop(engines_factory) -> dict:
    t0 = time.monotonic()
    long_reply = " ".join(f"Предложение {i} длинного ответа." for i in range(1, 12))
    engines = engines_factory([long_reply])
    rig = await make_rig(engines)
    task = await _start(rig)
    await rig.say("Расскажи длинно.")
    await rig.hush(700)
    speaking = await rig.wait_until(lambda: rig.session.playout.frames_sent > 30, 25)
    n0 = len(rig.transport.sent)
    calls0 = len(getattr(engines.brain, "history_seen", []))
    t_stop = time.monotonic()
    confirmed = await rig.session.stop_and_wait("selftest_stop", timeout=5)
    stop_ms = round((time.monotonic() - t_stop) * 1000, 1)
    n1 = len(rig.transport.sent)
    await asyncio.sleep(0.4)
    rec = await asyncio.wait_for(task, 5)
    checks = {"was_speaking": speaking, "hangup_confirmed": confirmed, "audio_stopped_at_once": len(rig.transport.sent) - n1 == 0 and n1 - n0 <= 3,
              "outcome_stopped": rec.outcome == Outcome.STOPPED, "hangup_called_once": rig.transport.hangup_calls == 1,
              "no_new_model_call": len(getattr(engines.brain, "history_seen", [])) == calls0,
              "summary_without_llm": rec.summary is not None and rec.summary.generated_by == "mechanical"}
    return _verdict("stop", checks, {"stop_to_teardown_ms": stop_ms, "frames_after_stop": len(rig.transport.sent) - n1}, rig, t0)


async def scenario_no_redial(engines_factory) -> dict:
    t0 = time.monotonic()
    results, dials = {}, {}
    for code, outcome in (("CALL_DECLINED", Outcome.DECLINED), ("CALL_BUSY", Outcome.BUSY), ("CALL_NO_ANSWER", Outcome.NO_ANSWER)):
        engines = engines_factory(None)
        tr = LoopbackTransport(dial_error=CallError(code))
        rig = await make_rig(engines, transport=tr)
        rec = await asyncio.wait_for(rig.session.run(), 10)
        results[code] = rec.outcome == outcome
        dials[code] = tr.dial_calls
    checks = {f"{c.lower()}_outcome": ok for c, ok in results.items()}
    checks["dialled_exactly_once_each"] = all(n == 1 for n in dials.values())
    return _verdict("no_redial", checks, {"dial_calls": dials}, None, t0)


# ---------------------------------------------------------------- answering machine (incoming calls)

ANSWERING_CALLER_LINES = ("Здравствуйте, это Иван.", "Я звоню по поводу договора, перезвоните мне, пожалуйста.", "Спасибо, до свидания.")
ANSWERING_GREETING = "Это ИИ-ассистент владельца. Приму сообщение."
ANSWERING_REPLIES = ("Здравствуйте. Кто вы и по какому вопросу?", "Понял, передам. Нужно ли перезвонить?", "Хорошо, передам. До свидания! [конец]")


async def _outgoing_quiet(transport: LoopbackTransport, *, start_timeout: float = 20.0, quiet_s: float = 1.2) -> None:
    """Wait until we (the assistant) have started speaking and then stayed silent for ``quiet_s``."""
    mark = len(transport.sent)
    end = time.monotonic() + start_timeout
    while len(transport.sent) <= mark and time.monotonic() < end and not transport.ended:
        await asyncio.sleep(0.02)
    last, since = len(transport.sent), time.monotonic()
    while time.monotonic() - since < quiet_s and not transport.ended:
        await asyncio.sleep(0.05)
        if len(transport.sent) != last:
            last, since = len(transport.sent), time.monotonic()


def answering_caller_script(lines: tuple[str, ...] | list[str] | None = None, *, hang_up: bool = True):
    """The synthetic CALLER of an answered call: waits for the greeting, speaks its lines (waiting for each answer), hangs up."""
    texts = list(lines or ANSWERING_CALLER_LINES)

    async def script(transport: LoopbackTransport) -> None:
        eng = type("E", (), {"tts": scripted.ToneTTS()})()
        await _outgoing_quiet(transport, start_timeout=30.0)                      # the greeting
        for i, text in enumerate(texts):
            if transport.ended:
                return
            await transport.feed_realtime(await synth_caller_audio(eng, text, seed=i + 1), CALLER_RATE)
            await transport.feed_realtime(b"\x00\x00" * (CALLER_RATE * 700 // 1000), CALLER_RATE)
            await _outgoing_quiet(transport)
        if hang_up and not transport.ended:
            transport.peer_hangup()
    return script


class _RecordingTTS:
    """TTS that remembers what it was asked to say (the self-test checks the greeting and the refusals)."""

    def __init__(self, inner):
        self.inner, self.said = inner, []
        self.name, self.voice, self.sample_rate = inner.name, inner.voice, inner.sample_rate

    async def synthesize(self, text, cancel):
        self.said.append(text)
        async for chunk in self.inner.synthesize(text, cancel):
            yield chunk

    def status(self):
        return self.inner.status()


async def _wait_for(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def scenario_answering_machine(engines_factory) -> dict:
    """Incoming call, owner silent -> Jeff answers after the ring delay and takes a message (>= 3 turns, a log with a summary);
    the owner picks up in time -> Jeff never joins; STOP while ringing -> declined. All on the loopback line (NOT Telegram)."""
    import tempfile

    from ..answering import AnsweringMachine
    from ..answering_store import render_notice
    from ..speech.factory import Engines
    t0 = time.monotonic()
    made: list[Engines] = []

    async def factory(settings, mode):
        base = _default_engines(list(ANSWERING_REPLIES))
        for line_text in ANSWERING_CALLER_LINES:
            base.stt.expect(line_text)
        base.tts = _RecordingTTS(base.tts)
        made.append(base)
        return base

    with tempfile.TemporaryDirectory(prefix="calls-answering-selftest-") as home:
        settings = CallSettings(answering_machine=True, answer_ring_delay_s=1, answer_greeting=ANSWERING_GREETING)
        line = LoopbackLine(driver=answering_caller_script())
        machine = AnsweringMachine(home=home, line=line, engines_factory=factory, settings_loader=lambda: settings,
                                   stop_active=lambda: False, mode="offline_test",
                                   cfg_overrides={"greet_wait_s": 0.3, "endpoint": EndpointConfig(hangover_ms=400), "speculative_ms": 200})
        await machine.arm()
        await _wait_for(lambda: machine.ready_state == "ready", 10)

        # 1) the owner does not pick up: the ring delay passes, Jeff answers, takes a message, the caller hangs up
        call = line.ring(555001, "Тестовый звонящий")
        await asyncio.sleep(0.4)
        not_before_delay = not line.accepted
        await _wait_for(lambda: bool(machine.store.list()), 90)
        report = (machine.store.list() or [{}])[0]
        answered_engines = next((e for e in made if getattr(e.brain, "history_seen", None)), None)
        brain_seen = answered_engines.brain.history_seen if answered_engines else []
        said = answered_engines.tts.said if answered_engines else []
        checks = {
            "not_answered_before_ring_delay": not_before_delay,
            "answered_after_ring_delay": call.call_ref in line.accepted,
            "greeting_says_an_assistant_answers": bool(said) and "ассистент" in said[0],
            "dialogue_of_3_turns": len(brain_seen) >= 3,
            "report_written": bool(report) and report.get("outcome") == "message_taken",
            "report_has_summary": len(report.get("summary") or []) >= 2,
            "report_has_transcript": len(report.get("transcript") or []) >= 3,
            "callback_request_found": bool((report.get("callback") or {}).get("requested")),
            "owner_notice_is_ready": bool(report) and "Автоответчик" in render_notice(report) and bool(report.get("notify")),
            "never_dialled_or_called_back": all(t.dial_calls == 0 for t in line.transports.values()),
        }
        # 2) the owner picks up himself in time: Jeff never joins
        n_before = len(line.accepted)
        call2 = line.ring(555002, "Другой звонящий")
        await asyncio.sleep(0.3)
        line.owner_answers_elsewhere(call2)
        await asyncio.sleep(1.6)
        checks["owner_picked_up_in_time_jeff_did_not_join"] = len(line.accepted) == n_before and call2.call_ref not in line.accepted
        # 3) STOP while ringing: the call is declined, never answered
        call3 = line.ring(555003, "Третий звонящий")
        await asyncio.sleep(0.3)
        machine.stop("selftest_stop")
        await asyncio.sleep(0.2)
        checks["stop_while_ringing_declines"] = any(ref == call3.call_ref for ref, _ in line.rejected) and call3.call_ref not in line.accepted
        await machine.shutdown()
        metrics = {"turns": report.get("turns"), "duration_s": report.get("duration_s"), "summary": report.get("summary"),
                   "ring_delay_s": settings.answer_ring_delay_s, "outcomes": sorted({r.get("outcome") for r in machine.store.list()}),
                   "speech_note": "scripted STT/LLM/TTS: proves control flow of the answering machine, not recognition quality"}
    return _verdict("answering_machine", checks, metrics, None, t0, note="loopback line, not Telegram")


_RUNNERS = {"basic": scenario_basic, "barge_in": scenario_barge_in, "echo": scenario_echo, "stop": scenario_stop, "no_redial": scenario_no_redial,
            "answering_machine": scenario_answering_machine}


async def run_selftest(settings: CallSettings, scenario: str = "all", *, real: bool = False,
                       engines_factory: Callable[..., Awaitable[Any]] | None = None, mode: str = "",
                       emit: Callable[[dict], None] | None = None) -> dict:
    """Run one scenario or all. Never places a real call, never touches Telegram."""
    names = list(SCENARIOS) if scenario == "all" else [scenario]
    if any(n not in _RUNNERS for n in names):
        raise CallError("INTERNAL", detail="unknown_scenario")
    real_engines = None
    if real:
        from ..speech.factory import build_engines
        real_engines = await (engines_factory or build_engines)(settings, mode)
        if any(isinstance(getattr(real_engines, k), (scripted.ScriptedSTT, scripted.ToneTTS, scripted.ScriptedBrain)) for k in ("stt", "tts", "brain")):
            return {"verdict": "BLOCKED", "reason": "real engines are not configured (doctor shows which)", "results": []}

    def factory(replies):
        if real_engines is not None:
            return real_engines
        return _default_engines(replies)

    results = []
    for name in names:
        started = time.monotonic()
        try:
            res = await asyncio.wait_for(_RUNNERS[name](factory), 180)
        except Exception as exc:  # noqa: BLE001
            res = {"scenario": name, "verdict": "FAIL", "evidence_level": "loopback", "checks": {}, "metrics": {},
                   "note": f"scenario crashed: {type(exc).__name__}", "duration_s": round(time.monotonic() - started, 2)}
        results.append(res)
        if emit:
            emit({"event": "selftest", "scenario": name, "verdict": res["verdict"]})
    verdicts = [r["verdict"] for r in results]
    return {"verdict": "PASS" if all(v == "PASS" for v in verdicts) else "FAIL", "evidence_level": "loopback",
            "engines": "real (Jeff/Bossman)" if real_engines is not None else "scripted (offline)",
            "label": "ТЕСТ БЕЗ TELEGRAM", "results": results}


def default_conversation_script(stt: Any | None = None, engines: Any | None = None):
    """The synthetic interlocutor's part of an offline-mode dial: a short scripted conversation ending with a goodbye."""
    lines = ["Привет. Ты меня слышишь?", "Расскажи, что ты умеешь.", "Спасибо, пока."]

    async def script(transport: LoopbackTransport) -> None:
        await asyncio.sleep(1.0)
        for i, text in enumerate(lines):
            if stt is not None and isinstance(stt, scripted.ScriptedSTT):
                stt.expect(text)
            eng = engines or type("E", (), {"tts": scripted.ToneTTS()})()
            pcm = await synth_caller_audio(eng, text, seed=i + 1)
            await transport.feed_realtime(pcm, CALLER_RATE)
            await transport.feed_realtime(b"\x00\x00" * (CALLER_RATE * 700 // 1000), CALLER_RATE)
            mark = len(transport.sent)
            for _ in range(2000):                                    # wait for the reply to start and finish
                await asyncio.sleep(0.02)
                if len(transport.sent) > mark:
                    break
            last, since = len(transport.sent), time.monotonic()
            while time.monotonic() - since < 1.5 and not transport.ended:
                await asyncio.sleep(0.05)
                if len(transport.sent) != last:
                    last, since = len(transport.sent), time.monotonic()
            if transport.ended:
                return
    return script
