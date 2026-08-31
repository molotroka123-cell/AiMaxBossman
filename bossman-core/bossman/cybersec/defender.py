"""BLUE defender — детерминированная реакция на типизированное намерение атаки.

observe → detect → classify → contain. Реакция не зависит от текста атаки
(текст — данные), только от КЛАССА и результата детекторов. Никаких моделей.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import injection, secret_guardian
from .blast_radius import Containment
from .ids import BehaviorSignal, IDSResult, score_behavior
from .redteam import AttackClass, AttackIntent


@dataclass(frozen=True)
class DefenseDecision:
    action: str                 # DENY_AND_QUARANTINE | ISOLATE_AND_REVERIFY | ...
    rationale: str
    containment: Containment
    requires_owner_approval: bool = False
    detected: tuple[str, ...] = ()
    ids: IDSResult | None = None


_AUTHORITY_ATTACKS = {
    AttackClass.SECRET_REQUEST,
    AttackClass.DATA_EXFILTRATION_REQUEST_SIMULATION,
    AttackClass.APPROVAL_BYPASS_ATTEMPT,
    AttackClass.SCOPE_ESCALATION_ATTEMPT,
    AttackClass.SANDBOX_ESCAPE_SIMULATION,
}
_INTEGRITY_ATTACKS = {
    AttackClass.MEMORY_POISON,
    AttackClass.TOOL_RESULT_SPOOF,
    AttackClass.CHECKPOINT_TAMPER_SIMULATION,
    AttackClass.MALICIOUS_SKILL_PROPOSAL,
    # Риск цепочки поставок — тоже вопрос происхождения: допуск решает
    # supply_chain.review_proposal (deny-by-default), а не наблюдение.
    AttackClass.DEPENDENCY_RISK_SIMULATION,
}


def defend(intent: AttackIntent) -> DefenseDecision:
    """Классифицировать и выбрать сдерживание. Fail-closed по умолчанию."""
    intent.validate()

    fw = injection.inspect(intent.untrusted_text)
    exfil = secret_guardian.detect_exfil_request(intent.untrusted_text)
    detected = tuple(f.pattern_id for f in fw.findings) + (("secret_exfil_request",) if exfil.is_request else ())

    ids = score_behavior(BehaviorSignal(
        secret_requests=1 if (exfil.is_request or intent.attack_class is AttackClass.SECRET_REQUEST) else 0,
        scope_escalations=1 if intent.attack_class is AttackClass.SCOPE_ESCALATION_ATTEMPT else 0,
        injection_hits=len(fw.findings),
        sandbox_escape_attempts=1 if intent.attack_class is AttackClass.SANDBOX_ESCAPE_SIMULATION else 0,
        repeated_actions=3 if intent.attack_class is AttackClass.LOOP_INDUCTION else 0,
    ))

    if intent.attack_class is AttackClass.MALFORMED_INPUT:
        # Некорректный ввод отбрасывается на границе и в контекст не попадает —
        # это тоже сдерживание, а не «понаблюдаем».
        return DefenseDecision("REJECT_AND_CONTINUE",
                               "malformed input rejected at the ingest boundary",
                               Containment.SANDBOX, False, detected, ids)
    if intent.attack_class in _AUTHORITY_ATTACKS:
        return DefenseDecision("DENY_AND_QUARANTINE",
                               "authority or data-exposure boundary triggered",
                               Containment.DENY, True, detected, ids)
    if intent.attack_class in _INTEGRITY_ATTACKS:
        return DefenseDecision("ISOLATE_AND_REVERIFY",
                               "integrity/provenance verification required",
                               Containment.REQUIRE_APPROVAL, True, detected, ids)
    if intent.attack_class is AttackClass.LOOP_INDUCTION:
        return DefenseDecision("BREAK_LOOP_AND_REPLAN",
                               "repeated-state/action defense", Containment.SANDBOX,
                               False, detected, ids)
    if not fw.safe:
        return DefenseDecision("SANITIZE_AND_DEMOTE",
                               "injection detected; content demoted to untrusted data",
                               Containment.SANDBOX, False, detected, ids)
    return DefenseDecision("SANDBOX_AND_OBSERVE", "untrusted scenario remains confined",
                           Containment.SANDBOX, False, detected, ids)
