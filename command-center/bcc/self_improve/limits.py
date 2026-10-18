"""Frozen limits and the list of paths the self-improvement code may never write.

``Limits`` is a frozen dataclass: an agent that tries to assign to it gets an exception, and
``assert_limits_unchanged`` compares against the digest journaled at the start of a run.
``guard_write_target`` refuses protected files (constitution, its pin, long_run.json, settings
with permission modes, the permission gates and this package) EVEN WITH an owner token: those
are changed by a human in a separate, reviewed step, never by this loop.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

PASS, FAIL, BLOCKED, NOT_RUN = "PASS", "FAIL", "BLOCKED", "NOT_RUN"
NA = "N/A"
STATUSES = (PASS, FAIL, BLOCKED, NOT_RUN)


class LimitViolation(RuntimeError):
    """A hard limit was hit or a protected resource was touched. The caller maps it to BLOCKED."""


@dataclass(frozen=True)
class Limits:
    max_delegation_depth: int = 2
    max_wall_seconds: float = 600.0
    max_tokens: int = 200_000
    cost_cap_usd: float = 0.0          # free models only unless long_run.json already carries a cap
    recurrence_threshold: int = 3      # >= N similar failures before a hypothesis is opened
    min_heldout_tasks: int = 5         # fewer held-out tasks than this -> the claim stays NOT_RUN
    max_sandbox_bytes: int = 5_000_000
    max_draft_bytes: int = 200_000

    @staticmethod
    def make(*, requested_cost_cap_usd: float = 0.0, long_run_cap_usd: float | None = None, **kw) -> "Limits":
        """Cost cap is 0 unless an owner-written cap already exists; a request can only lower it."""
        cap = 0.0
        if long_run_cap_usd is not None and long_run_cap_usd > 0:
            cap = max(0.0, min(float(requested_cost_cap_usd), float(long_run_cap_usd)))
        base = Limits()
        # the delegation depth can be lowered by a caller but never raised above the owner's 2
        depth = min(int(kw.pop("max_delegation_depth", base.max_delegation_depth)), base.max_delegation_depth)
        return Limits(max_delegation_depth=depth, cost_cap_usd=cap, **kw)

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


DEFAULT_LIMITS = Limits()


def long_run_cap_usd(data_dir: str | os.PathLike | None) -> float | None:
    """Read-only look at ``<data>/autonomy/long_run.json``: the largest existing positive per-day cap,
    or None. Never writes. USD ~ EUR booking is the same as bcc.long_run uses."""
    if not data_dir:
        return None
    path = Path(data_dir) / "autonomy" / "long_run.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    caps = raw.get("caps_eur_per_day")
    if not isinstance(caps, dict):
        return None
    vals = [float(v) for v in caps.values() if isinstance(v, (int, float)) and v > 0]
    return max(vals) if vals else None


def assert_limits_unchanged(limits: Limits, journaled_digest: str) -> None:
    if limits.digest() != journaled_digest:
        raise LimitViolation("limits differ from the digest journaled at run start")


PROTECTED_GLOBS = (
    "*long_run.json", "*BOSSMAN_CONSTITUTION*", "*constitution.sha256", "*constitution.py",
    "*settings.local.json", "*settings.json",
    "*/autonomy/policy.py", "*/autonomy/approver_policy.py", "*/autonomy/stop.py", "*/autonomy/hands.py",
    "*/bcc/permissions.py", "*/bcc/v2/permissions.py", "*/bcc/provider_governance.py",
    "*/bcc/approvals.py", "*/bcc/approval_scope.py", "*/bcc/secrets.py", "*/bcc/features/nl_permissions.py",
    "*/bcc/features/pc_agent_policy*.py", "*/bcc/self_improve/*", "*/self_improve/owner/*",
    "*/STOP", "*vault*", "*.env", "*provider-keys*", "*credentials*", "*bypassPermissions*",
)


def is_protected(path: str | os.PathLike) -> bool:
    p = "/" + str(path).replace("\\", "/").lower().lstrip("/")
    return any(fnmatch.fnmatch(p, g.lower()) for g in PROTECTED_GLOBS)


def guard_write_target(path: str | os.PathLike) -> None:
    if is_protected(path):
        raise LimitViolation(f"protected path, never writable by the self-improvement loop: {Path(path).name}")
