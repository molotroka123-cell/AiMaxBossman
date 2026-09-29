"""Fakes for Line B: fake ``pytgcalls`` / ``ntgcalls`` modules mirroring the API verified from the py-tgcalls 3.0.0
wheel, a fake Telethon client, and a local fake OpenAI-compatible SSE server. They prove control flow only."""
from __future__ import annotations

import asyncio
import enum
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ------------------------------------------------------------------ fake pytgcalls
class _Flag(enum.Flag):
    pass


def build_fake_modules(ctl: "FakeEngine") -> dict[str, types.ModuleType]:
    """Module objects for sys.modules. Names and signatures follow py-tgcalls 3.0.0 exactly where used."""
    exc = types.ModuleType("pytgcalls.exceptions")
    for name in ("CallDeclined", "CallBusy", "TimedOutAnswer", "CallDiscarded", "NotInCallError"):
        setattr(exc, name, type(name, (Exception,), {}))

    class Direction(_Flag):
        OUTGOING = enum.auto()
        INCOMING = enum.auto()

    class Device(_Flag):
        MICROPHONE = enum.auto()
        SPEAKER = enum.auto()
        CAMERA = enum.auto()
        SCREEN = enum.auto()

    class ExternalMedia(_Flag):
        AUDIO = enum.auto()
        VIDEO = enum.auto()

    class _Status(_Flag):
        KICKED = enum.auto()
        LEFT_GROUP = enum.auto()
        CLOSED_VOICE_CHAT = enum.auto()
        INVITED_VOICE_CHAT = enum.auto()
        DISCARDED_CALL = enum.auto()
        INCOMING_CALL = enum.auto()
        BUSY_CALL = enum.auto()

    class Update:
        def __init__(self, chat_id):
            self.chat_id = chat_id

    class ChatUpdate(Update):
        Status = _Status

        def __init__(self, chat_id, status):
            super().__init__(chat_id)
            self.status = status

    class Frame:
        def __init__(self, ssrc, frame, info=None):
            self.ssrc, self.frame, self.info = ssrc, frame, info

    class StreamFrames(Update):
        def __init__(self, chat_id, direction, device, frames):
            super().__init__(chat_id)
            self.direction, self.device, self.frames = direction, device, frames

    class CallConfig:
        def __init__(self, timeout=60, conference=False):
            self.timeout, self.conference = timeout, conference

    class AudioParameters:
        def __init__(self, bitrate=48000, channels=1):
            self.bitrate, self.channels = bitrate, channels

    class MediaStream:
        class Flags(_Flag):
            AUTO_DETECT = enum.auto()
            REQUIRED = enum.auto()
            IGNORE = enum.auto()

        def __init__(self, media_path, audio_parameters=None, video_parameters=None, audio_path=None,
                     audio_flags=None, video_flags=None, **kw):
            self.media_path, self.audio_parameters, self.video_flags = media_path, audio_parameters, video_flags

    class RecordStream:
        def __init__(self, audio=False, audio_parameters=None, camera=False, screen=False):
            self.audio, self.audio_parameters = audio, audio_parameters

    class PyTgCalls:
        def __init__(self, app, workers=4, cache_duration=3600):
            ctl.instance = self
            self.app = app
            self.handlers = []
            self.started = False
            self._calls: dict[int, bool] = {}

        def add_handler(self, func, filters=None):
            self.handlers.append(func)
            return func

        def remove_handler(self, func):
            self.handlers = [h for h in self.handlers if h != func]

        async def start(self):
            ctl.calls.append(("start",))
            if ctl.start_error:
                raise ctl.start_error
            self.started = True

        @property
        async def private_calls(self):
            async def _c():
                return {k: object() for k, v in self._calls.items() if v}
            return await _c()

        async def play(self, chat_id, stream=None, config=None):
            ctl.calls.append(("play", chat_id, stream, config))
            ctl.play_count += 1
            if ctl.play_delay:
                await asyncio.sleep(ctl.play_delay)
            if ctl.play_error:
                raise ctl.play_error
            self._calls[chat_id] = True

        async def record(self, chat_id, stream=None, config=None):
            ctl.calls.append(("record", chat_id, stream))
            if ctl.record_error:
                raise ctl.record_error

        async def send_frame(self, chat_id, device, data, frame_data=None):
            ctl.calls.append(("send_frame", chat_id, device, len(data)))
            if ctl.send_error:
                raise ctl.send_error
            if ctl.send_delay:
                await asyncio.sleep(ctl.send_delay)
            ctl.sent.append(bytes(data))

        async def leave_call(self, chat_id, close=False):
            ctl.calls.append(("leave_call", chat_id))
            if ctl.leave_error:
                raise ctl.leave_error
            was = self._calls.get(chat_id)
            self._calls[chat_id] = False
            if not was and not ctl.leave_ok_when_idle:
                raise exc.NotInCallError()

        def drop_call(self, chat_id):
            self._calls[chat_id] = False

        async def deliver(self, update):
            for h in list(self.handlers):
                await h(self, update)

    types_m = types.ModuleType("pytgcalls.types")
    for obj in (Direction, Device, ExternalMedia, ChatUpdate, StreamFrames, Frame, CallConfig, MediaStream,
                RecordStream, Update):
        setattr(types_m, obj.__name__, obj)
    raw = types.ModuleType("pytgcalls.types.raw")
    raw.AudioParameters = AudioParameters
    types_m.raw = raw
    sess = types.ModuleType("pytgcalls.pytgcalls_session")

    class PyTgCallsSession:
        notice_displayed = False
    sess.PyTgCallsSession = PyTgCallsSession
    top = types.ModuleType("pytgcalls")
    top.PyTgCalls = PyTgCalls
    top.exceptions, top.types, top.pytgcalls_session = exc, types_m, sess
    top.__version__ = "3.0.0"
    nt = types.ModuleType("ntgcalls")
    nt.__version__ = "3.0.0"
    ctl.mods = {"exc": exc, "types": types_m, "session": sess}
    return {"pytgcalls": top, "pytgcalls.exceptions": exc, "pytgcalls.types": types_m, "pytgcalls.types.raw": raw,
            "pytgcalls.pytgcalls_session": sess, "ntgcalls": nt}


class FakeEngine:
    def __init__(self):
        self.instance = None
        self.calls: list[tuple] = []
        self.sent: list[bytes] = []
        self.play_count = 0
        self.play_delay = 0.0
        self.send_delay = 0.0
        self.play_error = self.record_error = self.send_error = self.leave_error = self.start_error = None
        self.leave_ok_when_idle = False
        self.mods: dict = {}

    def install(self, monkeypatch) -> "FakeEngine":
        for name, mod in build_fake_modules(self).items():
            monkeypatch.setitem(sys.modules, name, mod)
        return self

    def names(self):
        return [c[0] for c in self.calls]

    def frames(self, chat_id, pcm: bytes, *, incoming=True, device="MICROPHONE"):
        t = self.mods["types"]
        d = t.Direction.INCOMING if incoming else t.Direction.OUTGOING
        return t.StreamFrames(chat_id, d, getattr(t.Device, device), [t.Frame(0, pcm)])


class FakeTelethon:
    def __init__(self, *, connected=True, authorized=True, known=(777,)):
        self.connected, self.authorized, self.known = connected, authorized, set(known)

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_input_entity(self, user_id):
        if user_id not in self.known:
            raise ValueError("Could not find the input entity")
        return object()


# ------------------------------------------------------------------ fake OpenAI-compatible SSE server
class FakeLLMServer:
    """Loopback server on a free port speaking /v1/chat/completions (stream and non-stream)."""

    def __init__(self, *, deltas=("Привет. ", "Как дела?"), reasoning=(), model="local-fake", status=200,
                 non_stream_text="", hang=False):
        self.deltas, self.reasoning, self.model, self.status = list(deltas), list(reasoning), model, status
        self.non_stream_text, self.hang = non_stream_text, hang
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "body": None, "auth": self.headers.get("Authorization")})
                payload = json.dumps({"data": [{"id": outer.model}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                if outer.hang:
                    threading.Event().wait(5)
                    return
                if not body.get("stream"):
                    payload = json.dumps({"model": outer.model, "choices": [
                        {"message": {"role": "assistant", "content": outer.non_stream_text},
                         "finish_reason": "stop"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def emit(delta, finish=None):
                    frame = {"model": outer.model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(b"data: " + json.dumps(frame, ensure_ascii=False).encode() + b"\n\n")
                    self.wfile.flush()
                for r in outer.reasoning:
                    emit({"reasoning_content": r})
                for d in outer.deltas:
                    emit({"content": d})
                emit({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
