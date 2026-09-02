"""Deep Fix gate (F4.1): план верификации привязан к задаче до патча."""
from __future__ import annotations

import pytest

from bcc.features import deep_fix

from .conftest import FakeAdapter
from .helpers import make_stack


async def _drive(env, task_id: int, n: int = 8) -> str:
    for _ in range(n):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


def _evidence(path) -> list[dict]:
    return [{"kind": "file", "target": str(path), "expect": {"contains": "ok"}}]


async def test_flag_off_is_noop(env, monkeypatch):
    monkeypatch.delenv(deep_fix.FLAG, raising=False)
    target = env.settings.data_dir / "a.txt"; target.write_text("ok", encoding="utf-8")
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client)
    await env.client.post("/api/review/enable", json={"task_id": stack["task"]["id"],
                                                      "evidence": _evidence(target)})
    await env.client.post(f"/api/tasks/{stack['task']['id']}/retry")
    assert await _drive(env, stack["task"]["id"]) == "completed"
    st = (await env.client.get(f"/api/deep_fix/status?task_id={stack['task']['id']}")).json()
    assert st["enabled"] is False and st["bound"] == {}


async def test_plan_bound_at_first_run_and_unchanged_plan_completes(env, monkeypatch):
    monkeypatch.setenv(deep_fix.FLAG, "1")
    target = env.settings.data_dir / "b.txt"; target.write_text("ok", encoding="utf-8")
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client)
    await env.client.post("/api/review/enable", json={"task_id": stack["task"]["id"],
                                                      "evidence": _evidence(target)})
    await env.client.post(f"/api/tasks/{stack['task']['id']}/retry")
    assert await _drive(env, stack["task"]["id"]) == "completed"
    st = (await env.client.get(f"/api/deep_fix/status?task_id={stack['task']['id']}")).json()
    assert st["bound"]["plan_hash"] == st["current_plan_hash"] and st["bound"]["bound_run_id"]


async def test_moved_goalpost_cannot_complete(env, monkeypatch):
    """План привязан к файлу b (которого нет) → после старта план подменяют на
    файл c (который есть). Свежее доказательство для c есть, но завершение
    запрещено: план изменился → эскалация человеку."""
    monkeypatch.setenv(deep_fix.FLAG, "1")
    missing = env.settings.data_dir / "missing.txt"
    present = env.settings.data_dir / "c.txt"; present.write_text("ok", encoding="utf-8")
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client, max_steps=2)
    tid = stack["task"]["id"]
    await env.client.post("/api/review/enable", json={"task_id": tid, "max_review_retries": 3,
                                                      "evidence": _evidence(missing)})
    await env.client.post(f"/api/tasks/{tid}/retry")
    rid = await env.svc.engine.claim()
    await env.svc.engine.execute(rid)                    # 1-й прогон: FAILED (файла нет), план привязан
    bound = (await env.client.get(f"/api/deep_fix/status?task_id={tid}")).json()["bound"]["plan_hash"]
    # подмена плана после привязки
    await env.client.post("/api/review/enable", json={"task_id": tid, "max_review_retries": 3,
                                                      "evidence": _evidence(present)})
    status = await _drive(env, tid)
    assert status == "waiting_approval"
    appr = (await env.client.get("/api/approvals?status=pending")).json()
    assert any(a["kind"] == "review_escalation" and "goalpost" in a["preview"] for a in appr)
    st = (await env.client.get(f"/api/deep_fix/status?task_id={tid}")).json()
    assert st["bound"]["plan_hash"] == bound and st["current_plan_hash"] != bound
