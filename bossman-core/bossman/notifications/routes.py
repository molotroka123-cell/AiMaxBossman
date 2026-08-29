from __future__ import annotations
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from ..perimeter import SCOPE_ADMIN,require_scope
from .runtime import STORE,TELEGRAM,enqueue_text

router=APIRouter(prefix="/notifications",tags=["notifications"])

@router.get("/status",dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def status():
    return {"queue":STORE.counts(),"telegram_enabled":TELEGRAM.enabled()}

class TestIn(BaseModel):
    text:str=Field(default="BOSSMAN test notification",max_length=500)

@router.post("/test",dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def test_notification(body:TestIn):
    await enqueue_text(body.text,event_type="notification.test")
    return {"queued":True}
