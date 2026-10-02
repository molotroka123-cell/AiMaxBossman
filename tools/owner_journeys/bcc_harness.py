"""In-process Bossman Command Center (bcc) harness for owner journeys and the learning lab.

Drives the REAL product path — FastAPI app, SQLite store, TaskEngine worker
loop, tool policy (auto/ask/deny), approvals API and the audit tables — inside
one Python process on a FRESH data dir. No port is opened (ASGI transport).

Only two things are harness-side:
* domain tools are registered as ``ToolSpec`` objects (there is no SwapMe /
  Fresh Vibes module in the product yet);
* ``LocalOllamaAdapter`` adds ``reasoning_effort: "none"`` to Ollama's
  OpenAI-compatible request so Qwen runs with thinking off (the product
  adapter has no such knob — reported as a finding).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "command-center", ROOT / "bossman-core", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from bcc.api import create_app  # noqa: E402
from bcc.auth import HEADER  # noqa: E402
from bcc.config import Settings  # noqa: E402
from bcc.db import approvals as approvals_t, tasks as tasks_t, tool_calls as tool_calls_t  # noqa: E402
from bcc.providers import OpenAICompatAdapter  # noqa: E402
from bcc.tools import REGISTRY, ToolSpec  # noqa: E402

from tools.owner_journeys.runtime_guard import wait_if_paused  # noqa: E402

OLLAMA_V1 = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
FINAL = ("completed", "failed", "stopped", "blocked")


class LocalOllamaAdapter(OpenAICompatAdapter):
    """Product OpenAI-compatible adapter + thinking off + PAUSE check per model call."""

    async def _request(self, method: str, url: str, *, timeout: float, headers: dict | None = None,
                       json: dict | None = None):
        if json is not None and url.endswith("/chat/completions"):
            await asyncio.to_thread(wait_if_paused)
            json = {**json, "reasoning_effort": "none"}
            if "temperature" not in json:
                json["temperature"] = 0
        return await super()._request(method, url, timeout=timeout, headers=headers, json=json)


@dataclass
class TaskOutcome:
    task_id: int
    status: str
    result: str
    tool_calls: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    seconds: float


ApprovalPolicy = Callable[[dict[str, Any]], Awaitable[bool] | bool]


class BccHarness:
    def __init__(self, data_dir: Path, *, adapter_factory: Optional[Callable[[dict, dict], Any]] = None):
        self.data_dir = Path(data_dir)
        self.adapter_factory = adapter_factory
        self.app = self.svc = self.client = None
        self._bg: list[asyncio.Task] = []
        self._registered: list[str] = []

    async def __aenter__(self) -> "BccHarness":
        self.data_dir.mkdir(parents=True, exist_ok=True)
        settings = Settings(data_dir=self.data_dir,
                            database_url=f"sqlite+aiosqlite:///{(self.data_dir / 'bcc.db').as_posix()}",
                            ui_dir=self.data_dir / "no-ui")
        self.app = create_app(settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        await self.svc.start()
        if self.adapter_factory is not None:
            self.svc.registry.adapter_factory = self.adapter_factory
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://bcc",
                                        headers={HEADER: self.svc.auth.token}, timeout=60)
        self.svc.engine.poll_interval = 0.05
        self._bg = [asyncio.create_task(self.svc.engine.worker_loop()),
                    asyncio.create_task(self.svc.engine.approval_watcher())]
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc) -> None:
        for t in self._bg:
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        for name in self._registered:
            REGISTRY.unregister(name)
        if self.client is not None:
            await self.client.aclose()
        if self.svc is not None:
            await self.svc.stop()

    # ------------------------------------------------------------ setup
    def register(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            REGISTRY.register(spec)
            self._registered.append(spec.name)

    async def agent(self, *, name: str, system_prompt: str, tools: list[str], model_name: str = DEFAULT_MODEL,
                    base_url: str = OLLAMA_V1, max_steps: int = 12, api_key: str = "local-no-key",
                    price_in: float | None = None, price_out: float | None = None) -> dict[str, Any]:
        r = await self.client.post("/api/providers", json={"name": f"provider-{name}", "kind": "openai_compat",
                                                           "base_url": base_url, "api_key": api_key})
        r.raise_for_status()
        provider = r.json()
        body = {"provider_id": provider["id"], "name": model_name, "alias": f"{name}-model"}
        if price_in is not None and price_out is not None:
            body.update(price_in=price_in, price_out=price_out)
        r = await self.client.post("/api/models", json=body)
        r.raise_for_status()
        model = r.json()
        r = await self.client.post("/api/agents", json={"name": name, "system_prompt": system_prompt,
                                                        "model_id": model["id"], "max_steps": max_steps})
        r.raise_for_status()
        agent = r.json()
        r = await self.client.patch(f"/api/agents/{agent['id']}", json={"tools": tools})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------ run
    async def run_task(self, *, agent_id: int, title: str, prompt: str, allowed_tools: list[str],
                       approve: Optional[ApprovalPolicy] = None, timeout: float = 600.0,
                       max_approvals: int = 6, meta: Optional[dict[str, Any]] = None) -> TaskOutcome:
        started = time.time()
        r = await self.client.post("/api/tasks", json={"title": title, "prompt": prompt, "agent_id": agent_id,
                                                       "run_now": False, "max_retries": 0})
        r.raise_for_status()
        task_id = int(r.json()["task"]["id"])
        await self._merge_meta(task_id, {"allowed_tools": list(allowed_tools), **(meta or {})})
        r = await self.client.post(f"/api/tasks/{task_id}/run")
        r.raise_for_status()
        decided: set[int] = set()
        deadline = time.time() + timeout
        status = "?"
        while time.time() < deadline:
            status = await self._status(task_id)
            if status in FINAL:
                break
            if status == "waiting_approval":
                pending = [a for a in (await self.client.get("/api/approvals")).json()
                           if a.get("task_id") == task_id and a.get("status") == "pending"
                           and int(a["id"]) not in decided]
                for a in pending:
                    if len(decided) >= max_approvals:
                        break
                    ok = False
                    if approve is not None:
                        res = approve(a)
                        ok = await res if asyncio.iscoroutine(res) else bool(res)
                    rr = await self.client.post(f"/api/approvals/{a['id']}",
                                                json={"approve": ok, "by": "harness-owner"})
                    rr.raise_for_status()
                    decided.add(int(a["id"]))
                if not pending and len(decided) >= max_approvals:
                    break
            await asyncio.sleep(0.2)
        else:
            await self.client.post(f"/api/tasks/{task_id}/stop")
            status = "timeout"
        detail = (await self.client.get(f"/api/tasks/{task_id}")).json()
        return TaskOutcome(task_id=task_id, status=status, result=str(detail.get("result") or ""),
                           tool_calls=await self.tool_rows(task_id), approvals=await self.approval_rows(task_id),
                           seconds=round(time.time() - started, 2))

    async def _status(self, task_id: int) -> str:
        async with self.svc.db.session() as s:
            row = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).first()
        return str(row[0]) if row else "?"

    async def _merge_meta(self, task_id: int, patch: dict) -> None:
        from bcc.db import utcnow
        async with self.svc.db.session() as s:
            row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
            meta = dict(row[0]) if row and isinstance(row[0], dict) else {}
            meta.update(patch)
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta,
                                                                                      updated_at=utcnow()))
            await s.commit()

    async def tool_rows(self, task_id: int) -> list[dict[str, Any]]:
        async with self.svc.db.session() as s:
            rows = (await s.execute(sa.select(tool_calls_t).where(tool_calls_t.c.task_id == task_id)
                                    .order_by(tool_calls_t.c.id))).fetchall()
        return [dict(r._mapping) for r in rows]

    async def approval_rows(self, task_id: int) -> list[dict[str, Any]]:
        async with self.svc.db.session() as s:
            rows = (await s.execute(sa.select(approvals_t).where(approvals_t.c.task_id == task_id)
                                    .order_by(approvals_t.c.id))).fetchall()
        return [dict(r._mapping) for r in rows]


def local_adapter_factory(base_url: str = OLLAMA_V1) -> Callable[[dict, dict], Any]:
    def factory(model: dict, provider: dict):
        return LocalOllamaAdapter(base_url=provider.get("base_url") or base_url, api_key=None)
    return factory


def fresh_dir(root: Path, name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(root) / f"{name}-{stamp}-{os.getpid()}"
    path.mkdir(parents=True, exist_ok=True)
    return path
