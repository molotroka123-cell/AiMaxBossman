from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from enum import Enum
from typing import Any
ZERO=Decimal('0'); MONEY_QUANT=Decimal('0.000001')
def money(value:Any)->Decimal:
 try: out=value if isinstance(value,Decimal) else Decimal(str(value))
 except (InvalidOperation,ValueError,TypeError) as exc: raise ValueError(f'invalid money value: {value!r}') from exc
 if not out.is_finite() or out<ZERO: raise ValueError('money must be finite and non-negative')
 return out.quantize(MONEY_QUANT)
def utc_day(ts:datetime|None=None)->str:
 ts=ts or datetime.now(timezone.utc)
 if ts.tzinfo is None: ts=ts.replace(tzinfo=timezone.utc)
 return ts.astimezone(timezone.utc).date().isoformat()
class BudgetScope(str,Enum): RUN='run';TASK='task';PROJECT='project';DAILY_GLOBAL='daily_global'
class HardLimitAction(str,Enum): STOP='stop';ASK='ask'
class DecisionKind(str,Enum): ALLOW='allow';REQUIRE_APPROVAL='require_approval';DENY='deny'
class ReservationStatus(str,Enum): ACTIVE='active';COMMITTED='committed';RELEASED='released';EXPIRED='expired'
@dataclass(slots=True,frozen=True)
class BudgetPolicy:
 scope:BudgetScope;hard_limit_usd:Decimal;subject:str='*';warning_fraction:Decimal=Decimal('0.80');hard_action:HardLimitAction=HardLimitAction.STOP;enabled:bool=True
 def __post_init__(self):
  limit=money(self.hard_limit_usd); wf=Decimal(str(self.warning_fraction))
  if not Decimal('0')<wf<Decimal('1'): raise ValueError('warning_fraction must be between 0 and 1')
  if not self.subject: raise ValueError('subject required')
  object.__setattr__(self,'hard_limit_usd',limit);object.__setattr__(self,'warning_fraction',wf)
@dataclass(slots=True,frozen=True)
class BudgetContext:
 run_id:str|int|None=None;task_id:str|int|None=None;project_id:str|int|None=None;owner_device_id:str|None=None;day_utc:str|None=None
 def normalized(self):
  return BudgetContext(None if self.run_id is None else str(self.run_id),None if self.task_id is None else str(self.task_id),None if self.project_id is None else str(self.project_id),self.owner_device_id,self.day_utc or utc_day())
 def fingerprint(self)->str:
  n=self.normalized();raw=json.dumps({'run':n.run_id,'task':n.task_id,'project':n.project_id,'device':n.owner_device_id,'day':n.day_utc},sort_keys=True,separators=(',',':')).encode();return hashlib.sha256(raw).hexdigest()
@dataclass(slots=True,frozen=True)
class BucketSnapshot:
 bucket_key:str;scope:BudgetScope;subject:str;spent_usd:Decimal;reserved_usd:Decimal;hard_limit_usd:Decimal;warning_fraction:Decimal;hard_action:HardLimitAction
@dataclass(slots=True,frozen=True)
class Reservation:
 id:str;idempotency_key:str;context_fingerprint:str;estimated_usd:Decimal;status:ReservationStatus;expires_at:float
@dataclass(slots=True,frozen=True)
class BudgetDecision:
 kind:DecisionKind;reason:str;reservation:Reservation|None=None;required_extra_usd:Decimal=ZERO;warnings:tuple[BucketSnapshot,...]=();exceeded:tuple[BucketSnapshot,...]=()
 @property
 def allowed(self): return self.kind is DecisionKind.ALLOW
