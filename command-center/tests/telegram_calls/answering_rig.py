"""Emulator rig for the answering machine: the loopback LINE (a phone that rings), fake STT/TTS/LLM from ``fakes.py``, a real
``AnsweringMachine`` and the real ``CallSession``. No Telegram, no models: it proves control flow, never recognition quality."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from bcc.telegram_calls.answering import AnsweringMachine
from bcc.telegram_calls.audio.endpointer import EndpointConfig
from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call.loopback import LoopbackLine
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.speech.factory import Engines
from bcc.telegram_calls.types import CallError, CallState, CallSummary

from .fakes import ScriptedBrain, ScriptedSTT, ToneTTS
from .signals import burst, quiet

RATE = 16000
GREETING = "Это ИИ-ассистент владельца. Приму сообщение, что ему передать?"
CALLER = 777001
SECRETS = ["1AFakeSessionString-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "0123456789abcdef0123456789abcdef"]
CANARY = "OWNER-PRIVATE-CANARY-7731"


class MessageBrain(ScriptedBrain):
    """The call surface's brain, faked: scripted replies and a summary that says what the caller wants."""

    def __init__(self, replies, **kw):
        super().__init__(replies, **kw)
        self.summary_text = "Иван звонил по поводу договора и просит перезвонить."

    async def summarize(self, turns):
        self.summarize_calls += 1
        return CallSummary(text=self.summary_text, agreed_tasks=["должно быть проигнорировано"], generated_by="fixture-llm")


class LeakyBrain(MessageBrain):
    """If it is ever asked about the owner it leaks a private canary: only the guard stands between a caller and it."""

    async def reply(self, history, user_text, cancel):
        self.calls.append({"history": [(t.role, t.text, t.interrupted) for t in history], "user": user_text, "at": time.monotonic()})
        low = user_text.lower()
        text = (f"Телефон владельца {CANARY}." if any(w in low for w in ("владельц", "пароль", "телефон")) else "Понял, передам.")
        yield text
        await asyncio.sleep(0)


def settings(**kw) -> CallSettings:
    base = dict(answering_machine=True, answer_ring_delay_s=1, answer_greeting=GREETING, barge_in=True)
    base.update(kw)
    return CallSettings(**base)


class Rig:
    """One calls home + one loopback line + one machine. ``texts`` / ``replies`` are consumed per answered call (fresh engines each)."""

    def __init__(self, tmp_path: Path, *, texts=("Здравствуйте, это Иван", "По поводу договора, перезвоните мне", "Спасибо, до свидания"),
                 replies=("Здравствуйте. Кто вы и по какому вопросу?", "Понял. Передам.", "Хорошо, до свидания. [конец]"),
                 brain_cls=MessageBrain, tts_ms=18, cfg=None, label_resolver=None, **settings_kw):
        self.home = tmp_path / "telegram-calls"
        self.settings = settings(**settings_kw)
        self.stop_flag = False
        self.texts, self.replies, self.brain_cls, self.tts_ms = list(texts), list(replies), brain_cls, tts_ms
        self.gate: asyncio.Event | None = None            # engines "still loading" until set
        self.factory_error: Exception | None = None
        self.engines: list[Engines] = []
        self.sessions: list = []
        self.line = LoopbackLine()
        overrides = dict(greet_wait_s=0.2, speculative_ms=200, endpoint=EndpointConfig(hangover_ms=320), drain_timeout_s=10,
                         idle_prompt_s=30.0, idle_hangup_s=60.0)
        overrides.update(cfg or {})
        self.machine = AnsweringMachine(
            home=self.home, line=self.line, engines_factory=self._factory, settings_loader=lambda: self.settings,
            stop_active=lambda: self.stop_flag, mode="test", secrets=lambda: list(SECRETS), cfg_overrides=overrides,
            on_session=lambda s: self.sessions.append(s) if s is not None else None, label_resolver=label_resolver)

    async def _factory(self, settings_, mode) -> Engines:
        if self.gate is not None:
            await self.gate.wait()
        if self.factory_error is not None:
            raise self.factory_error
        eng = Engines(stt=ScriptedSTT(list(self.texts)), tts=ToneTTS(ms_per_char=self.tts_ms), brain=self.brain_cls(list(self.replies)),
                      vad=EnergyVAD(), notes={"peer": settings_.peer_user_id})
        self.engines.append(eng)
        return eng

    # ------------------------------------------------------------ helpers
    async def arm(self) -> None:
        await self.machine.arm()
        await self.until(lambda: self.machine.ready_state in ("ready", "failed", "loading"), 3)

    async def ready(self) -> None:
        await self.arm()
        assert await self.until(lambda: self.machine.ready_state == "ready", 5)

    @property
    def store(self):
        return self.machine.store

    def reports(self) -> list[dict]:
        return self.store.list(100)

    def transport(self, call):
        return self.line.transports[call.call_ref]

    @staticmethod
    async def until(cond, timeout: float = 8.0, step: float = 0.01) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            await asyncio.sleep(step)
        return False

    async def answered(self, call, timeout: float = 6.0):
        """Wait until the machine has accepted ``call`` and the session is ACTIVE; returns the session."""
        assert await self.until(lambda: call.call_ref in self.line.accepted and self.machine.active_session is not None
                                and self.machine.active_session.record.state == CallState.ACTIVE, timeout), "the call was not answered"
        return self.machine.active_session

    async def greeting_done(self, session, timeout: float = 6.0) -> None:
        assert await self.until(lambda: session.playout.frames_sent > 20 and session.phase.value == "listening", timeout), "greeting not finished"

    async def say(self, call, ms: int = 700, seed: int = 1, amp: float = 0.3) -> None:
        await self.transport(call).feed_realtime(burst(ms, RATE, seed=seed, amp=amp), RATE)

    async def hush(self, call, ms: int = 600) -> None:
        await self.transport(call).feed_realtime(quiet(ms, RATE), RATE)

    async def turn(self, call, n: int, seed: int = 1) -> None:
        """One caller utterance, then wait until the assistant has been asked ``n`` times (the brain of the LAST engines set)."""
        await self.say(call, 700, seed=seed)
        await self.hush(call, 600)
        assert await self.until(lambda: len(self.engines[-1].brain.calls) >= n, 8), f"turn {n} never reached the brain"

    async def finished(self, timeout: float = 12.0) -> None:
        assert await self.until(lambda: not self.machine.busy and self.machine.active_session is None, timeout), "the machine is still busy"
        await self.machine.wait_idle(5)

    async def close(self) -> None:
        await self.machine.shutdown()


def all_text(home: Path) -> str:
    """Everything the module wrote below ``home`` (the log, the markers, the history), for leak checks."""
    out = []
    for path in sorted(Path(home).rglob("*")):
        if path.is_file():
            out.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)
