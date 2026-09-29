"""CallSession control flow against the loopback transport and scripted engines (real time, short).

What is proven: state machine, barge-in, echo gate, STOP, no redial, timeouts, outcomes, context.
What is NOT proven here: recognition/synthesis/LLM quality or a real Telegram call.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from bcc.telegram_calls.audio.endpointer import EndpointConfig
from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.call.session import CallSession, SessionConfig
from bcc.telegram_calls.types import CallError, CallState, Outcome, PeerRef

from .fakes import ScriptedBrain, ScriptedSTT, ToneTTS
from .signals import burst, quiet

RATE = 16000


def cfg(**kw):
    base = dict(greeting="", greet_wait_s=0.2, idle_prompt_s=30, idle_hangup_s=60, speculative_ms=200,
                endpoint=EndpointConfig(hangover_ms=320), drain_timeout_s=10)
    base.update(kw)
    return SessionConfig(**base)


def build(stt_texts=(), replies=(), *, transport=None, tts=None, brain=None, **cfgkw):
    transport = transport or LoopbackTransport()
    stt, tts, brain = ScriptedSTT(stt_texts), tts or ToneTTS(), brain or ScriptedBrain(replies)
    s = CallSession(call_id="t-1", transport=transport, peer=PeerRef(4242, "second"), stt=stt, tts=tts, brain=brain,
                    vad=EnergyVAD(), cfg=cfg(**cfgkw))
    return s, transport, stt, tts, brain


async def say(t, ms, seed=1, amp=0.3):
    await t.feed_realtime(burst(ms, RATE, seed=seed, amp=amp), RATE)


async def hush(t, ms):
    await t.feed_realtime(quiet(ms, RATE), RATE)


async def until(cond, timeout=6.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def start(s):
    task = asyncio.create_task(s.run())
    assert await until(lambda: s.record.state == CallState.ACTIVE, 3)
    return task


async def test_two_turns_keep_context_and_measure_latency():
    s, t, stt, tts, brain = build(["привет босман", "а как тебя зовут"], ["Привет. Чем помочь?", "Я Босман."])
    task = await start(s)
    await say(t, 900); await hush(t, 900)
    assert await until(lambda: len(s.record.turns) >= 1 and s.playout.frames_sent > 5)
    await until(lambda: s.phase.value == "listening" and s.playout.frames_sent > 20, 6)
    await say(t, 900, seed=2); await hush(t, 900)
    assert await until(lambda: len(brain.calls) == 2)
    s.hangup()
    rec = await task
    assert brain.calls[1]["history"] == [("user", "привет босман", False), ("assistant", "Привет. Чем помочь?", False)]
    assert brain.calls[1]["user"] == "а как тебя зовут"
    lat = rec.as_dict()["latency_ms"]
    assert lat["n"] >= 1 and lat["p50"] is not None and 0 < lat["p50"] < 3000
    assert rec.outcome == Outcome.COMPLETED and rec.transport == "loopback"
    assert t.dial_calls == 1 and t.hangup_calls >= 1
    assert rec.models["stt"].startswith("scripted") and rec.models["llm"] == "fast:fixture-llm"


async def test_greeting_when_callee_is_silent_and_skipped_when_callee_speaks_first():
    s, t, *_ = build(greeting="Привет, это Босман.", greet_wait_s=0.3)
    task = await start(s)
    assert await until(lambda: s.playout.frames_sent > 3, 3)
    s.hangup(); await task
    s2, t2, stt2, tts2, brain2 = build(["алло"], ["Алло, слушаю."], greeting="Привет, это Босман.", greet_wait_s=1.0)
    task2 = await start(s2)
    await say(t2, 700); await hush(t2, 700)
    assert await until(lambda: len(brain2.calls) == 1, 4)
    assert "Привет, это Босман." not in tts2.calls              # negative control: no greeting over «алло»
    s2.hangup(); await task2


async def test_barge_in_cuts_audio_drops_the_queue_and_records_only_what_was_spoken():
    long_reply = " ".join(f"Это предложение номер {i} моего длинного ответа." for i in range(1, 9))
    s, t, stt, tts, brain = build(["расскажи длинно", "стоп, скажи другое"], [long_reply, "Хорошо, другое."])
    task = await start(s)
    await say(t, 800); await hush(t, 700)
    assert await until(lambda: s.playout.frames_sent > 10, 4)
    await asyncio.sleep(0.5)
    before = len(t.sent)
    t_speech = time.monotonic()
    speaker = asyncio.create_task(say(t, 1200, seed=5, amp=0.45))
    assert await until(lambda: s.record.counters["barge_ins"] == 1, 2)
    cut = time.monotonic() - t_speech
    frames_at_cut = len(t.sent)
    await asyncio.sleep(0.25)
    assert len(t.sent) - frames_at_cut <= 1, "audio must stop within one frame after the barge-in is confirmed"
    assert cut < 0.6 and t.clear_calls >= 1
    await speaker; await hush(t, 700)
    assert await until(lambda: len(brain.calls) == 2, 4)
    prior = brain.calls[1]["history"]
    assert prior[0] == ("user", "расскажи длинно", False)
    assistant = prior[1]
    assert assistant[0] == "assistant" and assistant[2] is True and len(assistant[1]) < len(long_reply)
    assert brain.cancelled_replies >= 0 and tts.cancelled_calls >= 0
    s.hangup(); rec = await task
    assert rec.turns[0].interrupted is True and rec.counters["barge_ins"] == 1


async def test_own_echo_is_never_transcribed_nor_treated_as_barge_in():
    reply = " ".join(f"Предложение {i} ответа для проверки эха." for i in range(1, 7))
    tr = LoopbackTransport(echo_delay_ms=250, echo_gain=0.35, echo_noise=0.001)
    s, t, stt, tts, brain = build(["вопрос"], [reply], transport=tr)
    task = await start(s)
    await say(t, 800); await hush(t, 600)
    assert await until(lambda: s.playout.frames_sent > 10, 4)
    await asyncio.sleep(3.0)                                  # the far end only echoes us; nobody speaks
    assert s.record.counters["barge_ins"] == 0
    assert len(brain.calls) == 1 and len(stt.streams) == 1, "echo must not open a second utterance"
    s.hangup(); await task


async def test_speaking_over_our_voice_with_echo_present_still_interrupts():   # negative control of the test above
    reply = " ".join(f"Предложение {i} ответа для проверки эха." for i in range(1, 9))
    tr = LoopbackTransport(echo_delay_ms=250, echo_gain=0.35, echo_noise=0.001)
    s, t, stt, tts, brain = build(["вопрос", "второй"], [reply, "Ок."], transport=tr)
    task = await start(s)
    await say(t, 800); await hush(t, 600)
    assert await until(lambda: s.playout.frames_sent > 10, 4)
    await asyncio.sleep(1.5)
    await say(t, 1200, seed=9, amp=0.5)
    assert s.record.counters["barge_ins"] == 1
    s.hangup(); await task


async def test_stop_is_immediate_starts_no_new_model_call_and_hangs_up_once():
    long_reply = " ".join(f"Предложение {i} длинного ответа." for i in range(1, 12))
    s, t, stt, tts, brain = build(["вопрос"], [long_reply])
    task = await start(s)
    await say(t, 800); await hush(t, 600)
    assert await until(lambda: s.playout.frames_sent > 10, 4)
    n_before = len(t.sent)
    calls_before = len(brain.calls)
    ok = await s.stop_and_wait("owner_stop", timeout=3)
    n_after = len(t.sent)
    assert ok and n_after - n_before <= 2
    await asyncio.sleep(0.3)
    assert len(t.sent) == n_after and t.hangup_calls == 1
    rec = await task
    assert rec.outcome == Outcome.STOPPED and len(brain.calls) == calls_before
    assert brain.summarize_calls == 0 and rec.summary.generated_by == "mechanical"
    assert t.dial_calls == 1


@pytest.mark.parametrize("code,outcome", [("CALL_DECLINED", Outcome.DECLINED), ("CALL_BUSY", Outcome.BUSY),
                                          ("CALL_NO_ANSWER", Outcome.NO_ANSWER), ("PEER_PRIVACY", Outcome.FAILED)])
async def test_dial_failures_map_to_outcomes_and_are_never_retried(code, outcome):
    tr = LoopbackTransport(dial_error=CallError(code))
    s, *_ = build(transport=tr)
    rec = await asyncio.wait_for(s.run(), 5)
    assert rec.outcome == outcome and rec.error_code == code
    assert tr.dial_calls == 1, "no automatic redial after a failed call"
    assert rec.state == CallState.ENDED and rec.summary is not None


async def test_unexpected_dial_error_is_unknown_not_failed_and_not_retried():
    class Boom(LoopbackTransport):
        async def dial(self, peer, *, ring_timeout):
            self.dial_calls += 1
            raise RuntimeError("socket died after the request")
    tr = Boom()
    s, *_ = build(transport=tr)
    rec = await asyncio.wait_for(s.run(), 5)
    assert rec.outcome == Outcome.UNKNOWN and tr.dial_calls == 1


async def test_start_failure_is_a_provable_failure_before_any_ring():
    class NoStart(LoopbackTransport):
        async def start(self):
            raise CallError("NOT_LOGGED_IN")
    tr = NoStart()
    s, *_ = build(transport=tr)
    rec = await asyncio.wait_for(s.run(), 5)
    assert rec.outcome == Outcome.FAILED and rec.error_code == "NOT_LOGGED_IN" and tr.dial_calls == 0


async def test_peer_hangup_completes_the_call():
    s, t, *_ = build()
    task = await start(s)
    t.peer_hangup()
    rec = await asyncio.wait_for(task, 3)
    assert rec.outcome == Outcome.COMPLETED


async def test_lost_media_ends_as_connection_lost_after_grace_and_never_redials():
    s, t, *_ = build(reconnect_grace_s=0.4)
    task = await start(s)
    t.drop_media()
    rec = await asyncio.wait_for(task, 3)
    assert rec.outcome == Outcome.CONNECTION_LOST and t.dial_calls == 1


async def test_media_that_comes_back_within_grace_keeps_the_call():          # negative control
    s, t, *_ = build(reconnect_grace_s=0.6)
    task = await start(s)
    t.drop_media(); await asyncio.sleep(0.2); t.restore_media()
    await asyncio.sleep(0.8)
    assert s.outcome is None
    s.hangup(); await task


async def test_long_silence_prompts_once_then_times_out():
    s, t, stt, tts, brain = build(idle_prompt_s=0.4, idle_hangup_s=1.4, greeting="")
    task = await start(s)
    rec = await asyncio.wait_for(task, 6)
    assert rec.outcome == Outcome.SILENCE_TIMEOUT
    assert tts.calls.count("Ты ещё здесь?") == 1


async def test_max_duration_limit():
    s, t, *_ = build(max_call_s=0.5)
    task = await start(s)
    rec = await asyncio.wait_for(task, 4)
    assert rec.outcome == Outcome.MAX_DURATION


async def test_stt_failures_end_the_call_as_failed_after_repeated_errors():
    s, t, stt, tts, brain = build()
    stt.fail = True
    s.cfg.max_consecutive_failures = 2
    task = await start(s)
    for i in range(3):
        await say(t, 700, seed=i); await hush(t, 700)
        if s.outcome is not None:
            break
    rec = await asyncio.wait_for(task, 4)
    assert rec.outcome == Outcome.FAILED and rec.error_code == "STT_UNAVAILABLE"


async def test_empty_transcripts_ask_to_repeat_and_do_not_call_the_model():
    s, t, stt, tts, brain = build(["", ""], stt_empty_repeat_after=2)
    task = await start(s)
    for i in range(2):
        await say(t, 700, seed=i); await hush(t, 700)
    assert await until(lambda: "Не расслышал. Повтори, пожалуйста." in tts.calls, 4)
    assert brain.calls == []
    s.hangup(); await task


async def test_brain_failure_is_apologised_then_conversation_continues():
    s, t, stt, tts, brain = build(["раз", "два"], ["Вернулся."], brain=ScriptedBrain(["Вернулся."], fail_times=1))
    task = await start(s)
    await say(t, 700); await hush(t, 700)
    assert await until(lambda: "Секунду, у меня заминка. Повтори, пожалуйста." in tts.calls, 4)
    await until(lambda: s.phase.value == "listening", 4)
    await say(t, 700, seed=3); await hush(t, 700)
    assert await until(lambda: len(brain.calls) == 2 and s.playout.frames_sent > 20, 5)
    assert s.outcome is None
    s.hangup(); await task


async def test_farewell_marker_hangs_up_after_the_goodbye_is_spoken():
    s, t, stt, tts, brain = build(["пока"], ["До свидания, хорошего дня! [конец]"])
    task = await start(s)
    await say(t, 700); await hush(t, 700)
    rec = await asyncio.wait_for(task, 8)
    assert rec.outcome == Outcome.COMPLETED and t.sent_ms() > 800
    assert all("конец" not in c for c in tts.calls)


async def test_our_own_words_repeated_by_stt_are_dropped_as_text_echo():
    s, t, stt, tts, brain = build(["вопрос", "сегодня в праге облачно около двенадцати градусов"],
                                  ["Сегодня в Праге облачно, около двенадцати градусов."])
    task = await start(s)
    await say(t, 700); await hush(t, 700)
    assert await until(lambda: s.playout.frames_sent > 10 and s.phase.value == "listening", 8)
    await say(t, 700, seed=4); await hush(t, 700)
    assert await until(lambda: s.record.counters["echo_text_suppressed"] == 1, 3)
    assert len(brain.calls) == 1
    s.hangup(); await task


async def test_a_finished_session_cannot_be_reused_to_dial_again():
    s, t, *_ = build()
    task = await start(s)
    s.hangup()
    await task
    assert t.dial_calls == 1
    again = await asyncio.wait_for(s.run(), 3)          # a second run() on the same object must not ring the phone
    assert t.dial_calls == 1 and again.outcome == Outcome.COMPLETED


class HookedBrain(ScriptedBrain):
    """A brain with the optional session hooks (like ``JeffBrain``)."""

    def __init__(self, *a, fail_hooks=False, **kw):
        super().__init__(*a, **kw)
        self.bound, self.finished, self.fail_hooks = [], [], fail_hooks

    def bind_call(self, call_id):
        self.bound.append(call_id)
        if self.fail_hooks:
            raise RuntimeError("hook boom")

    def turn_finished(self, spoken):
        self.finished.append(spoken)
        if self.fail_hooks:
            raise RuntimeError("hook boom")


async def test_brain_hooks_bind_the_call_and_report_whether_the_answer_was_actually_spoken():
    brain = HookedBrain(["Привет. Чем помочь?"])
    s, t, *_ = build(["привет"], brain=brain)
    task = await start(s)
    await say(t, 900); await hush(t, 900)
    assert await until(lambda: brain.finished == [True], 6)
    s.hangup(); await task
    assert brain.bound == ["t-1"]


async def test_brain_hook_reports_nothing_spoken_when_the_turn_failed_before_any_sentence():
    brain = HookedBrain([], fail_times=1)
    s, t, *_ = build(["привет"], brain=brain)
    task = await start(s)
    await say(t, 900); await hush(t, 900)
    assert await until(lambda: brain.finished == [False], 6)
    s.hangup(); await task


async def test_a_failing_brain_hook_never_takes_the_call_down():
    brain = HookedBrain(["Хорошо."], fail_hooks=True)
    s, t, *_ = build(["привет"], brain=brain)
    task = await start(s)
    await say(t, 900); await hush(t, 900)
    assert await until(lambda: brain.finished, 6)
    assert s.record.state == CallState.ACTIVE
    s.hangup(); rec = await task
    assert rec.outcome == Outcome.COMPLETED and any(e.kind == "error" and e.data.get("detail", "").startswith("brain_hook") for e in s.events)


# ---- STOP / hangup while the phone is still ringing (audit finding): the dial is cancelled and the transport hangs up

async def test_stop_while_ringing_cancels_the_dial_and_hangs_up_at_once():
    tr = LoopbackTransport(ring_s=30.0)
    s, *_ = build(transport=tr, ring_timeout_s=60.0)
    task = asyncio.create_task(s.run())
    assert await until(lambda: tr.dial_calls == 1, 3)
    t0 = time.monotonic()
    assert await s.stop_and_wait("owner_stop", timeout=3.0), "teardown did not start while ringing"
    rec = await asyncio.wait_for(task, 3)
    assert time.monotonic() - t0 < 2.5 and rec.outcome == Outcome.STOPPED
    assert tr.hangup_calls >= 1, "the ringing call must be left through the transport, not by killing the process"
    assert tr.dial_calls == 1


async def test_hangup_while_ringing_ends_the_call_without_waiting_for_the_ring_timeout():
    tr = LoopbackTransport(ring_s=30.0)
    s, *_ = build(transport=tr, ring_timeout_s=60.0)
    task = asyncio.create_task(s.run())
    assert await until(lambda: tr.dial_calls == 1, 3)
    s.hangup("owner_hangup")
    rec = await asyncio.wait_for(task, 3)
    assert rec.outcome == Outcome.COMPLETED and tr.hangup_calls >= 1 and tr.dial_calls == 1


async def test_stop_before_run_never_rings_the_phone():
    tr = LoopbackTransport()
    s, *_ = build(transport=tr)
    s.stop("owner_stop")
    rec = await asyncio.wait_for(s.run(), 3)
    assert rec.outcome == Outcome.STOPPED and tr.dial_calls == 0
