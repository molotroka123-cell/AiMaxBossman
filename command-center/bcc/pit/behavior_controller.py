from __future__ import annotations

from dataclasses import dataclass

from .behavior_scores import BehaviorEvent, BehaviorLedger, BehaviorScores
from .discovery import DiscoveryCandidate, DiscoveryMode, choose_discovery_question
from .moderate_discovery import PersonalQuestion, choose_personal_question
from .risk import RiskLedger, RiskState
from .roleplay import RolePlayState, playful_discovery_question
from .topic_policy import SensitiveTopicState
from .vault import PersonaVault


@dataclass(frozen=True, slots=True)
class BehaviorSnapshot:
    risk: RiskState
    behavior: BehaviorScores

    @property
    def engagement(self) -> int:
        return self.behavior.engagement

    @property
    def profile_stability(self) -> int:
        return self.behavior.profile_stability


class BehaviorController:
    """Deterministic local behavior controller.

    LLMs never receive these scores. The controller only tunes:
    - benign discovery cadence;
    - memory confidence floor;
    - whether role-play discovery is eligible.
    """

    def __init__(self, vault: PersonaVault):
        self.vault = vault
        self.risk = RiskLedger(vault)
        self.behavior = BehaviorLedger(vault)

    def snapshot(self, person_key: str) -> BehaviorSnapshot:
        # Security telemetry is advisory. A damaged Windows ACL on its file
        # must not break the participant's conversation or alter consent/data.
        try:
            risk = self.risk.read(person_key)
        except OSError:
            risk = RiskState()
        try:
            behavior = self.behavior.read(person_key)
        except OSError:
            behavior = BehaviorScores()
        return BehaviorSnapshot(
            risk=risk,
            behavior=behavior,
        )

    def privacy_probe(self, person_key: str, *, kind: str, delta: int = 1) -> BehaviorSnapshot:
        try:
            self.risk.add(person_key, delta=max(0, int(delta)), kind=kind)
        except OSError:
            pass
        return self.snapshot(person_key)

    def record(self, person_key: str, event: BehaviorEvent) -> BehaviorSnapshot:
        try:
            self.behavior.apply(person_key, event)
        except OSError:
            pass
        return self.snapshot(person_key)

    def choose_normal_discovery(
        self,
        person_key: str,
        candidates: list[DiscoveryCandidate],
        *,
        enabled: bool,
        mode: DiscoveryMode,
    ) -> DiscoveryCandidate | None:
        state = self.snapshot(person_key)
        return choose_discovery_question(
            candidates,
            enabled=enabled,
            mode=mode,
            risk_score=state.risk.score,
            engagement_score=state.engagement,
        )

    def choose_contextual_personal_question(
        self,
        person_key: str,
        *,
        intent: str,
        already_known: set[str] | frozenset[str],
        skipped: set[str] | frozenset[str],
        enabled: bool,
        roleplay: RolePlayState | None = None,
    ) -> PersonalQuestion | None:
        state = self.snapshot(person_key)
        # Low engagement means stop pushing. High engagement permits one optional
        # contextual question. Risk cannot override a clear lack of engagement.
        if not enabled or state.engagement < 25:
            return None
        if roleplay is not None and roleplay.enabled:
            playful = playful_discovery_question(
                state=roleplay,
                intent=intent,
                already_known=already_known,
                skipped=skipped,
            )
            if playful is not None:
                return playful
        return choose_personal_question(
            intent=intent,
            already_known=already_known,
            skipped=skipped,
            enabled=True,
        )
