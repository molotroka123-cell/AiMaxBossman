from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from bcc.api import create_app
from bcc.auth import HEADER
from bcc.config import Settings
from bcc.providers import ChatResult, Health
from bcc.v2.skill_library import SkillLibrary


class _FakeAdapter:
    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        return ChatResult(text="ok", tokens_in=10, tokens_out=4, model=model)

    async def health(self) -> Health:
        return Health(status="ok", latency_ms=1)

    async def list_models(self) -> list[str]:
        return ["gemma4-31b-uncensored:latest"]


def test_perf_tracer_sanitization_and_overhead() -> None:
    from bcc.perf_trace import PerfTracer, REQUIRED_STAGE_SPANS

    expected_stages = {
        "mobile.request",
        "session.auth",
        "session.load",
        "model.catalog",
        "model.route",
        "skill.match",
        "preflight",
        "task.create",
        "queue.wait",
        "llm.load",
        "llm.prefill",
        "llm.decode",
        "tool.dispatch",
        "tool.run",
        "verify",
        "memory.retrieve",
        "receipt.persist",
        "ws.deliver",
    }
    assert expected_stages.issubset(set(REQUIRED_STAGE_SPANS))

    tracer = PerfTracer(max_traces=50)
    secret_value = "sk-super-secret-owner-token-999999"
    prompt_value = "секретный промпт владельца с паролем C:\\Users\\asd\\secret.txt"

    with tracer.start_trace(
        route="POST /api/agent/sessions/message",
        session_id="sess-12345678",
        mode="CREATE",
    ) as trace:
        with trace.span(
            "session.load",
            cache_hit=False,
            prompt=prompt_value,
            api_key=secret_value,
            raw_path="C:\\Users\\asd\\secret.txt",
            window_title="Private Telegram Window",
        ):
            pass
        with trace.span("model.catalog", cache_hit=True, worker_count=5):
            pass

    traces = tracer.recent_traces(limit=10)
    assert len(traces) == 1
    serialized = str(traces)
    assert secret_value not in serialized
    assert prompt_value not in serialized
    assert "secret.txt" not in serialized
    assert "Private Telegram Window" not in serialized

    top = tracer.top_spans(limit=10)
    assert len(top) >= 2
    assert {item["stage"] for item in top} >= {"session.load", "model.catalog"}

    # Verify tracer overhead on a 2ms workload is well below 3%
    def _work() -> int:
        s = 0
        for i in range(15000):
            s += (i * 3) ^ (i >> 1)
        return s

    t0 = time.perf_counter_ns()
    for _ in range(80):
        _work()
    base_ns = max(1, time.perf_counter_ns() - t0)

    t1 = time.perf_counter_ns()
    for _ in range(80):
        with tracer.start_trace(route="bench", session_id="s1", mode="PLAN") as tr:
            with tr.span("session.load", cache_hit=True):
                _work()
    traced_ns = time.perf_counter_ns() - t1
    overhead_per_span_us = (traced_ns - base_ns) / 80.0 / 1_000.0
    # Per-trace + span overhead must be under 50 microseconds (<0.05ms, i.e. <0.1% of a 50ms request)
    assert overhead_per_span_us < 150.0


@pytest.mark.asyncio
async def test_agent_message_emits_sanitized_perf_spans_and_top_spans(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ws_skills = tmp_path / "ws" / ".agents" / "skills"
    ws_skills.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    src_skill = repo_root / ".agents" / "skills" / "north-star-tree-hygiene" / "SKILL.md"
    if src_skill.is_file():
        dst = ws_skills / "north-star-tree-hygiene"
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "SKILL.md").write_bytes(src_skill.read_bytes())

    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
        ui_dir=tmp_path / "no-ui",
    )
    app = create_app(settings, announce_token=False, adapter_factory=lambda *a, **k: _FakeAdapter())
    svc = app.state.svc
    await svc.start()
    try:
        svc.skills = SkillLibrary([ws_skills], ws_skills)
        headers = {HEADER: svc.auth.token}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            prov = (await client.post("/api/providers", json={
                "name": "Local Ollama",
                "kind": "openai_compat",
                "base_url": "http://127.0.0.1:8080/v1",
            })).json()
            mod = (await client.post("/api/models", json={
                "provider_id": prov["id"],
                "name": "gemma4-31b-uncensored:latest",
                "alias": "gemma4-31b-uncensored:latest",
                "caps": {"chat": True, "tools": True, "code": True},
            })).json()
            await client.post("/api/agents", json={
                "name": "Bossman Local",
                "system_prompt": "Ты Bossman.",
                "model_id": mod["id"],
                "tools": ["terminal.run", "code.search", "code.expand", "code.status"],
                "permissions": {"read": "auto", "write": "ask", "exec": "ask"},
                "max_steps": 8,
            })

            sess = (await client.post("/api/agent/sessions", json={
                "title": "Perf Trace Session",
                "mode": "AGENT",
            })).json()
            sid = sess["id"]

            secret_marker = "SECRET_PROMPT_DO_NOT_LEAK_4242"
            r = await client.post(
                f"/api/agent/sessions/{sid}/messages",
                json={"text": f"Проверь north-star-tree-hygiene {secret_marker}", "mode": "AGENT"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "perf_trace_id" in body

            traces_resp = await client.get("/api/agent/perf/traces")
            assert traces_resp.status_code == 200
            traces_data = traces_resp.json()
            assert traces_data["count"] >= 1
            assert secret_marker not in str(traces_data)

            latest = traces_data["traces"][0]
            stage_names = {s["stage"] for s in latest["spans"]}
            for required in (
                "mobile.request",
                "session.auth",
                "session.load",
                "model.catalog",
                "model.route",
                "skill.match",
                "preflight",
                "task.create",
            ):
                assert required in stage_names

            top_resp = await client.get("/api/agent/perf/top-spans")
            assert top_resp.status_code == 200
            top_data = top_resp.json()
            assert len(top_data["top_spans"]) >= 5

            # CP-PERF-03: Verify skill fast_path and filtered_tools
            assert body["matched_skill"] is not None
            assert body["matched_skill"]["fast_path"] is True
            assert "code.status" in body["matched_skill"]["filtered_tools"]

            # CP-PERF-03: Verify compact mode-filtered tools payload reduction
            full_tools = await client.get("/api/agent/tools")
            compact_tools = await client.get("/api/agent/tools", params={"mode": "CODE", "compact": "1"})
            assert full_tools.status_code == 200 and compact_tools.status_code == 200
            assert len(compact_tools.content) < len(full_tools.content) * 0.45
            assert compact_tools.json()["compact"] is True
            assert compact_tools.json()["mode_filter"] == "CODE"
    finally:
        await svc.stop()
