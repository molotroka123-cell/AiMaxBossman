"""Bossman <-> War Terminal: Bossman acts as ANALYST through the versioned API; owner-only actions are not proxied."""
from __future__ import annotations

import pathlib
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "apps" / "war-terminal"
sys.path.insert(0, str(APP_DIR))

from warterminal.api import create_app  # noqa: E402

from bcc.features import war_terminal  # noqa: E402
from bcc.tools import REGISTRY  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    port = _free_port()
    app = create_app(tmp_path / "wt", live=False, today=None)           # tokens file is generated, as in real use
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    monkeypatch.setenv("BOSSMAN_WAR_TERMINAL_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("WAR_TERMINAL_DATA", str(tmp_path / "wt"))
    tokens = app.state.tokens
    httpx.post(f"http://127.0.0.1:{port}/api/v1/ingest", json={"source": "demo"}, headers={"Authorization": f"Bearer {tokens['analyst']}"}, timeout=10)
    yield app
    server.should_exit = True
    th.join(timeout=5)


async def test_bossman_sees_the_terminal_and_analyses_without_persisting(env, terminal):
    st = (await env.client.get("/api/war-terminal/status")).json()
    assert st["service_up"] is True and st["bossman_role"] == "analyst" and st["health"]["mode"] == "DEMO"
    assert "token" not in str(st).lower()
    listed = (await env.client.get("/api/war-terminal/tenders?include_demo=true")).json()
    assert listed["total"] >= 7 and all(i["demo"] for i in listed["items"])
    a = await env.client.post("/api/war-terminal/analyze", json={"tender_key": "EU:DEMO-001"})
    assert a.status_code == 200 and a.json()["dry_run"] is True and "id" not in a.json()
    assert terminal.state.store.docs("assessments") == []                   # dry-run: nothing stored in the terminal
    chk = (await env.client.get("/api/war-terminal/tenders/EU:DEMO-001/checklist")).json()
    assert chk["draft"] is True


async def test_owner_only_actions_are_not_reachable_from_bossman(env, terminal):
    for method, path in (("post", "/api/war-terminal/approvals/1/decide"), ("patch", "/api/war-terminal/company/profile"),
                         ("post", "/api/war-terminal/actions/submit-bid"), ("post", "/api/war-terminal/assessments/1/review")):
        r = await getattr(env.client, method)(path, json={})
        assert r.status_code in (404, 405), (path, r.status_code)
    refresh = await env.client.post("/api/war-terminal/refresh", json={"source": "ted"})
    assert refresh.status_code == 403 and "LIVE_NEEDS_OWNER_CONFIRM" in refresh.text
    confirmed = await env.client.post("/api/war-terminal/refresh", json={"source": "ted", "confirm_live": True})
    assert confirmed.status_code == 403 and "LIVE_DISABLED" in confirmed.text       # terminal itself still has live off


async def test_service_down_is_reported_not_hidden(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_WAR_TERMINAL_URL", f"http://127.0.0.1:{_free_port()}")
    monkeypatch.setenv("BOSSMAN_WAR_TERMINAL_TOKEN", "x")
    assert (await env.client.get("/api/war-terminal/status")).json()["service_up"] is False
    r = await env.client.get("/api/war-terminal/tenders")
    assert r.status_code == 503 and "WT_SERVICE_DOWN" in r.text


async def test_model_tools_are_registered_read_only_and_work(env, terminal):
    await war_terminal._setup(env.svc)
    names = {"war_terminal.analyze_tender", "war_terminal.check_compliance", "war_terminal.summarize_conflict",
             "war_terminal.estimate_cashflow", "war_terminal.draft_bid_checklist"}
    for n in names:
        spec = REGISTRY.get(n)
        assert spec is not None and spec.category == "read" and spec.external_output is True
    res = await REGISTRY.get("war_terminal.check_compliance").handler({"tender_key": "EU:DEMO-001"}, None)
    assert res.error is False and "REVIEW_REQUIRED" in res.content
    bad = await REGISTRY.get("war_terminal.analyze_tender").handler({"tender_key": "EU:NOPE"}, None)
    assert bad.error is True


# ------------------------------------------------------------------ free-only chat gateway used by the terminal's translation
def test_free_chat_refusal_is_strict():
    ok = {"price_in": 0.0, "price_out": 0.0, "caps": {"free_tier": "experiential_labs_free_plan"}, "kind": "cloud"}
    cloud = {"base_url": "https://api.experientiallabs.ai/v1", "kind": "openai_compat"}
    assert war_terminal.free_chat_refusal(ok, cloud) == ""
    assert war_terminal.free_chat_refusal({**ok, "price_in": 0.3}, cloud)                      # priced -> refused
    assert war_terminal.free_chat_refusal({**ok, "price_in": None}, cloud)                     # unknown price is not proof
    assert war_terminal.free_chat_refusal({**ok, "caps": {}}, cloud)                           # 0/0 typed by hand is not a free tier
    assert war_terminal.free_chat_refusal({**ok, "caps": {"free_tier": "x", "paid": True}}, cloud)
    assert war_terminal.free_chat_refusal(ok, {"base_url": "http://127.0.0.1:11434/v1", "kind": "openai_compat"})   # local GPU model
    assert war_terminal.free_chat_refusal(None, cloud)


async def test_free_chat_gateway_serves_free_and_refuses_paid_and_local(env):
    from .conftest import FakeAdapter
    reg = env.svc.registry
    cloud = await reg.create_provider("explabs-test", "openai_compat", "https://api.experientiallabs.ai/v1", "k")
    free = await reg.create_model(provider_id=cloud["id"], name="m-free", alias="t-free", kind="cloud", caps={"free_tier": "experiential_labs_free_plan"}, price_in=0.0, price_out=0.0)
    paid = await reg.create_model(provider_id=cloud["id"], name="m-paid", alias="t-paid", kind="cloud", caps={"free_tier": "x"}, price_in=0.3, price_out=0.9)
    local = await reg.create_provider("local-test", "openai_compat", "http://127.0.0.1:11434/v1", None)
    loc = await reg.create_model(provider_id=local["id"], name="m-local", alias="t-local", kind="local", caps={"free_tier": "x"}, price_in=0.0, price_out=0.0)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter('{"items":[]}')
    body = {"messages": [{"role": "user", "content": "x"}]}
    r = await env.client.post("/api/war-terminal/free-chat", json={"model_id": free["id"], **body})
    assert r.status_code == 200 and r.json()["text"] == '{"items":[]}' and r.json()["model"] == "t-free" and "cost" in r.json()["usage"]
    for mid in (paid["id"], loc["id"], 99999):
        assert (await env.client.post("/api/war-terminal/free-chat", json={"model_id": mid, **body})).status_code == 403
    bad = await env.client.post("/api/war-terminal/free-chat", json={"model_id": free["id"], "messages": [{"role": "assistant", "content": "x"}]})
    assert bad.status_code == 422
