"""Owner-input API: phone -> Bossman -> browser fields, without model-visible values."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..owner_input import OwnerInputError, OwnerInputStore
from . import Feature

router = APIRouter(prefix="/owner-input", tags=["owner-input"])


def _store(svc) -> OwnerInputStore:
    store = getattr(svc, "owner_input", None)
    if store is None:
        store = OwnerInputStore(Path(svc.settings.data_dir) / "owner-input" / "requests.json", svc.vault)
        svc.owner_input = store
    return store


@router.get("/pending")
async def pending(request: Request):
    return {"requests": _store(request.app.state.svc).pending()}


@router.get("/{request_id}")
async def get_one(request_id: str, request: Request):
    row = _store(request.app.state.svc).get(request_id)
    if row is None:
        raise HTTPException(404, {"code": "OWNER_INPUT_NOT_FOUND"})
    return row


@router.post("/{request_id}/answer")
async def answer(request_id: str, request: Request):
    body = await request.json()
    try:
        return _store(request.app.state.svc).answer(
            request_id, body.get("values"), actor=str(body.get("actor") or "owner-api"))
    except OwnerInputError as exc:
        raise HTTPException(409, {"code": "OWNER_INPUT_REJECTED", "message": str(exc)}) from exc


@router.post("/{request_id}/cancel")
async def cancel(request_id: str, request: Request):
    try:
        return _store(request.app.state.svc).cancel(request_id)
    except OwnerInputError as exc:
        raise HTTPException(404, {"code": "OWNER_INPUT_NOT_FOUND", "message": str(exc)}) from exc


async def _setup(svc):
    svc.owner_input = OwnerInputStore(Path(svc.settings.data_dir) / "owner-input" / "requests.json", svc.vault)


FEATURE = Feature(name="owner_input", router=router, setup=_setup)
