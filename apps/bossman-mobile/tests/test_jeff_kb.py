"""«Jeff · обучение» in Bossman Mobile (/m/api/jeff-kb/*): session + CSRF, fixed Bossman paths, upload limits, check."""
from __future__ import annotations

import base64
import json


def _calls(env, prefix):
    return [c for c in env.fake.calls if c["path"].startswith(prefix)]


def test_jeff_kb_endpoints_require_session_and_csrf(env):
    for method, path in (("GET", "/m/api/jeff-kb"), ("GET", "/m/api/jeff-kb/sections"),
                         ("PUT", "/m/api/jeff-kb/mode"), ("POST", "/m/api/jeff-kb/check"),
                         ("PUT", "/m/api/jeff-kb/manual"), ("DELETE", "/m/api/jeff-kb/manual"),
                         ("PUT", "/m/api/jeff-kb/model"), ("DELETE", "/m/api/jeff-kb/model")):
        assert env.c.request(method, path, json={}).status_code == 401, (method, path)
    env.login()
    assert env.c.put("/m/api/jeff-kb/mode", json={"strict": True}).status_code == 403      # no CSRF header
    assert env.c.post("/m/api/jeff-kb/check", json={"question": "q"}).status_code == 403
    assert _calls(env, "/api/jeff-") == []


def test_jeff_kb_proxies_to_the_owner_api(authed):
    h = {"X-BM-CSRF": authed.csrf}
    assert authed.c.get("/m/api/jeff-kb").json()["path"] == "/api/jeff-kb"
    r = authed.c.put("/m/api/jeff-kb/mode", headers=h, json={"strict": True})
    assert r.json() == {"path": "/api/jeff-kb/mode", "method": "PUT", "echo": {"strict": True}}
    data = base64.b64encode("# Раздел\nтекст".encode("utf-8")).decode()
    r = authed.c.put("/m/api/jeff-kb/manual", headers=h, json={"name": "rules.md", "data_b64": data})
    assert r.status_code == 200 and r.json()["echo"] == {"name": "rules.md", "data_b64": data}
    plain = authed.post("/m/api/jeff-kb/check", json={"question": "Сколько?", "use_manual": False}).json()
    manual = authed.post("/m/api/jeff-kb/check", json={"question": "Сколько?"}).json()
    assert plain["answer"] == "Обычно десять." and manual["cited"] == [{"n": 1, "title": "Разминка"}]
    r = authed.c.put("/m/api/jeff-kb/model", headers=h, json={"model": "gemma4-31b-uncensored:latest"})
    assert r.json() == {"path": "/api/jeff-model", "method": "PUT", "echo": {"model": "gemma4-31b-uncensored:latest"}}
    assert authed.c.delete("/m/api/jeff-kb/model", headers=h).json()["path"] == "/api/jeff-model"
    assert authed.c.delete("/m/api/jeff-kb/manual", headers=h).json()["method"] == "DELETE"


def test_jeff_kb_cloud_fallback_switch_and_provenance_go_through_the_real_models(authed):
    """Owner 21.10: the cloud fallback switch is a field of the mode model; the check's provenance reaches the phone."""
    h = {"X-BM-CSRF": authed.csrf}
    r = authed.c.put("/m/api/jeff-kb/mode", headers=h, json={"cloud_fallback": False})
    assert r.json() == {"path": "/api/jeff-kb/mode", "method": "PUT", "echo": {"cloud_fallback": False}}
    body = {"cloud_fallback": True, "local_timeout_s": 60, "cloud_daily_max": 50}
    assert authed.c.put("/m/api/jeff-kb/mode", headers=h, json=body).json()["echo"] == body
    sent = len(_calls(authed, "/api/jeff-kb/mode"))
    for bad in ({"cloud_fallback": "maybe"}, {"local_timeout_s": 5}, {"local_timeout_s": 301},
                {"cloud_daily_max": -1}, {"cloud_daily_max": 5000}):
        assert authed.c.put("/m/api/jeff-kb/mode", headers=h, json=bad).status_code == 422, bad
    assert len(_calls(authed, "/api/jeff-kb/mode")) == sent, "refused before Bossman"
    out = authed.post("/m/api/jeff-kb/check", json={"question": "Сколько?"}).json()
    assert out["engine"] == "cloud" and out["provider"] == "OpenRouter" and out["model"] == "vendor/model-x:free"
    plain = authed.post("/m/api/jeff-kb/check", json={"question": "Сколько?", "use_manual": False}).json()
    assert plain["engine"] == "local"


def test_jeff_kb_upload_refuses_wrong_type_bad_base64_and_oversize_before_bossman(authed):
    h = {"X-BM-CSRF": authed.csrf}
    ok_b64 = base64.b64encode(b"x").decode()
    assert authed.c.put("/m/api/jeff-kb/manual", headers=h, json={"name": "a.exe", "data_b64": ok_b64}).status_code == 422
    assert authed.c.put("/m/api/jeff-kb/manual", headers=h, json={"name": "a.txt", "data_b64": "###"}).status_code == 422
    big = base64.b64encode(b"a" * (8 * 1024 * 1024 + 1)).decode()
    assert authed.c.put("/m/api/jeff-kb/manual", headers=h, json={"name": "a.txt", "data_b64": big}).status_code in (413, 422)
    assert authed.c.put("/m/api/jeff-kb/model", headers=h, json={"model": "../x y"}).status_code == 422
    assert _calls(authed, "/api/jeff-") == []


def test_gateway_keeps_no_copy_of_the_manual(authed, tmp_path):
    h = {"X-BM-CSRF": authed.csrf}
    marker = "Синтетика-QW77"
    data = base64.b64encode(f"# Раздел\n{marker}".encode("utf-8")).decode()
    authed.c.put("/m/api/jeff-kb/manual", headers=h, json={"name": "rules.md", "data_b64": data})
    for f in (tmp_path / "mobile").rglob("*"):
        if f.is_file():
            blob = f.read_bytes()
            assert marker.encode("utf-8") not in blob and data.encode() not in blob, f.name
    assert json.dumps(authed.fake.calls[-1]["path"]) == '"/api/jeff-kb/manual"'
