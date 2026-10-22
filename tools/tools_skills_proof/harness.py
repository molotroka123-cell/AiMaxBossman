"""In-process lab: a real Bossman app (temp SQLite), the real engine tool loop, a SCRIPTED model.

The model is a double on purpose: what is proven is the tool path (permission decision ->
approval -> handler -> result returned to the model), not the intelligence of any LLM. The
tool handlers, the registry, the permission algebra, the approvals queue and the engine are
the product's own code.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import sqlalchemy as sa

from .common import REPO_ROOT, add_repo_paths, assert_isolated

add_repo_paths()

from bcc.api import create_app  # noqa: E402
from bcc.auth import HEADER  # noqa: E402
from bcc.config import Settings  # noqa: E402
from bcc.db import approvals as approvals_t, settings_kv, tasks as tasks_t, tool_calls as tool_calls_t  # noqa: E402
from bcc.providers import ChatResult, Health, ToolCall  # noqa: E402
from bcc.tools import REGISTRY, ToolResult, to_api_name  # noqa: E402

FINISHED = ("completed", "failed", "stopped")
SETTLED = FINISHED + ("waiting_approval",)


class ScriptedModel:
    """Model double. ``steps``: ("tool", name, args|callable(history)->args), ("tools", [(name,args),..]),
    ("text", answer). The last step repeats. ``history`` = list of tool-message contents so far."""

    def __init__(self, steps: list[tuple]):
        self.steps = list(steps)
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[Any] = []

    def history(self) -> list[str]:
        if not self.seen_messages:
            return []
        return [str(m.get("content") or "") for m in self.seen_messages[-1] if m.get("role") == "tool"]

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_tools.append(kw.get("tools"))
        step = self.steps[min(self.calls - 1, len(self.steps) - 1)]
        hist = [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]
        if step[0] in ("tool", "tools"):
            pairs = [(step[1], step[2])] if step[0] == "tool" else list(step[1])
            tcs = []
            for i, (name, args) in enumerate(pairs):
                a = args(hist) if callable(args) else args
                tcs.append(ToolCall(id=f"call_{self.calls}_{i}", name=to_api_name(name), arguments=a,
                                    raw_arguments=json.dumps(a, ensure_ascii=False)))
            return ChatResult(text="", tokens_in=5, tokens_out=2, finish="tool_calls", model=model, tool_calls=tcs)
        return ChatResult(text=step[1], tokens_in=5, tokens_out=3, model=model)

    async def health(self) -> Health:
        return Health(status="ok", latency_ms=1)

    async def list_models(self) -> list[str]:
        return ["proof-model"]


@dataclass
class Wait:
    """One moment the run was parked for the owner."""
    approval: dict
    tool_rows: list[dict]
    decision: bool | None = None


@dataclass
class Trace:
    task_id: int
    status: str
    model: ScriptedModel
    tool_rows: list[dict] = field(default_factory=list)
    waits: list[Wait] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)

    def tool_messages(self) -> list[str]:
        """Contents of the tool messages the model received, in order (last model turn)."""
        return self.model.history()

    def last_tool(self) -> str:
        t = self.tool_messages()
        return t[-1] if t else ""

    def rows_for(self, tool: str) -> list[dict]:
        return [r for r in self.tool_rows if r["tool"] == tool]


class Lab:
    def __init__(self, root: Path):
        assert_isolated()
        self.root = Path(root)
        self.data = self.root / "bcc-data"
        self.settings = Settings(data_dir=self.data,
                                 database_url=f"sqlite+aiosqlite:///{self.data / 'bcc.db'}",
                                 ui_dir=self.root / "no-ui")
        self.app = None
        self.svc = None
        self.client: httpx.AsyncClient | None = None
        self._provider: dict | None = None
        self._model: dict | None = None
        self._n = 0

    async def __aenter__(self) -> "Lab":
        self.app = create_app(self.settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        await self.svc.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                                        headers={HEADER: self.svc.auth.token}, timeout=60.0)
        r = await self.client.post("/api/providers", json={
            "name": "proof-local", "kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "sk-test-abcd"})
        self._provider = r.json()
        r = await self.client.post("/api/models", json={
            "provider_id": self._provider["id"], "name": "proof-model", "alias": "proof-model"})
        self._model = r.json()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.client is not None:
            await self.client.aclose()
        if self.svc is not None:
            await self.svc.stop()

    # ---------------------------------------------------------------- setup helpers

    async def agent(self, tools: list[str], permissions: dict | None = None, *, max_steps: int = 14,
                    name: str | None = None) -> dict:
        self._n += 1
        r = await self.client.post("/api/agents", json={
            "name": name or f"proof-agent-{self._n}", "system_prompt": "proof",
            "model_id": self._model["id"], "max_steps": max_steps,
            "tools": tools, "permissions": permissions or {}})
        assert r.status_code < 300, r.text
        return r.json()

    async def set_kv(self, key: str, value: Any) -> None:
        enc = self.svc.vault.encrypt(json.dumps(value, ensure_ascii=False))
        async with self.svc.db.session() as s:
            await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == key))
            await s.execute(sa.insert(settings_kv).values(key=key, value_enc=enc))
            await s.commit()

    async def terminal_roots(self, *paths: Path) -> None:
        await self.set_kv("terminal.roots", [str(p) for p in paths])

    async def memory_vault(self, root: Path, **extra: Any) -> None:
        from bcc.features.tools_memory import save_config
        await save_config(self.svc, {"root": str(root), "backend": "sqlite", **extra})

    # ---------------------------------------------------------------- running

    async def _status(self, task_id: int) -> str:
        async with self.svc.db.session() as s:
            return (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).scalar_one()

    async def _rows(self, task_id: int) -> list[dict]:
        async with self.svc.db.session() as s:
            res = (await s.execute(sa.select(tool_calls_t).where(tool_calls_t.c.task_id == task_id)
                                   .order_by(tool_calls_t.c.id))).fetchall()
        return [dict(r._mapping) for r in res]

    async def _pending_approvals(self, task_id: int) -> list[dict]:
        async with self.svc.db.session() as s:
            res = (await s.execute(sa.select(approvals_t).where(
                approvals_t.c.task_id == task_id, approvals_t.c.status == "pending"))).fetchall()
        return [dict(r._mapping) for r in res]

    async def _all_approvals(self, task_id: int) -> list[dict]:
        async with self.svc.db.session() as s:
            res = (await s.execute(sa.select(approvals_t).where(approvals_t.c.task_id == task_id))).fetchall()
        return [dict(r._mapping) for r in res]

    async def _until(self, task_id: int, states: tuple[str, ...], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = await self._status(task_id)
            if st in states:
                # a freshly parked task can flip for a moment; settle
                await asyncio.sleep(0.05)
                if await self._status(task_id) in states:
                    return st
            await asyncio.sleep(0.03)
        raise TimeoutError(f"task {task_id} stayed {await self._status(task_id)} after {timeout}s")

    async def run(self, agent: dict, steps: list[tuple], *, decide: Callable[[Wait], Awaitable[bool] | bool] | bool | None = None,
                  prompt: str = "proof", timeout: float = 40.0, workspace: str = "") -> Trace:
        """Create a task for ``agent``, drive the real engine until it finishes. When the run parks for an
        approval, ``decide`` (bool or callable(Wait)->bool) answers it; ``None`` leaves it parked and returns."""
        model = ScriptedModel(steps)
        self.svc.registry.adapter_factory = lambda m, p: model
        body = {"title": "proof", "prompt": prompt, "agent_id": agent["id"], "run_now": True, "max_retries": 0}
        r = await self.client.post("/api/tasks", json=body)
        assert r.status_code < 300, r.text
        task_id = r.json()["task"]["id"]
        if workspace:
            async with self.svc.db.session() as s:
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(workspace_path=workspace))
                await s.commit()
        self.svc.engine.poll_interval = 0.02
        worker = asyncio.create_task(self.svc.engine.worker_loop())
        watcher = asyncio.create_task(self.svc.engine.approval_watcher())
        trace = Trace(task_id=task_id, status="", model=model)
        try:
            while True:
                st = await self._until(task_id, SETTLED, timeout)
                if st != "waiting_approval":
                    break
                pend = await self._pending_approvals(task_id)
                if not pend:           # parked between rows; give it a moment
                    await asyncio.sleep(0.1)
                    pend = await self._pending_approvals(task_id)
                    if not pend:
                        raise RuntimeError("waiting_approval without a pending approval row")
                wait = Wait(approval=pend[0], tool_rows=await self._rows(task_id))
                trace.waits.append(wait)
                if decide is None:
                    break
                verdict = decide(wait) if callable(decide) else decide
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                wait.decision = bool(verdict)
                rr = await self.client.post(f"/api/approvals/{pend[0]['id']}", json={"approve": bool(verdict), "by": "proof-owner"})
                assert rr.status_code < 300, rr.text
                await asyncio.sleep(0.05)
        finally:
            worker.cancel()
            watcher.cancel()
            await asyncio.gather(worker, watcher, return_exceptions=True)
        trace.status = await self._status(task_id)
        trace.tool_rows = await self._rows(task_id)
        trace.approvals = await self._all_approvals(task_id)
        return trace


# ---------------------------------------------------------------- stubs / canaries

class Canary:
    """Replaces a registered tool's handler with a recorder. The spec (name, permission, category,
    default_effect, effect_hook, normalize_args, context_deny, bind_args, schema) is the REAL one."""

    def __init__(self, tool: str, reply: str = "CANARY-EXECUTED"):
        self.tool = tool
        self.calls: list[dict] = []
        self._orig = REGISTRY.get(tool)
        if self._orig is None:
            raise KeyError(tool)
        self._reply = reply

    async def _handler(self, args: dict, ctx: Any) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(content=self._reply, one_line=f"{self.tool}: canary")

    def __enter__(self) -> "Canary":
        REGISTRY._tools[self.tool] = dataclasses.replace(self._orig, handler=self._handler)
        return self

    def __exit__(self, *exc: Any) -> None:
        REGISTRY._tools[self.tool] = self._orig


def decide_effect_for(tool: str, args: dict, permissions: dict | None) -> tuple[str, str]:
    """The product's own decide_effect for a synthetic agent holding ``permissions``."""
    from bcc.tools import decide_effect
    spec = REGISTRY.get(tool)
    return decide_effect(spec, args, {"permissions": permissions or {}, "tools": [tool]})


# ---------------------------------------------------------------- matrix rows

def tool_row(name: str, status: str, evidence: str, caveat: str = "", live_note: str = "", **details: Any):
    """Row for ``name`` with group/category/permission/default effect read from the live REGISTRY."""
    from bcc.features.agent_opencode import classify_tool_group
    from .common import Row
    spec = REGISTRY.get(name)
    return Row(kind="tool", id=name, status=status,
               group=classify_tool_group(name, spec.source) if spec else "",
               category=spec.category if spec else "", permission=spec.permission if spec else "",
               effect=spec.default_effect if spec else "", evidence=evidence, caveat=caveat,
               live_note=live_note, details=details)


def gate_ok(trace: "Trace", tool: str, canary_calls: list, *, expect: str = "ask") -> list[str]:
    """Generic assertions for a gate probe; returns a list of problems (empty = gate behaved)."""
    problems: list[str] = []
    rows = [r for r in trace.tool_rows if r["tool"] == tool]
    if expect == "ask":
        if not trace.waits:
            problems.append("run never parked for approval")
        else:
            w = trace.waits[0]
            if tool not in (w.approval.get("preview") or ""):
                problems.append("approval preview does not name the tool")
            if not any(r["status"] == "pending_approval" and r["effect"] == "ask" for r in w.tool_rows):
                problems.append("no pending_approval/ask tool_calls row while parked")
    elif expect == "deny":
        if trace.waits:
            problems.append("deny tool asked for approval")
        if not any(r["status"] == "denied" for r in rows):
            problems.append("no denied row")
    return problems
