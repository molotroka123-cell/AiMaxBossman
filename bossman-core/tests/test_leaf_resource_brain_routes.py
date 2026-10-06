"""authored_by_lane (opsplug): bossman.resource_brain.routes - read-only /resource/* behind the admin scope."""
import pytest

from bossman.resource_brain import BRAIN, ResourceSnapshot, WorkloadRequest
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT

from tests.leaf_route_helpers import Devices, bearer, client, new_app


@pytest.fixture
def brain_state():
    before = BRAIN.current_snapshot
    BRAIN.set_snapshot(ResourceSnapshot(1000, 700, 200 * 1024 ** 3, 100 * 1024 ** 3, gpu_memory_used=100, probe="leaf"))
    yield BRAIN
    for lease in list(BRAIN.leases()):
        BRAIN.release(lease.id)
    BRAIN._snapshot = before


async def test_admin_reads_snapshot_leases_and_pressure(brain_state):
    lease = brain_state.acquire(WorkloadRequest(kind="llm", estimated_ram=50), ttl=60)
    with Devices() as dev:
        tok = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            snap = (await c.get("/resource/snapshot", headers=bearer(tok))).json()
            leases = (await c.get("/resource/leases", headers=bearer(tok))).json()
            pressure = (await c.get("/resource/pressure", headers=bearer(tok))).json()
    assert snap["probe"] == "leaf" and snap["unified_available"] == 700 - 0 and snap["gpu_memory_used"] == 100
    assert [l["id"] for l in leases["leases"]] == [lease.id] and leases["held_ram"] == 50
    assert pressure["pool_total"] == 1000 and pressure["held_ram"] == 50
    assert pressure["max_ram_pressure"] == brain_state.max_ram_pressure


async def test_scope_and_anonymous_are_refused_before_any_data_leaves(brain_state):
    with Devices() as dev:
        chat = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            for path in ("/resource/snapshot", "/resource/leases", "/resource/pressure"):
                assert (await c.get(path)).status_code in (401, 403)
                assert (await c.get(path, headers=bearer(chat))).status_code == 403


async def test_routes_are_read_only_no_mutation_verbs():
    from bossman.resource_brain.routes import router
    methods = {m for r in router.routes for m in r.methods}
    assert methods <= {"GET", "HEAD"}
    assert {r.path for r in router.routes} == {"/resource/snapshot", "/resource/leases", "/resource/pressure"}
