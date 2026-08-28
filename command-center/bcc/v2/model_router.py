"""Smart Router — чистая логика выбора модели (без БД и без сети).

V2.1: роутер обязан опираться на ПРОВЕРЕННЫЕ (verified) возможности, а не на
заявленные (advertised). Если проба показала, что tools НЕ работают, модель
исключается из задач, которым tools нужны, даже если каталог их обещает.

Контекст-эффективность: кандидатов может быть много (каталог OpenRouter — сотни
моделей). `shortlist()` режет список ДО скоринга и объяснения, чтобы наверх
(и в промпт) никогда не уезжал весь каталог.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_CANDIDATES = 12          # сколько моделей доходит до полного скоринга
MAX_REJECTED = 24            # сколько отказов объясняем (остальное — счётчиком)


@dataclass(slots=True)
class ModelCandidate:
    id: int | str
    alias: str
    online: bool = True
    local: bool = True
    context_window: int = 8192
    capabilities: set[str] = field(default_factory=set)            # advertised
    verified_capabilities: set[str] = field(default_factory=set)   # проба: OK
    unsupported_capabilities: set[str] = field(default_factory=set)  # проба: FAIL
    price_in: float = 0.0      # USD / 1M
    price_out: float = 0.0
    latency_ms: float | None = None
    gen_tps: float | None = None
    memory_mb: float | None = None
    queue_depth: int = 0
    success_rate: float | None = None
    role_scores: dict[str, float] = field(default_factory=dict)

    def cap_state(self, cap: str) -> str:
        """verified | falsified | advertised | unknown — что мы знаем о способности."""
        if cap in self.unsupported_capabilities:
            return "falsified"
        if cap in self.verified_capabilities:
            return "verified"
        if cap in self.capabilities:
            return "advertised"
        return "unknown"


@dataclass(slots=True)
class RouteRequest:
    task_type: str
    requires: set[str] = field(default_factory=set)
    min_context: int = 0
    cloud_allowed: bool = True
    max_price_out: float | None = None
    available_memory_mb: float | None = None
    prefer_local: bool = True
    max_candidates: int = MAX_CANDIDATES
    require_verified: bool = False   # True → advertised-но-непроверенное тоже отсеять


@dataclass(slots=True)
class RouteDecision:
    model: ModelCandidate | None
    score: float
    reasons: list[str]
    rejected: dict[str, list[str]]
    considered: int = 0        # сколько моделей дошло до скоринга (bounded)
    total: int = 0             # сколько было на входе


def disqualify(req: RouteRequest, m: ModelCandidate) -> list[str]:
    """Жёсткие фильтры. Пустой список = кандидат допущен."""
    bad: list[str] = []
    if not m.online:
        bad.append("unhealthy/offline")
    if not req.cloud_allowed and not m.local:
        bad.append("cloud disabled")
    if req.min_context and m.context_window < req.min_context:
        bad.append(f"context {m.context_window} < {req.min_context}")

    falsified, missing, unproven = [], [], []
    for cap in sorted(req.requires):
        state = m.cap_state(cap)
        if state == "falsified":
            falsified.append(cap)
        elif state == "unknown":
            missing.append(cap)
        elif state == "advertised" and req.require_verified:
            unproven.append(cap)
    if falsified:
        # ГЛАВНОЕ ПРАВИЛО: проба важнее рекламы каталога.
        bad.append("verified NOT supported: " + ", ".join(falsified))
    if missing:
        bad.append("missing capabilities: " + ", ".join(missing))
    if unproven:
        bad.append("capabilities advertised but not verified: " + ", ".join(unproven))

    if req.max_price_out is not None and m.price_out > req.max_price_out:
        bad.append(f"output price {m.price_out} > {req.max_price_out}")
    if (req.available_memory_mb is not None and m.local and m.memory_mb is not None
            and m.memory_mb > req.available_memory_mb):
        bad.append(f"memory {m.memory_mb:.0f}MB > available {req.available_memory_mb:.0f}MB")
    return bad


def _cheap_rank(req: RouteRequest, m: ModelCandidate) -> float:
    """Дешёвый предварительный ранг для отбора shortlist (без объяснений)."""
    r = 0.0
    if req.prefer_local and m.local:
        r += 14
    r += float(m.role_scores.get(req.task_type, 0.0)) * 20
    if m.success_rate is not None:
        r += (m.success_rate - 0.5) * 20
    # проверенные способности — дополнительный вес: пробы стоят дороже рекламы
    r += 2.0 * len(req.requires & m.verified_capabilities)
    if not m.local:
        r -= min((m.price_in + m.price_out) / 2.0, 20.0)
    return r


def shortlist(req: RouteRequest, models: list[ModelCandidate],
              limit: int | None = None) -> tuple[list[ModelCandidate], dict[str, list[str]]]:
    """Отфильтровать и ОГРАНИЧИТЬ кандидатов.

    Возвращает (кандидаты ≤ limit, объяснения отказов ≤ MAX_REJECTED).
    Никто выше по стеку не должен видеть весь каталог.
    """
    cap = max(1, int(limit if limit is not None else req.max_candidates))
    rejected: dict[str, list[str]] = {}
    ok: list[ModelCandidate] = []
    for m in models:
        bad = disqualify(req, m)
        if bad:
            if len(rejected) < MAX_REJECTED:
                rejected[m.alias] = bad
            continue
        ok.append(m)
    ok.sort(key=lambda m: _cheap_rank(req, m), reverse=True)
    dropped = ok[cap:]
    for m in dropped:
        if len(rejected) < MAX_REJECTED:
            rejected[m.alias] = [f"не вошла в shortlist (лимит {cap})"]
    return ok[:cap], rejected


def route(req: RouteRequest, models: list[ModelCandidate]) -> RouteDecision:
    total = len(models)
    candidates, rejected = shortlist(req, models)
    scored: list[tuple[float, ModelCandidate, list[str]]] = []

    for m in candidates:
        score = 50.0
        why: list[str] = []
        if req.prefer_local and m.local:
            score += 14
            why.append("+ local/free preference")
        role = float(m.role_scores.get(req.task_type, 0.0))
        score += role * 20
        if role:
            why.append(f"+ role score {role:.2f}")
        verified_hits = sorted(req.requires & m.verified_capabilities)
        if verified_hits:
            score += 3.0 * len(verified_hits)
            why.append("+ verified: " + ", ".join(verified_hits))
        if m.success_rate is not None:
            score += (m.success_rate - 0.5) * 20
            why.append(f"+ historical success {m.success_rate:.0%}")
        if m.gen_tps:
            speed_bonus = min(m.gen_tps / 10.0, 10.0)
            score += speed_bonus
            why.append(f"+ speed {m.gen_tps:.1f} tok/s")
        if m.latency_ms:
            score -= min(m.latency_ms / 1000.0, 8.0)
        score -= min(m.queue_depth * 2.0, 12.0)
        if not m.local:
            # Small cost penalty; role/quality can still win.
            score -= min((m.price_in + m.price_out) / 2.0, 20.0)
            why.append(f"- cloud cost ${m.price_in:.3f}/${m.price_out:.3f} per 1M")
        scored.append((score, m, why))

    if not scored:
        return RouteDecision(None, float("-inf"), [], rejected,
                             considered=0, total=total)
    scored.sort(key=lambda x: x[0], reverse=True)
    score, model, reasons = scored[0]
    return RouteDecision(model, round(score, 3), reasons, rejected,
                         considered=len(scored), total=total)


def candidate_digest(m: ModelCandidate) -> dict[str, Any]:
    """Компактное представление кандидата (для UI/промпта) — без raw-метаданных."""
    return {
        "alias": m.alias, "local": m.local, "context_window": m.context_window,
        "advertised": sorted(m.capabilities), "verified": sorted(m.verified_capabilities),
        "unsupported": sorted(m.unsupported_capabilities),
        "price_in": m.price_in, "price_out": m.price_out,
        "latency_ms": m.latency_ms, "gen_tps": m.gen_tps,
        "success_rate": m.success_rate,
    }
