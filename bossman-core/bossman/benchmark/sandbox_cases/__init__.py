"""REAL_SANDBOX case registry.

Every module here exercises REAL production classes over their REAL call path
with no paid or external service.  Each module exposes ``CASES`` mapping the
manifest case id to ``Callable[[int], dict]``; the row is always built through
:class:`bossman.benchmark.sandbox_row.CaseProbe`, so the pass/fail verdict is
computed by the trusted verifier and never self-reported by the case.
"""
from __future__ import annotations

from typing import Callable

from . import cache, context, reasoning, routing, safety, uca

_MODULES = (routing, context, reasoning, cache, safety, uca)

CASES: dict[str, Callable[[int], dict]] = {}
for _module in _MODULES:
    _clash = set(CASES) & set(_module.CASES)
    if _clash:  # two modules claiming one case id would make coverage ambiguous
        raise RuntimeError(f"duplicate sandbox case ids: {sorted(_clash)}")
    CASES.update(_module.CASES)

__all__ = ["CASES"]
