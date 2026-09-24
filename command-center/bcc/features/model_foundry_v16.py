"""Immutable model/dataset lineage and promotion gates."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json


def manifest_hash(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    base_model: str
    train_manifest_hash: str
    holdout_manifest_hash: str
    benchmark_old: float
    benchmark_new: float
    safety_old: float
    safety_new: float
    rollback_model_id: str


def promotable(c: ModelCandidate, *, min_relative_gain: float=.02) -> bool:
    if not c.train_manifest_hash or not c.holdout_manifest_hash:
        return False
    if c.train_manifest_hash == c.holdout_manifest_hash:
        return False
    if c.benchmark_old <= 0:
        return False
    return (c.benchmark_new/c.benchmark_old >= 1+min_relative_gain
            and c.safety_new >= c.safety_old and bool(c.rollback_model_id))
