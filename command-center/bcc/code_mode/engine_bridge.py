"""Engine side of code mode: drives `bossman.run` through the ordinary tool pipeline.

Contract (what makes the facade unable to widen authority):

  1. Every `tools.x(...)` call in the sandbox becomes a real engine tool call with its own id
     (`<outer-id>~<n>`) and goes through `TaskEngine._execute_tool_calls`: grant check, malformed
     args, decide_effect + owner rules, context_deny, argument binding, approval leases, journal
     row, fence, write-ahead for non-idempotent tools. Nothing here re-implements policy.
  2. The sandbox cannot be parked (an approval can take hours, a restart can intervene). When a
     call would need the owner (ASK, or an ambiguous prior effect), `facade_probe` stops the
     pipeline before it creates anything, the sandbox is killed, and the SAME call is then handed
     to the pipeline as a normal, separate tool call appended to the assistant message. The owner
     is asked about that one call - its tool, its canonical arguments, its digest - never about
     the code. After the decision the model simply continues.
  3. Owner STOP is checked before every inner call and cancellation kills the child.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..tools import ToolContext
from .facade import FACADE_NAMES, build_catalog, render_run_result
from .sandbox import AbortRun, SandboxLimits, run_sandboxed

MAX_CODE_PREVIEW = 600


class FacadeProbe:
    """Set by the pipeline instead of parking when an inner call needs the owner."""

    __slots__ = ("ask",)

    def __init__(self) -> None:
        self.ask: tuple[Any, Any] | None = None     # (ToolCall, ToolSpec)


def _args_preview(args: dict) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= 300 else text[:300] + "..."


def _assistant_message_of(messages: list[dict], call_id: str) -> dict | None:
    for message in reversed(messages):
        if message.get("role") == "assistant" and any(
                str(tc.get("id")) == str(call_id) for tc in message.get("tool_calls") or []):
            return message
    return None


async def run_code_step(engine: Any, *, run_id: int, task: dict, agent: dict, messages: list[dict],
                        call: Any, spec: Any, step: int, rest: list[Any], policy_rules: list[dict],
                        tool_specs: list[Any], usage: dict, limits: SandboxLimits | None = None
                        ) -> tuple[bool, bool]:
    """Execute one `bossman.run` call -> (waiting, took_rest).

    `waiting`: the run must wait for a human (state is parked). `took_rest`: the calls after this
    one (`rest`) were already handed to the pipeline here, so the caller must not run them again."""
    from .. import engine as eng
    from ..providers import ToolCall

    await engine.assert_fence(run_id)
    inner_specs = [t for t in tool_specs if t.name not in FACADE_NAMES]
    by_api = {t.api_name: t for t in inner_specs}
    ctx = ToolContext(svc=engine.services, task=task, run_id=run_id, agent=agent, step=step,
                      workspace=str(task.get("workspace_path") or ""), call_id=str(call.id))
    catalog = await build_catalog(ctx, inner_specs, policy_rules)
    py_to_api = catalog.py_names()
    api_to_py = {api: py for py, api in py_to_api.items()}
    code = call.arguments.get("code")
    code = code if isinstance(code, str) else ""

    await engine._emit_stream("run.tool_use", task_id=task["id"], run_id=run_id, step=step,
                              call_id=str(call.id), tool=spec.name, source=spec.source,
                              args={"code": eng._ps_redact_text(code[:MAX_CODE_PREVIEW])})
    started = time.monotonic()
    log: list[dict] = []
    probe = FacadeProbe()
    counter = 0

    async def host_call(api_name: str, args: dict) -> dict:
        nonlocal counter
        if await engine._task_status(task["id"]) in eng.STOPPED_TASK_STATUSES:
            raise AbortRun("owner stop", "stop")
        inner_spec = by_api.get(api_name)
        if inner_spec is None or api_name not in api_to_py:
            return {"ok": False, "content": f"tool {api_name} is not available", "status": "denied"}
        counter += 1
        inner = ToolCall(id=f"{call.id}~{counter}", name=api_name, arguments=dict(args),
                         raw_arguments=json.dumps(args, ensure_ascii=False))
        sink: list[dict] = []
        probe.ask = None
        waiting = await engine._execute_tool_calls(
            run_id, task, agent, sink, [inner], step, policy_rules, inner_specs,
            usage={}, facade_probe=probe)
        if probe.ask is not None:
            raise AbortRun("owner approval needed", "ask")
        if waiting:
            raise AbortRun("run is waiting for a human decision", "other")
        status = await engine._tool_call_status(run_id, inner.id)
        text = str(sink[-1].get("content") if sink else "")
        log.append({"n": counter, "tool": api_to_py[api_name], "status": status})
        return {"ok": status == "executed", "content": text, "status": status}

    outcome = await run_sandboxed(code, py_to_api, host_call, limits or SandboxLimits())

    ask: dict | None = None
    synth = None
    if outcome.status == "aborted" and outcome.abort_kind == "ask" and probe.ask is not None:
        ask_call, ask_spec = probe.ask
        synth = ToolCall(id=f"{call.id}~ask{counter}", name=ask_call.name,
                         arguments=dict(ask_call.arguments),
                         raw_arguments=json.dumps(ask_call.arguments, ensure_ascii=False))
        ask = {"tool": ask_spec.name, "args_preview": _args_preview(ask_call.arguments)}

    content = render_run_result(outcome, log, ask)
    duration = int((time.monotonic() - started) * 1000)
    failed = outcome.status in ("error", "rejected", "timeout", "violation")
    await engine._record_tool_call(
        run_id, task["id"], step, call, spec, effect="auto",
        status="error" if failed else "executed",
        preview=eng._ps_redact_text(content[:500]), duration_ms=duration,
        error=eng._ps_redact_text(content[:500]) if failed else None)
    messages.append(eng._tool_message(call, eng._ps_redact_text(content)))
    await engine._log(run_id, "warn" if failed else "info", "tool.error" if failed else "tool.result",
                      f"{spec.name}: {outcome.status}, вызовов {len(log)} ({duration} мс)")
    await engine.bus.emit("tool.called", task_id=task["id"], run_id=run_id, tool=spec.name,
                          source=spec.source, ok=not failed, duration_ms=duration)
    await engine._emit_stream("run.tool_result", task_id=task["id"], run_id=run_id, step=step,
                              call_id=str(call.id), tool=spec.name, ok=not failed, duration_ms=duration,
                              summary=f"{outcome.status}; calls={len(log)}",
                              preview=eng._ps_redact_text(content[:eng.STREAM_PREVIEW_CHARS]),
                              truncated=len(content) > eng.STREAM_PREVIEW_CHARS)

    if outcome.status == "aborted" and outcome.abort_kind == "stop":
        return False, False          # the engine's interrupt check ends the run on the next loop turn
    if synth is None:
        return False, False

    # Hand the exact call that needs the owner to the ordinary pipeline, as a separate tool call
    # of the same assistant message (so every tool_call id has its tool message).
    carrier = _assistant_message_of(messages, str(call.id))
    if carrier is not None:
        carrier["tool_calls"].append({
            "id": synth.id, "type": "function",
            "function": {"name": synth.name, "arguments": synth.raw_arguments}})
    waiting = await engine._execute_tool_calls(
        run_id, task, agent, messages, [synth, *rest], step, policy_rules, tool_specs, usage=usage)
    return waiting, True
