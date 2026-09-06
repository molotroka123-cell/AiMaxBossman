"""Measure what one desktop-operator step actually costs.

Scope, stated before any number: this profiles the *framework* — the real
``ComputerOperatorManager`` loop, its store writes, its policy/verifier/loop-guard
work and how many observations it asks for. Observation, planning and action are
driven by stubs whose cost the caller declares, because the true cost of a UIA
walk, a screenshot and a model turn is a property of the owner's host and model,
not of this repository. A PASS here is not human-level computer use and does not
authorize anything; see docs/testing/HUMAN_SPEED_AUDIT_20260906.md.

What it answers:

* how many observations the loop spends per verified action (2.0 before the
  reuse window, 1.0 with it) — the single largest avoidable item on a Windows
  host, where one observation is a UIA descendant walk plus a full-screen PNG;
* how much wall time the loop adds on top of the declared costs;
* p50/p95 per step, with every sample retained.

Run:

    python tools/operator_step_profile.py --steps 40 --observe-ms 120 --plan-ms 250

``--observe-ms`` etc. are the owner's measured costs; supply real ones to get a
prediction for the real host. With all costs at 0 the output is pure framework
overhead.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "bossman-core"
for path in (str(ROOT), str(CORE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bossman.computer_operator.models import (ActionKind, ComputerAction,  # noqa: E402
                                              ExpectedState, Observation, TaskState, new_id)
from bossman.computer_operator.wiring import make_manager  # noqa: E402


async def _spend(seconds: float) -> None:
    """Burn a declared cost without pretending it is free.

    ``asyncio.sleep`` would be measuring the event loop's timer, not the loop's
    own work, so a zero cost yields one clean scheduling point and a non-zero
    cost is a real wait — which is what an I/O-bound UIA/screenshot call is.
    """
    if seconds > 0:
        await asyncio.sleep(seconds)
    else:
        await asyncio.sleep(0)


class CostedObserver:
    def __init__(self, cost_s: float):
        self.cost_s = cost_s
        self.calls = 0

    async def observe(self, *, generation: int) -> Observation:
        self.calls += 1
        await _spend(self.cost_s)
        # The screen must actually change between steps: an unchanging state is
        # exactly what LoopGuard stops, and profiling a guarded no-op would be
        # measuring the wrong loop.
        return Observation(new_id("obs"), time.time(),
                           {"title": f"Editor - row {self.calls}", "app": "editor"},
                           f"ok row {self.calls}", {"elements": [{"name": f"row-{self.calls}"}]},
                           None, False, generation)


class CostedPlanner:
    def __init__(self, cost_s: float, steps: int):
        self.cost_s = cost_s
        self.remaining = steps
        self.calls = 0

    async def next_action(self, *, goal, observation_summary, foreground, ui_tree,
                          last_result, remaining_steps):
        self.calls += 1
        await _spend(self.cost_s)
        if self.remaining <= 0:
            # AT-01: COMPLETE обязан нести проверяемое постусловие, как и в
            # проде; наблюдение профиля всегда содержит "ok row N".
            return ComputerAction.make(ActionKind.COMPLETE,
                                       expected=ExpectedState(contains_text="ok"))
        self.remaining -= 1
        return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"),
                                   args={"x": 10, "y": 10 + self.calls,
                                         "semantic": f"button:row-{self.calls}"})


class CostedAdapter:
    name = "costed"

    def __init__(self, cost_s: float):
        self.cost_s = cost_s
        self.executed = 0

    async def supports(self, a, o):
        return True

    async def execute(self, a, o):
        self.executed += 1
        await _spend(self.cost_s)
        return self.name


def percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * p / 100) - 1]


async def profile(*, steps: int, observe_ms: float, plan_ms: float, act_ms: float,
                  reuse_max_age_s: float) -> dict:
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be a positive integer")
    observer = CostedObserver(observe_ms / 1000)
    planner = CostedPlanner(plan_ms / 1000, steps)
    adapter = CostedAdapter(act_ms / 1000)
    per_step: list[float] = []

    def emit(*a, **kw):
        if kw.get("event") == "step_verified":
            per_step.append(time.perf_counter())

    with tempfile.TemporaryDirectory() as tmp:
        mgr = make_manager(Path(tmp) / "tasks.json", planner, observer, adapter=adapter,
                           event_emit=emit, observation_reuse_max_age_s=reuse_max_age_s)
        task = mgr.create_task("profile the operator step")
        started = time.perf_counter()
        state = await mgr.run(task.id)
        elapsed = time.perf_counter() - started

    if state is not TaskState.COMPLETED or adapter.executed != steps:
        raise RuntimeError(f"profile run did not complete cleanly: {state} after {adapter.executed} actions")

    marks = [started] + per_step
    step_ms = [(marks[i + 1] - marks[i]) * 1000 for i in range(len(marks) - 1)]
    declared_ms = observer.calls * observe_ms + planner.calls * plan_ms + adapter.executed * act_ms
    total_ms = elapsed * 1000
    return {
        "steps": adapter.executed,
        "observations": observer.calls,
        "observations_reused": mgr.observations_reused,
        "observations_per_verified_action": round(observer.calls / adapter.executed, 4),
        "planner_calls": planner.calls,
        "declared_costs_ms": {"observe": observe_ms, "plan": plan_ms, "act": act_ms},
        "declared_total_ms": round(declared_ms, 3),
        "wall_total_ms": round(total_ms, 3),
        "framework_overhead_ms": round(total_ms - declared_ms, 3),
        "framework_overhead_per_step_ms": round((total_ms - declared_ms) / adapter.executed, 4),
        "p50_step_ms": round(percentile(step_ms, 50), 3),
        "p95_step_ms": round(percentile(step_ms, 95), 3),
        "max_step_ms": round(max(step_ms), 3),
        "samples_ms": [round(x, 3) for x in step_ms],
        "outliers_removed": 0,
        "scope": "framework_only: observation, planning and action costs are declared, not measured here",
        "human_comparison": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--observe-ms", type=float, default=0.0,
                        help="measured cost of one observation on the target host")
    parser.add_argument("--plan-ms", type=float, default=0.0, help="measured planner turn")
    parser.add_argument("--act-ms", type=float, default=0.0, help="measured input dispatch")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be at least 1")

    common = dict(steps=args.steps, observe_ms=args.observe_ms, plan_ms=args.plan_ms,
                  act_ms=args.act_ms)
    report = {
        "with_observation_reuse": asyncio.run(profile(**common, reuse_max_age_s=0.75)),
        "without_observation_reuse": asyncio.run(profile(**common, reuse_max_age_s=0.0)),
    }
    a, b = report["with_observation_reuse"], report["without_observation_reuse"]
    report["observation_calls_saved"] = b["observations"] - a["observations"]
    report["predicted_wall_saved_ms"] = round(
        (b["observations"] - a["observations"]) * args.observe_ms, 3)
    text = json.dumps(report, indent=2, allow_nan=False)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
