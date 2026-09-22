"""Telegram section of Bossman settings: API contract without network.

Model catalogs and Telegram are httpx.MockTransport; the companion process is
replaced by a tiny local Python script. No real bot token exists here: the
fixture token is assembled at runtime and is not a credential.
"""
from __future__ import annotations

import json
import sys

import httpx
import pytest

from bcc.features import telegram_settings as ts
from bcc.secrets import Vault

MAIN = "http://127.0.0.1:8081/v1"
FAST = "http://127.0.0.1:8082/v1"
OSS = "http://127.0.0.1:8083/v1"
MAIN_ID = r"C:\models\main-fixture.gguf"
FAST_ID = r"C:\models\fast-fixture.gguf"
OSS_ID = r"C:\models\oss-fixture-00001-of-00002.gguf"
TOKEN = "123456" + "789:" + "Fx" * 18          # fixture shape only, not a credential


def models_transport(calls=None, main=(MAIN_ID,), fast=(FAST_ID,), oss=(OSS_ID,)):
    def handler(request):
        url = str(request.url)
        if url.endswith("/props"):
            return httpx.Response(404, json={})      # vision unknown
        if calls is not None:
            calls.append(url)
        for base, ids in ((MAIN, main), (FAST, fast), (OSS, oss)):
            if url.startswith(base):
                if ids is None:
                    raise httpx.ConnectError("not loaded")
                return httpx.Response(200, json={"data": [{"id": i} for i in ids]})
        raise AssertionError("unexpected network target " + url)
    return httpx.MockTransport(handler)


def props_aware(calls=None, vision=(FAST,), **kw):
    inner = models_transport(calls, **kw)
    def handler(request):
        url = str(request.url)
        if url.endswith("/props"):
            return httpx.Response(200, json={"modalities": {"vision": any(url.startswith(v[:-3]) for v in vision)}})
        return inner.handler(request)
    return httpx.MockTransport(handler)


@pytest.fixture
def tg(tmp_path, monkeypatch):
    path = tmp_path / "tg" / "config.json"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(path))
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport())
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("Telegram must not be called")))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    yield path
    ts._stop()


def body(**kw):
    base = {"bot_token": TOKEN, "owner_id": 11111, "guest_ids": [22222],
            "best_url": OSS, "best_model": OSS_ID, "fastest_url": FAST, "fastest_model": FAST_ID,
            "default_route": "best", "fast_fallback": True, "local_timeout": 180, "max_tokens": 1024,
            "delegation": False, "enabled": True}
    base.update(kw)
    return base


async def test_unauthenticated_requests_are_rejected(env, tg):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for method, url, payload in (("GET", "/api/telegram/settings", None),
                                     ("PUT", "/api/telegram/settings", body()),
                                     ("POST", "/api/telegram/test", None),
                                     ("POST", "/api/telegram/start", None),
                                     ("POST", "/api/telegram/models", {"urls": [MAIN]})):
            r = await anon.request(method, url, json=payload)
            assert r.status_code == 401, (method, url)
    assert not tg.exists()


async def test_session_without_csrf_cannot_save(env, tg):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as browser:
        login = await browser.post("/api/login", json={"token": env.svc.auth.token})
        csrf = login.json()["csrf"]
        assert (await browser.put("/api/telegram/settings", json=body())).status_code == 403
        ok = await browser.put("/api/telegram/settings", json=body(), headers={"X-BCC-CSRF": csrf})
        assert ok.status_code == 200


async def test_save_roundtrip_never_echoes_token_and_encrypts_it(env, tg):
    r = await env.client.put("/api/telegram/settings", json=body())
    assert r.status_code == 200, r.text
    assert TOKEN not in r.text and r.json()["token_last4"] == "…" + TOKEN[-4:]
    got = await env.client.get("/api/telegram/settings")
    assert TOKEN not in got.text
    data = got.json()
    assert data["owner_id"] == 11111 and data["guest_ids"] == [22222]
    assert data["best_model"] == OSS_ID and data["fastest_model"] == FAST_ID
    assert data["local_timeout"] == 180 and data["token_set"] and data["enabled"]
    assert data["delegation"] is False and data["delegation_available"] is False

    home = tg.parent
    assert TOKEN not in tg.read_text(encoding="utf-8")
    assert TOKEN not in (home / "credentials.enc").read_text(encoding="utf-8")
    secrets = json.loads(Vault(home).decrypt((home / "credentials.enc").read_text(encoding="utf-8")))
    assert secrets["bot_token"] == TOKEN and secrets["core_token"] == "" and secrets["cloud_token"] == ""

    # The companion itself loads exactly what the UI saved.
    from bcc.telegram_companion.config import load
    s = load(tg)
    assert s.bot_token == TOKEN and s.local_model == OSS_ID and s.fast_model == FAST_ID
    assert s.local_timeout == 180 and s.default_route == "main" and s.max_tokens == 1024
    assert [p.user_id for p in s.people] == [11111, 22222]
    assert all(p.agent_id is None for p in s.people) and s.cloud_daily_usd == 0

    # Empty token keeps the saved one; other fields change.
    r = await env.client.put("/api/telegram/settings", json=body(bot_token="", guest_ids=[], default_route="fastest"))
    assert r.status_code == 200 and TOKEN not in r.text
    s = load(tg)
    assert s.bot_token == TOKEN and s.default_route == "fast" and len(s.people) == 1


@pytest.mark.parametrize("change", [
    {"owner_id": 0}, {"owner_id": -5}, {"owner_id": "abc"}, {"owner_id": 2**60},
    {"guest_ids": ["x"]}, {"guest_ids": [0]}, {"guest_ids": [11111]}, {"guest_ids": [3, 3]},
    {"guest_ids": list(range(1, 9))},
    {"bot_token": "not-a-token"},
    {"local_timeout": 0}, {"local_timeout": 601},
    {"default_route": "cloud"}, {"default_route": "fastest", "fastest_model": ""},
    {"delegation": True}, {"max_tokens": 10}, {"max_tokens": 5000},
    {"best_model": ""},
])
async def test_invalid_input_is_rejected_and_nothing_written(env, tg, change):
    r = await env.client.put("/api/telegram/settings", json=body(**change))
    assert r.status_code == 422, (change, r.text)
    assert not tg.exists() and not (tg.parent / "credentials.enc").exists()


@pytest.mark.parametrize("url", ["http://192.168.1.10:8081/v1", "http://example.com/v1",
                                 "http://localhost:8081/v1", "http://127.0.0.1@evil.example/v1"])
async def test_non_loopback_model_url_rejected(env, tg, url):
    assert (await env.client.put("/api/telegram/settings", json=body(best_url=url))).status_code == 422
    assert (await env.client.put("/api/telegram/settings", json=body(fastest_url=url))).status_code == 422
    assert (await env.client.post("/api/telegram/models", json={"urls": [url]})).status_code == 422
    assert not tg.exists()


async def test_model_dropdown_comes_from_served_ids(env, tg, monkeypatch):
    calls = []
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", props_aware(calls, main=(MAIN_ID, "other")))
    r = await env.client.post("/api/telegram/models", json={})
    data = r.json()
    by_url = {e["url"]: e["models"] for e in data["endpoints"]}
    assert by_url == {OSS: [OSS_ID], MAIN: [MAIN_ID, "other"], FAST: [FAST_ID]}
    assert data["scope"] == "CATALOG_ONLY_NOT_INFERENCE"
    assert sorted(calls) == sorted([OSS + "/models", MAIN + "/models", FAST + "/models"])  # catalog GET only
    assert {e["url"]: e["vision"] for e in data["endpoints"]} == {OSS: False, MAIN: False, FAST: True}
    # Defaults: best = GPT-OSS 8083, fastest = FAST 8082.
    assert data["suggested"] == {"best": {"url": OSS, "model": OSS_ID}, "fastest": {"url": FAST, "model": FAST_ID}}


async def test_suggestions_when_some_endpoints_are_not_loaded(env, tg, monkeypatch):
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport(fast=None, oss=None))
    data = (await env.client.post("/api/telegram/models", json={})).json()
    assert data["suggested"]["best"] == {"url": MAIN, "model": MAIN_ID}
    assert data["suggested"]["fastest"] == {"url": MAIN, "model": MAIN_ID}
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport(fast=None))
    data = (await env.client.post("/api/telegram/models", json={})).json()
    # No FAST: the fastest measured of what is served (GPT-OSS 15.7 tok/s > MAIN 8.8 tok/s).
    assert data["suggested"] == {"best": {"url": OSS, "model": OSS_ID}, "fastest": {"url": OSS, "model": OSS_ID}}


async def test_unserved_model_name_is_refused(env, tg):
    r = await env.client.put("/api/telegram/settings", json=body(best_model="gpt-oss"))
    assert r.status_code == 422 and OSS_ID in r.json()["error"]["message"]


async def test_unreachable_model_server_saves_with_warning(env, tg, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", httpx.MockTransport(down))
    r = await env.client.put("/api/telegram/settings", json=body())
    assert r.status_code == 200 and any("не проверено" in w for w in r.json()["warnings"])
    down_models = (await env.client.post("/api/telegram/models", json={"urls": [MAIN]})).json()
    assert down_models["endpoints"][0]["status"] == "NETWORK_UNAVAILABLE"
    assert down_models["suggested"] == {"best": None, "fastest": None}


async def test_test_bot_calls_only_getme_and_webhook_info(env, tg, monkeypatch):
    assert (await env.client.post("/api/telegram/test")).status_code == 409   # nothing saved yet
    await env.client.put("/api/telegram/settings", json=body())
    methods = []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        result = {"is_bot": True, "username": "fixture_bot"} if method == "getMe" else {"url": ""}
        return httpx.Response(200, json={"ok": True, "result": result})
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(handler))
    r = await env.client.post("/api/telegram/test")
    assert r.json() == {"ok": True, "status": "AUTH_AND_POLLING_CONFIG_OK_NOT_E2E", "username": "fixture_bot"}
    assert methods == ["getMe", "getWebhookInfo"] and TOKEN not in r.text

    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False})))
    r = await env.client.post("/api/telegram/test")
    assert r.json() == {"ok": False, "status": "AUTH_DENIED"} and TOKEN not in r.text


async def test_start_stop_status_and_last_error(env, tg, monkeypatch):
    assert (await env.client.post("/api/telegram/start")).status_code == 409   # not configured
    await env.client.put("/api/telegram/settings", json=body(enabled=False))
    assert (await env.client.post("/api/telegram/start")).status_code == 409   # disabled
    await env.client.put("/api/telegram/settings", json=body(bot_token=""))

    monkeypatch.setattr(ts, "_command", lambda path: [sys.executable, "-c", "import time; time.sleep(60)"])
    st = (await env.client.post("/api/telegram/start")).json()
    assert st["state"] == "running" and st["managed"]
    assert (await env.client.get("/api/telegram/status")).json()["state"] == "running"
    assert (await env.client.post("/api/telegram/stop")).json()["state"] in {"stopped", "error"}

    script = "import sys; print('TELEGRAM_COMPANION=AUTH_DENIED', flush=True); sys.exit(2)"
    monkeypatch.setattr(ts, "_command", lambda path: [sys.executable, "-c", script])
    await env.client.post("/api/telegram/start")
    ts._PROC["proc"].wait(timeout=10)
    st = (await env.client.get("/api/telegram/status")).json()
    assert st["state"] == "error" and st["last_error"] == "AUTH_DENIED" and TOKEN not in json.dumps(st)


async def test_disabling_in_settings_stops_managed_companion(env, tg, monkeypatch):
    await env.client.put("/api/telegram/settings", json=body())
    monkeypatch.setattr(ts, "_command", lambda path: [sys.executable, "-c", "import time; time.sleep(60)"])
    assert (await env.client.post("/api/telegram/start")).json()["state"] == "running"
    r = await env.client.put("/api/telegram/settings", json=body(bot_token="", enabled=False))
    assert r.json()["status"]["state"] != "running"


def test_companion_command_runs_this_servers_code(tmp_path):
    cmd = ts._command(tmp_path / "config.json")
    assert cmd[0] == sys.executable and cmd[1] == "-I" and cmd[-2:] == ["--config", str(tmp_path / "config.json")]
    import bcc
    from pathlib import Path
    assert str(Path(bcc.__file__).resolve().parents[1]) in cmd


async def test_photo_model_setting_roundtrip(env, tg):
    r = await env.client.put("/api/telegram/settings", json=body(vision_route="fastest"))
    assert r.status_code == 200 and r.json()["vision_route"] == "fastest"
    from bcc.telegram_companion.config import load
    assert load(tg).vision_route == "fast"
    assert (await env.client.get("/api/telegram/settings")).json()["vision_route"] == "fastest"
    assert (await env.client.put("/api/telegram/settings", json=body(vision_route="cloud"))).status_code == 422
    bad = body(vision_route="fastest", fastest_model="", fastest_url="")
    assert (await env.client.put("/api/telegram/settings", json=bad)).status_code == 422


async def test_image_generation_settings_and_studio_contract(env, tg):
    r = await env.client.put("/api/telegram/settings", json=body())
    home = tg.parent
    read = lambda: json.loads(Vault(home).decrypt((home / "credentials.enc").read_text(encoding="utf-8")))
    assert r.json()["image_enabled"] is False and read()["core_token"] == ""
    r = await env.client.put("/api/telegram/settings", json=body(bot_token="", image_enabled=True,
                                                               image_size=768, image_steps=12))
    assert r.status_code == 200 and r.json()["image_size"] == 768
    assert read()["core_token"] == env.svc.auth.token and env.svc.auth.token not in r.text
    for bad in ({"image_size": 999}, {"image_steps": 50}, {"image_model": "openrouter:x"}):
        assert (await env.client.put("/api/telegram/settings", json=body(bot_token="", **bad))).status_code == 422

    # The companion's Studio client against the REAL Studio routes of this app.
    from bcc.telegram_companion.adapters import Core
    from bcc.telegram_companion.config import load
    core = Core(load(tg), transport=httpx.ASGITransport(app=env.app))
    try:
        model = await core.studio_model("sdcpp:z-image-turbo")
        assert model is not None and model["available"] is False      # no engine in CI: honest
        assert await core.studio_runs(999) == []
    finally:
        await core.close()


async def test_set_bot_commands_only_on_request(env, tg, monkeypatch):
    assert (await env.client.post("/api/telegram/commands")).status_code == 409
    await env.client.put("/api/telegram/settings", json=body())
    seen = []
    def handler(request):
        seen.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": True})
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(handler))
    r = (await env.client.post("/api/telegram/commands")).json()
    assert r["ok"] and "menu" in r["commands"] and "img" in r["commands"]
    [(method, payload)] = seen
    assert method == "setMyCommands" and {"command": "best", "description": "Отвечать лучшей моделью"} in payload["commands"]


async def test_persona_learning_profiles_and_export(env, tg):
    r = await env.client.put("/api/telegram/settings", json=body(persona="", learning={"22222": False, "999": True},
                                                               retention_days=30, owner_priority=False))
    data = r.json()
    assert "Bossman" in data["persona"] and data["learning"] == {"11111": True, "22222": False}
    assert data["retention_days"] == 30 and data["owner_priority"] is False
    r = await env.client.put("/api/telegram/settings", json=body(bot_token="", persona="Ты Bossman, отвечай строго и кратко."))
    assert r.json()["persona"] == "Ты Bossman, отвечай строго и кратко."
    assert (await env.client.put("/api/telegram/settings", json=body(bot_token="", persona="x" * 3001))).status_code == 422

    # The companion's own encrypted store, filled like the companion would.
    from bcc.telegram_companion.store import Store
    store = Store(tg.parent)
    try:
        store.log("11111:11111", "моя почта owner@example.com, тел +7 915 123-45-67, ключ " + "123456789:" + "B" * 35, "ок")
        store.log("22222:22222", "гостевой вопрос", "гостевой ответ")
        store.put_profile("22222:22222", "- любит шахматы", 2)
    finally:
        store.close()

    people = (await env.client.get("/api/telegram/people")).json()["items"]
    by_id = {p["user_id"]: p for p in people}
    assert by_id[11111]["entries"] == 1 and by_id[22222]["profile"]["text"] == "- любит шахматы"
    assert by_id[22222]["learning"] is True

    import importlib.util
    if importlib.util.find_spec("bossman") is None:
        # Command Center installed without bossman-core: the sanitizer is
        # missing, so the owner edit is refused (503) and nothing is written.
        r = await env.client.put("/api/telegram/profile/22222", json={"text": "- любит шахматы и джаз"})
        assert r.status_code == 503 and "bossman-core" in r.text
        return
    edited = (await env.client.put("/api/telegram/profile/22222", json={"text": "- любит шахматы и джаз"})).json()
    assert edited["version"] == 2 and edited["edited_by_owner"] is True
    assert (await env.client.put("/api/telegram/profile/424242", json={"text": "x"})).status_code == 404
    assert (await env.client.delete("/api/telegram/profile/22222")).json() == {"ok": True}
    assert (await env.client.get("/api/telegram/people")).json()["items"][1]["profile"] is None

    exported = (await env.client.post("/api/telegram/export")).json()
    assert exported["records"] == 2 and len(exported["files"]) == 3
    combined = (tg.parent / "exports" / "all-users.jsonl").read_text(encoding="utf-8")
    assert "owner@example.com" not in combined and "915 123" not in combined and "B" * 35 not in combined
    rows = [json.loads(line) for line in combined.splitlines()]
    assert {r["user_id"] for r in rows} == {11111, 22222} and all("reasoning" not in json.dumps(r) for r in rows)
    guest_only = (tg.parent / "exports" / "user-22222.jsonl").read_text(encoding="utf-8")
    assert "гостевой" in guest_only and "owner" not in guest_only

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for method, url in (("GET", "/api/telegram/people"), ("POST", "/api/telegram/export"),
                            ("DELETE", "/api/telegram/profile/22222")):
            assert (await anon.request(method, url)).status_code == 401


# ---------------------------------------------------------------- отзыв и ротация токена

NEW_TOKEN = "987654" + "321:" + "Qz" * 18          # fixture shape only, not a credential


def _stored(home):
    return json.loads(Vault(home).decrypt((home / "credentials.enc").read_text(encoding="utf-8")))


async def test_token_rotation_is_an_owner_setting_not_a_chat_command(env, tg):
    """Ротация живёт в настройках Bossman: в ответе только маска, в файлах — новый секрет."""
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    bad = await env.client.post("/api/telegram/token", json={"bot_token": "не-токен"})
    assert bad.status_code == 422 and _stored(tg.parent)["bot_token"] == TOKEN
    same = await env.client.post("/api/telegram/token", json={"bot_token": TOKEN})
    assert same.status_code == 422                      # «ротация» в тот же токен — не ротация

    r = await env.client.post("/api/telegram/token", json={"bot_token": NEW_TOKEN})
    assert r.status_code == 200, r.text
    assert NEW_TOKEN not in r.text and TOKEN not in r.text
    assert r.json()["bot_token_masked"] == "…" + NEW_TOKEN[-4:]
    assert _stored(tg.parent)["bot_token"] == NEW_TOKEN
    assert NEW_TOKEN not in tg.read_text(encoding="utf-8")
    assert NEW_TOKEN not in (tg.parent / "credentials.enc").read_text(encoding="utf-8")
    # Остальные секреты не потеряны.
    assert set(_stored(tg.parent)) >= {"bot_token", "core_token", "cloud_token", "local_token", "proxy"}
    # Компаньон грузит уже новый токен.
    from bcc.telegram_companion.config import load
    assert load(tg).bot_token == NEW_TOKEN


async def test_token_revocation_stops_the_bridge_and_erases_the_secret(env, tg):
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    r = await env.client.delete("/api/telegram/token")
    assert r.status_code == 200 and r.json()["revoked"] is True
    assert TOKEN not in r.text and "@BotFather" in r.json()["next"]
    assert _stored(tg.parent)["bot_token"] == ""
    assert json.loads(tg.read_bytes())["enabled"] is False
    # Без токена мост не стартует и не притворяется работающим.
    start = await env.client.post("/api/telegram/start")
    assert start.status_code == 409
    repeat = await env.client.delete("/api/telegram/token")
    assert repeat.json() == {"revoked": False, "reason": "NO_TOKEN_STORED", "status": repeat.json()["status"]}


async def test_token_endpoints_require_an_authenticated_owner(env, tg):
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        assert (await anon.post("/api/telegram/token", json={"bot_token": NEW_TOKEN})).status_code == 401
        assert (await anon.delete("/api/telegram/token")).status_code == 401
    assert _stored(tg.parent)["bot_token"] == TOKEN


async def test_local_state_never_lands_in_the_repository(env, tg):
    """Скриншоты и память компаньона живут вне дерева репозитория."""
    from pathlib import Path

    import bcc
    repo = Path(bcc.__file__).resolve().parents[2]
    from bcc.telegram_companion.__main__ import default_config
    assert repo not in default_config().resolve().parents
    import tempfile
    frames = Path(tempfile.gettempdir()) / "bossman-computer"
    assert repo not in frames.resolve().parents
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.sqlite3", "credentials.enc", "screen-*.png"):
        assert pattern in gitignore, pattern


async def test_profile_edit_without_the_sanitizer_is_refused_and_not_stored(env, tg, monkeypatch):
    """Fail closed: without the canonical sanitizer (bossman-core) an owner edit
    must not store unredacted text; it answers a typed 503, never a 500."""
    import sys
    from bcc.telegram_companion.store import Store
    assert (await env.client.put("/api/telegram/settings", json=body())).status_code == 200   # 22222 is a known guest
    store = Store(tg.parent)
    try:
        store.log("22222:22222", "вопрос", "ответ")
        store.put_profile("22222:22222", "- любит шахматы", 1)
    finally:
        store.close()
    monkeypatch.setitem(sys.modules, "bossman.ai_lab.sanitizer", None)
    r = await env.client.put("/api/telegram/profile/22222", json={"text": "ключ sk-or-v1-" + "a" * 40})
    assert r.status_code == 503 and "не сохранён" in r.text
    people = (await env.client.get("/api/telegram/people")).json()["items"]
    prof = {p["user_id"]: p for p in people}[22222]["profile"]
    assert prof["text"] == "- любит шахматы"          # unchanged: nothing unredacted was written
