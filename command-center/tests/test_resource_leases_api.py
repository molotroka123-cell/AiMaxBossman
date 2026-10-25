"""/api/resources/* (TZ 2.5 T3): owner-only lease API over the governor ledger.

The ledger is injected with a fake memory reading and NO unload hook: the test never talks to the real Ollama and never
starts/stops a process. Negative controls: scoped agent token, participant header, no credential, stale token, bad id,
paid render untouched by a background request, /api/governor/* (AI behaviour governor) is not this API.
"""
from __future__ import annotations

from bcc.auth import HEADER
from bcc.features import resource_leases as feat


def _inject(env, tmp_path, mem_gb=100.0):
    mod = feat._load_module()
    led = mod.LeaseLedger(tmp_path / "leases.db", {}, mem=lambda: mem_gb, unload=None)
    env.svc.resource_leases = led
    return mod, led


async def test_owner_lease_cycle_state_heartbeat_release(env, tmp_path):
    _inject(env, tmp_path)
    r = await env.client.post("/api/resources/lease", json={"consumer": "gemma_chat", "priority": "owner_interactive",
                                                           "holder": "owner chat"})
    assert r.status_code == 200, r.text
    chat = r.json()
    assert chat["state"] == "granted" and chat["token"] >= 1 and chat["env"]["BOSSMAN_RESOURCE_LEASE"]
    bg = await env.client.post("/api/resources/lease", json={"consumer": "wangp_animate", "priority": "background"})
    assert bg.status_code == 202 and "владелец сейчас в чате" in bg.json()["reason"]
    st = (await env.client.get("/api/resources/state")).json()
    assert st["committed_gb"] == 38.0 and [q["consumer"] for q in st["queue"]] == ["wangp_animate"]
    assert all("token" not in x for x in st["leases"])                         # tokens never leak through state
    hb = await env.client.post(f"/api/resources/lease/{chat['id']}/heartbeat", json={"token": chat["token"]})
    assert hb.status_code == 200 and hb.json()["state"] == "granted"
    stale = await env.client.post(f"/api/resources/lease/{chat['id']}/heartbeat", json={"token": chat["token"] + 7})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_token"
    rel = await env.client.delete(f"/api/resources/lease/{chat['id']}", params={"token": chat["token"]})
    assert rel.status_code == 200 and rel.json()["state"] == "released"
    got = (await env.client.get(f"/api/resources/lease/{bg.json()['id']}")).json()
    assert got["state"] == "granted" and "token" not in got                   # promoted after the owner left


async def test_unknown_profile_refused_and_bad_input(env, tmp_path):
    _inject(env, tmp_path)
    r = await env.client.post("/api/resources/lease", json={"consumer": "z_image", "priority": "background"})
    assert r.status_code == 422 and r.json()["state"] == "refused" and "P95" in r.json()["reason"]
    bad = await env.client.post("/api/resources/lease", json={"consumer": "gemma_chat", "priority": "vip"})
    assert bad.status_code == 422
    assert (await env.client.get("/api/resources/lease/not-an-id")).status_code == 422
    extra = await env.client.post("/api/resources/lease", json={"consumer": "gemma_chat", "priority": "eval", "x": 1})
    assert extra.status_code == 422


async def test_paid_render_not_preempted_through_api(env, tmp_path):
    _inject(env, tmp_path)
    paid = (await env.client.post("/api/resources/lease", json={"consumer": "ltx25_sdcli", "priority": "paid_render",
                                                               "idempotency_key": "order-9/seg-1"})).json()
    again = (await env.client.post("/api/resources/lease", json={"consumer": "ltx25_sdcli", "priority": "paid_render",
                                                                "idempotency_key": "order-9/seg-1"})).json()
    assert again["id"] == paid["id"] and again["idempotent_replay"]
    bg = await env.client.post("/api/resources/lease", json={"consumer": "wangp_vace", "priority": "background"})
    assert bg.status_code == 202
    assert (await env.client.get(f"/api/resources/lease/{paid['id']}")).json()["state"] == "granted"


async def test_measurement_endpoint(env, tmp_path):
    _inject(env, tmp_path)
    r = await env.client.post("/api/resources/measurement", json={"consumer": "z_image", "peak_gb": 21.5,
                                                                 "config_hash": "1024px-8steps", "cold": True})
    assert r.status_code == 200 and r.json()["samples"] == 1 and r.json()["source"] == "unknown"


async def test_auth_owner_only(env, tmp_path):
    _inject(env, tmp_path)
    agent = env.svc.auth.issue_agent_token("worker-1", scopes=("agent.session", "read"))
    as_agent = {HEADER: agent}
    for method, url, body in (("get", "/api/resources/state", None),
                              ("post", "/api/resources/lease", {"consumer": "gemma_chat", "priority": "eval"}),
                              ("post", "/api/resources/measurement", {"consumer": "gemma_chat", "peak_gb": 30})):
        kw = {"json": body} if body is not None else {}
        r = await getattr(env.client, method)(url, headers=as_agent, **kw)
        assert r.status_code == 403, (url, r.status_code, r.text)
        r = await getattr(env.client, method)(url, headers={"X-Participant": "a" * 64}, **kw)
        assert r.status_code == 403, url
    lease = (await env.client.post("/api/resources/lease", json={"consumer": "gemma_chat", "priority": "eval"})).json()
    assert (await env.client.delete(f"/api/resources/lease/{lease['id']}", params={"force": True},
                                    headers=as_agent)).status_code == 403
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        assert (await anon.get("/api/resources/state")).status_code == 401
    # owner force-release works without the token; the agent attempt above changed nothing
    assert (await env.client.delete(f"/api/resources/lease/{lease['id']}", params={"force": True})).json()["state"] \
        == "released"


def test_resources_api_is_not_the_ai_governor():
    paths = {r.path for r in feat.router.routes}
    assert {"/resources/state", "/resources/lease", "/resources/lease/{lease_id}"} <= paths
    assert all(p.startswith("/resources/") for p in paths)                    # never /governor/* (AI governor)
