"""Native budgeted hybrid memory reranker for Bossnet 1.6.

Retrieval backends provide normalized signals; this module performs transparent,
versionable scoring and diversity-aware context packing without an LLM scan.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import exp, log
from typing import Callable


@dataclass(frozen=True)
class Signals:
    semantic: float = 0
    keyword: float = 0
    graph: float = 0
    temporal: float = 0
    verified: float = 0
    novelty: float = 0
    procedural: float = 0
    contradiction: float = 0
    recency: float = 0
    duplicate: float = 0
    stale: float = 0


@dataclass(frozen=True)
class Weights:
    semantic: float=.18; keyword: float=.10; graph: float=.14; temporal: float=.12
    verified: float=.16; novelty: float=.07; procedural: float=.10
    contradiction: float=.05; recency: float=.04; duplicate: float=.08; stale: float=.10


@dataclass(frozen=True)
class MemoryCandidate:
    ref: str
    tokens: int
    signals: Signals
    mandatory: bool=False


def temporal_decay(delta_seconds: float, half_life_seconds: float) -> float:
    if half_life_seconds <= 0 or delta_seconds < 0:
        raise ValueError("invalid temporal decay")
    return exp(-log(2) * delta_seconds / half_life_seconds)


def utility(s: Signals, w: Weights=Weights()) -> float:
    positive=(w.semantic*s.semantic+w.keyword*s.keyword+w.graph*s.graph+
              w.temporal*s.temporal+w.verified*s.verified+w.novelty*s.novelty+
              w.procedural*s.procedural+w.contradiction*s.contradiction+
              w.recency*s.recency)
    return positive-w.duplicate*s.duplicate-w.stale*s.stale


def pack(candidates: list[MemoryCandidate], budget: int,
         similarity: Callable[[str,str],float] | None=None, alpha: float=.8) -> list[MemoryCandidate]:
    """Greedy utility/token + MMR packing. Mandatory evidence is fail-closed."""
    if budget < 0 or not 0 <= alpha <= 1:
        raise ValueError("invalid budget/alpha")
    selected=[]; used=0
    for c in sorted((x for x in candidates if x.mandatory),key=lambda x:x.ref):
        if c.tokens<0 or used+c.tokens>budget:
            raise ValueError(f"mandatory evidence exceeds budget: {c.ref}")
        selected.append(c); used+=c.tokens
    remaining=[x for x in candidates if not x.mandatory and x.tokens>0]
    while remaining:
        scored=[]
        for c in remaining:
            redundancy=max((similarity(c.ref,s.ref) for s in selected),default=0.0) if similarity else 0.0
            marginal=alpha*utility(c.signals)-(1-alpha)*redundancy
            scored.append((marginal/c.tokens,marginal,c.ref,c))
        _,marginal,_,best=max(scored,key=lambda x:(x[0],x[1],x[2]))
        remaining.remove(best)
        if marginal<=0:
            continue
        if used+best.tokens<=budget:
            selected.append(best); used+=best.tokens
    return selected
