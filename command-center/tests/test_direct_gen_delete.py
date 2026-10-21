"""«Удалить навсегда»: DELETE /direct-gen/jobs/{id} removes a finished job's files and record from the PC.
Negative controls: running job -> 409, non-owner -> 403, malformed/traversal id -> 404, unknown id -> 404."""
from __future__ import annotations

import logging

from .test_direct_gen_photo import FakeSd, create, finish, install, wait_for

OWNER = {"X-Participant": "owner"}


def delete(env, job_id, who="owner"):
    return env.client.delete(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": who})


async def test_delete_removes_files_record_and_urls(env, tmp_path, caplog):
    svc = install(env, tmp_path, FakeSd)
    job_id = (await create(env)).json()["job_id"]
    assert (await finish(env, job_id))["status"] == "completed"
    folder = svc.store.job_dir("owner", job_id)
    assert (folder / "result").is_dir() and (folder / "job.json").is_file()
    caplog.set_level(logging.INFO)
    r = await delete(env, job_id)
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": True, "provenance_deleted": True, "job_id": job_id}
    assert not folder.exists()
    ids = [j["job_id"] for j in (await env.client.get("/api/direct-gen/jobs", headers=OWNER)).json()["jobs"]]
    assert job_id not in ids
    for url in (f"/api/direct-gen/jobs/{job_id}", f"/api/direct-gen/jobs/{job_id}/file"):
        assert (await env.client.get(url, headers=OWNER)).status_code == 404
    lines = [m for m in caplog.messages if m.startswith("direct_gen.delete")]
    assert len(lines) == 1 and job_id in lines[0] and "z-image-turbo" in lines[0]
    assert (await delete(env, job_id)).status_code == 404


async def test_running_job_is_refused_then_deletable_after_cancel(env, tmp_path):
    svc = install(env, tmp_path, lambda: FakeSd(steps=2, hold=True))
    job_id = (await create(env)).json()["job_id"]

    async def running():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers=OWNER)).json()
        return j if j["stage"] == "generating" else None
    await wait_for(running)
    r = await delete(env, job_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_running"
    assert svc.store.job_dir("owner", job_id).exists()
    await env.client.post(f"/api/direct-gen/jobs/{job_id}/cancel", headers=OWNER)
    assert (await delete(env, job_id)).status_code == 200
    assert not svc.store.job_dir("owner", job_id).exists()


async def test_non_owner_is_refused_and_files_stay(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    job_id = (await create(env)).json()["job_id"]
    await finish(env, job_id)
    r = await delete(env, job_id, who="b" * 64)
    assert r.status_code == 403 and r.json()["error"]["code"] == "owner_only"
    assert svc.store.job_dir("owner", job_id).exists()


async def test_bad_and_unknown_ids_are_404_and_nothing_outside_is_touched(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    job_id = (await create(env)).json()["job_id"]
    await finish(env, job_id)
    keep = svc.store.participant_dir("owner") / "keep.txt"
    keep.write_text("x", encoding="utf-8")
    for bad in ("..", "%2e%2e", "..%2f..", "..%5c..", "not-a-job", "A" * 32, "0" * 32, job_id + "%2f..", "..%2fassist"):
        r = await delete(env, bad)
        assert r.status_code in (404, 405), (bad, r.status_code)
    assert keep.exists() and svc.store.job_dir("owner", job_id).exists()


async def test_misspelled_or_conflicting_participant_header_never_gets_owner_effect(env, tmp_path):
    """RT-01 regression: X-BCC-Participant or conflicting dual participant headers must not silently default to owner."""
    svc = install(env, tmp_path, FakeSd)
    job_id = (await create(env)).json()["job_id"]
    assert (await finish(env, job_id))["status"] == "completed"
    folder = svc.store.job_dir("owner", job_id)
    assert folder.exists()

    # 1. Invalid X-BCC-Participant (e.g. "guest" without X-Participant) -> 422 invalid_participant, NOT silent owner delete
    r_invalid = await env.client.delete(
        f"/api/direct-gen/jobs/{job_id}",
        headers={"X-BCC-Participant": "guest"},
    )
    assert r_invalid.status_code == 422
    assert r_invalid.json()["error"]["code"] == "invalid_participant"
    assert folder.exists()

    # 2. Valid non-owner PIT key passed via X-BCC-Participant -> 403 owner_only, NOT silent owner delete
    r_pit_alias = await env.client.delete(
        f"/api/direct-gen/jobs/{job_id}",
        headers={"X-BCC-Participant": "b" * 64},
    )
    assert r_pit_alias.status_code == 403
    assert r_pit_alias.json()["error"]["code"] == "owner_only"
    assert folder.exists()

    # 3. Conflicting X-Participant and X-BCC-Participant -> 400 conflicting_participant_headers
    r_conflict = await env.client.delete(
        f"/api/direct-gen/jobs/{job_id}",
        headers={"X-Participant": "owner", "X-BCC-Participant": "b" * 64},
    )
    assert r_conflict.status_code == 400
    assert r_conflict.json()["error"]["code"] == "conflicting_participant_headers"
    assert folder.exists()
