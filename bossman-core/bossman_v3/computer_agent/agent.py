from __future__ import annotations

from dataclasses import dataclass, replace
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from bossman_v3.contracts import (
    ApprovalPort,
    ExecutorPort,
    ExecutionReceipt,
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
                 observer: ObservationPort, verifier: VerifierPort, *,
                 pre_dispatch: Callable[[TypedAction, Mapping[str, Any]], None] | None = None):
        self.policy = policy
        self.approval = approval
        self.executor = executor
        self.observer = observer
        self.verifier = verifier
        self.pre_dispatch = pre_dispatch

    @staticmethod
    def _reject_raw_shell(action: TypedAction) -> None:
        normalized = action.action_type.strip().lower()
        if normalized in _RAW_SHELL_NAMES or normalized.startswith("shell."):
            raise UnsafeActionError(
                "Raw shell execution is forbidden. Register a constrained typed action in the canonical Tool Registry."
            )

    def run(self, action: TypedAction, context: Mapping[str, Any] | None = None) -> ActionOutcome:
        context = dict(context or {})
        self._reject_raw_shell(action)
        # Nested args in a frozen TypedAction may still be caller-owned mutable
        # objects. Detach them, retain a full fingerprint (including expect),
        # and never verify or record a different action after callbacks run.
        from ..visual_state.action_state import _action_digest
        from ..visual_state.models import _canonical_json
        original = action
        expected_digest = _action_digest(original)
        action = replace(original, args=json.loads(_canonical_json(dict(original.args))))
        def check_action() -> None:
            if _action_digest(original) != expected_digest or _action_digest(action) != expected_digest:
                raise UnsafeActionError("action or expectation changed during execution; reauthorization required")
        check_action()
        if not self.executor.supports(action.action_type):
            raise UnsupportedActionError(action.action_type)

        policy = self.policy.authorize(action, context)
        check_action()
        if not policy.allowed:
            raise PolicyDeniedError(policy.reason or "policy denied")

        approval_id = None
        approved = False
        if policy.requires_approval:
            approval = self.approval.request(action, policy, context)
            check_action()
            if not approval.approved:
                raise ApprovalDeniedError(approval.reason or "approval denied")
            approval_id = approval.approval_id
            approved = True

        guard = context.get("execution_guard")
        with guard() if guard is not None else nullcontext():
            check_action()
            if self.pre_dispatch is not None:
                # Host-installed semantic/recipe validator, never model data.
                # It runs while the canonical guard owns the effect boundary.
                self.pre_dispatch(action, context)
                check_action()
            current_policy = self.policy.authorize(action, context)
            check_action()
            if not current_policy.allowed:
                raise PolicyDeniedError(current_policy.reason or "policy revoked before effect")
            if current_policy.requires_approval and not approved:
                # Do not block on a new human request while holding the guard.
                raise ApprovalDeniedError("current policy now requires approval; retry through approval queue")
            revalidate = getattr(self.approval, "revalidate", None)
            if approved and revalidate is not None:
                if not revalidate(action, approval_id, context):
                    raise ApprovalDeniedError("approval revoked or bound action changed before effect")
                check_action()
            if not self.executor.supports(action.action_type):
                raise UnsupportedActionError(action.action_type)
            check_action()
            record_intent = context.get("record_effect_intent")
            if record_intent is not None:
                record_intent()
                check_action()
            receipt = self.executor.execute(action)
            check_action()
        observation = self.observer.observe_fresh(action, receipt)
        check_action()
        if observation.observed_at < receipt.completed_at:
            raise StaleObservationError(
                "Observation predates execution completion; verification would use stale state."
            )
        verification = self.verifier.verify(action, receipt, observation)
        check_action()
        return ActionOutcome(action, observation, verification, receipt.effect_id, approval_id, receipt)
