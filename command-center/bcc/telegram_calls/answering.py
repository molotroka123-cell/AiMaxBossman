"""The answering machine: Jeff takes an incoming Telegram call when the owner does not, finds out what the caller needs, and the
owner gets a log.

Rules enforced here (each has an emulator test in ``tests/telegram_calls/test_answering_machine.py``):

* OFF by default and owner-only to enable (``CallSettings.answering_machine``, changed only through the owner-authenticated
  API / CLI; nothing a caller says can reach settings). A call that rings while the machine is off, not armed, STOPped, or while
  another call is in progress is simply NOT answered and NOT declined: the owner's phone keeps ringing as it always did.
* RING DELAY: the owner gets ``answer_ring_delay_s`` seconds to pick up himself. If he does (the line reports the call gone
  because it was answered elsewhere) Jeff never joins.
* ONE incoming call at a time; a second one is left ringing and logged with the reason ``busy``.
* Engines not ready (Whisper / Piper / local model still loading, or failed): not answered, logged as ``missed``.
* STOP (owner, any surface) declines a call that is still ringing, hangs up a call in progress, and keeps the machine from
  answering until the owner resumes. The settings are not touched.
* The caller hanging up ends the call cleanly; the log is still written. The machine NEVER places a call: there is no dial, no
  callback and no retry anywhere in this module.
* Deny-list beats allow-list; the lists are re-read from disk when the call rings AND again before answering.
* The greeting is spoken in full on every answered call (not interruptible) and says that an assistant, not the owner, answers.
* The log (``answering_store``) is scrubbed of secrets; a caller asking for the owner's private data gets a fixed refusal and
  never reaches the model (``answering_policy``).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from .answering_policy import ANSWERING_BRIEF, AnsweringBrain, decide_incoming
from .answering_store import AnsweringStore, build_report
from .call.session import CallSession, SessionConfig
from .settings import CallSettings
from .types import (CallError, CallLine, GONE_ANSWERED_ELSEWHERE, GONE_CALLER_HANGUP, IncomingCall, PeerRef, UNKNOWN_CALLER_ID)

log = logging.getLogger("bcc.telegram_calls.answering")

BUILD_TIMEOUT_S = 25.0
LABEL_TIMEOUT_S = 3.0
ANSWERING_DISCLOSURE = "Я Джефф, ИИ-ассистент владельца."      # spoken once if the greeting did not reach the caller (audit F2)
CLOSING_TEXT = "Время разговора заканчивается. Я передам ваше сообщение владельцу. До свидания."

EnginesFactory = Callable[[CallSettings, str], Awaitable[Any]]
RunSession = Callable[[CallSession], Awaitable[dict]]


def remember_call(seen: dict, call_ref: str, *, limit: int = 500, keep: int = 100) -> bool:
    """Record a call ref in an insertion-ordered dict; False when it was already there. Over ``limit`` only the newest ``keep`` stay."""
    if call_ref in seen:
        return False
    seen[call_ref] = None
    if len(seen) > limit:
        for old in list(seen)[:-keep]:
            del seen[old]
    return True


@dataclass
class _Pending:
    call: IncomingCall
    signal: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    engines_task: asyncio.Task | None = None
    label_task: asyncio.Task | None = None
    session: CallSession | None = None


class AnsweringMachine:
    def __init__(self, *, home, line: CallLine, engines_factory: EnginesFactory, settings_loader: Callable[[], CallSettings],
                 stop_active: Callable[[], bool], mode: str = "", run_session: RunSession | None = None,
                 emit: Callable[[dict], None] | None = None, secrets: Callable[[], list[str]] = lambda: [],
                 on_session: Callable[[CallSession | None], None] | None = None,
                 session_event: Callable[[Any], None] | None = None,
                 label_resolver: Callable[[int], Awaitable[str]] | None = None,
                 busy_probe: Callable[[], bool] = lambda: False, cfg_overrides: dict | None = None,
                 guard: bool = True, clock: Callable[[], float] = time.time):
        self.line, self.engines_factory, self._settings_loader = line, engines_factory, settings_loader
        self._stop_active, self.mode = stop_active, mode
        self._run_session = run_session or self._default_run
        self._emit_cb, self._secrets, self._on_session = emit, secrets, on_session
        self._busy_probe, self._cfg_overrides = busy_probe, dict(cfg_overrides or {})
        self._session_event = session_event
        self._label_resolver = label_resolver
        self._guard, self._clock = guard, clock
        self.store = AnsweringStore(home)
        self.armed = False
        self._listening = False                # the line is started once; disarm / arm only flips ``armed``
        self.ready_state = "idle"              # idle | loading | ready | failed
        self.ready_error = ""
        self.active_session: CallSession | None = None
        self._stopped = False
        self._current: _Pending | None = None
        self._pending: dict[str, _Pending] = {}
        self._seen: dict[str, None] = {}              # insertion-ordered: pruning keeps the NEWEST refs (audit F8)
        self._early_gone: dict[str, str] = {}               # the call went away before its handler had registered
        self._tasks: set[asyncio.Task] = set()
        self._prep_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.counters = {"rung": 0, "answered": 0, "missed": 0, "ignored": 0, "reports": 0}
        self.last_report_id: str | None = None

    # ================================================================== life cycle (owner actions)
    async def arm(self) -> None:
        """Start listening (the owner's action). Answering still needs the setting and ready engines at the moment a call rings."""
        self._loop = asyncio.get_running_loop()
        if not self._listening:
            self.line.set_incoming_callback(self.on_incoming)
            self.line.set_gone_callback(self.on_gone)
            await self.line.listen()
            self._listening = True
        self.armed = True
        if self._prep_task is None or self._prep_task.done():
            self._prep_task = self._loop.create_task(self._prepare(), name="answering-prepare")
        self.emit("armed", transport=self.line.name, live_tested=bool(getattr(self.line, "live_tested", False)))

    def disarm(self) -> None:
        """Stop answering new calls (the listener stays up: dropping it would need a second engine on the same client). A call
        in progress is finished by its owner, STOP or hangup, not here."""
        self.armed = False
        self.emit("disarmed")

    async def shutdown(self) -> None:
        self.armed = False
        self.stop("shutdown")
        for task in list(self._tasks) + ([self._prep_task] if self._prep_task else []):
            if task is not None and not task.done():
                task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def stop(self, reason: str = "owner_stop") -> None:
        """STOP from any surface: synchronous. Ringing calls are declined by their handler, a live call is hung up."""
        self._stopped = True
        for pend in list(self._pending.values()):
            pend.reason = "stop"
            pend.signal.set()
        session = self.active_session
        if session is not None:
            session.stop(reason)
        self.emit("stop", reason=str(reason)[:40])

    def resume(self) -> None:
        self._stopped = False

    async def wait_idle(self, timeout: float = 5.0) -> bool:
        """True when no incoming call is being handled any more (ringing or answered). Used by STOP to confirm the hangup."""
        tasks = [t for t in self._tasks if not t.done()]
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        return not pending

    @property
    def busy(self) -> bool:
        return self._current is not None or self.active_session is not None

    def status(self) -> dict:
        return {"armed": self.armed, "ready_state": self.ready_state, "ready_error": self.ready_error or None,
                "ringing": self._current is not None and self.active_session is None, "in_call": self.active_session is not None,
                "stopped": self._stopped, "transport": self.line.name, "live_tested": bool(getattr(self.line, "live_tested", False)),
                "counters": dict(self.counters), "last_report_id": self.last_report_id}

    def emit(self, kind: str, **data: Any) -> None:
        if self._emit_cb is not None:
            try:
                self._emit_cb({"event": "answering", "data": {"kind": kind, **data}})
            except Exception:  # noqa: BLE001 - an observer never breaks the machine
                pass

    # ================================================================== readiness (engines loading -> not answering)
    async def _prepare(self) -> None:
        self.ready_state, self.ready_error = "loading", ""
        try:
            settings = self._settings_loader()
            engines = await self.engines_factory(replace(settings, peer_user_id=UNKNOWN_CALLER_ID), self.mode)
            warm = getattr(engines.stt, "warmup", None)
            if callable(warm):
                await warm()
            await self._close_engines(engines)
            self.ready_state = "ready"
        except asyncio.CancelledError:
            self.ready_state = "idle"
            raise
        except CallError as exc:
            self.ready_state, self.ready_error = "failed", exc.code
        except Exception as exc:  # noqa: BLE001
            self.ready_state, self.ready_error = "failed", type(exc).__name__[:40]
        self.emit("ready" if self.ready_state == "ready" else "not_ready", error=self.ready_error or None)

    @staticmethod
    async def _close_engines(engines: Any) -> None:
        brain = getattr(engines, "brain", None)
        runtime = getattr(getattr(brain, "inner", brain), "runtime", None)
        close = getattr(runtime, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                result = close()
                if asyncio.iscoroutine(result):
                    await result

    # ================================================================== line callbacks
    def on_incoming(self, call: IncomingCall) -> None:
        """A call rings on the owner's account (any thread). Decisions are made in ``_handle``."""
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._spawn(call)
        else:
            loop.call_soon_threadsafe(self._spawn, call)

    def on_gone(self, call_ref: str, reason: str) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._gone(call_ref, reason)
        else:
            loop.call_soon_threadsafe(self._gone, call_ref, reason)

    def _gone(self, call_ref: str, reason: str) -> None:
        pend = self._pending.get(call_ref)
        if pend is not None and not pend.signal.is_set():
            pend.reason = "gone:" + (reason or "unknown")
            pend.signal.set()
        elif pend is None and call_ref in self._seen:
            self._early_gone[call_ref] = reason or "unknown"

    def _spawn(self, call: IncomingCall) -> None:
        if not remember_call(self._seen, call.call_ref):
            return
        self.counters["rung"] += 1
        task = asyncio.get_running_loop().create_task(self._handle(call), name="answering-call")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ================================================================== one incoming call
    def _settings(self) -> CallSettings | None:
        try:
            return self._settings_loader()
        except (ValueError, OSError):
            return None                                   # a corrupt file is never "enabled"

    def _report(self, call: IncomingCall, outcome: str, **kw: Any) -> dict:
        report = build_report(call=call, outcome=outcome, secrets=self._secrets(), now=self._clock(), **kw)
        try:
            self.store.save(report)
        except OSError:
            log.warning("answering report not written")
            return report
        self.last_report_id = report["id"]
        self.counters["reports"] += 1
        if report.get("answered"):
            self.counters["answered"] += 1
        elif outcome in ("missed", "busy", "not_ready", "failed"):
            self.counters["missed"] += 1
        self.emit("report", report_id=report["id"], outcome=outcome, notify=report["notify"], answered=report["answered"],
                  call_id=report.get("call_id") or None)
        return report

    async def _handle(self, call: IncomingCall) -> None:
        pend: _Pending | None = None
        engines = None
        try:
            settings = self._settings()
            if not self.armed or settings is None or not settings.answering_machine:
                self.counters["ignored"] += 1             # off / not listening: the phone keeps ringing, nothing is logged
                self.emit("skip", reason="off", known=call.known)
                return
            if self._stopped or self._stop_active():
                self.counters["ignored"] += 1
                self._report(call, "stopped", reason="STOP active: not answered")
                return
            if self._current is not None or self.active_session is not None or self._busy_probe():
                self._report(call, "busy", reason="another call is in progress; this one was left ringing")
                return
            decision = decide_incoming(settings, call)
            if not decision.answer:
                self._report(call, "denied", reason=decision.reason)
                return
            pend = self._current = _Pending(call)
            self._pending[call.call_ref] = pend
            early = self._early_gone.pop(call.call_ref, None)
            if early is not None:
                pend.reason = "gone:" + early
                pend.signal.set()
            if self._label_resolver is not None and call.known and not call.caller_label:
                pend.label_task = asyncio.get_running_loop().create_task(self._resolve_label(pend), name="answering-label")
            self.emit("ringing", known=call.known, ring_delay_s=settings.answer_ring_delay_s)
            pend.engines_task = asyncio.get_running_loop().create_task(self._build(settings, call), name="answering-engines")
            await self._wait_ring(pend, float(settings.answer_ring_delay_s))
            engines = await self._decide_to_answer(pend, settings)
            if engines is None:
                return
            await self._answer(pend, settings, engines)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let a bug leave a call hanging or the owner uninformed
            log.warning("answering handler failed: %s", type(exc).__name__)
            with contextlib.suppress(Exception):
                self._report(call, "failed", reason=f"internal error {type(exc).__name__}")
        finally:
            if pend is not None:
                self._pending.pop(call.call_ref, None)
                for task in (pend.engines_task, pend.label_task):
                    if task is not None and not task.done():
                        task.cancel()
                if self._current is pend:
                    self._current = None
            if engines is not None:
                await self._close_engines(engines)

    async def _resolve_label(self, pend: _Pending) -> None:
        """Best effort: the caller's display name for the log. A failure or a slow Telegram leaves the label empty (id only)."""
        try:
            label = await asyncio.wait_for(self._label_resolver(int(pend.call.caller_id)), LABEL_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return
        if isinstance(label, str) and label.strip():
            pend.call = replace(pend.call, caller_label=" ".join(label.split())[:120])

    async def _build(self, settings: CallSettings, call: IncomingCall) -> Any:
        peer = int(call.caller_id) if call.known else UNKNOWN_CALLER_ID
        return await asyncio.wait_for(self.engines_factory(replace(settings, peer_user_id=peer), self.mode), BUILD_TIMEOUT_S)

    async def _wait_ring(self, pend: _Pending, delay: float) -> None:
        if delay > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(pend.signal.wait(), delay)

    async def _decide_to_answer(self, pend: _Pending, first: CallSettings) -> Any | None:
        """After the ring delay: is the call still ringing, still allowed and is everything ready? Returns the engines or None
        (and the call has been logged)."""
        call = pend.call
        why = pend.reason
        if why == "stop":                                             # STOP while ringing: decline, do not answer
            await self._reject(call, "stop")
            self._report(call, "stopped", reason="STOP while ringing: call declined")
            return None
        if why.startswith("gone:"):
            reason = why.split(":", 1)[1]
            if reason == GONE_ANSWERED_ELSEWHERE:
                self._report(call, "owner_answered", reason="the owner picked up himself in time",
                             ring_delay_s=first.answer_ring_delay_s)
            else:
                self._report(call, "missed", reason="the caller hung up before the assistant answered" if reason == GONE_CALLER_HANGUP
                             else "the call stopped ringing (the engine did not say why)", ring_delay_s=first.answer_ring_delay_s)
            return None
        settings = self._settings()                                   # the owner may have changed his mind during the ring
        if settings is None or not settings.answering_machine or not self.armed:
            self._report(call, "disabled", reason="the owner switched the answering machine off while the call rang")
            return None
        if self._stopped or self._stop_active():
            await self._reject(call, "stop")
            self._report(call, "stopped", reason="STOP while ringing: call declined")
            return None
        decision = decide_incoming(settings, call)
        if not decision.answer:
            self._report(call, "denied", reason=decision.reason)
            return None
        if self.ready_state != "ready":
            self._report(call, "not_ready", reason=f"engines {self.ready_state}" + (f" ({self.ready_error})" if self.ready_error else ""))
            return None
        try:
            return await asyncio.wait_for(pend.engines_task, BUILD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            code = exc.code if isinstance(exc, CallError) else type(exc).__name__[:40]
            if code == "PEER_NOT_ALLOWED":                            # the owner's own block-list (Jeff's blocked ids)
                self._report(call, "denied", reason="blocked by the owner's block-list")
                return None
            self._report(call, "not_ready", reason=f"engines could not be built ({code})")
            return None

    async def _reject(self, call: IncomingCall, reason: str) -> None:
        try:
            await self.line.reject(call, reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("decline failed: %s", type(exc).__name__)

    def _session_cfg(self, settings: CallSettings) -> SessionConfig:
        base = dict(max_call_s=float(settings.answer_max_call_s), greeting=settings.answer_greeting, greet_wait_s=0.5,
                    greet_always=True, greeting_uninterruptible=True, keep_transcript=True, idle_prompt_s=10.0,
                    idle_hangup_s=25.0, barge_in=settings.barge_in, echo_mode=settings.echo_mode, answer_timeout_s=20.0,
                    closing_text=CLOSING_TEXT, closing_lead_s=8.0,
                    disclosure=ANSWERING_DISCLOSURE)
        base.update(self._cfg_overrides)
        return SessionConfig(**base)

    async def _answer(self, pend: _Pending, settings: CallSettings, engines: Any) -> None:
        call = pend.call                                  # carries the resolved display name when Telegram gave it in time
        brain = AnsweringBrain(engines.brain, guard=self._guard)
        transport = self.line.new_transport(call)
        peer = PeerRef(int(call.caller_id) if call.known else UNKNOWN_CALLER_ID, call.caller_label[:120])
        session = CallSession(call_id="c-" + uuid.uuid4().hex[:12], transport=transport, peer=peer, stt=engines.stt,
                              tts=engines.tts, brain=brain, vad=engines.vad, cfg=self._session_cfg(settings),
                              on_event=self._session_event, recorder=None, incoming=call)
        pend.session = session
        self.active_session = session
        if self._on_session is not None:
            self._on_session(session)
        if self._stopped or self._stop_active():                      # STOP raced the build: the call is never accepted
            session.stop("owner_stop")
        self.emit("answering", known=call.known)
        data: dict = {}
        try:
            data = await self._run_session(session)
        finally:
            self.active_session = None
            if self._on_session is not None:
                self._on_session(None)
        rec = data or session.record.as_dict()
        outcome, reason = self._classify(session, rec, brain)
        summary = session.record.summary                              # in memory: the history copy of it is text-free
        report = self._report(call, outcome, reason=reason, record=rec, transcript=session.transcript,
                              summary_text=(summary.text if summary is not None else "") if outcome in ("message_taken", "ended_early") else "",
                              flags=brain.flags, answered=session.connected, ring_delay_s=settings.answer_ring_delay_s,
                              received_at=call.received_at)

    def _classify(self, session: CallSession, rec: dict, brain: AnsweringBrain) -> tuple[str, str]:
        code, call_outcome = rec.get("error_code") or "", rec.get("outcome") or ""
        callers = sum(1 for t in session.transcript if t.get("role") == "user")
        if not session.connected:
            if call_outcome == "stopped":
                return "stopped", "STOP before the call was answered"
            if code == "CALL_DISCARDED":
                return "missed", "the caller hung up while the assistant was answering"
            return "failed", f"could not answer ({code or call_outcome or 'unknown'})"
        if call_outcome == "stopped":
            return "stopped", "STOP during the call: hung up"
        if session.interrupted_by_hangup:
            return "ended_early", "the caller hung up mid-sentence"
        if callers == 0:
            return "no_message", "the caller said nothing"
        if call_outcome == "max_duration":
            return "message_taken", "the maximum call duration was reached"
        return "message_taken", ""

    @staticmethod
    async def _default_run(session: CallSession) -> dict:
        record = await session.run()
        return record.as_dict()
