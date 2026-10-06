"""One view of the autonomy control plane for the API, the release panel and the CLI.

Same data dir as the rest of Bossman (``<data_dir>/autonomy``): the dashboard,
``bossman autonomy ...`` and the loop read and write the same goal files,
journal and lease. The release panel never merges or pushes: Apply records
the user's decision and returns the exact fast-forward and rollback commands;
the goal becomes DEPLOYED only when the owner confirms they ran them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import constitution as const
from . import stop as stop_mod
from .budget import DailyBudget
from .goals import GoalStore
from .hands import MAX_LEVEL
from .journal import Journal
from .lease import EngineeringLease, kill_process_group, pid_alive
from .policy import CANONICAL_TARGET_BRANCH, LEVELS
from .types import GOAL_STATES

DEFAULT_LEVEL = "L2"                 # constitution: the system starts at L2 at most
DEFAULT_TARGET_BRANCH = CANONICAL_TARGET_BRANCH
WEIGHTS = "WEIGHTS_UNCHANGED"        # experience is retrieval context; no training ever happens in this loop
LEARNING_KIND = "retrieval_context"
PROMOTION_REQUIRED_CYCLES = 25       # clean COMPLETE goals before a level change may even be discussed
_REF = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,200}$")


class ReleaseRefused(RuntimeError):
    pass


def _ref(value: str, what: str) -> str:
    if not _REF.match(value or "") or ".." in value:
        raise ReleaseRefused(f"unsafe {what}: {value!r}")
    return value


def release_commands(candidate: dict) -> dict:
    sha = _ref(candidate.get("sha", ""), "sha")
    target = _ref(candidate.get("target_branch") or DEFAULT_TARGET_BRANCH, "target branch")
    if target != CANONICAL_TARGET_BRANCH:          # the loop never prepares a release to any other branch
        raise ReleaseRefused(f"releases go only to {CANONICAL_TARGET_BRANCH}, not {target!r}")
    branch = candidate.get("branch") or ""
    base = candidate.get("base_sha") or ""
    release = []
    if branch:
        release.append(f"git fetch origin {_ref(branch, 'candidate branch')}")
    release += [f"git switch {target}", f"git pull --ff-only origin {target}", f"git merge --ff-only {sha}",
                "git rev-parse HEAD", f"git push origin {target}"]
    rng = f"{_ref(base, 'base sha')}..{sha}" if base else sha
    rollback = [f"git switch {target}", f"git revert --no-edit {rng}", f"git push origin {target}"]
    return {"release": release, "rollback": rollback, "expect_head": sha, "target_branch": target}


class AutonomyService:
    def __init__(self, data_dir: str | os.PathLike, *, constitution_path: str | os.PathLike | None = None,
                 pin_path: str | os.PathLike | None = None,
                 constitution_status: Callable[[], Any] | None = None):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "autonomy"
        self.root.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.root)
        self.goals = GoalStore(self.root, self.journal)
        self.lease = EngineeringLease(self.root, journal=self.journal)
        self.budget = DailyBudget(self.root, journal=self.journal)
        self._cpath = constitution_path
        self._ppath = pin_path
        self._cstatus = constitution_status

    # .......................................................... status
    def constitution(self):
        if self._cstatus is not None:
            return self._cstatus()
        return const.verify(self._cpath, self._ppath)

    def level(self) -> str:
        p = self.root / "level.json"
        try:
            level = json.loads(p.read_text(encoding="utf-8")).get("level", DEFAULT_LEVEL)
        except (OSError, ValueError, AttributeError):
            return DEFAULT_LEVEL
        if level not in LEVELS:
            return "L0"                                         # unknown -> the most restrictive level
        return level if LEVELS.index(level) <= LEVELS.index(MAX_LEVEL) else MAX_LEVEL   # never above the hard cap

    # .......................................................... emergency stop
    def stop_reason(self) -> str:
        """'' when the loop may run, otherwise why not (autonomy STOP and/or the owner's global STOP)."""
        return stop_mod.stop_reason(self.root, self.data_dir)

    def stop_state(self) -> dict:
        sources = stop_mod.stop_sources(self.root, self.data_dir)
        return {"active": bool(sources), "sources": sources, "reason": self.stop_reason()}

    def request_stop(self, *, by: str = "owner", reason: str = "") -> dict:
        """Set the autonomy STOP and kill the worker process trees registered with the engineering lease. The cycle
        (another process) sees the file on its next step and its sessions poll it while a CLI runs."""
        rec = stop_mod.request_stop(self.root, by=by, reason=reason)
        lease = self.lease.peek() or {}
        killed = []
        if lease and not lease.get("stale_reason"):
            for pid in lease.get("processes") or ():
                if pid_alive(int(pid)):
                    kill_process_group(int(pid))
                    killed.append(int(pid))
        self.journal.append("autonomy.stop", {"by": by, "reason": rec["reason"], "killed": killed,
                                              "lease_goal": lease.get("goal_id", "")})
        return {"stop": rec, "killed": killed, "state": self.stop_state()}

    def clear_stop(self, *, by: str = "owner") -> dict:
        """Clear the autonomy STOP only. The owner's global STOP is cleared where it was set (Computer Use resume)."""
        removed = stop_mod.clear_stop(self.root)
        self.journal.append("autonomy.resume", {"by": by, "removed": removed})
        state = self.stop_state()
        return {"cleared": removed, "state": state,
                "note": ("the owner's global STOP is still set: clear it with the Computer Use resume"
                         if "computer" in state["sources"] else "")}

    # .......................................................... mode / promotion (read-only)
    def autonomy_mode(self) -> dict:
        """What the loop may do by itself: nothing that changes the product. Read-only; nothing here can change it."""
        return {"autonomous_apply": "OFF", "level": self.level(), "max_level": MAX_LEVEL,
                "reason": f"level capped at {MAX_LEVEL} (hands.MAX_LEVEL), no apply executor exists, a release is "
                          "prepared for the owner as commands and only the owner runs them",
                "release": "OWNER_ONLY", "weights": WEIGHTS, "learning_kind": LEARNING_KIND}

    def promotion_eligibility(self, required: int = PROMOTION_REQUIRED_CYCLES) -> dict:
        """How many clean cycles exist (COMPLETE, never rolled back, no hand refused by the policy). Read-only: it
        never changes the level; raising it would be an owner command in an interactive terminal (not implemented)."""
        refused: dict[str, int] = {}
        for e in self.journal.entries(kind="hand.refused"):
            gid = (e.get("payload") or {}).get("goal_id")
            if gid:
                refused[gid] = refused.get(gid, 0) + 1
        clean, rolled = [], []
        for rec in self.goals.list():
            gid = rec["goal"]["goal_id"]
            went_back = rec.get("state") == "ROLLED_BACK" or any(h.get("to") == "ROLLED_BACK"
                                                                 for h in rec.get("history") or [])
            if went_back:
                rolled.append(gid)
            if rec.get("state") == "COMPLETE" and rec.get("outcome") == "accepted" and not went_back \
                    and not refused.get(gid):
                clean.append(gid)
        reasons = []
        if len(clean) < required:
            reasons.append(f"{len(clean)} of {required} clean cycles")
        if rolled:
            reasons.append(f"rolled back: {', '.join(sorted(rolled)[:5])}")
        return {"eligible": not reasons, "clean_cycles": len(clean), "required": required, "rolled_back": sorted(rolled),
                "policy_refusals": sum(refused.values()), "reasons": reasons, "level": self.level(),
                "note": "read-only; eligibility never changes the level"}

    def heartbeat(self) -> dict | None:
        try:
            raw = json.loads((self.root / "heartbeat.json").read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None

    def status(self) -> dict:
        c = self.constitution()
        counts = {s: 0 for s in GOAL_STATES}
        for rec in self.goals.list():
            counts[rec.get("state", "BLOCKED")] = counts.get(rec.get("state", "BLOCKED"), 0) + 1
        lease = self.lease.peek()
        if lease:
            lease.pop("token", None)                         # the token is the writer's capability
        v = self.journal.verify()
        stop = self.stop_state()
        loop = "BLOCKED" if not c.ok else ("STOPPED" if stop["active"] else "READY")
        reason = "" if c.ok else c.reason
        if c.ok and stop["active"]:
            reason = stop["reason"]
        return {"loop": loop, "reason": reason,
                "constitution": c.as_dict() if hasattr(c, "as_dict") else {"ok": c.ok, "reason": c.reason},
                "level": self.level(), "lease": lease,
                "goals": {k: n for k, n in counts.items() if n}, "goals_total": sum(counts.values()),
                "journal": {"ok": v.ok, "entries": v.entries, "head": v.head, "reason": v.reason},
                "stop": stop, "mode": self.autonomy_mode(), "promotion": self.promotion_eligibility(),
                "budget": self.budget.snapshot(), "heartbeat": self.heartbeat(),
                "weights": WEIGHTS, "learning_kind": LEARNING_KIND}

    # .......................................................... goals
    def summary(self, rec: dict) -> dict:
        g = rec["goal"]
        c = rec.get("candidate") or {}
        return {"goal_id": g["goal_id"], "problem": g["problem"], "state": rec["state"], "risk_tier": g["risk_tier"],
                "target_metric": g["target_metric"], "blocked_reason": rec.get("blocked_reason", ""), "outcome": rec.get("outcome", ""),
                "sha": c.get("sha", ""), "updated_at": rec.get("updated_at", ""),
                "approvals": sorted(k for k in (rec.get("approvals") or {})
                                    if GoalStore._approved_by(rec, k))}

    def list_goals(self) -> list[dict]:
        return [self.summary(r) for r in self.goals.list()]

    def release_actions(self, rec: dict) -> dict:
        c_ok = bool(self.constitution().ok)
        in_gate = rec["state"] == "USER_APPROVAL"
        approvals = GoalStore.approvals_valid(rec)
        staging = GoalStore.staging_passed(rec)
        decided = GoalStore.apply_decided(rec)

        def act(ok: bool, reason: str) -> dict:
            return {"allowed": ok, "reason": "" if ok else reason}

        apply_reason = ("constitution is not pinned / changed" if not c_ok else
                        f"the goal is {rec['state']}, not USER_APPROVAL" if not in_gate else
                        "both approvals bound to the current SHA+diff are required" if not approvals else
                        "staging did not pass for this SHA" if not staging else
                        "already applied: confirm after running the commands" if decided else "")
        return {"apply": act(not apply_reason, apply_reason),
                "confirm": act(in_gate and decided, "Apply first, then run the commands and confirm"),
                "reject": act(in_gate, f"the goal is {rec['state']}, not USER_APPROVAL"),
                "revise": act(in_gate, f"the goal is {rec['state']}, not USER_APPROVAL")}

    def goal_view(self, goal_id: str) -> dict:
        rec = self.goals.get(goal_id)
        evidence = self.journal.entries(goal_id=goal_id, limit=300)
        evaluations = self.journal.entries(kind="staging_evaluation", goal_id=goal_id, limit=1)
        view = {**self.summary(rec), "record": rec, "evidence": evidence, "actions": self.release_actions(rec),
                "approvals_valid": GoalStore.approvals_valid(rec), "staging_passed": GoalStore.staging_passed(rec),
                "evaluation": (evaluations[-1]["payload"] if evaluations else None),
                "weights": WEIGHTS, "learning_kind": LEARNING_KIND}
        if GoalStore.apply_decided(rec):
            view["commands"] = release_commands(rec["candidate"])
        return view

    # .......................................................... release panel
    def apply(self, goal_id: str, sha: str, diff_sha256: str, note: str = "") -> dict:
        rec = self.goals.get(goal_id)
        a = self.release_actions(rec)["apply"]
        if not a["allowed"]:
            raise ReleaseRefused(a["reason"])
        cmds = release_commands(rec["candidate"])
        rec = self.goals.record_user_decision(goal_id, "apply", sha=sha, diff_sha256=diff_sha256, note=note)
        self.journal.append("release.commands", {"goal_id": goal_id, "sha": sha, **cmds})
        return {"goal": self.summary(rec), "commands": cmds,
                "next": "run the release commands yourself, check HEAD, then press Confirm released"}

    def confirm_released(self, goal_id: str, sha: str, diff_sha256: str) -> dict:
        rec = self.goals.get(goal_id)
        if not self.release_actions(rec)["confirm"]["allowed"]:
            raise ReleaseRefused("Apply first, then run the commands and confirm")
        c = rec.get("candidate") or {}
        if c.get("sha") != sha or c.get("diff_sha256") != diff_sha256:
            raise ReleaseRefused("confirmation names a different SHA/diff than the applied candidate")
        rec = self.goals.transition(goal_id, "DEPLOYED", {"owner_confirmed": True, "sha": sha,
                                                          "diff_sha256": diff_sha256})
        return {"goal": self.summary(rec)}

    def reject(self, goal_id: str, note: str = "") -> dict:
        rec = self.goals.get(goal_id)
        c = rec.get("candidate") or {}
        rec = self.goals.record_user_decision(goal_id, "reject", sha=c.get("sha", ""),
                                              diff_sha256=c.get("diff_sha256", ""), note=note)
        return {"goal": self.summary(rec)}

    def revise(self, goal_id: str, note: str = "") -> dict:
        rec = self.goals.get(goal_id)
        c = rec.get("candidate") or {}
        rec = self.goals.record_user_decision(goal_id, "revise", sha=c.get("sha", ""),
                                              diff_sha256=c.get("diff_sha256", ""), note=note)
        return {"goal": self.summary(rec)}


__all__ = ["AutonomyService", "LEARNING_KIND", "PROMOTION_REQUIRED_CYCLES", "ReleaseRefused", "WEIGHTS",
           "release_commands"]
