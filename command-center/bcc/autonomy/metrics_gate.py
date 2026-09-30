"""Quality rule of the constitution: before/after metrics -> ACCEPT / REJECT / ROLLBACK.

An improvement is accepted only when the target metric improved (by at least
its ``min_improvement``) and no protected metric degraded beyond its
threshold. Missing or non-numeric values fail closed. Before deployment a
failure is REJECT; after deployment (``deployed=True``, monitoring) it is
ROLLBACK.

Thresholds: ``{metric: float}`` (max allowed regression, absolute) or
``{metric: {"direction": "higher"|"lower", "max_regression": x, "relative": bool,
"min_improvement": y}}``. Without a direction the metric name decides
(latency, cost, errors, leaks, refusals, hallucinations, cpu/ram/disk,
rollbacks are lower-is-better; everything else higher-is-better).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

Decision = Literal["ACCEPT", "REJECT", "ROLLBACK"]

LOWER_IS_BETTER = ("latency", "cost", "error", "errors", "leak", "leaks", "refusal", "refusals", "hallucination",
                   "hallucinations", "cpu", "ram", "memory_mb", "disk", "rollback", "rollbacks", "failures",
                   "violations", "duration", "tokens", "p95", "p99", "ms", "seconds")


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    before: float | None
    after: float | None
    direction: str
    change: float | None          # positive = better
    ok: bool
    note: str


@dataclass(frozen=True)
class GateVerdict:
    decision: Decision
    reasons: tuple[str, ...]
    target: MetricDelta | None
    protected: tuple[MetricDelta, ...]

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPT"

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def direction_of(metric: str) -> str:
    parts = metric.lower().replace("-", "_").replace(".", "_").split("_")
    return "lower" if any(p in LOWER_IS_BETTER for p in parts) else "higher"


def _spec(metric: str, thresholds: Mapping[str, Any]) -> dict:
    raw = thresholds.get(metric, {})
    spec = {"max_regression": float(raw)} if isinstance(raw, (int, float)) and not isinstance(raw, bool) \
        else dict(raw) if isinstance(raw, Mapping) else {}
    spec.setdefault("direction", direction_of(metric))
    spec.setdefault("max_regression", 0.0)
    spec.setdefault("min_improvement", 0.0)
    spec.setdefault("relative", False)
    if spec["direction"] not in ("higher", "lower"):
        raise ValueError(f"{metric}: direction must be higher or lower")
    return spec


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _delta(metric: str, before: Mapping, after: Mapping, spec: dict, *, target: bool) -> MetricDelta:
    b, a = _num(before.get(metric)), _num(after.get(metric))
    d = spec["direction"]
    if b is None or a is None:
        which = "before" if b is None else "after"
        return MetricDelta(metric, b, a, d, None, False, f"{metric}: missing or non-numeric {which} value")
    change = (a - b) if d == "higher" else (b - a)
    scale = abs(b) if spec["relative"] and b != 0 else 1.0
    rel = change / scale
    if target:
        need = float(spec["min_improvement"])
        ok = rel > need if need == 0 else rel >= need
        note = f"{metric}: {b} -> {a} ({'improved' if ok else 'not improved enough'}, need > {need})"
    else:
        allowed = float(spec["max_regression"])
        ok = -rel <= allowed + 1e-12
        note = f"{metric}: {b} -> {a} ({'within' if ok else 'beyond'} allowed regression {allowed})"
    return MetricDelta(metric, b, a, d, change, ok, note)


def decide(before: Mapping[str, Any], after: Mapping[str, Any], target: str, protected: Iterable[str],
           thresholds: Mapping[str, Any] | None = None, *, deployed: bool = False) -> GateVerdict:
    thresholds = thresholds or {}
    fail: Decision = "ROLLBACK" if deployed else "REJECT"
    protected = tuple(dict.fromkeys(protected))
    if not target:
        return GateVerdict(fail, ("no target metric: a task without a measurable result is not accepted",), None, ())
    if target in protected:
        return GateVerdict(fail, ("the target metric cannot also be protected",), None, ())
    try:
        t = _delta(target, before, after, _spec(target, thresholds), target=True)
        prot = tuple(_delta(m, before, after, _spec(m, thresholds), target=False) for m in protected)
    except (TypeError, ValueError) as exc:
        return GateVerdict(fail, (f"invalid thresholds: {exc}",), None, ())
    reasons = [x.note for x in (t, *prot) if not x.ok]
    if reasons:
        return GateVerdict(fail, tuple(reasons), t, prot)
    return GateVerdict("ACCEPT", (t.note,), t, prot)


__all__ = ["Decision", "GateVerdict", "MetricDelta", "decide", "direction_of"]
