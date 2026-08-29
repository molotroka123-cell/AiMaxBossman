"""HTTP-поверхность Stage 6: APIRouter под префиксом /remote.

Каждый маршрут гейтится зависимостью по скоупу (см. security.py):
  POST /remote/devices               — enroll (admin; выдаёт сырой токен ОДИН раз)
  POST /remote/auth                  — открыть сессию (валидный токен устройства)
  GET  /remote/whoami                — self-инфо (любой валидный)
  GET  /remote/events                — SSE-поток событий (events; фильтр по скоупам)
  POST /remote/approvals/{id}        — решение подтверждения (approve → approvals.decide)
  POST /remote/lock                  — экстренная блокировка устройства/всех (admin)
  POST /remote/devices/{id}/revoke   — отзыв устройства (admin)

Осознанно НЕ экспонируем ни одного маршрута мутации cloud_policy или иных
границ безопасности агента: удалённое устройство не может расширить свои права
или ослабить политику (см. README, раздел «Инварианты»).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import approvals
from ..errors import ScopeDenied
from .auth import (
    KNOWN_SCOPES,
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
    Principal,
)
from .events import iter_device_events, sse_wrap
from .security import authenticate_request, require_device_token, require_scope
from .service import get_service

router = APIRouter(prefix="/remote", tags=["remote_client"])


# ---------- enrollment (admin) ----------

class EnrollIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: [SCOPE_CHAT, SCOPE_EVENTS])


@router.post("/devices")
async def enroll_device(body: EnrollIn, principal: Principal = Depends(require_scope(SCOPE_ADMIN))):
    """Зарегистрировать устройство. Требует admin И запрещает выдавать скоупы шире
    собственных (устройство не может выпустить прокси с бОльшими правами)."""
    requested = {s for s in body.scopes if s}
    unknown = requested - KNOWN_SCOPES
    if unknown:
        raise ScopeDenied(f"unknown scopes: {sorted(unknown)}")
    if not requested <= principal.scopes:
        # Нельзя выдать права, которых нет у самого вызывающего — анти-эскалация.
        raise ScopeDenied("cannot grant scopes beyond caller's own")
    device_id, raw_token = await get_service().enroll(body.name, requested)
    # Сырой токен показывается РОВНО один раз и больше нигде не хранится/не логируется.
    return {
        "device_id": device_id,
        "token": raw_token,
        "scopes": sorted(requested),
        "note": "raw token is shown once; store it now — it is never retrievable again",
    }


# ---------- сессия ----------

@router.post("/auth")
async def open_session(principal: Principal = Depends(require_device_token)):
    """Обменять токен устройства на токен сессии (независимо отзываемой)."""
    session_id, raw_session = await get_service().open_session(principal.device_id)
    return {
        "device_id": principal.device_id,
        "session_id": session_id,
        "session_token": raw_session,
        "scopes": sorted(principal.scopes),
        "note": "session token is shown once",
    }


@router.get("/whoami")
async def whoami(principal: Principal = Depends(authenticate_request)):
    return {
        "device_id": principal.device_id,
        "session_id": principal.session_id,
        "name": principal.name,
        "scopes": sorted(principal.scopes),
    }


# ---------- события (events) ----------

@router.get("/events")
async def events_stream(principal: Principal = Depends(require_scope(SCOPE_EVENTS))):
    """SSE-поток. Даже с валидным events-скоупом устройство получает только те
    события, чью категорию покрывают его скоупы (approval → approve и т.д.)."""
    return StreamingResponse(sse_wrap(iter_device_events(principal)),
                             media_type="text/event-stream")


# ---------- подтверждения (approve) ----------

class DecisionIn(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}")
async def device_decide(approval_id: int, body: DecisionIn,
                        principal: Principal = Depends(require_scope(SCOPE_APPROVE))):
    """Решение подтверждения из удалённого клиента — структурно как telegram-путь:
    та же approvals.decide(...). chat-only устройство сюда не пройдёт (нет approve)."""
    row = await approvals.decide(approval_id, body.approve, decided_by=f"device:{principal.device_id}")
    if not row:
        raise HTTPException(409, "already decided or does not exist")
    return row


# ---------- экстренная блокировка / отзыв (admin) ----------

class LockIn(BaseModel):
    locked: bool = True
    device_id: str | None = None  # None => заблокировать ВСЕ устройства


@router.post("/lock")
async def lock(body: LockIn, principal: Principal = Depends(require_scope(SCOPE_ADMIN))):
    """Экстренно заблокировать устройство или все сразу (fail-closed), не удаляя
    enrollment. Пока блокировка активна — каждый запрос падает DeviceRevoked."""
    svc = get_service()
    if body.device_id is None:
        affected = await svc.lock_all(body.locked)
        return {"scope": "all", "locked": body.locked, "affected": affected}
    ok = await svc.lock_device(body.device_id, body.locked)
    if not ok:
        raise HTTPException(404, "device not found")
    return {"device_id": body.device_id, "locked": body.locked}


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str, principal: Principal = Depends(require_scope(SCOPE_ADMIN))):
    ok = await get_service().revoke_device(device_id)
    if not ok:
        raise HTTPException(404, "device not found")
    return {"device_id": device_id, "revoked": True}
