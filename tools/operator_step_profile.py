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
        self.identity_calls = 0

    #: Window/document identity. Stable across the run, as a real editor window
    #: is: what changes between steps is the CONTENT, which is what the verifier
    #: already confirms. Without this the reuse window is refused outright
    #: (AT-03 fail-closed), and the profile would silently measure the
    #: no-reuse path.
    IDENTITY = {"app": "editor", "handle": 4242}

    async def identity(self):
        self.identity_calls += 1
        await _spend(0)
        return dict(self.IDENTITY)

    async def observe(self, *, generation: int) -> Observation:
        self.calls += 1
        await _spend(self.cost_s)
        # The screen must actually change between steps: an unchanging state is
        # exactly what LoopGuard stops, and profiling a guarded no-op would be
        # measuring the wrong loop.
        return Observation(new_id("obs"), time.time(),
                           {**self.IDENTITY, "title": f"Editor - row {self.calls}"},
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
            return ComputerAction.make(ActionKind.COMPLETE)
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
        "reuse_rejected_by_identity": mgr.reuse_rejected_by_identity,
        "identity_probes": observer.identity_calls,
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
        "phases": mgr.phase_report(),
        "scope": "framework_only: observation, planning and action costs are declared, not measured here",
        "human_comparison": "NOT_RUN",
    }


class BlockingObserver(CostedObserver):
    """Holds the observation open so a stop can land inside it."""

    def __init__(self, hold_s: float):
        super().__init__(0.0)
        self.hold_s = hold_s
        self.entered = asyncio.Event()

    async def observe(self, *, generation: int):
        self.entered.set()
        await asyncio.sleep(self.hold_s)
        return await super().observe(generation=generation)


async def stop_latency(*, trials: int, hold_s: float) -> dict:
    """Measure owner-stop acknowledgement and dispatch prevention.

    The stop lands while the loop is inside an observation that would otherwise
    run for ``hold_s``. What is measured is the time from the owner's stop call
    to the loop having stopped — not a component microbenchmark: the loop really
    abandons the read, refuses the dispatch and writes the owner's verdict.
    """
    ack_ms: list[float] = []
    dispatched = 0
    for _ in range(trials):
        observer = BlockingObserver(hold_s)
        adapter = CostedAdapter(0.0)
        with tempfile.TemporaryDirectory() as tmp:
            mgr = make_manager(Path(tmp) / "tasks.json", CostedPlanner(0.0, 4), observer,
                               adapter=adapter, event_emit=lambda *a, **k: None)
            task = mgr.create_task("stop latency probe")
            run = asyncio.ensure_future(mgr.run(task.id))
            deadline = time.perf_counter() + 5
            while not observer.entered.is_set() and time.perf_counter() < deadline:
                await asyncio.sleep(0.001)
            started = time.perf_counter()
            mgr.stop(task.id)
            state = await asyncio.wait_for(run, hold_s + 5)
            ack_ms.append((time.perf_counter() - started) * 1000)
            dispatched += adapter.executed
            if state is not TaskState.CANCELLED:
                raise RuntimeError(f"stop did not cancel the task: {state}")
    return {
        "trials": trials,
        "held_observation_ms": hold_s * 1000,
        "p50_ack_ms": round(percentile(ack_ms, 50), 3),
        "p95_ack_ms": round(percentile(ack_ms, 95), 3),
        "max_ack_ms": round(max(ack_ms), 3),
        "samples_ms": [round(x, 3) for x in ack_ms],
        "outliers_removed": 0,
        "dispatches_after_stop": dispatched,
        "target_ack_p95_ms": 200.0,
        "target_dispatch_prevention_p95_ms": 250.0,
        "ack_within_target": percentile(ack_ms, 95) < 200.0,
        "dispatch_prevention_within_target": dispatched == 0 and percentile(ack_ms, 95) < 250.0,
        "scope": "owner stop received -> loop stopped; excludes UI transport and OS input hook",
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
    parser.add_argument("--stop-trials", type=int, default=20,
                        help="owner-stop acknowledgement trials (0 disables)")
    parser.add_argument("--stop-hold-ms", type=float, default=3000.0,
                        help="how long the observation the stop interrupts would have taken")
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be at least 1")

    common = dict(steps=args.steps, observe_ms=args.observe_ms, plan_ms=args.plan_ms,
                  act_ms=args.act_ms)
    report = {
        "with_observation_reuse": asyncio.run(profile(**common, reuse_max_age_s=0.75)),
        "without_observation_reuse": asyncio.run(profile(**common, reuse_max_age_s=0.0)),
    }
    if args.stop_trials > 0:
        report["owner_stop"] = asyncio.run(stop_latency(trials=args.stop_trials,
                                                        hold_s=args.stop_hold_ms / 1000))
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
