"""The calls worker: JSON-lines over stdin/stdout, run as ``python -I -m bcc.telegram_calls``.

Protocol (see docs/telegram-calls/ARCHITECTURE.md): one JSON object per line.
  request   {"id", "op", "args"}   ->  {"id", "ok": true, "result"} | {"id", "ok": false, "error": {code,message,hint[,detail]}}
  events    {"event": "state|call_event|record|log", ...}   (unsolicited)

Ops: ping, status, login_start, login_code, login_password, login_cancel, logout, contacts, resolve_peer,
dial, hangup, stop, shutdown.  ``dial`` takes NO peer: the target is read from the owner's confirmed settings
FRESH from disk at dial time and the guard is re-checked here (the manager checked it too).
``stop`` is handled inline in the reader loop (never queued behind another op) and also raises the persistent
STOP flag. On stdin EOF the worker hangs up and exits (a dead Command Center must not leave a live call).

Secrets: login secrets travel only in ``args`` of login ops; nothing secret is ever written to stdout
(results are status dicts, errors are stable codes) or logged. Anything a library prints is redirected to
stderr by ``__main__``.

Injection points (all for tests): ``client_factory``, ``transport_factory(client) -> CallTransport``,
``engines_factory(data_dir) -> (stt, tts, brain, vad_factory)`` (real one: ``speech.factory.build_engines``,
imported lazily).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import MODULE_VERSION
from ..account.credentials import CredentialStore
from ..account.login import LoginFlow, default_client_factory, list_contacts, resolve_peer
from ..guard import check_dial
from ..settings import SettingsStore
from ..stopflag import StopFlag
from ..types import CallError, CallEvent
from .session import CallSession, SessionConfig

STOP_WAIT_S = 6.0


def default_engines_factory(data_dir: Path) -> Any:
    try:
        from ..speech.factory import build_engines
    except ImportError:
        raise CallError("DEPENDENCIES_MISSING") from None
    return build_engines(data_dir)


def default_transport_factory(client: Any) -> Any:
    try:
        from .pytgcalls_transport import PyTgCallsTransport
    except ImportError:
        raise CallError("DEPENDENCIES_MISSING") from None
    return PyTgCallsTransport(client)


def _error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, CallError):
        return exc.as_dict()
    return CallError("INTERNAL", detail=type(exc).__name__).as_dict()


class Worker:
    def __init__(self, data_dir: Path | str, *, client_factory: Callable[..., Any] = default_client_factory,
                 transport_factory: Callable[[Any], Any] = default_transport_factory,
                 engines_factory: Callable[[Path], Any] = default_engines_factory,
                 session_cfg: Callable[[Any], SessionConfig] | None = None,
                 emit: Callable[[dict[str, Any]], None] | None = None, pace: float = 1.0):
        self.data_dir = Path(data_dir)
        self.settings = SettingsStore(self.data_dir)
        self.stopflag = StopFlag(self.data_dir)
        self.creds = CredentialStore(self.data_dir)
        self.login = LoginFlow(self.creds, client_factory)
        self._transport_factory, self._engines_factory = transport_factory, engines_factory
        self._session_cfg = session_cfg
        self._emit_fn = emit
        self._pace = pace
        self._login_lock = asyncio.Lock()
        self._session: CallSession | None = None
        self._call_task: asyncio.Task | None = None
        self._call_id: str | None = None
        self._busy = False                 # dial setup or call running: one call at a time
        self._stop_seen = False
        self._closing = False
        self._pending: set[asyncio.Task] = set()

    # ================================================================== output
    def emit(self, event: str, **data: Any) -> None:
        if self._emit_fn is not None:
            try:
                self._emit_fn({"event": event, **data})
            except Exception:  # noqa: BLE001 - a broken pipe must not break the call
                pass

    def _on_call_event(self, call_id: str, ev: CallEvent) -> None:
        self.emit("call_event", call_id=call_id, **ev.as_dict())
        if ev.kind == "state":
            self.emit("state", call_id=call_id, state=ev.data.get("state"))

    # ================================================================== ops
    async def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        rid = req.get("id")
        try:
            op = req.get("op")
            args = req.get("args") or {}
            if not isinstance(op, str) or not isinstance(args, dict):
                raise CallError("INTERNAL", detail="bad_request")
            fn = getattr(self, f"op_{op}", None)
            if fn is None or op.startswith("_"):
                raise CallError("INTERNAL", detail="unknown_op")
            return {"id": rid, "ok": True, "result": await fn(args)}
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - always answer with a stable code
            return {"id": rid, "ok": False, "error": _error_payload(exc)}

    async def op_ping(self, args: dict) -> dict:
        return {"pong": True, "version": MODULE_VERSION}

    async def op_status(self, args: dict) -> dict:
        return {"account": self.login.status(), "call": self._call_view(), "stop": self.stopflag.info(),
                "self_id_known": self.creds.self_id() is not None}

    def _call_view(self) -> dict | None:
        s = self._session
        if s is None:
            return {"call_id": self._call_id, "state": "dialing"} if self._busy else None
        return {"call_id": self._call_id, "state": s.record.state.value, "phase": s.phase.value}

    async def op_login_start(self, args: dict) -> dict:
        self._no_call()
        async with self._login_lock:
            return await self.login.start(args.get("phone"))

    async def op_login_code(self, args: dict) -> dict:
        async with self._login_lock:
            return await self.login.submit_code(args.get("code"))

    async def op_login_password(self, args: dict) -> dict:
        async with self._login_lock:
            return await self.login.submit_password(args.get("password"))

    async def op_login_cancel(self, args: dict) -> dict:
        async with self._login_lock:
            return await self.login.cancel()

    async def op_logout(self, args: dict) -> dict:
        self._no_call()
        async with self._login_lock:
            return await self.login.logout()

    def _no_call(self) -> None:
        # a second connection on the same session string while a call runs risks AUTH_KEY_DUPLICATED
        if self._busy:
            raise CallError("CALL_IN_PROGRESS")

    async def _with_session(self, fn: Callable[[Any, int | None], Awaitable[Any]]) -> Any:
        self._no_call()
        async with self._login_lock:          # serialised with login/dial: never two connections on one session
            client = None
            try:
                client = await self.login.open_session()
                return await fn(client, self.creds.self_id())
            finally:
                if client is not None:
                    try:
                        await asyncio.wait_for(client.disconnect(), 5)
                    except BaseException:  # noqa: BLE001
                        pass

    async def op_contacts(self, args: dict) -> dict:
        contacts = await self._with_session(lambda c, me: list_contacts(c, me))
        return {"contacts": contacts}

    async def op_resolve_peer(self, args: dict) -> dict:
        user_id = args.get("user_id")
        return {"peer": await self._with_session(lambda c, me: resolve_peer(c, user_id, me))}

    # ---------------------------------------------------------------- dial
    async def op_dial(self, args: dict) -> dict:
        if "peer" in args or "user_id" in args:          # there is no such parameter, by design
            raise CallError("PEER_NOT_ALLOWED")
        confirm = args.get("confirm_unknown") is True
        settings = self.settings.load()                    # FRESH read: never a cached copy
        peer = check_dial(settings, self.creds.self_id(), self.stopflag, settings.last_outcome,
                          in_call=self._busy, confirm_unknown=confirm)
        self._busy = True
        self._stop_seen = False
        call_id = f"c{int(time.time())}{secrets.token_hex(3)}"
        client = None
        try:
            stt, tts, brain, vad_factory = await self._build_engines()
            async with self._login_lock:
                client = await self.login.open_session()
            transport = self._transport_factory(client)
            if self.stopflag.is_set() or self._stop_seen:  # STOP raced the setup
                raise CallError("STOP_ACTIVE")
            cfg = self._session_cfg(settings) if self._session_cfg else SessionConfig(
                ring_timeout_s=settings.ring_timeout_s, max_call_s=settings.max_call_s, pace=self._pace)
            session = CallSession(call_id=call_id, transport=transport, peer=peer, stt=stt, tts=tts, brain=brain,
                                  vad=vad_factory(), cfg=cfg, on_event=lambda ev: self._on_call_event(call_id, ev))
            # from here on the phone may ring: until an outcome is PROVEN it stays UNKNOWN (crash-safe)
            self.settings.record_outcome("unknown", call_id)
        except BaseException:
            self._busy = False
            if client is not None:
                try:
                    await asyncio.wait_for(client.disconnect(), 5)
                except BaseException:  # noqa: BLE001
                    pass
            raise
        self._session, self._call_id = session, call_id
        self._call_task = asyncio.get_running_loop().create_task(self._run_call(session, client, call_id), name="calls-run")
        self.emit("state", call_id=call_id, state="dialing")
        return {"call_id": call_id, "accepted": True}

    async def _build_engines(self) -> tuple:
        factory = self._engines_factory
        try:
            if inspect.iscoroutinefunction(factory):
                result = await factory(self.data_dir)
            else:
                result = await asyncio.to_thread(factory, self.data_dir)   # model loading must not block STOP
                if inspect.isawaitable(result):
                    result = await result
            stt, tts, brain, vad_factory = result
        except CallError:
            raise
        except ImportError:
            raise CallError("DEPENDENCIES_MISSING") from None
        except (TypeError, ValueError):
            raise CallError("INTERNAL", detail="engines_shape") from None
        return stt, tts, brain, vad_factory

    async def _run_call(self, session: CallSession, client: Any, call_id: str) -> None:
        record = None
        try:
            record = await session.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.emit("log", level="error", code="INTERNAL", detail=type(exc).__name__)
        finally:
            outcome = record.outcome.value if record is not None and record.outcome else "unknown"
            try:
                self.settings.record_outcome(outcome, call_id)
            except Exception:  # noqa: BLE001
                pass
            if client is not None:
                try:
                    await asyncio.wait_for(client.disconnect(), 5)
                except BaseException:  # noqa: BLE001
                    pass
            self._session = None
            self._busy = False
            if record is not None:
                self.emit("record", record=record.as_dict())
            else:
                self.emit("record", record={"call_id": call_id, "outcome": "unknown", "error_code": "INTERNAL"})
            self.emit("state", call_id=call_id, state="ended")

    async def op_hangup(self, args: dict) -> dict:
        s = self._session
        if s is None:
            return {"hung_up": False}
        s.hangup("owner_hangup")
        return {"hung_up": True}

    # ---------------------------------------------------------------- stop / shutdown
    def stop_now(self, by: str = "worker_stop") -> dict:
        """Synchronous, fast path. Raises the persistent flag first, then silences the live call."""
        self._stop_seen = True
        try:
            self.stopflag.set(by)
        except OSError:
            pass
        s = self._session
        if s is not None:
            s.stop("owner_stop")
        return {"stopped": True, "had_call": s is not None}

    async def op_stop(self, args: dict) -> dict:
        return self.stop_now()

    async def _end_call(self) -> None:
        s, task = self._session, self._call_task
        if s is not None:
            await s.stop_and_wait("shutdown", STOP_WAIT_S)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), STOP_WAIT_S)
            except BaseException:  # noqa: BLE001
                pass

    async def op_shutdown(self, args: dict) -> dict:
        self._closing = True
        return {"bye": True}

    # ================================================================== serve loop
    async def serve(self, read_line: Callable[[], Awaitable[str]], write: Callable[[dict], None]) -> None:
        self._emit_fn = write
        self.emit("log", level="info", msg="worker_ready", version=MODULE_VERSION)
        while not self._closing:
            line = await read_line()
            if not line:                                    # stdin EOF: the Command Center is gone
                self._closing = True
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                write({"id": None, "ok": False, "error": CallError("INTERNAL", detail="bad_json").as_dict()})
                continue
            if not isinstance(req, dict):
                write({"id": None, "ok": False, "error": CallError("INTERNAL", detail="bad_request").as_dict()})
                continue
            if req.get("op") == "stop":                     # fast path: answered before anything else runs
                write({"id": req.get("id"), "ok": True, "result": self.stop_now("manager_stop")})
                continue
            if req.get("op") == "shutdown":
                self._closing = True
                await self._end_call()
                write({"id": req.get("id"), "ok": True, "result": {"bye": True}})
                break
            task = asyncio.get_running_loop().create_task(self._respond(req, write))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        await self._end_call()
        for task in list(self._pending):
            task.cancel()
        try:
            await self.login.cancel()
        except BaseException:  # noqa: BLE001
            pass

    async def _respond(self, req: dict, write: Callable[[dict], None]) -> None:
        resp = await self.handle(req)
        try:
            write(resp)
        except Exception:  # noqa: BLE001
            pass
