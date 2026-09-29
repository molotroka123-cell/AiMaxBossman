"""Telegram live calls: the owner's HTTP surface (thin layer over ``CallsManager``).

Everything is mounted under /api/telegram/calls/* with the normal token/session + CSRF auth. Rules:

* There is NO peer/"whom" field on dial: the only callee is the one confirmed second account stored in the
  settings; the body of ``POST /dial`` accepts ``confirm_unknown`` and nothing else (extra keys -> 422).
* Reads never start the worker. An unconfigured module answers 200 with an empty state, not 4xx.
* Secrets (api hash, login code, 2FA password) travel only in request bodies of the login endpoints; they are
  never echoed back, logged or put on the event bus. Validation errors never repeat the input.
* Every failure is a stable ``CallError`` code -> ``{"error": {"code", "message", "hint"}}``.
* STOP works from any surface: this router, the global STOP (bus ``computer.stop``, handled by the manager)
  and the terminal. ``/resume`` clears only the call-STOP flag.

doctor / selftest / install call functions of the later work packages (bcc.telegram_calls.doctor,
call.selftest, addons); until they exist they answer NOT_IMPLEMENTED_YET.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import Feature

router = APIRouter(prefix="/telegram/calls")

_STATUS = {
    "NOT_ENABLED": 409, "STOP_ACTIVE": 409, "CALL_IN_PROGRESS": 409, "UNCERTAIN_PREVIOUS_CALL": 409,
    "PEER_NOT_SELECTED": 409, "PEER_NOT_ALLOWED": 409, "NO_CREDENTIALS": 409, "NOT_LOGGED_IN": 409,
    "LOGIN_NOT_PENDING": 409, "SESSION_REVOKED": 409, "BRAIN_NOT_CONFIGURED": 409,
    "LOGIN_PHONE_INVALID": 400, "LOGIN_CODE_INVALID": 400, "LOGIN_CODE_EXPIRED": 400,
    "LOGIN_PASSWORD_INVALID": 400, "PEER_IS_SELF": 400, "PEER_INVALID": 400, "PEER_NOT_FOUND": 404,
    "LOGIN_FLOOD_WAIT": 429, "WORKER_UNAVAILABLE": 503, "DEPENDENCIES_MISSING": 503,
    "WORKER_TIMEOUT": 504, "TELEGRAM_NETWORK": 502, "TELEGRAM_RPC": 502, "INTERNAL": 500,
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsIn(Strict):
    enabled: bool | None = None
    max_call_s: float | None = None
    ring_timeout_s: float | None = None
    record_audio: bool | None = None
    keep_transcript: bool | None = None


class CredentialsIn(Strict):
    api_id: int
    api_hash: str = Field(min_length=1, max_length=200, repr=False)


class PhoneIn(Strict):
    phone: str = Field(min_length=1, max_length=40, repr=False)


class CodeIn(Strict):
    code: str = Field(min_length=1, max_length=20, repr=False)


class PasswordIn(Strict):
    password: str = Field(min_length=1, max_length=500, repr=False)


class PeerIn(Strict):
    user_id: int


class DialIn(Strict):
    """No peer field on purpose: unknown keys (peer, user_id, phone, username...) are rejected."""
    confirm_unknown: bool = False


class InstallIn(Strict):
    confirm: bool = False


def call_error(exc: Any) -> HTTPException:
    return HTTPException(_STATUS.get(exc.code, 409), detail=exc.as_dict())


def _manager(request: Request):
    svc = request.app.state.svc
    mgr = getattr(svc, "calls_manager", None)
    if mgr is None:
        mgr = _build(svc)
    return mgr


def _build(svc):
    from ..telegram_calls.call.manager import CallsManager
    from ..telegram_calls.postcall import PostCall
    mgr = CallsManager(svc.settings.data_dir, vault=svc.vault, postcall=PostCall.for_services(svc))
    svc.calls_manager = mgr
    return mgr


async def _run(request: Request, method: str, *args: Any, **kw: Any) -> Any:
    from ..telegram_calls.settings import SettingsError
    from ..telegram_calls.types import CallError
    mgr = _manager(request)
    try:
        result = getattr(mgr, method)(*args, **kw)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except CallError as exc:
        raise call_error(exc) from None
    except SettingsError as exc:
        raise HTTPException(400, detail={"code": "SETTINGS_INVALID", "message": str(exc)}) from None


# ---------------------------------------------------------------- reads (never start the worker)

@router.get("/status")
async def status(request: Request):
    return await _run(request, "status")


@router.get("/settings")
async def get_settings(request: Request):
    return await _run(request, "get_settings")


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request):
    return await _run(request, "set_settings", body.model_dump(exclude_none=True))


@router.get("/history")
async def history(request: Request, limit: int = 20):
    return {"calls": await _run(request, "history", limit)}


@router.get("/events")
async def events(request: Request, since: int = 0, limit: int = 200):
    return await _run(request, "events", since, limit)


# ---------------------------------------------------------------- account

@router.post("/login/credentials")
async def login_credentials(body: CredentialsIn, request: Request):
    try:
        return await _run(request, "login_credentials", body.api_id, body.api_hash)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "NO_CREDENTIALS":
            exc.status_code = 400          # a malformed api pair, not a missing one
        raise


@router.post("/login/start")
async def login_start(body: PhoneIn, request: Request):
    return await _run(request, "login_start", body.phone)


@router.post("/login/code")
async def login_code(body: CodeIn, request: Request):
    return await _run(request, "login_code", body.code)


@router.post("/login/password")
async def login_password(body: PasswordIn, request: Request):
    return await _run(request, "login_password", body.password)


@router.post("/login/cancel")
async def login_cancel(request: Request):
    return await _run(request, "login_cancel")


@router.post("/logout")
async def logout(request: Request):
    return await _run(request, "logout")


@router.get("/contacts")
async def contacts(request: Request):
    return await _run(request, "contacts")


# ---------------------------------------------------------------- the one allowed peer

@router.post("/peer")
async def select_peer(body: PeerIn, request: Request):
    """Choose (validate) the second account. NOT confirmed until /peer/confirm."""
    return await _run(request, "select_peer", body.user_id)


@router.post("/peer/confirm")
async def confirm_peer(body: PeerIn, request: Request):
    return await _run(request, "confirm_peer", body.user_id)


@router.delete("/peer")
async def clear_peer(request: Request):
    return await _run(request, "clear_peer")


# ---------------------------------------------------------------- calls

@router.post("/dial")
async def dial(body: DialIn, request: Request):
    return await _run(request, "dial", body.confirm_unknown)


@router.post("/hangup")
async def hangup(request: Request):
    return await _run(request, "hangup")


@router.post("/stop")
async def stop(request: Request):
    return await _run(request, "stop", "owner")


@router.post("/resume")
async def resume(request: Request):
    return await _run(request, "resume")


# ---------------------------------------------------------------- doctor / selftest / install (later packages)

def _later(module: str, func: str):
    try:
        return getattr(importlib.import_module(f"bcc.telegram_calls.{module}"), func)
    except (ImportError, AttributeError):
        return None


@router.get("/doctor")
async def doctor(request: Request):
    fn = _later("doctor", "run_checks")
    data_dir = request.app.state.svc.settings.data_dir
    if fn is None:
        return {"checks": [{"id": "doctor", "status": "BLOCKED", "code": "NOT_IMPLEMENTED_YET",
                            "message": "Диагностика звонков недоступна."}]}
    try:
        checks = await asyncio.to_thread(fn, data_dir)
    except Exception as exc:  # noqa: BLE001 - a broken check must not become a 500 with details
        return {"checks": [{"id": "doctor", "status": "BLOCKED", "code": "INTERNAL",
                            "message": f"Диагностика упала: {type(exc).__name__}"}]}
    return {"checks": list(checks)}


@router.post("/selftest")
async def selftest(request: Request):
    fn = _later("call.selftest", "run")
    if fn is None:
        return {"status": "NOT_IMPLEMENTED_YET", "ok": False, "message": "Самопроверка недоступна."}
    try:
        return await asyncio.to_thread(fn, request.app.state.svc.settings.data_dir)
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "ok": False, "code": "INTERNAL",
                "message": f"Самопроверка упала: {type(exc).__name__}"}


@router.post("/install")
async def install(body: InstallIn, request: Request):
    """Owner-triggered install of the optional dependencies; explicit ``confirm: true`` required."""
    if not body.confirm:
        raise HTTPException(400, detail={"code": "CONFIRM_REQUIRED",
                                         "message": "Установка требует явного подтверждения.",
                                         "hint": "Отправьте confirm: true (кнопка «Установить» / bossman call install)."})
    fn = _later("addons", "install")
    if fn is None:
        return {"status": "NOT_IMPLEMENTED_YET", "ok": False, "message": "Установка недоступна."}
    try:
        return await asyncio.to_thread(fn, request.app.state.svc.settings.data_dir)
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "ok": False, "code": "INTERNAL",
                "message": f"Установка упала: {type(exc).__name__}"}


# ---------------------------------------------------------------- lifecycle

async def setup(svc) -> None:
    """Build the one manager, hook the global STOP bus, and shut the worker down with the app."""
    mgr = getattr(svc, "calls_manager", None) or _build(svc)
    mgr.attach_bus(svc.bus)

    async def _guardian() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            with contextlib.suppress(BaseException):
                await mgr.shutdown()

    svc._tasks.append(asyncio.get_running_loop().create_task(_guardian(), name="bcc-telegram-calls"))


FEATURE = Feature(name="telegram_calls", router=router, setup=setup)
