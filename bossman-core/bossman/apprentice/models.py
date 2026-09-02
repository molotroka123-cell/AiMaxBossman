"""Typed models of the apprentice. Reuses computer_operator models for
observations / expected states / action kinds; adds semantic targets (never
coordinates), plan steps with risk classes and the schema-bound ActionRecord."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from bossman.computer_operator.models import ActionKind, ExpectedState, Observation

from .errors import CoordinateTargetForbidden


class ApprenticeState(str, Enum):
    RECEIVE_TASK = "RECEIVE_TASK"; PLAN = "PLAN"; OBSERVE = "OBSERVE"; ACT = "ACT"
    VERIFY = "VERIFY"; CONTINUE = "CONTINUE"; RECOVER = "RECOVER"; FALLBACK = "FALLBACK"
    WAIT_APPROVAL = "WAIT_APPROVAL"; SUCCEED = "SUCCEED"; FAIL = "FAIL"


TERMINAL = frozenset({ApprenticeState.SUCCEED, ApprenticeState.FAIL})

# Allowed transitions (deterministic; tested).
TRANSITIONS: dict[ApprenticeState, frozenset[ApprenticeState]] = {
    ApprenticeState.RECEIVE_TASK: frozenset({ApprenticeState.PLAN, ApprenticeState.FAIL}),
    ApprenticeState.PLAN: frozenset({ApprenticeState.OBSERVE, ApprenticeState.FAIL, ApprenticeState.RECOVER}),
    ApprenticeState.OBSERVE: frozenset({ApprenticeState.ACT, ApprenticeState.PLAN, ApprenticeState.RECOVER,
                                        ApprenticeState.FAIL}),
    ApprenticeState.ACT: frozenset({ApprenticeState.VERIFY, ApprenticeState.RECOVER, ApprenticeState.WAIT_APPROVAL,
                                    ApprenticeState.FAIL, ApprenticeState.CONTINUE}),
    ApprenticeState.VERIFY: frozenset({ApprenticeState.CONTINUE, ApprenticeState.RECOVER, ApprenticeState.SUCCEED,
                                       ApprenticeState.FAIL}),
    ApprenticeState.CONTINUE: frozenset({ApprenticeState.OBSERVE, ApprenticeState.SUCCEED, ApprenticeState.FAIL}),
    ApprenticeState.RECOVER: frozenset({ApprenticeState.OBSERVE, ApprenticeState.PLAN, ApprenticeState.FALLBACK,
                                        ApprenticeState.FAIL}),
    ApprenticeState.FALLBACK: frozenset({ApprenticeState.PLAN, ApprenticeState.FAIL, ApprenticeState.WAIT_APPROVAL}),
    ApprenticeState.WAIT_APPROVAL: frozenset({ApprenticeState.ACT, ApprenticeState.FAIL}),
    ApprenticeState.SUCCEED: frozenset(),
    ApprenticeState.FAIL: frozenset(),
}


class RiskClass(str, Enum):
    LOW = "LOW"; MEDIUM = "MEDIUM"; HIGH = "HIGH"; EXTERNAL_EFFECT = "EXTERNAL_EFFECT"


APPROVAL_RISK = frozenset({RiskClass.HIGH, RiskClass.EXTERNAL_EFFECT})
_COORD_KEYS = frozenset({"x", "y", "coordinates", "coords", "px", "point", "bbox"})


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sha(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticTarget:
    """UI element identified by meaning, never by pixels."""
    role: str
    name: str = ""
    text: str = ""
    description: str = ""
    anchors: tuple[str, ...] = ()          # extra anchors: neighbour labels, aria ids, section titles

    def __post_init__(self) -> None:
        if not (self.role or self.name):
            raise ValueError("semantic target needs role or name")
        for a in self.anchors:
            if not isinstance(a, str):
                raise CoordinateTargetForbidden("anchors must be semantic strings")

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticTarget":
        if any(k in _COORD_KEYS for k in d):
            raise CoordinateTargetForbidden(f"coordinate keys are not allowed: {sorted(set(d) & _COORD_KEYS)}")
        return cls(role=str(d.get("role", "")), name=str(d.get("name", "")), text=str(d.get("text", "")),
                   description=str(d.get("description", "")), anchors=tuple(d.get("anchors") or ()))

    def as_dict(self) -> dict:
        return {"role": self.role, "name": self.name, "text": self.text, "description": self.description,
                "anchors": list(self.anchors)}

    def label(self) -> str:
        return f"{self.role}:{self.name or self.text}"


@dataclass(frozen=True, slots=True)
class AppIdentity:
    app: str = ""
    title_contains: str = ""
    url_contains: str = ""
    tab_id: str = ""

    def matches(self, foreground: dict | None) -> tuple[bool, str]:
        fg = foreground or {}
        if self.app and self.app.lower() not in str(fg.get("app", "")).lower():
            return False, f"foreground app {fg.get('app')!r} is not {self.app!r}"
        if self.title_contains and self.title_contains.lower() not in str(fg.get("title", "")).lower():
            return False, f"window title {fg.get('title')!r} lacks {self.title_contains!r}"
        if self.url_contains and self.url_contains.lower() not in str(fg.get("url", "")).lower():
            return False, f"url {fg.get('url')!r} lacks {self.url_contains!r}"
        if self.tab_id and str(fg.get("tab_id", "")) != self.tab_id:
            return False, f"tab {fg.get('tab_id')!r} is not {self.tab_id!r}"
        return True, "window matches"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    kind: ActionKind
    app: AppIdentity
    target: SemanticTarget | None = None
    text: str = ""
    args: dict = field(default_factory=dict)
    expected: ExpectedState = field(default_factory=ExpectedState)
    precondition: str = ""
    risk: RiskClass = RiskClass.LOW
    side_effecting: bool = False
    checkpoint: str = ""                    # name of a checkpoint predicate verified after the step
    is_goal: bool = False                   # verifying this step's checkpoint == task success
    derived_from_observation: bool = False  # planner derived the instruction from observed text
    allowed_domains: tuple[str, ...] = ()
    source: str = "planner"                 # planner | skill:<id> | recovery

    def __post_init__(self) -> None:
        if any(k in _COORD_KEYS for k in self.args):
            raise CoordinateTargetForbidden("plan steps cannot carry coordinates")
        if self.kind in (ActionKind.CLICK, ActionKind.DOUBLE_CLICK, ActionKind.TYPE, ActionKind.DRAG,
                         ActionKind.UI_INVOKE) and self.target is None:
            raise ValueError(f"{self.kind.value} needs a semantic target")


@dataclass(slots=True)
class Plan:
    goal: str
    steps: list[PlanStep]
    source: str = "planner"
    skill_ref: str = ""
    based_on_observation: str = ""


@dataclass(frozen=True, slots=True)
class ObservationRef:
    id: str
    generation: int
    hash: str
    observed_at: float

    @classmethod
    def of(cls, obs: Observation, hash_: str) -> "ObservationRef":
        return cls(id=obs.id, generation=int(obs.generation), hash=hash_, observed_at=float(obs.created_at))

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Verification:
    method: str
    ok: bool
    reason: str
    verifier_principal: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ActionRecord:
    """One attempted action. Stored only redacted (text_redacted / args_redacted)."""
    record_id: str
    task_id: str
    run_id: str
    session_id: str
    application: dict
    semantic_target: dict
    action: dict
    precondition: str
    pre_observation: dict
    expected_transition: dict
    post_observation: dict | None
    verification: dict | None
    result: str
    risk_class: str
    side_effect_id: str
    timestamp: float
    evidence_source: str
    step_id: str = ""
    injection_flagged: bool = False
    duplicate_suppressed: bool = False
    error_code: str = ""
    checkpoint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class TaskResult:
    task_id: str
    run_id: str
    session_id: str
    state: ApprenticeState
    reason: str
    records: list[ActionRecord]
    checkpoints_reached: list[str]
    steps_used: int
    recoveries: int
    fallbacks: int
    pending_step: PlanStep | None = None
    pending_digest: str = ""
    head_sha: str = ""
    environment: str = ""
    goal: str = ""

    @property
    def ok(self) -> bool:
        return self.state is ApprenticeState.SUCCEED


@dataclass(frozen=True, slots=True)
class ApprenticeTask:
    task_id: str
    goal: str
    run_id: str
    session_id: str
    head_sha: str = ""
    environment: str = ""
    task_type: str = "generic"
    max_steps: int = 40
    max_recoveries: int = 3
    max_fallbacks: int = 1
    owner_requested_fallback: bool = False
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls, goal: str, *, session_id: str, run_id: str | None = None, **kw) -> "ApprenticeTask":
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal is required")
        return cls(task_id=new_id("uca"), goal=goal[:4000], run_id=run_id or new_id("run"),
                   session_id=session_id, **kw)


def expected_as_dict(e: ExpectedState) -> dict:
    return {"contains_text": e.contains_text, "window_title_contains": e.window_title_contains,
            "foreground_app_contains": e.foreground_app_contains, "url_contains": e.url_contains,
            "absent_text": e.absent_text}
