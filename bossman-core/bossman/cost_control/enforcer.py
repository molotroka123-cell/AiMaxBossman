from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .governor import CostGovernor
from .models import BudgetContext,DecisionKind

class BudgetHardStop(RuntimeError):pass
class BudgetApprovalRejected(RuntimeError):pass

@dataclass(slots=True)
class BudgetEnforcer:
    """Async adapter for the existing BOSSMAN approvals boundary.

    `estimated_usd` MUST be a conservative upper bound before the network call.
    """
    governor:CostGovernor
    approval_create:object
    approval_wait:object

    async def reserve(self,context:BudgetContext,estimated_usd,*,idempotency_key:str,
                      cloud_allowed:bool,ttl_s:int=900):
        d=self.governor.reserve_cloud_call(context,estimated_usd,
            idempotency_key=idempotency_key,cloud_allowed=cloud_allowed,ttl_s=ttl_s)
        if d.kind is DecisionKind.ALLOW:return d.reservation
        if d.kind is DecisionKind.DENY:raise BudgetHardStop(d.reason)

        # ASK: request one bounded, single-use override via the existing approval system.
        preview=(
            "BOSSMAN cloud budget override\n"
            f"Extra required: ${d.required_extra_usd}\n"
            f"run={context.run_id or '-'} task={context.task_id or '-'} "
            f"project={context.project_id or '-'}\n"
            "This approval is single-use and does not change the persistent budget."
        )
        aid=await self.approval_create(
            "budget_override",preview,tool="cost_governor",
            payload={"context_fingerprint":context.fingerprint(),
                     "extra_usd":str(d.required_extra_usd),
                     "idempotency_key":idempotency_key},
        )
        result=await self.approval_wait(aid)
        if result.get("status")!="approved":
            raise BudgetApprovalRejected(f"budget approval {result.get('status','unknown')}")
        grant=self.governor.store.issue_override(context,d.required_extra_usd,ttl_s=300)
        retry=self.governor.reserve_cloud_call(context,estimated_usd,
            idempotency_key=idempotency_key,cloud_allowed=cloud_allowed,
            override_token=grant.token,ttl_s=ttl_s)
        if retry.kind is not DecisionKind.ALLOW or retry.reservation is None:
            raise BudgetHardStop("budget changed while approval was pending")
        return retry.reservation

    def commit(self,reservation_id:str,actual_usd):
        return self.governor.commit(reservation_id,actual_usd)

    def release(self,reservation_id:str):
        return self.governor.release(reservation_id)
