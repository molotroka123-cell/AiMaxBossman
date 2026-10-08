"""CallsManager: the Command Center side of the calls worker (one instance per backend process).

The MTProto client and the native VoIP engine live in ONE worker process (``python -I -m bcc.telegram_calls``, see
``call/worker.py``). The manager starts it ONLY on an owner action (login / contacts / peer check / dial / selftest /
logout), never at server start, and talks to it over stdio JSON-lines.

What the manager guarantees (each has a test in ``tests/telegram_calls/test_manager.py``):

* the durable STOP file is written FIRST by ``stop()``, then the fast ``stop`` op is sent; without an acknowledgement in
  ~3 s (or with an unconfirmed hangup that does not clear) the worker process is terminated;
* ``dial`` re-evaluates ``account.guard.check_dial`` here, with fresh settings from disk, BEFORE anything reaches the worker;
  a dial request has no peer parameter: the peer is whatever the saved settings say;
* a worker that dies during a call produces a synthesized record with outcome ``UNKNOWN`` (the call may still be live at
  Telegram), ``CallState.note_call_finished(id, UNKNOWN)`` is written, and nothing is ever redialled;
* secrets in the login ops (phone, code, 2FA password) travel only over the pipe; they are never logged, never put in an
  event, never returned; only masks and stable error codes leave this module;
* events for polling live in a bounded in-memory ring with a monotonically growing ``seq`` and carry no text, no
  transcripts, no secrets (values are redacted, banned keys dropped).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import deps
from ..account.credentials import CredentialStore
from ..account.guard import DialContext, check_dial
from ..account.stopflag import CallState
from ..hardening import check_calls_home, redact
from ..settings import CallSettings, load_settings, save_settings
from ..types import AccountState, CallError, ERRORS, Outcome, latency_stats

log = logging.getLogger("bcc.telegram_calls.manager")

MODE_ENV = "BOSSMAN_CALLS_MODE"
OFFLINE_MODE = "offline_test"
HOME_ENV = "BOSSMAN_TELEGRAM_CALLS_HOME"
DEFAULT_ARGV = (sys.executable, "-I", "-m", "bcc.telegram_calls")
RING_SIZE = 500
_MAX_LINE = 4 * 1024 * 1024
_BANNED_EVENT_KEYS = frozenset({
    "text", "transcript", "content", "reply", "utterance", "prompt", "system_prompt", "messages", "password",
    "secret", "token", "api_key", "api_hash", "phone", "session", "cookie", "authorization"})
_OUTCOME_ERROR = {"declined": "CALL_DECLINED", "busy": "CALL_BUSY", "no_answer": "CALL_NO_ANSWER",
                  "connection_lost": "CONNECTION_LOST", "max_duration": "MAX_DURATION",
                  "silence_timeout": "SILENCE_TIMEOUT"}
SELFTEST_SCENARIOS = ("all", "basic", "barge_in", "echo", "stop", "no_redial")

Callback = Callable[..., Any]


def outcome_error(outcome: str | None, error_code: str | None) -> dict | None:
    """The owner-facing (message + hint) description of how a call ended, or None when it ended normally."""
    code = error_code if error_code in ERRORS else _OUTCOME_ERROR.get(str(outcome or ""))
    if code is None and outcome == "unknown":
        code = "UNCERTAIN_PREVIOUS_CALL"
    return CallError(code).as_dict() if code else None


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)[:120]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return None


def scrub_event(data: dict) -> dict:
    """A worker call_event -> the polling shape: scalar values only, redacted, text-carrying keys dropped."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("seq", "kind", "at") or str(key).lower() in _BANNED_EVENT_KEYS:
            continue
        clean = _scrub(value)
        if clean is not None or value is None:
            out[str(key)[:40]] = clean
    return out


class CallsManager:
    def __init__(self, data_dir: Path, *, home: Path | None = None, vault: Any = None,
                 worker_argv: list[str] | tuple[str, ...] | None = None, env: dict[str, str] | None = None,
                 on_record: Callback | None = None, on_state: Callback | None = None,
                 stop_ack_s: float = 3.0, hello_timeout_s: float = 25.0, clock: Callable[[], float] = time.time):
        self.data_dir = Path(data_dir)
        self.home = Path(home) if home is not None else self.data_dir / "telegram-calls"
        self.store = CredentialStore(self.home, vault=vault)
        self.state = CallState(self.home)
        self.argv = list(worker_argv) if worker_argv else list(DEFAULT_ARGV)
        self._env_override = dict(env) if env is not None else None
        self.on_record = on_record
        self.on_state = on_state
        self.stop_ack_s = stop_ack_s
        self.hello_timeout_s = hello_timeout_s
        self._clock = clock
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._spawn_lock = asyncio.Lock()
        self._dial_lock = asyncio.Lock()
        self._selftest_lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()
        self._ring: deque[dict] = deque(maxlen=RING_SIZE)
        self._seq = 0
        self._hello: dict | None = None
        self._active_call: dict | None = None
        self._dial_pending = False
        self._last_record: dict | None = None
        self._selftest_running = False
        self._stop_latched = False              # in memory: the dial stays blocked even if the durable STOP file cannot be written
        self._cache: dict[str, tuple[float, Any]] = {}
        self.stderr_lines = 0
        self._permissions_checked = False       # first status() tightens a restored/copied folder once

    # ------------------------------------------------------------------ facts
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def mode(self) -> str:
        if self._hello and self._hello.get("mode"):
            return str(self._hello["mode"])
        env = self._env_override if self._env_override is not None else os.environ
        return OFFLINE_MODE if env.get(MODE_ENV, "") == OFFLINE_MODE else "telegram"

    @property
    def offline(self) -> bool:
        return self.mode == OFFLINE_MODE

    @property
    def active_call(self) -> dict | None:
        return dict(self._active_call) if self._active_call else None

    @property
    def busy(self) -> bool:
        """A call is live OR a dial is in flight: settings / peer / credentials must not change under it."""
        return self._active_call is not None or bool(self._dial_pending)

    @property
    def dial_pending(self) -> bool:
        """A dial is in flight (the phone may ring before the call shows up as active): the owner STOP must see it too."""
        return bool(self._dial_pending)

    def settings(self) -> CallSettings:
        return load_settings(self.home)          # fresh, never cached

    def save_settings(self, settings: CallSettings) -> None:
        save_settings(settings, self.home)

    # ------------------------------------------------------------------ process
    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ if self._env_override is None else self._env_override)
        env["BCC_DATA_DIR"] = str(self.data_dir)
        if self.home != self.data_dir / "telegram-calls":
            env[HOME_ENV] = str(self.home)
        else:
            env.pop(HOME_ENV, None)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    async def ensure_worker(self) -> None:
        """Start the worker if it is not running. ONLY owner actions may call this."""
        async with self._spawn_lock:
            if self.running:
                return
            old = self._reader_task
            if old is not None and not old.done():           # let the previous worker's exit handling finish first
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    await asyncio.wait_for(old, 3.0)
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.repair_unreadable)
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=self._child_env(), limit=_MAX_LINE, **kwargs)
            except (OSError, ValueError) as exc:
                raise CallError("WORKER_UNAVAILABLE", detail=type(exc).__name__) from None
            self._proc = proc
            self._hello = None
            self._reader_task = asyncio.get_running_loop().create_task(self._reader(proc), name="calls-manager-reader")
            self._stderr_task = asyncio.get_running_loop().create_task(self._drain_stderr(proc), name="calls-manager-stderr")
        try:
            self._hello = await self._request_raw("hello", {}, timeout=self.hello_timeout_s)
        except CallError:
            await self._terminate()
            raise CallError("WORKER_UNAVAILABLE", detail="hello_failed") from None
        self._event("worker", state="started")

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        # stderr is only drained (a full pipe would stall the worker); its text is never stored or shown.
        try:
            while proc.stderr is not None and await proc.stderr.readline():
                self.stderr_lines += 1
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    async def _reader(self, proc: asyncio.subprocess.Process) -> None:
        try:
            while proc.stdout is not None:
                try:
                    raw = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    continue                       # an oversized line is dropped, the stream stays usable
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("worker reader stopped unexpectedly")
        finally:
            await self._on_exit(proc)

    def _spawn_bg(self, coro: Awaitable[Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for post-processing tasks (post-call hooks). Used by tests and shutdown."""
        pending = [t for t in self._bg if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    # ------------------------------------------------------------------ protocol
    def _dispatch(self, msg: dict) -> None:
        rid = msg.get("id")
        if rid is not None:
            fut = self._pending.pop(str(rid), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        kind = msg.get("event")
        if kind == "call_event" and isinstance(msg.get("data"), dict):
            self._ingest_call_event(msg["data"])
        elif kind == "record" and isinstance(msg.get("record"), dict):
            self._ingest_record(msg["record"])
        elif kind == "selftest":
            self._event("selftest", scenario=_scrub(msg.get("scenario")), verdict=_scrub(msg.get("verdict")))

    async def _request_raw(self, op: str, args: dict | None, *, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise CallError("WORKER_UNAVAILABLE")
        rid = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        line = (json.dumps({"id": rid, "op": op, "args": args or {}}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            proc.stdin.write(line)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._pending.pop(rid, None)
            raise CallError("WORKER_UNAVAILABLE") from None
        try:
            reply = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise CallError("WORKER_TIMEOUT", detail=op) from None
        if not reply.get("ok"):
            err = reply.get("error") if isinstance(reply.get("error"), dict) else {}
            code = str(err.get("code") or "INTERNAL")
            raise CallError(code, detail=err.get("detail") if isinstance(err.get("detail"), str) else None)
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    async def request(self, op: str, args: dict | None = None, timeout: float = 30.0, *, spawn: bool = True) -> dict:
        """One worker round trip. ``spawn=False`` never starts the worker (read-only callers)."""
        if not self.running:
            if not spawn:
                raise CallError("WORKER_UNAVAILABLE")
            await self.ensure_worker()
        return await self._request_raw(op, args, timeout=timeout)

    # ------------------------------------------------------------------ events / records
    def _event(self, kind: str, **data: Any) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "at": round(self._clock(), 3), "kind": kind, **{k: v for k, v in data.items()}}
        self._ring.append(ev)
        return ev

    def events(self, after: int = 0, limit: int = 200) -> dict:
        items = [e for e in self._ring if e["seq"] > after][-max(1, min(limit, RING_SIZE)):]
        return {"events": items, "last_seq": self._seq}

    def _ingest_call_event(self, data: dict) -> None:
        kind = str(data.get("kind") or "log")[:24]
        clean = scrub_event(data)
        ac = self._active_call
        if kind == "state" and clean.get("state") != "ended":
            if ac is None:
                ac = self._active_call = {"call_id": None, "state": None, "phase": None, "transport": None,
                                          "models": {}, "started_at": self._clock(), "latencies": []}
            ac["state"] = clean.get("state")
        elif kind == "phase" and ac is not None:
            ac["phase"] = clean.get("phase")
        elif kind == "metric" and ac is not None and isinstance(clean.get("response_latency_ms"), (int, float)):
            ac["latencies"].append(float(clean["response_latency_ms"]))
        self._event(kind, **clean)
        if kind in ("state", "phase") and self.on_state is not None:
            self._spawn_bg(self._safe(self.on_state, kind, self.call_view()))

    def _ingest_record(self, rec: dict) -> None:
        self._active_call = None
        self._dial_pending = False
        self._last_record = rec
        self._event("record", call_id=_scrub(rec.get("call_id")), outcome=_scrub(rec.get("outcome")),
                    error_code=_scrub(rec.get("error_code")))
        self._spawn_bg(self._after_record(rec))

    async def _after_record(self, rec: dict) -> None:
        await asyncio.to_thread(self.heal_permissions)
        if self.on_record is not None:
            await self._safe(self.on_record, rec)

    @staticmethod
    async def _safe(fn: Callback, *args: Any) -> None:
        try:
            result = fn(*args)
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an observer never breaks the call path
            log.warning("calls hook failed: %s", type(exc).__name__)

    def call_view(self) -> dict | None:
        ac = self._active_call
        if ac is None:
            return None
        return {"call_id": ac.get("call_id"), "state": ac.get("state"), "phase": ac.get("phase"),
                "transport": ac.get("transport"), "models": dict(ac.get("models") or {}),
                "started_at": ac.get("started_at"), "latency_ms": latency_stats(list(ac.get("latencies") or []))}

    async def _on_exit(self, proc: asyncio.subprocess.Process) -> None:
        if self._proc is not proc:
            return
        self._proc = None
        self._hello = None
        call = self._active_call                                       # captured BEFORE any await: a failing dial() resets its flags
        dial_pending = self._dial_pending
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(CallError("WORKER_UNAVAILABLE"))
        self._pending.clear()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 2)
        in_flight = None
        with contextlib.suppress(Exception):
            in_flight = self.state._state().get("in_flight")           # written by the worker BEFORE the phone can ring
        if call is not None or (dial_pending and in_flight):
            call_id = str((call or {}).get("call_id") or in_flight or "c-lost")
            await self._synthesize_unknown(call_id, call or {})
        self._active_call = None
        self._dial_pending = False
        self._event("worker", state="exited")

    async def _synthesize_unknown(self, call_id: str, call: dict) -> None:
        """The worker died with a call in flight: outcome UNKNOWN (never COMPLETED, never a redial)."""
        now = self._clock()
        settings: CallSettings | None = None
        with contextlib.suppress(Exception):
            settings = self.settings()
        rec = {"call_id": call_id, "transport": call.get("transport") or ("loopback" if self.offline else "telegram"),
               "peer_user_id": (settings.peer_user_id if settings else None) or 0,
               "started_at": call.get("started_at") or now, "ended_at": now, "outcome": Outcome.UNKNOWN.value,
               "error_code": "WORKER_UNAVAILABLE", "state": "ended", "turns": [],
               "latency_ms": latency_stats(list(call.get("latencies") or [])), "models": dict(call.get("models") or {}),
               "counters": {"worker_died": 1}, "recorded_audio": False, "summary": None, "synthesized": True}
        with contextlib.suppress(Exception):
            self.state.note_call_finished(call_id, Outcome.UNKNOWN)
        with contextlib.suppress(OSError):
            self.state.append_history(rec)
        self._active_call = None
        self._dial_pending = False
        self._last_record = rec
        self._event("record", call_id=call_id, outcome=Outcome.UNKNOWN.value, error_code="WORKER_UNAVAILABLE")
        self._spawn_bg(self._after_record(rec))

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 2.0)
        task = self._reader_task
        if task is not None and task is not asyncio.current_task():
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(task, 3.0)          # the reader's finally runs the exit handling

    # ------------------------------------------------------------------ owner actions: account
    def save_credentials(self, api_id: int, api_hash: str) -> dict:
        if self.busy:
            raise CallError("CALL_IN_PROGRESS")
        self.store.save_api(api_id, api_hash)
        return self.store.public()

    async def credentials_changed(self) -> None:
        """A running worker holds a client built from the OLD api id/hash: drop it, the next action starts a fresh one."""
        if self.running and self._active_call is None:
            await self.shutdown()

    async def login_start(self, phone: str) -> dict:
        return await self.request("login.start", {"phone": phone}, timeout=60.0)

    async def login_code(self, code: str) -> dict:
        return await self.request("login.code", {"code": code}, timeout=60.0)

    async def login_password(self, password: str) -> dict:
        return await self.request("login.password", {"password": password}, timeout=60.0)

    async def logout(self) -> dict:
        if self.busy:
            raise CallError("CALL_IN_PROGRESS")
        return await self.request("logout", {}, timeout=30.0)

    async def contacts(self, query: str = "", limit: int = 50) -> dict:
        return await self.request("contacts", {"query": query, "limit": limit}, timeout=60.0)

    async def peer_lookup(self, *, user_id: int | None = None, username: str | None = None) -> dict:
        args = {"username": username} if username else {"user_id": int(user_id or 0)}
        return await self.request("peer.lookup", args, timeout=45.0)

    # ------------------------------------------------------------------ owner actions: call
    def _dial_context(self, *, global_stop: bool) -> DialContext:
        try:
            creds = self.store.load()
        except CallError:
            account, me_id = AccountState.ERROR, None
        else:
            account = (AccountState.NO_CREDENTIALS if not creds.has_api
                       else AccountState.READY if creds.session else AccountState.LOGGED_OUT)
            me_id = creds.me_id or None
        deps_ok = True if self.offline else bool(deps.probe()["ready_for_telegram_call"])
        return DialContext(account=account, me_id=me_id, active_call=self._active_call is not None or self._dial_pending,
                           stop_active=self.state.stop_is_set() or self._stop_latched or bool(global_stop),
                           uncertain_previous=self.state.is_uncertain(), deps_ok=deps_ok)

    async def dial(self, *, confirm_unknown: bool = False, global_stop: bool = False) -> dict:
        """The one way to ring the phone. No peer parameter exists: the peer is the saved one, checked twice."""
        async with self._dial_lock:
            if self._selftest_running:
                raise CallError("CALL_IN_PROGRESS")
            try:
                settings = self.settings()
            except (ValueError, OSError):
                raise CallError("NOT_ENABLED", detail="settings_unreadable") from None    # a corrupt file is never 'enabled'
            check_dial(settings, self._dial_context(global_stop=global_stop), confirm_unknown=bool(confirm_unknown))
            self._dial_pending = True
            try:
                result = await self.request("dial", {"confirm_unknown": bool(confirm_unknown), "global_stop": bool(global_stop)},
                                            timeout=90.0)
            except CallError:
                # A refusal from the worker's own guard means nothing was dialled; a timeout / death is settled by the
                # worker-exit handling (in_flight in state.json), never by guessing here.
                if self._active_call is None and self.running:
                    self._dial_pending = False              # (a dead worker is settled by _on_exit, which needs the flag)
                elif not self.running and self._reader_task is not None:
                    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                        await asyncio.wait_for(self._reader_task, 3.0)     # let the exit handling settle the call first
                elif not self.running and self._active_call is None:
                    self._dial_pending = False              # the worker could not even be started: nothing was dialled
                raise
            call = self._active_call or {"started_at": self._clock(), "latencies": [], "phase": None, "state": "dialing"}
            call.update({"call_id": result.get("call_id"), "transport": result.get("transport"),
                         "models": dict(result.get("models") or {})})
            if self._last_record is None or self._last_record.get("call_id") != result.get("call_id"):
                self._active_call = call
            self._dial_pending = False
            self._event("dial", call_id=_scrub(result.get("call_id")), transport=_scrub(result.get("transport")))
            return {"call_id": result.get("call_id"), "accepted": True, "transport": result.get("transport"),
                    "models": dict(result.get("models") or {}), "notes": _safe_notes(result.get("notes"))}

    async def hangup(self) -> dict:
        if not self.running:
            return {"ended": False}
        return await self._request_raw("hangup", {}, timeout=8.0)

    async def stop(self, by: str = "owner") -> dict:
        """STOP: the durable file first, then the fast op, then (no ack in time / hangup unconfirmed) the process.

        A failing file write (disk, ACL) never keeps the call on the line: the dial is latched in memory, the hangup still runs and
        the reply says ``stop_flag: false`` so nobody believes the STOP survives a restart."""
        self._stop_latched = True
        persisted = True
        try:
            self.state.set_stop(by[:40] or "owner")
        except OSError:
            persisted = False
            log.warning("calls STOP file could not be written")
        out: dict[str, Any] = {"stop_flag": persisted, "worker": "not_running", "hangup_confirmed": None, "terminated": False}
        self._event("stop", by=by[:40])
        try:
            return await self._stop_worker(by, out)
        finally:                                                   # the ACL check runs AFTER the hangup, never in front of it
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.heal_permissions)

    async def _stop_worker(self, by: str, out: dict[str, Any]) -> dict:
        if not self.running:
            return out
        out["worker"] = "running"
        try:
            res = await self._request_raw("stop", {"reason": by[:40] or "owner", "timeout": max(0.5, self.stop_ack_s - 1.0)},
                                          timeout=self.stop_ack_s)
            out["hangup_confirmed"] = bool(res.get("hangup_confirmed"))
        except CallError:
            await self._terminate()
            out["terminated"] = True
            return out
        if out["hangup_confirmed"] is False and self._active_call is not None:
            for _ in range(10):                                    # up to 1 s for the worker to finish the teardown
                if self._active_call is None:
                    break
                await asyncio.sleep(0.1)
            if self._active_call is not None:
                await self._terminate()
                out["terminated"] = True
        return out

    async def resume(self) -> dict:
        self.state.clear_stop()
        self._stop_latched = False
        if self.running:
            with contextlib.suppress(CallError):
                await self._request_raw("resume", {}, timeout=5.0)
        self._event("resume")
        return {"stop_flag": self.state.stop_is_set()}

    async def selftest(self, scenario: str = "all") -> dict:
        if scenario not in SELFTEST_SCENARIOS:
            raise CallError("INTERNAL", detail="unknown_scenario")
        if self._selftest_lock.locked() or self._active_call is not None or self._dial_pending:
            raise CallError("CALL_IN_PROGRESS")
        async with self._selftest_lock:
            self._selftest_running = True
            try:
                return await self.request("selftest", {"scenario": scenario}, timeout=420.0)
            finally:
                self._selftest_running = False

    async def shutdown(self) -> None:
        """Stop the worker (and any call) and release everything. Safe to call twice."""
        if self.running:
            with contextlib.suppress(CallError):
                await self._request_raw("shutdown", {}, timeout=6.0)
            proc = self._proc
            if proc is not None and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), 3.0)
                except asyncio.TimeoutError:
                    await self._terminate()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done() and task is not asyncio.current_task():
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, 3.0)
        await self.drain(3.0)

    # ------------------------------------------------------------------ history
    def history(self, limit: int = 20) -> list[dict]:
        return self.state.history(max(1, min(int(limit), 200)))

    def history_entry(self, call_id: str) -> dict | None:
        return next((r for r in self.state.history(200) if r.get("call_id") == call_id), None)

    # ------------------------------------------------------------------ permissions (ACL of what we own)
    def _own_files(self) -> list[Path]:
        return [self.home, *(self.home / n for n in ("credentials.enc", "config.json", "state.json", "history.jsonl",
                                                     "worker.log", "STOP"))]

    def heal_permissions(self) -> list[str]:
        """Owner-only permissions for every file of this module. Returns the names that had to be tightened."""
        from ..hardening import check_owner_only, restrict_to_owner
        healed: list[str] = []
        for path in self._own_files():
            if not path.exists() or check_owner_only(path).ok:
                continue
            try:
                if restrict_to_owner(path):      # a directory keeps an inheritable owner ACE: its files stay readable
                    healed.append(path.name)
            except OSError:
                continue
        return healed

    def repair_unreadable(self) -> list[str]:
        """Cheap pre-flight of the worker start: a file the owner cannot even open (an empty DACL left by an older build or
        a copy/restore of the folder) is given back to the owner, so the worker never dies on its own config."""
        from ..hardening import repair_unreadable
        return repair_unreadable(p for p in self._own_files() if p != self.home)

    def acl_report(self) -> list[dict]:
        key_path = getattr(getattr(self.store, "_vault", None), "path", None)
        secret_key = Path(key_path) if key_path else self.data_dir / "secret.key"
        return [{"path": Path(r.path).name or "telegram-calls", "ok": r.ok, "detail": r.detail, "platform": r.platform}
                for r in check_calls_home(self.home, secret_key)]

    # ------------------------------------------------------------------ status
    async def _cached(self, key: str, ttl: float, fn: Callable[[], Any], *, thread: bool = False) -> Any:
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = await asyncio.to_thread(fn) if thread else fn()
        self._cache[key] = (now, value)
        return value

    def invalidate_cache(self) -> None:
        self._cache.clear()

    def _local_account(self, creds: dict) -> str:
        if creds.get("unreadable"):
            return AccountState.ERROR.value
        if not creds.get("has_api"):
            return AccountState.NO_CREDENTIALS.value
        return AccountState.READY.value if creds.get("has_session") else AccountState.LOGGED_OUT.value

    async def status(self, *, global_stop: bool = False) -> dict:
        """Local facts merged with the worker's own status when it is running. Never starts the worker."""
        if not self._permissions_checked:
            # A folder that was copied or restored (backup, new machine) carries the default umask: tighten it before
            # anything reads it, not only after the first call or `doctor`. Once per process, off the event loop.
            self._permissions_checked = True
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.heal_permissions)
        # The panel polls this every 1.5 s: the file reads (config.json, credentials.enc + vault decrypt, history.jsonl) and the
        # package probe run in a worker thread, never on the event loop that also serves every other request and the worker pipe.
        settings_error: str | None = None
        try:
            settings = await asyncio.to_thread(self.settings)
        except (ValueError, OSError):
            settings, settings_error = CallSettings(), "settings_unreadable"
        creds = await asyncio.to_thread(self.store.public)
        worker_status: dict | None = None
        worker_error: str | None = None
        if self.running:
            try:
                worker_status = await self._request_raw("status", {}, timeout=2.0)
            except CallError as exc:
                worker_error = exc.code
        account_state = self._local_account(creds)
        pending_phone = None
        if worker_status is not None:
            acct = worker_status.get("account") or {}
            if isinstance(acct.get("state"), str) and acct["state"] in {s.value for s in AccountState}:
                account_state = acct["state"]
            pending_phone = acct.get("pending_phone")
        probe = await self._cached("deps", 10.0, deps.probe, thread=True)
        acl = await self._cached("acl", 30.0, self.acl_report, thread=True)
        last = self._last_record or ((await asyncio.to_thread(self.state.history, 1)) or [None])[0]
        call = self.call_view()
        if call is None and worker_status and isinstance(worker_status.get("call"), dict):
            wc = worker_status["call"]
            call = {"call_id": wc.get("call_id"), "state": wc.get("state"), "phase": wc.get("phase"),
                    "transport": wc.get("transport"), "models": dict(wc.get("models") or {}),
                    "started_at": wc.get("started_at"), "latency_ms": wc.get("latency_ms")}
        offline = self.offline
        transport = ((call or {}).get("transport") or (last or {}).get("transport")
                     or ("loopback" if offline else "telegram"))
        models = (call or {}).get("models") or (last or {}).get("models") or {}
        latency = (call or {}).get("latency_ms") or (last or {}).get("latency_ms") or latency_stats([])
        last_error = outcome_error((last or {}).get("outcome"), (last or {}).get("error_code")) if last else None
        peer = settings.peer
        stop_call = self.state.stop_is_set() or self._stop_latched
        return {
            "mode": self.mode,
            "transport": transport,
            "test_label": bool(offline or transport == "loopback"),
            "enabled": settings.enabled,
            "settings": {k: v for k, v in settings.to_json().items() if k != "extra"},
            "settings_error": settings_error,
            "peer": {"user_id": peer.user_id, "label": peer.label} if peer else None,
            "account": {"state": account_state, "has_api": bool(creds.get("has_api")), "api_id": creds.get("api_id"),
                        "phone": creds.get("phone"), "has_session": bool(creds.get("has_session")),
                        "unreadable": bool(creds.get("unreadable")), "pending_phone": pending_phone},
            "worker": {"running": self.running, "error": worker_error, "version": (self._hello or {}).get("version")},
            "call": call,
            "call_active": call is not None,
            "stop": {"call": stop_call, "global": bool(global_stop), "active": stop_call or bool(global_stop)},
            "uncertain_previous": self.state.is_uncertain(call_in_progress=call is not None or self._dial_pending),
            "last_call": _last_call_view(last),
            "last_error": last_error,
            "latency": latency,
            "models": models,
            "selftest_running": self._selftest_running,
            "deps": {"ready": bool(probe.get("ready_for_telegram_call")), "missing": list(probe.get("missing_required") or []),
                     "packages": {k: bool(v.get("installed")) for k, v in (probe.get("packages") or {}).items()}},
            "acl": {"ok": all(r["ok"] for r in acl), "problems": [r for r in acl if not r["ok"]]},
            "last_seq": self._seq,
        }

    # ------------------------------------------------------------------ doctor
    async def doctor(self, *, global_stop: bool = False) -> list[dict]:
        """Local diagnostics only: no Telegram, no call. Rows {check, status PASS|WARN|BLOCKED, detail, remedy}."""
        rows: list[dict] = []

        def row(check: str, status: str, detail: str, remedy: str = "") -> None:
            rows.append({"check": check, "status": status, "detail": detail, "remedy": remedy})

        offline = self.offline
        probe = await asyncio.to_thread(deps.probe)
        missing = list(probe.get("missing_required") or [])
        if not missing:
            row("Зависимости", "PASS", "все обязательные пакеты звонков установлены")
        elif offline:
            row("Зависимости", "WARN", "не установлены: " + ", ".join(missing) + " (тестовый режим их не требует)",
                "Для настоящего звонка выполните: bossman call install")
        else:
            row("Зависимости", "BLOCKED", "не установлены: " + ", ".join(missing), "Выполните: bossman call install")
        if offline:
            row("Движок звонков", "WARN", "не проверялся: включён тестовый режим без Telegram",
                "Настоящий движок проверяется вне тестового режима")
        elif missing:
            row("Движок звонков", "BLOCKED", "нельзя собрать движок без зависимостей", "Выполните: bossman call install")
        else:
            selfcheck = await asyncio.to_thread(deps.engine_selfcheck)
            if selfcheck.get("ok"):
                row("Движок звонков", "PASS", "движок собирается (без сети, без звонка)")
            else:
                row("Движок звонков", "BLOCKED", "не собирается: " + str(selfcheck.get("reason") or "неизвестно"),
                    "Переустановите: bossman call install")
        healed = await asyncio.to_thread(self.heal_permissions)
        acl = await asyncio.to_thread(self.acl_report)
        self.invalidate_cache()
        bad = [r for r in acl if not r["ok"]]
        if bad:
            row("Права доступа к файлам", "BLOCKED", "; ".join(f"{r['path']}: {r['detail']}" for r in bad),
                "Оставьте доступ только владельцу (chmod 600 / icacls) и повторите проверку")
        elif healed:
            row("Права доступа к файлам", "WARN", "были шире нужного и исправлены: " + ", ".join(healed),
                "Проверка выполнена заново: сейчас доступ только у владельца")
        else:
            row("Права доступа к файлам", "PASS", "доступ к данным звонков только у владельца")
        row("Ключ шифрования", *self._vault_row())
        stop_call = self.state.stop_is_set()
        if stop_call or global_stop:
            row("STOP", "WARN", "звонки заблокированы: " + ("STOP звонков" if stop_call else "общий STOP Bossman"),
                "Снимите вручную («Продолжить»), когда будете готовы")
        else:
            row("STOP", "PASS", "STOP не активен")
        row("Журнал процесса звонков", *await asyncio.to_thread(self._log_row))
        row("Резервные копии", *self._backup_row())
        # The voice tract is checked the way the worker resolves it (real files, not just variable names): PASS or WARN only.
        from ..doctor_rows import asr_row, model_row, tts_row
        for voice_row in (await asyncio.to_thread(asr_row), await asyncio.to_thread(tts_row, self.data_dir, self._child_env()),
                          await asyncio.to_thread(model_row, self.data_dir)):
            row(voice_row["check"], voice_row["status"], voice_row["detail"], voice_row["remedy"])
        return rows

    def _vault_row(self) -> tuple[str, str, str]:
        try:
            self.store.load()
        except CallError:
            return ("BLOCKED", "сохранённые данные входа не расшифровываются этим ключом",
                    "Сохраните api_id и api_hash заново (bossman call setup)")
        return ("PASS", "данные входа расшифровываются ключом Bossman", "")

    def _log_row(self) -> tuple[str, str, str]:
        path = self.home / "worker.log"
        if not path.is_file():
            return ("PASS", "журнала ещё нет", "")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-600_000:]
            creds = self.store.load()
        except (OSError, CallError):
            return ("WARN", "журнал не удалось прочитать", "Проверьте права доступа")
        secrets = [v for v in (creds.api_hash, creds.session) if v]
        if any(s in text for s in secrets):
            return ("BLOCKED", "в журнале найдено секретное значение", "Удалите worker.log и обновите Bossman")
        suspicious = sum(1 for line in text.splitlines() if redact(line) != line and "[REDACTED]" not in line)
        if suspicious:
            return ("WARN", f"в журнале {suspicious} строк, похожих на секрет (проверьте вручную)",
                    "Строки с номерами и длинными токенами лучше удалить")
        return ("PASS", "секретов в журнале не найдено", "")

    def _backup_row(self) -> tuple[str, str, str]:
        try:
            from ...v2.derived_stores import discover
            leaked = [p for p in discover(self.data_dir) if "telegram-calls" in Path(p).parts]
            from ...features import diag_bundle
            denied = "credentials.enc" in diag_bundle.DENY_NAMES
        except Exception:  # noqa: BLE001
            return ("WARN", "правила резервных копий не удалось проверить", "")
        if leaked:
            return ("BLOCKED", "снимок состояния включает файлы звонков", "Уберите их из списка копируемых хранилищ")
        if not denied:
            return ("BLOCKED", "архив диагностики не исключает credentials.enc", "Добавьте файл в список исключений")
        return ("PASS", "снимки состояния и архив диагностики не включают данные входа Telegram", "")


def _safe_notes(notes: Any) -> dict:
    if not isinstance(notes, dict):
        return {}
    return {str(k)[:40]: _scrub(v) for k, v in notes.items() if isinstance(v, (str, int, float, bool))}


def _last_call_view(rec: dict | None) -> dict | None:
    if not rec:
        return None
    return {"call_id": rec.get("call_id"), "transport": rec.get("transport"), "outcome": rec.get("outcome"),
            "error_code": rec.get("error_code"), "started_at": rec.get("started_at"), "ended_at": rec.get("ended_at"),
            "turns": len(rec.get("turns") or []), "latency_ms": rec.get("latency_ms"),
            "synthesized": bool(rec.get("synthesized"))}
