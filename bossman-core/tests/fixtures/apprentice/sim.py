"""Generic desktop/browser simulator: a World with a foreground window and a
semantic UI tree; an Observer that produces fresh Observations (generation
increases per observe); an Actuator that mutates the world by semantic target.
No coordinates anywhere."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from bossman.computer_operator.models import ActionKind, Observation
from bossman.apprentice.models import Plan, PlanStep

_ids = itertools.count(1)


@dataclass
class Element:
    role: str
    name: str
    text: str = ""
    description: str = ""
    neighbors: list[str] = field(default_factory=list)
    on_click: Callable[["World"], None] | None = None
    on_type: Callable[["World", str], None] | None = None
    enabled: bool = True

    def as_dict(self) -> dict:
        return {"control_type": self.role, "role": self.role, "name": self.name, "text": self.text,
                "description": self.description, "neighbors": list(self.neighbors), "enabled": self.enabled}


@dataclass
class World:
    app: str = "Browser"
    title: str = "Home"
    url: str = ""
    tab_id: str = "t1"
    elements: list[Element] = field(default_factory=list)
    summary: str = ""
    version: int = 0
    sensitive: bool = False
    log: list[tuple[str, str]] = field(default_factory=list)

    def touch(self) -> None:
        self.version += 1

    def find(self, role: str, name: str) -> Element | None:
        for e in self.elements:
            if e.role == role and e.name == name:
                return e
        return None

    def foreground(self) -> dict:
        return {"app": self.app, "title": self.title, "url": self.url, "tab_id": self.tab_id}


class SimObserver:
    name = "sim"

    def __init__(self, world: World, *, clock: Callable[[], float] | None = None) -> None:
        self.world = world
        self._gen = 0
        self._t = 1_000.0
        self._last_version: dict[str, int] = {}
        self.observations = 0

    def observe(self) -> Observation:
        self._gen += 1
        self._t += 1.0
        self.observations += 1
        w = self.world
        obs = Observation(id=f"obs_{next(_ids)}", created_at=self._t, foreground=w.foreground(), summary=w.summary,
                          ui_tree={"elements": [e.as_dict() for e in w.elements]}, sensitive=w.sensitive,
                          generation=self._gen)
        self._last_version[obs.id] = w.version
        return obs

    def is_current(self, obs: Observation) -> bool:
        return self._last_version.get(obs.id) == self.world.version


class SimActuator:
    def __init__(self, world: World) -> None:
        self.world = world
        self.calls: list[tuple[str, str]] = []

    def act(self, step: PlanStep, obs: Observation) -> dict:
        w = self.world
        label = step.target.label() if step.target else step.kind.value
        self.calls.append((step.kind.value, label))
        if step.kind is ActionKind.FOCUS:
            want = step.args.get("focus") or {}
            if want.get("title_contains"):
                w.title = want["title_contains"]
            if want.get("app"):
                w.app = want["app"]
            if want.get("url_contains"):
                w.url = want["url_contains"]
            w.touch()
            return {"detail": "focused"}
        if step.kind in (ActionKind.CLICK, ActionKind.DOUBLE_CLICK, ActionKind.UI_INVOKE):
            el = w.find(step.target.role, step.target.name)
            if el is None:
                raise RuntimeError(f"no element {label}")
            if el.on_click:
                el.on_click(w)
            w.log.append(("click", label))
            w.touch()
            return {"detail": f"clicked {label}"}
        if step.kind is ActionKind.TYPE:
            el = w.find(step.target.role, step.target.name)
            if el is None:
                raise RuntimeError(f"no element {label}")
            if el.on_type:
                el.on_type(w, step.text)
            else:
                el.text = step.text
            w.log.append(("type", label))
            w.touch()
            return {"detail": f"typed into {label}"}
        if step.kind is ActionKind.BROWSER and step.args.get("op") == "navigate":
            w.url = str(step.args.get("url"))
            w.log.append(("navigate", w.url))
            w.touch()
            return {"detail": "navigated"}
        if step.kind in (ActionKind.WAIT, ActionKind.NOOP, ActionKind.TAKE_SCREENSHOT):
            return {"detail": step.kind.value.lower()}
        if step.kind is ActionKind.APP_LAUNCH:
            w.app = str(step.args.get("app") or w.app)
            w.touch()
            return {"detail": "launched"}
        raise RuntimeError(f"unsupported {step.kind}")


class ScriptedPlanner:
    """Returns the scripted plan; replan returns the remaining steps unless a
    recovery script is provided (failure substring -> steps)."""

    def __init__(self, steps: list[PlanStep], recovery: dict[str, list[PlanStep]] | None = None,
                 goal: str = "goal") -> None:
        self.steps, self.recovery, self.goal = list(steps), dict(recovery or {}), goal
        self.plans = 0
        self.replans: list[str] = []

    def plan(self, task: Any, view: Any) -> Plan:
        self.plans += 1
        return Plan(goal=self.goal, steps=list(self.steps))

    def replan(self, task: Any, view: Any, failure: str, remaining: list[PlanStep]) -> Plan:
        self.replans.append(failure)
        for key, steps in self.recovery.items():
            if key in failure:
                return Plan(goal=self.goal, steps=list(steps), source="recovery")
        return Plan(goal=self.goal, steps=list(remaining), source="recovery")
