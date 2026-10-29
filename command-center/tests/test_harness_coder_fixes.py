"""Harness coder fixes after the trio audit 29.10 (Codex H4-A/H4-B, Claude Hole #3, independent review F1).

Each test reproduces a hole on the real code path first (red), then pins the fix with a negative control.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from bcc.features import pc_agent


def _svc(tmp_path: Path):
    async def _publish(*_a, **_k):
        return None
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path, port=8801), approvals=SimpleNamespace(),
                           db=None, bus=SimpleNamespace(publish=_publish, subscribe=lambda *_a, **_k: None))


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


# ------------------------------------------------------------------ Task 1 (Codex H4-B): descendants after a NORMAL exit

@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_run_command_kills_a_detached_descendant_after_a_normal_exit(tmp_path):
    svc = _svc(tmp_path)
    rt = pc_agent.get_runtime(svc)
    parent = rt.workspace_dir / "parent_spawns_child.py"
    parent.write_text(
        "import subprocess, sys\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print(c.pid)\n", encoding="utf-8")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])     # NOT in the step's job
    try:
        res = asyncio.run(pc_agent.execute_pc_step(svc, rt, task_id=1, run_id=1, step_idx=0, tool="pc.run_command",
                                                   args={"argv": [sys.executable, str(parent)], "cwd": str(rt.workspace_dir)},
                                                   actor="owner", approved=True))
        assert res.get("exit_code") == 0, res
        child = int(str(res.get("stdout", "")).strip().split()[-1])
        assert _wait_gone(child), "the detached descendant outlived its finished pc.run_command step"
        assert psutil.pid_exists(bystander.pid) and bystander.poll() is None        # negative control: other processes live
    finally:
        bystander.kill()


# ------------------------------------------------------------------ Task 2 (Claude Hole #3 / review F1): approval binding

def _row(aid, topic="demo-skill", action="propose", task_id=7):
    return {"id": aid, "status": "approved", "kind": "tool", "task_id": task_id,
            "preview": (f'[Argon PC-Agent · T2 · self_modify] Задача #{task_id}: pc.self_modify '
                        f'{{"action": "{action}", "topic": "{topic}"}} — reason')}


def test_self_modify_approval_is_bound_to_topic_and_spent_once():
    rows = {21: _row(21), 22: _row(22, topic="another-skill")}

    async def get(aid):
        return rows.get(int(aid))
    svc = SimpleNamespace(approvals=SimpleNamespace(get=get))
    rt = SimpleNamespace(consumed_approvals=set())
    check = pc_agent._self_modify_approval_check
    assert asyncio.run(check(svc, rt, 21, 7, topic="demo-skill", action="propose")) == ""                 # positive owner control
    assert asyncio.run(check(svc, rt, 21, 7, topic="demo-skill", action="propose")) == "approval_scope_mismatch"   # reuse
    assert asyncio.run(check(svc, rt, 22, 7, topic="demo-skill", action="propose")) == "approval_scope_mismatch"   # other topic
    assert asyncio.run(check(svc, rt, 22, 7, topic="another-skill", action="apply")) == "approval_scope_mismatch"  # other action


class _Sess:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        return SimpleNamespace(rowcount=1)

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _pending_task(tmp_path, monkeypatch, *, change_args: bool):
    svc = _svc(tmp_path)
    svc.db = SimpleNamespace(session=lambda: _Sess())
    rt = pc_agent.get_runtime(svc)
    calls: list = []

    async def fake_exec(_svc, _rt, **kw):
        calls.append(kw)
        return {"ok": True, "verified": True, "summary": "done"}

    async def noop(*_a, **_k):
        return None
    for name in ("_finish_db_task", "_update_db_tool_call", "_mark_db_task_stopped"):
        monkeypatch.setattr(pc_agent, name, noop)

    async def fake_record(*_a, **_k):
        return 1
    monkeypatch.setattr(pc_agent, "_record_db_tool_call", fake_record)
    monkeypatch.setattr(pc_agent, "execute_pc_step", fake_exec)
    approved_args = {"path": str(rt.workspace_dir / "note.txt"), "text": "hello"}
    step_args = dict(approved_args, text="rm -rf / && exfiltrate") if change_args else dict(approved_args)
    rt.save_task_record(31, {
        "task_id": 31, "run_id": 41, "actor": "owner", "prompt": "write a note", "status": "waiting_approval",
        "steps_planned": [{"tool": "pc.write_file", "args": step_args}], "completed_step_indices": [], "step_results": [],
        "pending_approval": {"approval_id": 51, "step_index": 0, "tool": "pc.write_file", "args": approved_args,
                             "payload_sha256": pc_agent._compute_step_payload_sha256("pc.write_file", approved_args)},
    })
    rec = asyncio.run(pc_agent.resume_after_approval(svc, 51, approve=True, actor="owner", wait=True, wait_timeout_s=10))
    return rec, calls


def test_changed_step_arguments_after_approval_do_not_run(tmp_path, monkeypatch):
    rec, calls = _pending_task(tmp_path, monkeypatch, change_args=True)
    assert calls == []                                                        # nothing executed
    assert rec["status"] == "failed" and "approval_payload_mismatch" in rec["error"]


# ------------------------------------------------------------------ Task 3 (Codex H4-A): owner reconciliation of unknown spend

def _unknown_day(tmp_path):
    from bcc.features import tools_cloud
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    asyncio.run(tools_cloud.audit(svc, model="m-paid", provider="p", worker="w", cost_usd=0.0, status="billing_unknown",
                                  error="CancelledError", chars_out=10, chars_in=0))
    sha = tools_cloud.unresolved_unknown_spend(tmp_path)[0]["sha256"]
    return tools_cloud, svc, sha


def test_owner_reconciliation_unlocks_the_budget_and_counts_the_confirmed_cost(tmp_path):
    tc, svc, sha = _unknown_day(tmp_path)
    before = asyncio.run(tc.budget_state(svc, None))
    assert before["remaining_usd"] == 0 and before.get("billing_unknown") is True
    row = tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=sha, actual_cost_usd=0.05, owner_id="owner",
                                              evidence_note="provider usage page, request at 10:00Z")
    after = asyncio.run(tc.budget_state(svc, None))
    assert row["status"] == "billing_reconciled" and row["decided_by"] == "owner"
    assert not after.get("billing_unknown") and after["spent_usd"] == pytest.approx(0.05) and after["remaining_usd"] > 0
    assert tc.unresolved_unknown_spend(tmp_path) == []
    again = tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=sha, actual_cost_usd=0.07, owner_id="owner",
                                                evidence_note="duplicate")
    assert again["cost_usd"] == 0.05                                          # idempotent, the first decision stands
    lines = (tmp_path / "cloud_ask" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and '"billing_unknown"' in lines[0]                # append-only: the original row is untouched


@pytest.mark.parametrize("kw,exc", [
    ({"owner_id": "agent"}, PermissionError),
    ({"actual_cost_usd": -0.01}, ValueError),
    ({"actual_cost_usd": float("nan")}, ValueError),
    ({"evidence_note": "  "}, ValueError),
    ({"target_record_sha256": "0" * 64}, LookupError),
])
def test_bad_reconciliations_are_refused_and_the_budget_stays_locked(tmp_path, kw, exc):
    tc, svc, sha = _unknown_day(tmp_path)
    args = {"target_record_sha256": sha, "actual_cost_usd": 0.05, "owner_id": "owner", "evidence_note": "usage page", **kw}
    with pytest.raises(exc):
        tc.reconcile_unknown_spend_by_owner(tmp_path, **args)
    assert asyncio.run(tc.budget_state(svc, None))["remaining_usd"] == 0


def test_unchanged_step_arguments_run_once(tmp_path, monkeypatch):
    rec, calls = _pending_task(tmp_path, monkeypatch, change_args=False)      # positive control
    assert len(calls) == 1 and calls[0]["tool"] == "pc.write_file"
    assert "approval_payload_mismatch" not in str(rec.get("error") or "")


# ------------------------------------------------------------------ Task 4: harness execute-step cycle

def _harness_env(tmp_path, monkeypatch, stop=False):
    from bcc.config import Settings
    from bcc.db import Database
    from bcc.features import bossman_20, telegram_calls
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite+aiosqlite:///{tmp_path/'data/bcc.db'}",
                        ui_dir=tmp_path / "ui")
    (tmp_path / "data").mkdir()
    db = Database(settings.database_url)
    asyncio.run(db.create_all())
    vault = tmp_path / "vault"
    vault.mkdir()

    async def cfg(_svc):
        return {"root": str(vault), "write_folder": "BOSSMAN Memory"}
    monkeypatch.setattr(bossman_20, "load_config", cfg)
    monkeypatch.setattr(telegram_calls, "global_stop_active", lambda _svc, **_k: stop)
    svc = SimpleNamespace(settings=settings, db=db)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=svc)))
    return bossman_20, req, vault


def test_execute_step_checks_the_postcondition_learns_and_returns_a_receipt(tmp_path, monkeypatch):
    b20, req, vault = _harness_env(tmp_path, monkeypatch)
    done = tmp_path / "work" / "out.txt"
    done.parent.mkdir()
    done.write_text("harness ok", encoding="utf-8")
    res = asyncio.run(b20.http_harness_execute_step(req, {
        "step_id": "t4-ok", "title": "write out.txt", "files": [str(done)], "test": "this test",
        "postcondition": {"type": "file_contains", "path": str(done), "text": "harness ok"}}))
    assert res["ok"] is True and res["postcondition_ok"] is True
    assert all(res["db_counts"][k] == 1 for k in ("facts", "decisions", "procedures", "failures")), res["db_counts"]
    assert len(res["sha256_receipt"]) == 64 and int(res["sha256_receipt"], 16) >= 0
    assert "[t4-ok]" in (vault / "BOSSMAN Memory" / "2026-10-29-Bossman-Harness-Step-Lessons.md").read_text(encoding="utf-8")
    # the receipt is the sha256 of its own body: recompute it
    import hashlib
    import json
    body = {k: res[k] for k in ("step_id", "postcondition_ok", "postcondition_detail", "recalled", "db_counts",
                                "obsidian_written", "ts")}
    assert hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest() == res["sha256_receipt"]


def test_execute_step_with_a_missing_file_is_not_ok_and_is_learned_as_a_failure(tmp_path, monkeypatch):
    b20, req, _ = _harness_env(tmp_path, monkeypatch)
    res = asyncio.run(b20.http_harness_execute_step(req, {
        "step_id": "t4-bad", "title": "missing file",
        "postcondition": {"type": "file_exists", "path": str(tmp_path / "nope.txt")}}))
    assert res["ok"] is False and res["postcondition_ok"] is False
    assert res["db_counts"]["failures"] == 1
    none = asyncio.run(b20.http_harness_execute_step(req, {"step_id": "t4-none", "title": "no postcondition"}))
    assert none["ok"] is False                                                # no postcondition is never a pass


def test_execute_step_under_owner_stop_does_nothing(tmp_path, monkeypatch):
    b20, req, _ = _harness_env(tmp_path, monkeypatch, stop=True)
    res = asyncio.run(b20.http_harness_execute_step(req, {"step_id": "t4-stop", "title": "x",
                                                          "postcondition": {"type": "file_exists", "path": __file__}}))
    assert res["code"] == "STOP_ACTIVE"
    assert asyncio.run(b20._db_memory_counts(req.app.state.svc)) == {"facts": 0, "decisions": 0, "procedures": 0, "failures": 0}
