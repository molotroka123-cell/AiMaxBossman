"""Reasoning 7/10 → 10/10: достаточная глубина вместо "всегда думать дольше".

Хранится только структура (Structured Thought State), НЕ скрытый chain-of-thought.
Режимы: FAST | STANDARD | DEEP | MULTI_HYPOTHESIS | ADVERSARIAL | HUMAN_APPROVAL.
Глубина выбирается Complexity Estimator D с калиброванными порогами.
Остановка — по stop rule (verified / EV следующей проверки < cost / timeout /
approval / честный BLOCKED). Fable — по EV, для P0-security раньше.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Sequence

from .storage import CognitiveStore, stable_id, utcnow_iso


class ReasoningMode(str, Enum):
    FAST = "FAST"                        # простая обратимая задача
    STANDARD = "STANDARD"                # обычное изменение
    DEEP = "DEEP"                        # архитектура, сложный баг
    MULTI_HYPOTHESIS = "MULTI_HYPOTHESIS"  # причина неизвестна
    ADVERSARIAL = "ADVERSARIAL"          # безопасность, обход защиты
    HUMAN_APPROVAL = "HUMAN_APPROVAL"    # необратимое/дорогое действие


@dataclass(slots=True)
class ThoughtState:
    """Структура мышления. Невидимый CoT сюда не пишется."""

    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    verified_facts: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    candidate_plans: list[str] = field(default_factory=list)
    selected_plan: str = ""
    dag: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    verification_requirements: list[str] = field(default_factory=list)
    confidence: float = 0.5
    next_action: str = ""
    stop_condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Complexity Estimator: D = 0.20N + 0.15G + 0.20R + 0.15U + 0.10C + 0.10F + 0.10B
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComplexitySignals:
    novelty: float = 0.0        # N: новизна задачи 0..1
    graph_size: float = 0.0     # G: число зависимостей (нормализованное) 0..1
    risk: float = 0.0           # R: риск 0..1
    uncertainty: float = 0.0    # U: неопределённость 0..1
    conflict: float = 0.0       # C: конфликт evidence 0..1
    past_failures: float = 0.0  # F: предыдущие неудачи 0..1
    budget_pressure: float = 0.0  # B: влияние бюджета 0..1


def complexity_score(sig: ComplexitySignals) -> float:
    """D в диапазоне 0..1. Все входы обязаны быть 0..1 (clamp для надёжности)."""
    c = lambda v: max(0.0, min(1.0, float(v)))
    return (
        0.20 * c(sig.novelty)
        + 0.15 * c(sig.graph_size)
        + 0.20 * c(sig.risk)
        + 0.15 * c(sig.uncertainty)
        + 0.10 * c(sig.conflict)
        + 0.10 * c(sig.past_failures)
        + 0.10 * c(sig.budget_pressure)
    )


@dataclass(frozen=True)
class ModeThresholds:
    """Пороги НЕ выбираются вручную навсегда: калибруются benchmark (см. verify.py).

    Значения ниже — стартовые defaults, зафиксированные как v1. Калибровка
    пишет новую версию в store (thoughts/metric_events) с указанием benchmark SHA.
    """

    version: str = "v1-default"
    deep: float = 0.55
    multi: float = 0.70  # + unknowns>=2 или conflict высокий → MULTI_HYPOTHESIS
    fast_max: float = 0.25

    def pick(
        self,
        d: float,
        *,
        irreversible: bool = False,
        security_sensitive: bool = False,
        unknowns: int = 0,
        conflict: float = 0.0,
    ) -> ReasoningMode:
        if irreversible:
            return ReasoningMode.HUMAN_APPROVAL
        if security_sensitive:
            return ReasoningMode.ADVERSARIAL
        if (d >= self.multi and unknowns >= 2) or conflict >= 0.7:
            return ReasoningMode.MULTI_HYPOTHESIS
        if d >= self.deep:
            return ReasoningMode.DEEP
        if d <= self.fast_max:
            return ReasoningMode.FAST
        return ReasoningMode.STANDARD


def calibrate_thresholds(
    labeled: Sequence[tuple[float, str]],
) -> ModeThresholds:
    """Калибровка порогов на benchmark: labeled = [(D, ideal_mode), ...].

    Подбирает deep/fast_max как середины между классами. Детерминировано.
    """
    fast_ds = [d for d, m in labeled if m == "FAST"]
    std_ds = [d for d, m in labeled if m == "STANDARD"]
    deep_ds = [d for d, m in labeled if m in ("DEEP", "MULTI_HYPOTHESIS")]
    fast_max = 0.25
    deep = 0.55
    if fast_ds and std_ds:
        fast_max = (max(fast_ds) + min(std_ds)) / 2
    if std_ds and deep_ds:
        deep = (max(std_ds) + min(deep_ds)) / 2
    fast_max = max(0.05, min(0.5, fast_max))
    deep = max(fast_max + 0.05, min(0.9, deep))
    return ModeThresholds(version="v-calibrated", deep=deep, fast_max=fast_max)


# ---------------------------------------------------------------------------
# Multi-hypothesis reasoning
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    hid: str
    cause: str
    probability: float = 0.5
    discriminating_test: str = ""
    refuted: bool = False
    confirmed: bool = False


class MultiHypothesisTracker:
    """1) несколько причин → 2) различающий тест → 3) дешёвый информативный тест
    → 4) update вероятностей → 5) исключить опровергнутые → 6) чинить root cause."""

    def __init__(self, hypotheses: Sequence[Hypothesis]) -> None:
        if len(hypotheses) < 2:
            raise ValueError("multi-hypothesis requires >= 2 hypotheses")
        total = sum(h.probability for h in hypotheses) or 1.0
        self.hypotheses: list[Hypothesis] = [
            Hypothesis(h.hid, h.cause, h.probability / total,
                       h.discriminating_test, h.refuted, h.confirmed)
            for h in hypotheses
        ]

    def cheapest_informative_test(self, costs: dict[str, float] | None = None) -> Hypothesis:
        """Самый дешёвый тест среди неподтверждённых/неопровергнутых."""
        costs = costs or {}
        live = [h for h in self.hypotheses if not h.refuted and not h.confirmed]
        if not live:
            raise ValueError("no live hypotheses")
        return min(live, key=lambda h: (costs.get(h.hid, 1.0), -h.probability))

    def observe(self, hid: str, *, supports: bool, strength: float = 0.7) -> None:
        """Байесовское обновление (упрощённое, детерминированное)."""
        strength = max(0.05, min(0.95, strength))
        for h in self.hypotheses:
            if h.hid == hid:
                if supports:
                    h.probability = h.probability * (1 + strength)
                else:
                    h.probability = h.probability * (1 - strength)
                    if strength >= 0.6:
                        h.refuted = True
            else:
                # Конкурирующие гипотезы слегка теряют/набирают массу.
                h.probability = h.probability * (1 - strength * 0.2) if supports else h.probability
        total = sum(h.probability for h in self.hypotheses) or 1.0
        for h in self.hypotheses:
            h.probability /= total
            if h.probability >= 0.9:
                h.confirmed = True

    def confirmed_root_cause(self) -> Hypothesis | None:
        for h in self.hypotheses:
            if h.confirmed and not h.refuted:
                return h
        return None

    def live(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if not h.refuted]


# ---------------------------------------------------------------------------
# Stop rule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StopSignals:
    verified: bool = False
    marginal_gain: float = 1.0   # насколько следующая проверка изменит решение
    next_check_cost: float = 0.0
    expected_benefit: float = 0.0
    timeout_reached: bool = False
    approval_required: bool = False
    evidence_insufficient: bool = False


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str  # verified | low_marginal_gain | cost_exceeds_benefit | timeout |
    #            # approval_required | blocked_insufficient_evidence | continue


def should_stop(s: StopSignals, *, min_gain: float = 0.02) -> StopDecision:
    if s.verified:
        return StopDecision(True, "verified")
    if s.approval_required:
        return StopDecision(True, "approval_required")
    if s.timeout_reached:
        return StopDecision(True, "timeout")
    if s.evidence_insufficient:
        # Честный BLOCKED, а не выдуманный ответ.
        return StopDecision(True, "blocked_insufficient_evidence")
    if s.marginal_gain < min_gain:
        return StopDecision(True, "low_marginal_gain")
    if s.next_check_cost > s.expected_benefit:
        return StopDecision(True, "cost_exceeds_benefit")
    return StopDecision(False, "continue")


# ---------------------------------------------------------------------------
# Fable routing: EV = P(улучшение)*Value − Cost − Latency − Risk
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FableOptions:
    p_improve: float
    value: float
    cost: float
    latency: float
    risk: float


def fable_expected_value(o: FableOptions) -> float:
    return o.p_improve * o.value - o.cost - o.latency - o.risk


def should_call_fable(
    o: FableOptions, *, local_continuation_ev: float = 0.0, p0_security: bool = False
) -> dict[str, Any]:
    ev = fable_expected_value(o)
    if p0_security and o.p_improve > 0:
        # Для P0-security Fable вызывается раньше (даже при EV <= local).
        return {"call": True, "reason": "p0_security_early", "ev_fable": ev}
    return {
        "call": ev > local_continuation_ev,
        "reason": "ev_compare",
        "ev_fable": ev,
        "ev_local": local_continuation_ev,
    }


# ---------------------------------------------------------------------------
# Adaptive Reasoning Controller (durable thought states)
# ---------------------------------------------------------------------------

class ReasoningController:
    def __init__(
        self,
        store: CognitiveStore,
        thresholds: ModeThresholds = ModeThresholds(),
    ) -> None:
        self.store = store
        self.thresholds = thresholds

    def start_thought(
        self,
        *,
        task_id: str,
        run_id: str = "",
        state: ThoughtState,
        signals: ComplexitySignals,
        irreversible: bool = False,
        security_sensitive: bool = False,
    ) -> dict[str, Any]:
        d = complexity_score(signals)
        mode = self.thresholds.pick(
            d, irreversible=irreversible,
            security_sensitive=security_sensitive,
            unknowns=len(state.unknowns), conflict=signals.conflict,
        )
        tid = stable_id("thought", task_id, run_id, utcnow_iso())
        import json as _json

        self.store.execute(
            "INSERT INTO thoughts VALUES (?,?,?,?,?,?)",
            (tid, task_id, run_id, mode.value, utcnow_iso(),
             _json.dumps({**state.to_dict(), "D": d}, ensure_ascii=False)),
        )
        self.store.commit()
        return {"thought_id": tid, "mode": mode.value, "D": round(d, 4)}

    def unsupported_certainty(self, state: ThoughtState) -> bool:
        """Высокая confidence без verified_facts/evidence — нарушение 10/10."""
        return state.confidence >= 0.85 and not state.verified_facts and not state.evidence
