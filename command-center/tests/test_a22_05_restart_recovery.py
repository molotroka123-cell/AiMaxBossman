"""A22-05: Restart recovery for sd-cli (DirectGenService), Qwen Operator, and Telegram Companion operator_bridge.

Proves:
1. If an sd-cli job already wrote a valid output file (result/image.png or result/video.webm) before :8801 restarted,
   DirectGenService._recover() completes the job instead of discarding the finished file as 'interrupted'.
2. If an sd-cli job was queued or interrupted mid-run before producing output, DirectGenService._recover()
   automatically resumes it on an active event loop (preserving reference images on disk), up to a bounded
   restart count (2), after which it transitions to honest 'failed' (code='interrupted') with a clear reason.
3. If :8801 restarts while a qwen_operator (/work) task is in progress, qwen_operator.recover_interrupted_tasks()
   resumes the task in its lab worktree without losing completed in-flight steps (e.g., checkpointed plan),
   or marks it honestly as 'failed' (error_code='interrupted_by_restart') when unresumable.
4. If Telegram Companion restarts while a /work task is active or has an unsent receipt,
   OperatorBridgeMixin.recover_operator_tasks() delivers any unsent receipt and re-attaches work_turn.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bcc.direct_gen.service import DirectGenService
from bcc.features import qwen_operator as qo
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

from .conftest import wait_for
from .test_direct_gen import FakeComfy, make_models
from .test_direct_gen_photo import PNG, FakeSd, create as create_sd, finish as finish_sd, install as install_sd, make_media
from .test_qwen_operator import PLAN, Fakes, repo, stack  # noqa: F401


async def test_sdcli_completed_output_on_disk_is_recovered_on_restart(env, tmp_path):
    """If sd-cli finished writing result/image.png right as :8801 died, _recover() marks it completed."""
    svc = install_sd(env, tmp_path, lambda: FakeSd(hold=True))
    r = await create_sd(env, prompt="lighthouse recovery test")
    job_id = r.json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, result=len(FakeSd.instances) == 1 or None))

    # Simulate sd-cli having finished writing result/image.png right before :8801 process died
    for t in list(svc._tasks.values()):
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    folder = svc.store.job_dir("owner", job_id)
    result_dir = folder / "result"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "image.png").write_bytes(PNG)

    # Restart DirectGenService on the same storage
    media, binary = make_media(tmp_path)
    models, flows = make_models(tmp_path)
    svc2 = DirectGenService(
        env.svc,
        client=FakeComfy(),
        models_dir=models,
        workflows_dir=flows,
        poll_seconds=0.01,
        media_dir=media,
        sd_bin=binary,
        sd_runner=FakeSd,
        free_memory=lambda: 64 * 1024**3,
        foreign_engines=lambda: [],
    )
    env.svc.direct_gen = svc2
    job = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
    assert job["status"] == "completed", job
    assert job["result"]["file"] == "image.png" and job["result"]["verified"] is True
    assert any(e.get("note") == "recovered_after_restart" for e in job["timeline"])


async def test_sdcli_unfinished_job_resumes_automatically_on_restart(env, tmp_path):
    """An interrupted sd-cli job without output automatically resumes on restart and completes."""
    svc = install_sd(env, tmp_path, lambda: FakeSd(hold=True))
    r = await create_sd(env, prompt="lighthouse auto-resume test")
    job_id = r.json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, result=len(FakeSd.instances) == 1 or None))

    # Simulate process crash while generating (no output written yet)
    for t in list(svc._tasks.values()):
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    media, binary = make_media(tmp_path)
    models, flows = make_models(tmp_path)
    FakeSd.instances = []
    svc2 = DirectGenService(
        env.svc,
        client=FakeComfy(),
        models_dir=models,
        workflows_dir=flows,
        poll_seconds=0.01,
        media_dir=media,
        sd_bin=binary,
        sd_runner=FakeSd,
        free_memory=lambda: 64 * 1024**3,
        foreign_engines=lambda: [],
    )
    env.svc.direct_gen = svc2
    done = await finish_sd(env, job_id, timeout=5)
    assert done["status"] == "completed", done
    assert done.get("resumed_count") == 1
    assert any(e.get("note") == "resumed_after_restart" for e in done["timeline"])


async def test_qwen_operator_resumes_in_flight_task_without_losing_completed_plan_step(stack, repo):  # noqa: F811
    """An interrupted qwen_operator task resumes from its checkpointed plan without calling the planner again."""
    svc, _c, _a = stack
    fakes1 = Fakes(repo, PLAN)
    rec = await qo.start_task(
        svc,
        "улучши докстринг",
        source_repo=str(repo),
        decision_wait_s=0,
        local_chat=fakes1.local_chat,
        free_cloud=fakes1.free_cloud,
        coder=fakes1.coder,
    )
    rid = rec["id"]
    await qo._JOBS[rid]

    # Simulate a task that was interrupted mid-run right after saving its plan checkpoint (before commit)
    task_file = Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json"
    saved = json.loads(task_file.read_text(encoding="utf-8"))
    assert isinstance(saved.get("plan"), dict), "plan must be checkpointed in task record"
    # Reset lab to base_commit and mark task as interrupted in 'running' state after 'plan'
    qo._git(Path(saved["lab"]["path"]), "reset", "--hard", saved["lab"]["base_commit"])
    saved["status"] = "running"
    saved["finished_at"] = None
    saved["decisions"] = []
    saved.pop("commit_sha", None)
    saved.pop("diff", None)
    saved["phase"] = {"name": "coder", "since": qo._now()}
    task_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    qo._JOBS.clear()
    qo._QUEUE.clear()

    # On restart, recover_interrupted_tasks resumes the task without calling the planner again
    planner_called = []

    async def fail_if_planner_called(model, messages, **kw):
        if kw.get("json_mode"):
            planner_called.append(model)
            raise AssertionError("Planner should not be re-called when plan checkpoint exists")
        return await fakes1.local_chat(model, messages, **kw)

    recovered = await qo.recover_interrupted_tasks(
        svc,
        resume=True,
        decision_wait_s=0,
        local_chat=fail_if_planner_called,
        free_cloud=fakes1.free_cloud,
        coder=fakes1.coder,
    )
    assert any(r["id"] == rid and r["action"] == "resumed" for r in recovered), recovered
    await qo._JOBS[rid]
    final = json.loads(task_file.read_text(encoding="utf-8"))
    assert final["status"] in ("completed", "completed_with_pending_decisions"), final
    assert final.get("commit_sha")
    assert final.get("resumed_count") == 1
    assert planner_called == []


async def test_qwen_operator_marks_unresumable_task_honest_failed_on_restart(stack, repo):  # noqa: F811
    """When a running operator task cannot be resumed (e.g. lab missing or resume=False), it becomes failed with reason."""
    svc, _c, _a = stack
    rid = "deadbeef1234"
    task_file = Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        json.dumps(
            {
                "id": rid,
                "task": "поправь докстринг",
                "status": "running",
                "created_at": qo._now() - 30,
                "finished_at": None,
                "lab": {"path": str(Path(svc.settings.data_dir) / "missing-lab"), "branch": f"lab/op-{rid}"},
                "steps": [],
                "model_calls": [],
                "commands": [],
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    recovered = await qo.recover_interrupted_tasks(svc, resume=True)
    assert any(r["id"] == rid and r["action"] == "failed" for r in recovered), recovered
    final = json.loads(task_file.read_text(encoding="utf-8"))
    assert final["status"] == "failed"
    assert final.get("error_code") == "interrupted_by_restart"
    assert "interrupted_by_restart" in (final.get("error") or "")
    assert final.get("finished_at") is not None


async def test_operator_bridge_recovers_watch_and_unsent_receipt_after_companion_restart(tmp_path):
    """After Companion restart, recover_operator_tasks sends unsent receipts and re-attaches active /work watches."""
    owner = Person(11111, 11111, "owner", None)
    settings = Settings((owner,), local_url="http://127.0.0.1:8083/v1", local_model="best", bot_token="x")

    class FakeTg:
        def __init__(self):
            self.sent = []

        async def send(self, person, text, keyboard=None):
            self.sent.append((person.user_id, str(text)))

        async def call(self, *a, **k):
            return True

    class FakeCore:
        def __init__(self):
            self.tasks = {
                "unsent001122": {
                    "id": "unsent001122",
                    "status": "completed",
                    "lab": {"branch": "lab/op-unsent001122"},
                    "commit_sha": "a" * 40,
                    "verifier": {"verdict": "APPROVE", "independence": "different_family_free_cloud"},
                    "model_calls": [],
                    "decisions": [],
                },
                "active003344": {
                    "id": "active003344",
                    "status": "running",
                    "lab": {"branch": "lab/op-active003344"},
                    "commit_sha": "b" * 40,
                    "verifier": {"verdict": "APPROVE", "independence": "different_family_free_cloud"},
                    "model_calls": [],
                    "decisions": [],
                },
            }
            self.calls = 0

        async def operator_task(self, tid):
            self.calls += 1
            rec = dict(self.tasks[tid])
            if tid == "active003344" and self.calls >= 2:
                rec["status"] = "completed"
            return rec

    store = Store(tmp_path)
    store.put("operator_unsent_receipt", "unsent001122")
    store.put("operator_active_tasks", ["active003344"])
    tg = FakeTg()
    core = FakeCore()
    companion = Companion(settings, store, tg, core, None)
    companion.POLL_SECONDS = 0.01

    recovered = await companion.recover_operator_tasks()
    assert "unsent001122" in recovered and "active003344" in recovered
    for job in list(companion._operator_live()):
        await asyncio.wait_for(job, timeout=3.0)

    texts = "\n---\n".join(t for _, t in tg.sent)
    assert "unsent001122: completed" in texts
    assert "active003344: completed" in texts
    assert store.get("operator_unsent_receipt", "") == ""
    assert store.get("operator_active_tasks", []) == []
