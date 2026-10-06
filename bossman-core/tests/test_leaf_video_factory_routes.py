"""authored_by_lane (opsplug): bossman.video_factory.routes - /video/jobs behind the chat scope, bounded queue."""
import pytest

import bossman.video_factory as vf
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT, SCOPE_EVENTS
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.video_factory import VideoFactoryService

from tests.leaf_route_helpers import Devices, bearer, client, new_app


@pytest.fixture
def service(tmp_path, monkeypatch):
    brain = ResourceBrain(disk_reserve=0, max_ram_pressure=0.999)
    brain.set_snapshot(ResourceSnapshot(10 ** 12, 10 ** 12, 10 ** 12, 10 ** 12))
    svc = VideoFactoryService(tmp_path / "jobs", brain=brain, queue_size=2)
    monkeypatch.setattr(vf, "FACTORY", svc)             # the router resolves the singleton lazily
    return svc


async def test_chat_scope_creates_lists_and_reads_a_queued_job(service):
    with Devices() as dev:
        tok = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            made = await c.post("/video/jobs", json={"title": "demo", "prompts": ["one", "two"], "duration_s": 3},
                                headers=bearer(tok))
            assert made.status_code == 200, made.text
            job = made.json()
            listed = (await c.get("/video/jobs", headers=bearer(tok))).json()
            got = await c.get(f"/video/jobs/{job['id']}", headers=bearer(tok))
    assert job["title"] == "demo" and [s["id"] for s in job["scenes"]] == ["s001", "s002"]
    assert job["state"] == "queued"
    assert any(j["id"] == job["id"] for j in listed["jobs"])
    assert got.status_code == 200 and got.json()["id"] == job["id"]
    assert service.queue.qsize() == 1                      # exactly one entry waits for a worker


async def test_unknown_job_is_a_404_and_the_queue_is_bounded(service):
    with Devices() as dev:
        tok = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            missing = await c.get("/video/jobs/does-not-exist", headers=bearer(tok))
            codes = [(await c.post("/video/jobs", json={"title": f"j{i}", "prompts": ["p"]}, headers=bearer(tok))).status_code
                     for i in range(3)]
    assert missing.status_code == 404
    assert codes[:2] == [200, 200] and codes[2] in (429, 503)       # queue_size=2: backpressure, not unbounded growth


@pytest.mark.parametrize("body", [{"title": "", "prompts": ["p"]}, {"title": "t", "prompts": []},
                                  {"title": "t", "prompts": ["p"], "duration_s": 0},
                                  {"title": "t", "prompts": ["p"], "duration_s": 121}])
async def test_invalid_bodies_are_rejected_before_anything_is_created(service, body):
    with Devices() as dev:
        tok = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            r = await c.post("/video/jobs", json=body, headers=bearer(tok))
    assert r.status_code == 422 and service.queue.qsize() == 0


async def test_anonymous_and_foreign_scopes_are_refused(service):
    with Devices() as dev:
        events_only = await dev.token(SCOPE_EVENTS)
        async with client(new_app()) as c:
            for h in ({}, bearer(events_only)):
                assert (await c.get("/video/jobs", headers=h)).status_code in (401, 403)
                assert (await c.post("/video/jobs", json={"title": "t", "prompts": ["p"]}, headers=h)).status_code in (401, 403)
    assert service.queue.qsize() == 0
