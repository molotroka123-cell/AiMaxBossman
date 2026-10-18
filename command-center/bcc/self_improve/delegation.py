"""Delegation guard: depth <= 2, cycles stop the chain, no agent can widen access or touch its own limits.

Nothing here creates agents or grants rights. It only DECIDES (and journals the reason); the
caller must obey. Every refusal is a journal entry of kind ``self_improve.delegation.stop`` or
``self_improve.refusal`` so a stopped chain always leaves its reason behind.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .limits import LimitViolation, Limits
from .store import Store


class DelegationStop(LimitViolation):
    def __init__(self, reason: str, chain: tuple[str, ...]):
        super().__init__(reason)
        self.reason, self.chain = reason, chain


@dataclass
class DelegationChain:
    limits: Limits
    store: Store | None = None
    chain: list[str] = field(default_factory=list)

    def _stop(self, reason: str) -> None:
        if self.store is not None:
            self.store.record("delegation.stop", {"reason": reason, "chain": list(self.chain),
                                                  "max_depth": self.limits.max_delegation_depth})
        raise DelegationStop(reason, tuple(self.chain))

    def enter(self, agent_id: str) -> "DelegationChain":
        """Add one hop. Depth counts delegations: root -> A is depth 1, A -> B is depth 2, B -> C is refused."""
        agent_id = str(agent_id)
        if agent_id in self.chain:
            self._stop(f"cycle: {agent_id} already in chain {'>'.join(self.chain)}")
        if len(self.chain) >= self.limits.max_delegation_depth:
            self._stop(f"depth {len(self.chain) + 1} exceeds limit {self.limits.max_delegation_depth}")
        self.chain.append(agent_id)
        return self

    def leave(self) -> None:
        if self.chain:
            self.chain.pop()


#: fields of an agent definition that grant power. A child may only have a SUBSET of its creator's.
_POWER_LISTS = ("tools",)
_POWER_FLAGS = "permissions"
_NUMERIC_CAPS = ("max_steps", "max_tokens", "budget_usd")


def check_agent_spec(creator: dict, spec: dict) -> list[str]:
    """Return reasons a new agent spec would be BROADER than its creator; empty list = allowed."""
    bad: list[str] = []
    for key in _POWER_LISTS:
        extra = set(spec.get(key) or []) - set(creator.get(key) or [])
        if extra:
            bad.append(f"{key} beyond creator: {sorted(extra)}")
    cperm, sperm = creator.get(_POWER_FLAGS) or {}, spec.get(_POWER_FLAGS) or {}
    for k, v in sperm.items():
        if v and not cperm.get(k):
            bad.append(f"permission beyond creator: {k}")
    for k in _NUMERIC_CAPS:
        if k in spec and k in creator and float(spec[k] or 0) > float(creator[k] or 0):
            bad.append(f"{k} larger than creator's")
    if float(spec.get("budget_usd") or 0) > 0 and float(creator.get("budget_usd") or 0) <= 0:
        bad.append("budget_usd above zero while creator has none")
    return bad


def refuse_self_modification(store: Store | None, actor: str, target: str, what: str) -> None:
    """Always refuses: an agent never changes its own permissions, these limits, the constitution
    or long_run.json. There is no approval path for this - it is a human edit outside the loop."""
    if store is not None:
        store.record("refusal", {"actor": actor, "target": target, "what": what,
                                 "reason": "agents cannot modify their own permissions or the limits"})
    raise LimitViolation(f"refused: {actor} may not change {what} of {target}")


def assert_same_or_narrower(creator: dict, specs: Iterable[dict]) -> None:
    for spec in specs:
        bad = check_agent_spec(creator, spec)
        if bad:
            raise LimitViolation("; ".join(bad))
