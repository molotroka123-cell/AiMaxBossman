"""Регрессия RC TEST C: HTTP/tools-слой фактов ранее звал несуществующий API
FactStore (query/current_only/object_value/known_at) → 500 и ошибка инструмента
внутри approval-flow. Эндпоинты и tool-handlers должны работать по настоящему
контракту FactStore.
"""
from bcc.features.tools_facts import tool_fact_add
from bcc.tools import ToolContext, ToolResult


async def test_http_add_and_search_with_query(env):
    r = await env.client.post("/api/memory/facts", json={
        "subject": "rc:approve", "predicate": "status",
        "statement": "fact RCAPPROVE-A1 stored", "object": "ok"})
    assert r.status_code == 200, r.text

    r = await env.client.get("/api/memory/facts", params={"query": "RCAPPROVE-A1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["subject"] == "rc:approve"

    r = await env.client.get("/api/memory/facts", params={"query": "нет такого"})
    assert r.status_code == 200 and r.json()["total"] == 0


async def test_http_as_of_with_known_at(env):
    r = await env.client.post("/api/memory/facts", json={
        "subject": "rc:known", "predicate": "state", "statement": "known test",
        "valid_at": "2026-08-29T00:00:00Z"})
    assert r.status_code == 200, r.text
    r = await env.client.get("/api/memory/facts/as-of", params={
        "world_at": "2026-08-30T00:00:00Z", "known_at": "2027-01-01T00:00:00Z",
        "query": "known test"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


async def test_tool_fact_add_executes(env):
    ctx = ToolContext(svc=env.svc, task={"id": 0}, run_id=None, agent={})
    result: ToolResult = await tool_fact_add(
        {"subject": "rc:tool", "predicate": "status", "statement": "via tool",
         "object": "", "mode": "append"}, ctx)
    assert not result.error, result.content
    assert "fact#" in result.one_line
    # проверяем через тот же HTTP слой
    r = await env.client.get("/api/memory/facts", params={"query": "via tool"})
    assert r.status_code == 200 and r.json()["total"] == 1
