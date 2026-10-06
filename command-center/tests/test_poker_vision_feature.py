"""Bossman <-> Poker Vision: the feature is a loopback proxy + lifecycle glue over the ONE poker-vision backend.

A real poker-vision service (uvicorn, loopback) is started; Bossman's own API is exercised through its real app."""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "poker-vision"
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("uvicorn")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def pv_service(tmp_path, monkeypatch):
    import uvicorn
    sys.path.insert(0, str(APP_DIR))
    from pokervision.api import create_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "pv"), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close(); break
        except OSError:
            time.sleep(0.05)
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{port}")
    yield port
    server.should_exit = True; t.join(5)


def _frames(d: Path, n: int) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cv2.imwrite(str(d / f"{i:04d}.png"), np.full((900, 520, 3), 20, np.uint8))
    return d


async def test_status_reports_service_down_and_session_is_503(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{_free_port()}")
    st = (await env.client.get("/api/poker-vision/status")).json()
    assert st["service_up"] is False and st["session"] is None
    r = await env.client.post("/api/poker-vision/session", json={"mode": "replay", "path": "/x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "PV_SERVICE_DOWN"


async def test_override_must_be_loopback(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", "http://evil.example.com:80")
    r = await env.client.get("/api/poker-vision/state")
    assert r.status_code == 500 and r.json()["error"]["code"] == "PV_BAD_OVERRIDE"


async def test_replay_through_bossman_then_stop(env, pv_service, tmp_path):
    frames = _frames(tmp_path / "f", 300)
    caps = (await env.client.get("/api/poker-vision/capabilities")).json()
    assert caps["adapters"]["ton_poker"]["capabilities"]["act"] is False
    r = await env.client.post("/api/poker-vision/session", json={"mode": "replay", "adapter": "poker_train", "path": str(frames), "interval_s": 0.03})
    assert r.status_code == 200, r.text
    time.sleep(0.5)
    ov = await env.client.get("/api/poker-vision/overlay.png")
    assert ov.status_code == 200 and ov.headers["content-type"] == "image/png"
    stop = (await env.client.post("/api/poker-vision/stop")).json()
    assert stop["stopped"] is True and stop["via"] == "app" and stop["status"]["running"] is False
    n = stop["status"]["frames"]
    assert n < 300
    time.sleep(0.2)
    assert (await env.client.get("/api/poker-vision/status")).json()["session"]["frames"] == n


@pytest.mark.parametrize("body", [
    {"mode": "trainer", "adapter": "poker_train", "url": "https://www.example.com/", "act": True},
    {"mode": "trainer", "adapter": "ton_poker", "url": "http://127.0.0.1:3000/"},
    {"mode": "window", "adapter": "ton_poker", "act": True, "window": {"rect": [0, 0, 400, 400]}},
])
async def test_policy_refusals_pass_through(env, pv_service, body):
    r = await env.client.post("/api/poker-vision/session", json=body)
    assert r.status_code in (400, 403), r.text


async def test_stop_falls_back_to_process_control_when_service_is_gone(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{_free_port()}")
    r = (await env.client.post("/api/poker-vision/stop")).json()
    assert r["via"] in ("process", "none") and r["stopped"] is True


async def test_session_body_is_validated(env, pv_service):
    r = await env.client.post("/api/poker-vision/session", json={"mode": "rm -rf", "adapter": "../x"})
    assert r.status_code == 422


async def test_tree_sync_writes_journal_for_the_pv_node(env, tmp_path, monkeypatch):
    from bcc.features import capability_tree as tree
    seed = tmp_path / "seed.json"
    import json as _j
    seed.write_text(_j.dumps({"schema_version": "1.0", "nodes": [{"id": "bossman", "parent": "", "status": "blocked", "sources": []},
                                                                  {"id": "pv", "parent": "bossman", "status": "code", "sources": []}]}), encoding="utf-8")
    monkeypatch.setattr(tree, "PACKAGE_MAP", seed)
    body = {"task": "Poker Vision holdout", "sha": "a" * 40, "run": "run-1", "metrics": {"card_error": 0.0}, "blockers": ["TON: no recordings"], "state": "blocked"}
    r = await env.client.post("/api/poker-vision/tree-sync", json=body)
    assert r.status_code == 200
    notes = tree._read(tree._tree_dir(env.svc) / "owner-notes.json", {})
    assert notes["pv"]["state"] == "blocked" and "TON: no recordings" in notes["pv"]["text"] and "a" * 40 in notes["pv"]["text"]
    bad = await env.client.post("/api/poker-vision/tree-sync", json={**body, "sha": "not-a-sha"})
    assert bad.status_code == 422
