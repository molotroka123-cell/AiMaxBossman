"""V7 convergence: the gates that decide whether a night's work actually landed.

Nothing here executes product work. It runs the checks that already exist and
reports what they returned — an objective is PASS because a suite passed and an
evidence file says so, never because this package says it is.
"""
from __future__ import annotations

__all__ = ["nightly_run"]
