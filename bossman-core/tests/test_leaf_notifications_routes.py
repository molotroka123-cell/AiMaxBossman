"""authored_by_lane (opsplug): bossman.notifications.routes - admin-only status and test notification."""
import pytest

from bossman.notifications import routes as nroutes
from bossman.notifications.store import SQLiteNotificationStore
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT

from tests.leaf_route_helpers import Devices, bearer, client, new_app


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SQLiteNotificationStore(tmp_path / "n.db")
    import bossman.notifications.runtime as rt
    monkeypatch.setattr(nroutes, "STORE", s)
    monkeypatch.setattr(rt, "STORE", s)                  # enqueue_text writes to the runtime store
    return s


async def test_admin_can_queue_a_test_notification_and_see_it_counted(store):
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            before = (await c.get("/notifications/status", headers=bearer(admin))).json()
            r = await c.post("/notifications/test", json={"text": "leaf ping"}, headers=bearer(admin))
            after = (await c.get("/notifications/status", headers=bearer(admin))).json()
    assert r.status_code == 200 and r.json() == {"queued": True}
    assert before["queue"] == {} and after["queue"] == {"pending": 1}
    assert "telegram_enabled" in after and isinstance(after["telegram_enabled"], bool)


async def test_secrets_in_the_test_text_never_reach_the_queue(store):
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            await c.post("/notifications/test", json={"text": "api_key=sk-proj-THISISNOTAREALSECRET123"},  # ci-secret-scan: allow
                         headers=bearer(admin))
    claimed = store.claim_next()
    assert claimed is not None and "THISISNOTAREALSECRET123" not in claimed.body


async def test_chat_scope_and_anonymous_are_refused_and_nothing_is_queued(store):
    with Devices() as dev:
        chat = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            for h in ({}, bearer(chat)):
                assert (await c.get("/notifications/status", headers=h)).status_code in (401, 403)
                assert (await c.post("/notifications/test", json={}, headers=h)).status_code in (401, 403)
    assert store.counts() == {}


async def test_overlong_text_is_rejected(store):
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            r = await c.post("/notifications/test", json={"text": "x" * 501}, headers=bearer(admin))
    assert r.status_code == 422 and store.counts() == {}
