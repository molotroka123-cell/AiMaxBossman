"""authored_by_lane (opsplug): bossman.dev_factory.routes - read-only job status; the FULL diff is admin-only."""
import pytest

from bossman.dev_factory import routes as dev_routes
from bossman.dev_factory.factory import DevFactory
from bossman.dev_factory.models import Patch
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT

from tests.leaf_route_helpers import Devices, bearer, client, new_app


@pytest.fixture
def factory(tmp_path, monkeypatch):
    f = DevFactory(tmp_path / "dev", max_attempts=2)
    monkeypatch.setattr(dev_routes, "FACTORY", f)
    return f


async def test_admin_lists_jobs_with_budget_and_files(factory, tmp_path):
    planned = factory.create("fix the bug in a.py", str(tmp_path))
    done = factory.create("add feature", str(tmp_path))
    done.patch = Patch(diff="--- a/x\n+++ b/x\n+1\n", files=("x.py",), sha256="abc", evidence_summary="1 passed")
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            jobs = (await c.get("/dev-factory/jobs", headers=bearer(admin))).json()
    by_id = {j["id"]: j for j in jobs}
    assert by_id[planned.id]["state"].lower() == "planned"
    assert by_id[planned.id]["attempts_used"] == 0 and by_id[planned.id]["attempts_max"] == 2
    assert by_id[planned.id]["files"] == [] and by_id[planned.id]["task"] == "fix the bug in a.py"
    assert by_id[done.id]["files"] == ["x.py"]


async def test_patch_endpoint_returns_none_before_a_patch_and_the_full_evidence_after(factory, tmp_path):
    pending = factory.create("t1", str(tmp_path))
    ready = factory.create("t2", str(tmp_path))
    ready.patch = Patch(diff="DIFFTEXT", files=("a.py", "b.py"), sha256="deadbeef", evidence_summary="all green")
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            none_yet = (await c.get(f"/dev-factory/jobs/{pending.id}/patch", headers=bearer(admin))).json()
            full = (await c.get(f"/dev-factory/jobs/{ready.id}/patch", headers=bearer(admin))).json()
            unknown = await c.get("/dev-factory/jobs/dj_nope/patch", headers=bearer(admin))
    assert none_yet["patch"] is None and none_yet["id"] == pending.id
    assert full["diff"] == "DIFFTEXT" and full["files"] == ["a.py", "b.py"] and full["sha256"] == "deadbeef"
    assert full["evidence"] == "all green"
    assert unknown.status_code == 404


async def test_chat_scope_and_anonymous_never_see_a_diff(factory, tmp_path):
    job = factory.create("secret refactor", str(tmp_path))
    job.patch = Patch(diff="PRIVATE DIFF", files=("p.py",))
    with Devices() as dev:
        chat = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            for path in ("/dev-factory/jobs", f"/dev-factory/jobs/{job.id}/patch"):
                for h in ({}, bearer(chat)):
                    r = await c.get(path, headers=h)
                    assert r.status_code in (401, 403) and "PRIVATE DIFF" not in r.text


async def test_router_exposes_only_read_verbs():
    assert {m for r in dev_routes.router.routes for m in r.methods} <= {"GET", "HEAD"}
