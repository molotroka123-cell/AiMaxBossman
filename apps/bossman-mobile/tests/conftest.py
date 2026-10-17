import json
import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bossmobile.api import create_app  # noqa: E402
from bossmobile.config import Settings  # noqa: E402

BOSS_TOKEN = "BOSSMAN-OWNER-TOKEN-0123456789abcdef"
SID = "UPSTREAM-SESSION-ID-abcdef0123456789"
REAL_CSRF = "UPSTREAM-CSRF-TOKEN-fedcba9876543210"
PAIR = "pairing-token-for-tests-123"

OPTIONS = {"agents": [
    {"id": 1, "name": "Qwen Оператор (компьютер)", "role": "", "enabled": True,
     "model": {"id": 11, "alias": "local-qwen", "name": "qwen:latest", "kind": "local", "locality": "local",
               "locality_detail": "local", "billing": "local", "usable": True, "refusal": ""}},
    {"id": 2, "name": "Пилот", "role": "", "enabled": True,
     "model": {"id": 11, "alias": "local-qwen", "name": "qwen:latest", "kind": "local", "locality": "local",
               "locality_detail": "local", "billing": "local", "usable": True, "refusal": ""}},
    {"id": 3, "name": "Nemotron", "role": "", "enabled": True,
     "model": {"id": 12, "alias": "nemotron", "name": "nvidia/nemotron:free", "kind": "cloud", "locality": "cloud",
               "locality_detail": "cloud", "billing": "free_cloud", "usable": True, "refusal": ""}},
], "auto": {"agent": None}}
AGENTS = [{"id": 1, "tools": ["computer.observe", "computer.act"], "fallback_model_id": None, "permissions": {}},
          {"id": 2, "tools": [], "fallback_model_id": 12, "permissions": {}},
          {"id": 3, "tools": [], "fallback_model_id": None, "permissions": {}}]
MODELS = [{"id": 11, "kind": "local", "provider_id": 1}, {"id": 12, "kind": "cloud", "provider_id": 2}]
PROVIDERS = [{"id": 1, "base_url": "http://127.0.0.1:11434/v1"}, {"id": 2, "base_url": "https://openrouter.ai/api/v1"}]
DG_MODELS = {"models": [
    {"id": "z-image-turbo", "label": "Z-Image Turbo", "kind": "photo", "modes": ["T2I"], "available": True, "reason": "",
     "requires_image": False, "accepts_image": False, "default_steps": 8, "max_steps": 20, "default_resolution": "512x512",
     "min_side": 256, "max_side": 1536, "side_multiple": 16, "weights": [{"file": "secret-path.gguf"}]},
    {"id": "qwen-image-2.1-uc", "label": "Qwen-Image 2.1 UC (режим «uncensored»)", "kind": "photo", "modes": ["T2I"],
     "available": True, "reason": "", "requires_image": False, "accepts_image": False},
    {"id": "wan2.2-ti2v-5b", "label": "Wan2.2", "kind": "video", "modes": ["T2V"], "available": True}]}
JOB = {"job_id": "a" * 32, "model": "z-image-turbo", "kind": "photo", "status": "completed", "stage": "completed",
       "raw_prompt": "горы", "params": {"width": 512, "height": 512, "seed": 1}, "progress": {"kind": "steps", "percent": 100},
       "result": {"mime": "image/png", "file": "image.png"}, "elapsed_s": 20.0, "provenance": {"command": ["sd-cli"]}}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeBossman:
    def __init__(self):
        self.calls: list[dict] = []
        self.sessions = {SID}
        self.logins = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = request.content.decode("utf-8", "replace") if request.content else ""
        self.calls.append({"method": method, "path": path, "query": dict(request.url.params), "body": body,
                           "headers": dict(request.headers)})
        if path == "/health/live":
            return httpx.Response(200, json={"alive": True, "build_sha_short": "586345db8270", "status": "ALIVE"})
        if path == "/api/login":
            if json.loads(body).get("token") != BOSS_TOKEN:
                return httpx.Response(401, json={"error": {"message": "неверный токен"}})
            self.logins += 1
            self.sessions.add(SID)
            return httpx.Response(200, json={"ok": True, "csrf": REAL_CSRF},
                                  headers={"set-cookie": f"bcc_session={SID}; HttpOnly; Path=/; SameSite=Strict"})
        if path in ("/", "/index.html") and method == "GET" or path.endswith(".js") or path.endswith(".css"):
            if path.endswith(".js"):
                return httpx.Response(200, text="console.log('ui')", headers={"content-type": "text/javascript"})
            return httpx.Response(200, text="<!doctype html><html><head><title>BCC</title></head><body>panel</body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"})
        cookie = request.headers.get("cookie", "")
        if f"bcc_session={SID}" not in cookie or SID not in self.sessions:
            return httpx.Response(401, json={"error": {"message": "нужна аутентификация"}})
        if method not in ("GET", "HEAD") and request.headers.get("x-bcc-csrf") != REAL_CSRF:
            return httpx.Response(403, json={"error": {"message": "csrf", "code": "csrf"}})
        if path == "/api/chat/options":
            return httpx.Response(200, json=OPTIONS)
        if path == "/api/agents":
            return httpx.Response(200, json=AGENTS)
        if path == "/api/models":
            return httpx.Response(200, json=MODELS)
        if path == "/api/providers":
            return httpx.Response(200, json=PROVIDERS)
        if path == "/api/chat/threads" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "T1", "title": "t", "turns": 1}], "total": 1})
        if path == "/api/chat/threads" and method == "POST":
            return httpx.Response(201, json={"id": "T2", "title": "new"})
        if path == "/api/chat/threads/T1" and method == "GET":
            return httpx.Response(200, json={"id": "T1", "turns": [{"idx": 0, "task_id": 5, "text": "hi", "status": "completed",
                                                                     "result": "ok"}]})
        if path == "/api/chat/threads/T1/send":
            return httpx.Response(200, json={"task": {"id": 7, "status": "queued"}, "admission": {"ok": True},
                                             "model": {"alias": "local-qwen"}})
        if path == "/api/chat/threads/T1/stop":
            return httpx.Response(200, json={"ok": True, "task_id": 7, "status": "stopped"})
        if path == "/api/tasks/7":
            return httpx.Response(200, json={"task": {"id": 7, "status": "completed", "agent_id": 1, "prompt": "p"},
                                             "result": "готово", "error": None, "runs": []})
        if path == "/api/events/stream":
            frames = [
                {"kind": "stream.open", "task_id": 7},
                {"kind": "run.tool_use", "seq": 1, "task_id": 7, "call_id": "c1", "tool": "computer.observe", "args": {}},
                {"kind": "run.tool_result", "seq": 2, "task_id": 7, "call_id": "c1", "tool": "computer.observe", "ok": True,
                 "summary": "3 окна", "preview": f"leak {BOSS_TOKEN} end"},
                {"kind": "system.metrics", "seq": 3, "cpu": 5},
                {"kind": "run.answer_delta", "seq": 4, "task_id": 7, "text": "Открыто 3 окна"},
                {"kind": "task.completed", "seq": 5, "task_id": 7, "secret_field": "x"},
            ]
            text = ": keepalive\n\n" + "".join(f"id: {f.get('seq', 0)}\ndata: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames)
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        if path == "/api/tasks/7/events":
            return httpx.Response(200, json={"task_id": 7, "status": "completed", "events": [
                {"kind": "run.tool_use", "seq": 1, "call_id": "c1", "tool": "computer.observe", "args": {}},
                {"kind": "system.metrics", "seq": 2}]})
        if path == "/api/approvals" and method == "GET":
            return httpx.Response(200, json=[{"id": 9, "kind": "tool", "preview": "computer.act click", "status": "pending",
                                              "task_id": 7, "secret": "x"}])
        if path == "/api/approvals/9" and method == "POST":
            return httpx.Response(200, json={"id": 9, "status": "rejected", "kind": "tool", "task_id": 7, "lease": None})
        if path == "/api/direct-gen/models":
            return httpx.Response(200, json=DG_MODELS)
        if path == "/api/direct-gen/status":
            return httpx.Response(200, json={"queue": {"running": None, "waiting": 0}, "sd_cli": {"present": True}})
        if path == "/api/direct-gen/jobs" and method == "POST":
            return httpx.Response(202, json={**JOB, "status": "queued", "stage": "queued"})
        if path == "/api/direct-gen/jobs" and method == "GET":
            return httpx.Response(200, json={"jobs": [JOB, {**JOB, "job_id": "b" * 32, "kind": "video"}]})
        if path == f"/api/direct-gen/jobs/{'a' * 32}":
            return httpx.Response(200, json=JOB)
        if path == f"/api/direct-gen/jobs/{'a' * 32}/file":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        # ---- panel-only endpoints (generic proxy)
        if path == "/api/echo":
            return httpx.Response(200, json={"method": method, "body": body, "query": dict(request.url.params)})
        if path == "/api/leak":
            return httpx.Response(200, text=f"token={BOSS_TOKEN} sid={SID} csrf={REAL_CSRF}",
                                  headers={"content-type": "text/plain", "set-cookie": f"bcc_session={SID}; Path=/",
                                           "x-debug": f"sid={SID}"})
        if path == "/api/redir":
            return httpx.Response(307, headers={"location": "http://127.0.0.1:8801/api/elsewhere"})
        if path == "/api/system":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": {"message": "not found"}})


def fake_ollama(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "ace-decider-08b:latest", "size": 1, "details": {"parameter_size": "0.8B"}}]})
    if request.url.path == "/api/ps":
        return httpx.Response(200, json={"models": []})
    return httpx.Response(404)


def war_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


class Env:
    def __init__(self, tmp_path):
        tok = tmp_path / "bcc-token"
        tok.write_text(BOSS_TOKEN, encoding="utf-8")
        self.fake = FakeBossman()
        st = Settings(bossman_url="http://127.0.0.1:8801", token_file=tok, data_dir=tmp_path / "mobile")
        self.app = create_app(st, bossman_transport=httpx.MockTransport(self.fake.handler),
                              ollama_transport=httpx.MockTransport(fake_ollama), war_transport=httpx.MockTransport(war_down),
                              pairing_token=PAIR)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8840", client=("127.0.0.1", 50000))
        self.csrf = ""

    def login(self):
        r = self.c.post("/m/api/login", json={"token": PAIR})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf"]
        return r

    def post(self, path, **kw):
        headers = kw.pop("headers", {})
        headers.setdefault("X-BM-CSRF", self.csrf)
        return self.c.post(path, headers=headers, **kw)

    def panel_csrf(self):
        row = next(iter(self.app.state.sessions._rows.values()))
        return row["panel_csrf"]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def authed(env):
    env.login()
    return env
