"""Bounded supervisor of the autonomy loop: `bossman autonomy run`.

A supervisor is not a daemon that runs forever: it is a LIMITED runner that a scheduler (Windows Task Scheduler, see
``tools/autonomy_supervisor.ps1``) or the owner starts. Every run has hard limits: a number of cycles
(default 1) and a wall-clock time (default 2 hours), both capped. It runs a goal only when ALL of these hold on every
pass, and stops with a named status otherwise:

* the owner pinned the constitution (``constitution.verify`` ok);
* no STOP (autonomy STOP or the owner's global STOP) - checked before every pass and every few seconds while waiting;
  the cycle itself also checks it on every step and the writer / reviewer sessions kill their CLI tree;
* the goal's and the daily budget allow one more turn (``DailyBudget``);
* the engineering lease is free (one writer at a time, across processes);
* no goal waits for the owner (USER_APPROVAL): the loop never stacks a second gate on top of an open one.

Every pass writes ``<root>/heartbeat.json`` (also while a cycle runs) and journal entries ``supervisor.*``. A goal that
reaches USER_APPROVAL stops the supervisor (WAITING_OWNER): the owner's decision is the next step, never a timer.
A failing pass is retried only after an exponential pause and at most ``max_errors`` times in a row - never an endless
loop of errors. Autonomous apply stays OFF: the supervisor can only run the same bounded cycle (level cap L2).

Statuses: FINISHED, WAITING_OWNER, STOPPED, BLOCKED (constitution / goal blocked / budget / lease), DRY, ERRORS,
REFUSED (another supervisor is running, invalid limits), IDLE (nothing to run).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .journal import atomic_write_bytes
from .lease import pid_alive
from .service import LEARNING_KIND, WEIGHTS, AutonomyService

HARD_MAX_CYCLES = 25
HARD_MAX_HOURS = 24.0
HEARTBEAT_NAME = "heartbeat.json"
MID_CYCLE_STATES = ("PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING", "DEPLOYED",
                    "MONITORING")


@dataclass
class SupervisorConfig:
    max_cycles: int = 1
    max_hours: float = 2.0
    interval_s: float = 0.0          # 0 = one sweep over the work, no waiting; > 0 = wait this long when idle
    dry: bool = False
    poll_s: float = 1.0              # how often a wait checks the STOP
    heartbeat_s: float = 15.0
    backoff_base_s: float = 5.0
    backoff_max_s: float = 300.0
    max_errors: int = 3

    def problems(self) -> list[str]:
        out = []
        if not 1 <= int(self.max_cycles) <= HARD_MAX_CYCLES:
            out.append(f"--max-cycles must be 1..{HARD_MAX_CYCLES}")
        if not 0 < float(self.max_hours) <= HARD_MAX_HOURS:
            out.append(f"--max-hours must be > 0 and at most {HARD_MAX_HOURS}")
        if float(self.interval_s) < 0:
            out.append("--interval-s must not be negative")
        return out


@dataclass
class SupervisorReport:
    status: str
    reason: str = ""
    cycles: int = 0
    passes: list[dict] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "cycles": self.cycles, "passes": self.passes,
                "started_at": self.started_at, "finished_at": self.finished_at, "weights": WEIGHTS,
                "learning_kind": LEARNING_KIND}


Sleeper = Callable[[float], Awaitable[Any]]


class Supervisor:
    def __init__(self, service: AutonomyService, make_cycle: Callable[[], Any], config: SupervisorConfig | None = None,
                 *, clock: Callable[[], float] = time.time, sleep: Sleeper = asyncio.sleep, pid: int | None = None):
        self.svc = service
        self.make_cycle = make_cycle
        self.c = config or SupervisorConfig()
        self._clock, self._sleep = clock, sleep
        self._pid = os.getpid() if pid is None else pid
        self._cycle: Any = None
        self._hb: dict = {}
        self.report = SupervisorReport(status="STARTING")

    # ------------------------------------------------------------ heartbeat / journal
    @property
    def heartbeat_path(self):
        return self.svc.root / HEARTBEAT_NAME

    def _beat(self, **fields: Any) -> None:
        self._hb.update(fields)
        body = {"pid": self._pid, "at": self._clock(), "max_cycles": self.c.max_cycles, "max_hours": self.c.max_hours,
                "interval_s": self.c.interval_s, "cycles": self.report.cycles, "weights": WEIGHTS,
                "learning_kind": LEARNING_KIND, **self._hb}
        try:
            atomic_write_bytes(self.heartbeat_path, json.dumps(body, sort_keys=True, default=str).encode("utf-8"))
        except OSError:
            pass                                    # a heartbeat that cannot be written must not stop a safe loop

    def _log(self, kind: str, payload: dict) -> None:
        self.svc.journal.append(kind, {"pid": self._pid, **payload})

    async def _heartbeat_task(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.c.heartbeat_s)
                self._beat()
        except asyncio.CancelledError:
            return

    def _other_supervisor(self) -> dict | None:
        hb = self.svc.heartbeat()
        if not hb or hb.get("status") not in ("RUNNING", "WAITING", "BACKOFF"):
            return None
        pid = int(hb.get("pid") or 0)
        fresh = self._clock() - float(hb.get("at") or 0) < max(3 * self.c.heartbeat_s, 45.0)
        return hb if pid != self._pid and pid_alive(pid) and fresh else None

    # ------------------------------------------------------------ work selection
    def _pick(self) -> tuple[str, str, str]:
        """(action, goal_id, why): action is resume | resume_after_stop | run | wait_owner | none."""
        recs = self.svc.goals.list()
        if any(r["state"] == "USER_APPROVAL" for r in recs):
            gid = next(r["goal"]["goal_id"] for r in recs if r["state"] == "USER_APPROVAL")
            return "wait_owner", gid, "a goal waits for the owner's decision (Apply / Reject / Revise)"
        for r in recs:
            if r["state"] in MID_CYCLE_STATES:
                return "resume", r["goal"]["goal_id"], f"continue {r['state']}"
        for r in recs:
            if r["state"] == "BLOCKED" and str(r.get("blocked_reason", "")).startswith("owner STOP"):
                return "resume_after_stop", r["goal"]["goal_id"], "blocked by an owner STOP that is now cleared"
        proposed = sorted((r for r in recs if r["state"] == "PROPOSED"), key=lambda r: str(r.get("created_at", "")))
        if proposed:
            return "run", proposed[0]["goal"]["goal_id"], "oldest PROPOSED goal"
        return "none", "", "no goal to run (create one with `bossman autonomy plan`)"

    def _preflight(self) -> tuple[str, str]:
        """('', '') when a pass may start, else (status, reason)."""
        stopped = self.svc.stop_reason()
        if stopped:
            return "STOPPED", stopped
        c = self.svc.constitution()
        if not getattr(c, "ok", False):
            return "BLOCKED", f"constitution: {getattr(c, 'reason', 'not verified')}"
        lim, used = self.svc.budget.limits(), self.svc.budget.usage()
        if used.cycles >= lim["cycles_per_day"]:
            return "BLOCKED", f"daily budget: cycles/day cap reached ({used.cycles}/{lim['cycles_per_day']})"
        why = self.svc.budget.exhausted(need_turns=1)
        if why:
            return "BLOCKED", why
        lease = self.svc.lease.peek()
        if lease and not lease.get("stale_reason"):
            return "BLOCKED", f"engineering lease is held by {lease.get('holder')} for {lease.get('goal_id')}"
        return "", ""

    async def _nap(self, seconds: float) -> str:
        """Sleep in short slices; returns the STOP reason the moment one appears (instant STOP), else ''."""
        end = self._clock() + seconds
        while True:
            stopped = self.svc.stop_reason()
            if stopped:
                return stopped
            left = end - self._clock()
            if left <= 0:
                return ""
            await self._sleep(min(self.c.poll_s, left))
            self._beat()

    def _cycle_obj(self) -> Any:
        if self._cycle is None:
            self._cycle = self.make_cycle()
        return self._cycle

    # ------------------------------------------------------------ the run
    async def run(self) -> SupervisorReport:
        rep = self.report
        rep.started_at = self._clock()
        problems = self.c.problems()
        if problems:
            return self._finish("REFUSED", "; ".join(problems))
        other = self._other_supervisor()
        if other:                                   # never overwrite the running supervisor's heartbeat
            return self._finish("REFUSED", f"another supervisor is running (pid {other.get('pid')})", beat=False)
        deadline = rep.started_at + float(self.c.max_hours) * 3600.0
        self._beat(status="RUNNING", started_at=rep.started_at, goal_id="", state="", reason="")
        self._log("supervisor.started", {"max_cycles": self.c.max_cycles, "max_hours": self.c.max_hours,
                                         "interval_s": self.c.interval_s, "dry": self.c.dry})
        beat = asyncio.ensure_future(self._heartbeat_task())
        errors = 0
        try:
            while True:
                if rep.cycles >= self.c.max_cycles:
                    return self._finish("FINISHED", f"max cycles reached ({rep.cycles})")
                if self._clock() >= deadline:
                    return self._finish("FINISHED", f"max hours reached ({self.c.max_hours})")
                status, reason = self._preflight()
                if status:
                    if status == "BLOCKED" and "engineering lease" in reason and self.c.interval_s > 0:
                        self._beat(status="WAITING", reason=reason)
                        stopped = await self._nap(self.c.interval_s)
                        if stopped:
                            return self._finish("STOPPED", stopped)
                        continue
                    return self._finish(status, reason)
                action, gid, why = self._pick()
                if action == "wait_owner":
                    if self.c.interval_s > 0 and not self.c.dry:      # a long-running supervisor waits for the owner
                        self._beat(status="WAITING", goal_id=gid, reason=why)
                        stopped = await self._nap(self.c.interval_s)
                        if stopped:
                            return self._finish("STOPPED", stopped)
                        continue
                    return self._finish("WAITING_OWNER", f"{gid}: {why}")
                if action == "none":
                    if self.c.interval_s > 0 and not self.c.dry:
                        self._beat(status="WAITING", goal_id="", reason=why)
                        stopped = await self._nap(self.c.interval_s)
                        if stopped:
                            return self._finish("STOPPED", stopped)
                        continue
                    return self._finish("FINISHED" if rep.passes else "IDLE", why)
                if self.c.dry:
                    self._log("supervisor.dry", {"action": action, "goal_id": gid, "why": why})
                    rep.passes.append({"action": action, "goal_id": gid, "dry": True})
                    return self._finish("DRY", f"would {action} {gid}: {why}")
                self._beat(status="RUNNING", goal_id=gid, state=action, reason=why)
                self._log("supervisor.pass", {"action": action, "goal_id": gid, "why": why})
                try:
                    out = await self._run_pass(action, gid)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one failing pass is paused and bounded, never an endless loop
                    errors += 1
                    detail = f"{type(exc).__name__}: {str(exc)[:200]}"
                    rep.passes.append({"action": action, "goal_id": gid, "error": detail})
                    self._log("supervisor.error", {"goal_id": gid, "error": detail, "consecutive": errors})
                    if errors >= self.c.max_errors:
                        return self._finish("ERRORS", f"{errors} consecutive errors, last: {detail}")
                    pause = min(self.c.backoff_base_s * (2 ** (errors - 1)), self.c.backoff_max_s)
                    self._beat(status="BACKOFF", reason=detail, backoff_s=pause)
                    stopped = await self._nap(pause)
                    if stopped:
                        return self._finish("STOPPED", stopped)
                    continue
                errors = 0
                rep.cycles += 1
                rep.passes.append({"action": action, "goal_id": gid, "state": out.state, "reason": out.reason})
                self._beat(status="RUNNING", goal_id=gid, state=out.state, reason=out.reason)
                self._log("supervisor.pass_finished", {"goal_id": gid, "state": out.state, "reason": out.reason[:300]})
                if out.state == "USER_APPROVAL":
                    return self._finish("WAITING_OWNER", f"{gid} waits for the owner's decision")
                if out.state == "BLOCKED":
                    stopped = str(out.reason).startswith("owner STOP")
                    return self._finish("STOPPED" if stopped else "BLOCKED", f"{gid}: {out.reason}")
        finally:
            beat.cancel()

    async def _run_pass(self, action: str, goal_id: str) -> Any:
        cycle = self._cycle_obj()
        if action == "resume":
            return await cycle.resume(goal_id)
        if action == "resume_after_stop":
            return await cycle.resume_after_stop(goal_id)
        return await cycle.run_goal(self.svc.goals.goal(goal_id))

    def _finish(self, status: str, reason: str, *, beat: bool = True) -> SupervisorReport:
        rep = self.report
        rep.status, rep.reason, rep.finished_at = status, reason, self._clock()
        if beat:
            self._beat(status=status, reason=reason, finished_at=rep.finished_at)
        self._log("supervisor.finished", {"status": status, "reason": reason[:300], "cycles": rep.cycles})
        return rep


EXIT_BY_STATUS = {"FINISHED": 0, "DRY": 0, "IDLE": 0, "WAITING_OWNER": 0, "STOPPED": 6, "BLOCKED": 5, "REFUSED": 5,
                  "ERRORS": 1}

__all__ = ["EXIT_BY_STATUS", "HARD_MAX_CYCLES", "HARD_MAX_HOURS", "HEARTBEAT_NAME", "Supervisor", "SupervisorConfig",
           "SupervisorReport"]
