"""In-process controller interrupt timing. No OS hook/UI/human attestation."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

from bossman_v3.contracts import TypedAction
from bossman_v3.visual_state.models import StateFragment, StateIdentity
from bossman_v3.visual_state.reaction import ReactionCoordinator


def test_controller_interrupt_invalidation_latency(record_property):
    samples = []
    state = {"element": "save", "enabled": True, "focused": True, "modal": None}
    identity = StateIdentity("fixture-editor", "window-1", "doc-1", 1, 1)
    for _ in range(100):
        controller = ReactionCoordinator(enabled=True)
        now = datetime.now(timezone.utc)
        fragment = StateFragment("a11y", now, state, "local-fixture", identity=identity)
        assert controller.observe([fragment], capture_sequence=1,
                                  captured_monotonic=time.monotonic(), now=now)
        controller.queue_action(TypedAction("editor.click", {"element": "save"}),
                                required_state=state, timeout_seconds=1, now=now)
        start = time.perf_counter_ns()
        controller.interrupt()
        result = controller.poll(now=now)
        samples.append((time.perf_counter_ns() - start) / 1e6)
        assert result.reason == "owner_interrupted"
        assert not result.ready_for_canonical_gate and not result.dispatch_authorized
    ordered = sorted(samples)
    p95, p99 = ordered[math.ceil(len(ordered) * .95) - 1], ordered[math.ceil(len(ordered) * .99) - 1]
    report = {"tier": "LOCAL_COMPONENT", "scope": "interrupt_to_invalidation",
              "n": len(samples), "p95_ms": p95, "p99_ms": p99,
              "max_ms": max(samples), "samples_ms": samples,
              "os_hook_latency": "NOT_RUN", "visible_input_ack": "NOT_RUN",
              "n0_activation_authorized": False}
    record_property("controller_interrupt", json.dumps(report, allow_nan=False))
    directory = os.getenv("BOSSMAN_SPEED_RESULTS")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "controller_interrupt.json").open("x", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
    assert p95 < 50.0 and p99 < 100.0, report
