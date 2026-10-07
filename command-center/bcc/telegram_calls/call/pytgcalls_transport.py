"""Real private-call transport: py-tgcalls 3.0.0 (+ ntgcalls 3.0.0) over the Telethon user session.

Verified facts this module is built on (source reading + a native two-instance media loopback, see docs/telegram-calls/OSS.md):
* positive ``chat_id`` = P2P call; ``play(user_id, MediaStream(ExternalMedia.AUDIO, AudioParameters(48000, 1)), CallConfig(timeout))``
  places the call and returns when media is connected; ``CallConfig.timeout`` only bounds the wait for an ANSWER, so the
  outer ``asyncio.wait_for`` here bounds the rest;
* ``send_frame`` needs ``bytes`` of EXACTLY 10 ms (960 B at 48 kHz mono): ntgcalls consumes only the first frame of a longer
  buffer and over-reads a shorter one; there is no pacing or back-pressure and pushed audio cannot be recalled, so the caller
  (``Playout``) paces on a wall-clock deadline and keeps at most about one frame in flight; the rate must be a multiple of 100 Hz;
* incoming audio: ``record(user_id, RecordStream(audio=True, audio_parameters=AudioParameters(16000, 1)))`` makes ntgcalls
  deliver 10 ms frames (320 B) as ``StreamFrames`` updates (``Direction.INCOMING``, ``Device.MICROPHONE``), one callback per frame;
  frames keep coming as near-silence after the call died, so the end of a call is taken from ``ChatUpdate``, never from silence;
* py-tgcalls hangs up a P2P call by itself when the media connection drops (``_handle_connection_changed`` -> ``discard_call``);
  we wrap that private method on OUR instance only to learn that the end was a connection loss, not the peer hanging up;
* ``PyTgCallsSession.start`` prints a banner to stdout and does a version check against raw.githubusercontent.com; the worker's
  stdout is its IPC channel and Bossman makes no unrequested external calls, so ``notice_displayed`` is set before ``start``;
* Telethon's private ``_call/_sender/...`` are used by py-tgcalls' Telethon adapter: both packages are pinned exactly.

INCOMING CALLS (answering machine) -- NOT LIVE-TESTED. ``PyTgCallsLine`` / ``PyTgCallsTransport.accept`` are written from the
py-tgcalls 3.0.0 source and covered by unit tests against a fake engine only; no incoming call has ever reached this code on a
real account. What the source says (pytgcalls/methods/internal/handle_mtproto_updates.py, connect_call.py, calls/leave_call.py):
* a ringing P2P call arrives as ``ChatUpdate(user_id, Status.INCOMING_CALL)`` (``chat_id`` is the caller's user id) once the app
  is started; py-tgcalls has already stored the key-exchange data for it, so ``play(user_id, MediaStream, CallConfig(timeout))``
  ANSWERS it (``data.outgoing`` is False -> ``accept_call`` instead of ``request_call``) and returns when media is connected;
* ``leave_call(user_id)`` on a call that has not been answered yet DECLINES it (``is_p2p_waiting`` -> ``discard_call``);
* a call that stops ringing (caller gave up, or another device of the owner answered) arrives as a LEFT_CALL ``ChatUpdate``;
  the library does not pass the discard reason on, so the line reports ``unknown`` (the owner is still told about the call).
Unverified on a real account: that the owner's own phone keeps ringing while this session listens, that a decline by this
session ends the ring on the owner's other devices, and the exact update seen when the owner answers elsewhere.

Everything heavy is imported lazily; the ``_Engine`` seam is what the unit tests replace.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Protocol

from ..types import (AudioFormat, CallError, GONE_UNKNOWN, IncomingCall, PeerRef, TransportEvent,
                     TransportEventKind)

log = logging.getLogger("bcc.telegram_calls.transport")

TX_RATE = 48000
RX_RATE = 16000
FRAME_MS = 10

_ERROR_BY_CLASS = {
    "CallDeclined": "CALL_DECLINED",
    "CallDiscarded": "CALL_DECLINED",         # the peer hung up while we were still connecting
    "CallBusy": "CALL_BUSY",
    "TimedOutAnswer": "CALL_NO_ANSWER",
    "UserPrivacyRestrictedError": "PEER_PRIVACY",
    "UserIsBlockedError": "PEER_PRIVACY",
    "TelegramServerError": "TELEGRAM_NETWORK",  # ICE / media path failed after the answer
    "ConnectionNotFound": "CONNECTION_LOST",
    "NotInCallError": "CONNECTION_LOST",
    "FloodWaitError": "TELEGRAM_RPC",
    "PeerFloodError": "TELEGRAM_RPC",
}


def map_call_error(exc: BaseException) -> CallError:
    """Library exception -> stable CallError, by class name (no message text is ever copied)."""
    if isinstance(exc, CallError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return CallError("TELEGRAM_NETWORK", detail="timeout")
    for cls in type(exc).__mro__:
        code = _ERROR_BY_CLASS.get(cls.__name__)
        if code:
            return CallError(code, detail=cls.__name__)
    if isinstance(exc, (ConnectionError, OSError)):
        return CallError("TELEGRAM_NETWORK", detail=type(exc).__name__)
    return CallError("TELEGRAM_RPC", detail=type(exc).__name__)


class _Engine(Protocol):
    async def start(self) -> None: ...
    async def play(self, user_id: int, ring_timeout: int) -> None: ...
    async def record(self, user_id: int) -> None: ...
    async def send_frame(self, user_id: int, pcm: bytes) -> None: ...
    async def leave_call(self, user_id: int) -> None: ...
    def set_frame_handler(self, cb: Callable[[bytes], None]) -> None: ...
    def set_end_handler(self, cb: Callable[[bool], None]) -> None: ...   # (busy) -> None: the call was discarded
    def set_media_handler(self, cb: Callable[[bool], None]) -> None: ...  # (connected)
    def set_incoming_handler(self, cb: Callable[[int], None]) -> None: ...  # (caller user id): a call is ringing
    def set_left_handler(self, cb: Callable[[int, bool], None]) -> None: ...  # (user id, busy): any call went away
    async def close(self) -> None: ...


class PyTgCallsEngine:
    """The only place that touches py-tgcalls. Must be constructed inside the running event loop."""

    def __init__(self, client: Any):
        try:
            from pytgcalls import PyTgCalls, filters
            from pytgcalls.pytgcalls_session import PyTgCallsSession
            from pytgcalls.types import ChatUpdate, Device, Direction
        except Exception:  # noqa: BLE001
            raise CallError("DEPENDENCIES_MISSING", detail="py-tgcalls") from None
        PyTgCallsSession.notice_displayed = True       # no stdout banner, no version check to raw.githubusercontent.com
        self._app = PyTgCalls(client)
        self._frame_cb: Callable[[bytes], None] | None = None
        self._end_cb: Callable[[bool], None] | None = None
        self._media_cb: Callable[[bool], None] | None = None
        self._incoming_cb: Callable[[int], None] | None = None
        self._left_cb: Callable[[int, bool], None] | None = None
        self._ended = False
        self._started = False

        async def on_incoming(_, update) -> None:       # NOT live-tested: a P2P call is ringing on this account
            cb = self._incoming_cb
            if cb is not None:
                cb(int(getattr(update, "chat_id", 0) or 0))

        async def on_frames(_, update) -> None:         # runs once per 10 ms frame: must not block
            cb = self._frame_cb
            if cb is not None:
                for frame in update.frames:
                    cb(frame.frame)

        async def on_left(_, update) -> None:
            busy = bool(update.status & ChatUpdate.Status.BUSY_CALL)
            left = self._left_cb
            if left is not None:
                left(int(getattr(update, "chat_id", 0) or 0), busy)
            if self._ended:
                return
            self._ended = True
            if self._end_cb is not None:
                self._end_cb(busy)

        self._app.on_update(filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))(on_frames)
        self._app.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))(on_left)
        self._app.on_update(filters.chat_update(ChatUpdate.Status.INCOMING_CALL))(on_incoming)
        self._wrap_connection_changes()

    def _wrap_connection_changes(self) -> None:
        """Learn about media drops: py-tgcalls discards the call itself, which would look like a peer hangup."""
        original = getattr(self._app, "_handle_connection_changed", None)
        if original is None:
            return

        async def wrapped(chat_id, net_state):
            try:
                state = getattr(getattr(net_state, "state", None), "name", str(getattr(net_state, "state", "")))
                if state not in ("CONNECTING", "CONNECTED") and self._media_cb is not None:
                    self._media_cb(False)
                elif state == "CONNECTED" and self._media_cb is not None:
                    self._media_cb(True)
            except Exception:  # noqa: BLE001 - observation must never break the engine
                pass
            await original(chat_id, net_state)

        self._app._handle_connection_changed = wrapped

    async def start(self) -> None:
        if self._started:                                # the line starts the app once; a per-call transport reuses it
            return
        await self._app.start()
        self._started = True

    async def play(self, user_id: int, ring_timeout: int) -> None:
        from pytgcalls.types import CallConfig, ExternalMedia, MediaStream
        from pytgcalls.types.raw import AudioParameters
        self._ended = False                              # a new call on a shared app: its end must be reported again
        stream = MediaStream(ExternalMedia.AUDIO, audio_parameters=AudioParameters(TX_RATE, 1))
        await self._app.play(user_id, stream, CallConfig(timeout=ring_timeout))

    async def record(self, user_id: int) -> None:
        from pytgcalls.types import RecordStream
        from pytgcalls.types.raw import AudioParameters
        await self._app.record(user_id, RecordStream(audio=True, audio_parameters=AudioParameters(RX_RATE, 1)))

    async def send_frame(self, user_id: int, pcm: bytes) -> None:
        from pytgcalls.types import Device
        await self._app.send_frame(user_id, Device.MICROPHONE, bytes(pcm))     # statictypes: exactly ``bytes``

    async def leave_call(self, user_id: int) -> None:
        await self._app.leave_call(user_id)

    def set_frame_handler(self, cb):
        self._frame_cb = cb

    def set_end_handler(self, cb):
        self._end_cb = cb

    def set_media_handler(self, cb):
        self._media_cb = cb

    def set_incoming_handler(self, cb):
        self._incoming_cb = cb

    def set_left_handler(self, cb):
        self._left_cb = cb

    async def close(self) -> None:
        return None


class PyTgCallsTransport:
    """``CallTransport`` over an ``_Engine``. One instance = one call attempt; ``dial`` runs once."""

    name = "telegram"

    def __init__(self, engine: _Engine, *, connect_grace_s: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 engine_started: bool = False):
        self._engine = engine
        self._engine_started = engine_started          # a per-call transport of a ``PyTgCallsLine`` shares the line's running app
        self.audio_format = AudioFormat(sample_rate=TX_RATE)
        self.rx_sample_rate, self.frame_ms = RX_RATE, FRAME_MS
        self._frame_bytes = self.audio_format.frame_bytes(FRAME_MS)
        self._grace, self._clock = connect_grace_s, clock
        self._audio_cb: Callable[[bytes], None] | None = None
        self._event_cb: Callable[[TransportEvent], None] | None = None
        self._peer: PeerRef | None = None
        self._dialed = False
        self._connected = False
        self._ended = False
        self._media_dropped = False
        self.cleared = 0
        engine.set_frame_handler(self._on_frame)
        engine.set_end_handler(self._on_call_left)
        engine.set_media_handler(self._on_media)

    # ------------------------------------------------------------ CallTransport
    def set_audio_callback(self, cb: Callable[[bytes], None]) -> None:
        self._audio_cb = cb

    def set_event_callback(self, cb: Callable[[TransportEvent], None]) -> None:
        self._event_cb = cb

    async def start(self) -> None:
        if self._engine_started:
            return
        try:
            await asyncio.wait_for(self._engine.start(), 30)
        except CallError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_call_error(exc) from None

    async def dial(self, peer: PeerRef, *, ring_timeout: float) -> None:
        if self._dialed:
            raise CallError("CALL_IN_PROGRESS", detail="dial_twice")      # a transport instance rings at most once
        self._dialed = True
        self._peer = peer
        self._emit(TransportEventKind.RINGING)
        try:
            await asyncio.wait_for(self._engine.play(peer.user_id, int(ring_timeout)), ring_timeout + self._grace)
            await asyncio.wait_for(self._engine.record(peer.user_id), 10)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            err = map_call_error(exc if isinstance(exc, Exception) else RuntimeError())
            if isinstance(exc, asyncio.TimeoutError):
                # We do not know whether the phone rang: the session records UNKNOWN for anything that is not provable.
                err = CallError("TELEGRAM_NETWORK", detail="dial_timeout")
            raise err from None
        self._connected = True
        self._emit(TransportEventKind.CONNECTED)

    async def accept(self, call: IncomingCall, *, answer_timeout: float) -> None:
        """Answer a ringing incoming call (NOT live-tested). ``play`` on a call that is ringing IN answers it; there is no dial."""
        if self._dialed:
            raise CallError("CALL_IN_PROGRESS", detail="accept_twice")      # a transport instance answers at most once
        if not call.known:
            raise CallError("CALL_DISCARDED", detail="no_caller_id")
        self._dialed = True
        self._peer = PeerRef(int(call.caller_id), call.caller_label[:120])
        try:
            await asyncio.wait_for(self._engine.play(self._peer.user_id, int(answer_timeout)), answer_timeout + self._grace)
            await asyncio.wait_for(self._engine.record(self._peer.user_id), 10)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            err = map_call_error(exc if isinstance(exc, Exception) else RuntimeError())
            if type(exc).__name__ == "CallDiscarded":
                err = CallError("CALL_DISCARDED", detail="CallDiscarded")  # the caller gave up while we were answering
            elif isinstance(exc, asyncio.TimeoutError):
                err = CallError("CALL_NO_ANSWER", detail="accept_timeout")
            raise err from None
        self._connected = True
        self._emit(TransportEventKind.CONNECTED)

    async def send_audio(self, pcm: bytes) -> None:
        if self._ended or not self._connected or self._peer is None:
            return
        if len(pcm) != self._frame_bytes:
            raise ValueError("send_audio must receive exactly one 10 ms frame")
        try:
            await self._engine.send_frame(self._peer.user_id, pcm)
        except Exception as exc:  # noqa: BLE001
            err = map_call_error(exc)
            if err.code == "CONNECTION_LOST":
                self._finish("error")
            else:
                log.warning("send_frame failed: %s", err.code)

    async def clear_outgoing(self) -> None:
        # ntgcalls offers no way to recall pushed audio (mute/re-issuing the stream source did not flush it in tests).
        # The mitigation is upstream: Playout paces in real time, so at most ~one frame is ever in flight.
        self.cleared += 1

    async def hangup(self, reason: str = "local") -> None:
        if self._peer is None or self._ended:
            self._ended = True
            return
        self._ended = True
        self._connected = False
        try:
            await self._engine.leave_call(self._peer.user_id)
        except Exception as exc:  # noqa: BLE001 - already gone is the same outcome for us
            err = map_call_error(exc)
            if err.code != "CONNECTION_LOST":
                raise err from None
        self._emit(TransportEventKind.ENDED, "local_hangup")

    async def close(self) -> None:
        await self._engine.close()

    # ------------------------------------------------------------ engine callbacks (may run on any loop callback)
    def _on_frame(self, pcm: bytes) -> None:
        cb = self._audio_cb
        if cb is not None and self._connected and not self._ended:
            cb(pcm)

    def _on_media(self, connected: bool) -> None:
        if self._ended or not self._connected:
            return
        if not connected:
            self._media_dropped = True
            self._emit(TransportEventKind.DISCONNECTED)
        elif self._media_dropped:
            self._media_dropped = False
            self._emit(TransportEventKind.RECONNECTED)

    def _on_call_left(self, busy: bool) -> None:
        if self._ended or not self._connected:
            return        # still dialing: a decline/busy arrives as the exception of ``play`` and is mapped there
        self._finish("connection_lost" if self._media_dropped else "peer_hangup")

    def _finish(self, reason: str) -> None:
        if self._ended:
            return
        self._ended = True
        self._connected = False
        self._emit(TransportEventKind.ENDED, reason)

    def _emit(self, kind: TransportEventKind, reason: str = "") -> None:
        cb = self._event_cb
        if cb is not None:
            try:
                cb(TransportEvent(kind, reason, self._clock()))
            except Exception:  # noqa: BLE001
                log.exception("transport event observer failed")


def build_transport(client: Any) -> PyTgCallsTransport:
    """Factory used by the worker: real engine over the already authorised Telethon client (inside the running loop)."""
    return PyTgCallsTransport(PyTgCallsEngine(client))


class PyTgCallsLine:
    """``CallLine`` over the py-tgcalls engine (NOT live-tested, see the module docstring): one long-lived app that watches for
    ringing P2P calls and hands out one ``PyTgCallsTransport`` per call. It never answers or dials by itself."""

    name = "telegram"
    live_tested = False

    def __init__(self, engine: _Engine, *, clock: Callable[[], float] = time.time):
        self._engine = engine
        self._clock = clock
        self._incoming_cb: Callable[[IncomingCall], None] | None = None
        self._gone_cb: Callable[[str, str], None] | None = None
        self._ringing: dict[int, IncomingCall] = {}
        self._n = 0
        engine.set_incoming_handler(self._on_incoming)
        engine.set_left_handler(self._on_left)

    def set_incoming_callback(self, cb: Callable[[IncomingCall], None]) -> None:
        self._incoming_cb = cb

    def set_gone_callback(self, cb: Callable[[str, str], None]) -> None:
        self._gone_cb = cb

    async def listen(self) -> None:
        try:
            await asyncio.wait_for(self._engine.start(), 30)
        except CallError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_call_error(exc) from None

    def new_transport(self, call: IncomingCall) -> PyTgCallsTransport:
        self._ringing.pop(int(call.caller_id or 0), None)            # from now on its end is the transport's ENDED event
        return PyTgCallsTransport(self._engine, engine_started=True)

    async def reject(self, call: IncomingCall, reason: str = "rejected") -> None:
        self._ringing.pop(int(call.caller_id or 0), None)
        if not call.known:
            return
        try:
            await self._engine.leave_call(int(call.caller_id))      # on a call that was not answered this declines it
        except Exception as exc:  # noqa: BLE001 - already gone is the same outcome for us
            err = map_call_error(exc)
            if err.code != "CONNECTION_LOST":
                log.warning("decline of an incoming call failed: %s", err.code)

    async def close(self) -> None:
        await self._engine.close()

    # ------------------------------------------------------------ engine callbacks
    def _on_incoming(self, user_id: int) -> None:
        if user_id in self._ringing:
            return
        self._n += 1
        call = IncomingCall(call_ref=f"tg-in-{self._n}", caller_id=user_id, caller_label="", received_at=self._clock(),
                            transport="telegram")
        self._ringing[user_id] = call
        cb = self._incoming_cb
        if cb is not None:
            try:
                cb(call)
            except Exception:  # noqa: BLE001
                log.exception("incoming call observer failed")

    def _on_left(self, user_id: int, busy: bool) -> None:
        call = self._ringing.pop(user_id, None)
        if call is None:
            return                                                   # an answered call: its transport reports ENDED itself
        cb = self._gone_cb
        if cb is not None:
            try:
                cb(call.call_ref, GONE_UNKNOWN)
            except Exception:  # noqa: BLE001
                log.exception("incoming call observer failed")


def build_line(client: Any) -> PyTgCallsLine:
    """Factory used by the worker for the answering machine: the real engine over the authorised Telethon client."""
    return PyTgCallsLine(PyTgCallsEngine(client))
