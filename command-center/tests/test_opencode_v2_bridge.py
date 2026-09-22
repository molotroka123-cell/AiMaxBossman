"""R12 (2026-09-22): мост к OpenCode различает v1 и v2 и не верит HTML.

Установленный OpenCode v2.0.12 держит API под `/api/…`, а корневые v1-пути
(`/session`, `/config`) отдают HTML веб-приложения с HTTP 200. Прежний health
по `/config` рапортовал «online», и все вызовы сессий падали. Проверяем на
фальшивых серверах: v1 JSON, v2 JSON, «только SPA», v2 с паролем, v1-путь с
JSON не той формы.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import settings_kv
from bcc.v2.opencode_bridge import OpenCodeBridge, OpenCodeIncompatible, assistant_text

from .fixtures.fake_opencode_server import FakeOpenCode
from .fixtures.fake_opencode_v2_server import FakeOpenCodeV2


@pytest.fixture
def v1():
    with FakeOpenCode() as srv:
        yield srv


@pytest.fixture
def v2():
    with FakeOpenCodeV2() as srv:
        yield srv


@pytest.fixture
def spa():
    with FakeOpenCodeV2(spa_only=True) as srv:
        yield srv


# ------------------------------------------------------------------- health

async def test_v1_json_is_online_v1(v1):
    h = await OpenCodeBridge(base_url=v1.url).health(2)
    assert h["status"] == "online" and h["api"] == "v1" and h["probe"] == "/session"


async def test_v2_json_is_online_v2_with_version(v2):
    h = await OpenCodeBridge(base_url=v2.url).health(2)
    assert h["status"] == "online" and h["api"] == "v2"
    assert h["version"] == "2.0.12" and h["probe"] == "/api/session"
    assert h["capabilities"] == {"todo": False}


async def test_spa_html_is_not_available(spa):
    """Ровно дефект R12: HTML с кодом 200 на всех путях — НЕ online."""
    bridge = OpenCodeBridge(base_url=spa.url)
    h = await bridge.health(2)
    assert h["status"] == "incompatible_version", h
    assert "text/html" in h["detail"] and "/session" in h["detail"]
    # и сессии не делают вид, что работают: честное исключение, не JSONDecodeError
    with pytest.raises(OpenCodeIncompatible):
        await bridge.create_session("/tmp/x", title="t")
    with pytest.raises(OpenCodeIncompatible):
        await bridge.list_sessions()


async def test_v2_root_paths_are_html_so_v1_probe_alone_would_lie(v2):
    """Сам фальшивый v2 устроен как настоящий: /config → HTML 200."""
    async with httpx.AsyncClient(trust_env=False) as c:
        r = await c.get(v2.url + "/config")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


async def test_json_of_the_wrong_shape_is_incompatible():
    """JSON — ещё не «наш API»: форма списка сессий обязана совпасть."""
    bridge = OpenCodeBridge(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"sessions": "не список"})))
    h = await bridge.health(2)
    assert h["status"] == "incompatible_version" and bridge.api == ""


async def test_v2_password_required_is_unauthorized_not_online():
    with FakeOpenCodeV2(password="s3cret") as srv:
        h = await OpenCodeBridge(base_url=srv.url).health(2)
        assert h["status"] == "unauthorized" and "OPENCODE_PASSWORD" in h["hint"]
        ok = await OpenCodeBridge(base_url=srv.url, password="s3cret").health(2)
        assert ok["status"] == "online" and ok["api"] == "v2"


async def test_nothing_listening_is_unavailable():
    h = await OpenCodeBridge(base_url="http://127.0.0.1:1").health(0.5)
    assert h["status"] == "unavailable" and h["hint"]


# ---------------------------------------------------------------- v2 сессии

async def test_v2_session_lifecycle_through_bridge(v2, tmp_path):
    bridge = OpenCodeBridge(base_url=v2.url)
    s = await bridge.create_session(str(tmp_path), title="починить", agent="build")
    sid = s["id"]
    assert sid.startswith("ses")
    post = [b for m, p, _, b in v2.requests if m == "POST" and p == "/api/session"][-1]
    assert post["location"] == {"directory": str(tmp_path)} and post["title"] == "починить"

    assert (await bridge.get_session(sid))["id"] == sid
    assert [x["id"] for x in await bridge.list_sessions(str(tmp_path))] == [sid]

    v2.reply = "исправил calc.py"
    reply = await bridge.send_message(sid, "почини тест", agent="build")
    assert assistant_text(reply) == "исправил calc.py"
    assert reply["info"]["role"] == "assistant" and reply["info"]["id"].startswith("msg_")

    diffs = await bridge.diff(sid)
    assert diffs and diffs[0]["file"] == "calc.py"
    msgs = await bridge.messages(sid)
    assert [m["info"]["role"] for m in msgs] == ["user", "assistant"]

    assert (await bridge.session_status(sid))["type"] == "idle"
    v2.hold = True
    assert await bridge.prompt_async(sid, "длинная задача") is True
    assert (await bridge.session_status(sid))["type"] == "busy"
    assert await bridge.abort(sid) is True
    assert (await bridge.session_status(sid))["type"] == "idle"

    child = await bridge.fork(sid)
    assert child["parentID"] == sid
    assert [c["id"] for c in await bridge.children(sid)] == [child["id"]]
    assert await bridge.todo(sid) == []
    with pytest.raises(OpenCodeIncompatible, match="message_id"):
        await bridge.diff(sid, "msg_1")


async def test_v1_still_works_through_bridge(v1, tmp_path):
    bridge = OpenCodeBridge(base_url=v1.url)
    s = await bridge.create_session(str(tmp_path), title="t")
    assert (await bridge.session_status(s["id"]))["type"] == "idle"
    assert bridge.api == "v1"


# ------------------------------------------------------- через Command Center

async def _roots(env, roots: list[Path]) -> None:
    enc = env.svc.vault.encrypt(json.dumps([str(Path(r).resolve()) for r in roots]))
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "opencode.roots"))
        await s.execute(sa.insert(settings_kv).values(key="opencode.roots", value_enc=enc))
        await s.commit()


async def test_http_api_against_v2(env, v2, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_URL", v2.url)
    project = (tmp_path / "proj").resolve()
    project.mkdir()
    await _roots(env, [tmp_path])
    health = (await env.client.get("/api/opencode/health")).json()
    assert health["status"] == "online" and health["api"] == "v2"

    started = (await env.client.post("/api/opencode/sessions",
                                     json={"project_path": str(project)})).json()
    sid = started["session_id"]
    assert sid.startswith("ses")
    sent = (await env.client.post(f"/api/opencode/sessions/{sid}/send",
                                  json={"text": "сделай"})).json()
    assert sent["text"] == "готово" and sent["message_id"].startswith("msg_")
    st = (await env.client.get(f"/api/opencode/sessions/{sid}/status")).json()
    assert st["state"]["type"] == "idle"
    diff = (await env.client.get(f"/api/opencode/sessions/{sid}/diff")).json()
    assert diff["source"] == "live" and diff["summary"]["files"] == 1


async def test_http_api_against_spa_is_honest(env, spa, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_URL", spa.url)
    project = (tmp_path / "proj").resolve()
    project.mkdir()
    await _roots(env, [tmp_path])
    health = (await env.client.get("/api/opencode/health")).json()
    assert health["status"] == "incompatible_version"
    r = await env.client.post("/api/opencode/sessions", json={"project_path": str(project)})
    assert r.status_code == 502                         # не 500 и не «успех»
    assert "OpenCodeIncompatible" in json.dumps(r.json(), ensure_ascii=False)
