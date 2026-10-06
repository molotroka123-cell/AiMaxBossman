"""authored_by_lane (opsplug): bossman.sandbox.routes - read-only sandbox status behind the admin scope."""
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT
from bossman.sandbox.models import SandboxSession, SandboxSpec
from bossman.sandbox.subsystem import MANAGER

from tests.leaf_route_helpers import Devices, bearer, client, new_app


async def test_admin_sees_status_and_sessions_without_secrets_or_paths():
    s = SandboxSession(id="sbx_leaf_route", spec=SandboxSpec(task="t"))
    MANAGER.sessions[s.id] = s
    try:
        with Devices() as dev:
            tok = await dev.token(SCOPE_ADMIN)
            async with client(new_app()) as c:
                st = await c.get("/sandbox/status", headers=bearer(tok))
                ss = await c.get("/sandbox/sessions", headers=bearer(tok))
        assert st.status_code == 200
        body = st.json()
        assert set(body) == {"enabled", "runtime", "sessions", "active_leases"}
        assert body["sessions"] >= 1 and body["runtime"] == MANAGER.runtime.name
        row = next(r for r in ss.json() if r["id"] == "sbx_leaf_route")
        assert row["state"] == "REQUESTED" and row["risk"] is None and row["lease"] is None
        assert set(row) == {"id", "state", "risk", "policy", "isolation", "lease", "error"}
    finally:
        MANAGER.sessions.pop("sbx_leaf_route", None)


async def test_anonymous_and_chat_scope_are_refused():
    with Devices() as dev:
        chat = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            for path in ("/sandbox/status", "/sandbox/sessions"):
                assert (await c.get(path)).status_code in (401, 403)
                assert (await c.get(path, headers=bearer(chat))).status_code == 403


async def test_no_mutating_verbs_exist_on_the_router():
    from bossman.sandbox.routes import router
    assert {m for r in router.routes for m in r.methods} <= {"GET", "HEAD"}
