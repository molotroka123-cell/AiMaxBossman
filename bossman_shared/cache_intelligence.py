"""PASS3 observe-only intelligence over cache observations (pure functions).

  * Context Waste Detector (BOSSMAN_CONTEXT_WASTE_OBSERVE): signals only — never
    removes or reorders context.
  * Cache Intelligence Advisor (BOSSMAN_CACHE_ADVISOR): advisory text only — it
    cannot change security ordering, cache credentials/live state, enable caching,
    raise memory authority or touch provider requests. Security-context movement is
    a BLOCK, never an optimisation.
  * Local cognitive reuse gate (BOSSMAN_COGNITIVE_REUSE_EXPERIMENT): same-model A/B
    non-inferiority; fresh observation always wins; no API-dollar claims for local.
Inputs are numbers/hashes from cache observations and task outcomes; no prompts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

FLAG_WASTE = "BOSSMAN_CONTEXT_WASTE_OBSERVE"
FLAG_ADVISOR = "BOSSMAN_CACHE_ADVISOR"
FLAG_REUSE = "BOSSMAN_COGNITIVE_REUSE_EXPERIMENT"


def flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


# ------------------------------------------------------------------ waste detector

@dataclass(frozen=True, slots=True)
class WasteSignal:
    kind: str
    detail: str
    severity: str = "info"        # info | warn | red
    evidence: dict = field(default_factory=dict)


def detect_context_waste(observations: Iterable[Mapping[str, Any]], *,
                         layout: Mapping[str, Any] | None = None,
                         episodes: Iterable[Mapping[str, Any]] = ()) -> list[WasteSignal]:
    """observations — normalized cache observations (same session ordered by time);
    layout — {"blocks": [{"kind": "policy"|"tools"|"role"|"map"|"task"|"diff"|"observation"|
                          "timestamp"|"live_state"|"credential", "hash": str, "tokens": int}]};
    episodes — {"strategy_hash", "new_evidence": bool, "context_tokens", "verified": bool,
                "confidence": float, "reused": bool, "verified_success": float}."""
    out: list[WasteSignal] = []
    obs = list(observations)
    # 1. churn стабильного префикса без семантической причины
    hashes = [o.get("prefix_hash") for o in obs if o.get("prefix_hash")]
    if len(hashes) >= 3:
        changes = sum(1 for a, b in zip(hashes, hashes[1:]) if a != b)
        if changes / max(1, len(hashes) - 1) > 0.5:
            out.append(WasteSignal("stable_prefix_churn", "prefix hash changes on most consecutive requests",
                                   "warn", {"changes": changes, "requests": len(hashes)}))
    # 2/7/8. динамика и live/security state ДО стабильного префикса; дубли схем
    if layout:
        blocks = list(layout.get("blocks") or [])
        stable = {"policy", "tools", "role", "map"}
        dynamic = {"task", "diff", "observation", "timestamp"}
        seen_dynamic = False
        for b in blocks:
            k = b.get("kind")
            if k in dynamic:
                seen_dynamic = True
            elif k in stable and seen_dynamic:
                out.append(WasteSignal("dynamic_before_stable_prefix",
                                       f"{k} block placed after dynamic content", "warn", {"block": k}))
            if k in ("live_state", "credential") and b.get("cacheable"):
                out.append(WasteSignal("security_or_live_state_cached",
                                       f"{k} marked cacheable", "red", {"block": k}))
        kinds = [b.get("hash") for b in blocks if b.get("kind") == "tools" and b.get("hash")]
        if len(kinds) != len(set(kinds)):
            out.append(WasteSignal("duplicate_schemas", "identical tool schema block repeated", "warn"))
    # 3/4/5/6/9. эпизоды
    eps = list(episodes)
    strategies: dict[str, int] = {}
    for e in eps:
        sh = e.get("strategy_hash")
        if sh and not e.get("new_evidence"):
            strategies[sh] = strategies.get(sh, 0) + 1
    for sh, n in strategies.items():
        if n >= 2:
            out.append(WasteSignal("repeated_strategy_without_evidence",
                                   f"strategy repeated {n}x with no new evidence", "warn", {"repeats": n}))
    big = [e for e in eps if int(e.get("context_tokens") or 0) >= 50_000]
    if len(big) >= 2:
        confs = [float(e.get("confidence") or 0) for e in big]
        if max(confs) - min(confs) < 0.05 and not any(e.get("verified") for e in big):
            out.append(WasteSignal("large_context_no_confidence_gain",
                                   "≥50k-token contexts without confidence/verified gain", "warn"))
    scored = [e for e in eps if "verified_success" in e]
    reused = [e for e in scored if e.get("reused")]
    fresh = [e for e in scored if not e.get("reused")]
    if reused and fresh:
        r = sum(float(e.get("verified_success") or 0) for e in reused) / len(reused)
        f = sum(float(e.get("verified_success") or 0) for e in fresh) / len(fresh)
        if r + 1e-9 < f:
            out.append(WasteSignal("reuse_degrades_quality",
                                   f"reused episodes verified {r:.2f} < fresh {f:.2f}", "red"))
    full_retrievals = [e for e in eps if e.get("retrieval_full_repeat")]
    if len(full_retrievals) >= 2:
        out.append(WasteSignal("repeated_full_retrieval", "same verified artifact retrieved in full repeatedly", "info"))
    return out


# ------------------------------------------------------------------ advisor

@dataclass(frozen=True, slots=True)
class Advice:
    action: str                    # NO_ACTION | EXPERIMENT | CHECK | BLOCK
    text: str
    sample_count: int
    evidence: dict = field(default_factory=dict)
    security_check: str = "ok"
    rollback: str = "advice only — nothing to roll back"


FORBIDDEN_ADVICE = ("move policy", "reorder security", "cache credential", "cache approval",
                    "enable cache", "promote memory")


def cache_advice(summary: Mapping[str, Any], *, security_context_moved: bool = False,
                 prefix_stability: float | None = None, min_samples: int = 20) -> list[Advice]:
    """Advisory only. Any security-context movement → BLOCK first."""
    n = int(summary.get("eligible_requests") or 0)
    if security_context_moved:
        return [Advice("BLOCK", "restore immutable security-context order; hit rate is not a reason to move policy",
                       n, security_check="violation")]
    if n < min_samples:
        return [Advice("NO_ACTION", f"insufficient evidence ({n} eligible requests < {min_samples})", n)]
    out: list[Advice] = []
    hit = summary.get("hit_rate_percent")
    if prefix_stability is not None and prefix_stability < 0.8:
        out.append(Advice("EXPERIMENT", "move non-security dynamic fields (task/diff/observations) after the stable prefix; run same-model A/B",
                          n, {"prefix_stability": prefix_stability}))
    if hit is not None and hit < 20 and (prefix_stability is None or prefix_stability >= 0.8):
        out.append(Advice("CHECK", "low hit rate with stable prefix: check TTL expiry, provider drift, minimum cacheable size",
                          n, {"hit_rate_percent": hit}))
    if int(summary.get("cache_control_without_usage") or 0) > 0:
        out.append(Advice("CHECK", "cache_control applied but provider returned no usage evidence — do not report savings",
                          n, {"unknown": summary.get("cache_control_without_usage")}))
    if int(summary.get("degraded_events") or 0) > 0:
        out.append(Advice("CHECK", "degraded cache metadata events present", n, {"degraded": summary.get("degraded_events")}))
    for a in out:
        assert not any(f in a.text.lower() for f in FORBIDDEN_ADVICE)
    return out or [Advice("NO_ACTION", "cache behaviour within observed bounds", n)]


# ------------------------------------------------------------------ local cognitive reuse gate

@dataclass(frozen=True, slots=True)
class ReuseOutcome:
    verified_success_on: float
    verified_success_off: float
    continuity_delta: float = 0.0
    compute_delta: float = 0.0          # отрицательное = меньше compute/времени
    stale_error_delta: float = 0.0
    false_success_delta: float = 0.0
    security_regressions: int = 0
    samples_on: int = 0
    samples_off: int = 0
    holdout_isolated: bool = True
    same_model: bool = True
    same_budget: bool = True


def allow_local_cognitive_reuse(o: ReuseOutcome, *, noninferiority_margin: float = 0.0,
                                min_samples: int = 20) -> tuple[bool, str]:
    """Same-model A/B: non-inferior VerifiedSuccess AND (continuity or compute gain) AND
    no stale/false-success/security regression AND isolated holdout. Never money claims."""
    if not (o.same_model and o.same_budget):
        return False, "A/B must use the same model and budget"
    if not o.holdout_isolated:
        return False, "holdout not isolated"
    if o.samples_on < min_samples or o.samples_off < min_samples:
        return False, f"INSUFFICIENT_EVIDENCE: {o.samples_on}/{o.samples_off} < {min_samples}"
    if o.verified_success_on + noninferiority_margin < o.verified_success_off:
        return False, "VerifiedSuccess inferior with reuse ON"
    if o.stale_error_delta > 0 or o.false_success_delta > 0 or o.security_regressions > 0:
        return False, "stale/false-success/security regression with reuse ON"
    if not (o.continuity_delta > 0 or o.compute_delta < 0):
        return False, "no continuity or compute benefit"
    return True, "non-inferior and beneficial (local objective: success + continuity − compute − stale risk)"


def fresh_observation_wins(reused: Mapping[str, Any] | None, fresh: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Инвариант: при конфликте свежее наблюдение важнее переиспользованного."""
    return fresh if fresh is not None else reused
