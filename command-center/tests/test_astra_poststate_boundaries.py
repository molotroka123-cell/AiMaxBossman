"""Independent negative controls for release post-state observations."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.finalize import _override_reason, finalize_task
from bcc.v2.verification import ExpectedState, ObservedState, _compare, verify

from .helpers import make_stack
from .test_p0_completion_truth import _ChallengeManager, _bind_session


@pytest.mark.parametrize("url", [
    "https://evil.test/?next=youtube.com/watch",
    "https://youtube.com.evil.test/watch",
    "https://notyoutube.com/watch",
    "https://youtube.com@evil.test/watch",
    "https://www.youtube.com/watching",
    "https://evil.test/#youtube.com/watch",
    "data:text/html,youtube.com/watch",
])
def test_browser_domain_goal_cannot_be_satisfied_by_a_url_substring(url):
    exp = ExpectedState("browser", "session", {"url_contains": "youtube.com/watch"})
    obs = ObservedState("browser", "session", {"url": url, "challenge": False}, time.time())
    assert _compare(exp, obs)[0] != "VERIFIED"


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abc",
    "https://www.youtube.com/watch?v=abc",
    "https://m.youtube.com/watch?v=abc",
])
def test_browser_domain_goal_accepts_the_actual_host_and_path(url):
    exp = ExpectedState("browser", "session", {"url_contains": "youtube.com/watch"})
    obs = ObservedState("browser", "session", {"url": url, "challenge": False}, time.time())
    assert _compare(exp, obs)[0] == "VERIFIED"


def test_owner_takeover_is_not_completion_evidence():
    exp = ExpectedState("browser", "session", {"url_contains": "youtube.com/watch"})
    obs = ObservedState("browser", "session", {
        "url": "https://youtube.com/watch?v=abc", "challenge": False, "takeover": True,
    }, time.time())
    assert _compare(exp, obs)[0] == "BLOCKED"


async def test_unconfigured_file_observer_never_reads_a_host_file(tmp_path, monkeypatch):
    target = tmp_path / "outside.txt"
    target.write_text("private outside value", encoding="utf-8")
    reads = []
    original = Path.read_bytes

    def watched(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", watched)
    result = await verify(ExpectedState("file", str(target), {"exists": True}),
                          svc=None, task={}, roots=[])
    assert result.status == "UNVERIFIED"
    assert not reads
    assert "private outside value" not in repr(result)


async def test_configured_file_observer_still_verifies_real_bytes(tmp_path):
    target = tmp_path / "allowed.txt"
    target.write_text("observed value", encoding="utf-8")
    result = await verify(ExpectedState("file", str(target), {"contains": "observed value"}),
                          svc=None, task={}, roots=[tmp_path])
    assert result.status == "VERIFIED"


async def test_snapshot_failure_cannot_finalize_a_browser_task(env, monkeypatch):
    from bcc.features import browser as feature

    class Unavailable(_ChallengeManager):
        async def snapshot(self, *args, **kwargs):
            raise ConnectionError("browser disconnected")

    monkeypatch.setattr(feature, "_mgr", lambda svc: Unavailable())
    stack = await make_stack(env.client, prompt="plain task")
    task_id = stack["task"]["id"]
    await _bind_session(env, task_id, stack["agent"]["id"])
    run_id = await env.svc.engine.claim()
    decision = await finalize_task(env.svc.engine, run_id, task_id, answer="Done", usage={})
    assert not decision.ok
    assert decision.checks.get("verification") == "UNVERIFIED"


async def test_review_override_cannot_waive_a_live_challenge(env, monkeypatch):
    from bcc.features import browser as feature

    monkeypatch.setattr(feature, "_mgr", lambda svc: _ChallengeManager(captcha=True))
    stack = await make_stack(env.client, prompt="plain task")
    task = stack["task"]
    await _bind_session(env, task["id"], stack["agent"]["id"])
    assert "BROWSER_CHALLENGE" in await _override_reason(env.svc, task, [])


@pytest.mark.parametrize("change", ["revoke", "delete", "new_run", "new_obligation"])
async def test_review_authority_is_current_after_poststate_observation(env, monkeypatch, change):
    import bcc.finalize as finalizer
    from bcc.db import approvals, tasks, task_runs

    stack = await make_stack(env.client, prompt="plain task")
    task_id = stack["task"]["id"]
    run_id = await env.svc.engine.claim()
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(status="waiting_approval"))
        aid = (await session.execute(sa.insert(approvals).values(
            task_id=task_id, run_id=run_id, kind="review_escalation", status="approved",
        ))).inserted_primary_key[0]
        await session.commit()
    original = finalizer._override_reason

    async def changed_after_observing(svc, task, rows):
        reason = await original(svc, task, rows)
        async with svc.db.session() as session:
            if change == "revoke":
                await session.execute(sa.update(approvals).where(approvals.c.id == aid).values(status="revoked"))
            elif change == "delete":
                await session.execute(sa.delete(approvals).where(approvals.c.id == aid))
            elif change == "new_run":
                await session.execute(sa.insert(task_runs).values(task_id=task_id, status="queued", attempt=2))
            else:
                await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(
                    meta={"required_effects": [{"kind": "file", "target": "/missing", "expect": {"exists": True}}]}))
            await session.commit()
        return reason

    monkeypatch.setattr(finalizer, "_override_reason", changed_after_observing)
    assert not await finalizer.finalize_override(env.svc, task_id, approval={"id": aid, "run_id": run_id})
    actual = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]
    assert actual["status"] == "waiting_approval"
