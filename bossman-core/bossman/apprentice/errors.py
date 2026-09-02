"""Typed errors. Every refusal in the apprentice is one of these (never a bare string)."""
from __future__ import annotations


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


class BudgetExhausted(ApprenticeError):
    code = "budget_exhausted"


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
