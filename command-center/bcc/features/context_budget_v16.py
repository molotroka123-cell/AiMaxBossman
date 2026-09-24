"""Context budget contracts for local-model workers."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextBudget:
    capacity_tokens: int
    output_reserve: int
    system_tool_reserve: int
    safety_reserve: int
    max_utilization: float

    def evidence_budget(self) -> int:
        if self.capacity_tokens <= 0 or not 0 < self.max_utilization <= 1:
            raise ValueError("invalid context budget")
        usable = int(self.capacity_tokens * self.max_utilization)
        return max(0, usable - self.output_reserve - self.system_tool_reserve - self.safety_reserve)


DEFAULT_UTILIZATION = {
    "router": .15,
    "specialist": .30,
    "verifier": .25,
    "deep_reasoning": .50,
}


@dataclass(frozen=True)
class EvidenceItem:
    ref: str
    tokens: int
    relevance: float
    novelty: float = 0.0
    mandatory: bool = False


def select_evidence(items: list[EvidenceItem], token_budget: int) -> list[EvidenceItem]:
    """Budgeted evidence selection; mandatory facts first, then value/token."""
    if token_budget < 0:
        raise ValueError("negative token budget")
    chosen: list[EvidenceItem] = []
    used = 0
    mandatory = sorted((x for x in items if x.mandatory), key=lambda x: x.ref)
    for x in mandatory:
        if used + x.tokens > token_budget:
            raise ValueError(f"mandatory evidence exceeds context budget: {x.ref}")
        chosen.append(x); used += x.tokens
    rest = [x for x in items if not x.mandatory and x.tokens > 0]
    rest.sort(key=lambda x: (-((.8*x.relevance+.2*x.novelty)/x.tokens), -x.relevance, x.ref))
    for x in rest:
        if used + x.tokens <= token_budget:
            chosen.append(x); used += x.tokens
    return chosen


def utilization(input_tokens: int, capacity_tokens: int) -> float:
    if capacity_tokens <= 0 or input_tokens < 0:
        raise ValueError("invalid token counts")
    return input_tokens / capacity_tokens
