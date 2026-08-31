"""Thin adapter examples for the existing AiMaxBossman core.

IMPORTANT: this file intentionally does not instantiate or replace canonical
Gateway/Tool Registry/Policy/Approval/EventBus/Memory/Computer Operator.
Wire existing objects into the Protocols in bossman_v3.contracts.
"""
from __future__ import annotations
from typing import Any, Mapping
from bossman_v3.contracts import TypedAction, PolicyDecision, ApprovalDecision

class ExistingPolicyAdapter:
    def __init__(self, canonical_policy): self._policy=canonical_policy
    def authorize(self, action: TypedAction, context: Mapping[str,Any]) -> PolicyDecision:
        result=self._policy.authorize(action=action, context=context)
        if isinstance(result, PolicyDecision): return result
        return PolicyDecision(bool(getattr(result,"allowed",False)), bool(getattr(result,"requires_approval",False)), str(getattr(result,"reason","")))

class ExistingApprovalAdapter:
    def __init__(self, canonical_approval): self._approval=canonical_approval
    def request(self, action: TypedAction, policy: PolicyDecision, context: Mapping[str,Any]) -> ApprovalDecision:
        result=self._approval.request(action=action, context=context)
        if isinstance(result, ApprovalDecision): return result
        return ApprovalDecision(bool(getattr(result,"approved",False)), getattr(result,"approval_id",None), str(getattr(result,"reason","")))

# Executor/observer/verifier adapters should be equally thin and repository-specific.
# Map only PRE-REGISTERED typed actions from the canonical Tool Registry/Computer Operator.
# Never expose a generic `run(command: str)` or shell passthrough here.
