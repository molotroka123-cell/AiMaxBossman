"""One call, end to end: dial -> converse -> end, with barge-in, echo guard, timeouts and STOP.

The session owns NO models and NO Telegram code: it is wired to a ``CallTransport``, an ``STTEngine``,
a ``TTSEngine`` and a ``Brain`` (all Protocols from ``types``), so the same code runs against the real
py-tgcalls transport and against the loopback used for offline tests.

Rules that are enforced here (each has a test):
* ``transport.dial`` is called exactly once; nothing in this module retries or redials;
* an outcome is decided exactly once; anything unprovable is ``UNKNOWN``;
* STOP is synchronous at the audio level (playout invalidated before any ``await``), starts no new model
  call, and the summary is mechanical after STOP;
* while we speak (or right after), incoming audio is *gated*: only a confirmed barge-in (long enough, not our
  own echo) opens an utterance, so we never transcribe and answer ourselves;
* what the interlocutor says is data: this module has no way to change permissions, settings or targets.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..audio.echo import EchoGuard, is_text_echo
from ..audio.endpointer import EndpointConfig, Endpointer
from ..audio.pcm import FrameSlicer, StreamResampler, rms
from ..audio.playout import Playout
from ..audio.vad import VAD, WINDOW_BYTES, WINDOW_MS
from ..speech.text import SentenceChunker
from ..types import (ANALYSIS_RATE, Brain, CallError, CallEvent, CallRecord, CallState, CallSummary,
                     CallTransport, CancelToken, Outcome, PeerRef, Phase, STTEngine, STTStream,
                     TransportEvent, TransportEventKind, TTSEngine, Turn, TurnMetrics)

_WORD = re.compile(r"\w", re.UNICODE)


@dataclass
class SessionConfig:
    ring_timeout_s: float = 45.0
    max_call_s: float = 900.0
    greeting: str = "Привет! Это Джефф, ИИ-ассистент. Ты меня слышишь?"
    greet_wait_s: float = 1.5             # let the callee say «алло» first
    idle_prompt_s: float = 25.0
    idle_prompt_text: str = "Ты ещё здесь?"
    idle_hangup_s: float = 60.0
    reconnect_grace_s: float = 8.0
    barge_in: bool = True
    barge_min_ms: int = 240
    echo_mode: str = "guard"              # "guard" | "half_duplex"
    half_duplex_barge_ms: int = 700
    echo_tail_ms: int = 400
    speculative_ms: int = 200
    repeat_prompt_text: str = "Не расслышал. Повтори, пожалуйста."
    apology_text: str = "Секунду, у меня заминка. Повтори, пожалуйста."
    stt_empty_repeat_after: int = 2
    stt_timeout_s: float = 15.0
    max_consecutive_failures: int = 3
    hangup_timeout_s: float = 3.0
    summary_timeout_s: float = 25.0
    drain_timeout_s: float = 120.0
    pace: float = 1.0                     # 1.0 = real time; tests may use 0 (no pacing)
    history_chars: int = 6000
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)


class CallSession:
    def __init__(self, *, call_id: str, transport: CallTransport, peer: PeerRef, stt: STTEngine, tts: TTSEngine,
                 brain: Brain, vad: VAD, cfg: SessionConfig | None = None,
                 on_event: Callable[[CallEvent], None] | None = None, recorder: Any = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.call_id, self.transport, self.peer = call_id, transport, peer
        self.stt, self.tts, self.brain, self.vad = stt, tts, brain, vad
        self.cfg = cfg or SessionConfig()
        self._on_event, self.recorder, self._clock, self._wall = on_event, recorder, clock, wall

        self.record = CallRecord(call_id=call_id, transport=transport.name, peer_user_id=peer.user_id,
                                 started_at=wall(), recorded_audio=recorder is not None,
                                 models={"stt": f"{stt.name}:{stt.model}", "llm": f"{brain.route}:{brain.model}",
                                         "tts": f"{tts.name}:{tts.voice}"})
        for key in ("barge_ins", "echo_blocked", "echo_text_suppressed", "stt_empty", "stt_errors",
                    "brain_errors", "tts_errors", "rx_dropped", "hangup_confirmed", "playout_underruns"):
            self.record.counters[key] = 0
        self.events: deque[CallEvent] = deque(maxlen=1000)
        self._seq = 0

        self.phase = Phase.LISTENING
        self._outcome: Outcome | None = None
        self._error_code: str | None = None
        self._done = asyncio.Event()
        self._teardown_done = asyncio.Event()
        self._stopping = False
        self._active_since: float | None = None

        fmt = transport.audio_format
        self.playout = Playout(self._send_frame, transport.clear_outgoing, sample_rate=fmt.sample_rate,
                               frame_ms=transport.frame_ms, pace=self.cfg.pace, clock=clock)
        self.playout.on_first_frame = self._on_first_frame
        self.playout.on_marker_started = self._on_marker_started
        self.playout.on_frame_sent = self._on_frame_sent
        self.echo = EchoGuard()
        self.ep = Endpointer(vad, self.cfg.endpoint)
        self._rx_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=400)
        self._recent: deque[bytes] = deque(maxlen=80)          # last ~2.5 s of 16 kHz windows (barge-in seed)
        self._rs_out: StreamResampler | None = None
        self._tasks: list[asyncio.Task] = []
        self._turn_task: asyncio.Task | None = None
        self._stage: str | None = None                         # "stt" | "respond" | None
        self._cancel = CancelToken()
        self._history: list[Turn] = []
        self._pending_user: list[str] = []
        self._utt_open = False
        self._stt_stream: STTStream | None = None
        self._spec_task: asyncio.Task | None = None
        self._spec_started = False
        self._user_spoke = asyncio.Event()
        self._last_user_speech = 0.0
        self._last_tx_at = -1e9
        self._prompted_idle = False
        self._disconnected_at: float | None = None
        self._turn_no = 0
        self._consecutive_empty = 0
        self._consecutive_failures = 0
        self._gen_metrics: dict[int, TurnMetrics] = {}
        self._started_markers: set[int] = set()
        self._recent_spoken: deque[tuple[float, str]] = deque(maxlen=6)
        self._echo_block_run = 0
        self._end_call_after_speech = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._used = False

    # ================================================================== public
    @property
    def outcome(self) -> Outcome | None:
        return self._outcome

    async def run(self) -> CallRecord:
        if self._used:                                # one CallSession = at most one dial, ever
            return self.record
        self._used = True
        self._loop = asyncio.get_running_loop()
        self._brain_hook("bind_call", self.call_id)
        self.transport.set_audio_callback(self._on_transport_audio)
        self.transport.set_event_callback(self._on_transport_event)
        try:
            self._set_state(CallState.DIALING)
            await self._connect()
            if self._outcome is None:
                await self._converse()
        except asyncio.CancelledError:
            self._finish(Outcome.UNKNOWN, "INTERNAL")
            raise
        except Exception as exc:  # noqa: BLE001 - never let a bug leave a call hanging
            self.emit("error", code="INTERNAL", detail=type(exc).__name__)
            self._finish(Outcome.UNKNOWN, "INTERNAL")
        finally:
            await self._teardown()
        return self.record

    def stop(self, reason: str = "owner_stop") -> None:
        """STOP from any surface. Synchronous part: silence + no new generation. Idempotent."""
        if self._stopping:
            return
        self._stopping = True
        self._cancel.cancel(reason)
        self.playout.invalidate()
        if self._stt_stream is not None:
            self._stt_stream.cancel()
        self.emit("stop", reason=reason)
        self._finish(Outcome.STOPPED)

    async def stop_and_wait(self, reason: str = "owner_stop", timeout: float = 5.0) -> bool:
        self.stop(reason)
        try:
            await asyncio.wait_for(self._teardown_done.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def hangup(self, reason: str = "owner_hangup") -> None:
        """Owner ends the call normally (not an emergency): same teardown, outcome COMPLETED."""
        self._cancel.cancel(reason)
        self.playout.invalidate()
        self._finish(Outcome.COMPLETED)

    def emit(self, kind: str, **data: Any) -> CallEvent:
        self._seq += 1
        ev = CallEvent(self._seq, kind, self._wall(), data)
        self.events.append(ev)
        if self._on_event is not None:
            try:
                self._on_event(ev)
            except Exception:  # noqa: BLE001 - observers never break the call
                pass
        return ev

    # ================================================================== connect / converse / teardown
    async def _connect(self) -> None:
        if self._outcome is not None:                # STOP / hangup before anything was requested: nothing is dialled
            return
        try:
            await self.transport.start()
        except CallError as exc:                     # nothing was requested yet: a provable failure
            self.emit("error", code=exc.code)
            self._finish(Outcome.FAILED, exc.code)
            return
        except Exception as exc:  # noqa: BLE001
            self.emit("error", code="INTERNAL", detail=type(exc).__name__)
            self._finish(Outcome.FAILED, "INTERNAL")
            return
        if self._outcome is not None:                # STOP raced the transport start: the phone must not ring
            return
        try:
            await self._dial_until_done()            # exactly once, never retried; STOP / hangup interrupt the ringing
        except CallError as exc:
            mapping = {"CALL_DECLINED": Outcome.DECLINED, "CALL_BUSY": Outcome.BUSY, "CALL_NO_ANSWER": Outcome.NO_ANSWER,
                       "PEER_PRIVACY": Outcome.FAILED, "DEPENDENCIES_MISSING": Outcome.FAILED, "NOT_LOGGED_IN": Outcome.FAILED}
            outcome = mapping.get(exc.code, Outcome.UNKNOWN)     # anything else: we may have rung the phone
            self.emit("error", code=exc.code)
            self._finish(outcome, exc.code)
            return
        except Exception as exc:  # noqa: BLE001
            self.emit("error", code="INTERNAL", detail=type(exc).__name__)
            self._finish(Outcome.UNKNOWN, "INTERNAL")
            return
        if self._outcome is not None:                # STOP / peer hangup raced the connect
            return
        self._active_since = self._clock()
        self._set_state(CallState.ACTIVE)

    async def _dial_until_done(self) -> None:
        """``transport.dial`` raced against the end of the call: a STOP or hangup while the phone rings cancels the dial, and the
        teardown then hangs up through the transport (a terminated worker could never leave the call)."""
        loop = asyncio.get_running_loop()
        dial = loop.create_task(self.transport.dial(self.peer, ring_timeout=self.cfg.ring_timeout_s), name="calls-dial")
        ended = loop.create_task(self._done.wait(), name="calls-dial-ended")
        try:
            await asyncio.wait({dial, ended}, return_when=asyncio.FIRST_COMPLETED)
            if not dial.done():
                dial.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await dial
                return
            dial.result()                            # re-raises the dial's own CallError for the caller's mapping
        finally:
            for task in (dial, ended):
                if not task.done():
                    task.cancel()

    async def _converse(self) -> None:
        self.playout.start()
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._rx_loop(), name="calls-rx"),
                       loop.create_task(self._watchdog(), name="calls-watchdog"),
                       loop.create_task(self._greet(), name="calls-greet")]
        await self._done.wait()

    async def _teardown(self) -> None:
        try:
            self._cancel.cancel("teardown")
            self.playout.invalidate()
            if self._stt_stream is not None:
                self._stt_stream.cancel()
            if self._outcome is None:
                self._outcome = Outcome.UNKNOWN
            if self.record.state not in (CallState.ENDED,):
                self._set_state(CallState.ENDING)
            for task in [self._turn_task, self._spec_task, *self._tasks]:
                if task is not None and not task.done() and task is not asyncio.current_task():
                    task.cancel()
            for task in [self._turn_task, self._spec_task, *self._tasks]:
                if task is not None and task is not asyncio.current_task():
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            await self.playout.close()
            try:
                await asyncio.wait_for(self.transport.hangup("local"), self.cfg.hangup_timeout_s)
                self.record.counters["hangup_confirmed"] = 1
            except Exception:  # noqa: BLE001 - reported, never raised; UI shows "hangup unconfirmed"
                self.emit("error", code="CONNECTION_LOST", detail="hangup_unconfirmed")
            try:
                await asyncio.wait_for(self.transport.close(), self.cfg.hangup_timeout_s)
            except Exception:  # noqa: BLE001
                pass
            self.record.counters["playout_underruns"] = self.playout.underruns
            if self.recorder is not None:
                try:
                    self.recorder.close()
                except Exception:  # noqa: BLE001
                    pass
            self.record.ended_at = self._wall()
            self.record.outcome = self._outcome
            self.record.error_code = self._error_code
            self.record.summary = await self._summary()
            self._history.clear()                      # transcript is not kept
            self._pending_user.clear()
            self._set_state(CallState.ENDED)
            self.emit("ended", outcome=self._outcome.value, error_code=self._error_code,
                      latency_ms=self.record.as_dict()["latency_ms"])
        finally:
            self._teardown_done.set()

    def _brain_hook(self, name: str, *args) -> None:
        """Optional brain callbacks (``bind_call`` / ``turn_finished``): a brain without them (scripted) is fine, one that fails
        in them must never take the call down."""
        hook = getattr(self.brain, name, None)
        if callable(hook):
            try:
                hook(*args)
            except Exception:  # noqa: BLE001
                self.emit("error", code="INTERNAL", detail=f"brain_hook_{name}")

    async def _summary(self) -> CallSummary:
        turns = list(self._history)
        user_turns = sum(1 for t in turns if t.role == "user")
        dur = (self.record.ended_at or self._wall()) - self.record.started_at
        mechanical = CallSummary(
            text=(f"Звонок ассистента на выбранный аккаунт: {user_turns} реплик собеседника, {dur:.0f} с, "
                  f"исход: {self._outcome.value if self._outcome else 'unknown'}. Содержание не сохранялось."),
            agreed_tasks=[], generated_by="mechanical")
        if user_turns == 0 or self._outcome in (Outcome.STOPPED,):      # STOP starts no new model call
            return mechanical
        try:
            result = await asyncio.wait_for(self.brain.summarize(turns), self.cfg.summary_timeout_s)
            return result if result and result.text else mechanical
        except Exception:  # noqa: BLE001
            return mechanical

    # ================================================================== state helpers
    def _set_state(self, state: CallState) -> None:
        if self.record.state != state:
            self.record.state = state
            self.emit("state", state=state.value)

    def _set_phase(self, phase: Phase) -> None:
        if self.phase != phase:
            self.phase = phase
            self.emit("phase", phase=phase.value)

    def _finish(self, outcome: Outcome, error_code: str | None = None) -> None:
        if self._outcome is not None:
            return
        self._outcome, self._error_code = outcome, error_code
        self._done.set()

    # ================================================================== transport callbacks
    def _on_transport_audio(self, pcm: bytes) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._enqueue_rx(pcm)
        else:
            loop.call_soon_threadsafe(self._enqueue_rx, pcm)

    def _enqueue_rx(self, pcm: bytes) -> None:
        if self._rx_q.full():
            try:
                self._rx_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.record.counters["rx_dropped"] += 1
        self._rx_q.put_nowait(pcm)

    def _on_transport_event(self, ev: TransportEvent) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._handle_transport_event(ev)
        else:
            loop.call_soon_threadsafe(self._handle_transport_event, ev)

    def _handle_transport_event(self, ev: TransportEvent) -> None:
        if ev.kind == TransportEventKind.RINGING:
            if self.record.state == CallState.DIALING:
                self._set_state(CallState.RINGING)
        elif ev.kind == TransportEventKind.DISCONNECTED:
            self._disconnected_at = self._clock()
            self.emit("log", msg="media_disconnected")
        elif ev.kind == TransportEventKind.RECONNECTED:
            self._disconnected_at = None
            self.emit("log", msg="media_reconnected")
        elif ev.kind == TransportEventKind.ENDED:
            if self._outcome is None:
                if ev.reason in ("peer_hangup", "local_hangup"):
                    self._finish(Outcome.COMPLETED)
                else:
                    self._finish(Outcome.CONNECTION_LOST, "CONNECTION_LOST")

    async def _send_frame(self, frame: bytes) -> None:
        if self.recorder is not None:
            self.recorder.write_local(frame, self.transport.audio_format.sample_rate)
        await self.transport.send_audio(frame)

    # ================================================================== playout observers
    def _on_first_frame(self, gen: int, at: float) -> None:
        m = self._gen_metrics.get(gen)
        if m is not None and m.t_first_frame_sent is None:
            m.t_first_frame_sent = at
            self.emit("metric", turn=m.turn_id, response_latency_ms=m.response_latency_ms)
        if not self._stopping:
            self._set_phase(Phase.SPEAKING)

    def _on_marker_started(self, gen: int, marker: int) -> None:
        if gen == self.playout.generation:
            self._started_markers.add(marker)

    def _on_frame_sent(self, frame: bytes, at: float) -> None:
        self._last_tx_at = at
        self.echo.tx(rms(frame), at)

    # ================================================================== receive path
    async def _rx_loop(self) -> None:
        rs = StreamResampler(self.transport.rx_sample_rate, ANALYSIS_RATE, quality="MQ")
        slicer = FrameSlicer(WINDOW_BYTES)
        update_at = 0.0
        while True:
            chunk = await self._rx_q.get()
            if chunk is None:
                return
            pcm16 = rs.process(chunk)
            if self.recorder is not None:
                self.recorder.write_remote(pcm16)
            for window in slicer.push(pcm16):
                self._on_window(window)
            now = self._clock()
            if self.phase == Phase.SPEAKING and now >= update_at:
                update_at = now + 0.5
                self.echo.update()

    def _gated(self, now: float) -> bool:
        if self._utt_open:
            return False
        if self.phase in (Phase.THINKING, Phase.SPEAKING):
            return True
        return (now - self._last_tx_at) * 1000.0 < self.cfg.echo_tail_ms

    def _on_window(self, window: bytes) -> None:
        now = self._clock()
        level = rms(window)
        self._recent.append(window)
        self.echo.rx(level, now)
        events = self.ep.push(window)
        if self.ep.last_prob >= self.cfg.endpoint.on_threshold:
            self._last_user_speech = now
            self._prompted_idle = False
        if self._gated(now):
            self.ep.abandon()                            # our own echo must not define where the next utterance ends
            if self.cfg.barge_in and self._barge_in_wanted(now):
                self._barge_in()
                # the window that confirmed the barge-in is already in the seed; later windows flow normally
            return
        self._echo_block_run = 0
        for e in events:
            self._on_ep_event(e, now)
        if self._utt_open and self.ep.in_speech and not self._spec_started and self.ep.silence_ms >= self.cfg.speculative_ms:
            self._spec_started = True
            stream = self._stt_stream
            if stream is not None:
                self._spec_task = asyncio.get_running_loop().create_task(self._speculate(stream))

    async def _speculate(self, stream: STTStream) -> None:
        try:
            await stream.partial()
        except Exception:  # noqa: BLE001 - speculation is only a speed-up
            pass

    def _barge_in_wanted(self, now: float) -> bool:
        run = self.ep.run
        if run.windows == 0:
            self._echo_block_run = 0
            return False
        if self.cfg.echo_mode == "half_duplex":
            return run.ms >= self.cfg.half_duplex_barge_ms and run.mean_level >= self.echo.strict_level
        if run.ms < self.cfg.barge_min_ms:
            return False
        # judge only the most recent stretch: an echo-only run can already be seconds long when the person cuts in
        verdict = self.echo.assess(min(run.windows, self._confirm_windows()))
        if verdict.real_speech:
            return True
        if self._echo_block_run != run.windows and run.windows % 8 == 0:      # count a blocked run, not every window
            self.record.counters["echo_blocked"] += 1
            self._echo_block_run = run.windows
            self.emit("echo", reason=verdict.reason, run_ms=run.ms)
        return False

    def _confirm_windows(self) -> int:
        return max(1, -(-self.cfg.barge_min_ms // WINDOW_MS))

    def _barge_in(self) -> None:
        windows = min(self.ep.run.windows, self._confirm_windows())
        was = self.phase
        self.record.counters["barge_ins"] += 1
        self.emit("barge_in", phase=was.value, confirm_ms=windows * WINDOW_MS)
        # 1) audible stop first, synchronously
        self._cancel.cancel("barge_in")
        self.playout.invalidate()
        asyncio.get_running_loop().create_task(self._clear_transport())
        # 2) drop the response in flight (not a transcription that is still being finalised)
        for m in self._gen_metrics.values():
            if m.t_first_frame_sent is None or m.turn_id == self._turn_no:
                m.interrupted = True
        if self._stage == "respond" and self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        self._set_phase(Phase.LISTENING)
        # 3) the interruption is the start of the next utterance: seed it with what we already heard
        seed_windows = list(self._recent)[-(windows + self.cfg.endpoint.preroll_ms // WINDOW_MS):]
        self.ep.resume(windows)
        self._open_utterance(b"".join(seed_windows))

    async def _clear_transport(self) -> None:
        try:
            await self.transport.clear_outgoing()
        except Exception:  # noqa: BLE001
            pass

    def _on_ep_event(self, e, now: float) -> None:
        if e.kind == "start":
            if not self._utt_open:
                self._open_utterance(e.pcm)
        elif e.kind == "audio":
            if self._utt_open and self._stt_stream is not None:
                self._stt_stream.feed(e.pcm)
        elif e.kind == "end":
            if self._utt_open:
                self._close_utterance(now, forced=e.forced)
        elif e.kind == "discard":
            if self._stt_stream is not None:
                self._stt_stream.cancel()
            self._stt_stream, self._utt_open = None, False

    def _open_utterance(self, initial: bytes) -> None:
        if self._stt_stream is not None:
            self._stt_stream.cancel()
        try:
            self._stt_stream = self.stt.new_stream()
        except Exception:  # noqa: BLE001
            self.record.counters["stt_errors"] += 1
            self.emit("error", code="STT_UNAVAILABLE")
            self._stt_stream, self._utt_open = None, False
            self._register_failure("STT_UNAVAILABLE")
            return
        self._stt_stream.feed(initial)
        self._utt_open, self._spec_started = True, False
        self._user_spoke.set()
        self._last_user_speech = self._clock()
        self.emit("speech_start")

    def _close_utterance(self, now: float, *, forced: bool) -> None:
        stream, self._stt_stream, self._utt_open = self._stt_stream, None, False
        if stream is None:
            return
        hangover = 0.0 if forced else self.cfg.endpoint.hangover_ms / 1000.0
        t_end = now - hangover                       # when the person actually stopped, not when we were sure
        loop = asyncio.get_running_loop()
        if self._turn_task is not None and not self._turn_task.done() and self._stage == "respond":
            self._turn_task.cancel()
        self._turn_task = loop.create_task(self._turn(stream, t_end), name="calls-turn")

    # ================================================================== a turn
    async def _turn(self, stream: STTStream, t_end: float) -> None:
        self._turn_no += 1
        m = TurnMetrics(turn_id=self._turn_no, t_speech_end=t_end)
        self._stage = "stt"
        self._set_phase(Phase.THINKING)
        try:
            try:
                res = await asyncio.wait_for(stream.finalize(), self.cfg.stt_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.record.counters["stt_errors"] += 1
                self.emit("error", code="STT_UNAVAILABLE")
                self._register_failure("STT_UNAVAILABLE")
                self._set_phase(Phase.LISTENING)
                return
            m.t_stt_final, m.stt_ms = self._clock(), res.decode_ms
            text = (res.text or "").strip()
            if not text or not _WORD.search(text):
                self.record.counters["stt_empty"] += 1
                self._consecutive_empty += 1
                self._set_phase(Phase.LISTENING)
                if self._consecutive_empty >= self.cfg.stt_empty_repeat_after:
                    self._consecutive_empty = 0
                    await self._say_canned(self.cfg.repeat_prompt_text)
                return
            self._consecutive_empty = 0
            self._consecutive_failures = 0
            if self._recent_echo_window() and is_text_echo(text, [s for _, s in self._recent_spoken]):
                self.record.counters["echo_text_suppressed"] += 1
                self.emit("echo", reason="text", chars=len(text))
                self._set_phase(Phase.LISTENING)
                return
            if self._utt_open or self.ep.in_speech:            # the person is still talking: keep their words, wait
                self._pending_user.append(text)
                self._set_phase(Phase.LISTENING)
                return
            if self._pending_user:
                text = " ".join([*self._pending_user, text])
                self._pending_user.clear()
            self._history.append(Turn("user", text))
            m.user_chars = len(text)
            self.record.turns.append(m)
            self.emit("turn", turn=m.turn_id, user_chars=m.user_chars, stt_ms=m.stt_ms)
            self._stage = "respond"
            await self._respond(text, m)
        finally:
            if self._stage in ("stt", "respond") and asyncio.current_task() is self._turn_task:
                self._stage = None

    def _recent_echo_window(self) -> bool:
        return (self._clock() - self._last_tx_at) < 3.0 or self.phase == Phase.SPEAKING

    async def _respond(self, text: str, m: TurnMetrics) -> None:
        cancel = self._cancel = CancelToken()
        if self._stopping:
            return
        gen = self.playout.generation
        self._gen_metrics[gen] = m
        self._started_markers = set()
        self._rs_out = None
        chunker = SentenceChunker()
        spoken: list[str] = []
        idx = 0
        history = self._history_for_llm(exclude_pending_user=True)
        try:
            async for delta in self.brain.reply(history, text, cancel):
                if cancel.cancelled or self._stopping:
                    break
                if m.t_llm_first_token is None and delta.strip():
                    m.t_llm_first_token = self._clock()
                for sentence in chunker.feed(delta):
                    spoken.append(sentence)
                    await self._speak(sentence, gen, idx, cancel, m)
                    idx += 1
            if not cancel.cancelled and not self._stopping:
                for sentence in chunker.flush():
                    spoken.append(sentence)
                    await self._speak(sentence, gen, idx, cancel, m)
                    idx += 1
            self._consecutive_failures = 0
            if not cancel.cancelled and not self._stopping:
                self.playout.end(gen)
                await self.playout.wait_drained(gen, self.cfg.drain_timeout_s)
                self._end_call_after_speech = chunker.end_call
        except asyncio.CancelledError:
            cancel.cancel("cancelled")
            raise
        except CallError as exc:
            self.record.counters["brain_errors" if exc.code.startswith("BRAIN") else "tts_errors"] += 1
            self.emit("error", code=exc.code)
            self._register_failure(exc.code)
            if not self._stopping and self._outcome is None:
                self.playout.end(gen)
                await self._say_canned(self.cfg.apology_text)
        except Exception as exc:  # noqa: BLE001
            self.record.counters["brain_errors"] += 1
            self.emit("error", code="BRAIN_UNAVAILABLE", detail=type(exc).__name__)
            self._register_failure("BRAIN_UNAVAILABLE")
            if not self._stopping and self._outcome is None:
                self.playout.end(gen)
                await self._say_canned(self.cfg.apology_text)
        finally:
            said = [s for i, s in enumerate(spoken) if i in self._started_markers]
            interrupted = cancel.cancelled or len(said) < len(spoken)
            if said:
                self._history.append(Turn("assistant", " ".join(said), interrupted=interrupted))
                now = self._clock()
                for s in said:
                    self._recent_spoken.append((now, s))
                m.assistant_chars = sum(len(s) for s in said)
            m.interrupted = m.interrupted or interrupted
            self._brain_hook("turn_finished", bool(said))      # Jeff learns from the peer's words only if something was spoken
            if not self._stopping and self._outcome is None and self.phase != Phase.LISTENING and not self._utt_open:
                self._set_phase(Phase.LISTENING)
        if self._end_call_after_speech and not self._stopping and not cancel.cancelled:
            self._finish(Outcome.COMPLETED)

    async def _speak(self, sentence: str, gen: int, marker: int, cancel: CancelToken, m: TurnMetrics | None) -> None:
        if self._rs_out is None and self.tts.sample_rate != self.transport.audio_format.sample_rate:
            self._rs_out = StreamResampler(self.tts.sample_rate, self.transport.audio_format.sample_rate)
        first = True
        try:
            async for chunk in self.tts.synthesize(sentence, cancel):
                if cancel.cancelled or gen != self.playout.generation:
                    return
                if m is not None and m.t_tts_first_audio is None:
                    m.t_tts_first_audio = self._clock()
                pcm = self._rs_out.process(chunk) if self._rs_out is not None else chunk
                self.playout.enqueue(pcm, gen, marker if first else None)
                first = False
        except asyncio.CancelledError:
            raise
        except CallError:
            raise
        except Exception:  # noqa: BLE001
            raise CallError("TTS_UNAVAILABLE") from None

    async def _say_canned(self, text: str) -> None:
        """Speak a fixed phrase (greeting, prompts, apology) without the LLM."""
        if self._stopping or self._outcome is not None:
            return
        cancel = self._cancel = CancelToken()
        gen = self.playout.generation
        try:
            await self._speak(text, gen, 0, cancel, None)
            self.playout.end(gen)
            await self.playout.wait_drained(gen, self.cfg.drain_timeout_s)
        except CallError as exc:
            self.record.counters["tts_errors"] += 1
            self.emit("error", code=exc.code)
            self._register_failure(exc.code)
        finally:
            self._recent_spoken.append((self._clock(), text))
            if not self._stopping and not self._utt_open and self.phase != Phase.LISTENING:
                self._set_phase(Phase.LISTENING)

    def _history_for_llm(self, *, exclude_pending_user: bool) -> list[Turn]:
        """Alternating roles (several chat templates reject anything else), bounded size."""
        turns = list(self._history)
        if exclude_pending_user and turns and turns[-1].role == "user":
            turns = turns[:-1]
        merged: list[Turn] = []
        for t in turns:
            if not t.text.strip():
                continue
            if merged and merged[-1].role == t.role:
                merged[-1] = Turn(t.role, merged[-1].text + " " + t.text, merged[-1].interrupted or t.interrupted)
            else:
                merged.append(Turn(t.role, t.text, t.interrupted))
        out: list[Turn] = []
        budget = self.cfg.history_chars
        for t in reversed(merged):
            if budget - len(t.text) < 0 and out:
                break
            out.insert(0, t)
            budget -= len(t.text)
        while out and out[0].role != "user":
            out.pop(0)
        return out

    def _register_failure(self, code: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.cfg.max_consecutive_failures:
            self._finish(Outcome.FAILED, code)

    # ================================================================== greeting / watchdog
    async def _greet(self) -> None:
        try:
            await asyncio.wait_for(self._user_spoke.wait(), self.cfg.greet_wait_s)
            return                                     # the callee spoke first («алло»): just listen and answer
        except asyncio.TimeoutError:
            pass
        if self.cfg.greeting and not self._stopping and self._outcome is None:
            m = TurnMetrics(turn_id=0)
            self._gen_metrics[self.playout.generation] = m
            await self._say_canned(self.cfg.greeting)

    async def _watchdog(self) -> None:
        while not self._done.is_set():
            await asyncio.sleep(0.1 if self.cfg.pace else 0.02)
            now = self._clock()
            if self._active_since is not None and now - self._active_since >= self.cfg.max_call_s:
                self._finish(Outcome.MAX_DURATION, "MAX_DURATION")
                return
            if self._disconnected_at is not None and now - self._disconnected_at >= self.cfg.reconnect_grace_s:
                self._finish(Outcome.CONNECTION_LOST, "CONNECTION_LOST")
                return
            if self.phase == Phase.LISTENING and not self._utt_open and self._turn_task is not None and not self._turn_task.done():
                continue
            quiet_since = max(self._last_user_speech, self._last_tx_at, self._active_since or 0.0)
            if self.phase == Phase.LISTENING and not self._utt_open and self._active_since is not None:
                silent_for = now - quiet_since
                if silent_for >= self.cfg.idle_hangup_s:
                    self._finish(Outcome.SILENCE_TIMEOUT, "SILENCE_TIMEOUT")
                    return
                if silent_for >= self.cfg.idle_prompt_s and not self._prompted_idle:
                    self._prompted_idle = True
                    asyncio.get_running_loop().create_task(self._say_canned(self.cfg.idle_prompt_text))
