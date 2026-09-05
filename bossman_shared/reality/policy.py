"""Owner policy is host-owned; trust scores may only restrict its ceiling."""
from dataclasses import dataclass, asdict
from .contracts import RealityError, digest, integer, unit


@dataclass(frozen=True)
class Constitution:
    version: str
    max_level: int = 1
    max_budget_microusd: int = 3_000_000
    allowed_actions: tuple[str, ...] = ()
    allowed_targets: tuple[str, ...] = ()
    verifiers: tuple[str, ...] = ()
    financial_execution: bool = False

    def __post_init__(self):
        integer(self.max_level, 'max_level')
        integer(self.max_budget_microusd, 'max_budget')
        if self.max_level > 4 or not self.version or type(self.financial_execution) is not bool:
            raise RealityError('invalid constitution')
        for v in (self.allowed_actions, self.allowed_targets, self.verifiers):
            if type(v) is not tuple or any(not isinstance(x, str) or not x for x in v):
                raise RealityError('policy allowlists must be immutable strings')

    @property
    def fingerprint(self):
        return digest(asdict(self))

    def admit(self, mission, *, current_level, cloud=False):
        integer(current_level, 'current_level')
        if mission.policy_digest != self.fingerprint:
            raise RealityError('policy changed or unbound')
        if mission.budget_microusd > self.max_budget_microusd:
            raise RealityError('budget exceeds owner grant')
        if cloud and mission.privacy == 'LOCAL':
            raise RealityError('local data cannot leave host')
        if any(o.verifier not in self.verifiers for o in mission.obligations):
            raise RealityError('untrusted verifier')
        for e in mission.effects:
            if e.required_level > min(current_level, self.max_level):
                raise RealityError('autonomy ceiling')
            if e.action not in self.allowed_actions or e.target not in self.allowed_targets:
                raise RealityError('action/target outside owner grant')
            if e.action.startswith('finance.') and not self.financial_execution:
                raise RealityError('financial execution disabled')


def autonomy_level(*, owner_ceiling, reliability, verifiability, reversibility, confidence, historical_success, risk, divergence=False):
    integer(owner_ceiling, 'owner_ceiling')
    if owner_ceiling > 4:
        raise RealityError('invalid ceiling')
    inputs = [unit(x) for x in (reliability, verifiability, reversibility, confidence, historical_success, risk)]
    if divergence:
        return min(owner_ceiling, 1)
    score = inputs[0] * inputs[1] * inputs[2] * inputs[3] * inputs[4] * (1 - inputs[5])
    # Heuristic restriction, NOT calibrated probability or an authorization grant.
    level = sum(score >= threshold for threshold in (0.2, 0.5, 0.75, 0.95))
    return min(owner_ceiling, level)
