"""Direct Gen honest backend indicator (master prompt 28.10, task 1b): photo never depends on ComfyUI.

ComfyUI is down, a photo job still completes on the sd runner, the status says so plainly, and the result carries the
real engine name and the SHA-256 of the file on disk. Fake runners only.
"""
from __future__ import annotations

import hashlib

from bcc.direct_gen.service import DirectGenService

from .test_direct_gen import FakeComfy, make_models
from .test_direct_gen_photo import FakeSd, create, finish, make_media

H = {"X-Participant": "owner"}


def install_down_comfy(env, tmp_path, runner):
    media, binary = make_media(tmp_path)
    models, flows = make_models(tmp_path)
    FakeSd.instances = []
    svc = DirectGenService(env.svc, client=FakeComfy(reachable=False), models_dir=models, workflows_dir=flows, poll_seconds=0.01,
                           verify=lambda p: {"verified": True, "duration_s": 1.0, "width": 480, "height": 272},
                           media_dir=media, sd_bin=binary, sd_runner=runner,
                           free_memory=lambda: 64 * 1024 ** 3, foreign_engines=lambda: [], gpu_grace_seconds=0.0)
    env.svc.direct_gen = svc
    return svc


async def test_status_separates_photo_and_video_backends_when_comfyui_is_down(env, tmp_path):
    install_down_comfy(env, tmp_path, FakeSd)
    st = (await env.client.get("/api/direct-gen/status")).json()
    assert st["runtime"]["reachable"] is False
    assert st["backends"]["photo"] == {"active": "sd-cli", "available": True, "resident_mode": None, "reason": ""}
    assert st["backends"]["video"]["active"] is None and st["backends"]["video"]["available"] is False
    assert "фото от него не зависит" in st["backends"]["video"]["note"]


async def test_photo_job_completes_without_comfyui_with_real_sha256(env, tmp_path):
    install_down_comfy(env, tmp_path, FakeSd)
    done = await finish(env, (await create(env)).json()["job_id"])
    assert done["status"] == "completed", done.get("error")
    body = await env.client.get(f"/api/direct-gen/jobs/{done['job_id']}/file", headers=H)
    assert body.status_code == 200 and done["result"]["sha256"] == hashlib.sha256(body.content).hexdigest()
    assert done["provenance"]["runtime"] == "sdcpp"      # the engine name field is written by the real runner (fake has none)
