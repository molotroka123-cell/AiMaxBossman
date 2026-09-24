"""Provider-fleet routing contracts for Bossman 1.6 preparation.

No account creation, credential capture, or provider-limit circumvention.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ProviderClass(str, Enum):
    OWNER_PRIMARY = "OWNER_PRIMARY"
    OWNER_SECONDARY = "OWNER_SECONDARY"
    LOCAL = "LOCAL"
    RENTED_GPU = "RENTED_GPU"


@dataclass(frozen=True)
class ProviderSlot:
    provider: str
    account_alias: str
    account_class: ProviderClass
    available: bool
    capability_ok: bool
    quality_score: float
    latency_ms: float
    marginal_cost_usd: float
    quota_remaining: float | None = None
    privacy_ok: bool = True
    circuit_open: bool = False
    secondary_terms_allowed: bool = False

    def eligible(self) -> bool:
        if not (self.available and self.capability_ok and self.privacy_ok) or self.circuit_open:
            return False
        if self.account_class == ProviderClass.OWNER_SECONDARY and not self.secondary_terms_allowed:
            return False
        return True


def route(slots: Iterable[ProviderSlot], *, min_quality: float,
          max_cost_usd: float | None = None) -> ProviderSlot | None:
    """Prefer primary/free/already-funded capacity; secondary is a last resort."""
    eligible = [s for s in slots if s.eligible() and s.quality_score >= min_quality]
    if max_cost_usd is not None:
        eligible = [s for s in eligible if s.marginal_cost_usd <= max_cost_usd]
    if not eligible:
        return None
    class_rank = {
        ProviderClass.OWNER_PRIMARY: 0,
        ProviderClass.LOCAL: 1,
        ProviderClass.OWNER_SECONDARY: 2,
        ProviderClass.RENTED_GPU: 3,
    }
    return min(eligible, key=lambda s: (
        class_rank[s.account_class],
        s.marginal_cost_usd,
        s.latency_ms,
        -s.quality_score,
        s.provider,
        s.account_alias,
    ))


@dataclass(frozen=True)
class RentalEstimate:
    hourly_usd: float
    hours: float
    storage_usd: float = 0.0
    egress_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return self.hourly_usd * self.hours + self.storage_usd + self.egress_usd


def should_rent(*, rental: RentalEstimate, api_cost_usd: float,
                local_hours: float, rental_hours: float,
                required_memory_fits_local: bool,
                deadline_hours: float | None = None,
                owner_budget_usd: float | None = None) -> dict:
    if min(rental.hourly_usd, rental.hours, api_cost_usd, local_hours, rental_hours) < 0:
        raise ValueError("negative cost/time")
    if owner_budget_usd is not None and rental.total_usd > owner_budget_usd:
        return {"rent": False, "reason": "owner_budget_exceeded", "rental_cost_usd": rental.total_usd}
    if not required_memory_fits_local:
        return {"rent": True, "reason": "required_memory_does_not_fit_local",
                "rental_cost_usd": rental.total_usd}
    if deadline_hours is not None and local_hours > deadline_hours >= rental_hours:
        return {"rent": True, "reason": "deadline_requires_rental",
                "rental_cost_usd": rental.total_usd}
    if rental.total_usd < api_cost_usd and rental_hours < local_hours:
        return {"rent": True, "reason": "cheaper_and_faster_than_api_and_local",
                "rental_cost_usd": rental.total_usd}
    return {"rent": False, "reason": "local_or_api_preferred",
            "rental_cost_usd": rental.total_usd}


def cleanup_required(state: str) -> bool:
    return state in {"DONE", "FAILED", "TIMEOUT", "BUDGET_EXHAUSTED", "VERIFIER_FAILED"}
