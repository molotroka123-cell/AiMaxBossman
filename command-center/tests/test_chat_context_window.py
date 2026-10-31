"""Owner 31.10, mobile pult chat: «модель при загрузке новая может читать весь контекст текущего чата; если контекст
не влазит — сама его скомпоновывать предлагает» and «облачные модели лагают, не читая переписок».

Live DB 31.10 (read-only, ids/lengths only): every chat prompt carried at most 3 previous turns, each side cut to 1500
chars, whatever the model's window (a 28-turn chat across 4 models: the new model saw 3); the AI Gateway cut the task
message (request + chat) out of cloud calls after ~4 tool rounds (312 of 396 Outrun calls `trimmed_chat_history`); a
privacy refusal moved a GLM turn to a local model with no word to the owner (first token after 140 s).

Positive and negative controls for each fix: the window decides how much history goes; what does not fit is named
and /compact is proposed; switching to a bigger model brings the whole chat back; the gateway keeps the task message;
a privacy refusal is announced live; cloud turns do not wait for a memory recall they never get.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc import conversation_context
from bcc.db import events as events_t, run_events as run_events_t, task_runs as runs_t, tasks as tasks_t
from bcc.features import chat_threads as ct
from bcc.security.ai_gateway import ContextMinifier, GatewayBlockedError, RedactionSession

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, _run_task

LONG = "ы" * 3000          # one answer longer than the old 1500-char cap


async def _agent(env, *, window: int, name: str, kind: str = "local", base: str = "http://127.0.0.1:8080/v1") -> dict:
    prov = (await env.client.post("/api/providers", json={
        "name": f"p-{name}", "kind": "openai_compat", "base_url": base, "api_key": "sk-test-abcd"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": f"m-{name}", "alias": f"m-{name}", "kind": kind,
        "context_window": window, "price_in": 0.0, "price_out": 0.0})).json()
    return (await env.client.post("/api/agents", json={
        "name": name, "system_prompt": "отвечай коротко", "model_id": model["id"], "max_steps": 1,
        "max_tokens": 1024})).json()


async def _thread(env) -> str:
    resp = await env.client.post("/api/chat/threads", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _turns(env, thread_id: str, agent_id: int, n: int, answer: str = LONG) -> None:
    for i in range(n):
        resp = await env.client.post(f"/api/chat/threads/{thread_id}/send", json={
            "text": f"вопрос {i}", "agent_id": agent_id, "client_request_id": f"ctxwin-{thread_id[-6:]}-{i:04d}"})
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task"]["id"]
        async with env.svc.db.session() as s:
            await s.execute(sa.update(runs_t).where(runs_t.c.task_id == task_id).values(status="completed",
                                                                                        result=f"ответ {i} {answer}"))
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(status="completed"))
            await s.commit()


async def _send(env, thread_id: str, agent_id: int, text: str, rid: str) -> dict:
    resp = await env.client.post(f"/api/chat/threads/{thread_id}/send",
                                 json={"text": text, "agent_id": agent_id, "client_request_id": rid})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    body["prompt"] = (await env.client.get(f"/api/tasks/{body['task']['id']}")).json()["task"]["prompt"]
    return body


# ------------------------------------------------------------------ A: the window decides


async def test_big_window_model_reads_the_whole_chat(env):
    big = await _agent(env, window=200_000, name="big")
    tid = await _thread(env)
    await _turns(env, tid, big["id"], 8)
    out = await _send(env, tid, big["id"], "итог?", "ctxwin-big-final")
    prompt, ctx = out["prompt"], out["context"]
    assert all(f"Владелец: вопрос {i}" in prompt for i in range(8)), "all 8 turns reach the model (was: last 3)"
    assert LONG in prompt, "a 3000-char answer is not cut at the old 1500 chars"
    assert "Опущено ранних ходов" not in prompt
    assert ctx["turns_live"] == 8 and ctx["turns_included"] == 8 and ctx["turns_dropped"] == 0
    assert ctx["window"] == 200_000 and ctx["compact_suggested"] is False
    assert conversation_context.current_request(prompt) == "итог?"


async def test_small_window_drops_oldest_names_it_and_proposes_compact(env):
    small = await _agent(env, window=8192, name="small")
    tid = await _thread(env)
    await _turns(env, tid, small["id"], 8)
    out = await _send(env, tid, small["id"], "итог?", "ctxwin-small-final")
    prompt, ctx = out["prompt"], out["context"]
    assert ctx["turns_dropped"] > 0 and ctx["turns_included"] < 8, ctx
    assert ctx["compact_suggested"] is True and ctx["used_tokens"] <= ctx["budget_tokens"]
    assert f"Владелец: вопрос 7" in prompt, "the newest turn is always kept"
    assert "Владелец: вопрос 0" not in prompt, "the oldest leaves first"
    assert f"(Опущено ранних ходов: {ctx['turns_dropped']} из 8" in prompt, "the model is told what it does not see"
    assert conversation_context.current_request(prompt) == "итог?"


async def test_switching_to_a_bigger_model_brings_the_whole_chat_back(env):
    small = await _agent(env, window=8192, name="small2")
    big = await _agent(env, window=1_000_000, name="big2", kind="cloud", base="https://api.example.com/v1")
    tid = await _thread(env)
    await _turns(env, tid, small["id"], 8)
    first = await _send(env, tid, small["id"], "на малой", "ctxwin-switch-0001")
    assert first["context"]["turns_dropped"] > 0
    second = await _send(env, tid, big["id"], "а теперь на большой", "ctxwin-switch-0002")
    assert second["context"]["turns_dropped"] == 0 and second["context"]["turns_included"] == 9, second["context"]
    assert all(f"Владелец: вопрос {i}" in second["prompt"] for i in range(8))


async def test_compact_summary_is_kept_when_history_does_not_fit(env):
    small = await _agent(env, window=8192, name="small3")
    tid = await _thread(env)
    await _turns(env, tid, small["id"], 8)
    path = env.settings.data_dir / "terminal" / "sessions" / f"{tid}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(summary="РЕЗЮМЕ-ЧАТА", compacted_at_turn=2, compact_tasks=[1])
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    out = await _send(env, tid, small["id"], "итог?", "ctxwin-summary-final")
    assert "РЕЗЮМЕ-ЧАТА" in out["prompt"] and out["context"]["summary"] is True
    assert out["context"]["turns_live"] == 6


async def test_context_gauge_follows_the_selected_model(env):
    small = await _agent(env, window=8192, name="small4")
    big = await _agent(env, window=200_000, name="big4")
    tid = await _thread(env)
    await _turns(env, tid, small["id"], 8)
    on_small = (await env.client.get(f"/api/agent/sessions/{tid}/context", params={"agent_id": small["id"]})).json()
    on_big = (await env.client.get(f"/api/agent/sessions/{tid}/context", params={"agent_id": big["id"]})).json()
    assert on_small["compact_suggested"] is True and on_small["turns_dropped"] > 0
    assert on_big["compact_suggested"] is False and on_big["turns_in_context"] == 8
    assert on_big["context_window"] == 200_000 and on_big["context_window_source"] == "selected_model"


# ------------------------------------------------------------------ B: the gateway keeps the task message


def _tool_loop(rounds: int, head: str) -> list[dict]:
    msgs = [{"role": "system", "content": "ты агент"}, {"role": "user", "content": head}]
    for i in range(rounds):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "code.search", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"результат {i} " + "x" * 3000})
    return msgs


def test_gateway_keeps_the_task_message_after_many_tool_rounds():
    head = conversation_context.HEADER + "Владелец: ИСТОРИЯ-ЧАТА" + conversation_context.MARKER + "сделай"
    session = RedactionSession()
    packed = ContextMinifier().pack_minimal_context(_tool_loop(6, head), session, max_history_messages=8)
    dialogue = [m for m in packed if m["role"] != "system"]
    assert "context:trimmed_chat_history" in session.triggered_rules
    assert dialogue[0]["content"] == head, "the request + chat survive the trim (was: cut first)"
    assert dialogue[1]["role"] != "tool", "no orphan tool result without its assistant tool_calls"
    assert len(dialogue) <= 8


def test_gateway_char_budget_keeps_the_task_message():
    head = "ЗАДАЧА " + "ч" * 30_000
    session = RedactionSession()
    packed = ContextMinifier().pack_minimal_context(_tool_loop(3, head), session, max_history_messages=40,
                                                    max_context_chars=24_000)
    dialogue = [m for m in packed if m["role"] != "system"]
    assert "context:enforced_char_budget" in session.triggered_rules
    assert dialogue[0]["content"] == head
    assert all(m["role"] != "tool" for m in dialogue[:2])


def test_gateway_window_meta_scales_with_the_model():
    from bcc.provider_governance import gateway_window_meta
    assert gateway_window_meta({"context_window": 1_000_000}) == {"max_context_chars": 600_000,
                                                                 "max_history_messages": 40}
    assert gateway_window_meta({"context_window": 8192}) == {"max_context_chars": 24_000, "max_history_messages": 8}
    assert gateway_window_meta({"context_window": None}) == {}, "unknown window: the gateway defaults stay"


def test_negative_control_small_history_is_untouched():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "привет"}]
    session = RedactionSession()
    assert ContextMinifier().pack_minimal_context(msgs, session) == msgs
    assert "context:trimmed_chat_history" not in session.triggered_rules


# ------------------------------------------------------------------ B: privacy refusal is announced, no wasted recall


class _Blocked(FakeAdapter):
    async def chat(self, model, messages, **kw):
        self.calls += 1
        raise GatewayBlockedError("Конфиденциальный запрос: внешний запрос запрещён",
                                  reason_code="confidential_external_forbidden",
                                  triggered_rules=["confidential:protected_secret_file", "context:trimmed_chat_history",
                                                   "gate:confidential_blocked_external"])


async def _cloud_task(env, adapter, prompt="напиши план"):
    stack = await make_stack(env.client, prompt=prompt)
    prov = (await env.client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-test-abcd"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": "vendor/cloud-x", "alias": "cloud-x", "kind": "cloud",
        "price_in": 0.0, "price_out": 0.0})).json()
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"model_id": model["id"]})
    env.svc.registry.adapter_factory = lambda m, p: adapter
    return stack


async def _events(env, kind: str, task_id: int) -> list[dict]:
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t.c.data).where(events_t.c.kind == kind))).fetchall()
    out = []
    for (data,) in rows:
        data = json.loads(data) if isinstance(data, str) else data
        if isinstance(data, dict) and data.get("task_id") == task_id:
            out.append(data)
    return out


async def _log_kinds(env, task_id: int) -> list[str]:
    async with env.svc.db.session() as s:
        run_ids = [r[0] for r in (await s.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task_id)))]
        return [r[0] for r in (await s.execute(sa.select(run_events_t.c.kind)
                                               .where(run_events_t.c.run_id.in_(run_ids))))]


async def test_privacy_refusal_is_announced_live_with_rule_names_only(env):
    stack = await _cloud_task(env, _Blocked())
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    notices = await _events(env, "run.notice", stack["task"]["id"])
    assert notices, "the owner sees why the cloud model did not get the chat"
    n = notices[0]
    assert n["notice"] == "privacy_withheld" and n["reason_code"] == "confidential_external_forbidden"
    assert n["rules"] == ["protected_secret_file"], "rule names only, no gate/context noise"


async def test_negative_control_ordinary_failure_is_not_a_privacy_notice(env):
    stack = await _cloud_task(env, FakeAdapter(fail_times=99))
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert not await _events(env, "run.notice", stack["task"]["id"])


async def test_cloud_turn_skips_memory_recall_it_would_never_receive(env):
    stack = await _cloud_task(env, FakeAdapter("готово"))
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    kinds = await _log_kinds(env, stack["task"]["id"])
    assert "memory.recall_skipped" in kinds and "memory.recalled" not in kinds


async def test_negative_control_local_turn_still_recalls(env):
    stack = await make_stack(env.client)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    async with env.svc.db.session() as s:
        run_ids = [r[0] for r in (await s.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == stack["task"]["id"])))]
        msgs = [r[0] for r in (await s.execute(sa.select(run_events_t.c.message).where(
            run_events_t.c.run_id.in_(run_ids), run_events_t.c.kind == "memory.recall_skipped")))]
    assert not any("облачная" in str(m) for m in msgs), "a local model is not treated as cloud"


# ------------------------------------------------------------------ C: parallel chats — the queue says why


async def test_queue_names_the_chat_that_holds_the_slots(env, monkeypatch):
    agent = await _agent(env, window=32768, name="q")
    busy_thread, waiting_thread = await _thread(env), await _thread(env)
    busy = await _send(env, busy_thread, agent["id"], "долгая работа", "ctxwin-queue-busy")
    waiting = await _send(env, waiting_thread, agent["id"], "второй чат", "ctxwin-queue-wait")
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.task_id == busy["task"]["id"]).values(status="running"))
        await s.commit()
    monkeypatch.setattr(env.svc.engine, "workers", 1)
    q = (await env.client.get(f"/api/chat/tasks/{waiting['task']['id']}/queue")).json()
    assert q["status"] == "queued" and q["reason"] == "slots_busy" and q["workers"] == 1
    assert q["running"][0]["thread_id"] == busy_thread and q["running"][0]["thread_title"]
    monkeypatch.setattr(env.svc.engine, "workers", 3)
    q2 = (await env.client.get(f"/api/chat/tasks/{waiting['task']['id']}/queue")).json()
    assert q2["reason"] != "slots_busy", "a free slot: chats are not serialised behind each other"
    assert (await env.client.get("/api/chat/tasks/999999/queue")).status_code == 404


# ------------------------------------------------------------------ item 3: carry a compacted context to another chat


def _compact(env, thread_id: str, summary: str) -> None:
    path = env.settings.data_dir / "terminal" / "sessions" / f"{thread_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(summary=summary, compacted_at_turn=len(raw.get("turns") or []), compact_tasks=[1])
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


async def test_import_compacted_context_into_another_chat(env):
    agent = await _agent(env, window=32768, name="imp")
    src, plain, dst = await _thread(env), await _thread(env), await _thread(env)
    await env.client.patch(f"/api/chat/threads/{src}", json={"title": "Проект Альфа"})
    await _turns(env, src, agent["id"], 2, answer="коротко")
    _compact(env, src, "РЕЗЮМЕ-АЛЬФА: решили делать X")
    await _turns(env, plain, agent["id"], 1, answer="коротко")         # turns but no /compact
    listed = (await env.client.get("/api/chat/compacted", params={"exclude": dst})).json()["items"]
    assert [i["id"] for i in listed] == [src], "only chats with a /compact summary are offered"
    assert listed[0]["title"] == "Проект Альфа" and "summary" not in listed[0], "titles and sizes, not the text"

    resp = await env.client.post(f"/api/chat/threads/{dst}/import-context",
                                 json={"source_thread_id": src, "agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported"] is True and resp.json()["source_title"] == "Проект Альфа"
    thread = (await env.client.get(f"/api/chat/threads/{dst}")).json()
    note = thread["turns"][-1]
    assert note["kind"] == "note" and note["text"] == "Контекст из чата «Проект Альфа»"
    assert note["note"]["kind"] == "context_import"
    again = await env.client.post(f"/api/chat/threads/{dst}/import-context", json={"source_thread_id": src})
    assert again.status_code == 201 and again.json()["imported"] is False, "the same summary is not inserted twice"

    out = await _send(env, dst, agent["id"], "что решили?", "ctxwin-import-ask")
    head = out["prompt"].split(conversation_context.MARKER)[0]
    assert "Контекст, перенесённый владельцем из чата «Проект Альфа»" in head and "РЕЗЮМЕ-АЛЬФА" in head
    assert conversation_context.current_request(out["prompt"]) == "что решили?"


async def test_import_refusals(env, monkeypatch):
    agent = await _agent(env, window=32768, name="imp2")
    plain, dst, big = await _thread(env), await _thread(env), await _thread(env)
    await _turns(env, plain, agent["id"], 1, answer="коротко")
    no_summary = await env.client.post(f"/api/chat/threads/{dst}/import-context", json={"source_thread_id": plain})
    assert no_summary.status_code == 409 and no_summary.json()["error"]["code"] == "CHAT_NO_SUMMARY"
    missing = await env.client.post(f"/api/chat/threads/{dst}/import-context",
                                    json={"source_thread_id": "20990101-000000-abcdef"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "CHAT_THREAD_NOT_FOUND"
    same = await env.client.post(f"/api/chat/threads/{dst}/import-context", json={"source_thread_id": dst})
    assert same.status_code == 422
    _compact(env, big, "Р" * 4000)
    monkeypatch.setattr(ct, "IMPORT_MAX_SHARE", 0.01)                  # budget smaller than the summary
    too_big = await env.client.post(f"/api/chat/threads/{dst}/import-context",
                                    json={"source_thread_id": big, "agent_id": agent["id"]})
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "CHAT_IMPORT_TOO_LARGE"
    turns = (await env.client.get(f"/api/chat/threads/{dst}")).json()["turns"]
    assert not any(t.get("kind") == "note" for t in turns), "a refused import writes nothing"
