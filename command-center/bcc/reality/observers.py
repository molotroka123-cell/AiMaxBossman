"""Observation adapters: measured readings, never beliefs.

The World State Graph is only worth having if what goes into it was actually
observed. Every adapter here is deterministic and model-free by construction —
`subprocess`, `psutil`, a SQL count, a health record — so no model output can
enter the projection as a fact. That is the mechanical form of
`WorldState belief != verified external truth`.

Two rules each adapter follows:

  * An adapter that cannot measure something says so. It returns `available:
    False` with a reason and contributes no fact, rather than a zero, an empty
    list, or a cached value. "We could not look" and "we looked and found
    nothing" are different findings, and merging them is how a broken probe
    reads as a healthy system.
  * Every reading carries how long it stays valid. A fact with no expiry is a
    fact that is fresh forever, which is the one thing an observation never is.

The facts are handed to `bossman_shared.objective_world_state`, which already
refuses to return a stale value; nothing here re-implements freshness.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from ..db import approvals as approvals_t, models as models_t, tasks as tasks_t

#: Default validity per source, in seconds. Chosen from how fast each thing
#: actually changes: a git worktree can change between two commands, while a
#: provider's health does not meaningfully move minute to minute.
VALIDITY = {"git": 30.0, "process": 15.0, "task": 20.0, "provider": 300.0, "app": 60.0}


@dataclass
class Observation:
    """One adapter's reading. `available=False` is a finding, not an error."""
    source: str
    key: str
    available: bool
    value: Any = None
    reason: str = ""
    observed_at: float = field(default_factory=time.time)
    max_age_seconds: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "key": self.key, "available": self.available,
                "value": self.value, "reason": self.reason,
                "observed_at": self.observed_at,
                "max_age_seconds": self.max_age_seconds}


def _unavailable(source: str, key: str, reason: str) -> Observation:
    return Observation(source, key, False, None, reason,
                       max_age_seconds=VALIDITY.get(source, 60.0))


# ------------------------------------------------------------------- git

def observe_git(repo: Path | str | None) -> Observation:
    """Working-tree state of a repository, read with git itself."""
    key = "git.worktree"
    if repo is None:
        return _unavailable("git", key, "репозиторий не указан")
    path = Path(repo)
    if not (path / ".git").exists():
        return _unavailable("git", key, f"{path} — не git-репозиторий")
    if shutil.which("git") is None:
        return _unavailable("git", key, "git не установлен")
    try:
        proc = subprocess.run(["git", "-C", str(path), "status", "--porcelain=v1", "--branch"],
                              capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable("git", key, f"{type(exc).__name__}")
    if proc.returncode != 0:
        return _unavailable("git", key, f"git status вернул {proc.returncode}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    branch = ""
    changed = []
    for line in lines:
        if line.startswith("##"):
            branch = line[2:].strip().split("...")[0]
            continue
        changed.append(line[3:].strip())
    return Observation("git", key, True,
                       {"branch": branch, "dirty": bool(changed),
                        "changed_count": len(changed), "changed": changed[:50]},
                       max_age_seconds=VALIDITY["git"])


# --------------------------------------------------------------- process

def observe_process() -> Observation:
    """Host CPU and memory. Reported only if actually measured — an invented
    memory headroom is the defect the resource work already closed once."""
    key = "process.host"
    try:
        import psutil  # type: ignore
    except ImportError:
        return _unavailable("process", key, "psutil не установлен — измерить нечем")
    try:
        memory = psutil.virtual_memory()
        value = {"cpu_percent": psutil.cpu_percent(interval=0.1),
                 "ram_total_mb": round(memory.total / 1024 / 1024),
                 "ram_available_mb": round(memory.available / 1024 / 1024),
                 "ram_percent": memory.percent}
    except Exception as exc:  # noqa: BLE001 — недоступный счётчик не выдумывается
        return _unavailable("process", key, f"{type(exc).__name__}")
    return Observation("process", key, True, value, max_age_seconds=VALIDITY["process"])


# ------------------------------------------------------------------ task

async def observe_tasks(svc) -> Observation:
    """Live task/approval counts, including the deadlock metric this run pins
    at zero — the world state should be able to answer "is anything stuck"."""
    key = "task.queue"
    try:
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(tasks_t.c.status,
                                              sa.func.count()).group_by(tasks_t.c.status))).fetchall()
            pending = int((await s.execute(sa.select(sa.func.count()).select_from(
                approvals_t).where(approvals_t.c.status == "pending"))).scalar() or 0)
        from ..review_escalation import audit as deadlock_audit
        deadlocks = await deadlock_audit(svc)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("task", key, f"{type(exc).__name__}")
    return Observation("task", key, True,
                       {"by_status": {str(r[0]): int(r[1]) for r in rows},
                        "pending_approvals": pending,
                        "deadlocked": deadlocks["deadlocked"]},
                       max_age_seconds=VALIDITY["task"])


# -------------------------------------------------------------- provider

async def observe_provider_health(svc) -> Observation:
    """Measured model health, straight from the B5 records.

    Deliberately reports `unmeasured` as its own count rather than folding it
    into healthy or unhealthy: the whole point of that status is that it is
    neither."""
    key = "provider.models"
    try:
        from .. import model_health as mh
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(models_t.c.id, models_t.c.alias,
                                              models_t.c.health))).fetchall()
        records = {int(r[0]): mh.HealthRecord.from_dict(r[2]) for r in rows}
    except Exception as exc:  # noqa: BLE001
        return _unavailable("provider", key, f"{type(exc).__name__}")
    if not records:
        return _unavailable("provider", key, "модели не настроены")
    from .. import model_health as mh
    return Observation("provider", key, True, mh.summary(records),
                       max_age_seconds=VALIDITY["provider"])


# ------------------------------------------------------------------- app

async def observe_apps(svc) -> Observation:
    """Discovered apps and how many are actually live."""
    key = "app.inventory"
    try:
        from ..features.apps import collect
        from ..features import apps_control as ctl
        apps = await collect()
        policy = await ctl.policy(svc)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("app", key, f"{type(exc).__name__}")
    return Observation("app", key, True,
                       {"count": len(apps),
                        "live": sum(1 for a in apps if a.get("status") == "LIVE"),
                        "control_enabled": bool(policy.get("enabled"))},
                       max_age_seconds=VALIDITY["app"])


# ------------------------------------------------------------------- all

async def observe_all(svc, *, repo: Path | str | None = None) -> list[Observation]:
    """Every adapter, in one pass. One adapter failing never hides the others:
    a partial picture with an honest gap beats no picture at all."""
    out = [observe_git(repo), observe_process()]
    for coroutine in (observe_tasks(svc), observe_provider_health(svc), observe_apps(svc)):
        try:
            out.append(await coroutine)
        except Exception as exc:  # noqa: BLE001
            out.append(_unavailable("unknown", "unknown", f"{type(exc).__name__}"))
    return out


def to_world_facts(observations: list[Observation], *, scope_id: str) -> list[dict[str, Any]]:
    """Shape the available readings for `objective_world_state.WorldFact`.

    Unavailable observations are dropped rather than ingested as a null: an
    absent fact reads as MISSING in the projection, which is the honest answer,
    while a null fact would read as FRESH knowledge that the value is nothing."""
    facts = []
    for obs in observations:
        if not obs.available:
            continue
        facts.append({"scope_id": scope_id, "key": obs.key, "value": obs.value,
                      "source": f"observer:{obs.source}",
                      "observed_at": obs.observed_at,
                      "max_age_seconds": obs.max_age_seconds,
                      "provenance": f"bcc.reality.observers.{obs.source}"})
    return facts
