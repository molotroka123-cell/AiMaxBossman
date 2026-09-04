"""Typed errors. Every refusal in the apprentice is one of these (never a bare string)."""
from __future__ import annotations

# BudgetExhausted живёт в общем модуле потолка: отказ выдаёт он, и ловить его
# обязаны обе стороны ОДНИМ И ТЕМ ЖЕ классом. Свой двойник здесь означал бы,
# что `except BudgetExhausted` в одном приложении не ловит отказ другого.
try:
    from .._shared import AVAILABLE as _shared_available  # noqa: F401 — кладёт корень репозитория в sys.path
    from bossman_shared.fable_budget import BudgetExhausted as BudgetExhausted  # noqa: F401
except Exception:  # noqa: BLE001
    # Общего модуля в этой установке нет — значит и платить нечем: без него и
    # прямой транспорт, и Command Center отказывают в любом платном вызове.
    # Разойтись этому классу не с кем, потому что второго в процессе не будет;
    # а ронять из-за потолка весь остальной apprentice было бы несоразмерно.
    class BudgetExhausted(RuntimeError):  # type: ignore[no-redef]
        code = "budget_exhausted"


class ApprenticeError(RuntimeError):
    code = "apprentice_error"


class ApprenticeDisabled(ApprenticeError):
    code = "apprentice_disabled"


class FlagDisabled(ApprenticeError):
    code = "flag_disabled"


class CoordinateTargetForbidden(ApprenticeError):
    code = "coordinate_target_forbidden"


class StaleObservation(ApprenticeError):
    code = "stale_observation"


class WrongWindow(ApprenticeError):
    code = "wrong_window"


class SelectorDrift(ApprenticeError):
    code = "selector_drift"


class DuplicateAction(ApprenticeError):
    code = "duplicate_action"


class InjectionBlocked(ApprenticeError):
    code = "injection_blocked"


class ApprovalRequired(ApprenticeError):
    code = "approval_required"


class ApprovalInvalid(ApprenticeError):
    code = "approval_invalid"


# BudgetExhausted импортируется наверху: он общий с Command Center.


class PolicyRefused(ApprenticeError):
    code = "policy_refused"


class LoopDetected(ApprenticeError):
    code = "loop_detected"


class FalseCompletion(ApprenticeError):
    code = "false_completion"


class SecretInRecord(ApprenticeError):
    code = "secret_in_record"


class LessonBlocked(ApprenticeError):
    code = "lesson_blocked"


class VerificationFailed(ApprenticeError):
    code = "verification_failed"


class FallbackRefused(ApprenticeError):
    code = "fallback_refused"


class TeacherRejected(ApprenticeError):
    code = "teacher_rejected"


class CircuitOpen(ApprenticeError):
    code = "circuit_open"


class PersonalDataRefused(ApprenticeError):
    code = "personal_data_refused"


class OutreachRefused(ApprenticeError):
    code = "outreach_refused"


class IdempotencyKeyRequired(ApprenticeError):
    code = "idempotency_key_required"


class ReceiptInvalid(VerificationFailed):
    code = "receipt_invalid"


class InvalidObservation(StaleObservation):
    code = "invalid_observation"


class UnverifiedEpisode(ApprenticeError):
    code = "unverified_episode"
