"""Owner 31.10, the phone gateway half of: per-chat STOP, the thread queue, Steer, the queue reason, «Перенести контекст
из…», and the «облако» label for a cloud agent whose model view says local.

Drives bossmobile.api.create_app through tests/conftest.py (mocked Bossman transport, tmp data dir); every chat text
is synthetic. Upstream answers follow command-center/bcc/features/chat_threads.py.
"""
from __future__ import annotations

import json

import httpx
import pytest
from conftest import AGENTS, MODELS, OPTIONS, PROVIDERS, FakeBossman

from bossmobile.policy import local_agents

SEEN: list[tuple[str, str, dict | None, dict]] = []


def reply(request: httpx.Request):
    path, method = request.url.path, request.method
    body = json.loads(request.content) if request.content else None
    SEEN.append((method, path, body, dict(request.url.params)))
    if path == "/api/chat/threads/T1/queue" and method == "POST":
        return httpx.Response(201, json={"thread_id": "T1", "item": {"id": "qabc123", "text": body["text"], "kind": "queued"},
                                         "items": [{"id": "qabc123", "text": body["text"], "kind": "queued"}]})
    if path == "/api/chat/threads/T1/queue" and method == "GET":
        return httpx.Response(200, json={"thread_id": "T1", "items": []})
    if path == "/api/chat/threads/T1/queue/qabc123" and method == "DELETE":
        return httpx.Response(200, json={"ok": True, "removed": "qabc123"})
    if path == "/api/chat/threads/T1/steer" and method == "POST":
        return httpx.Response(202, json={"ok": True, "task_id": 7, "pending": 1})
    if path == "/api/chat/tasks/7/queue":
        return httpx.Response(200, json={"task_id": 7, "status": "queued", "workers": 3, "reason": "slots_busy",
                                         "running": [{"task_id": 5, "thread_id": "T2", "thread_title": "Другой чат"}],
                                         "running_total": 3, "ahead": 0})
    if path == "/api/chat/compacted":
        return httpx.Response(200, json={"items": [{"id": "T2", "title": "Сжатый", "summary_chars": 900,
                                                    "summary_tokens": 410, "compacted_at_turn": 4}]})
    if path == "/api/chat/threads/T1/import-context" and method == "POST":
        if body["source_thread_id"] == "T9":
            return httpx.Response(404, json={"error": {"message": "чат не найден", "code": "CHAT_THREAD_NOT_FOUND"}})
        return httpx.Response(201, json={"ok": True, "imported": True, "source_thread_id": body["source_thread_id"]})
    return None


@pytest.fixture(autouse=True)
def _upstream(monkeypatch):
    SEEN.clear()
    orig = FakeBossman.handler

    def handler(self, request):
        resp = orig(self, request)
        if resp.status_code == 404:
            mine = reply(request)
            if mine is not None:
                return mine
        return resp

    monkeypatch.setattr(FakeBossman, "handler", handler)
    yield


def sent(method: str, path: str):
    return [s for s in SEEN if s[0] == method and s[1] == path]


def test_queue_post_list_cancel_and_cloud_rule(authed):
    ok = authed.post("/m/api/threads/T1/queue", json={"text": "следующий", "agent_id": 1})
    assert ok.status_code == 201 and ok.json()["item"]["kind"] == "queued"
    assert sent("POST", "/api/chat/threads/T1/queue")[0][2] == {"text": "следующий", "agent_id": 1}
    assert authed.c.get("/m/api/threads/T1/queue").json()["items"] == []
    assert authed.c.delete("/m/api/threads/T1/queue/qabc123", headers={"X-BM-CSRF": authed.csrf}).json()["ok"] is True
    bad_id = authed.c.delete("/m/api/threads/T1/queue/..%2Fx", headers={"X-BM-CSRF": authed.csrf})
    assert bad_id.status_code == 404
    # negative: the same phone rule as Send — a cloud agent without the owner's opt-in is refused, nothing goes upstream
    SEEN.clear()
    refused = authed.post("/m/api/threads/T1/queue", json={"text": "в облако", "agent_id": 3})
    assert refused.status_code == 403 and not sent("POST", "/api/chat/threads/T1/queue")


def test_queue_needs_csrf(authed):
    assert authed.c.post("/m/api/threads/T1/queue", json={"text": "x", "agent_id": 1}).status_code == 403
    assert authed.c.post("/m/api/threads/T1/steer", json={"text": "x"}).status_code == 403


def test_steer_and_queue_reason_pass_through(authed):
    r = authed.post("/m/api/threads/T1/steer", json={"text": "сейчас"})
    assert r.status_code == 202 and r.json()["task_id"] == 7
    assert sent("POST", "/api/chat/threads/T1/steer")[0][2] == {"text": "сейчас"}
    q = authed.c.get("/m/api/tasks/7/queue").json()
    assert q["reason"] == "slots_busy" and q["running"][0]["thread_title"] == "Другой чат"


def test_compacted_list_and_import(authed):
    items = authed.c.get("/m/api/compacted-threads", params={"exclude": "T1"}).json()["items"]
    assert items[0]["id"] == "T2" and sent("GET", "/api/chat/compacted")[0][3] == {"exclude": "T1"}
    ok = authed.post("/m/api/threads/T1/import-context", json={"source_thread_id": "T2", "agent_id": 1})
    assert ok.status_code == 201 and ok.json()["imported"] is True
    assert sent("POST", "/api/chat/threads/T1/import-context")[0][2] == {"source_thread_id": "T2", "agent_id": 1}
    missing = authed.post("/m/api/threads/T1/import-context", json={"source_thread_id": "T9"})
    assert missing.status_code == 404
    bad = authed.post("/m/api/threads/T1/import-context", json={"source_thread_id": "../x"})
    assert bad.status_code == 404, "a malformed id never reaches Bossman"


def test_chat_stop_does_not_flip_the_global_worker_stop(authed):
    authed.post("/m/api/workers/register", json={"device_id": "pc", "role": "gpu-worker"})
    authed.post("/m/api/workers/heartbeat", json={"device_id": "pc", "active_task_id": 55})
    assert authed.post("/m/api/threads/T1/stop").status_code == 200          # stops task 7 of T1
    ws = authed.c.get("/m/api/workers").json()
    assert ws["global_stop"] is False and ws["devices"][0]["active_task_id"] == 55


def test_cloud_provider_is_never_labelled_local_even_if_the_view_says_local():
    """owner 31.10 screenshot: «GLM 5.3 Flash Uncensored — локально». The provider address decides."""
    opts = json.loads(json.dumps(OPTIONS))
    glm = next(a for a in opts["agents"] if a["id"] == 4)
    glm["model"].update(locality="local", locality_detail="local", billing="local")      # a wrong upstream view
    by = {a["id"]: a for a in local_agents(opts, AGENTS, MODELS, PROVIDERS)}
    assert by[4]["local"] is False and by[4]["cloud"] is True and by[4]["provider"] == "7API"
    assert by[1]["local"] is True and by[1]["cloud"] is False, "negative control: a loopback agent stays local"
