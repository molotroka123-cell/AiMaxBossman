"""RC19 audit P1-4: the owner token only reaches the backend serving the data root.

Owner PC: companion and Jeff configs said core_url=:8800 while the release
candidate ran on :8820. Pult approvals and Jeff Studio calls went to whatever
listened on 8800, carrying the RC's X-BCC-Token. No network: MockTransport and
a real backend.lock taken in a temp data dir.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bcc.backend_lock import acquire
from bcc.build_identity import DESKTOP_APP_IDENTITY
from bcc.features import telegram_settings as ts
from bcc.telegram_companion import backend_target
from bcc.telegram_companion.adapters import Core
from bcc.telegram_companion.config import CompanionError, Person, Settings

SHA = "a" * 40
OWNER = Person(11111, 11111, "owner", None)


def settings(**kw) -> Settings:
    return Settings((OWNER,), local_model="local-fixture", core_url="http://127.0.0.1:8800",
                    core_token="fixture-owner-token", **kw)


def recording_transport(seen, *, identity_sha=SHA, app=DESKTOP_APP_IDENTITY):
    def handler(request):
        seen.append((request.url.port, request.url.path, request.headers.get("X-BCC-Token")))
        if request.url.path == "/api/identity":
            return httpx.Response(200, json={"app": app, "build_sha": identity_sha})
        if request.url.path == "/api/approvals":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.fixture
def serving_root(tmp_path):
    """A data root whose backend.lock is held by a backend on 8820 (as the RC)."""
    lock = acquire(tmp_path, host="127.0.0.1", port=8820, build_sha=SHA)
    try:
        yield tmp_path
    finally:
        lock.release()


def run(coro):
    return asyncio.run(coro)


def test_discover_prefers_the_lock_holder_over_a_stale_core_url(serving_root):
    assert backend_target.discover("http://127.0.0.1:8800", serving_root) == ("http://127.0.0.1:8820", SHA)


def test_pult_talks_to_the_serving_backend_after_identity_check(serving_root):
    seen = []
    core = Core(settings(core_data_dir=str(serving_root)), transport=recording_transport(seen),
                verify_backend=True)
    try:
        assert run(core.approvals()) == []
    finally:
        run(core.close())
    assert all(port == 8820 for port, _, _ in seen)
    assert seen[0] == (8820, "/api/identity", None)          # identity first, no token
    assert seen[1] == (8820, "/api/approvals", "fixture-owner-token")


def test_token_is_never_sent_to_a_backend_of_another_build(serving_root):
    seen = []
    core = Core(settings(core_data_dir=str(serving_root)),
                transport=recording_transport(seen, identity_sha="b" * 40), verify_backend=True)
    try:
        with pytest.raises(CompanionError, match="CORE_IDENTITY_MISMATCH"):
            run(core.approvals())
    finally:
        run(core.close())
    assert [token for _, _, token in seen] == [None]


def test_saved_data_root_that_nobody_serves_sends_nothing(tmp_path):
    seen = []
    core = Core(settings(core_data_dir=str(tmp_path)), transport=recording_transport(seen),
                verify_backend=True)
    try:
        with pytest.raises(CompanionError, match="CORE_NOT_RUNNING"):
            run(core.approvals())
    finally:
        run(core.close())
    assert seen == []


def test_legacy_config_refuses_a_non_bossman_process_on_the_old_port(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_target, "default_core_data_dir", lambda: tmp_path)   # nobody serves it
    seen = []
    core = Core(settings(), transport=recording_transport(seen, app="some-other-app"),
                verify_backend=True)
    try:
        with pytest.raises(CompanionError, match="CORE_IDENTITY_MISMATCH"):
            run(core.approvals())
    finally:
        run(core.close())
    assert seen == [(8800, "/api/identity", None)]


def test_jeff_studio_broker_uses_the_verified_backend(serving_root):
    from bcc.pit.studio_image_edit import StudioImageEditBroker, StudioImageEditConfig
    seen = []

    def handler(request):
        seen.append((request.url.port, request.url.path, request.headers.get("X-BCC-Token")))
        if request.url.path == "/api/identity":
            return httpx.Response(200, json={"app": DESKTOP_APP_IDENTITY, "build_sha": SHA})
        return httpx.Response(200, json={"items": []})

    broker = StudioImageEditBroker(
        StudioImageEditConfig("http://127.0.0.1:8800", "fixture-owner-token", "local:test"),
        transport=httpx.MockTransport(handler),
        backend=backend_target.VerifiedBackend("http://127.0.0.1:8800", serving_root))
    try:
        assert run(broker.available()) is False
    finally:
        run(broker.close())
    assert [(port, path) for port, path, _ in seen] == [(8820, "/api/identity"), (8820, "/api/studio/models")]


# -- the settings page saves the serving backend, not a default 8800 ------------------------
@pytest.fixture
def tg(tmp_path, monkeypatch):
    from .test_telegram_settings import models_transport
    path = tmp_path / "tg" / "config.json"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(path))
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", models_transport())
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("Telegram must not be called")))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    yield path


async def test_settings_save_the_serving_port_and_data_root(env, tg):
    from .test_telegram_settings import body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://127.0.0.1:8820", headers=dict(env.client.headers)) as client:
        r = await client.put("/api/telegram/settings", json=body())
    assert r.status_code == 200, r.text
    saved = json.loads(tg.read_text(encoding="utf-8"))
    assert saved["core_url"] == "http://127.0.0.1:8820"
    from pathlib import Path
    assert Path(saved["core_data_dir"]) == Path(env.settings.data_dir).resolve()
