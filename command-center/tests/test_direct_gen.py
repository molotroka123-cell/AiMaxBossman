"""Direct Generation (DIRECT / ASSISTED video window) against a FAKE ComfyUI client.

No real generation: the backend is a scripted fake with the same interface as
bcc.direct_gen.client.ComfyUIVideoClient. Scenes are neutral by design.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bcc.direct_gen.service import DirectGenService

from .conftest import wait_for

TEMPLATE = {
    "1": {"class_type": "AnyVideoSampler", "inputs": {
        "text": "{{prompt}}", "neg": "{{negative}}", "seed": "{{seed}}",
        "width": "{{width}}", "height": "{{height}}", "frames": "{{frames}}"}},
    "2": {"class_type": "AnySave", "inputs": {"video": ["1", 0], "prefix": "BOSSMAN/direct"}},
}
NEUTRAL = "A red kite over a quiet beach at sunrise, slow dolly in"
TERMINAL = {"completed", "failed", "cancelled"}


class FakeComfy:
    """Scripted ComfyUI: steps = list of progress dicts/None, then completion."""

    def __init__(self, *, steps=(None,), fail: str | None = None, hold: bool = False,
                 reachable: bool = True, payload: bytes = b"\x00\x00\x00\x18ftypmp42FAKEVIDEO"):
        self.steps, self.fail, self.hold, self.reachable = list(steps), fail, hold, reachable
        self.payload = payload
        self.submitted: list[dict] = []
        self.interrupts: list[str] = []
        self.uploads: list[str] = []
        self.polls = 0
        self._n = 0

    async def health(self):
        if not self.reachable:
            raise ConnectionError("connection refused")
        return {"available": True}

    async def upload(self, name: str, data: bytes) -> str:
        self.uploads.append(name)
        return name

    async def submit_video(self, workflow: dict, client_id: str) -> str:
        self._n += 1
        self.submitted.append(workflow)
        return f"p{self._n}"

    async def progress(self, prompt_id: str):
        i = min(self.polls, len(self.steps) - 1)
        return self.steps[i]

    async def poll(self, prompt_id: str) -> dict:
        self.polls += 1
        if prompt_id in self.interrupts:
            return {"state": "failed", "outputs": [], "error": "interrupted"}
        if self.hold:
            return {"state": "running", "outputs": [], "error": None}
        if self.polls <= len(self.steps):
            return {"state": "running", "outputs": [], "error": None}
        if self.fail:
            return {"state": "failed", "outputs": [], "error": self.fail}
        return {"state": "completed", "error": None,
                "outputs": [{"filename": "direct_00001.mp4", "subfolder": "BOSSMAN", "type": "output"}]}

    async def download(self, descriptor: dict, dest: Path) -> int:
        dest.write_bytes(self.payload)
        return len(self.payload)

    async def interrupt(self, prompt_id: str) -> None:
        self.interrupts.append(prompt_id)


def make_models(root: Path, *, wan_t2v=True, template=True) -> tuple[Path, Path]:
    models, flows = root / "models", root / "flows"
    for sub in ("diffusion_models", "text_encoders", "vae", "audio_encoders", "checkpoints"):
        (models / sub).mkdir(parents=True, exist_ok=True)
    flows.mkdir(parents=True, exist_ok=True)
    if wan_t2v:
        (models / "diffusion_models" / "wan2.1_t2v_1.3B_bf16.safetensors").write_bytes(b"w" * 64)
        (models / "text_encoders" / "umt5_xxl_fp8.safetensors").write_bytes(b"t" * 8)
        (models / "vae" / "wan_2.1_vae.safetensors").write_bytes(b"v" * 8)
    if template:
        (flows / "wan2.1-t2v-1.3b.json").write_text(json.dumps(TEMPLATE), encoding="utf-8")
    return models, flows


def install(env, tmp_path, fake, **kw):
    models, flows = make_models(tmp_path, **kw)
    svc = DirectGenService(env.svc, client=fake, models_dir=models, workflows_dir=flows,
                           poll_seconds=0.01, verify=lambda p: {"verified": False, "reason": "test"})
    env.svc.direct_gen = svc
    return svc


async def create(env, who="owner", **over):
    body = {"model": "wan2.1-t2v-1.3b", "prompt": NEUTRAL, "duration": 2,
            "resolution": "480x320", "seed": 7, "mode": "DIRECT", **over}
    return await env.client.post("/api/direct-gen/jobs", json=body, headers={"X-Participant": who})


async def finish(env, job_id, who="owner", timeout=5):
    async def check():
        j = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": who})).json()
        return j if j["status"] in TERMINAL else None
    return await wait_for(check, timeout=timeout)


async def test_models_list_reports_real_availability_and_unknowns(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    models = (await env.client.get("/api/direct-gen/models")).json()["models"]
    by = {m["id"]: m for m in models}
    wan = by["wan2.1-t2v-1.3b"]
    assert wan["available"] is True and wan["reason"] == ""
    assert "T2V" in wan["modes"] and wan["runtime"] == "comfyui"
    # unknown provenance is stated, never invented
    assert wan["source"] == "UNKNOWN" and wan["revision"] == "UNKNOWN"
    assert wan["license"] == "UNKNOWN" and wan["sha256"] == "UNKNOWN"
    assert wan["weights"][0]["size_bytes"] == 64
    s2v = by["wan2.2-s2v-14b"]
    assert s2v["available"] is False and "weights" in s2v["reason"].lower()
    assert "S2V" in s2v["modes"]
    assert by["wan2.2-animate-14b"]["available"] is False


async def test_missing_template_makes_model_unavailable_with_reason(env, tmp_path):
    install(env, tmp_path, FakeComfy(), template=False)
    wan = {m["id"]: m for m in (await env.client.get("/api/direct-gen/models")).json()["models"]}["wan2.1-t2v-1.3b"]
    assert wan["available"] is False and "workflow" in wan["reason"].lower()


async def test_lifecycle_completed_with_stages_and_events(env, tmp_path):
    fake = FakeComfy(steps=({"value": 1, "max": 4}, {"value": 3, "max": 4}))
    install(env, tmp_path, fake)
    q = env.svc.bus.subscribe()
    r = await create(env)
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["bytes"] == len(fake.payload) and len(job["result"]["sha256"]) == 64
    file = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file")
    assert file.status_code == 200 and file.content == fake.payload
    stages = [t["stage"] for t in job["timeline"]]
    assert stages[0] == "queued" and "loading" in stages and "generating" in stages
    assert stages.index("generating") < stages.index("postprocessing") < stages.index("completed")
    seen = []
    while not q.empty():
        m = q.get_nowait()
        if m["kind"].startswith("direct_gen."):
            seen.append(m)
    assert any(m["job_id"] == job["job_id"] and m["status"] == "completed" for m in seen)
    assert all("prompt" not in json.dumps(m) for m in seen)
    assert job["provenance"]["model_id"] == "wan2.1-t2v-1.3b"
    assert job["provenance"]["runtime"] == "comfyui" and job["provenance"]["workflow_sha256"]


async def test_direct_prompt_reaches_model_unchanged(env, tmp_path):
    fake = FakeComfy()
    install(env, tmp_path, fake)
    raw = "  {{seed}} MiXeD  case\nnewline & «юникод»  "
    r = await create(env, prompt=raw, negative="blur  ")
    job = await finish(env, r.json()["job_id"])
    node = fake.submitted[0]["1"]["inputs"]
    assert node["text"] == raw and node["neg"] == "blur  "
    assert job["raw_prompt"] == raw and job["effective_prompt"] == raw
    assert node["seed"] == 7 and node["frames"] == job["params"]["frames"]


async def test_progress_is_not_invented(env, tmp_path):
    fake = FakeComfy(steps=(None, None), hold=False)
    install(env, tmp_path, fake)
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "completed"
    assert job["progress"]["kind"] == "none" and "percent" not in job["progress"]
    assert "elapsed_s" in job

    fake2 = FakeComfy(steps=({"value": 2, "max": 8},), hold=True)
    install(env, tmp_path / "b", fake2)
    j = (await create(env)).json()["job_id"]

    async def got():
        x = (await env.client.get(f"/api/direct-gen/jobs/{j}")).json()
        return x if x["progress"]["kind"] == "steps" else None
    x = await wait_for(got)
    assert x["progress"] == {"kind": "steps", "value": 2, "max": 8, "percent": 25.0}
    assert x["status"] == "generating" and x["stage"] == "generating"
    await env.client.post(f"/api/direct-gen/jobs/{j}/cancel")
    await finish(env, j)


async def test_stop_mid_run_interrupts_and_leaves_no_background_job(env, tmp_path):
    fake = FakeComfy(hold=True)
    svc = install(env, tmp_path, fake)
    j = (await create(env)).json()["job_id"]

    await wait_for(lambda: asyncio.sleep(0, result=len(fake.submitted) == 1))
    r = await env.client.post(f"/api/direct-gen/jobs/{j}/cancel")
    assert r.status_code == 200
    job = await finish(env, j)
    assert job["status"] == "cancelled" and fake.interrupts == ["p1"]
    assert svc.active_tasks() == 0
    polls = fake.polls
    await asyncio.sleep(0.1)
    assert fake.polls == polls  # nothing keeps polling after STOP
    # cancelling again is harmless and does not resurrect the job
    r2 = await env.client.post(f"/api/direct-gen/jobs/{j}/cancel")
    assert r2.status_code in (200, 409)
    assert (await env.client.get(f"/api/direct-gen/jobs/{j}")).json()["status"] == "cancelled"


class SlowSubmitComfy(FakeComfy):
    """submit_video is still in flight when STOP arrives; it answers only when released."""

    def __init__(self, **kw):
        super().__init__(hold=True, **kw)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def submit_video(self, workflow: dict, client_id: str) -> str:
        self.entered.set()
        await self.release.wait()
        return await super().submit_video(workflow, client_id)


async def test_stop_during_submission_still_interrupts_the_submitted_prompt(env, tmp_path):
    """A STOP mid-request must not orphan the ComfyUI job: the submission finishes, its prompt id
    is learned, and that exact prompt is interrupted. No asyncio.shield involved (bcc/single_flight.py)."""
    fake = SlowSubmitComfy()
    svc = install(env, tmp_path, fake)
    j = (await create(env)).json()["job_id"]
    await asyncio.wait_for(fake.entered.wait(), 5)

    stop = asyncio.ensure_future(env.client.post(f"/api/direct-gen/jobs/{j}/cancel"))
    await asyncio.sleep(0.05)
    assert not stop.done(), "STOP waits for the in-flight submission instead of abandoning it"
    fake.release.set()
    r = await asyncio.wait_for(stop, 10)
    assert r.status_code == 200
    job = await finish(env, j)
    assert job["status"] == "cancelled"
    assert fake.interrupts == ["p1"], "the prompt that reached ComfyUI is the one interrupted"

    async def idle():
        return svc.active_tasks() == 0

    await wait_for(idle, timeout=5)


async def test_cancel_queued_job_never_reaches_backend(env, tmp_path):
    fake = FakeComfy(hold=True)
    svc = install(env, tmp_path, fake)
    a = (await create(env)).json()["job_id"]
    b = (await create(env, seed=9)).json()["job_id"]
    jb = (await env.client.get(f"/api/direct-gen/jobs/{b}")).json()
    assert jb["status"] == "queued" and jb["queue_position"] == 1
    await env.client.post(f"/api/direct-gen/jobs/{b}/cancel")
    assert (await finish(env, b))["status"] == "cancelled"

    # job a must really be inside the backend before it is stopped; cancelling it
    # while its task has not started yet made `submitted` 0 and the test flaky
    async def a_submitted():
        return len(fake.submitted) == 1

    await wait_for(a_submitted, timeout=5)
    await env.client.post(f"/api/direct-gen/jobs/{a}/cancel")
    await finish(env, a)

    # status turns terminal slightly before the asyncio task is reaped: wait for
    # the condition itself (bounded) instead of asserting at an arbitrary instant
    async def idle():
        return svc.active_tasks() == 0

    await wait_for(idle, timeout=5)
    assert len(fake.submitted) == 1


async def test_missing_weights_is_a_clear_error_not_a_job(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    r = await create(env, model="wan2.2-s2v-14b")
    assert r.status_code == 409
    detail = r.json()["error"]
    assert detail["code"] == "model_unavailable" and "weights" in detail["message"].lower()
    r = await create(env, model="no-such-model")
    assert r.status_code == 404


async def test_runtime_unreachable_fails_honestly(env, tmp_path):
    install(env, tmp_path, FakeComfy(reachable=False))
    r = await create(env)
    assert r.status_code == 409 and r.json()["error"]["code"] == "runtime_unreachable"


async def test_oom_is_reported_as_is(env, tmp_path):
    install(env, tmp_path, FakeComfy(fail="HIP out of memory. Tried to allocate 2.00 GiB"))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "out_of_memory"
    assert "out of memory" in job["error"]["message"].lower()


async def test_generic_runtime_error_keeps_original_text(env, tmp_path):
    install(env, tmp_path, FakeComfy(fail="node X refused the input"))
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "runtime_error"
    assert "refused the input" in job["error"]["message"]


async def test_retry_same_seed_is_deterministic(env, tmp_path):
    fake = FakeComfy()
    install(env, tmp_path, fake)
    first = await finish(env, (await create(env, seed=None)).json()["job_id"])
    seed = first["params"]["seed"]
    assert isinstance(seed, int)  # random seed is chosen and RECORDED
    r = await env.client.post(f"/api/direct-gen/jobs/{first['job_id']}/retry")
    assert r.status_code == 202
    second = await finish(env, r.json()["job_id"])
    assert second["params"]["seed"] == seed and second["retry_of"] == first["job_id"]
    assert second["raw_prompt"] == first["raw_prompt"]
    assert fake.submitted[0] == fake.submitted[1]
    assert second["provenance"]["workflow_sha256"] == first["provenance"]["workflow_sha256"]


async def test_history_is_isolated_between_participants(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    alice, bob = "a" * 64, "b" * 64
    ja = (await create(env, who=alice, prompt="alice private scene")).json()["job_id"]
    await finish(env, ja, who=alice)
    jb = (await create(env, who=bob)).json()["job_id"]
    await finish(env, jb, who=bob)
    la = (await env.client.get("/api/direct-gen/jobs", headers={"X-Participant": alice})).json()["jobs"]
    lb = (await env.client.get("/api/direct-gen/jobs", headers={"X-Participant": bob})).json()["jobs"]
    assert [j["job_id"] for j in la] == [ja] and [j["job_id"] for j in lb] == [jb]
    for path in (f"/api/direct-gen/jobs/{ja}", f"/api/direct-gen/jobs/{ja}/file"):
        assert (await env.client.get(path, headers={"X-Participant": bob})).status_code == 404
    assert (await env.client.post(f"/api/direct-gen/jobs/{ja}/cancel", headers={"X-Participant": bob})).status_code == 404
    assert (await env.client.post(f"/api/direct-gen/jobs/{ja}/retry", headers={"X-Participant": bob})).status_code == 404
    assert "alice private scene" not in json.dumps(lb)
    bad = await env.client.get("/api/direct-gen/jobs", headers={"X-Participant": "../../etc"})
    assert bad.status_code == 422


async def test_storage_is_per_participant_on_disk(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    who = "c" * 64
    jid = (await create(env, who=who)).json()["job_id"]
    await finish(env, jid, who=who)
    found = list(Path(env.settings.data_dir).rglob("job.json"))
    assert len(found) == 1 and who in found[0].parts and jid in found[0].parts


async def test_invalid_parameters_are_rejected(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    for over in ({"prompt": ""}, {"duration": 0}, {"duration": 9999}, {"resolution": "banana"},
                 {"resolution": "100000x100000"}, {"seed": -5}, {"mode": "WHATEVER"}):
        r = await create(env, **over)
        assert r.status_code in (409, 422), (over, r.text)


async def test_assisted_unavailable_without_qwen_in_registry(env, tmp_path):
    install(env, tmp_path, FakeComfy())
    st = (await env.client.get("/api/direct-gen/status")).json()
    assert st["assist"]["available"] is False and "qwen" in st["assist"]["reason"].lower()
    r = await env.client.post("/api/direct-gen/assist", json={"prompt": NEUTRAL})
    assert r.status_code == 409 and r.json()["error"]["code"] == "assist_unavailable"


async def test_assisted_shows_both_versions_and_user_chooses(env, tmp_path):
    fake = FakeComfy()
    svc = install(env, tmp_path, fake)

    class Qwen:
        async def chat(self, model, messages, **kw):
            from bcc.providers import ChatResult
            self.sent = messages
            return ChatResult(text="Edited: " + NEUTRAL, model=model)

    qwen = Qwen()
    svc.qwen_lookup = lambda: asyncio.sleep(0, result=(qwen, "qwen3:8b"))
    st = (await env.client.get("/api/direct-gen/status")).json()
    assert st["assist"]["available"] is True and st["assist"]["model"] == "qwen3:8b"
    r = await env.client.post("/api/direct-gen/assist", json={"prompt": NEUTRAL})
    a = r.json()
    assert a["original"] == NEUTRAL and a["suggested"] == "Edited: " + NEUTRAL and a["applied"] is False
    assert not fake.submitted  # an assist never starts generation or loads weights
    # user picks the ORIGINAL
    j1 = (await create(env, mode="ASSISTED", assist_id=a["assist_id"], assist_choice="original")).json()["job_id"]
    job1 = await finish(env, j1)
    assert job1["effective_prompt"] == NEUTRAL and job1["raw_prompt"] == NEUTRAL
    # user picks the SUGGESTION: raw stays original, effective is the edit
    j2 = (await create(env, mode="ASSISTED", assist_id=a["assist_id"], assist_choice="suggested")).json()["job_id"]
    job2 = await finish(env, j2)
    assert job2["raw_prompt"] == NEUTRAL and job2["effective_prompt"] == "Edited: " + NEUTRAL
    assert job2["provenance"]["assist_model"] == "qwen3:8b"
    # ASSISTED without an explicit choice is refused
    r = await create(env, mode="ASSISTED", assist_id=a["assist_id"])
    assert r.status_code == 422


async def test_assist_ids_do_not_cross_participants(env, tmp_path):
    svc = install(env, tmp_path, FakeComfy())

    class Qwen:
        async def chat(self, model, messages, **kw):
            from bcc.providers import ChatResult
            return ChatResult(text="x", model=model)
    svc.qwen_lookup = lambda: asyncio.sleep(0, result=(Qwen(), "qwen3:8b"))
    a = (await env.client.post("/api/direct-gen/assist", json={"prompt": NEUTRAL},
                               headers={"X-Participant": "a" * 64})).json()
    r = await create(env, who="b" * 64, mode="ASSISTED", assist_id=a["assist_id"], assist_choice="suggested")
    assert r.status_code == 404


async def test_restart_marks_unfinished_jobs_interrupted(env, tmp_path):
    fake = FakeComfy(hold=True)
    svc = install(env, tmp_path, fake)
    j = (await create(env)).json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, result=len(fake.submitted) == 1))
    # simulate process death: drop the live service, build a new one on the same storage
    for t in list(svc._tasks.values()):
        t.cancel()
    svc2 = DirectGenService(env.svc, client=FakeComfy(), models_dir=svc.models_dir,
                            workflows_dir=svc.workflows_dir, poll_seconds=0.01)
    env.svc.direct_gen = svc2
    job = (await env.client.get(f"/api/direct-gen/jobs/{j}")).json()
    assert job["status"] == "failed" and job["error"]["code"] == "interrupted"
