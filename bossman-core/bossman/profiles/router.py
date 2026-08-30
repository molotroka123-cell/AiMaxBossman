"""HTTP-поверхность профилей: APIRouter под /profiles.

Управление — только для admin-скоупа (владелец сервера). Гость/устройство свои
права расширить не может (симметрично remote_client). Секреты не отдаются.

  POST   /profiles                    — создать аккаунт (admin)
  GET    /profiles                    — список (admin)
  GET    /profiles/{id}               — один (admin)
  PATCH  /profiles/{id}/toggles       — изменить переключатели доступа (admin)
  POST   /profiles/{id}/enabled       — включить/выключить (admin)
  POST   /profiles/{id}/bind          — привязать device_id / telegram_user_id (admin)
  GET    /profiles/{id}/access/{cap}  — решение gate по capability (admin; для UI)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..remote_client.auth import SCOPE_ADMIN
from ..remote_client.security import require_scope
from . import gate
from .models import TOGGLES
from .service import get_service

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _svc():
    svc = get_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="profiles service unavailable")
    return svc


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    device_id: str | None = None
    telegram_user_id: str | None = None
    toggles: dict[str, bool] | None = None


class TogglesIn(BaseModel):
    toggles: dict[str, bool] = Field(default_factory=dict)


class EnabledIn(BaseModel):
    enabled: bool


class BindIn(BaseModel):
    device_id: str | None = None
    telegram_user_id: str | None = None


@router.get("/_vocabulary", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def vocabulary():
    """Полный словарь переключателей с безопасными дефолтами (для UI)."""
    return {"toggles": dict(TOGGLES)}


@router.post("", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def create(body: CreateIn):
    prof = _svc().store.create(
        body.name, device_id=body.device_id,
        telegram_user_id=body.telegram_user_id, toggles=body.toggles)
    return prof.to_row()


@router.get("", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def list_profiles():
    return {"profiles": [p.to_row() for p in _svc().store.list()]}


@router.get("/{profile_id}", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def get_one(profile_id: str):
    prof = _svc().store.get(profile_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return prof.to_row()


@router.patch("/{profile_id}/toggles", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def patch_toggles(profile_id: str, body: TogglesIn):
    prof = _svc().store.update_toggles(profile_id, body.toggles)
    if prof is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return prof.to_row()


@router.post("/{profile_id}/enabled", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def set_enabled(profile_id: str, body: EnabledIn):
    prof = _svc().store.set_enabled(profile_id, body.enabled)
    if prof is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return prof.to_row()


@router.post("/{profile_id}/bind", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def bind(profile_id: str, body: BindIn):
    prof = _svc().store.bind(
        profile_id, device_id=body.device_id, telegram_user_id=body.telegram_user_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return prof.to_row()


@router.get("/{profile_id}/access/{capability}", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def access(profile_id: str, capability: str):
    prof = _svc().store.get(profile_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="profile not found")
    d = gate.decide(prof, capability)
    return {"allow": d.allow, "reason": d.reason, "capability": d.capability, "toggle": d.toggle}
