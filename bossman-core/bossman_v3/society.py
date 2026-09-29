"""Persistent local role society for Bossman 1.5.

Roles survive tasks and restarts. Selection uses measured verifier-backed
performance, task-class specialization and reusable skill references. This
module has no model/network/action authority; Jev/routers may choose among the
candidates it exposes, while existing Bossman policy remains authoritative.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from bossman_v3.skill_factory.reliability import reliability_lcb


DEFAULT_ROLES = {
    "coder": ("coding", "debug", "repo", "tests"),
    "researcher": ("research", "web", "documents", "evidence"),
    "market": ("market", "trading", "orderflow", "timeseries"),
    "vision": ("vision", "image", "video", "ocr"),
    "security": ("security", "redteam", "privacy", "permissions"),
    "verifier": ("verify", "testing", "evidence", "release"),
    "product": ("product", "ux", "business", "workflow"),
    "auditor": ("audit", "regression", "release", "governance"),
}


@dataclass
class RoleStats:
    attempts: int = 0
    successes: int = 0
    verifier_rejects: int = 0
    total_latency_s: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def failures(self) -> int:
        return max(0, self.attempts - self.successes)

    @property
    def reliability(self) -> float:
        return reliability_lcb(self.successes, self.failures, q_low=0.05)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["reliability_lcb"] = self.reliability
        return out


@dataclass
class Role:
    name: str
    specialties: tuple[str, ...] = ()
    skill_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    stats: dict[str, RoleStats] = field(default_factory=dict)

    def score(self, task_class: str) -> tuple[float, int, int]:
        task = task_class.lower()
        specialty = sum(1 for s in self.specialties if s in task or task in s)
        st = self.stats.get(task_class) or self.stats.get("*") or RoleStats()
        measured = 1 if st.attempts else 0
        return (st.reliability, specialty, measured)


class PersistentAgentSociety:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.roles: dict[str, Role] = {}
        self._load()
        self.ensure_defaults()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if payload.get("version") != self.VERSION:
            return
        for raw in payload.get("roles") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            stats = {}
            for task_class, st in (raw.get("stats") or {}).items():
                if isinstance(st, dict):
                    stats[str(task_class)] = RoleStats(
                        attempts=int(st.get("attempts") or 0),
                        successes=int(st.get("successes") or 0),
                        verifier_rejects=int(st.get("verifier_rejects") or 0),
                        total_latency_s=float(st.get("total_latency_s") or 0.0),
                        total_cost_usd=float(st.get("total_cost_usd") or 0.0),
                    )
            self.roles[str(raw["name"])] = Role(
                name=str(raw["name"]),
                specialties=tuple(str(x) for x in raw.get("specialties") or ()),
                skill_refs=[str(x) for x in raw.get("skill_refs") or ()],
                memory_refs=[str(x) for x in raw.get("memory_refs") or ()],
                stats=stats,
            )

    def _save(self) -> None:
        rows = []
        for role in sorted(self.roles.values(), key=lambda r: r.name):
            rows.append({
                "name": role.name,
                "specialties": list(role.specialties),
                "skill_refs": list(dict.fromkeys(role.skill_refs))[-200:],
                "memory_refs": list(dict.fromkeys(role.memory_refs))[-200:],
                "stats": {k: v.as_dict() for k, v in sorted(role.stats.items())},
            })
        payload = {"version": self.VERSION, "updated_at": time.time(), "roles": rows}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def ensure_defaults(self) -> None:
        with self._lock:
            changed = False
            for name, specialties in DEFAULT_ROLES.items():
                if name not in self.roles:
                    self.roles[name] = Role(name=name, specialties=tuple(specialties))
                    changed = True
            if changed:
                self._save()

    def remember_skill(self, role: str, skill_ref: str) -> None:
        if not skill_ref:
            raise ValueError("skill_ref required")
        with self._lock:
            self.roles[role].skill_refs.append(str(skill_ref))
            self._save()

    def remember_memory(self, role: str, memory_ref: str) -> None:
        if not memory_ref:
            raise ValueError("memory_ref required")
        with self._lock:
            self.roles[role].memory_refs.append(str(memory_ref))
            self._save()

    def record_outcome(self, role: str, task_class: str, *, verified_success: bool,
                       verifier_rejected: bool = False, latency_s: float = 0.0,
                       cost_usd: float = 0.0) -> None:
        if role not in self.roles:
            raise KeyError(role)
        if latency_s < 0 or cost_usd < 0:
            raise ValueError("negative metrics")
        with self._lock:
            st = self.roles[role].stats.setdefault(task_class, RoleStats())
            st.attempts += 1
            st.successes += int(bool(verified_success))
            st.verifier_rejects += int(bool(verifier_rejected))
            st.total_latency_s += float(latency_s)
            st.total_cost_usd += float(cost_usd)
            self._save()

    def candidates(self, task_class: str, *, allowed: Iterable[str] | None = None) -> list[dict]:
        allow = set(allowed or self.roles)
        rows = []
        for role in self.roles.values():
            if role.name not in allow:
                continue
            st = role.stats.get(task_class) or RoleStats()
            rows.append({
                "role": role.name,
                "specialties": list(role.specialties),
                "skill_refs": list(role.skill_refs),
                "memory_refs": list(role.memory_refs),
                "attempts": st.attempts,
                "reliability_lcb": st.reliability,
                "score": role.score(task_class),
            })
        rows.sort(key=lambda r: (r["score"], r["role"]), reverse=True)
        return rows

    def select_team(self, task_class: str, *, max_members: int = 3,
                    required: Iterable[str] = ()) -> list[str]:
        if max_members < 1:
            raise ValueError("max_members")
        required_list = list(dict.fromkeys(required))
        if any(r not in self.roles for r in required_list):
            raise KeyError("unknown required role")
        result = required_list[:max_members]
        for row in self.candidates(task_class):
            if row["role"] not in result:
                result.append(row["role"])
            if len(result) >= max_members:
                break
        return result
