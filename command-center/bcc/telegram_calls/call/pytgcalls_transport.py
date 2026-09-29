"""Real ``CallTransport`` for a private (1:1) Telegram voice call: py-tgcalls 3.0.0 + ntgcalls 3.0.0 on a Telethon 1.45 user client.

The transport is handed an ALREADY STARTED, already authorised Telethon client (created by the login module).
It never creates a client, never reads or stores credentials, never logs in, never sends chat messages.

Imports of ``pytgcalls`` / ``ntgcalls`` / ``telethon`` are lazy (inside ``start``), so importing this module
needs none of them.

VERIFIED FROM SOURCE (read in full for the paths used here)
-------------------------------------------------------------------------------------------------------
Artifacts (sha256): py_tgcalls-3.0.0-py3-none-any.whl c736066f3f79804f6f128231f4adad228e6aefb3418a473e0a52175c0a8baf49;
ntgcalls-3.0.0.tar.gz (C++ sources) 10f1822650c8a0f010e5273c5841e5bd143ad0afefa6d54d524ca66b0ef38ef0;
ntgcalls-3.0.0-cp312-cp312-win_amd64.whl 545cfe0069911cbc22f98b1ac8ba4be42065903e88467b590037aa5514c9beba.
* Private call = ``chat_id > 0`` (the user id). ``PyTgCalls(telethon_client)``, ``await start()`` (needs a connected
  client that receives updates; the constructor calls ``asyncio.get_event_loop()``, so it must be built inside the
  running loop).
* Outgoing call: ``await play(user_id, MediaStream(ExternalMedia.AUDIO, AudioParameters(rate, 1),
  video_flags=MediaStream.Flags.IGNORE), CallConfig(timeout=N))``. ``play`` runs ``request_call`` ONCE (for a
  private call the internal ``for retries in range(4)`` loop re-raises ``TelegramServerError`` at once because
  ``chat_id > 0`` -> the engine never redials), waits ``timeout`` seconds for the peer to accept
  (``TimedOutAnswer`` + ``discard_call(missed)``), then key exchange, then waits for the media connection with NO
  timeout of its own (the transport adds one, ``connect_grace_s``). It returns only when media is CONNECTED.
* Failure exceptions (``pytgcalls.exceptions``): ``CallDeclined``, ``CallBusy`` (PhoneCallDiscardReasonBusy),
  ``TimedOutAnswer``, ``CallDiscarded`` (peer hung up while media was connecting), ``NotInCallError``. A
  server-side "missed" discard is NOT distinguishable from "declined" in py-tgcalls (both -> ``CallDeclined``).
* Receive PCM: ``await record(user_id, RecordStream(audio=True, audio_parameters=AudioParameters(rate, 1)))``
  (sets the PLAYBACK side to an EXTERNAL audio writer). Frames arrive as ``StreamFrames`` updates
  (``direction == Direction.INCOMING``, ``device == Device.MICROPHONE`` = the peer's microphone), delivered on the
  event loop through ``add_handler(async def cb(client, update))``. Each ``Frame.frame`` is PCM16 little-endian
  signed, ``AudioParameters.bitrate`` (misnamed: it IS the sample rate in Hz) x ``channels``; ntgcalls resamples /
  remixes the remote audio to that description (``AudioReceiver::resample_frame``).
* Frame size: ``AudioSink::frame_size = sample_rate * 16/8 / 100 * channels`` bytes, i.e. exactly 10 ms
  (48000 Hz mono -> 480 samples = 960 bytes); ``frame_time = 10 ms``. Incoming frames are that size.
* Send PCM: ``await send_frame(user_id, Device.MICROPHONE, data)`` -> ``send_external_frame``. ntgcalls reads
  exactly ``frame_size`` bytes from ``data`` (``AudioStreamer::send_data`` ignores ``size``), so EVERY frame must be
  exactly 10 ms: this transport splits the 20 ms playout frames into two 10 ms frames and buffers a remainder.
  The engine does no pacing of its own (it pushes into the WebRTC source at once): the caller (Playout) paces
  in real time. There is NO flush / clear API for the external source anywhere in py-tgcalls or ntgcalls.
* Hangup: ``leave_call(user_id)``; while ringing it discards the request (``is_p2p_waiting`` branch); after
  connect it stops the binding and discards; ``NotInCallError`` when nothing is running. Peer hangup reaches us as
  ``ChatUpdate`` with ``Status.DISCARDED_CALL`` (``| BUSY_CALL`` when busy).
* A connection-state change other than CONNECTED after media is up makes py-tgcalls silently ``discard_call`` and
  clear its state WITHOUT emitting an update; the only public signal is that the call disappears from
  ``await client.private_calls``. A watchdog polls that and reports it as ENDED("error") after a short grace
  (so a normal peer hangup, whose ``ChatUpdate`` arrives first, is not misreported).
* ``PyTgCallsSession.start()`` performs a version check against raw.githubusercontent.com on the first start in a
  process. Because "notice_displayed" short-circuits it, the transport sets it True first: NO github request is made.

NOT VERIFIABLE WITHOUT A LIVE CALL (owner-live, NOT_RUN)
-------------------------------------------------------
* that Telegram delivers the call, that audio is intelligible in both directions, the real latency and jitter;
* whether the 10 ms burst of two frames per 20 ms tick is smoothed by the WebRTC jitter buffer without artefacts;
* how long the WebRTC source holds already pushed audio after ``clear_outgoing`` (there is no flush; we drop only
  what this transport still buffers, and stop pushing) - barge-in tail of a few tens of ms is possible;
* that a RINGING state can be reported: py-tgcalls has no public "ringing" update for outgoing calls, so
  ``TransportEventKind.RINGING`` is never emitted by this transport (the session simply stays in DIALING);
* DISCONNECTED / RECONNECTED are likewise never emitted: py-tgcalls does not expose media flaps for private calls
  (it hangs up on them); the watchdog turns that into ENDED("error"), which the session maps to CONNECTION_LOST;
* privacy / blocked errors, flood waits and their exact Telethon class names (mapped by class name below).

Guarantees (unit-tested against fake modules mirroring the verified API): dial at most once per transport object
and never retried (a second ``dial`` raises), idempotent ``hangup`` in any state (also during dialing: cancels the
dial), ``clear_outgoing`` stops in-flight sends, exactly one ENDED event, secret-free ``CallError`` codes only
(exception class names as ``detail``, never messages).
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable

from ..types import AudioFormat, CallError, PeerRef, TransportEvent, TransportEventKind

#: py-tgcalls / ntgcalls audio frame (10 ms). Verified: AudioSink::frame_time = 10 ms.
NATIVE_FRAME_MS = 10
#: what we hand to the audio callback (same as the loopback transport / playout).
RX_FRAME_MS = 20

_RECOMMENDED = ("py-tgcalls==3.0.0", "ntgcalls==3.0.0", "telethon==1.45.0")

# class-name -> CallError code. Matched against every class in the exception's MRO (no imports needed).
_NAME_TO_CODE: dict[str, tuple[str, str | None]] = {
    "CallDeclined": ("CALL_DECLINED", None),
    "CallBusy": ("CALL_BUSY", None),
    "TimedOutAnswer": ("CALL_NO_ANSWER", None),
    "CallDiscarded": ("CALL_DISCARDED", None),
    "NotInCallError": ("CONNECTION_LOST", None),
    "TelegramServerError": ("TELEGRAM_NETWORK", None),
    "UserPrivacyRestrictedError": ("PEER_PRIVACY", None),
    "UserIsBlockedError": ("PEER_PRIVACY", None),
    "UserBlockedError": ("PEER_PRIVACY", None),
    "PrivacyKeyInvalidError": ("PEER_PRIVACY", None),
    "UserIdInvalidError": ("PEER_NOT_FOUND", None),
    "PeerIdInvalidError": ("PEER_NOT_FOUND", None),
    "InputUserDeactivatedError": ("PEER_INVALID", None),
    "UserDeactivatedError": ("SESSION_REVOKED", None),
    "AuthKeyUnregisteredError": ("SESSION_REVOKED", None),
    "SessionRevokedError": ("SESSION_REVOKED", None),
    "SessionExpiredError": ("SESSION_REVOKED", None),
    "FloodWaitError": ("TELEGRAM_RPC", "flood_wait"),
    "ConnectionError": ("TELEGRAM_NETWORK", None),
    "TimeoutError": ("TELEGRAM_NETWORK", None),
    "OSError": ("TELEGRAM_NETWORK", None),
}


def map_exception(exc: BaseException) -> CallError:
    """Turn an engine / Telethon exception into a stable secret-free ``CallError`` (never keeps ``str(exc)``)."""
    if isinstance(exc, CallError):
        return exc
    names = [c.__name__ for c in type(exc).__mro__]
    for name in names:
        hit = _NAME_TO_CODE.get(name)
        if hit is not None:
            return CallError(hit[0], detail=hit[1] or type(exc).__name__)
    if "RPCError" in names:
        return CallError("TELEGRAM_RPC", detail=type(exc).__name__)
    return CallError("INTERNAL", detail=type(exc).__name__)


class PyTgCallsTransport:
    """One private call. Create per call; ``close()`` when done. All methods run on the worker's event loop."""

    name = "pytgcalls"

    def __init__(self, client: Any, *, sample_rate: int = 48000, connect_grace_s: float = 30.0,
                 watchdog_s: float = 2.0, lost_grace_s: float = 1.5, hangup_timeout_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        if sample_rate not in (8000, 16000, 24000, 48000):
            raise ValueError("unsupported transport sample rate")
        self.audio_format = AudioFormat(sample_rate=sample_rate, channels=1)
        self._client = client
        self._connect_grace, self._watchdog_s = float(connect_grace_s), float(watchdog_s)
        self._lost_grace, self._hangup_timeout = float(lost_grace_s), float(hangup_timeout_s)
        self._clock = clock
        self._native_bytes = self.audio_format.frame_bytes(NATIVE_FRAME_MS)
        self._rx_bytes = self.audio_format.frame_bytes(RX_FRAME_MS)
        self._audio_cb: Callable[[bytes], None] | None = None
        self._event_cb: Callable[[TransportEvent], None] | None = None
        self._mods: dict[str, Any] | None = None
        self._pytg: Any = None
        self._peer_id: int | None = None
        self._dialed = False
        self._dial_task: asyncio.Task | None = None
        self._connected = False
        self._hanging = False
        self._hangup_done = False
        self._ended_emitted = False
        self._closed = False
        self._rx_buf = bytearray()
        self._tx_buf = bytearray()
        self._tx_gen = 0
        self._tx_lock = asyncio.Lock()
        self._hangup_lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None
        self.stats = {"rx_frames": 0, "tx_frames": 0, "tx_dropped": 0, "tx_errors": 0, "hangup_errors": 0}

    # ------------------------------------------------------------------ setup
    def set_audio_callback(self, cb: Callable[[bytes], None]) -> None:
        self._audio_cb = cb

    def set_event_callback(self, cb: Callable[[TransportEvent], None]) -> None:
        self._event_cb = cb

    def _emit(self, kind: TransportEventKind, reason: str = "") -> None:
        if self._event_cb is None:
            return
        try:
            self._event_cb(TransportEvent(kind, reason, self._clock()))
        except Exception:  # noqa: BLE001 - a broken observer must not break the call path
            pass

    def _emit_ended(self, reason: str) -> None:
        if self._ended_emitted:
            return
        self._ended_emitted = True
        self._connected = False
        self._emit(TransportEventKind.ENDED, reason)

    async def start(self) -> None:
        """Import the engine lazily and start py-tgcalls on the injected client. No call is requested here."""
        if self._pytg is not None:
            return
        try:
            import ntgcalls  # noqa: F401
            import pytgcalls
            from pytgcalls import exceptions as tg_exc
            from pytgcalls import types as tg_types
            from pytgcalls.types import raw as tg_raw
        except Exception as exc:  # noqa: BLE001
            raise CallError("DEPENDENCIES_MISSING", detail=type(exc).__name__) from None
        self._mods = {"pytgcalls": pytgcalls, "exc": tg_exc, "types": tg_types, "raw": tg_raw}
        client = self._client
        try:
            if not client.is_connected():
                raise CallError("TELEGRAM_NETWORK", detail="client_not_connected")
            if not await client.is_user_authorized():
                raise CallError("NOT_LOGGED_IN")
            try:  # skip the GitHub version check py-tgcalls would do on first start (no network side effects)
                from pytgcalls.pytgcalls_session import PyTgCallsSession
                PyTgCallsSession.notice_displayed = True
            except Exception:  # noqa: BLE001
                pass
            pytg = pytgcalls.PyTgCalls(client)
            pytg.add_handler(self._on_update, None)
            await pytg.start()
        except CallError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from None
        self._pytg = pytg

    # ------------------------------------------------------------------ dial
    async def dial(self, peer: PeerRef, *, ring_timeout: float) -> None:
        """Request the call exactly once; return when media is connected; otherwise raise ``CallError``."""
        if self._dialed:
            raise CallError("CALL_IN_PROGRESS", detail="dial_called_twice")
        self._dialed = True                       # set BEFORE anything can fail: a failed dial is never repeated
        if self._pytg is None or self._mods is None:
            raise CallError("WORKER_UNAVAILABLE", detail="transport_not_started")
        if not isinstance(peer, PeerRef):
            raise CallError("PEER_INVALID")
        self._peer_id = peer.user_id
        t = max(1, int(math.ceil(ring_timeout)))
        try:                                      # the Telethon session must already know the access hash
            await self._client.get_input_entity(peer.user_id)
        except CallError:
            raise
        except Exception:  # noqa: BLE001
            raise CallError("PEER_NOT_FOUND") from None
        self._dial_task = asyncio.ensure_future(self._dial_inner(peer.user_id, t))
        try:
            await self._dial_task
        except asyncio.CancelledError:
            if self._hanging:                     # our own hangup cancelled the dial
                raise CallError("CALL_DISCARDED", detail="local_hangup") from None
            await self._abort_dial()
            raise
        except BaseException as exc:  # noqa: BLE001
            err = map_exception(exc) if isinstance(exc, Exception) else CallError("INTERNAL", detail=type(exc).__name__)
            if self._hanging and err.code in ("CONNECTION_LOST", "CALL_DISCARDED", "CALL_NO_ANSWER", "INTERNAL"):
                err = CallError("CALL_DISCARDED", detail="local_hangup")
            await self._abort_dial()
            raise err from None
        finally:
            self._dial_task = None
        self._connected = True
        self._watchdog = asyncio.ensure_future(self._watch())
        self._emit(TransportEventKind.CONNECTED)

    async def _dial_inner(self, chat_id: int, ring_timeout_s: int) -> None:
        m = self._mods
        types_, raw, pytg = m["types"], m["raw"], self._pytg
        params = raw.AudioParameters(self.audio_format.sample_rate, 1)
        stream = types_.MediaStream(types_.ExternalMedia.AUDIO, audio_parameters=params,
                                    video_flags=types_.MediaStream.Flags.IGNORE)
        config = types_.CallConfig(timeout=ring_timeout_s)
        try:
            await asyncio.wait_for(pytg.play(chat_id, stream, config), ring_timeout_s + self._connect_grace)
        except asyncio.TimeoutError:
            raise CallError("TELEGRAM_NETWORK", detail="connect_timeout") from None
        # PLAYBACK side: external writer so the peer's decoded audio comes back as StreamFrames updates.
        await pytg.record(chat_id, types_.RecordStream(audio=True, audio_parameters=params))

    async def _abort_dial(self) -> None:
        """Best effort: make sure nothing keeps ringing / connected after a failed or cancelled dial."""
        await self._leave()

    # ------------------------------------------------------------------ inbound
    async def _on_update(self, _client: Any, update: Any) -> None:
        m = self._mods
        if m is None or self._peer_id is None or getattr(update, "chat_id", None) != self._peer_id:
            return
        types_ = m["types"]
        if isinstance(update, types_.StreamFrames):
            if not self._connected or self._hanging:
                return
            direction, device = update.direction, update.device
            if direction is None or device is None:
                return
            if not (direction & types_.Direction.INCOMING) or not (device & types_.Device.MICROPHONE):
                return
            for fr in update.frames:
                data = getattr(fr, "frame", b"")
                if data:
                    self._push_rx(bytes(data))
        elif isinstance(update, types_.ChatUpdate):
            gone = (types_.ChatUpdate.Status.DISCARDED_CALL | types_.ChatUpdate.Status.KICKED
                    | types_.ChatUpdate.Status.LEFT_GROUP | types_.ChatUpdate.Status.CLOSED_VOICE_CHAT)
            if (update.status & gone) and self._connected and not self._hanging:
                self._emit_ended("peer_hangup")

    def _push_rx(self, data: bytes) -> None:
        self._rx_buf += data
        cb = self._audio_cb
        n = self._rx_bytes
        while len(self._rx_buf) >= n:
            frame = bytes(self._rx_buf[:n])
            del self._rx_buf[:n]
            self.stats["rx_frames"] += 1
            if cb is not None:
                try:
                    cb(frame)
                except Exception:  # noqa: BLE001
                    pass

    async def _watch(self) -> None:
        try:
            while not self._hanging and not self._ended_emitted:
                await asyncio.sleep(self._watchdog_s)
                if self._hanging or self._ended_emitted or self._pytg is None:
                    return
                try:
                    calls = await self._pytg.private_calls
                except Exception:  # noqa: BLE001 - cannot tell: keep watching
                    continue
                if self._peer_id in calls:
                    continue
                await asyncio.sleep(self._lost_grace)        # let a normal peer-hangup ChatUpdate win
                if not self._hanging and not self._ended_emitted:
                    self._emit_ended("error")
                return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ outbound
    async def send_audio(self, pcm: bytes) -> None:
        if not self._connected or self._hanging or self._pytg is None or not pcm:
            return
        types_ = self._mods["types"]
        async with self._tx_lock:
            gen = self._tx_gen
            self._tx_buf += pcm
            n = self._native_bytes
            while len(self._tx_buf) >= n:
                if gen != self._tx_gen or self._hanging or not self._connected:
                    self.stats["tx_dropped"] += 1
                    return
                chunk = bytes(self._tx_buf[:n])
                del self._tx_buf[:n]
                try:
                    await self._pytg.send_frame(self._peer_id, types_.Device.MICROPHONE, chunk)
                    self.stats["tx_frames"] += 1
                except Exception as exc:  # noqa: BLE001
                    self.stats["tx_errors"] += 1
                    if "NotInCallError" in [c.__name__ for c in type(exc).__mro__]:
                        self._emit_ended("error")
                    return

    async def clear_outgoing(self) -> None:
        """Barge-in / STOP: drop everything this transport still holds and stop in-flight pushes.

        The engine has no flush for already pushed external frames (verified: none exists), so audio that is
        already inside the WebRTC source keeps playing for at most its own small buffer."""
        self._tx_gen += 1
        self._tx_buf.clear()

    # ------------------------------------------------------------------ teardown
    async def _leave(self) -> None:
        pytg, chat_id = self._pytg, self._peer_id
        if pytg is None or chat_id is None or not self._dialed:
            return
        try:
            await asyncio.wait_for(pytg.leave_call(chat_id), self._hangup_timeout)
        except Exception as exc:  # noqa: BLE001
            if "NotInCallError" not in [c.__name__ for c in type(exc).__mro__]:
                self.stats["hangup_errors"] += 1

    async def hangup(self, reason: str = "local") -> None:
        """Idempotent; safe before start, during dialing, when connected, after the peer hung up."""
        self._hanging = True
        self._tx_gen += 1
        self._tx_buf.clear()
        task = self._dial_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        async with self._hangup_lock:
            if not self._hangup_done:
                self._hangup_done = True
                await self._leave()
        wd = self._watchdog
        if wd is not None and not wd.done():
            wd.cancel()
        if self._dialed:
            self._emit_ended("local_hangup")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.hangup("close")
        pytg, self._pytg = self._pytg, None
        if pytg is not None:
            try:
                pytg.remove_handler(self._on_update)
            except Exception:  # noqa: BLE001
                pass
        self._audio_cb = None
        self._client = None


def dependencies_status() -> dict[str, Any]:
    """Doctor probe without importing the heavy modules: which of the pinned packages are installed."""
    import importlib.metadata as md
    out: dict[str, Any] = {"recommended": list(_RECOMMENDED)}
    for dist in ("py-tgcalls", "ntgcalls", "telethon"):
        try:
            out[dist] = md.version(dist)
        except md.PackageNotFoundError:
            out[dist] = None
    out["ok"] = all(out[d] is not None for d in ("py-tgcalls", "ntgcalls", "telethon"))
    return out
