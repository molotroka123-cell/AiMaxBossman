"""Bossman Learning Layer — structured engineering records and reusable analysis tools.

The canonical engineering learning store still keeps only explicit, auditable
records (never hidden chain-of-thought). Trader Apprentice helpers expose a
separate deterministic market-analysis API; they do not place orders.
"""
from .trace import (FORBIDDEN_FIELDS, STATUSES, LearningStore, ValidationError, case_id,
                    redact_text, validate)
from .trader_apprentice import (
    Analysis,
    Direction,
    LevelMap,
    Regime,
    Snapshot,
    Stance,
    accepted_above,
    analyze,
    classify_regime,
    long_return_pct,
    sweep_and_reclaim,
    weighted_average_entry,
)

__all__ = [
    "FORBIDDEN_FIELDS", "STATUSES", "LearningStore", "ValidationError", "case_id",
    "redact_text", "validate",
    "Analysis", "Direction", "LevelMap", "Regime", "Snapshot", "Stance",
    "accepted_above", "analyze", "classify_regime", "long_return_pct",
    "sweep_and_reclaim", "weighted_average_entry",
]
