from __future__ import annotations
import asyncio
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from ..remote_client.auth import SCOPE_ADMIN,SCOPE_CHAT
from ..remote_client.security import require_scope
from .models import TaskMode
from .subsystem import MANAGER

router=APIRouter(prefix="/computer",tags=["computer-operator"])
class CreateIn(BaseModel):
    goal:str=Field(min_length=1,max_length=12000)
    mode:TaskMode=TaskMode.CONTROL

def view(t):
    return {"id":t.id,"goal":t.goal,"mode":t.mode.value,"state":t.state.value,"steps_used":t.steps_used,
            "max_steps":t.max_steps,"replans_used":t.replans_used,"last_error":t.last_error,
            "waiting_approval_id":t.waiting_approval_id,
            "foreground":t.last_observation.foreground if t.last_observation else None,
            "observation_summary":t.last_observation.summary[:2000] if t.last_observation else None}

def owned(t,p):
    if not t or (not p.has_scope(SCOPE_ADMIN) and t.owner_device_id!=p.device_id):raise HTTPException(404)
    return t

@router.post("/tasks")
async def create(body:CreateIn,p=Depends(require_scope(SCOPE_CHAT))):
    t=MANAGER.create_task(body.goal,mode=body.mode,source=f"core:{p.device_id}",owner_device_id=p.device_id)
    asyncio.create_task(MANAGER.run(t.id));return view(t)

@router.get("/tasks")
async def ls(p=Depends(require_scope(SCOPE_CHAT))):
    return [view(t) for t in MANAGER.store.list() if p.has_scope(SCOPE_ADMIN) or t.owner_device_id==p.device_id]

@router.get("/tasks/{i}")
async def get(i:str,p=Depends(require_scope(SCOPE_CHAT))):return view(owned(MANAGER.store.get(i),p))

@router.post("/tasks/{i}/pause")
async def pause(i:str,p=Depends(require_scope(SCOPE_CHAT))):owned(MANAGER.store.get(i),p);return view(MANAGER.pause(i))

@router.post("/tasks/{i}/resume")
async def resume(i:str,p=Depends(require_scope(SCOPE_CHAT))):
    owned(MANAGER.store.get(i),p);t=MANAGER.resume(i);asyncio.create_task(MANAGER.run(i));return view(t)

@router.post("/tasks/{i}/take-control")
async def take(i:str,p=Depends(require_scope(SCOPE_CHAT))):owned(MANAGER.store.get(i),p);return view(MANAGER.take_control(i))

@router.post("/tasks/{i}/stop")
async def stop(i:str,p=Depends(require_scope(SCOPE_CHAT))):owned(MANAGER.store.get(i),p);return view(MANAGER.stop(i))

@router.post("/emergency-lock")
async def lock(p=Depends(require_scope(SCOPE_ADMIN))):MANAGER.emergency_lock();return {"locked":True}
