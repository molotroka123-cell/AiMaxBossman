"""Owner activity marker (owner rule 23.10: the owner's live requests always beat background local-model work).

Incident: tools/context_probe.py held ollama ~10 min while the owner's pult chat waited («нет связи»). The fix is a
short-lived marker <data>/runtime/owner_active.json written on every owner chat turn and refreshed while that turn's
task runs; background jobs read it (tests for the readers live in the root suite).
"""
from __future__ import annotations

import asyncio
import json
import time

import sqlalchemy as sa

from bcc import owner_activity as oa
from bcc.db import task_runs as runs_t, tasks as tasks_t
from bcc.features import chat_threads as ct

PROMPT = "Посчитай 17*23. Не используй инструменты."


def test_mark_then_active_until_expiry(tmp_path):
    doc = oa.mark("chat_send", data_dir=tmp_path, ttl=60, now=1000.0)
    path = tmp_path / "runtime" / "owner_active.json"
    assert path.is_file() and json.loads(path.read_text(encoding="utf-8"))["expires_at"] == 1060.0
    assert doc["schema"] == oa.SCHEMA and doc["source"] == "chat_send"
    assert oa.active(data_dir=tmp_path, now=1059.0)["source"] == "chat_send"
    assert oa.active(data_dir=tmp_path, now=1060.5) is None, "an expired marker means idle"
    assert oa.background_gate(data_dir=tmp_path, now=1010.0) == "owner_active:chat_send"
    assert oa.background_gate(data_dir=tmp_path, now=2000.0) == ""


def test_expiry_only_moves_forward_and_bad_files_mean_idle(tmp_path):
    oa.mark("chat_send", data_dir=tmp_path, ttl=600, now=1000.0)
    oa.mark("chat_run", data_dir=tmp_path, ttl=60, now=1010.0)      # a short refresh never shortens the window
    assert oa.active(data_dir=tmp_path, now=1500.0)["expires_at"] == 1600.0
    assert oa.active(data_dir=tmp_path / "nowhere") is None
    (tmp_path / "runtime" / "owner_active.json").write_text("{not json", encoding="utf-8")
    assert oa.active(data_dir=tmp_path) is None
    oa.mark("chat_send", data_dir=tmp_path, ttl=5, now=1000.0)      # a corrupt marker is simply replaced
    assert oa.active(data_dir=tmp_path, now=1001.0) is not None


def test_ttl_env_and_default_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(oa.TTL_ENV, "42")
    assert oa.ttl_s() == 42.0
    monkeypatch.setenv(oa.TTL_ENV, "junk")
    assert oa.ttl_s() == oa.DEFAULT_TTL_S
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    assert oa.marker_path() == tmp_path / "runtime" / "owner_active.json"


def test_wait_until_idle_blocks_while_active_and_can_give_up(tmp_path):
    clock = {"t": 1000.0}
    oa.mark("chat_send", data_dir=tmp_path, ttl=30, now=1000.0)
    naps: list[float] = []

    def sleep(s: float) -> None:
        naps.append(s)
        clock["t"] += s

    assert oa.wait_until_idle(data_dir=tmp_path, poll_s=10, sleep=sleep, clock=lambda: clock["t"]) is True
    assert naps == [10, 10, 10]
    oa.mark("chat_send", data_dir=tmp_path, ttl=300, now=clock["t"])
    assert oa.wait_until_idle(data_dir=tmp_path, poll_s=10, max_wait_s=25, sleep=sleep,
                              clock=lambda: clock["t"]) is False


# --------------------------------------------------------------- writer: the owner chat send path


async def _stack(client) -> None:
    provider = (await client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    await client.post("/api/agents", json={"name": "аналитик", "system_prompt": "коротко", "model_id": model["id"],
                                           "max_steps": 1})


async def _thread(env) -> str:
    resp = await env.client.post("/api/chat/threads", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_owner_chat_send_writes_a_short_lived_marker(env):
    await _stack(env.client)
    thread_id = await _thread(env)
    data_dir = env.settings.data_dir
    assert oa.active(data_dir=data_dir) is None
    before = time.time()
    resp = await env.client.post(f"/api/chat/threads/{thread_id}/send",
                                 json={"text": PROMPT, "client_request_id": "chat-owner-active-1"})
    assert resp.status_code == 200, resp.text
    doc = oa.active(data_dir=data_dir)
    assert doc is not None and doc["source"] in ("chat_send", "chat_run")
    assert before + oa.ttl_s() - 5 <= doc["expires_at"] <= time.time() + oa.ttl_s() + 1
    assert set(doc) == {"schema", "source", "at", "expires_at", "pid"}, "no chat content in the marker"
    assert oa.active(data_dir=data_dir, now=doc["expires_at"] + 1) is None, "the marker expires by itself"


async def test_refused_send_still_marks_the_owner_present(env):
    # The owner is here even when no executor is available (409): background work must still yield.
    thread_id = await _thread(env)
    resp = await env.client.post(f"/api/chat/threads/{thread_id}/send",
                                 json={"text": PROMPT, "client_request_id": "chat-owner-active-2"})
    assert resp.status_code == 409
    assert oa.active(data_dir=env.settings.data_dir)["source"] == "chat_send"


async def test_running_owner_task_keeps_the_marker_alive_then_lets_go(env, monkeypatch):
    monkeypatch.setattr(ct, "OWNER_KEEP_S", 0.02)
    await _stack(env.client)
    thread_id = await _thread(env)
    sent = (await env.client.post(f"/api/chat/threads/{thread_id}/send",
                                  json={"text": PROMPT, "client_request_id": "chat-owner-active-3"})).json()
    task_id = sent["task"]["id"]
    state = ct._state(env.svc)
    assert task_id in state.owner_tasks and state.owner_keeper is not None
    path = oa.marker_path(env.settings.data_dir)
    path.unlink()                                   # only the keeper can bring it back now
    for _ in range(200):
        if path.is_file():
            break
        await asyncio.sleep(0.01)
    assert oa.active(data_dir=env.settings.data_dir)["source"] == "chat_run"

    async with env.svc.db.session() as s:          # the owner's turn finishes
        await s.execute(sa.update(runs_t).where(runs_t.c.task_id == task_id).values(status="completed"))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(status="completed"))
        await s.commit()
    await asyncio.wait_for(asyncio.shield(state.owner_keeper), 5)
    assert state.owner_tasks == {}
    path.unlink()
    await asyncio.sleep(0.1)
    assert not path.exists(), "a finished turn does not keep background work away"
