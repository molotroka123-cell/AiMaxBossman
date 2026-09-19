"""V7 reality core — adaptive execution on top of the existing V4-V6 substrate.

Four pieces, each answering a failure the acceptance corpus actually recorded:

  compiler   owner intent -> a validated Mission IR CANDIDATE. Compilation
             grants nothing; the IR is a structural contract, and every effect
             it names still goes through the same approvals and evidence gates.
  observers  deterministic, model-free readings of git, processes, tasks,
             provider health and app state, turned into world facts. An
             observer that cannot measure something reports that it cannot,
             rather than guessing.
  strategy   ranked execution paths with evidence BANDS instead of invented
             decimals, plus a shadow router that recommends and never decides.
  recovery   a bounded ladder that changes strategy on failure instead of
             re-sending the same request to the same model.

What this package deliberately does NOT do:

  * It does not define a second Mission IR. `bossman_shared.mission_ir` is the
    contract; a parallel one would be a second authority ledger.
  * It does not define a second world state. `bossman_shared.objective_world_state`
    already refuses to return a stale value, which is the property that matters.
  * It does not grant authority. A strategy score is not a permission, a model's
    confidence is not evidence, and a shadow recommendation changes nothing
    until the owner's own gates say so.
"""
from __future__ import annotations

from .compiler import CompileResult, compile_intent
from .observers import observe_all
from .recovery import Ladder, Rung, classify_failure, next_rung
from .strategy import Band, Strategy, ShadowDecision, generate_strategies, shadow_route
from .telemetry import ShadowRecord, record_outcome, record_shadow

__all__ = [
    "CompileResult", "compile_intent",
    "observe_all",
    "Ladder", "Rung", "classify_failure", "next_rung",
    "Band", "Strategy", "ShadowDecision", "generate_strategies", "shadow_route",
    "ShadowRecord", "record_shadow", "record_outcome",
]
