"""Model policy for Jeff, Jev and the autonomy planner (autonomy freeze, line C).

Two owner rules, both fail closed:

1. **Banned family.** Liquid/LFM models (an id containing ``lfm`` or ``liquid/``, any case) are refused on every
   route: participant chat, local models, governed Bossman inference, the core gateway and the planner. The ban is
   code, not configuration, so a reloaded settings file or an environment override cannot bring one back.
2. **Main planning models.** A cloud model may plan (and approve architecture, security, memory or release work)
   only when it has at least ``MIN_MAIN_PARAMS_B`` billion parameters AND the LIVE provider catalog priced it at
   exactly 0/0 no longer than ``PRICE_RECHECK_SECONDS`` ago. An unknown size or an unknown/stale price is a
   refusal. A smaller model is at most a rare, explicitly requested fallback with ``may_approve=False`` for every
   approval scope.

Parameter counts come from a curated table (known ids) or from the provider catalog fields (``id``,
``hugging_face_id``, ``name``, llama.cpp ``meta.n_params``); every resolution records its source and a timestamp.
The free-text description is never parsed: it names other models too often.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .model_route import PRICE_RECHECK_SECONDS, route_verdict

MODEL_POLICY_SCHEMA = "bossman.pit.model-policy/1"
MIN_MAIN_PARAMS_B = 10.0
APPROVAL_SCOPES = ("architecture", "security", "memory", "release")

BANNED_MODEL = "banned_model_family"
PARAMS_UNKNOWN = "params_unknown"
PARAMS_BELOW_MIN = "params_below_min"
PRICE_STALE = "price_stale"

_BANNED = re.compile(r"lfm|liquid/", re.I)


def is_banned_model(model: Any) -> bool:
    """True for a Liquid/LFM model id (any provider prefix, any case)."""
    return bool(_BANNED.search(str(model or "")))


def banned_refusal(model: Any) -> str:
    """'' or a stable, owner-readable refusal for a banned model."""
    return (f"model policy: {model} belongs to a banned model family (Liquid/LFM) and is refused on every route"
            if is_banned_model(model) else "")


def split_banned(models: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(kept, rejected) preserving order."""
    kept, rejected = [], []
    for model in models:
        (rejected if is_banned_model(model) else kept).append(model)
    return tuple(kept), tuple(rejected)


# -- parameter metadata ------------------------------------------------------------------------------
# id without the ':free' / ':variant' suffix -> (total B, active B or None). Only sizes the vendor publishes.
CURATED_PARAMS: dict[str, tuple[float, float | None]] = {
    "nvidia/nemotron-3-ultra-550b-a55b": (550.0, 55.0),
    "openai/gpt-oss-120b": (117.0, 5.1),
    "openai/gpt-oss-20b": (21.0, 3.6),
    "meta-llama/llama-3.3-70b-instruct": (70.0, None),
    "deepseek/deepseek-r1": (671.0, 37.0),
    "deepseek/deepseek-chat-v3.1": (671.0, 37.0),
    "qwen/qwen3-235b-a22b": (235.0, 22.0),
    "qwen/qwen3-coder": (480.0, 35.0),
    "moonshotai/kimi-k2": (1000.0, 32.0),
    "z-ai/glm-4.5-air": (106.0, 12.0),
}
CURATED_AS_OF = "2026-09-29"

_TOTAL = re.compile(r"(\d+(?:\.\d+)?)([bm])")
_MOE = re.compile(r"(\d+)x(\d+(?:\.\d+)?)b")
_ACTIVE = re.compile(r"a(\d+(?:\.\d+)?)b")
_EFFECTIVE = re.compile(r"e(\d+(?:\.\d+)?)b")
_TOKEN_SPLIT = re.compile(r"[\s/_:\-]+")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def base_id(model: str) -> str:
    return str(model or "").split(":", 1)[0].strip().lower()


@dataclass(frozen=True)
class ParamInfo:
    model: str
    total_b: float | None
    active_b: float | None
    source: str                 # curated | catalog:<field> | unknown
    resolved_at: str

    @property
    def known(self) -> bool:
        return self.total_b is not None

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "total_b": self.total_b, "active_b": self.active_b,
                "source": self.source, "resolved_at": self.resolved_at}


def parse_param_text(text: str) -> tuple[float | None, float | None]:
    """(total, active) billions from a model id or name such as ``x-550b-a55b`` / ``8x7B`` / ``350M``."""
    total: float | None = None
    active: float | None = None
    for token in _TOKEN_SPLIT.split(str(text or "").lower()):
        if not token:
            continue
        if m := _MOE.fullmatch(token):
            total = max(total or 0.0, float(m.group(1)) * float(m.group(2)))
        elif m := _ACTIVE.fullmatch(token):
            active = float(m.group(1))
        elif m := _EFFECTIVE.fullmatch(token):
            total = max(total or 0.0, float(m.group(1)))
        elif m := _TOTAL.fullmatch(token):
            value = float(m.group(1)) / (1000.0 if m.group(2) == "m" else 1.0)
            total = max(total or 0.0, value)
    return (total if total else None), active


def resolve_params(model: str, row: Mapping[str, Any] | None = None, *, now: float | None = None) -> ParamInfo:
    """Parameter count with provenance; ``total_b=None`` when nothing trustworthy says it."""
    stamp = _iso(time.time() if now is None else now)
    curated = CURATED_PARAMS.get(base_id(model))
    if curated is not None:
        return ParamInfo(model, curated[0], curated[1], "curated", stamp)
    row = row or {}
    meta = row.get("meta") if isinstance(row.get("meta"), Mapping) else {}
    n_params = meta.get("n_params")
    if isinstance(n_params, int) and not isinstance(n_params, bool) and n_params > 0:
        return ParamInfo(model, round(n_params / 1e9, 2), None, "catalog:meta.n_params", stamp)
    for name, value in (("id", row.get("id") or model), ("hugging_face_id", row.get("hugging_face_id")),
                        ("name", row.get("name"))):
        if not isinstance(value, str) or not value.strip():
            continue
        total, active = parse_param_text(value)
        if total is not None:
            return ParamInfo(model, total, active, f"catalog:{name}", stamp)
    return ParamInfo(model, None, None, "unknown", stamp)


# -- planning decision -----------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanningDecision:
    model: str
    tier: str                   # main | fallback | rejected
    reason: str                 # '' for main; stable code otherwise (fallback keeps 'params_below_min')
    params: ParamInfo
    price_verified_at: str | None
    may_approve: dict[str, bool] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.tier != "rejected"

    def to_dict(self) -> dict[str, Any]:
        return {"schema": MODEL_POLICY_SCHEMA, "model": self.model, "tier": self.tier, "reason": self.reason,
                "params": self.params.to_dict(), "price_verified_at": self.price_verified_at,
                "may_approve": dict(self.may_approve), "min_main_params_b": MIN_MAIN_PARAMS_B}


def _no_approvals() -> dict[str, bool]:
    return {scope: False for scope in APPROVAL_SCOPES}


def may_approve(decision: PlanningDecision, scope: str) -> bool:
    """Only a main planner approves; an unknown scope is never approved."""
    return decision.tier == "main" and bool(decision.may_approve.get(scope, False))


def planning_decision(model: str, *, listed: bool, prices: Mapping[str, Any] | None,
                      row: Mapping[str, Any] | None, price_checked_at: float | None, now: float | None = None,
                      max_price_age_s: float = PRICE_RECHECK_SECONDS,
                      allow_small_fallback: bool = False) -> PlanningDecision:
    now = time.time() if now is None else now
    params = resolve_params(model, row, now=now)
    verified = None if price_checked_at is None else _iso(price_checked_at)

    def reject(reason: str) -> PlanningDecision:
        return PlanningDecision(model, "rejected", reason, params, verified, _no_approvals())

    if is_banned_model(model):
        return reject(BANNED_MODEL)
    verdict = route_verdict(model, listed, dict(prices) if prices is not None else None)
    if verdict:
        return reject(verdict)
    if price_checked_at is None or not 0 <= now - price_checked_at <= max_price_age_s:
        return reject(PRICE_STALE)
    if not params.known:
        return reject(PARAMS_UNKNOWN)
    if params.total_b < MIN_MAIN_PARAMS_B:
        if allow_small_fallback:
            return PlanningDecision(model, "fallback", PARAMS_BELOW_MIN, params, verified, _no_approvals())
        return reject(PARAMS_BELOW_MIN)
    return PlanningDecision(model, "main", "", params, verified, {scope: True for scope in APPROVAL_SCOPES})


def select_planning_models(models: Iterable[str], *, rows: Mapping[str, Mapping[str, Any]],
                           pricing: Mapping[str, Mapping[str, Any] | None], price_checked_at: float | None,
                           now: float | None = None) -> list[PlanningDecision]:
    """Main planners, biggest first; a small model only when no main planner qualifies (rare fallback)."""
    now = time.time() if now is None else now
    decisions = [planning_decision(m, listed=m in rows, prices=pricing.get(m), row=rows.get(m),
                                   price_checked_at=price_checked_at, now=now) for m in models]
    main = sorted((d for d in decisions if d.tier == "main"), key=lambda d: -(d.params.total_b or 0.0))
    if main:
        return main
    small = [planning_decision(d.model, listed=True, prices=pricing.get(d.model), row=rows.get(d.model),
                               price_checked_at=price_checked_at, now=now, allow_small_fallback=True)
             for d in decisions if d.reason == PARAMS_BELOW_MIN]
    return sorted(small, key=lambda d: -(d.params.total_b or 0.0))[:1]


async def evaluate_live(adapter: Any, models: Iterable[str], *, now: float | None = None) -> list[PlanningDecision]:
    """Read the live catalog + prices once through ``adapter`` and decide every model (no inference)."""
    rows_list = await adapter.list_model_info()
    pricing = await adapter.list_model_pricing()
    checked = time.time() if now is None else now
    rows = {str(r.get("id")): r for r in rows_list if isinstance(r, Mapping) and r.get("id")}
    return [planning_decision(m, listed=m in rows, prices=pricing.get(m), row=rows.get(m),
                              price_checked_at=checked, now=checked) for m in models]
