from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Mapping

from bossman_v3.contracts import (
    ApprovalPort,
    ExecutorPort,
    Observation,
    ObservationPort,
    PolicyPort,
    TypedAction,
    VerificationResult,
    VerifierPort,
)

_RAW_SHELL_NAMES = {
    "shell", "exec", "execute_shell", "arbitrary_shell", "cmd", "powershell",
    "bash", "sh", "zsh", "terminal.exec", "subprocess", "os.system",
}


class UnsafeActionError(ValueError):
    pass


class UnsupportedActionError(ValueError):
    pass


class PolicyDeniedError(PermissionError):
    pass


class ApprovalDeniedError(PermissionError):
    pass


class StaleObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionOutcome:
    action: TypedAction
    observation: Observation
    verification: VerificationResult
    effect_id: str | None
    approval_id: str | None
    receipt: ExecutionReceipt | None = None      # что исполнение ЗАЯВИЛО (для ActionReceipt)


class UniversalComputerAgent:
    """Safe typed-action orchestrator.

    Required order:
    policy -> approval (when required) -> executor -> fresh observation -> verifier.
    It never invokes a shell or subprocess itself.
    """

    def __init__(self, policy: PolicyPort, approval: ApprovalPort, executor: ExecutorPort,
                 observer: ObservationPort, verifier: VerifierPort):
        self.policy = policy
        self.approval = approval
        self.executor = executor
        self.observer = observer
        self.verifier = verifier

    @staticmethod
    def _reject_raw_shell(action: TypedAction) -> None:
        normalized = action.action_type.strip().lower()
        if normalized in _RAW_SHELL_NAMES or normalized.startswith("shell."):
            raise UnsafeActionError(
                "Raw shell execution is forbidden. Register a constrained typed action in the canonical Tool Registry."
            )

    def run(self, action: TypedAction, context: Mapping[str, Any] | None = None) -> ActionOutcome:
        context = context or {}
        self._reject_raw_shell(action)
        if not self.executor.supports(action.action_type):
            raise UnsupportedActionError(action.action_type)

        policy = self.policy.authorize(action, context)
        if not policy.allowed:
            raise PolicyDeniedError(policy.reason or "policy denied")

        approval_id = None
        if policy.requires_approval:
            approval = self.approval.request(action, policy, context)
            if not approval.approved:
                raise ApprovalDeniedError(approval.reason or "approval denied")
            approval_id = approval.approval_id

        guard = context.get("execution_guard")
        with guard() if guard is not None else nullcontext():
            receipt = self.executor.execute(action)
        observation = self.observer.observe_fresh(action, receipt)
        if observation.observed_at < receipt.completed_at:
            raise StaleObservationError(
                "Observation predates execution completion; verification would use stale state."
            )
        verification = self.verifier.verify(action, receipt, observation)
        return ActionOutcome(action, observation, verification, receipt.effect_id, approval_id, receipt)
