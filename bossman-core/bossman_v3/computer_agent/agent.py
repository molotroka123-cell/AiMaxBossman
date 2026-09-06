from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
import json
from typing import Any, Mapping

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


def _action_binding(action: TypedAction) -> str:
    """Use the shared contract JSON boundary, not repr/pickle or model claims."""
    from bossman_shared.mission_ir import _canonical, _json_types
    try:
        payload = {
            "action_type": action.action_type, "args": dict(action.args),
            "scopes": list(action.scopes), "side_effect": action.side_effect.value,
            "idempotency_key": action.idempotency_key, "source": action.source,
        }
        _json_types(payload)
        return _canonical(payload)
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise UnsafeActionError("Action cannot be bound to a finite JSON contract") from exc


def snapshot_action(action: TypedAction) -> TypedAction:
    """Detach nested arguments so caller mutations cannot change an admitted effect.

    This is identity preservation, not authorization. The policy port still
    decides at planning AND inside the actual effect guard.
    """
    from ..contracts import SideEffectClass
    data = json.loads(_action_binding(action))
    return TypedAction(data["action_type"], data["args"], tuple(data["scopes"]),
                       SideEffectClass(data["side_effect"]), data["idempotency_key"], data["source"])


def _assert_unchanged(action: TypedAction, binding: str) -> None:
    if _action_binding(action) != binding:
        raise UnsafeActionError("Action changed across an authorization or execution boundary")


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
    policy -> approval (when required) -> guarded current policy -> executor
    -> fresh observation -> verifier. Nested arguments remain identity-bound.
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
        action = snapshot_action(action)
        binding = _action_binding(action)
        if not self.executor.supports(action.action_type):
            raise UnsupportedActionError(action.action_type)

        policy = self.policy.authorize(action, context)
        _assert_unchanged(action, binding)
        if not policy.allowed:
            raise PolicyDeniedError(policy.reason or "policy denied")

        approval_id = None
        approved = False
        if policy.requires_approval:
            approval = self.approval.request(action, policy, context)
            _assert_unchanged(action, binding)
            if not approval.approved:
                raise ApprovalDeniedError(approval.reason or "approval denied")
            approval_id = approval.approval_id
            approved = True

        guard = context.get("execution_guard")
        with guard() if guard is not None else nullcontext():
            _assert_unchanged(action, binding)
            current_policy = self.policy.authorize(action, context)
            _assert_unchanged(action, binding)
            if not current_policy.allowed:
                raise PolicyDeniedError(current_policy.reason or "policy revoked before effect")
            if current_policy.requires_approval and not approved:
                raise ApprovalDeniedError("current policy requires a new owner approval")
            consume = getattr(self.approval, "consume_at_effect", None)
            if approved and consume is not None:
                if not consume(action, approval_id, context):
                    raise ApprovalDeniedError("approval revoked, consumed or action identity changed")
                _assert_unchanged(action, binding)
            receipt = self.executor.execute(action)
            _assert_unchanged(action, binding)
        observation = self.observer.observe_fresh(action, receipt)
        _assert_unchanged(action, binding)
        if observation.observed_at < receipt.completed_at:
            raise StaleObservationError(
                "Observation predates execution completion; verification would use stale state."
            )
        verification = self.verifier.verify(action, receipt, observation)
        _assert_unchanged(action, binding)
        return ActionOutcome(snapshot_action(action), observation, verification, receipt.effect_id, approval_id, receipt)
