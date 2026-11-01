"""Owner 31.10, mobile pult chat: several chats at once, a queued next prompt, «Steer», and the running marker.

* «Пока один чат работает, другие не могут — это ошибка»: chats are not serialised behind each other — two threads
  each get their own running task; STOP in chat A stops only A (and clears only A's queue).
* «Пока модель отвечает, следующий промпт встаёт в очередь треда и уходит сразу после текущего, видно и отменяемо.»
* «Steer — отправить сейчас»: the message goes into the RUNNING task and the model sees it at its next step, without a
  restart; an unread one (the answer was already final) becomes the next turn — no owner word is lost.
* the chat list marks the running chat and its model.

Synthetic chats only (owner rule: never read chat contents).
"""
from __future__ import annotations

import asyncio

import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tasks as tasks_t
from bcc.engine import STEER_PREFIX
from bcc.features import chat_threads as ct

from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task


async def _agent(env, name="q") -> dict:
    prov = (await env.client.post("/api/providers", json={
        "name": f"p-{name}", "kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "sk-test-abcd"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": f"m-{name}", "alias": f"m-{name}", "context_window": 32768})).json()
    return (await env.client.post("/api/agents", json={
        "name": name, "system_prompt": "s", "model_id": model["id"], "max_steps": 4})).json()


async def _thread(env, title: str | None = None) -> str:
    body = {"title": title} if title else {}
    return (await env.client.post("/api/chat/threads", json=body)).json()["id"]


async def _send(env, tid: str, agent_id: int, text: str, rid: str) -> int:
    resp = await env.client.post(f"/api/chat/threads/{tid}/send",
                                 json={"text": text, "agent_id": agent_id, "client_request_id": rid})
    assert resp.status_code == 200, resp.text
    return resp.json()["task"]["id"]


async def _status(env, task_id: int) -> str:
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


async def _set(env, task_id: int, status: str, result: str | None = None) -> None:
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(status=status))
        values = {"status": status}
        if result is not None:
            values["result"] = result
        await s.execute(sa.update(runs_t).where(runs_t.c.task_id == task_id).values(**values))
        await s.commit()


async def _until(fn, what: str, timeout: float = 8.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        got = await fn()
        if got:
            return got
        await asyncio.sleep(0.05)
    raise AssertionError(f"timeout: {what}")


# ------------------------------------------------------------------ several chats at once


async def test_three_chats_send_while_each_other_runs_and_stop_is_per_chat(env):
    agent = await _agent(env)
    a, b, c = await _thread(env, "A"), await _thread(env, "B"), await _thread(env, "C")
    ta = await _send(env, a, agent["id"], "долго в A", "par-a-0001")
    await _set(env, ta, "running")
    tb = await _send(env, b, agent["id"], "в B, пока A работает", "par-b-0001")
    tc = await _send(env, c, agent["id"], "в C, пока A работает", "par-c-0001")
    assert {await _status(env, tb), await _status(env, tc)} <= {"queued", "running"}, "B and C are accepted, not blocked"
    stop = (await env.client.post(f"/api/chat/threads/{a}/stop")).json()
    assert stop["task_id"] == ta
    assert await _status(env, ta) == "stopped"
    assert await _status(env, tb) != "stopped" and await _status(env, tc) != "stopped", "STOP in A leaves B and C"

    listed = {t["id"]: t for t in (await env.client.get("/api/chat/threads")).json()["items"]}
    assert listed[b]["last"]["running"] is True and listed[b]["last"]["model"] == "m-q", "running marker + model"
    assert "running" not in listed[a]["last"], "a stopped chat is not marked running"


# ------------------------------------------------------------------ queued next prompt


async def test_queued_prompt_is_sent_right_after_the_current_turn_and_can_be_cancelled(env, monkeypatch):
    monkeypatch.setattr(ct, "QUEUE_POLL_S", 0.05)
    agent = await _agent(env, "q2")
    tid = await _thread(env)
    first = await _send(env, tid, agent["id"], "первый", "queue-0001")
    await _set(env, first, "running")
    one = (await env.client.post(f"/api/chat/threads/{tid}/queue", json={"text": "второй", "agent_id": agent["id"]})).json()
    two = (await env.client.post(f"/api/chat/threads/{tid}/queue", json={"text": "третий", "agent_id": agent["id"]})).json()
    shown = (await env.client.get(f"/api/chat/threads/{tid}/queue")).json()["items"]
    assert [i["text"] for i in shown] == ["второй", "третий"], "the queue is visible"
    assert (await env.client.delete(f"/api/chat/threads/{tid}/queue/{two['item']['id']}")).json()["ok"] is True
    assert (await env.client.delete(f"/api/chat/threads/{tid}/queue/{two['item']['id']}")).status_code == 404
    await asyncio.sleep(0.3)
    turns = (await env.client.get(f"/api/chat/threads/{tid}")).json()["turns"]
    assert [t["text"] for t in turns] == ["первый"], "nothing is sent while the current turn runs"

    await _set(env, first, "completed", "ответ 1")

    async def sent():
        turns = (await env.client.get(f"/api/chat/threads/{tid}")).json()["turns"]
        return turns if len(turns) == 2 else None
    turns = await _until(sent, "queued prompt sent after the current turn")
    assert turns[1]["text"] == "второй"
    await asyncio.sleep(0.3)
    assert len((await env.client.get(f"/api/chat/threads/{tid}")).json()["turns"]) == 2, "the cancelled one never goes"
    assert (await env.client.get(f"/api/chat/threads/{tid}/queue")).json()["items"] == []
    assert one["item"]["kind"] == "queued"


async def test_stop_clears_only_this_chats_queue(env, monkeypatch):
    monkeypatch.setattr(ct, "QUEUE_POLL_S", 0.05)
    agent = await _agent(env, "q3")
    a, b = await _thread(env), await _thread(env)
    ta, tb = await _send(env, a, agent["id"], "A", "qs-a-0001"), await _send(env, b, agent["id"], "B", "qs-b-0001")
    await _set(env, ta, "running")
    await _set(env, tb, "running")
    await env.client.post(f"/api/chat/threads/{a}/queue", json={"text": "A2", "agent_id": agent["id"]})
    await env.client.post(f"/api/chat/threads/{b}/queue", json={"text": "B2", "agent_id": agent["id"]})
    await env.client.post(f"/api/chat/threads/{a}/stop")
    assert (await env.client.get(f"/api/chat/threads/{a}/queue")).json()["items"] == []
    assert [i["text"] for i in (await env.client.get(f"/api/chat/threads/{b}/queue")).json()["items"]] == ["B2"]


async def test_queue_refusals(env, monkeypatch):
    # Only the refusals are under test: no drainer sends these 50 items (CI py3.14 hung on teardown with one running).
    monkeypatch.setattr(ct, "_ensure_drainer", lambda *a, **k: None)
    agent = await _agent(env, "q4")
    tid = await _thread(env)
    for i in range(ct.QUEUE_MAX):
        assert (await env.client.post(f"/api/chat/threads/{tid}/queue", json={"text": f"m{i}"})).status_code == 201
    full = await env.client.post(f"/api/chat/threads/{tid}/queue", json={"text": "ещё"})
    assert full.status_code == 409 and full.json()["error"]["code"] == "CHAT_QUEUE_FULL"
    assert (await env.client.post("/api/chat/threads/20990101-000000-abcdef/queue", json={"text": "x"})).status_code == 404
    assert agent["id"]


# ------------------------------------------------------------------ Steer


async def test_steer_reaches_the_running_task_at_its_next_step(env):
    _install("test.echo")
    agent = await _agent(env, "st")
    async with env.svc.db.session() as s:
        from bcc.db import agents as agents_t
        await s.execute(sa.update(agents_t).where(agents_t.c.id == agent["id"]).values(tools=["test.echo"]))
        await s.commit()
    tid = await _thread(env)
    seen: list[list[dict]] = []
    steered = asyncio.Event()

    class _Steered(ToolAdapter):
        async def chat(self, model, messages, **kw):
            seen.append([dict(m) for m in messages])
            if not steered.is_set():                 # during step 1 the owner presses Steer
                resp = await env.client.post(f"/api/chat/threads/{tid}/steer", json={"text": "НАПРАВЛЕНИЕ-ВЛАДЕЛЬЦА"})
                assert resp.status_code == 202, resp.text
                steered.set()
            return await super().chat(model, messages, **kw)
    adapter = _Steered([("tool", "test.echo", {"text": "1"}), ("text", "готово")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    task_id = await _send(env, tid, agent["id"], "сделай", "steer-0001")
    assert await _run_task(env, task_id, timeout=10, until=FINISHED) == "completed"
    assert steered.is_set()
    later = seen[-1]
    assert any(m.get("role") == "user" and m.get("content") == STEER_PREFIX + "НАПРАВЛЕНИЕ-ВЛАДЕЛЬЦА" for m in later), \
        "the model sees the owner's message at its next step"
    assert len((await env.client.get(f"/api/chat/threads/{tid}")).json()["turns"]) == 1, "no restart, no new turn"


async def test_steer_without_a_running_task_is_refused(env):
    agent = await _agent(env, "st2")
    tid = await _thread(env)
    t = await _send(env, tid, agent["id"], "x", "steer-none-0001")
    await _set(env, t, "completed", "ok")
    resp = await env.client.post(f"/api/chat/threads/{tid}/steer", json={"text": "поздно"})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CHAT_NOTHING_RUNNING"


async def test_unread_steer_becomes_the_next_turn(env, monkeypatch):
    monkeypatch.setattr(ct, "QUEUE_POLL_S", 0.05)
    agent = await _agent(env, "st3")
    tid = await _thread(env)
    t = await _send(env, tid, agent["id"], "x", "steer-left-0001")
    await _set(env, t, "running")
    assert (await env.client.post(f"/api/chat/threads/{tid}/steer", json={"text": "НЕ-УСПЕЛ"})).status_code == 202
    await _set(env, t, "completed", "финальный ответ")          # the run ended before another step

    async def sent():
        turns = (await env.client.get(f"/api/chat/threads/{tid}")).json()["turns"]
        return turns if len(turns) == 2 else None
    turns = await _until(sent, "unread steer sent as the next turn")
    assert turns[1]["text"] == "НЕ-УСПЕЛ"
    assert t not in env.svc.engine.steer_inbox
