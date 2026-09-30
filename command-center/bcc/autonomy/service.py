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
from .goals import GoalStore
from .journal import Journal
from .lease import EngineeringLease
from .policy import LEVELS
from .types import GOAL_STATES

DEFAULT_LEVEL = "L2"                 # constitution: the system starts at L2 at most
DEFAULT_TARGET_BRANCH = "release/bossman-owner"
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
        self.root = Path(data_dir) / "autonomy"
        self.root.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.root)
        self.goals = GoalStore(self.root, self.journal)
        self.lease = EngineeringLease(self.root, journal=self.journal)
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
        return level if level in LEVELS else "L0"          # unknown -> the most restrictive level

    def status(self) -> dict:
        c = self.constitution()
        counts = {s: 0 for s in GOAL_STATES}
        for rec in self.goals.list():
            counts[rec.get("state", "BLOCKED")] = counts.get(rec.get("state", "BLOCKED"), 0) + 1
        lease = self.lease.peek()
        if lease:
            lease.pop("token", None)                         # the token is the writer's capability
        v = self.journal.verify()
        loop = "BLOCKED" if not c.ok else "READY"
        return {"loop": loop, "reason": "" if c.ok else c.reason,
                "constitution": c.as_dict() if hasattr(c, "as_dict") else {"ok": c.ok, "reason": c.reason},
                "level": self.level(), "lease": lease,
                "goals": {k: n for k, n in counts.items() if n}, "goals_total": sum(counts.values()),
                "journal": {"ok": v.ok, "entries": v.entries, "head": v.head, "reason": v.reason}}

    # .......................................................... goals
    def summary(self, rec: dict) -> dict:
        g = rec["goal"]
        c = rec.get("candidate") or {}
        return {"goal_id": g["goal_id"], "problem": g["problem"], "state": rec["state"], "risk_tier": g["risk_tier"],
                "target_metric": g["target_metric"], "blocked_reason": rec.get("blocked_reason", ""),
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
        view = {**self.summary(rec), "record": rec, "evidence": evidence, "actions": self.release_actions(rec),
                "approvals_valid": GoalStore.approvals_valid(rec), "staging_passed": GoalStore.staging_passed(rec)}
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


__all__ = ["AutonomyService", "ReleaseRefused", "release_commands"]
