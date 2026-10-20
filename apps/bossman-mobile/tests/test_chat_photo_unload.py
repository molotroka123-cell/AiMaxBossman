"""POST /m/api/chat/unload (owner 19.10, «Фото по описанию»): before a photo job from the chat, the chat model's
own local Ollama model is unloaded (keep_alive=0) so the GPU/RAM go to the image model. Nothing else is unloaded,
cloud agents are refused, a dead Ollama is reported in Russian, and the photo pipeline itself is not changed."""
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from bossmobile.api import create_app
from bossmobile.config import Settings
from conftest import BOSS_TOKEN, PAIR, FakeBossman, war_down


class FakeOllama:
    def __init__(self, loaded=("qwen:latest", "z-other:7b"), down=False, unload_status=200):
        self.loaded, self.down, self.unload_status = list(loaded), down, unload_status
        self.calls: list[tuple[str, str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": n, "model": n} for n in self.loaded]})
        if request.url.path == "/api/generate":
            if self.unload_status == 200:
                self.loaded = [n for n in self.loaded if n != body["model"]]
            return httpx.Response(self.unload_status, json={"done": True, "done_reason": "unload"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)


def make(tmp_path, ollama: FakeOllama, ollama_url="http://127.0.0.1:11434"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tok = tmp_path / "bcc-token"
    tok.write_text(BOSS_TOKEN, encoding="utf-8")
    fake = FakeBossman()
    st = Settings(bossman_url="http://127.0.0.1:8801", token_file=tok, data_dir=tmp_path / "mobile", ollama_url=ollama_url)
    app = create_app(st, bossman_transport=httpx.MockTransport(fake.handler), ollama_transport=httpx.MockTransport(ollama.handler),
                     war_transport=httpx.MockTransport(war_down), pairing_token=PAIR)
    c = TestClient(app, base_url="http://127.0.0.1:8840", client=("127.0.0.1", 50000))
    csrf = c.post("/m/api/login", json={"token": PAIR}).json()["csrf"]
    return c, csrf, fake


def test_unloads_only_the_agents_own_loaded_model(tmp_path):
    ol = FakeOllama()
    c, csrf, _ = make(tmp_path, ol)
    r = c.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "unloaded": ["qwen:latest"], "was_loaded": True}
    posts = [b for m, p, b in ol.calls if m == "POST"]
    assert posts == [{"model": "qwen:latest", "keep_alive": 0}], "only the chat agent's model, keep_alive 0"
    assert ol.loaded == ["z-other:7b"], "another workload's model is left alone"


def test_already_unloaded_is_ok_and_sends_nothing(tmp_path):
    ol = FakeOllama(loaded=["z-other:7b"])
    c, csrf, _ = make(tmp_path, ol)
    r = c.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf})
    assert r.status_code == 200 and r.json()["unloaded"] == [] and r.json()["was_loaded"] is False
    assert not [1 for m, _p, _b in ol.calls if m == "POST"]


def test_cloud_or_unknown_agent_is_refused(tmp_path):
    ol = FakeOllama()
    c, csrf, _ = make(tmp_path, ol)
    for agent in (3, 2, 999):          # 3: cloud model, 2: cloud fallback, 999: no such agent
        r = c.post("/m/api/chat/unload", json={"agent_id": agent}, headers={"X-BM-CSRF": csrf})
        assert r.status_code == 403 and "локальную модель" in r.json()["detail"]["message"]
    assert ol.calls == [], "Ollama is not even asked for a refused agent"


def test_ollama_down_or_refusing_is_reported_in_russian(tmp_path):
    c, csrf, _ = make(tmp_path, FakeOllama(down=True))
    r = c.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf})
    assert r.status_code == 502 and r.json()["detail"]["message"] == "Ollama не отвечает — модель чата не выгружена"
    c2, csrf2, _ = make(tmp_path / "b", FakeOllama(unload_status=500))
    r2 = c2.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf2})
    assert r2.status_code == 502 and "не выгрузил qwen:latest" in r2.json()["detail"]["message"]


def test_needs_session_csrf_and_a_loopback_ollama(tmp_path):
    ol = FakeOllama()
    c, csrf, _ = make(tmp_path, ol)
    assert c.post("/m/api/chat/unload", json={"agent_id": 1}).status_code == 403          # no CSRF header
    c.cookies.clear()
    assert c.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf}).status_code == 401
    lan = FakeOllama()
    c3, csrf3, _ = make(tmp_path / "lan", lan, ollama_url="http://192.168.1.20:11434")
    r = c3.post("/m/api/chat/unload", json={"agent_id": 1}, headers={"X-BM-CSRF": csrf3})
    assert r.status_code == 503 and lan.calls == []


def test_photo_job_from_the_chat_uses_the_unchanged_photo_pipeline(tmp_path):
    """The chat flow posts the same body the Фото tab posts; the gateway forwards it as before (mode DIRECT)."""
    c, csrf, fake = make(tmp_path, FakeOllama())
    r = c.post("/m/api/photo/jobs", json={"model": "z-image-turbo", "prompt": "a red fox in snow", "resolution": "512x512"},
               headers={"X-BM-CSRF": csrf})
    assert r.status_code == 202, r.text
    sent = [json.loads(x["body"]) for x in fake.calls if x["path"] == "/api/direct-gen/jobs" and x["method"] == "POST"]
    assert sent == [{"model": "z-image-turbo", "prompt": "a red fox in snow", "negative": "", "mode": "DIRECT", "resolution": "512x512"}]
