from __future__ import annotations
import hashlib
from dataclasses import replace
from typing import Iterable
from .models import ContextItem, GuardianConfig, GuardianReport, RetentionMetrics


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def stable_hash(item: ContextItem) -> str:
    raw = f"{item.category}\x1f{item.content or ''}\x1f{item.raw_ref or ''}\x1f{item.source}\x1f{item.version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def keep_risk(item: ContextItem) -> float:
    return _clamp(item.importance) * _clamp(item.uncertainty) * _clamp(item.irrecoverability)


def context_loss_risk(item: ContextItem) -> float:
    return _clamp(item.probability_important) * _clamp(item.impact)


class ContextDataGuardian:
    """Anti-starvation context guard.

    It may compact context, but cannot silently erase hard-critical evidence.
    Omitted material must retain a raw reference when one exists.
    """
    def __init__(self, config: GuardianConfig | None = None):
        self.config = config or GuardianConfig()

    def _mandatory(self, item: ContextItem) -> bool:
        return (
            item.protected
            or item.priority in self.config.mandatory_priorities
            or item.category.lower() in self.config.protected_categories
        )

    def select(self, items: Iterable[ContextItem], *, low_memory: bool = False) -> GuardianReport:
        original = list(items)
        # exact/stable dedup first; protected/conflict records are not collapsed across IDs
        unique: list[ContextItem] = []
        seen: set[str] = set()
        for item in original:
            h = stable_hash(item)
            if h in seen and not item.protected and not item.conflict_group:
                continue
            seen.add(h)
            unique.append(item)

        budget = self.config.low_memory_budget if low_memory else self.config.token_budget
        selected: list[ContextItem] = []
        omitted: list[ContextItem] = []
        reasons: list[str] = []
        used = 0

        # Hard critical first. They can exceed the nominal budget; correctness wins over compression.
        mandatory = [x for x in unique if self._mandatory(x)]
        optional = [x for x in unique if not self._mandatory(x)]
        for item in mandatory:
            selected.append(item)
            used += max(0, item.token_count)

        # Preserve every side of a conflict if any member is selected/mandatory.
        mandatory_conflicts = {x.conflict_group for x in selected if x.conflict_group}
        if mandatory_conflicts:
            carry = [x for x in optional if x.conflict_group in mandatory_conflicts]
            for item in carry:
                selected.append(item)
                used += max(0, item.token_count)
                optional.remove(item)
                reasons.append(f"preserved conflict group {item.conflict_group}")

        def utility(item: ContextItem) -> float:
            loss = context_loss_risk(item)
            kr = keep_risk(item)
            safety = max(loss, kr)
            return (item.importance + safety + (1.0 / (1 + max(0, item.priority)))) / max(1, item.token_count)

        optional.sort(key=utility, reverse=True)
        deep = False
        for item in optional:
            kr = keep_risk(item)
            clr = context_loss_risk(item)
            risky_to_drop = kr >= self.config.keep_risk_threshold or clr >= item.savings_utility
            if used + max(0, item.token_count) <= budget or risky_to_drop:
                selected.append(item)
                used += max(0, item.token_count)
                if used > budget and risky_to_drop:
                    deep = True
                    reasons.append(f"deep context: omission risk for {item.item_id}")
            else:
                omitted.append(item)
                if max(kr, clr) >= self.config.deep_context_threshold:
                    deep = True
                    reasons.append(f"recall risk: {item.item_id}")

        # If one side of a non-mandatory conflict survived, keep every side.
        groups_selected = {x.conflict_group for x in selected if x.conflict_group}
        for item in list(omitted):
            if item.conflict_group and item.conflict_group in groups_selected:
                omitted.remove(item)
                selected.append(item)
                used += max(0, item.token_count)
                deep = deep or used > budget
                reasons.append(f"preserved conflicting evidence {item.conflict_group}")

        raw_refs = tuple(dict.fromkeys(x.raw_ref for x in omitted if x.raw_ref))
        return GuardianReport(
            selected=tuple(selected), omitted=tuple(omitted), raw_fallback_refs=raw_refs,
            deep_escalated=deep, reasons=tuple(reasons), selected_tokens=used,
            original_tokens=sum(max(0, x.token_count) for x in original),
        )

    def recall_check(self, report: GuardianReport) -> bool:
        """True means raw/deep context should be re-opened before final decision."""
        for item in report.omitted:
            if keep_risk(item) >= self.config.keep_risk_threshold:
                return True
            if context_loss_risk(item) >= item.savings_utility:
                return True
        return report.deep_escalated

    def retention_gate(
        self,
        *, raw_verified_success: float,
        filtered_verified_success: float,
        raw_quality: float,
        filtered_quality: float,
        raw_effective_cost: float,
        filtered_effective_cost: float,
    ) -> RetentionMetrics:
        raw_success = max(0.0, raw_verified_success)
        filtered_success = max(0.0, filtered_verified_success)
        retention = filtered_success / raw_success if raw_success > 0 else (1.0 if filtered_success == 0 else float("inf"))
        degradation = raw_success - filtered_success
        raw_eff = raw_quality / max(raw_effective_cost, 1e-12)
        filtered_eff = filtered_quality / max(filtered_effective_cost, 1e-12)
        gain = filtered_eff / max(raw_eff, 1e-12)
        allowed = degradation <= self.config.max_verified_success_degradation
        return RetentionMetrics(retention, gain, degradation, allowed)
