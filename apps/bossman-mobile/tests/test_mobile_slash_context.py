"""Gateway half of the phone «/» menu and the context gauge (owner 21.10): /m/api/agent/palette, .../context, .../compact,
.../memory.

Facts for the gate: new test file, no importers. It drives bossmobile.api.create_app through tests/conftest.py (mocked
Bossman and Ollama transports, tmp data dir), adding the four upstream answers as in the BCC contract
(command-center/bcc/features/agent_extras.py). Owner rule 21.10 verbatim: «Запрещено читать локальные переписки данные
и фото» -> every chat text below is a synthetic canary string; no live service and no owner data is touched.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from conftest import BOSS_TOKEN, REAL_CSRF, SID, FakeBossman

from bossmobile import slash

CANARY = "CANARY-PRIVATE-CHAT-TURN-7f3a9c51"
ROOT = pathlib.Path(__file__).resolve().parents[1]

PALETTE = {
    "commands": [
        {"name": "code", "usage": "/code <задача>", "help": "Режим кода", "kind": "message"},
        {"name": "compact", "usage": "/compact [подсказка]", "help": "Сжать контекст", "kind": "action"},
        {"name": "new", "usage": "/new", "help": "Новый чат", "kind": "client"},
    ],
    "tools": [
        {"name": "computer.observe", "group": "Computer", "description": "Снимок экрана", "risk": "low",
         "needs_approval": False, "effect": "allow"},
        {"name": "cloud.ask", "group": "Cloud", "description": "Спросить сильную облачную модель", "risk": "high",
         "needs_approval": True, "effect": "ask"},
    ],
    "skills": [{"id": "pytest-fix", "title": "Починить тесты", "status": "green", "triggers": ["тест", "pytest"]}],
    "agent": {"id": 41, "name": "Gemma", "mode": "AGENT"},
    "generated_at": "2026-10-21T10:00:00+00:00",
}
CONTEXT = {
    "session_id": "S1", "turns_total": 12, "turns_in_context": 9, "compacted": True, "compacted_at_turn": 3,
    "summary_chars": 1800, "used_tokens": 6400, "context_window": 8192, "used_pct": 78.1, "left_tokens": 1792,
    "left_pct": 21.9, "level": "warn", "source": "provider_usage",
    "compact": {"state": "idle", "task_id": None, "finished_at": None, "error": None},
}

STATE: dict = {}


def reply(self: FakeBossman, request: httpx.Request):
    """The four new upstream endpoints. Only reached when conftest's handler answered 404 (auth/CSRF already checked)."""
    path, method = request.url.path, request.method
    body = json.loads(request.content) if request.content else None
    self.slash_bodies.append((method, path, body))
    if path == "/api/agent/palette" and method == "GET":
        return httpx.Response(200, json=STATE.get("palette", PALETTE))
    if path == "/api/agent/sessions/S1/context" and method == "GET":
        if "context_error" in STATE:
            return httpx.Response(STATE["context_error"][0], json=STATE["context_error"][1])
        return httpx.Response(200, json=STATE.get("context", CONTEXT))
    if path == "/api/agent/sessions/S1/compact" and method == "POST":
        code, payload = STATE.get("compact", (202, {"session_id": "S1", "task_id": "cmp-1", "status": "running"}))
        return httpx.Response(code, json=payload)
    if path == "/api/agent/sessions/S1/memory" and method == "POST":
        code, payload = STATE.get("memory", (200, {"ok": True, "note_id": "mem-7", "chars": 1800}))
        return httpx.Response(code, json=payload)
    # synthetic chat history with a canary: nothing in this file may ever fetch it
    if path.startswith("/api/chat/threads/") or path == "/api/agent/sessions/S1":
        return httpx.Response(200, json={"id": "S1", "turns": [{"idx": 0, "text": CANARY, "result": CANARY}]})
    return None


@pytest.fixture(autouse=True)
def _upstream(monkeypatch):
    STATE.clear()
    orig = FakeBossman.handler

    def handler(self, request):
        if not hasattr(self, "slash_bodies"):
            self.slash_bodies = []
        resp = orig(self, request)
        if resp.status_code == 404:
            mine = reply(self, request)
            if mine is not None:
                return mine
        return resp

    monkeypatch.setattr(FakeBossman, "handler", handler)
    yield
    STATE.clear()


def mine(env):
    return getattr(env.fake, "slash_bodies", [])


# ----------------------------------------------------------------------------------------------- auth and CSRF
@pytest.mark.parametrize("method,path", [
    ("GET", "/m/api/agent/palette"), ("GET", "/m/api/agent/sessions/S1/context"),
    ("POST", "/m/api/agent/sessions/S1/compact"), ("POST", "/m/api/agent/sessions/S1/memory")])
def test_needs_login(env, method, path):
    r = env.c.request(method, path)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "BM_LOGIN_REQUIRED"
    assert mine(env) == [], "nothing may reach Bossman without a phone session"


@pytest.mark.parametrize("path", ["/m/api/agent/sessions/S1/compact", "/m/api/agent/sessions/S1/memory"])
def test_post_needs_csrf(authed, path):
    r = authed.c.post(path, json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    r = authed.c.post(path, json={}, headers={"X-BM-CSRF": "wrong-token-value"})
    assert r.status_code == 403
    assert mine(authed) == []


# ----------------------------------------------------------------------------------------------- upstream mapping
def test_palette_forwards_session_id_and_shape(authed):
    r = authed.c.get("/m/api/agent/palette", params={"session_id": "S1"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert [c["name"] for c in data["commands"]] == ["code", "compact", "new"]
    assert data["commands"][1]["kind"] == "action"
    assert data["tools"][1] == {"name": "cloud.ask", "group": "Cloud", "description": "Спросить сильную облачную модель",
                                "risk": "high", "needs_approval": True, "effect": "ask"}
    assert data["skills"][0]["triggers"] == ["тест", "pytest"]
    assert data["agent"] == {"id": 41, "name": "Gemma", "mode": "AGENT"}
    call = next(c for c in authed.fake.calls if c["path"] == "/api/agent/palette")
    assert call["method"] == "GET" and call["query"] == {"session_id": "S1"}
    assert f"bcc_session={SID}" in call["headers"]["cookie"] and "x-bcc-csrf" not in call["headers"]


def test_palette_without_session_sends_no_query(authed):
    assert authed.c.get("/m/api/agent/palette").status_code == 200
    call = next(c for c in authed.fake.calls if c["path"] == "/api/agent/palette")
    assert call["query"] == {}


def test_context_maps_path_and_keeps_numbers(authed):
    r = authed.c.get("/m/api/agent/sessions/S1/context")
    assert r.status_code == 200
    assert r.json() == CONTEXT
    call = next(c for c in authed.fake.calls if c["path"] == "/api/agent/sessions/S1/context")
    assert call["method"] == "GET" and call["body"] == ""


def test_compact_posts_instruction_and_answers_202(authed):
    r = authed.post("/m/api/agent/sessions/S1/compact", json={"instruction": "оставь решения по API"})
    assert r.status_code == 202
    assert r.json() == {"session_id": "S1", "task_id": "cmp-1", "status": "running"}
    assert mine(authed) == [("POST", "/api/agent/sessions/S1/compact", {"instruction": "оставь решения по API"})]
    call = next(c for c in authed.fake.calls if c["path"] == "/api/agent/sessions/S1/compact")
    assert call["headers"]["x-bcc-csrf"] == REAL_CSRF and f"bcc_session={SID}" in call["headers"]["cookie"]


def test_compact_without_body_sends_an_empty_object(authed):
    assert authed.post("/m/api/agent/sessions/S1/compact").status_code == 202
    assert mine(authed) == [("POST", "/api/agent/sessions/S1/compact", {})]
    assert authed.post("/m/api/agent/sessions/S1/compact", json={}).status_code == 202
    assert mine(authed)[-1] == ("POST", "/api/agent/sessions/S1/compact", {})


def test_memory_posts_note_and_returns_result(authed):
    r = authed.post("/m/api/agent/sessions/S1/memory", json={"note": "важное про заказ"})
    assert r.status_code == 200 and r.json() == {"ok": True, "chars": 1800, "note_id": "mem-7"}
    assert mine(authed) == [("POST", "/api/agent/sessions/S1/memory", {"note": "важное про заказ"})]
    authed.post("/m/api/agent/sessions/S1/memory", json={})
    assert mine(authed)[-1] == ("POST", "/api/agent/sessions/S1/memory", {})


def test_memory_path_variant_is_kept(authed):
    STATE["memory"] = (200, {"ok": True, "path": "memory/chat-S1.md", "chars": 40})
    assert authed.post("/m/api/agent/sessions/S1/memory", json={}).json() == {"ok": True, "chars": 40, "path": "memory/chat-S1.md"}


# ----------------------------------------------------------------------------------------------- ids and bodies
@pytest.mark.parametrize("sid", ["a b", "x..y/z", "a" * 65, "%2e%2e", "s;1", "и"])
def test_bad_session_id_is_404_and_never_forwarded(authed, sid):
    for method, suffix in (("GET", "context"), ("POST", "compact"), ("POST", "memory")):
        r = authed.c.request(method, f"/m/api/agent/sessions/{sid}/{suffix}", headers={"X-BM-CSRF": authed.csrf}, json={})
        assert r.status_code == 404, (method, suffix, sid)
    assert authed.c.get("/m/api/agent/palette", params={"session_id": sid}).status_code == 404
    assert mine(authed) == []


def test_body_is_validated_and_extra_keys_are_refused(authed):
    assert authed.post("/m/api/agent/sessions/S1/compact", json={"instruction": "x" * 2001}).status_code == 422
    assert authed.post("/m/api/agent/sessions/S1/compact", json={"instruction": "ok", "session": "other"}).status_code == 422
    assert authed.post("/m/api/agent/sessions/S1/memory", json={"note": "n" * 2001}).status_code == 422
    assert authed.post("/m/api/agent/sessions/S1/memory", json={"path": "C:/x"}).status_code == 422
    assert mine(authed) == []


def test_other_session_subpaths_stay_closed(authed):
    for p in ("/m/api/agent/sessions/S1/undo", "/m/api/agent/sessions/S1/context/raw", "/m/api/agent/palette/x"):
        r = authed.c.get(p)
        assert r.status_code == 404, p
    assert not any(c["path"].endswith(("/undo", "/raw")) for c in authed.fake.calls)


# ----------------------------------------------------------------------------------------------- error pass-through
@pytest.mark.parametrize("shape", ["error", "detail"])
@pytest.mark.parametrize("call,code", [
    (("POST", "compact"), "AGENT_NOTHING_TO_COMPACT"), (("POST", "compact"), "AGENT_COMPACT_RUNNING"),
    (("POST", "memory"), "AGENT_NO_SUMMARY")])
def test_upstream_409_codes_reach_the_phone(authed, shape, call, code):
    err = {"code": code, "message": f"сообщение {code}"}
    STATE[call[1]] = (409, {"error": err} if shape == "error" else {"detail": err})
    r = authed.post(f"/m/api/agent/sessions/S1/{call[1]}", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == code and code in r.json()["detail"]["message"]


def test_other_upstream_statuses_pass_through(authed):
    STATE["context_error"] = (404, {"error": {"code": "AGENT_SESSION_NOT_FOUND", "message": "нет такого чата"}})
    r = authed.c.get("/m/api/agent/sessions/S1/context")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "AGENT_SESSION_NOT_FOUND"
    STATE["context_error"] = (503, {"detail": {"code": "AGENT_BUSY", "message": "занято"}})
    r = authed.c.get("/m/api/agent/sessions/S1/context")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "AGENT_BUSY"


# ----------------------------------------------------------------------------------------------- privacy
def test_secrets_are_hidden_in_every_answer(authed):
    leak = f"token={BOSS_TOKEN} sid={SID} csrf={REAL_CSRF}"
    STATE["palette"] = {**PALETTE, "tools": [{**PALETTE["tools"][0], "description": leak}],
                        "commands": [{"name": "x", "usage": leak, "help": leak, "kind": "message"}]}
    STATE["compact"] = (409, {"error": {"code": "AGENT_COMPACT_RUNNING", "message": leak}})
    STATE["context"] = {**CONTEXT, "compact": {"state": "failed", "task_id": "t", "finished_at": None, "error": leak}}
    bodies = [authed.c.get("/m/api/agent/palette").text, authed.c.get("/m/api/agent/sessions/S1/context").text,
              authed.post("/m/api/agent/sessions/S1/compact", json={}).text]
    for text in bodies:
        for secret in (BOSS_TOKEN, SID, REAL_CSRF):
            assert secret not in text
    assert "[hidden]" in bodies[0] and "[hidden]" in bodies[1] and "[hidden]" in bodies[2]


def test_canary_chat_text_never_reaches_the_phone(authed):
    """An upstream bug (or a later backend change) that puts chat text into one of these answers is cut off here."""
    STATE["palette"] = {**PALETTE, "last_user_text": CANARY,
                        "commands": [{**PALETTE["commands"][0], "turn": CANARY, "extra": {"t": CANARY}}],
                        "tools": [{**PALETTE["tools"][0], "last_result": CANARY}],
                        "skills": [{**PALETTE["skills"][0], "history": [CANARY]}],
                        "agent": {"id": 41, "mode": "AGENT", "last_turn": {"text": CANARY}, "notes": [CANARY]}}
    STATE["context"] = {**CONTEXT, "turns": [{"idx": 0, "text": CANARY}], "summary": CANARY, "last_message": CANARY,
                        "compact": {"state": "done", "task_id": "t1", "finished_at": "2026-10-21T10:00:00+00:00",
                                    "error": None, "summary": CANARY, "preview": CANARY}}
    STATE["compact"] = (202, {"session_id": "S1", "task_id": "cmp-1", "status": "running", "summary": CANARY})
    STATE["memory"] = (200, {"ok": True, "note_id": "mem-7", "chars": 5, "text": CANARY, "summary": CANARY})
    bodies = [authed.c.get("/m/api/agent/palette", params={"session_id": "S1"}).text,
              authed.c.get("/m/api/agent/sessions/S1/context").text,
              authed.post("/m/api/agent/sessions/S1/compact", json={}).text,
              authed.post("/m/api/agent/sessions/S1/memory", json={}).text]
    assert all(CANARY not in b for b in bodies)
    assert json.loads(bodies[1])["compact"]["state"] == "done"            # the useful part survived the cut
    assert json.loads(bodies[0])["agent"] == {"id": 41, "mode": "AGENT"}
    # and the gateway itself never went to read a chat to answer
    assert not any(c["path"].startswith("/api/chat/threads") or c["path"] == "/api/agent/sessions/S1" for c in authed.fake.calls)


def test_manifest_lists_the_new_calls():
    text = (ROOT / "app.manifest.yaml").read_text(encoding="utf-8")
    for needle in ("agent.palette: GET /m/api/agent/palette", "agent.context: GET /m/api/agent/sessions/{id}/context",
                   "agent.compact: POST /m/api/agent/sessions/{id}/compact", "agent.memory: POST /m/api/agent/sessions/{id}/memory"):
        assert needle in text


# ----------------------------------------------------------------------------------------------- projection (pure)
def test_context_view_coerces_wrong_types_and_unknown_enums():
    out = slash.context_view({"used_pct": "high", "left_tokens": True, "level": "red", "source": "guess",
                              "turns_total": 3.0, "compacted": "yes", "compact": {"state": "exploded", "error": "x" * 999}})
    assert out["used_pct"] is None and out["left_tokens"] is None and out["level"] == "unknown" and out["source"] is None
    assert out["turns_total"] == 3 and out["compacted"] is False
    assert out["compact"]["state"] == "idle" and len(out["compact"]["error"]) == 300
    assert slash.context_view(None)["level"] == "unknown" and slash.context_view([1])["compact"]["state"] == "idle"


def test_palette_view_drops_nameless_rows_and_normalises():
    out = slash.palette_view({"commands": [{"name": "/help", "kind": "client"}, {"help": "no name"}, "junk"],
                              "tools": [{"name": ""}], "skills": [{"id": "s", "triggers": "один"}, {"title": "no id"}]})
    assert [c["name"] for c in out["commands"]] == ["help"] and out["tools"] == []
    assert out["skills"] == [{"id": "s", "title": "", "status": "", "triggers": ["один"]}]
    assert out["agent"] is None


def test_compact_and_memory_views():
    assert slash.compact_view({"session_id": "S1", "task_id": 7, "status": "weird"}) == {"session_id": "S1", "task_id": 7, "status": "running"}
    assert slash.memory_view({"ok": 1, "chars": "3"}) == {"ok": False, "chars": None}
