"""FROZEN red-vs-blue training engine (стресс-тест на будущее железо).

ЗАМОРОЖЕН по умолчанию: `run_episode` не выполнится, пока не пройден тройной
гейт (`gates.assert_lab_enabled`) и не подтверждена одноразовая песочница без
продакшн-секретов и продакшн-сети.

Красная сторона передаёт только `AttackIntent` (абстрактное намерение).
Синяя сторона: observe → detect → classify → contain → log → preserve evidence
→ recover → verify → learning proposal.

Автообучения в продакшн нет: результатом эпизода является ПРЕДЛОЖЕНИЕ на
стадии PROPOSED, дальше — только через benchmark/shadow/verify/owner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import learning, recovery
from .defender import DefenseDecision, defend
from .evidence import EvidenceLedger
from .gates import SandboxFacts, assert_lab_enabled
from .redteam import AttackIntent


@dataclass(frozen=True)
class EpisodeResult:
    scenario_id: str
    attack_class: str
    defense: DefenseDecision
    contained: bool
    evidence_path: str
    proposal: learning.LearningProposal
    recovery_plan: recovery.RecoveryPlan


#: Действия защиты, которые считаются РЕАЛЬНЫМ сдерживанием.
_CONTAINING = {"DENY_AND_QUARANTINE", "ISOLATE_AND_REVERIFY",
               "BREAK_LOOP_AND_REPLAN", "SANITIZE_AND_DEMOTE",
               "REJECT_AND_CONTINUE"}


class FrozenTrainingEngine:
    """Движок стресс-теста. Заморожен, пока гейт не открыт явно."""

    def __init__(self, evidence_root: str | Path) -> None:
        self.ledger = EvidenceLedger(evidence_root)

    def run_episode(self, intent: AttackIntent, sandbox: SandboxFacts) -> EpisodeResult:
        assert_lab_enabled(sandbox)          # LabFrozen, если гейт закрыт
        intent.validate()                    # красная сторона в своих границах

        decision = defend(intent)
        # Сдержано ⇔ защита реально ограничила, а не «вернула какое-то действие».
        contained = decision.action in _CONTAINING

        plan = recovery.plan(
            severity=(decision.ids.severity if decision.ids else "low"),
            contained=contained,
            state_tampered=intent.attack_class.value.endswith("tamper_simulation"),
        )

        record: dict[str, Any] = {
            "scenario": {"id": intent.scenario_id, "class": intent.attack_class.value,
                         "level": intent.level, "surface": intent.target_surface},
            "defense": {"action": decision.action, "rationale": decision.rationale,
                        "containment": int(decision.containment),
                        "detected": list(decision.detected)},
            "ids": (asdict(decision.ids) if decision.ids else None),
            "contained": contained,
            "recovery_steps": [int(s) for s in plan.steps],
            # Сырой текст атаки сохраняем только очищенным (ledger редактирует).
            "untrusted_excerpt": intent.untrusted_text[:500],
        }
        evidence_path = str(self.ledger.write_episode(intent.scenario_id, record))

        proposal = learning.propose(
            intent.scenario_id, intent.attack_class.value, decision.action,
            contained=contained, evidence_ref=evidence_path)

        return EpisodeResult(intent.scenario_id, intent.attack_class.value, decision,
                             contained, evidence_path, proposal, plan)
