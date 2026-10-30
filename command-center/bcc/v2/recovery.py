from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RecoveryAction = Literal["retry", "restart_component", "fallback", "requeue", "escalate"]

@dataclass(slots=True)
class RecoveryPolicy:
    retry_limit: int = 2
    restart_limit: int = 1
    fallback_allowed: bool = True

    def choose(self, *, retries: int, restarts: int, idempotent: bool,
               fallback_available: bool) -> RecoveryAction:
        if idempotent and retries < self.retry_limit:
            return "retry"
        if restarts < self.restart_limit:
            return "restart_component"
        if self.fallback_allowed and fallback_available:
            return "fallback"
        if idempotent:
            return "requeue"
        return "escalate"


LaneName = Literal["owner_generation", "interactive", "background"]


@dataclass(slots=True)
class LaneTask:
    task_id: str | int
    lane: LaneName
    priority: int = 5


def schedule_lanes(tasks: list[LaneTask], *, active_owner_generations: int = 0) -> dict:
    """Lane scheduler: owner generations always preempt; background tasks pause when GPU generation is active."""
    lane_order = {"owner_generation": 0, "interactive": 1, "background": 2}
    runnable: list[str | int] = []
    deferred_background: list[str | int] = []
    for t in sorted(tasks, key=lambda x: (lane_order.get(x.lane, 9), -int(x.priority), str(x.task_id))):
        if t.lane == "background" and active_owner_generations > 0:
            deferred_background.append(t.task_id)
        else:
            runnable.append(t.task_id)
    return {
        "active_owner_generations": active_owner_generations,
        "runnable": runnable,
        "deferred_background": deferred_background,
        "owner_preempts_background": True,
    }


def classify_and_recover(error_text: str, *, retries: int = 0, fallback_available: bool = True,
                         base_backoff_s: float = 30.0) -> dict:
    """Failure taxonomy with fast recovery (< 0.5 of uncalibrated backoff) for transient and fallback-eligible errors."""
    low = (error_text or "").lower()
    if any(k in low for k in ("401", "403", "unauthorized", "invalid api key", "spend meter")):
        return {"category": "auth_or_budget", "action": "escalate", "delay_s": 0.0, "naive_delay_s": base_backoff_s}
    if any(k in low for k in ("429", "rate limit", "quota", "throttled")) and fallback_available:
        # Immediate failover to healthy free/local model instead of waiting 30s
        return {"category": "rate_limit", "action": "fallback", "delay_s": 0.5, "naive_delay_s": base_backoff_s}
    if any(k in low for k in ("timeout", "timed out", "502", "503", "connection")):
        action = "fallback" if (retries >= 1 and fallback_available) else "retry"
        delay = 1.0 if action == "fallback" else min(5.0, base_backoff_s * 0.2)
        return {"category": "transient_network", "action": action, "delay_s": delay, "naive_delay_s": base_backoff_s}
    return {"category": "permanent_or_unknown", "action": "escalate", "delay_s": 0.0, "naive_delay_s": base_backoff_s}


# ------------------------------------------------------------- CUA-Recovery (arXiv:2610.27645)
# Сверка наблюдаемого состояния с ожидаемым перед следующим шагом в длинных сценариях
# (неверное поле формы, закрытый диалог, неудачное сохранение) с остановкой или откатом
# до следующего необратимого действия.

InjectedFaultKind = Literal["wrong_form_field", "closed_dialog", "failed_save", "none"]
StepGuardDecision = Literal["proceed", "rollback_and_retry", "halt_before_irreversible"]


@dataclass(frozen=True, slots=True)
class StepStateCheck:
    step_index: int
    action_name: str
    irreversible: bool
    expected_state: dict
    observed_state: dict
    matched: bool
    mismatched_keys: tuple[str, ...]
    detected_fault: InjectedFaultKind
    decision: StepGuardDecision
    repaired_state: dict | None = None


def detect_mid_step_fault(expected: dict, observed: dict) -> tuple[bool, tuple[str, ...], InjectedFaultKind]:
    """Сравнивает ожидаемое состояние шага с наблюдаемым и классифицирует тип сбоя."""
    mismatches: list[str] = []
    for k, exp_val in (expected or {}).items():
        if observed.get(k) != exp_val:
            mismatches.append(str(k))
    if not mismatches:
        return True, (), "none"
    keys = set(mismatches)
    if "active_field" in keys or "field_value" in keys or "focused_input" in keys:
        fault: InjectedFaultKind = "wrong_form_field"
    elif "dialog_open" in keys or "modal_id" in keys or observed.get("dialog_open") is False:
        fault = "closed_dialog"
    elif "saved" in keys or "disk_sha256" in keys or "save_status" in keys or observed.get("saved") is False:
        fault = "failed_save"
    else:
        fault = "failed_save"
    return False, tuple(sorted(mismatches)), fault


class StepStateGuard:
    """Предшаговый барьер состояния для длинных сценариев Computer Use (CUA-Recovery).

    Перед каждым действием (особенно необратимым: submit/delete/pay/publish) сверяет
    наблюдаемое состояние UI/приложения с ожидаемым результатом предыдущего шага.
    При расхождении блокирует каскадный сбой: выполняет локальный откат/восстановление
    (перефокусировка поля, повторное открытие диалога, повтор сохранения) либо останавливает
    цепочку до необратимого действия.
    """

    def __init__(self, *, allow_auto_repair: bool = True):
        self.allow_auto_repair = allow_auto_repair

    def verify_before_next_action(
        self,
        *,
        step_index: int,
        action_name: str,
        irreversible: bool,
        expected_state: dict,
        observed_state: dict,
        checkpoint_state: dict | None = None,
    ) -> StepStateCheck:
        matched, mismatches, fault = detect_mid_step_fault(expected_state, observed_state)
        if matched:
            return StepStateCheck(
                step_index=step_index,
                action_name=action_name,
                irreversible=irreversible,
                expected_state=dict(expected_state),
                observed_state=dict(observed_state),
                matched=True,
                mismatched_keys=(),
                detected_fault="none",
                decision="proceed",
                repaired_state=dict(observed_state),
            )
        # Если шаг необратимый и нет безопасного чекпойнта восстановления — жёсткая остановка
        if self.allow_auto_repair and checkpoint_state is not None:
            repaired = dict(checkpoint_state)
            repaired.update(expected_state)
            return StepStateCheck(
                step_index=step_index,
                action_name=action_name,
                irreversible=irreversible,
                expected_state=dict(expected_state),
                observed_state=dict(observed_state),
                matched=False,
                mismatched_keys=mismatches,
                detected_fault=fault,
                decision="rollback_and_retry",
                repaired_state=repaired,
            )
        return StepStateCheck(
            step_index=step_index,
            action_name=action_name,
            irreversible=irreversible,
            expected_state=dict(expected_state),
            observed_state=dict(observed_state),
            matched=False,
            mismatched_keys=mismatches,
            detected_fault=fault,
            decision="halt_before_irreversible",
            repaired_state=None,
        )

