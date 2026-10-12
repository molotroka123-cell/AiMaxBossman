"""Motion workflow core: a versioned plan, a local video analyzer and a crash-safe project store.

This package is the CORE of the Motion workflow inside Bossman Motion Studio; routes and UI are
registered elsewhere.  Nothing in it makes a network call or a paid API call.

  plan.py     the plan document: schema, limits, validation, ``@name`` binding, edits, diff, estimate
  analyze.py  video file -> plan (scene cuts, cut type, camera motion, audio timing, text or UNKNOWN)
  store.py    on-disk project store: versions, assets, render jobs, locks, restart recovery
"""
from __future__ import annotations

from . import analyze, plan, store
from .analyze import AnalysisError, AnalyzerUnavailable, analyze_video, available
from .plan import (CLOUD_STATEMENT, COST_MODEL, LIMITS, LimitError, MissingReferenceError, PlanError,
                   SCHEMA_VERSION, apply_ops, diff, effective_output, estimate, find_unknowns, limits_report,
                   limits_snapshot, new_plan, readiness, validate)
from .store import (ConflictError, IntegrityError, JobStateError, PathEscapeError, ProjectNotFound, Store,
                    StoreBusyError, StoreError)

__all__ = [
    "analyze", "plan", "store", "Store", "analyze_video", "available", "new_plan", "validate", "apply_ops",
    "diff", "estimate", "readiness", "effective_output", "find_unknowns", "limits_report", "limits_snapshot",
    "LIMITS", "COST_MODEL", "CLOUD_STATEMENT", "SCHEMA_VERSION",
    "AnalysisError", "AnalyzerUnavailable", "PlanError", "LimitError", "MissingReferenceError",
    "StoreError", "PathEscapeError", "ProjectNotFound", "ConflictError", "IntegrityError", "StoreBusyError",
    "JobStateError",
]
