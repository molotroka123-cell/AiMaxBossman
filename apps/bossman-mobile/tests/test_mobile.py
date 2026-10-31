import json

import pytest
from starlette.websockets import WebSocketDisconnect

from bossmobile.auth import load_or_create_pairing
from bossmobile.bossman import Redactor
from bossmobile.policy import local_agents, pick_default
from conftest import AGENTS, BOSS_TOKEN, MODELS, OPTIONS, PAIR, PNG, PROVIDERS, REAL_CSRF, SID

SECRETS = (BOSS_TOKEN, SID, REAL_CSRF)


def assert_clean(resp):
    blob = resp.text + json.dumps(dict(resp.headers))
    for s in SECRETS:
        assert s not in blob, f"secret leaked in {resp.request.url}"


# ------------------------------------------------------------------ auth
def test_health_is_public(env):
    r = env.c.get("/m/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.parametrize("path", ["/m/api/status", "/m/api/agents", "/m/api/threads", "/m/api/photo/models",
                                  "/m/api/approvals", "/m/api/session", "/panel/", "/panel/api/system", "/api/system",
                                  "/api/tasks"])
def test_no_cookie_is_401(env, path):
    r = env.c.get(path)
    assert r.status_code == 401
    assert env.fake.calls == [], "nothing may reach Bossman without a phone session"


def test_wrong_token_401_then_rate_limited_429(env):
    for _ in range(5):
        assert env.c.post("/m/api/login", json={"token": "nope"}).status_code == 401
    r = env.c.post("/m/api/login", json={"token": "nope"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    # the limit is checked BEFORE the token: even the right token waits now (no oracle for brute force)
    assert env.c.post("/m/api/login", json={"token": PAIR}).status_code == 429


def test_rate_limit_is_per_forwarded_client(env):
    for _ in range(5):
        env.c.post("/m/api/login", json={"token": "x"}, headers={"X-Forwarded-For": "100.64.0.9"})
    assert env.c.post("/m/api/login", json={"token": "x"}, headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 429
    assert env.c.post("/m/api/login", json={"token": PAIR}, headers={"X-Forwarded-For": "100.64.0.10"}).status_code == 200


def test_global_failed_logins_cannot_lock_out_legitimate_owner(env):
    """Security audit 31.10 (1d): even if 40+ failed login attempts arrive from other
    forwarded keys (> old global limit of 30), the owner's client key can still log in."""
    for idx in range(12):
        ip = f"100.64.99.{idx + 1}"
        for _ in range(4):
            assert env.c.post("/m/api/login", json={"token": "wrong"}, headers={"X-Forwarded-For": ip}).status_code == 401
    # 48 failed attempts globally; owner's phone (100.64.0.77) must NOT be locked out
    r = env.c.post("/m/api/login", json={"token": PAIR}, headers={"X-Forwarded-For": "100.64.0.77"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_mobile_auto_full_card_proxy(authed):
    """Item 2: Mobile Pult Auto Full Owner Card endpoints (selected folder, expiry, action log, OFF button)."""
    st = authed.c.get("/m/api/auto-full")
    assert st.status_code == 200 and st.json()["active"] is True
    assert len(st.json()["action_log"]) == 1
    on = authed.post("/m/api/auto-full", json={"folder": "C:\\work\\demo", "ttl_seconds": 1800, "max_uses": 100})
    assert on.status_code == 200 and on.json()["current"]["folder"] == "C:\\work\\demo"
    off = authed.post("/m/api/auto-full/revoke", json={"lease_id": 42})
    assert off.status_code == 200 and off.json()["ok"] is True and off.json()["active"] is False


def test_login_cookie_flags(env):
    r = env.c.post("/m/api/login", json={"token": PAIR})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "secure" not in sc
    r2 = env.c.post("/m/api/login", json={"token": PAIR}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r2.headers["set-cookie"].lower()
    assert PAIR not in r.text


def test_post_needs_csrf_header(authed):
    r = authed.c.post("/m/api/threads", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    r = authed.c.post("/m/api/threads", json={}, headers={"X-BM-CSRF": "wrong"})
    assert r.status_code == 403
    assert authed.post("/m/api/threads", json={}).status_code == 201


def test_logout_revokes(authed):
    assert authed.post("/m/api/logout").status_code == 200
    assert authed.c.get("/m/api/status").status_code == 401


def test_pairing_token_file(tmp_path):
    tok = load_or_create_pairing(tmp_path)
    assert len(tok) >= 20 and load_or_create_pairing(tmp_path) == tok
    raw_text = (tmp_path / "tokens.json").read_text(encoding="utf-8")
    doc = json.loads(raw_text)
    assert "dpapi_b64" in doc and tok not in raw_text



# ------------------------------------------------------------------ local-only chat
def test_policy_hides_cloud_and_cloud_fallback():
    items = local_agents(OPTIONS, AGENTS, MODELS, PROVIDERS)
    by = {a["id"]: a for a in items}
    assert by[1]["local"] is True
    assert by[2]["local"] is False and "запасная модель облачная" in by[2]["why_not"]
    assert by[3]["local"] is False and "модель не локальная" in by[3]["why_not"]
    assert pick_default(items) == 1


def test_default_prefers_local_uncensored_never_cloud_uncensored():
    """owner 17.10: the uncensored local agent is the default; negative control: a cloud agent named uncensored
    (or a disabled-by-policy one) is not picked even though it sorts first."""
    items = [{"id": 5, "name": "Cloud chat (uncensored)", "tools": [], "local": False},
             {"id": 1, "name": "Qwen Оператор (компьютер)", "tools": ["computer.observe"], "local": True},
             {"id": 39, "name": "Qwen-Operator L1 (uncensored)", "tools": ["read_file"], "local": True}]
    assert pick_default(items) == 39
    assert pick_default([items[0], items[1]]) == 1


def test_default_prefers_local_gemma_uncensored_over_qwen_uncensored():
    """owner 17.10 «установи Gemma в мобильный app»: the local Gemma 4 uncensored chat agent beats the local Qwen
    uncensored operator (39) whatever the order; negative controls: a cloud Gemma uncensored agent and a local Gemma
    without «uncensored» do not take the default from agent 39."""
    qwen = {"id": 39, "name": "Qwen-Operator L1 (uncensored)", "tools": ["read_file"], "local": True}
    gemma = {"id": 41, "name": "Gemma 4 uncensored", "tools": [], "local": True}
    cloud_gemma = {"id": 7, "name": "Gemma 4 uncensored (cloud)", "tools": [], "local": False}
    plain_gemma = {"id": 8, "name": "Gemma 4", "tools": [], "local": True}
    assert pick_default([qwen, gemma]) == 41
    assert pick_default([gemma, qwen]) == 41
    assert pick_default([cloud_gemma, plain_gemma, qwen]) == 39


def test_agents_endpoint_lists_opted_in_cloud_first_then_local(authed):
    r = authed.c.get("/m/api/agents")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [a["id"] for a in items] == [4, 1] and items[0]["provider"] == "7API" and items[0]["cloud"] is True
    assert r.json()["default_agent_id"] == 1 and r.json()["hidden_not_local"] == 2


@pytest.mark.parametrize("agent_id", [3, 2, None, 999])
def test_send_refuses_non_local(authed, agent_id):
    """negative control: a cloud agent, a local agent with a cloud fallback, Auto and an unknown id -> 403."""
    r = authed.post("/m/api/threads/T1/send", json={"text": "привет", "agent_id": agent_id})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "BM_LOCAL_ONLY"
    assert not [c for c in authed.fake.calls if c["path"].endswith("/send")], "refused turn must not reach Bossman"


def test_send_local_passes_through_bossman_chat(authed):
    r = authed.post("/m/api/threads/T1/send", json={"text": "привет", "agent_id": 1, "client_request_id": "m-abcdef12"})
    assert r.status_code == 200 and r.json()["task"]["id"] == 7
    sent = [c for c in authed.fake.calls if c["path"] == "/api/chat/threads/T1/send"]
    assert len(sent) == 1 and json.loads(sent[0]["body"]) == {"text": "привет", "agent_id": 1, "client_request_id": "m-abcdef12"}
    assert sent[0]["headers"]["x-bcc-csrf"] == REAL_CSRF


def test_bad_thread_id_404(authed):
    assert authed.c.get("/m/api/threads/..%2F..%2Fapi%2Fproviders").status_code == 404


# ------------------------------------------------------------------ allowlist
@pytest.mark.parametrize("method,path", [("GET", "/m/api/providers"), ("GET", "/m/api/admin/keys"),
                                         ("POST", "/m/api/tasks/7/run"), ("DELETE", "/m/api/threads/T1"),
                                         ("GET", "/m/api/direct-gen/vault/prompts")])
def test_unknown_mobile_paths_404(authed, method, path):
    before = len(authed.fake.calls)
    r = authed.c.request(method, path, headers={"X-BM-CSRF": authed.csrf})
    assert r.status_code in (404, 405)
    assert len(authed.fake.calls) == before


# ------------------------------------------------------------------ streaming + secrets
def test_sse_is_filtered_and_redacted(authed):
    r = authed.c.get("/m/api/tasks/7/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    kinds = [e["kind"] for e in events]
    assert kinds == ["stream.open", "run.tool_use", "run.tool_result", "run.answer_delta", "task.completed"]
    assert "system.metrics" not in kinds and "secret_field" not in events[-1]
    assert "[hidden]" in events[2]["preview"]
    assert_clean(r)


def test_token_never_in_any_response(authed):
    paths = ["/m/api/status", "/m/api/agents", "/m/api/threads", "/m/api/threads/T1", "/m/api/tasks/7",
             "/m/api/tasks/7/history", "/m/api/approvals", "/m/api/photo/models", "/m/api/photo/jobs",
             "/m/api/session", "/panel/", "/panel/api/leak", "/api/leak"]
    for p in paths:
        r = authed.c.get(p)
        assert r.status_code == 200, (p, r.text)
        assert_clean(r)
    assert "set-cookie" not in authed.c.get("/panel/api/leak").headers


def test_bossman_session_renewed_on_401(authed):
    authed.c.get("/m/api/threads")
    logins = authed.fake.logins
    authed.fake.sessions.clear()             # Bossman restarted / session revoked
    r = authed.c.get("/m/api/threads")
    assert r.status_code == 200 and authed.fake.logins == logins + 1


# ------------------------------------------------------------------ approvals: only on the owner's tap
def test_approvals_list_and_decide(authed):
    items = authed.c.get("/m/api/approvals").json()["items"]
    assert items[0]["id"] == 9 and "secret" not in items[0]
    assert not [c for c in authed.fake.calls if c["path"] == "/api/approvals/9"], "no automatic decision"
    r = authed.post("/m/api/approvals/9", json={"approve": False})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    call = [c for c in authed.fake.calls if c["path"] == "/api/approvals/9"][0]
    assert json.loads(call["body"]) == {"approve": False, "by": "owner"}


# ------------------------------------------------------------------ photo
def test_photo_models_only_photo_with_label_flag(authed):
    ms = authed.c.get("/m/api/photo/models").json()["models"]
    assert [m["id"] for m in ms] == ["z-image-turbo", "qwen-image-2.1-uc"]
    assert ms[1]["uncensored_label"] is True and ms[0]["uncensored_label"] is False
    assert "weights" not in ms[0]


def test_photo_create_and_file(authed):
    r = authed.post("/m/api/photo/jobs", json={"model": "wan2.2-ti2v-5b", "prompt": "x"})
    assert r.status_code == 404
    r = authed.post("/m/api/photo/jobs", json={"model": "z-image-turbo", "prompt": "горы на закате", "resolution": "512x512",
                                               "steps": 8})
    assert r.status_code == 202 and r.json()["job_id"] == "a" * 32
    sent = json.loads([c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"][0]["body"])
    assert sent == {"model": "z-image-turbo", "prompt": "горы на закате", "negative": "", "mode": "DIRECT",
                    "resolution": "512x512", "steps": 8}
    jobs = authed.c.get("/m/api/photo/jobs").json()["jobs"]
    assert len(jobs) == 1 and "provenance" not in jobs[0]
    f = authed.c.get(f"/m/api/photo/jobs/{'a' * 32}/file?download=1")
    assert f.status_code == 200 and f.content == PNG and f.headers["content-type"] == "image/png"
    assert f.headers["content-disposition"].startswith("attachment")
    assert authed.c.get("/m/api/photo/jobs/..%2Fx/file").status_code == 404


# ------------------------------------------------------------------ panel proxy (Bossman 1:1)
def test_panel_forwards_method_body_query_and_swaps_auth(authed):
    pc = authed.panel_csrf()
    r = authed.c.post("/panel/api/echo?x=1&y=2", content=b'{"a":1}',
                      headers={"X-BCC-CSRF": pc, "Content-Type": "application/json", "X-BCC-Token": "phone-guess"})
    assert r.status_code == 200, r.text
    assert r.json() == {"method": "POST", "body": '{"a":1}', "query": {"x": "1", "y": "2"}}
    up = [c for c in authed.fake.calls if c["path"] == "/api/echo"][0]
    assert up["headers"]["cookie"] == f"bcc_session={SID}"
    assert up["headers"]["x-bcc-csrf"] == REAL_CSRF and "x-bcc-token" not in up["headers"]
    assert "bm_session" not in up["headers"]["cookie"]
    for m in ("PUT", "PATCH", "DELETE"):
        assert authed.c.request(m, "/api/echo", headers={"X-BCC-CSRF": pc}).json()["method"] == m


def test_panel_mutation_needs_panel_csrf_and_same_origin(authed):
    assert authed.c.post("/panel/api/echo").status_code == 403
    assert authed.c.post("/api/echo", headers={"X-BCC-CSRF": REAL_CSRF}).status_code == 403
    pc = authed.panel_csrf()
    r = authed.c.post("/api/echo", headers={"X-BCC-CSRF": pc, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert not [c for c in authed.fake.calls if c["path"] == "/api/echo"]


def test_panel_html_injection_and_location_rewrite(authed):
    r = authed.c.get("/panel/")
    assert r.status_code == 200 and "localStorage.setItem('bcc.csrf'" in r.text
    assert authed.panel_csrf() in r.text and r.headers["cache-control"] == "no-store"
    red = authed.c.get("/panel/api/redir", follow_redirects=False)
    assert red.status_code == 307 and red.headers["location"] == "/panel/api/elsewhere"
    assert authed.c.get("/panel/app.js").text == "console.log('ui')"


def test_panel_auth_endpoints_intercepted(authed):
    assert authed.c.get("/api/login-hint").json() == {"token_file": None}
    pc = authed.panel_csrf()
    r = authed.c.post("/api/login", json={"token": "anything"}, headers={"X-BCC-CSRF": pc})
    assert r.json()["csrf"] == pc and REAL_CSRF not in r.text
    assert authed.c.post("/api/logout", headers={"X-BCC-CSRF": pc}).status_code == 200
    assert authed.c.get("/panel/api/system").status_code == 401


def test_websocket_requires_session(env):
    with pytest.raises(WebSocketDisconnect) as exc:
        with env.c.websocket_connect("/api/events") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_websocket_proxied_with_server_side_cookie(authed):
    seen = {}

    class Up:
        def __init__(self):
            self.sent = []
            self.msgs = [json.dumps({"kind": "hello", "leak": SID})]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.msgs:
                return self.msgs.pop(0)
            import asyncio
            await asyncio.sleep(3600)

        async def send(self, m):
            self.sent.append(m)

        async def close(self):
            pass

    up = Up()

    async def connect(url, headers):
        seen.update(url=url, headers=headers)
        return up

    from bossmobile import api as api_mod
    import httpx
    from conftest import FakeBossman, fake_ollama, war_down
    from bossmobile.config import Settings
    st = authed.app.state.settings
    app = api_mod.create_app(st, bossman_transport=httpx.MockTransport(authed.fake.handler), ws_connect=connect,
                             ollama_transport=httpx.MockTransport(fake_ollama), war_transport=httpx.MockTransport(war_down),
                             pairing_token=PAIR)
    from fastapi.testclient import TestClient
    c = TestClient(app, base_url="http://127.0.0.1:8840")
    assert c.post("/m/api/login", json={"token": PAIR}).status_code == 200
    cookie = f"bm_session={c.cookies.get('bm_session')}"      # the TestClient jar does not attach cookies to ws://
    with c.websocket_connect("/api/events", headers={"origin": "http://127.0.0.1:8840", "host": "127.0.0.1:8840", "cookie": cookie}) as ws:
        msg = ws.receive_text()
    assert seen["url"] == "ws://127.0.0.1:8801/api/events" and seen["headers"] == {"Cookie": f"bcc_session={SID}"}
    assert SID not in msg and "[hidden]" in msg


# ------------------------------------------------------------------ shell
def test_app_shell_served_with_csp(env):
    r = env.c.get("/m/")
    assert r.status_code == 200 and "Bossman" in r.text and "apple-mobile-web-app-capable" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    js = env.c.get("/m/app.js")
    assert js.headers["content-type"].startswith("text/javascript")
    man = env.c.get("/m/manifest.webmanifest").json()
    assert man["display"] == "standalone" and man["theme_color"] == "#0a0c10" and man["name"] == "Bossman"
    for icon in ("apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        assert env.c.get(f"/m/icons/{icon}").content[:8] == b"\x89PNG\r\n\x1a\n"
    assert env.c.get("/", follow_redirects=False).headers["location"] == "/m/"


def test_ui_never_uses_innerhtml():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and "document.write" not in src


def test_service_worker_caches_shell_only():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "sw.js").read_text(encoding="utf-8")
    assert "SHELL.includes(url.pathname)" in src and "/m/api" not in src.split("const SHELL")[1].split("];")[0]


def test_redactor_handles_split_secret():
    r = Redactor(["SECRET-VALUE-123"])
    out = r.feed(b"aaa SECRET-") + r.feed(b"VALUE-123 bbb") + r.flush()
    assert out == b"aaa [hidden] bbb"


def test_worker_device_registry_and_30d_phone_session(tmp_path):
    from bossmobile.auth import Sessions, WorkerDeviceRegistry, load_or_create_pairing
    from bossmobile.config import Settings

    # Step 15.1 & 15.2: two worker devices on single core + global STOP <= 2s with 0 hanging tasks
    reg = WorkerDeviceRegistry()
    w1 = reg.register("pc-main", role="gpu-worker", capabilities=["direct_gen", "llm"])
    w2 = reg.register("laptop-secondary", role="cpu-worker", capabilities=["ocr", "scrape"])
    assert w1["stopped"] is False and w2["stopped"] is False

    reg.heartbeat("pc-main", active_task_id=101)
    reg.heartbeat("laptop-secondary", active_task_id=102)
    stop_res = reg.trigger_global_stop()
    assert stop_res["global_stop"] is True
    assert stop_res["devices_stopped"] == 2
    assert stop_res["tasks_aborted"] == 2
    assert stop_res["hanging_tasks"] == 0
    assert stop_res["elapsed_s"] <= 2.0

    # Step 15.3: 30-day session TTL + persistent pairing token across restarts
    st = Settings(data_dir=tmp_path)
    assert st.session_ttl_s == 30 * 24 * 3600
    tok1 = load_or_create_pairing(tmp_path)
    sess1 = Sessions(tmp_path, st.session_ttl_s)
    sid, row = sess1.create(label="owner-iphone")
    assert row["expires"] - row["created"] >= 30 * 24 * 3600 - 5

    # Simulated service restart: pairing token is unchanged and 30-day session remains valid
    tok2 = load_or_create_pairing(tmp_path)
    sess2 = Sessions(tmp_path, st.session_ttl_s)
    assert tok1 == tok2
    assert sess2.get(sid) is not None


def test_worker_device_endpoints_and_task_eta_runtime_wiring(authed):
    # 1. Register two worker devices via real /m/api/workers/register endpoint
    r1 = authed.post("/m/api/workers/register", json={"device_id": "pc-main", "role": "gpu-worker", "capabilities": ["llm"]})
    r2 = authed.post("/m/api/workers/register", json={"device_id": "laptop-2", "role": "cpu-worker", "capabilities": ["ocr"]})
    assert r1.status_code == 200 and r2.status_code == 200

    # 2. Heartbeat with active tasks
    hb = authed.post("/m/api/workers/heartbeat", json={"device_id": "pc-main", "active_task_id": 7})
    assert hb.status_code == 200 and hb.json()["active_task_id"] == 7

    # 3. Check /m/api/workers and /m/api/status expose registered workers
    ws = authed.c.get("/m/api/workers").json()
    assert ws["count"] == 2 and ws["online"] == 2 and ws["global_stop"] is False
    st = authed.c.get("/m/api/status").json()
    assert st["workers"]["count"] == 2

    # 4. Trigger STOP via /m/api/threads/T1/stop -> propagates to worker registry
    stop_resp = authed.post("/m/api/threads/T1/stop")
    assert stop_resp.status_code == 200
    ws_after = authed.c.get("/m/api/workers").json()
    assert ws_after["global_stop"] is True
    assert all(d["active_task_id"] is None and d["stopped"] is True for d in ws_after["devices"])

    # 5. /m/api/tasks/7 returns calibrated ETA from calibrate_task_eta
    task_resp = authed.c.get("/m/api/tasks/7").json()
    assert "eta" in task_resp and isinstance(task_resp["eta"].get("eta_s"), (int, float))


# ------------------------------------------------------------------ owner opt-in cloud agent (24.10)
def _cloud_fixture(opt_in: bool, enabled: bool = True):
    prov = [{"id": 15, "name": "7API", "base_url": "https://7api.st/v1"}]
    mods = [{"id": 105, "provider_id": 15, "kind": "api"}]
    ags = [{"id": 46, "model_id": 105, "tools": ["code.read"], "permissions": {"mobile_cloud_ok": True} if opt_in else {}},
           {"id": 1, "model_id": 105, "tools": [], "permissions": {}}]
    opts = {"agents": [
        {"id": 46, "name": "GLM 5.3 Flash Uncensored (7API)", "enabled": enabled,
         "model": {"alias": "glm-5.3-flash-uncensored", "locality_detail": "cloud", "billing": "paid", "usable": True}},
        {"id": 1, "name": "Local Gemma", "enabled": True,
         "model": {"alias": "gemma", "locality_detail": "local", "billing": "local", "usable": True}}]}
    ags[1]["model_id"] = None
    return opts, ags, mods, prov


def test_cloud_agent_without_owner_opt_in_stays_blocked():
    items = local_agents(*_cloud_fixture(False))
    by = {a["id"]: a for a in items}
    assert by[46]["allowed"] is False and by[46]["cloud"] is False and by[1]["allowed"] is True


def test_opted_in_cloud_agent_is_listed_first_with_provider_and_local_after():
    items = local_agents(*_cloud_fixture(True))
    assert [a["id"] for a in items] == [46, 1]
    assert items[0]["allowed"] and items[0]["cloud"] and not items[0]["local"] and items[0]["provider"] == "7API"
    assert pick_default(items) == 1, "a cloud agent is never the default"


def test_opt_in_does_not_enable_a_disabled_agent():
    by = {a["id"]: a for a in local_agents(*_cloud_fixture(True, enabled=False))}
    assert by[46]["allowed"] is False


def test_opted_in_cloud_agent_accepts_text_but_refuses_attachments(authed):
    ok = authed.post("/m/api/threads/T1/send", json={"text": "привет", "agent_id": 4})
    assert ok.status_code != 403
    bad = authed.post("/m/api/threads/T1/send", json={"text": "вот фото", "agent_id": 4, "attachments": ["abc"]})
    assert bad.status_code == 403 and bad.json()["detail"]["code"] == "BM_CLOUD_NO_ATTACH"
    local_ok = authed.post("/m/api/threads/T1/send", json={"text": "вот фото", "agent_id": 1, "attachments": ["abc"]})
    assert local_ok.status_code != 403
