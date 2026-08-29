from __future__ import annotations
from decimal import Decimal
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from ..perimeter import SCOPE_ADMIN,SCOPE_CHAT,require_scope
from .models import BudgetPolicy,BudgetScope,HardLimitAction
from .runtime import STORE

router=APIRouter(prefix="/budget",tags=["cost-governor"])

class PolicyIn(BaseModel):
    scope:BudgetScope
    subject:str=Field(default="*",min_length=1,max_length=200)
    hard_limit_usd:Decimal=Field(gt=0)
    warning_fraction:Decimal=Field(default=Decimal("0.80"),gt=0,lt=1)
    hard_action:HardLimitAction=HardLimitAction.STOP
    enabled:bool=True

@router.get("/policies",dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def list_policies():
    return [{"scope":p.scope.value,"subject":p.subject,"hard_limit_usd":str(p.hard_limit_usd),
             "warning_fraction":str(p.warning_fraction),"hard_action":p.hard_action.value,
             "enabled":p.enabled} for p in STORE.list_policies()]

@router.put("/policies",dependencies=[Depends(require_scope(SCOPE_ADMIN))])
async def put_policy(body:PolicyIn):
    STORE.set_policy(BudgetPolicy(scope=body.scope,subject=body.subject,
        hard_limit_usd=body.hard_limit_usd,warning_fraction=body.warning_fraction,
        hard_action=body.hard_action,enabled=body.enabled))
    return {"ok":True}

@router.get("/status",dependencies=[Depends(require_scope(SCOPE_CHAT))])
async def status():
    return {"buckets":STORE.snapshots()}
