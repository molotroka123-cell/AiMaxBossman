"""Agent Capability Marketplace (§9) — организация просит СПОСОБНОСТЬ, а не
имя модели.

Кандидат допускается, если: включён, из нужного отдела, несёт требуемую роль,
покрывает способность, допущен к уровню риска, не перегружен, и уровень (tier)
не ниже минимального после эскалации. Дальше — детерминированный скоринг, где
порядок лестницы deterministic → local_small → local_strong → cheap_cloud →
frontier важнее всего остального: механическую работу frontier-модель не
получает, пока дешёвый уровень не ДОКАЗАЛ свою ненадёжность.

Эскалация — по данным обучения, не по настроению: на следующую ступень
переходят после провала текущей на этой способности (мандат §9), и каждый
переход виден в решении.

ORG-05: внутри одного tier ранжирование — не по точечной надёжности (чистая
эксплуатация морит голодом новых агентов), а по риску контракта: LOW —
Thompson-выборка из Beta-апостериора с детерминированным seed от digest
контракта (воспроизводимо в тестах, но исследует); MEDIUM — UCB μ + 0.5σ;
HIGH — только μ (никакого исследования на опасной работе, там независимый
ревьюер). Штраф за ложный успех остаётся лексикографически выше.

ORG-06: бюджетная проверка кандидата — ожидаемое число вызовов
|steps| · (1 + retry_rate) · cost_per_call против бюджета контракта, а не цена
одного вызова.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable

from .contracts import DelegationContract
from .learning import OrganizationalLearning
from .models import (AgentProfile, RiskTier, TIER_LADDER, TIER_RANK, VERIFYING_ROLES)

_RISK_RANK = {RiskTier.LOW: 0, RiskTier.MEDIUM: 1, RiskTier.HIGH: 2}


@dataclass(frozen=True)
class RouteDecision:
    selected: tuple[str, ...]
    reason: str
    min_tier: str
    considered: int
    rejected: dict[str, str] = field(default_factory=dict)
    requires_owner: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {"selected": list(self.selected), "reason": self.reason, "min_tier": self.min_tier,
                "considered": self.considered, "rejected": dict(self.rejected),
                "requires_owner": self.requires_owner}


class CapabilityMarketplace:
    def __init__(self, agents: Iterable[AgentProfile], learning: OrganizationalLearning | None = None) -> None:
        self._agents = {a.agent_id: a for a in agents}
        self.learning = learning or OrganizationalLearning()

    def agent(self, agent_id: str) -> AgentProfile | None:
        return self._agents.get(agent_id)

    def agents(self) -> list[AgentProfile]:
        return list(self._agents.values())

    def upsert(self, a: AgentProfile) -> None:
        self._agents[a.agent_id] = a

    # ---------------------------------------------------------- eligibility

    def _reject_reason(self, a: AgentProfile, c: DelegationContract, role: str, min_tier: str,
                       exclude: set[str]) -> str:
        if not a.enabled:
            return "disabled"
        if a.agent_id in exclude:
            return "excluded (producer of the work under review / already failed)"
        if a.department_id != c.department_id:
            return f"department {a.department_id!r} != {c.department_id!r}"
        if role not in a.roles:
            return f"lacks role {role!r}"
        if c.required_capability not in a.capabilities:
            return f"lacks capability {c.required_capability!r}"
        if _RISK_RANK[a.risk_clearance] < _RISK_RANK[c.risk]:
            return f"risk clearance {a.risk_clearance.value} < {c.risk.value}"
        if a.max_load and a.current_load >= a.max_load:
            return "at max load"
        if TIER_RANK[a.tier] < TIER_RANK[min_tier]:
            return f"tier {a.tier} below escalated minimum {min_tier}"
        if str(getattr(c, "privacy", "private")).lower() in ("private", "local_only") and a.tier in ("cheap_cloud", "frontier"):
            return f"cloud-tier agent {a.tier!r} not allowed for {c.privacy} work"      # O001: PRIVATE ⇒ no cloud
        if c.budget.usd and a.cost_per_call_usd:
            st = self.learning.stats(a.agent_id, c.required_capability)
            expected_calls = max(1, len(c.steps)) * (1.0 + st.retry_rate)
            expected_cost = expected_calls * a.cost_per_call_usd
            if expected_cost > c.budget.usd:
                return (f"expected cost {expected_cost:.3f} ({expected_calls:.1f} calls × {a.cost_per_call_usd}) "
                        f"exceeds work budget {c.budget.usd}")
        return ""

    # ------------------------------------------------------------- scoring

    def _quality(self, a: AgentProfile, c: DelegationContract) -> float:
        """Оценка качества внутри tier с исследованием, пропорциональным риску."""
        s = self.learning.stats(a.agent_id, c.required_capability)
        if c.risk == RiskTier.HIGH:
            return s.reliability                                  # только эксплуатация
        if c.risk == RiskTier.MEDIUM:
            return s.reliability + 0.5 * s.uncertainty            # UCB, c = 0.5
        alpha, beta = s.posterior                                 # LOW: Thompson, детерминированный seed
        rng = random.Random(f"{c.digest()}|{a.agent_id}")
        return rng.betavariate(alpha, beta)

    def _score(self, a: AgentProfile, c: DelegationContract) -> tuple:
        s = self.learning.stats(a.agent_id, c.required_capability)
        # Лексикографически: уровень (дешевле — лучше), потом штраф за ложные
        # успехи (это худшее, что может делать агент), потом качество с
        # исследованием, нагрузка, цена, задержка. Имя — детерминированный tie-break.
        return (TIER_RANK[a.tier], round(s.false_success_rate, 3), -round(self._quality(a, c), 4),
                a.current_load, a.cost_per_call_usd, a.latency_ms, a.agent_id)

    # --------------------------------------------------------------- route

    def route(self, c: DelegationContract, *, role: str | None = None, count: int = 1,
              min_tier: str = TIER_LADDER[0], exclude: Iterable[str] = ()) -> RouteDecision:
        role = role or c.required_role
        excl = set(exclude)
        rejected: dict[str, str] = {}
        eligible: list[AgentProfile] = []
        for a in self._agents.values():
            why = self._reject_reason(a, c, role, min_tier, excl)
            if why:
                rejected[a.agent_id] = why
            else:
                eligible.append(a)
        eligible.sort(key=lambda a: self._score(a, c))
        if not eligible:
            return RouteDecision((), f"no eligible agent for role={role!r} capability={c.required_capability!r} "
                                     f"in department {c.department_id!r} (min tier {min_tier})",
                                 min_tier, len(self._agents), rejected, requires_owner=True)
        chosen = tuple(a.agent_id for a in eligible[:count])
        return RouteDecision(chosen, "capability match; ladder tier first; then false-success penalty, "
                                     "reliability, load, cost, latency", min_tier, len(self._agents), rejected)

    def route_reviewer(self, c: DelegationContract, *, producer_id: str, role: str = "reviewer") -> RouteDecision:
        """Независимый проверяющий: не производитель и не та же модель под другим
        именем (иначе это самопроверка, а не ревью)."""
        if role not in VERIFYING_ROLES:
            raise ValueError(f"{role!r} is not a verifying role")
        producer = self._agents.get(producer_id)
        exclude = {producer_id}
        if producer is not None and producer.model:
            exclude |= {a.agent_id for a in self._agents.values()
                        if a.model and a.model.strip().lower() == producer.model.strip().lower()}
        return self.route(c, role=role, exclude=exclude)

    # ---------------------------------------------------------- escalation

    @staticmethod
    def next_tier(current: str) -> str | None:
        i = TIER_RANK[current]
        return TIER_LADDER[i + 1] if i + 1 < len(TIER_LADDER) else None

    def escalated_min_tier(self, c: DelegationContract, *, failed_agents: Iterable[str]) -> str:
        """После провала агента уровня T минимальный уровень становится T+1.
        Никакого перескока через ступени: эскалация ровно на одну."""
        top = TIER_LADDER[0]
        for agent_id in failed_agents:
            a = self._agents.get(agent_id)
            if a is None:
                continue
            nxt = self.next_tier(a.tier)
            if nxt is not None and TIER_RANK[nxt] > TIER_RANK[top]:
                top = nxt
        return top
