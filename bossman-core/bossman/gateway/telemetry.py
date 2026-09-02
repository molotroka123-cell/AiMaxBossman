from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from threading import Lock

from .prompt_cache import ALLOWED_TTLS, minimum_cacheable_tokens


@dataclass
class GatewayMetrics:
    started_at: float = field(default_factory=time.time)
    requests_total: int = 0
    errors_total: int = 0
    inflight: int = 0
    queued: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    cache_requests: int = 0
    cache_eligible_requests: int = 0
    cache_hits: int = 0
    cached_tokens: int = 0
    fresh_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_actual_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    cache_baseline_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    cache_saved_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    # Requests that actually contributed to cache_saved_usd (provider-observed cache
    # tokens + both costs known + cache eligible). Requests skipped by that gate are
    # counted separately so the number can be audited instead of merely trusted.
    cache_savings_events: int = 0
    cache_savings_skipped: int = 0
    prefix_comparisons: int = 0
    prefix_matches: int = 0
    last_cache: dict = field(default_factory=dict)
    _prefix_sessions: dict[str, dict] = field(default_factory=dict, repr=False)
    backend_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    alias_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latencies_ms: list[float] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def begin(self, alias: str) -> float:
        with self._lock:
            self.requests_total += 1
            self.inflight += 1
            self.alias_requests[alias] += 1
        return time.perf_counter()

    def end(self, started: float, backend: str | None, usage: dict | None = None, error: bool = False) -> None:
        elapsed = (time.perf_counter() - started) * 1000
        with self._lock:
            self.inflight = max(0, self.inflight - 1)
            if backend:
                self.backend_requests[backend] += 1
            if error:
                self.errors_total += 1
            if usage:
                self.tokens_prompt += int(usage.get("prompt_tokens") or 0)
                self.tokens_completion += int(usage.get("completion_tokens") or 0)
            self.latencies_ms.append(elapsed)
            if len(self.latencies_ms) > 2000:
                del self.latencies_ms[:1000]

    @staticmethod
    def _money(value) -> Decimal | None:
        if value is None:
            return None
        try:
            result = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return result if result.is_finite() and result >= 0 else None

    def end_cache(self, meta: dict, usage: dict, *, backend: str, model: str,
                  actual_cost=None, baseline_cost=None, upstream_provider: str | None = None,
                  degraded_reason: str | None = None) -> None:
        """Record numeric/cache metadata only; never retain request content."""
        now = time.time()
        provider_kind = str(meta.get("provider") or "unknown")
        eligible = provider_kind == "openrouter" and bool(meta.get("enabled"))
        session_hash = meta.get("session_id_hash")
        prefix_hash = meta.get("prefix_hash")
        prefix_tokens = int(meta.get("prefix_tokens") or 0)
        ttl = str(meta.get("ttl") or "5m")
        state = str(meta.get("state") or "UNKNOWN")
        miss_reason = meta.get("miss_reason")

        with self._lock:
            self.cache_requests += 1
            if eligible:
                self.cache_eligible_requests += 1
            self.cached_tokens += int(usage.get("cached_tokens") or 0)
            self.fresh_input_tokens += int(usage.get("fresh_input_tokens") or 0)
            self.cache_write_tokens += int(usage.get("cache_write_tokens") or 0)

            previous = self._prefix_sessions.get(session_hash) if session_hash else None
            if previous and prefix_hash:
                self.prefix_comparisons += 1
                if previous.get("prefix_hash") == prefix_hash:
                    self.prefix_matches += 1

            if not eligible:
                state, miss_reason = "UNSUPPORTED", "unsupported provider"
            elif degraded_reason or meta.get("degraded_reason"):
                state, miss_reason = "DEGRADED", degraded_reason or meta.get("degraded_reason")
            elif int(usage.get("cached_tokens") or 0) > 0:
                state, miss_reason = "HOT", None
                self.cache_hits += 1
            elif int(usage.get("cache_write_tokens") or 0) > 0:
                state, miss_reason = "COLD", None
            else:
                state = "MISS"
                if previous and upstream_provider and previous.get("upstream_provider") and \
                        previous.get("upstream_provider") != upstream_provider:
                    miss_reason = "provider drift"
                elif previous and now - float(previous.get("seen_at") or 0) > ALLOWED_TTLS.get(ttl, 300):
                    miss_reason = "TTL expired"
                elif previous and prefix_hash and previous.get("prefix_hash") != prefix_hash:
                    miss_reason = "prefix changed"
                elif minimum_cacheable_tokens(model) is not None and \
                        prefix_tokens < int(minimum_cacheable_tokens(model) or 0):
                    miss_reason = "too short"
                else:
                    miss_reason = "unknown"

            if session_hash:
                self._prefix_sessions[session_hash] = {
                    "prefix_hash": prefix_hash,
                    "prefix_tokens": prefix_tokens,
                    "backend": backend,
                    "model": model,
                    "upstream_provider": upstream_provider,
                    "seen_at": now,
                }
                if len(self._prefix_sessions) > 4096:
                    oldest = min(self._prefix_sessions,
                                 key=lambda key: self._prefix_sessions[key].get("seen_at", 0))
                    self._prefix_sessions.pop(oldest, None)

            actual = self._money(actual_cost)
            baseline = self._money(baseline_cost)
            if actual is not None:
                self.cache_actual_cost_usd += actual
            if baseline is not None:
                self.cache_baseline_cost_usd += baseline
            # AUDIT-ONLY-001 / F6: cache savings may be accrued ONLY from provider
            # evidence.  Without this gate `baseline - actual` was booked for every
            # request — including ones where the provider reported zero cache tokens
            # and ones where prompt caching was disabled entirely — so the published
            # "Saved $" was price-table drift, not an attributable cache effect.
            provider_cache_tokens = (int(usage.get("cached_tokens") or 0)
                                     + int(usage.get("cache_write_tokens") or 0))
            if actual is not None and baseline is not None and eligible and provider_cache_tokens:
                self.cache_saved_usd += baseline - actual
                self.cache_savings_events += 1
            else:
                self.cache_savings_skipped += 1
            self.last_cache = {
                "session_affinity": bool(meta.get("session_affinity")),
                "provider": upstream_provider or backend,
                "gateway_backend": backend,
                "model": model,
                "ttl": ttl,
                "state": state,
                "miss_reason": miss_reason,
                "prefix_tokens": prefix_tokens,
                "cache_discount": (None if usage.get("cache_discount") is None
                                   else str(usage.get("cache_discount"))),
            }

    def record_observation(self, obs) -> None:
        """PASS3: normalized cache observation (numbers/hashes only) into a bounded log."""
        log = getattr(self, "_observations", None)
        if log is None:
            from .._shared import cache_observation as co
            if co is None:
                return
            log = self._observations = co.ObservationLog()
        with self._lock:
            log.record(obs)

    def observations_summary(self) -> dict | None:
        log = getattr(self, "_observations", None)
        return None if log is None else log.summary()

    def snapshot(self) -> dict:
        with self._lock:
            lat = sorted(self.latencies_ms)
            def pct(q: float) -> float:
                if not lat:
                    return 0.0
                return round(lat[min(len(lat)-1, int((len(lat)-1)*q))], 2)
            prefix_stability = (None if not self.prefix_comparisons else
                                round(100 * self.prefix_matches / self.prefix_comparisons, 1))
            cache_hit_percent = (0.0 if not self.cache_eligible_requests else
                                 round(100 * self.cache_hits / self.cache_eligible_requests, 1))
            prompt_cache = {
                "cache_hit_percent": cache_hit_percent,
                "cached_tokens": self.cached_tokens,
                "fresh_input_tokens": self.fresh_input_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "saved_usd": float(self.cache_saved_usd),
                # Named cost concepts kept apart (AUDIT-ONLY-001 / F6): the only
                # savings number this process may publish is the provider-observed
                # one.  verified_net_savings additionally needs non-inferior
                # VerifiedSuccess evidence, which the gateway does not hold — it is
                # reported as null rather than approximated.
                "provider_observed_savings_usd": float(self.cache_saved_usd),
                "savings_basis": ("provider-observed" if self.cache_savings_events else "none"),
                "savings_events": self.cache_savings_events,
                "savings_skipped_requests": self.cache_savings_skipped,
                "verified_net_savings_usd": None,
                "savings_note": ("saved_usd accrues only from requests with provider-reported "
                                 "cache tokens and configured cache prices; verified net savings "
                                 "require VerifiedSuccess evidence not available at the gateway"),
                "actual_cost_usd": float(self.cache_actual_cost_usd),
                "estimated_baseline_cost_usd": float(self.cache_baseline_cost_usd),
                "prefix_stability_percent": prefix_stability,
                "requests": self.cache_requests,
                "eligible_requests": self.cache_eligible_requests,
                "hits": self.cache_hits,
                **({"session_affinity": False, "provider": None, "gateway_backend": None,
                    "model": None, "ttl": None, "state": "UNKNOWN", "miss_reason": "unknown",
                    "prefix_tokens": 0, "cache_discount": None} if not self.last_cache
                   else self.last_cache),
            }
            obs_summary = (self._observations.summary() if getattr(self, "_observations", None) else None)
            return {
                "cache_observations": obs_summary,
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "inflight": self.inflight,
                "queued": self.queued,
                "prompt_tokens": self.tokens_prompt,
                "completion_tokens": self.tokens_completion,
                "latency_ms": {"p50": pct(.50), "p95": pct(.95), "p99": pct(.99)},
                "backend_requests": dict(self.backend_requests),
                "alias_requests": dict(self.alias_requests),
                "prompt_cache": prompt_cache,
                "process": process_resources(),
            }


def process_resources() -> dict:
    data = {"pid": os.getpid()}
    try:
        import psutil  # optional
        p = psutil.Process()
        m = p.memory_info()
        data.update({"rss_mb": round(m.rss/1024/1024, 1), "cpu_percent": p.cpu_percent(interval=None)})
        vm = psutil.virtual_memory()
        data.update({"system_ram_total_mb": round(vm.total/1024/1024), "system_ram_available_mb": round(vm.available/1024/1024)})
    except Exception:
        pass
    return data
