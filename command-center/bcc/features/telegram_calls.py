"""Telegram live calls: the API of the ONE Command Center backend (panel «Telegram-звонки» and ``bossman call`` are thin clients).

Same product, one more control surface: no second data directory, vault, memory or task engine. The MTProto session and the
call engine live in the calls worker process (``bcc.telegram_calls``), which ``CallsManager`` starts ONLY on an owner action.
Nothing here registers an agent tool: an agent cannot place a call.

CONTRACT (fixed by the coordinator; do not rename): routes are /api/telegram/calls/*, bus events are telegram_call.state /
telegram_call.ended. There is ONE prefix and ONE event family: no /api/calls alias and no calls.* events.

Endpoints (paths below are relative to /api/telegram; the normal auth applies):
  GET  /calls/status                      merged local facts + worker status (never starts the worker)
  GET|PUT /calls/settings                 owner settings (calls OFF by default; the peer is NOT part of them)
  POST /calls/credentials                 api_id / api_hash -> the existing Vault; never returned, only masks
  POST /calls/login/start|code|password   phone -> code -> 2FA password; secrets travel only to the worker pipe
  POST /calls/logout                      forgets the session; also clears the peer and switches calls off
  GET  /calls/contacts?q=                 candidates for the ONE test peer
  PUT|DELETE /calls/peer                  choose (needs confirm=true, re-validated by the worker) / clear the peer
  POST /calls/call                        dial; the body has NO peer field (extra fields are rejected)
  POST /calls/hangup | /calls/stop | /calls/resume
  GET  /calls/events?after=N              polling; secret- and text-free
  GET  /calls/history?limit=              finished calls: outcome, latency, short summary, proposals, post-call results
  POST /calls/history/{id}/save-memory    owner click: the short summary -> Bossman memory (idempotent)
  POST /calls/history/{id}/draft-tasks    owner click: proposals -> DRAFT tasks only (never run)
  POST /calls/selftest {scenario}         audio contour WITHOUT Telegram (label «ТЕСТ БЕЗ TELEGRAM»)
  POST /calls/doctor                      local diagnostics only, no external call
  GET|POST /calls/install                 add-on dependencies: status / start (owner-triggered)

STOP: the durable calls STOP file is written first, then the call is hung up. The bus event ``computer.stop`` (the global
Bossman STOP) does the same, ``POST /api/control-plane/stop-all`` stops the ``calls`` plane, and dialing is refused while the
global STOP is set. Bus events: ``telegram_call.state`` / ``telegram_call.ended`` (no text, no phone numbers, no secrets, at most
~5 per second).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..telegram_calls import postcall
from ..telegram_calls.call.manager import CallsManager, SELFTEST_SCENARIOS
from ..telegram_calls.settings import CallSettings
from ..telegram_calls.types import CallError
from . import Feature

router = APIRouter()

MIN_EMIT_GAP_S = 0.2                      # at most ~5 bus events per second
_CALL_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$"

_STATUS = {
    **dict.fromkeys(("NOT_ENABLED", "NO_CREDENTIALS", "NOT_LOGGED_IN", "LOGIN_NOT_PENDING", "SESSION_REVOKED", "PEER_NOT_SELECTED",
                     "PEER_NOT_ALLOWED", "PEER_IS_SELF", "PEER_PRIVACY", "CALL_IN_PROGRESS", "STOP_ACTIVE",
                     "UNCERTAIN_PREVIOUS_CALL", "DEPENDENCIES_MISSING", "BRAIN_NOT_CONFIGURED"), 409),
    **dict.fromkeys(("LOGIN_PHONE_INVALID", "LOGIN_CODE_INVALID", "LOGIN_CODE_EXPIRED", "LOGIN_PASSWORD_INVALID", "PEER_INVALID"), 422),
    "PEER_NOT_FOUND": 404, "LOGIN_FLOOD_WAIT": 429, "WORKER_TIMEOUT": 504,
    **dict.fromkeys(("WORKER_UNAVAILABLE", "STT_UNAVAILABLE", "TTS_UNAVAILABLE", "BRAIN_UNAVAILABLE", "VAD_UNAVAILABLE"), 503),
    **dict.fromkeys(("TELEGRAM_NETWORK", "TELEGRAM_RPC"), 502),
}


def _http(exc: CallError) -> HTTPException:
    return HTTPException(_STATUS.get(exc.code, 500), exc.as_dict())


def _bad(code: str, message: str, hint: str = "", status: int = 422) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **({"hint": hint} if hint else {})})


# ---------------------------------------------------------------- request bodies (unknown fields are rejected)

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CredentialsIn(_Strict):
    api_id: int = Field(gt=0, lt=2 ** 31)
    api_hash: str = Field(min_length=32, max_length=32)


class LoginStartIn(_Strict):
    phone: str = Field(min_length=5, max_length=32)


class LoginCodeIn(_Strict):
    code: str = Field(min_length=3, max_length=16)


class LoginPasswordIn(_Strict):
    password: str = Field(min_length=1, max_length=256)


class PeerIn(_Strict):
    user_id: int | None = Field(default=None, gt=0, lt=2 ** 52)
    username: str | None = Field(default=None, min_length=1, max_length=40)
    confirm: bool = False


class DialIn(_Strict):
    """No peer parameter, on purpose: the peer is the ONE saved test account, checked again before the phone rings."""
    confirm_unknown: bool = False


class SelftestIn(_Strict):
    scenario: Literal["all", "basic", "barge_in", "echo", "stop", "no_redial"] = "all"


class SettingsIn(_Strict):
    """Owner-tunable settings. The peer and audio recording are deliberately NOT here."""
    enabled: bool | None = None
    max_call_s: int | None = None
    ring_timeout_s: int | None = None
    idle_prompt_s: int | None = None
    idle_hangup_s: int | None = None
    barge_in: bool | None = None
    echo_mode: str | None = None
    llm_route: str | None = None
    vad: str | None = None
    greeting: str | None = Field(default=None, max_length=200)
    stt_model_path: str | None = Field(default=None, max_length=500)
    tts_voice_path: str | None = Field(default=None, max_length=500)
    auto_save_to_bossman_memory: bool | None = None


# ---------------------------------------------------------------- runtime (one per backend process)

class _Runtime:
    def __init__(self, svc: Any):
        self.svc = svc
        self.manager = CallsManager(Path(svc.settings.data_dir), vault=svc.vault, on_record=self._on_record,
                                    on_state=self._on_state)
        self._last_emit = 0.0
        self._pending_state: dict | None = None
        self._flush: asyncio.TimerHandle | None = None
        self._install: dict = {"state": "idle", "progress": [], "result": None, "error": None, "started_at": None}
        self._install_task: asyncio.Task | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    # ---- bus bridge (rate limited, text-free)
    def _on_state(self, kind: str, view: dict | None) -> None:
        if not view:
            return
        self._pending_state = {"state": view.get("state"), "phase": view.get("phase"), "call_id": view.get("call_id"),
                               "transport": view.get("transport")}
        gap = time.monotonic() - self._last_emit
        if gap >= MIN_EMIT_GAP_S:
            self._send_state()
        elif self._flush is None:
            self._flush = asyncio.get_running_loop().call_later(MIN_EMIT_GAP_S - gap, self._send_state)

    def _send_state(self) -> None:
        self._flush = None
        data, self._pending_state = self._pending_state, None
        if data is None:
            return
        self._last_emit = time.monotonic()
        task = asyncio.get_running_loop().create_task(self._emit("telegram_call.state", data))
        self.manager._bg.add(task)
        task.add_done_callback(self.manager._bg.discard)

    async def _emit(self, kind: str, data: dict) -> None:
        # telegram_call.state / telegram_call.ended: text-free payload (no transcript, phone number or secret)
        with contextlib.suppress(Exception):
            if kind == "telegram_call.state":
                await self.svc.bus.emit("telegram_call.state", state=data.get("state"), phase=data.get("phase"),
                                        call_id=data.get("call_id"), transport=data.get("transport"))
            else:
                await self.svc.bus.emit("telegram_call.ended", call_id=data.get("call_id"), outcome=data.get("outcome"),
                                        error_code=data.get("error_code"), transport=data.get("transport"),
                                        turns=data.get("turns"), latency_p50_ms=data.get("latency_p50_ms"))

    async def _on_record(self, rec: dict) -> None:
        try:
            settings = self.manager.settings()
            auto = postcall.auto_save_enabled(settings)
        except (ValueError, OSError):
            auto = False
        await postcall.process_record(self.svc, self.manager.state, rec, auto_save=auto)
        lat = rec.get("latency_ms") if isinstance(rec.get("latency_ms"), dict) else {}
        await self._emit("telegram_call.ended", {"call_id": rec.get("call_id"), "outcome": rec.get("outcome"),
                                         "error_code": rec.get("error_code"), "transport": rec.get("transport"),
                                         "turns": len(rec.get("turns") or []), "latency_p50_ms": lat.get("p50")})

    # ---- small cached facts for the status poll
    async def cached(self, key: str, ttl: float, fn) -> Any:
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = await fn()
        self._cache[key] = (time.monotonic(), value)
        return value

    def install_view(self) -> dict:
        return {k: (list(v) if k == "progress" else v) for k, v in self._install.items()}


def _rt(request: Request) -> _Runtime:
    svc = request.app.state.svc
    rt = getattr(svc, "_calls", None)
    if rt is None:
        rt = svc._calls = _Runtime(svc)
    return rt


def global_stop_active(svc: Any) -> bool:
    """The global Bossman STOP (features/tools_computer): live state, or the persisted STOP file after a restart."""
    try:
        from . import tools_computer
        state = getattr(svc, "_computer_state", None)
        if state is not None and state.stopped():
            return True
        return (Path(svc.settings.data_dir) / "computer" / tools_computer.STOP_FILE).is_file()
    except Exception:  # noqa: BLE001 - if the state cannot be read, fail closed only for dialing (the guard refuses)
        return False


async def _memory_configured(svc: Any) -> bool:
    try:
        from . import tools_memory
        return bool((await tools_memory.load_config(svc)).get("root"))
    except Exception:  # noqa: BLE001
        return False


def _settings_view(settings: CallSettings) -> dict:
    data = {k: v for k, v in settings.to_json().items() if k != "extra"}
    data.setdefault("auto_save_to_bossman_memory", postcall.auto_save_enabled(settings))
    return data


# ---------------------------------------------------------------- status / settings

@router.get("/calls/status")
async def calls_status(request: Request):
    rt = _rt(request)
    svc = request.app.state.svc
    status = await rt.manager.status(global_stop=global_stop_active(svc))
    status["memory"] = {"configured": await rt.cached("memory", 10.0, lambda: _memory_configured(svc))}
    status["postcall"] = {"auto_save_to_bossman_memory": bool(status["settings"].get("auto_save_to_bossman_memory"))}
    status["install"] = rt.install_view()
    return status


@router.get("/calls/settings")
async def get_settings(request: Request):
    mgr = _rt(request).manager
    try:
        return _settings_view(mgr.settings())
    except (ValueError, OSError):
        raise _bad("SETTINGS_UNREADABLE", "Файл настроек звонков повреждён.",
                   "Звонки остаются выключенными; сохраните настройки заново.", 409) from None


@router.put("/calls/settings")
async def put_settings(body: SettingsIn, request: Request):
    mgr = _rt(request).manager
    try:
        current = mgr.settings()
    except (ValueError, OSError):
        current = CallSettings()                    # a corrupt file is replaced by explicit owner input, never trusted
    changes = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
    if not changes:
        return _settings_view(current)
    if mgr.active_call is not None and changes.keys() - {"greeting"}:
        raise _http(CallError("CALL_IN_PROGRESS"))
    known = CallSettings.__dataclass_fields__
    direct = {k: v for k, v in changes.items() if k in known}
    extra = {k: v for k, v in changes.items() if k not in known}
    try:
        updated = replace(current, **direct, **({"extra": {**current.extra, **extra}} if extra else {}))
    except (ValueError, TypeError) as exc:
        raise _bad("SETTINGS_INVALID", "Настройки не приняты: " + str(exc)[:160], "Проверьте значения и повторите.") from None
    mgr.save_settings(updated)
    return _settings_view(updated)


# ---------------------------------------------------------------- account

@router.post("/calls/credentials")
async def save_credentials(body: CredentialsIn, request: Request):
    rt = _rt(request)
    mgr = rt.manager
    before = mgr.store.public()
    try:
        public = mgr.save_credentials(body.api_id, body.api_hash)
    except CallError as exc:
        if exc.code == "NO_CREDENTIALS":
            raise HTTPException(422, {**exc.as_dict(), "message": "api_id или api_hash не подходят по формату.",
                                      "hint": "api_id — число, api_hash — 32 символа (0-9, a-f) с my.telegram.org."}) from None
        raise _http(exc) from None
    if before.get("has_session") and not public.get("has_session"):
        _forget_peer(mgr)                            # the session belonged to the old api id: nothing carries over
    await mgr.credentials_changed()
    mgr.invalidate_cache()
    return {"ok": True, "credentials": {"has_api": bool(public.get("has_api")), "api_id": public.get("api_id"),
                                        "has_session": bool(public.get("has_session"))}}


def _forget_peer(mgr: CallsManager) -> None:
    """After logout / a changed api id the ONE peer is forgotten and calls switch off: the next account must choose again."""
    with contextlib.suppress(ValueError, OSError):
        mgr.save_settings(replace(mgr.settings(), peer_user_id=None, peer_label="", enabled=False))


async def _account_reply(mgr: CallsManager, result: dict) -> dict:
    return {"state": result.get("state"), "phone": mgr.store.public().get("phone")}


@router.post("/calls/login/start")
async def login_start(body: LoginStartIn, request: Request):
    mgr = _rt(request).manager
    try:
        return await _account_reply(mgr, await mgr.login_start(body.phone))
    except CallError as exc:
        raise _http(exc) from None


@router.post("/calls/login/code")
async def login_code(body: LoginCodeIn, request: Request):
    mgr = _rt(request).manager
    try:
        result = await mgr.login_code(body.code)
    except CallError as exc:
        raise _http(exc) from None
    mgr.invalidate_cache()
    return await _account_reply(mgr, result)


@router.post("/calls/login/password")
async def login_password(body: LoginPasswordIn, request: Request):
    mgr = _rt(request).manager
    try:
        result = await mgr.login_password(body.password)
    except CallError as exc:
        raise _http(exc) from None
    mgr.invalidate_cache()
    return await _account_reply(mgr, result)


@router.post("/calls/logout")
async def logout(request: Request):
    mgr = _rt(request).manager
    try:
        result = await mgr.logout()
    except CallError as exc:
        raise _http(exc) from None
    _forget_peer(mgr)
    mgr.invalidate_cache()
    return {"state": result.get("state"), "peer_cleared": True, "enabled": False}


@router.get("/calls/contacts")
async def contacts(request: Request, q: str = ""):
    mgr = _rt(request).manager
    try:
        result = await mgr.contacts(q[:60])
    except CallError as exc:
        raise _http(exc) from None
    return {"contacts": [{"id": c.get("id"), "label": c.get("label"), "username": c.get("username")}
                         for c in result.get("contacts", [])]}


@router.put("/calls/peer")
async def put_peer(body: PeerIn, request: Request):
    mgr = _rt(request).manager
    if not body.confirm:
        raise _bad("PEER_NOT_CONFIRMED", "Выбор собеседника нужно подтвердить.",
                   "Отметьте «Это мой второй аккаунт» и повторите.")
    if (body.user_id is None) == (body.username is None):
        raise _bad("PEER_INVALID", "Укажите либо id, либо юзернейм собеседника (что-то одно).")
    if mgr.active_call is not None:
        raise _http(CallError("CALL_IN_PROGRESS"))
    try:
        found = await mgr.peer_lookup(user_id=body.user_id, username=body.username)      # re-validated by the worker right now
    except CallError as exc:
        raise _http(exc) from None
    peer = found.get("peer") or {}
    try:
        settings = replace(mgr.settings(), peer_user_id=int(peer["user_id"]), peer_label=str(peer.get("label") or "")[:120])
    except (KeyError, TypeError, ValueError):
        raise _http(CallError("PEER_INVALID")) from None
    mgr.save_settings(settings)
    return {"peer": {"user_id": settings.peer_user_id, "label": settings.peer_label}, "enabled": settings.enabled}


@router.delete("/calls/peer")
async def delete_peer(request: Request):
    mgr = _rt(request).manager
    if mgr.active_call is not None:
        raise _http(CallError("CALL_IN_PROGRESS"))
    try:
        mgr.save_settings(replace(mgr.settings(), peer_user_id=None, peer_label=""))
    except (ValueError, OSError):
        mgr.save_settings(CallSettings())
    return {"peer": None}


# ---------------------------------------------------------------- call control

@router.post("/calls/call")
async def dial(request: Request, body: DialIn | None = None):
    rt = _rt(request)
    svc = request.app.state.svc
    try:
        result = await rt.manager.dial(confirm_unknown=bool(body and body.confirm_unknown),
                                       global_stop=global_stop_active(svc))
    except CallError as exc:
        raise _http(exc) from None
    return result


@router.post("/calls/hangup")
async def hangup(request: Request):
    try:
        return await _rt(request).manager.hangup()
    except CallError as exc:
        raise _http(exc) from None


@router.post("/calls/stop")
async def stop(request: Request):
    """Owner STOP: never silent. The durable file is written first; the call is hung up; the worker is terminated if needed."""
    rt = _rt(request)
    result = await rt.manager.stop("owner")
    with contextlib.suppress(Exception):
        await rt._emit("telegram_call.state", {"state": "stopped", "phase": None, "call_id": None, "transport": None})
    return {"stopped": True, "persisted": rt.manager.state.stop_is_set(), **result}


@router.post("/calls/resume")
async def resume(request: Request):
    rt = _rt(request)
    result = await rt.manager.resume()
    return {**result, "global_stop": global_stop_active(request.app.state.svc)}


# ---------------------------------------------------------------- events / history / post-call

@router.get("/calls/events")
async def events(request: Request, after: int = 0, limit: int = 200):
    return _rt(request).manager.events(max(0, after), limit)


@router.get("/calls/history")
async def history(request: Request, limit: int = 20):
    return {"items": await asyncio.to_thread(_rt(request).manager.history, limit)}


def _call_id(call_id: str) -> str:
    import re
    if not re.match(_CALL_ID, call_id):
        raise _bad("CALL_NOT_FOUND", "Такого звонка нет в истории.", "Выберите звонок из списка.", 404)
    return call_id


@router.post("/calls/history/{call_id}/save-memory")
async def save_memory(call_id: str, request: Request):
    rt = _rt(request)
    try:
        memory = await postcall.save_memory_for(request.app.state.svc, rt.manager.state, _call_id(call_id))
    except postcall.PostCallError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from None
    return {"memory": memory}


@router.post("/calls/history/{call_id}/draft-tasks")
async def draft_tasks(call_id: str, request: Request):
    rt = _rt(request)
    try:
        drafts = await postcall.draft_tasks_for(request.app.state.svc, rt.manager.state, _call_id(call_id))
    except postcall.PostCallError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from None
    return {"drafts": drafts}


# ---------------------------------------------------------------- checks

@router.post("/calls/selftest")
async def selftest(request: Request, body: SelftestIn | None = None):
    scenario = body.scenario if body else "all"
    assert scenario in SELFTEST_SCENARIOS
    try:
        return await _rt(request).manager.selftest(scenario)
    except CallError as exc:
        raise _http(exc) from None


@router.post("/calls/doctor")
async def doctor(request: Request):
    rt = _rt(request)
    rows = await rt.manager.doctor(global_stop=global_stop_active(request.app.state.svc))
    worst = "BLOCKED" if any(r["status"] == "BLOCKED" for r in rows) else "WARN" if any(r["status"] == "WARN" for r in rows) else "PASS"
    return {"verdict": worst, "rows": rows}


# ---------------------------------------------------------------- add-on install (owner-triggered)

def _addon():
    try:
        from ..telegram_calls import addon
    except ImportError:
        raise _bad("NOT_AVAILABLE", "Установщик зависимостей звонков недоступен в этой сборке.",
                   'Установите вручную: pip install "bossman-command-center[calls]"', 501) from None
    return addon


@router.get("/calls/install")
async def install_status(request: Request):
    rt = _rt(request)
    addon = _addon()
    return {**rt.install_view(), "addon": await asyncio.to_thread(addon.status, Path(request.app.state.svc.settings.data_dir))}


@router.post("/calls/install")
async def install_start(request: Request):
    rt = _rt(request)
    addon = _addon()
    data_dir = Path(request.app.state.svc.settings.data_dir)
    if rt._install["state"] != "running":
        rt._install = {"state": "running", "progress": [], "result": None, "error": None, "started_at": round(time.time(), 1)}

        def progress(line: str) -> None:
            if len(rt._install["progress"]) < 200:
                rt._install["progress"].append(str(line)[:120])

        async def job() -> None:
            try:
                result = await asyncio.to_thread(addon.install, data_dir, progress=progress)
                rt._install.update(state="done", result=result)
                rt.manager.invalidate_cache()
                await rt.manager.credentials_changed()          # a running worker must be restarted to see the new packages
            except CallError as exc:
                rt._install.update(state="error", error=exc.as_dict())
            except Exception as exc:  # noqa: BLE001
                rt._install.update(state="error", error=CallError("INTERNAL", detail=type(exc).__name__).as_dict())

        rt._install_task = asyncio.get_running_loop().create_task(job(), name="calls-addon-install")
        rt.manager._bg.add(rt._install_task)
        rt._install_task.add_done_callback(rt.manager._bg.discard)
    return {**rt.install_view(), "addon": await asyncio.to_thread(addon.status, data_dir)}


# ---------------------------------------------------------------- global STOP -> calls STOP

async def _watch_global_stop(svc: Any, rt: _Runtime) -> None:
    """``computer.stop`` (the global Bossman STOP) ends the call too. A lost event is caught by the periodic check."""
    q = svc.bus.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), 5.0)
            except asyncio.TimeoutError:
                if not svc.bus.is_subscribed(q):
                    q = svc.bus.subscribe()
                msg = None
                if global_stop_active(svc) and (rt.manager.active_call is not None or rt.manager.dial_pending):
                    await rt.manager.stop("computer_stop")
                continue
            if msg.get("kind") == "computer.stop":
                await rt.manager.stop("computer_stop")
    finally:
        svc.bus.unsubscribe(q)
        with contextlib.suppress(BaseException):
            await rt.manager.shutdown()


async def _setup(svc: Any) -> None:
    rt = svc._calls = _Runtime(svc)                # nothing is started here: the worker starts only on an owner action
    task = asyncio.create_task(_watch_global_stop(svc, rt), name="bcc-calls-global-stop")
    if hasattr(svc, "_tasks"):
        svc._tasks.append(task)


# ---------------------------------------------------------------- mounting
# ONE PREFIX: /api/telegram/calls/*  (the docs, the panel, the CLI and the command-bar block all name it). No alias.
_calls_router = router
router = APIRouter()
router.include_router(_calls_router, prefix="/telegram")

FEATURE = Feature(name="telegram_calls", router=router, setup=_setup)
