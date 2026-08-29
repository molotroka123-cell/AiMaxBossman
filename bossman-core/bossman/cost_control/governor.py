from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable
from .models import BudgetContext,BudgetDecision,DecisionKind,money
from .store import SQLiteBudgetStore

EmitFn=Callable[...,None]

@dataclass(slots=True)
class CostGovernor:
    store:SQLiteBudgetStore
    emit:EmitFn
    def reserve_cloud_call(self,context:BudgetContext,estimated_usd,*,idempotency_key:str,
                           cloud_allowed:bool,override_token:str|None=None,ttl_s:int=900)->BudgetDecision:
        amount=money(estimated_usd)
        if amount>Decimal("0") and not cloud_allowed:
            d=BudgetDecision(DecisionKind.DENY,"cloud policy forbids external call")
            self.emit("budget.exceeded",reason=d.reason,amount_usd=str(amount),
                      context_fingerprint=context.fingerprint()[:16])
            return d
        d=self.store.reserve(context,amount,idempotency_key=idempotency_key,
                             override_token=override_token,ttl_s=ttl_s)
        for s in d.warnings:
            self.emit("budget.warning",scope=s.scope.value,subject=s.subject,
                      projected_usd=str(s.spent_usd+s.reserved_usd+amount),
                      limit_usd=str(s.hard_limit_usd),warning_fraction=str(s.warning_fraction))
        if d.kind in {DecisionKind.DENY,DecisionKind.REQUIRE_APPROVAL}:
            self.emit("budget.exceeded",action=d.kind.value,reason=d.reason,
                      required_extra_usd=str(d.required_extra_usd),
                      scopes=[s.scope.value for s in d.exceeded])
        return d
    def extend(self,reservation_id:str,additional_usd,*,override_token:str|None=None):
        return self.store.extend_reservation(reservation_id,additional_usd,override_token=override_token)
    def commit(self,reservation_id:str,actual_usd):
        return self.store.commit(reservation_id,actual_usd)
    def release(self,reservation_id:str):
        return self.store.release(reservation_id)
    def cleanup_expired(self)->int:
        return self.store.cleanup_expired()
