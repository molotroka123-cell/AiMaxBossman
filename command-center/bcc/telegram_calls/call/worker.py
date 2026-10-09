"""The calls worker: one process that owns the Telegram user session, the call engine and the running ``CallSession``.

Protocol (newline-delimited UTF-8 JSON on stdin/stdout, see docs/telegram-calls/ARCHITECTURE.md):
  request  {"id": str, "op": str, "args": {...}}
  response {"id": str, "ok": true,  "result": {...}}  |  {"id": str, "ok": false, "error": {"code", "message", "hint", ...}}
  event    {"event": "call_event" | "record" | "state" | "log", ...}
``stop`` never waits behind another request: the stdin reader hands it to the loop directly.

The worker re-evaluates the dial guard itself (fresh settings from disk) even though the manager already did: neither side
trusts the other with the one decision that rings a phone.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import deps
from ..account.credentials import CredentialStore
from ..account.guard import DialContext, check_dial, check_peer_candidate
from ..account.login import TelegramAccount, map_error
from ..account.stopflag import CallState
from ..hardening import configure_worker_logging, redact
from ..settings import CallSettings, calls_home, load_settings
from ..types import (AccountState, Brain, CallError, CallEvent, CallRecord, CallState as CS, Outcome, STTEngine,
                     TTSEngine)
from ..audio.vad import VAD
from ...pit import voice_language
from ...pit.voice_language import call_phrases
from .session import CallSession, SessionConfig

log = logging.getLogger("bcc.telegram_calls.worker")
MODE_ENV = "BOSSMAN_CALLS_MODE"               # "offline_test" only ever comes from the operator's environment, never from the API
OFFLINE_MODE = "offline_test"
DISCLOSURE = voice_language.PHRASES["ru"]["disclosure"]          # spoken before the first reply when the greeting did not say it (see SessionConfig)


from ..speech.factory import Engines  # noqa: E402  (re-exported for callers of the worker)

EnginesFactory = Callable[[CallSettings, str], Awaitable[Engines]]
TransportFactory = Callable[[Any], Any]


class Worker:
    def __init__(self, home: Path | None = None, *, write: Callable[[dict], None],
                 engines_factory: EnginesFactory | None = None, transport_factory: TransportFactory | None = None,
                 account: TelegramAccount | None = None, mode: str | None = None,
                 line_factory: Callable[[Any], Any] | None = None,
                 answering_engines_factory: EnginesFactory | None = None):
        self.home = home or calls_home()
        self._write = write
        self.mode = mode if mode is not None else os.environ.get(MODE_ENV, "")
        self.offline = self.mode == OFFLINE_MODE
        self.store = CredentialStore(self.home)
        self.state = CallState(self.home)
        self.account = account or self._make_account()
        self._engines_factory = engines_factory
        self._transport_factory = transport_factory
        self._line_factory = line_factory
        self._answering_engines_factory = answering_engines_factory
        self.answering: Any = None                              # AnsweringMachine, built on the owner's "answering.start"
        self._line: Any = None
        self.session: CallSession | None = None
        self._call_task: asyncio.Task | None = None
        self._last_record: dict | None = None
        self._events: list[dict] = []
        self._seq = 0
        self._dialing = False                     # single flight: a second dial while one is still being built is refused
        self._stop_epoch = 0                      # bumped by every STOP: a dial that is still being built must see it

    # ------------------------------------------------------------ construction helpers
    def _make_account(self) -> TelegramAccount:
        if self.offline:
            from .offline_mode import offline_client_factory
            return TelegramAccount(self.store, client_factory=offline_client_factory)
        return TelegramAccount(self.store)

    def _secrets(self) -> list[str]:
        try:
            c = self.store.load()
        except CallError:
            return []
        return [v for v in (c.api_hash, c.session) if v]

    # ------------------------------------------------------------ dispatch
    async def handle(self, msg: dict) -> dict:
        rid = msg.get("id")
        op = msg.get("op")
        args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
        try:
            fn = getattr(self, "op_" + str(op).replace(".", "_"), None)
            if fn is None or not str(op).replace(".", "_").replace("_", "").isalnum():
                raise CallError("INTERNAL", detail="unknown_op")
            result = await fn(args)
            return {"id": rid, "ok": True, "result": result if result is not None else {}}
        except CallError as exc:
            return {"id": rid, "ok": False, "error": exc.as_dict()}
        except Exception as exc:  # noqa: BLE001 - nothing may escape as free text
            mapped = map_error(exc)
            log.warning("op %s failed: %s", op, mapped.code)
            return {"id": rid, "ok": False, "error": mapped.as_dict()}

    def emit(self, event: dict) -> None:
        try:
            self._write(event)
        except Exception:  # noqa: BLE001 - a dead pipe means the manager is gone; stdin EOF handles the shutdown
            pass

    # ------------------------------------------------------------ ops: info
    async def op_hello(self, args: dict) -> dict:
        from .. import MODULE_VERSION
        if self.offline:                                      # the fake client must never overwrite (or clear) a REAL session
            try:
                session = self.store.load().session
            except CallError:
                session = ""
            if session and not session.startswith("OFFLINE-TEST-SESSION"):
                raise CallError("INTERNAL", detail="offline_mode_with_real_session")
        return {"version": MODULE_VERSION, "pid": os.getpid(), "mode": self.mode or "telegram", "transport": "loopback" if self.offline else "telegram"}

    async def op_deps(self, args: dict) -> dict:
        report = deps.probe()
        if args.get("engine") and not self.offline:
            report["engine"] = await asyncio.to_thread(deps.engine_selfcheck)
        return report

    async def op_status(self, args: dict) -> dict:
        s = self.session
        return {"account": self.account.public(), "mode": self.mode or "telegram",
                "call": self._call_view(), "last_record": self._last_record,
                "uncertain_previous": self.state.is_uncertain(), "stop": self.state.stop_is_set(),
                "answering": self.answering.status() if self.answering is not None else {"armed": False}}

    def _call_view(self) -> dict | None:
        s = self.session
        if s is None:
            return None
        return {"call_id": s.call_id, "state": s.record.state.value, "phase": s.phase.value, "transport": s.transport.name,
                "direction": s.record.direction, "models": dict(s.record.models), "counters": dict(s.record.counters),
                "latency_ms": s.record.as_dict()["latency_ms"], "started_at": s.record.started_at}

    async def op_events(self, args: dict) -> dict:
        after = int(args.get("after") or 0)
        return {"events": [e for e in self._events if e["seq"] > after][-200:]}

    # ------------------------------------------------------------ ops: account
    async def op_login_start(self, args: dict) -> dict:
        await self.account.start_login(str(args.get("phone") or ""))
        return {"state": self.account.state().value}

    async def op_login_code(self, args: dict) -> dict:
        state = await self.account.submit_code(str(args.get("code") or ""))
        return {"state": state.value}

    async def op_login_password(self, args: dict) -> dict:
        state = await self.account.submit_password(str(args.get("password") or ""))
        return {"state": state.value}

    async def op_logout(self, args: dict) -> dict:
        if self.session is not None:
            raise CallError("CALL_IN_PROGRESS")
        await self.account.logout()
        return {"state": self.account.state().value}

    async def op_contacts(self, args: dict) -> dict:
        return {"contacts": await self.account.contacts(str(args.get("query") or ""), int(args.get("limit") or 50))}

    async def op_peer_lookup(self, args: dict) -> dict:
        if args.get("username"):
            user = await self.account.resolve_username(str(args["username"]))
        else:
            user = await self.account.confirm_user(int(args.get("user_id") or 0))
        peer = check_peer_candidate(user, self.account.me_id())
        return {"peer": {"user_id": peer.user_id, "label": peer.label}}

    # ------------------------------------------------------------ ops: call
    def _dial_guard(self, args: dict) -> tuple[CallSettings, Any, DialContext]:
        """The dial guard with FRESH settings from disk. Evaluated twice per dial (before and after the slow engine build)."""
        settings = load_settings(self.home)                      # fresh, never cached
        ctx = DialContext(account=self.account.state(), me_id=self.account.me_id(),
                          active_call=self.session is not None or (self.answering is not None and self.answering.busy),
                          stop_active=self.state.stop_is_set() or bool(args.get("global_stop")),
                          uncertain_previous=self.state.is_uncertain(),
                          deps_ok=True if self.offline else deps.probe()["ready_for_telegram_call"])
        peer = check_dial(settings, ctx, confirm_unknown=bool(args.get("confirm_unknown")))
        return settings, peer, ctx

    async def op_dial(self, args: dict) -> dict:
        if self._dialing or self.session is not None:            # single flight: two overlapping dials would be two phone calls
            raise CallError("CALL_IN_PROGRESS")
        self._dialing = True
        try:
            return await self._dial(args)
        finally:
            self._dialing = False

    async def _dial(self, args: dict) -> dict:
        epoch = self._stop_epoch
        settings, peer, ctx = self._dial_guard(args)
        engines = await self._engines(settings)
        call_id = "c-" + uuid.uuid4().hex[:12]
        transport = await self._build_transport(engines)
        # The build is slow (models, network) and a STOP / a settings change can arrive meanwhile: nothing may ring after a
        # STOP that was acknowledged, and the peer / enabled flag are read again from disk right before the phone can ring.
        try:
            if self._stop_epoch != epoch:
                raise CallError("STOP_ACTIVE")
            settings, peer, ctx = self._dial_guard(args)
        except CallError:
            with contextlib.suppress(Exception):
                await transport.close()
            raise
        if ctx.uncertain_previous:
            self.state.acknowledge_uncertain()                    # the owner confirmed it explicitly (only a dial that goes ahead spends it)
        words = call_phrases(settings.language, settings.greeting)       # fixed phrases (incl. the AI disclosure) in the call's language
        cfg = SessionConfig(ring_timeout_s=float(settings.ring_timeout_s), max_call_s=float(settings.max_call_s),
                            idle_prompt_s=float(settings.idle_prompt_s), idle_hangup_s=float(settings.idle_hangup_s),
                            barge_in=settings.barge_in, echo_mode=settings.echo_mode, greeting=words["greeting"],
                            disclosure=words["disclosure"], idle_prompt_text=words["idle_prompt_text"],
                            repeat_prompt_text=words["repeat_prompt_text"], apology_text=words["apology_text"])
        session = CallSession(call_id=call_id, transport=transport, peer=peer, stt=engines.stt, tts=engines.tts,
                              brain=engines.brain, vad=engines.vad, cfg=cfg, on_event=self._on_session_event,
                              recorder=engines.recorder)
        self.session = session
        self.state.note_call_started(call_id)                    # BEFORE the phone can ring: a crash leaves it 'uncertain'
        self._call_task = asyncio.get_running_loop().create_task(self._run_call(session), name="calls-call")
        return {"call_id": call_id, "accepted": True, "transport": transport.name, "models": dict(session.record.models),
                "notes": engines.notes or {}}

    async def _engines(self, settings: CallSettings) -> Engines:
        if self._engines_factory is None:
            from ..speech.factory import build_engines
            return await build_engines(settings, self.mode, self.state.stop_is_set)      # Jeff's STT/TTS/brain see the durable STOP
        return await self._engines_factory(settings, self.mode)

    async def _build_transport(self, engines: Engines) -> Any:
        if self._transport_factory is not None:
            return self._transport_factory(await self.account.ensure_client())
        if self.offline:
            from .offline_mode import ScriptedLoopback
            from .selftest import default_conversation_script
            return ScriptedLoopback(default_conversation_script(engines.stt, engines))
        from .pytgcalls_transport import build_transport
        return build_transport(await self.account.ensure_client())

    async def _run_call(self, session: CallSession, *, incoming: bool = False) -> dict:
        record: CallRecord | None = None
        data: dict = {"call_id": session.call_id, "outcome": None}
        try:
            record = await session.run()
        except Exception:  # noqa: BLE001 - CallSession.run does not raise; belt and braces
            log.exception("call session crashed")
        finally:
            # Every step is guarded on its own: one failing write must not leave the worker "in a call" for ever
            # (the dial guard would refuse everything until a restart) or swallow the record.
            outcome = record.outcome if record is not None else None
            data = record.as_dict() if record is not None else {"call_id": session.call_id, "outcome": None}
            if incoming and isinstance(data.get("summary"), dict):
                # history.jsonl and the IPC record stay text-free: the words of the caller live ONLY in the answering log
                data["summary"] = {"text": f"Входящий звонок на автоответчик, исход: {data.get('outcome') or 'unknown'}. "
                                           "Содержание — в журнале автоответчика.", "agreed_tasks": [], "generated_by": "mechanical"}
            self._last_record = data
            if not incoming:                      # nothing was dialled for an incoming call: it never makes the next dial "uncertain"
                try:
                    self.state.note_call_finished(session.call_id, outcome)
                except OSError:
                    log.warning("state write failed")
            try:
                self.state.append_history(data)
            except OSError:
                log.warning("history write failed")
            self.session = None
            self.emit({"event": "record", "record": data})
        return data

    # ------------------------------------------------------------ ops: answering machine (incoming calls)
    async def _answering_engines(self, settings: CallSettings, mode: str) -> Engines:
        factory = self._answering_engines_factory or self._engines_factory
        if factory is not None:
            return await factory(settings, mode)
        from ..speech.factory import build_engines
        return await build_engines(settings, mode, self.state.stop_is_set, answering=True)   # the durable STOP reaches answering STT/TTS/brain too

    async def _build_line(self) -> Any:
        if self._line_factory is not None:
            return self._line_factory(await self.account.ensure_client())
        if self.offline:
            from .loopback import LoopbackLine
            from .selftest import answering_caller_script
            return LoopbackLine(driver=answering_caller_script())
        from .pytgcalls_transport import build_line
        return build_line(await self.account.ensure_client())

    async def _caller_label(self, user_id: int) -> str:
        user = await self.account.confirm_user(user_id)           # the same read-only lookup the peer picker uses
        return str(user.get("label") or "")

    def _on_incoming_session(self, session: CallSession | None) -> None:
        self.session = session

    async def _run_incoming(self, session: CallSession) -> dict:
        return await self._run_call(session, incoming=True)

    async def op_answering_start(self, args: dict) -> dict:
        """The owner arms the answering machine. Needs the setting, a logged-in account, no STOP; never places a call."""
        settings = load_settings(self.home)                      # fresh, never cached
        if not settings.answering_machine:
            raise CallError("ANSWERING_NOT_ENABLED")
        if not self.offline:
            state = self.account.state()
            if state == AccountState.NO_CREDENTIALS:
                raise CallError("NO_CREDENTIALS")
            if state != AccountState.READY:
                raise CallError("NOT_LOGGED_IN")
            if not deps.probe()["ready_for_telegram_call"]:
                raise CallError("DEPENDENCIES_MISSING")
        if self.state.stop_is_set():
            raise CallError("STOP_ACTIVE")
        if self.answering is None:
            from ..answering import AnsweringMachine
            self._line = await self._build_line()
            self.answering = AnsweringMachine(
                home=self.home, line=self._line, engines_factory=self._answering_engines,
                settings_loader=lambda: load_settings(self.home), stop_active=self.state.stop_is_set, mode=self.mode,
                run_session=self._run_incoming, emit=self.emit, secrets=self._secrets, on_session=self._on_incoming_session,
                session_event=self._on_session_event, label_resolver=self._caller_label,
                busy_probe=lambda: self.session is not None or self._dialing)
        await self.answering.arm()
        return {"answering": self.answering.status()}

    async def op_answering_stop(self, args: dict) -> dict:
        if self.answering is not None:
            self.answering.disarm()
        return {"answering": self.answering.status() if self.answering is not None else {"armed": False}}

    async def op_answering_simulate(self, args: dict) -> dict:
        """Offline test mode ONLY: make the loopback line ring, as a synthetic caller. Refused against real Telegram."""
        if not self.offline:
            raise CallError("INTERNAL", detail="offline_only")
        if self.answering is None or self._line is None:
            raise CallError("ANSWERING_NOT_ENABLED")
        from .offline_mode import OFFLINE_PEER_ID
        call = self._line.ring(int(args.get("caller_id") or OFFLINE_PEER_ID), str(args.get("label") or "Тестовый звонящий")[:60])
        return {"call_ref": call.call_ref}

    def _on_session_event(self, ev: CallEvent) -> None:
        d = ev.as_dict()
        self._events.append(d)
        del self._events[:-500]
        self.emit({"event": "call_event", "data": d})

    async def op_hangup(self, args: dict) -> dict:
        if self.session is None:
            return {"ended": False}
        self.session.hangup("owner_hangup")
        return {"ended": True}

    # ``stop`` is also reachable through the fast path (``stop_now``), so it must be safe to call from a bare callback.
    def stop_now(self, reason: str = "owner_stop") -> None:
        self._stop_epoch += 1                                      # a dial still being built sees this before it can ring
        try:
            self.state.set_stop(reason)                            # durable first: survives a crash right after
        except OSError:                                            # a failing write must never keep the call on the line
            log.warning("stop flag write failed")
        if self.session is not None:
            self.session.stop(reason)
        if self.answering is not None:                             # declines a ringing call, hangs up an answered one
            self.answering.stop(reason)

    async def op_stop(self, args: dict) -> dict:
        reason = str(args.get("reason") or "owner_stop")[:40]
        self.stop_now(reason)
        timeout = float(args.get("timeout") or 5.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._dialing and loop.time() < deadline:            # a dial still being built aborts itself (STOP_ACTIVE) or hands
            await asyncio.sleep(0.02)                              # its session over atomically: wait for that, bounded
        confirmed = not self._dialing                              # still building after the timeout: NOT a confirmed hangup
        if self._call_task is not None and not self._call_task.done():
            # asyncio.wait never cancels the call task on timeout (and never re-raises its failure into the STOP path)
            done, _pending = await asyncio.wait({self._call_task}, timeout=max(0.1, deadline - loop.time()))
            confirmed = bool(done) and confirmed
        if self.answering is not None and not await self.answering.wait_idle(max(0.1, deadline - loop.time())):
            confirmed = False                                      # a ringing/answered incoming call is part of the same STOP
        return {"stopped": True, "hangup_confirmed": confirmed, "stop_flag": self.state.stop_is_set()}

    async def op_resume(self, args: dict) -> dict:
        self.state.clear_stop()
        if self.answering is not None:
            self.answering.resume()
        return {"stop_flag": False}

    async def op_selftest(self, args: dict) -> dict:
        if self.session is not None or (self.answering is not None and self.answering.busy):
            raise CallError("CALL_IN_PROGRESS")
        from .selftest import run_selftest
        settings = load_settings(self.home)
        return await run_selftest(settings, str(args.get("scenario") or "all"), engines_factory=self._engines_factory,
                                  mode=self.mode, emit=self.emit)

    async def op_shutdown(self, args: dict) -> dict:
        if self.answering is not None:
            await self.answering.shutdown()
        if self._line is not None:
            with contextlib.suppress(Exception):
                await self._line.close()
        if self.session is not None:
            self.session.stop("shutdown")
            if self._call_task is not None:
                await asyncio.wait({self._call_task}, timeout=5)
        await self.account.close()
        return {"bye": True}


# ---------------------------------------------------------------- process entry

def _protect_stdout() -> Any:
    """Keep the real stdout for the protocol; anything printed by a library goes to stderr instead."""
    real = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real


def _raise_windows_timer_resolution() -> None:
    if os.name == "nt":                                            # 10 ms pacing needs finer than the default 15.6 ms tick
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)                 # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


async def serve_stdio(worker_factory: Callable[[Callable[[dict], None]], Worker], stdin: Any, stdout: Any) -> int:
    loop = asyncio.get_running_loop()
    lock = threading.Lock()

    def write(obj: dict) -> None:
        line = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with lock:
            stdout.write(line)
            try:
                stdout.flush()
            except Exception:  # noqa: BLE001
                pass

    worker = worker_factory(write)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def reader() -> None:
        try:
            for raw in iter(stdin.readline, b""):
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("op") == "stop":                        # fast path: never queued behind a slow request
                    loop.call_soon_threadsafe(worker.stop_now, str((msg.get("args") or {}).get("reason") or "owner_stop"))
                loop.call_soon_threadsafe(queue.put_nowait, msg)
        finally:                                                   # EOF = the manager is gone: stop any call, then exit
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, name="calls-stdin", daemon=True).start()
    write({"event": "state", "state": "worker_ready", "pid": os.getpid()})
    tasks: set[asyncio.Task] = set()
    while True:
        msg = await queue.get()
        if msg is None:
            worker.stop_now("manager_gone")
            await worker.op_shutdown({})
            return 0
        task = loop.create_task(_respond(worker, msg, write))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        if msg.get("op") == "shutdown":
            await asyncio.gather(*tasks, return_exceptions=True)
            return 0


async def _respond(worker: Worker, msg: dict, write: Callable[[dict], None]) -> None:
    write(await worker.handle(msg))


def main(argv: list[str] | None = None) -> int:
    real_out = _protect_stdout()
    _raise_windows_timer_resolution()
    home = calls_home()
    store = CredentialStore(home)

    def secrets() -> list[str]:
        try:
            c = store.load()
            return [v for v in (c.api_hash, c.session) if v]
        except CallError:
            return []
    configure_worker_logging(home, secrets)
    try:
        return asyncio.run(serve_stdio(lambda w: Worker(home, write=w), sys.stdin.buffer, real_out))
    except KeyboardInterrupt:
        return 0
