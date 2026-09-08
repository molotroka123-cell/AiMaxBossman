"""Ranked execution paths with evidence bands, and a router that only advises.

Ported from `v7/phase1-reality-core` with one substantive change: the original
hard-coded success probabilities (`.90`, `.78`, `.88`) that nothing had
measured. A decimal implies a measurement, and a decimal nobody measured is a
fabricated one that later reads as evidence — the same mistake as calling a
silent model healthy because its endpoint answered.

So a strategy carries a BAND (`unknown` / `low` / `medium` / `high`), derived
from the model-health record the system actually has, and the utility ordering
is over bands plus measured costs. `unknown` sorts below `low` rather than
being optimistically promoted: an unmeasured path is not a good bet, it is an
unknown one.

The router is shadow-only by construction. It ranks; it does not route. Two
separate invariants keep that honest:

  * `strategy score != permission`. Candidates are filtered by the mission's
    permissions BEFORE ranking, so a high score can never surface a path the
    mission is not allowed to take.
  * A recommendation changes nothing until production code chooses to follow
    it, and `telemetry` records both what was recommended and what actually
    happened so the two can be compared before anyone grants it authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import model_health as mh

#: Ordered worst to best. `unknown` is deliberately the floor.
BANDS = ("unknown", "low", "medium", "high")
BAND_WEIGHT = {"unknown": 0.0, "low": 0.35, "medium": 0.65, "high": 0.9}


@dataclass(frozen=True, slots=True)
class Band:
    """An evidence band plus what it was derived from.

    The provenance is not decoration: a band with no samples behind it must be
    visibly different from one backed by twenty observations, or "high" from a
    single lucky probe reads the same as "high" from a week of them."""
    label: str
    samples: int = 0
    source: str = "unmeasured"

    @property
    def weight(self) -> float:
        return BAND_WEIGHT.get(self.label, 0.0)

    @classmethod
    def from_health(cls, record: mh.HealthRecord | None) -> "Band":
        if record is None or record.status == mh.UNMEASURED:
            return cls("unknown", 0, "unmeasured")
        if not record.usable():
            return cls("low", record.samples, f"health={record.status}")
        return cls(record.confidence if record.confidence in BANDS else "low",
                   record.samples, "health=healthy")


@dataclass(frozen=True, slots=True)
class MemoryReading:
    """Measured free memory, or the explicit absence of a measurement.

    `available_mb is None` means nobody measured — which is NOT the same as
    zero and NOT the same as plenty. The V6 resource defect this mirrors was
    exactly that conflation: before the first sample the system planned against
    a fictional 128 GB. Here an unmeasured budget makes memory-hungry paths
    unofferable rather than free.
    """
    available_mb: float | None = None
    source: str = "unmeasured"

    @property
    def measured(self) -> bool:
        return self.available_mb is not None


@dataclass(frozen=True, slots=True)
class Strategy:
    """One way of getting the work done, with its measured costs."""
    strategy_id: str
    path: str
    success_band: Band
    goal_value: float = 100.0
    latency_cost: float = 0.0
    money_cost: float = 0.0
    risk_penalty: float = 0.0
    resource_pressure: float = 0.0
    uncertainty_penalty: float = 0.0
    required_permissions: tuple[str, ...] = ()
    #: Non-empty when this path cannot run at all. It is still returned rather
    #: than dropped, so "why was that not considered" has an answer; the router
    #: filters it out before ranking, exactly like a permission it lacks.
    unavailable_reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return not self.unavailable_reason

    @property
    def utility(self) -> float:
        return (self.success_band.weight * self.goal_value
                - self.latency_cost - self.money_cost - self.risk_penalty
                - self.resource_pressure - self.uncertainty_penalty)

    def terms(self) -> dict[str, Any]:
        """Every component kept separately, not collapsed into the scalar.

        A single number cannot be argued with: "why did it pick that" needs the
        parts, and a router whose reasoning is a float is a router nobody can
        review."""
        return {"strategy_id": self.strategy_id, "path": self.path,
                "band": self.success_band.label, "band_samples": self.success_band.samples,
                "band_source": self.success_band.source,
                "goal_value": self.goal_value, "latency_cost": self.latency_cost,
                "money_cost": self.money_cost, "risk_penalty": self.risk_penalty,
                "resource_pressure": self.resource_pressure,
                "uncertainty_penalty": self.uncertainty_penalty,
                "utility": round(self.utility, 4),
                "available": self.available,
                "unavailable_reason": self.unavailable_reason,
                "required_permissions": list(self.required_permissions)}


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    selected: Strategy | None
    ranked: tuple[Strategy, ...]
    reason: str
    #: Always True in this module. Kept explicit so a future change that makes
    #: the router authoritative has to say so in the data, not just in code.
    shadow_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"selected": self.selected.terms() if self.selected else None,
                "ranked": [s.terms() for s in self.ranked],
                "reason": self.reason, "shadow_only": self.shadow_only}


#: Fraction of measured free memory a path may claim before it is refused.
#: Not a hardware fact — a headroom policy, so the machine keeps room to answer
#: the owner while a model is resident.
MEMORY_HEADROOM = 0.85

#: How much utility a path loses for the memory it occupies, at full headroom.
MEMORY_PRESSURE_WEIGHT = 25.0


def _finite(value: Any) -> float | None:
    """A real number, or None. NaN and infinity are not measurements.

    They arrive from arithmetic that already went wrong — a division by a zero
    sample count, a parsed field that was empty, a subtraction of two unknowns.
    Letting them through is worse than letting a wrong number through, because
    NaN compares False against everything: `needs > budget` is False, so the
    path is judged to FIT and is offered. A quantity nobody can order is not a
    quantity, and here it closes the path rather than opening it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _memory_verdict(reading: "MemoryReading | None", needs_mb: float) -> tuple[float, str]:
    """Resource pressure for a path that needs `needs_mb`, and why if refused.

    Four outcomes, and the middle two are the point:

      * no declared requirement — nothing to weigh, no pressure;
      * a requirement that is not a real number (NaN, infinity, negative) —
        refused. NaN compares False against every bound, so an unguarded
        comparison decides such a path FITS;
      * requirement declared but memory NOT measured — refused. An unmeasured
        budget cannot be shown to fit, and treating unknown as roomy is how a
        planner ends up scheduling a 70 GB model onto a machine nobody sampled;
      * measured — refused when it does not fit inside the headroom, otherwise
        charged in proportion to the headroom it consumes.
    """
    need = _finite(needs_mb)
    if need is None:
        return 0.0, (f"объявленная потребность {needs_mb!r} не является числом — "
                     f"путь не предлагается: сравнить её с бюджетом нельзя")
    if need < 0:
        return 0.0, (f"объявленная потребность {need:.0f} МБ отрицательна — "
                     f"это ошибка вызывающего, а не свободная память")
    if need == 0:
        return 0.0, ""
    if reading is None or not reading.measured:
        return 0.0, (f"нужно {need:.0f} МБ, а свободная память не измерена "
                     f"({reading.source if reading else 'нет показания'}) — "
                     f"неизмеренное не считается достаточным")
    available = _finite(reading.available_mb)
    if available is None or available < 0:
        return 0.0, (f"показание свободной памяти ({reading.available_mb!r}) не "
                     f"является числом — измерением это не считается")
    budget = available * MEMORY_HEADROOM
    if need > budget:
        return 0.0, (f"нужно {need:.0f} МБ, доступно {available:.0f} МБ "
                     f"(с запасом {budget:.0f} МБ) — не помещается")
    if budget <= 0:
        return 0.0, (f"нужно {need:.0f} МБ, а свободной памяти {available:.0f} МБ — "
                     f"не помещается")
    return MEMORY_PRESSURE_WEIGHT * (need / budget), ""


def generate_strategies(*, deterministic_available: bool,
                        model_health: Mapping[str, mh.HealthRecord] | None = None,
                        unknown_facts: int = 0,
                        memory: "MemoryReading | None" = None,
                        model_memory_mb: Mapping[str, float] | None = None
                        ) -> tuple[Strategy, ...]:
    """Candidate paths for a piece of work.

    Deterministic execution is offered only when something can actually do the
    job deterministically — offering it unconditionally and scoring it highest
    would recommend a path that does not exist.

    `unknown_facts` is the count of world facts the projection could not answer
    freshly. It raises the uncertainty penalty on every model-driven path
    equally, because acting on an unknown world is riskier regardless of which
    model acts."""
    health = dict(model_health or {})
    needs = dict(model_memory_mb or {})
    penalty = min(20.0, max(0, int(unknown_facts)) * 1.5)
    candidates: list[Strategy] = []

    def resources(path: str) -> tuple[float, str]:
        return _memory_verdict(memory, float(needs.get(path, 0.0)))
    if deterministic_available:
        # No model call, so no model-health band applies: this path's success
        # depends on code that either exists or does not.
        candidates.append(Strategy(
            "deterministic-tool", "tool", Band("high", 0, "deterministic"),
            latency_cost=5, risk_penalty=5, uncertainty_penalty=penalty))
    small_pressure, small_reason = resources("small_model")
    candidates.append(Strategy(
        "small-model-tools", "small_model", Band.from_health(health.get("small")),
        latency_cost=8, money_cost=1, risk_penalty=8, uncertainty_penalty=penalty,
        resource_pressure=small_pressure, unavailable_reason=small_reason))
    large_pressure, large_reason = resources("large_model")
    candidates.append(Strategy(
        "large-model-tools", "large_model", Band.from_health(health.get("large")),
        latency_cost=18, money_cost=6, risk_penalty=7, uncertainty_penalty=penalty,
        resource_pressure=large_pressure, unavailable_reason=large_reason))
    candidates.append(Strategy(
        "human-escalation", "human", Band("high", 0, "owner"),
        latency_cost=35, risk_penalty=2, uncertainty_penalty=0,
        required_permissions=()))
    return tuple(candidates)


def shadow_route(strategies: Sequence[Strategy], *,
                 permissions: Sequence[str] = (),
                 blocked_paths: Sequence[str] = ()) -> ShadowDecision:
    """Rank candidates. Never routes, never grants.

    Permission filtering happens BEFORE ranking and cannot be outscored: that
    is the mechanical form of `strategy score != permission`."""
    allowed = set(permissions)
    blocked = set(blocked_paths)
    eligible = [s for s in strategies
                if s.available
                and set(s.required_permissions).issubset(allowed) and s.path not in blocked]
    if not eligible:
        refused = "; ".join(f"{s.strategy_id}: {s.unavailable_reason}"
                            for s in strategies if not s.available)
        return ShadowDecision(None, (), "нет кандидатов, разрешённых политикой"
                              + (f" или ресурсами ({refused})" if refused else ""))
    ranked = tuple(sorted(eligible, key=lambda s: (-s.utility, s.strategy_id)))
    top = ranked[0]
    if top.success_band.label == "unknown":
        # Ranking something we have never measured first is a statement about
        # ignorance, not about quality, and the reason must say so.
        return ShadowDecision(top, ranked,
                              "лучший кандидат ни разу не измерен — рекомендация "
                              "основана на стоимости, а не на доказательствах")
    return ShadowDecision(top, ranked,
                          "рекомендация теневого маршрутизатора; production-маршрут не изменён")
