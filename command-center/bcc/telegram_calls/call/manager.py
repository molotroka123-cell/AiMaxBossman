"""CallsManager: the Command Center's side of Telegram calls (one instance per Command Center).

It starts/stops the worker subprocess (``python -I -m bcc.telegram_calls``, JSON lines), sends requests with
timeouts, re-checks the dial guard, reacts to the global STOP, keeps a small call history and runs the post-call
step. The HTTP router (bcc/features/telegram_calls.py, another work package) is a thin layer over the PUBLIC API:

    Construction
        CallsManager(data_dir, *, vault=None, spawn=None, postcall=None)   # postcall = PostCall.for_services(svc)
        attach_bus(bus) / detach_bus()      # subscribe to the existing EventBus; kind "computer.stop" -> stop()
        start_worker() / stop_worker() / shutdown()      # worker lifecycle (start is lazy on first owner action)
    Read-only (never spawn the worker)
        status()                 -> {worker, account, call, stop, settings, last_call}
        get_settings()           -> settings dict (no secrets)
        history(limit=20)        -> [call record dicts, newest first; no transcripts]
        events(since_seq=0, limit=200) -> {"events": [{seq, at, event, data}], "seq": last_seq}
    Settings / account
        set_settings(patch)      -> settings dict     (enabled, max_call_s, ring_timeout_s, record_audio, keep_transcript)
        login_credentials(api_id, api_hash) -> account dict   (encrypted in the Vault; secrets never returned)
        login_start(phone) / login_code(code) / login_password(password) / login_cancel() / logout() -> account dict
        contacts()               -> {"contacts": [{user_id, label, username}]}
        select_peer(user_id)     -> {"peer", "settings"}   (validated; NOT confirmed)
        confirm_peer(user_id)    -> settings dict
        clear_peer()             -> settings dict
    Calls   (NO peer parameter anywhere)
        dial(confirm_unknown=False) -> {"call_id", "accepted"}
        hangup()                 -> {"hung_up": bool}
        stop(by="owner")         -> {"stopped": True, ...}   sets the persistent call-STOP flag, silences + hangs up,
                                    hard-terminates the worker if it does not confirm
        resume()                 -> status dict           clears ONLY the call-STOP flag (global STOP is separate)
    Errors: every failure is a ``CallError`` (stable code from ``types.ERRORS``); ``SettingsError`` (a ValueError)
    for an invalid settings patch.

Honesty: if the worker dies (or times out) while a call may be live, a synthetic record with outcome UNKNOWN is
produced; the next dial then needs ``confirm_unknown=True``. There is no auto-redial anywhere.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..account.credentials import CredentialStore
from ..guard import check_dial
from ..settings import CallsSettings, SettingsError, SettingsStore, atomic_write_json, calls_dir
from ..stopflag import StopFlag
from ..types import CallError

REQUEST_TIMEOUT_S = 15.0
LOGIN_TIMEOUT_S = 60.0
DIAL_ACK_S = 90.0                # engine loading + session open happen before the ack
STOP_ACK_S = 2.0
STOP_GRACE_S = 6.0
HISTORY_MAX = 50
EVENTS_MAX = 1000
STREAM_LIMIT = 8 * 1024 * 1024
HISTORY_FILE = "history.json"


class WorkerHandle(Protocol):
    """What the manager needs from a worker process (real: asyncio subprocess; tests: in-process fake)."""

    async def readline(self) -> bytes: ...            # b"" at EOF
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


Spawn = Callable[[list, dict], Awaitable[WorkerHandle]]


class _ProcHandle:
    def __init__(self, proc: asyncio.subprocess.Process):
        self._p = proc

    async def readline(self) -> bytes:
        try:
            return await self._p.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            return b""

    def write(self, data: bytes) -> None:
        self._p.stdin.write(data)

    async def drain(self) -> None:
        await self._p.stdin.drain()

    def terminate(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._p.terminate()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._p.kill()

    async def wait(self) -> int:
        return await self._p.wait()


async def default_spawn(argv: list, env: dict) -> WorkerHandle:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=env, limit=STREAM_LIMIT)
    return _ProcHandle(proc)


class CallsManager:
    def __init__(self, data_dir: Path | str, *, vault: Any = None, spawn: Spawn | None = None,
                 postcall: Any = None, clock: Callable[[], float] = time.time):
        self.data_dir = Path(data_dir)
        self.settings_store = SettingsStore(self.data_dir)
        self.stopflag = StopFlag(self.data_dir)
        self.creds = CredentialStore(self.data_dir, vault)
        self._spawn = spawn or default_spawn
        self._postcall = postcall
        self._clock = clock
        self._handle: WorkerHandle | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._start_lock = asyncio.Lock()
        self._dial_lock = asyncio.Lock()
        self._active = False                       # a call may be live (dial in flight or running)
        self._active_call_id: str | None = None
        self._active_started: float | None = None
        self._record_seen = asyncio.Event()
        self._login_state: str | None = None
        self._events: deque[dict] = deque(maxlen=EVENTS_MAX)
        self._seq = 0
        self._history: deque[dict] = deque(self._load_history(), maxlen=HISTORY_MAX)
        self._bus: Any = None
        self._bus_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()

    # ================================================================== worker lifecycle
    @property
    def worker_running(self) -> bool:
        return self._handle is not None

    async def start_worker(self) -> None:
        async with self._start_lock:
            if self._handle is not None:
                return
            env = dict(os.environ)
            env["BCC_DATA_DIR"] = str(self.data_dir)
            try:
                handle = await self._spawn([sys.executable, "-I", "-m", "bcc.telegram_calls"], env)
            except CallError:
                raise
            except Exception:  # noqa: BLE001
                raise CallError("WORKER_UNAVAILABLE") from None
            self._handle = handle
            self._reader = asyncio.get_running_loop().create_task(self._read_loop(handle), name="calls-manager-reader")

    async def stop_worker(self, *, timeout: float = 8.0) -> None:
        """Owner-initiated stop of the worker (ends a live call first)."""
        handle = self._handle
        if handle is None:
            return
        try:
            await self._request("shutdown", {}, timeout, ensure=False)
        except CallError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(self._reader), timeout) if self._reader else None
        except BaseException:  # noqa: BLE001
            await self._hard_kill()

    async def shutdown(self) -> None:
        await self.detach_bus()
        await self.stop_worker()
        for t in list(self._bg):
            t.cancel()

    async def _hard_kill(self) -> None:
        handle = self._handle
        if handle is None:
            return
        handle.terminate()
        try:
            await asyncio.wait_for(handle.wait(), 2.0)
        except BaseException:  # noqa: BLE001
            handle.kill()
        if self._reader is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(self._reader), 3.0)

    # ================================================================== IPC
    async def _read_loop(self, handle: WorkerHandle) -> None:
        try:
            while True:
                raw = await handle.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "event" in msg:
                    await self._on_worker_event(msg)
                else:
                    fut = self._pending.get(msg.get("id"))
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        finally:
            if self._handle is handle:
                await self._on_worker_exit()

    async def _on_worker_exit(self) -> None:
        self._handle = None
        self._login_state = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(CallError("WORKER_UNAVAILABLE"))
        if self._active and not self._record_seen.is_set():
            now = self._clock()
            await self._on_record({"call_id": self._active_call_id or f"c{int(now)}-lost", "transport": "telegram",
                                   "started_at": self._active_started or now, "ended_at": now, "outcome": "unknown",
                                   "error_code": "WORKER_UNAVAILABLE", "state": "ended", "synthesized": True,
                                   "summary": None})
        self._push("log", {"msg": "worker_exited"})

    async def _request(self, op: str, args: dict | None = None, timeout: float = REQUEST_TIMEOUT_S, *,
                       ensure: bool = True) -> Any:
        if ensure:
            await self.start_worker()
        handle = self._handle
        if handle is None:
            raise CallError("WORKER_UNAVAILABLE")
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            try:
                handle.write(json.dumps({"id": rid, "op": op, "args": args or {}}, ensure_ascii=False).encode("utf-8") + b"\n")
                await handle.drain()
            except (OSError, ConnectionError, RuntimeError):
                raise CallError("WORKER_UNAVAILABLE") from None
            try:
                resp = await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                raise CallError("WORKER_TIMEOUT") from None
        finally:
            self._pending.pop(rid, None)
        if resp.get("ok") is True:
            return resp.get("result")
        err = resp.get("error") if isinstance(resp.get("error"), dict) else {}
        detail = err.get("detail")
        raise CallError(str(err.get("code") or "INTERNAL"), detail=detail if isinstance(detail, str) else None)

    # ================================================================== events / records
    def _push(self, event: str, data: dict) -> dict:
        self._seq += 1
        item = {"seq": self._seq, "at": self._clock(), "event": event, "data": data}
        self._events.append(item)
        return item

    async def _on_worker_event(self, msg: dict) -> None:
        name = str(msg.get("event"))
        data = {k: v for k, v in msg.items() if k != "event"}
        if name == "record":
            await self._on_record(dict(data.get("record") or {}))
            return
        if name not in ("state", "call_event", "log"):
            return
        self._push(name, data)

    async def _on_record(self, rec: dict) -> None:
        call_id = rec.get("call_id")
        if any(h.get("call_id") == call_id for h in self._history):
            return
        outcome = str(rec.get("outcome") or "unknown")
        rec["outcome"] = outcome
        self._active, self._active_call_id, self._active_started = False, None, None
        self._record_seen.set()
        with contextlib.suppress(Exception):
            self.settings_store.record_outcome(outcome, str(call_id)[:128] if call_id else None)
        if self._postcall is not None:
            try:
                rec["postcall"] = await self._postcall.run(rec)
            except Exception as exc:  # noqa: BLE001
                rec["postcall"] = {"error": type(exc).__name__}
        summary = rec.get("summary")
        rec["summary"] = ({"generated_by": summary.get("generated_by"), "tasks": len(summary.get("agreed_tasks") or [])}
                          if isinstance(summary, dict) else None)
        self._history.appendleft(rec)
        self._save_history()
        self._push("record", {"call_id": call_id, "outcome": outcome, "error_code": rec.get("error_code")})

    def _load_history(self) -> list[dict]:
        try:
            data = json.loads((calls_dir(self.data_dir) / HISTORY_FILE).read_text(encoding="utf-8"))
            return [h for h in data if isinstance(h, dict)][:HISTORY_MAX]
        except (OSError, ValueError, TypeError):
            return []

    def _save_history(self) -> None:
        with contextlib.suppress(OSError):
            atomic_write_json(calls_dir(self.data_dir) / HISTORY_FILE, list(self._history))

    # ================================================================== read-only API
    def get_settings(self) -> dict:
        return self.settings_store.load().as_dict()

    def _account_state(self) -> str:
        if self._login_state in ("code_sent", "password_needed"):
            return self._login_state
        st = self.creds.status()
        if st["has_session"]:
            return "ready"
        return "logged_out" if st["has_credentials"] else "no_credentials"

    def _account(self) -> dict:
        return {"state": self._account_state(), **self.creds.status()}

    def status(self) -> dict:
        s = self.settings_store.load()
        return {"worker": {"running": self.worker_running}, "account": self._account(),
                "call": ({"call_id": self._active_call_id, "active": True} if self._active else None),
                "stop": self.stopflag.info(), "settings": s.as_dict(),
                "last_call": self._history[0] if self._history else None}

    def history(self, limit: int = 20) -> list[dict]:
        return list(self._history)[:max(1, min(int(limit), HISTORY_MAX))]

    def events(self, since_seq: int = 0, limit: int = 200) -> dict:
        items = [e for e in self._events if e["seq"] > since_seq][:max(1, min(int(limit), EVENTS_MAX))]
        return {"events": items, "seq": self._seq}

    # ================================================================== settings / account
    def set_settings(self, patch: dict) -> dict:
        return self.settings_store.update(patch).as_dict()

    def login_credentials(self, api_id: Any, api_hash: Any) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        self.creds.save_api(api_id, api_hash)
        return self._account()

    async def _login_op(self, op: str, args: dict) -> dict:
        res = await self._request(op, args, LOGIN_TIMEOUT_S)
        state = (res or {}).get("state")
        self._login_state = state if state in ("code_sent", "password_needed") else None
        return self._account()

    async def login_start(self, phone: str) -> dict:
        return await self._login_op("login_start", {"phone": phone})

    async def login_code(self, code: str) -> dict:
        return await self._login_op("login_code", {"code": code})

    async def login_password(self, password: str) -> dict:
        return await self._login_op("login_password", {"password": password})

    async def login_cancel(self) -> dict:
        if not self.worker_running:
            self._login_state = None
            return self._account()
        return await self._login_op("login_cancel", {})

    async def logout(self) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        if self.worker_running or self.creds.has_session():
            await self._login_op("logout", {})
        self.creds.clear_all()
        self.settings_store.clear_peer()
        self._login_state = None
        return self._account()

    async def contacts(self) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        return await self._request("contacts", {}, LOGIN_TIMEOUT_S)

    async def select_peer(self, user_id: int) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        if user_id == self.creds.self_id():
            raise CallError("PEER_IS_SELF")
        res = await self._request("resolve_peer", {"user_id": user_id}, LOGIN_TIMEOUT_S)
        peer = (res or {}).get("peer") or {}
        if peer.get("user_id") != user_id:
            raise CallError("PEER_INVALID")
        settings = self.settings_store.set_peer(user_id, peer.get("label") or "")
        return {"peer": peer, "settings": settings.as_dict()}

    def confirm_peer(self, user_id: int) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        if user_id == self.creds.self_id():
            raise CallError("PEER_IS_SELF")
        try:
            return self.settings_store.confirm_peer(user_id).as_dict()
        except SettingsError:
            raise CallError("PEER_NOT_SELECTED") from None

    def clear_peer(self) -> dict:
        if self._active:
            raise CallError("CALL_IN_PROGRESS")
        return self.settings_store.clear_peer().as_dict()

    # ================================================================== calls
    def _guard(self, confirm_unknown: bool) -> None:
        s: CallsSettings = self.settings_store.load()
        check_dial(s, self.creds.self_id(), self.stopflag, s.last_outcome, self._active, confirm_unknown is True)

    async def dial(self, confirm_unknown: bool = False) -> dict:
        async with self._dial_lock:
            self._guard(confirm_unknown)                     # 1st check (the worker repeats it from a fresh read)
            await self.start_worker()
            self._record_seen.clear()
            self._active, self._active_started, self._active_call_id = True, self._clock(), None
            try:
                res = await self._request("dial", {"confirm_unknown": confirm_unknown is True}, DIAL_ACK_S, ensure=False)
            except CallError as exc:
                if exc.code == "WORKER_TIMEOUT":
                    await self._hard_kill()                  # cannot prove the phone did not ring -> UNKNOWN record
                elif exc.code != "WORKER_UNAVAILABLE":
                    self._active, self._active_started = False, None    # refused before any ring
                raise
            self._active_call_id = (res or {}).get("call_id")
            return {"call_id": self._active_call_id, "accepted": True}

    async def hangup(self) -> dict:
        if not self.worker_running:
            return {"hung_up": False}
        return await self._request("hangup", {}, REQUEST_TIMEOUT_S, ensure=False)

    async def stop(self, by: str = "owner") -> dict:
        """STOP from any surface. Persistent flag first, then silence + hangup, hard terminate as fallback."""
        self.stopflag.set(by)
        self._push("log", {"msg": "stop", "by": str(by)[:20]})
        handle = self._handle
        if handle is None:
            return {"stopped": True, "worker": "not_running"}
        try:
            res = await self._request("stop", {}, STOP_ACK_S, ensure=False)
        except CallError:
            await self._hard_kill()
            return {"stopped": True, "worker": "terminated"}
        if self._active:
            try:
                await asyncio.wait_for(self._record_seen.wait(), STOP_GRACE_S)
            except asyncio.TimeoutError:
                await self._hard_kill()                      # worker did not finish the hangup: terminate it
                return {"stopped": True, "worker": "terminated"}
        return {"stopped": True, "worker": "confirmed", "had_call": bool((res or {}).get("had_call"))}

    async def resume(self) -> dict:
        """Owner's «Продолжить»: clears the call-STOP flag only (never the global computer STOP)."""
        self.stopflag.clear()
        return self.status()

    # ================================================================== global STOP (bus)
    def attach_bus(self, bus: Any) -> None:
        if self._bus_task is not None:
            return
        self._bus = bus
        self._bus_task = asyncio.get_running_loop().create_task(self._bus_loop(bus), name="calls-manager-bus")

    async def detach_bus(self) -> None:
        task, self._bus_task = self._bus_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _bus_loop(self, bus: Any) -> None:
        q = bus.subscribe()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 5.0)
                except asyncio.TimeoutError:
                    if not bus.is_subscribed(q):             # dropped as a lagging subscriber: resubscribe
                        q = bus.subscribe()
                        if self.stopflag.global_stop_set() and self._active:
                            await self.stop("global_stop")
                    continue
                if isinstance(msg, dict) and msg.get("kind") == "computer.stop":
                    await self.stop("global_stop")
        finally:
            bus.unsubscribe(q)
