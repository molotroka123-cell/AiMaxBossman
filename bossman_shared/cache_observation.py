"""PASS3 normalized cache observation (schema v1) — provider-evidence classification.

Contract (docs: BOSSMAN_META_INTELLIGENCE_7_MODULES/PASS3_CACHE_TELEMETRY_SCHEMA.md):
  * mutually exclusive buckets: total_input = fresh_input + cache_read + cache_write;
  * state from PROVIDER USAGE only: read>0 → HIT; write>0 → WRITE; usage present but
    zero → MISS; ineligible/disabled → BYPASS; no usage evidence → UNKNOWN; partial
    telemetry/metadata failure → DEGRADED. cache_control alone is never HIT/WRITE;
  * raw provider usage kept verbatim (numbers) next to the normalized buckets;
  * no raw prompt, no message text, no credentials — hashes/numbers only.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Mapping

EVENT_VERSION = 1
STATES = ("HIT", "WRITE", "MISS", "BYPASS", "UNKNOWN", "DEGRADED")
ROUTES = ("gateway", "direct", "local")
TTLS = ("5m", "1h", None)
# Поля, которых в наблюдении быть не может (защита от утечки контента).
FORBIDDEN_KEYS = frozenset({"prompt", "messages", "system", "content", "text", "api_key", "authorization",
                            "cookie", "token", "cache_content", "tools_json"})


def opaque(value: Any) -> str | None:
    """Стабильный непрозрачный ключ корреляции (sha256[:32]); None → None."""
    if value is None or value == "":
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]


def _int(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass(frozen=True, slots=True)
class TokenBuckets:
    fresh_input: int
    cache_read: int
    cache_write: int
    output: int

    @property
    def total_input(self) -> int:
        return self.fresh_input + self.cache_read + self.cache_write


def normalize_anthropic_usage(usage: Mapping[str, Any] | None) -> TokenBuckets | None:
    """Anthropic Messages API: input_tokens = НЕкэшированный (fresh) вход;
    cache_read_input_tokens / cache_creation_input_tokens — отдельные корзины.
    None → нет usage-доказательства (UNKNOWN)."""
    if not isinstance(usage, Mapping) or "input_tokens" not in usage:
        return None
    return TokenBuckets(_int(usage.get("input_tokens")), _int(usage.get("cache_read_input_tokens")),
                        _int(usage.get("cache_creation_input_tokens")), _int(usage.get("output_tokens")))


def normalize_openai_style_usage(usage: Mapping[str, Any] | None) -> TokenBuckets | None:
    """OpenAI/OpenRouter: prompt_tokens УЖЕ включает cached/cache-write; fresh =
    prompt − cached − written (не суммировать повторно)."""
    if not isinstance(usage, Mapping) or usage.get("prompt_tokens") is None:
        return None
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    details = details if isinstance(details, Mapping) else {}
    read = max(_int(details.get("cached_tokens")), _int(details.get("cache_read_tokens")),
               _int(usage.get("cached_tokens")), _int(usage.get("cache_read_tokens")),
               _int(usage.get("cache_read_input_tokens")))
    write = max(_int(details.get("cache_write_tokens")), _int(usage.get("cache_write_tokens")),
                _int(usage.get("cache_creation_input_tokens")))
    prompt = _int(usage.get("prompt_tokens"))
    fresh = max(0, prompt - read - write)
    return TokenBuckets(fresh, read, write, _int(usage.get("completion_tokens") or usage.get("output_tokens")))


def classify(*, eligible: bool, buckets: TokenBuckets | None, degraded: bool = False) -> str:
    if not eligible:
        return "BYPASS"
    if buckets is None:
        return "UNKNOWN"
    if degraded:
        return "DEGRADED"
    if buckets.cache_read > 0:
        return "HIT"
    if buckets.cache_write > 0:
        return "WRITE"
    return "MISS"


def cost_pair(buckets: TokenBuckets, *, fresh_per_m: Decimal | None, read_per_m: Decimal | None,
              write_per_m: Decimal | None, output_per_m: Decimal | None) -> tuple[Decimal | None, Decimal | None, bool]:
    """(actual, baseline_all_fresh, baseline_is_estimate). Неизвестная цена корзины,
    в которой есть токены → actual=None (UNKNOWN), а не выдуманная экономия."""
    m = Decimal(1_000_000)
    if fresh_per_m is None or output_per_m is None:
        return None, None, True
    baseline = (Decimal(buckets.total_input) * fresh_per_m + Decimal(buckets.output) * output_per_m) / m
    if (buckets.cache_read and read_per_m is None) or (buckets.cache_write and write_per_m is None):
        return None, baseline, True
    actual = (Decimal(buckets.fresh_input) * fresh_per_m
              + Decimal(buckets.cache_read) * (read_per_m or Decimal(0))
              + Decimal(buckets.cache_write) * (write_per_m or Decimal(0))
              + Decimal(buckets.output) * output_per_m) / m
    return actual, baseline, True


@dataclass(slots=True)
class CacheObservation:
    provider: str
    model: str
    route: str
    state: str
    fresh_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    event_version: int = EVENT_VERSION
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    task_id_hash: str | None = None
    session_id_hash: str | None = None
    ttl: str | None = None
    cache_control_applied: bool = False
    prefix_hash: str | None = None
    prefix_tokens: int = 0
    miss_reason: str | None = None
    actual_cost_usd: float | None = None
    baseline_cost_usd: float | None = None
    baseline_is_estimate: bool = True
    verified_success: bool | None = None
    environment_fingerprint: str | None = None
    security_context_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        errs = validate_observation(d)
        if errs:
            raise ValueError("; ".join(errs))
        return d


def build_observation(*, provider: str, model: str, route: str, eligible: bool,
                      buckets: TokenBuckets | None, degraded: bool = False, **extra: Any) -> CacheObservation:
    state = classify(eligible=eligible, buckets=buckets, degraded=degraded)
    b = buckets or TokenBuckets(0, 0, 0, 0)
    miss = extra.pop("miss_reason", None)
    if state == "BYPASS" and not miss:
        miss = "disabled or unsupported"
    elif state == "UNKNOWN" and not miss:
        miss = "no provider usage evidence"
    return CacheObservation(provider=provider, model=model, route=route, state=state,
                            fresh_input_tokens=b.fresh_input, cache_read_tokens=b.cache_read,
                            cache_write_tokens=b.cache_write, output_tokens=b.output,
                            miss_reason=miss, **extra)


def validate_observation(d: Mapping[str, Any]) -> list[str]:
    """Проверка по schemas/cache_observation.schema.json (без внешних зависимостей)."""
    errs: list[str] = []
    for k in ("event_version", "timestamp", "provider", "model", "route", "state",
              "fresh_input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"):
        if k not in d:
            errs.append(f"missing {k}")
    if d.get("event_version") != EVENT_VERSION:
        errs.append("event_version must be 1")
    if d.get("state") not in STATES:
        errs.append(f"state not in {STATES}")
    if d.get("route") not in ROUTES:
        errs.append(f"route not in {ROUTES}")
    if d.get("ttl") not in TTLS:
        errs.append("ttl must be 5m, 1h or null")
    for k in ("fresh_input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "prefix_tokens"):
        v = d.get(k, 0)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            errs.append(f"{k} must be a non-negative integer")
    for k in d:
        if k in FORBIDDEN_KEYS:
            errs.append(f"forbidden content field {k}")
    if d.get("state") == "HIT" and int(d.get("cache_read_tokens") or 0) <= 0:
        errs.append("HIT without cache_read_tokens")
    if d.get("state") == "WRITE" and (int(d.get("cache_write_tokens") or 0) <= 0 or int(d.get("cache_read_tokens") or 0) > 0):
        errs.append("WRITE requires cache_write_tokens>0 and no read tokens")
    return errs


class ObservationLog:
    """Bounded in-memory ring of validated observations + counters (numbers only)."""

    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        self.items: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {s: 0 for s in STATES}
        self.tokens = {"fresh_input": 0, "cache_read": 0, "cache_write": 0, "output": 0}

    def record(self, obs: CacheObservation | Mapping[str, Any]) -> dict[str, Any]:
        d = obs.as_dict() if isinstance(obs, CacheObservation) else dict(obs)
        errs = validate_observation(d)
        if errs:
            raise ValueError("; ".join(errs))
        self.items.append(d)
        if len(self.items) > self.capacity:
            del self.items[: len(self.items) - self.capacity]
        self.counts[d["state"]] += 1
        self.tokens["fresh_input"] += d["fresh_input_tokens"]
        self.tokens["cache_read"] += d["cache_read_tokens"]
        self.tokens["cache_write"] += d["cache_write_tokens"]
        self.tokens["output"] += d["output_tokens"]
        return d

    def summary(self) -> dict[str, Any]:
        eligible = sum(self.counts[s] for s in ("HIT", "WRITE", "MISS", "DEGRADED"))
        measured_actual = [x["actual_cost_usd"] for x in self.items if x.get("actual_cost_usd") is not None]
        baselines = [x["baseline_cost_usd"] for x in self.items if x.get("baseline_cost_usd") is not None]
        return {"counts": dict(self.counts), "eligible_requests": eligible,
                "hit_rate_percent": (round(100 * self.counts["HIT"] / eligible, 1) if eligible else None),
                "tokens": dict(self.tokens),
                "measured_actual_cost_usd": round(sum(measured_actual), 6) if measured_actual else None,
                "estimated_baseline_cost_usd": round(sum(baselines), 6) if baselines else None,
                "unknown_cost_requests": sum(1 for x in self.items if x.get("actual_cost_usd") is None
                                             and x["state"] in ("HIT", "WRITE", "MISS")),
                "degraded_events": self.counts["DEGRADED"], "unknown_events": self.counts["UNKNOWN"],
                "cache_control_without_usage": sum(1 for x in self.items
                                                   if x.get("cache_control_applied") and x["state"] == "UNKNOWN")}
