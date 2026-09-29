"""Fakes for the account / worker / manager tests (Line A). They prove control flow, never Telegram itself."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.call.session import SessionConfig
from bcc.telegram_calls.call.worker import Worker

from .fakes import ScriptedBrain, ScriptedSTT, ToneTTS


class RPCError(Exception):
    """Same NAME as telethon.errors.RPCError; the mapper works on class names only."""


def tg_error(name: str, base: type = RPCError, **attrs: Any) -> Exception:
    """An exception whose message carries a fake phone number: it must never surface anywhere."""
    exc = type(name, (base,), {})("secret text +79001234567")
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


class User:
    """Class name is literally ``User`` like telethon.tl.types.User."""

    def __init__(self, id: int, first_name: str = "Second", last_name: str = "", username: str | None = None,
                 bot: bool = False, deleted: bool = False, is_self: bool = False, phone: str = "79990000000"):
        self.id, self.first_name, self.last_name, self.username = id, first_name, last_name, username
        self.bot, self.deleted, self.is_self, self.phone = bot, deleted, is_self, phone


class Channel:
    def __init__(self, id: int):
        self.id = id


class FakeSession:
    def save(self) -> str:
        return "SESSION-STRING-XYZ"


class FakeClient:
    """Telethon-shaped client. ``script`` maps method name -> exception to raise (once, list = sequence)."""

    def __init__(self, *, me_id: int = 111, script: dict | None = None, contacts=None, entities=None, authorized: bool = True):
        self.me_id, self.script = me_id, {k: (list(v) if isinstance(v, list) else [v]) for k, v in (script or {}).items()}
        self.contacts, self.entities, self.authorized = contacts or [], entities or {}, authorized
        self.session = FakeSession()
        self.calls: list[tuple[str, tuple, dict]] = []
        self.connected = False
        self.disconnected = 0
        self.logged_out = False

    def _maybe_raise(self, name: str) -> None:
        seq = self.script.get(name)
        if seq:
            raise seq.pop(0)

    async def connect(self):
        self.calls.append(("connect", (), {}))
        self._maybe_raise("connect")
        self.connected = True

    async def disconnect(self):
        self.disconnected += 1
        self.connected = False

    async def send_code_request(self, phone):
        self.calls.append(("send_code_request", (phone,), {}))
        self._maybe_raise("send_code_request")
        return SimpleNamespace(phone_code_hash="HASH-1")

    async def sign_in(self, **kw):
        self.calls.append(("sign_in", (), kw))
        self._maybe_raise("sign_in")
        return SimpleNamespace(id=self.me_id)

    async def get_me(self):
        return User(self.me_id, "Me", is_self=True)

    async def is_user_authorized(self):
        return self.authorized

    async def log_out(self):
        self.logged_out = True

    async def get_contacts(self):
        self._maybe_raise("get_contacts")
        return list(self.contacts)

    async def get_entity(self, uid):
        self._maybe_raise("get_entity")
        if uid not in self.entities:
            raise ValueError("Cannot find any entity")
        return self.entities[uid]


class Factory:
    """client_factory recording the arguments; hands out one prepared client (or a new one per call)."""

    def __init__(self, client: FakeClient | None = None, **kw):
        self.kw, self.client, self.made, self.args = kw, client, [], []

    def __call__(self, api_id, api_hash, session):
        self.args.append((api_id, api_hash, session))
        c = self.client or FakeClient(**self.kw)
        self.made.append(c)
        return c


API_HASH = "0123456789abcdef0123456789abcdef"


class MemVault:
    """Vault stand-in with the same API (tests of credentials use the REAL Vault too)."""

    def encrypt(self, v):
        return None if not v else "enc:" + v[::-1]

    def decrypt(self, b):
        return None if not b or not b.startswith("enc:") else b[4:][::-1]


# ---------------------------------------------------------------- worker in-process
def fast_cfg(settings) -> SessionConfig:
    return SessionConfig(ring_timeout_s=settings.ring_timeout_s, max_call_s=settings.max_call_s, greeting="",
                         greet_wait_s=0.1, idle_prompt_s=30, idle_hangup_s=60, drain_timeout_s=5, pace=0.0)


def fake_engines_factory(replies=()):
    def build(data_dir):
        return ScriptedSTT([]), ToneTTS(), ScriptedBrain(list(replies)), EnergyVAD
    return build


class InProcWorker:
    """WorkerHandle implementation running a real ``Worker.serve`` loop inside the test's event loop."""

    def __init__(self, worker: Worker):
        self.worker = worker
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.outbox: asyncio.Queue[bytes] = asyncio.Queue()
        self.terminated = False
        self.killed = False
        self.lines_in: list[str] = []
        self.lines_out: list[dict] = []
        self.task = asyncio.get_running_loop().create_task(self._run())
        self._exit = asyncio.Event()

    async def _run(self):
        def write(obj):
            self.lines_out.append(obj)
            self.outbox.put_nowait(json.dumps(obj).encode() + b"\n")
        try:
            await self.worker.serve(self.inbox.get, write)
        finally:
            self.outbox.put_nowait(b"")
            self._exit.set()

    async def readline(self):
        return await self.outbox.get()

    def write(self, data: bytes):
        for line in data.decode().splitlines():
            self.lines_in.append(line)
            self.inbox.put_nowait(line)

    async def drain(self):
        await asyncio.sleep(0)

    def terminate(self):
        self.terminated = True
        self.task.cancel()

    def kill(self):
        self.killed = True
        self.task.cancel()

    async def wait(self):
        await self._exit.wait()
        return 0

    def eof(self):
        """Simulate the parent closing our stdin."""
        self.inbox.put_nowait("")


class DeadHandle:
    """A worker that exits by itself (crash) - or hangs and never answers when ``hang``."""

    def __init__(self, *, hang: bool = False):
        self.hang, self.q, self.terminated, self.killed, self.writes = hang, asyncio.Queue(), False, False, []

    async def readline(self):
        return await self.q.get()

    def write(self, data: bytes):
        self.writes.append(json.loads(data.decode()))

    async def drain(self):
        pass

    def terminate(self):
        self.terminated = True
        self.q.put_nowait(b"")

    def kill(self):
        self.killed = True
        self.q.put_nowait(b"")

    async def wait(self):
        return 1

    def crash(self):
        self.q.put_nowait(b"")

    def reply(self, obj):
        self.q.put_nowait(json.dumps(obj).encode() + b"\n")


def make_worker(tmp_path, *, transport=None, engines=None, client=None, **kw) -> Worker:
    return Worker(tmp_path, client_factory=Factory(client or FakeClient()),
                  transport_factory=lambda c: transport or LoopbackTransport(),
                  engines_factory=engines or fake_engines_factory(), session_cfg=fast_cfg, **kw)


async def until(cond, timeout: float = 5.0, step: float = 0.01) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return False
