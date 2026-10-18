"""Safe self-improvement superstructure for the Bossman swarm (owner spec 18.10.2026).

Everything here is PROPOSE-ONLY: bots observe repeated failures, write reviewable drafts,
test them in an isolated directory against a frozen baseline on held-out tasks with an
independent verifier, and report. Applying anything needs an owner approval token that
agents can neither issue nor read (see ``owner``). Nothing here edits the constitution,
the limits, permission gates or ``long_run.json``.

Statuses are literal and only these: PASS / FAIL / BLOCKED / NOT_RUN (and ``N/A`` for data
that does not exist). The word "improved" is never used without a PASS on held-out tasks.
"""
from __future__ import annotations

from .limits import BLOCKED, FAIL, NA, NOT_RUN, PASS, STATUSES, Limits

__all__ = ["BLOCKED", "FAIL", "NA", "NOT_RUN", "PASS", "STATUSES", "Limits"]
