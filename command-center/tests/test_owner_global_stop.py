"""The owner STOP reaches a live Terminal process even without a core task."""
from __future__ import annotations

import asyncio
import re
import sys

import psutil
import pytest
import sqlalchemy as sa

from bcc.v2.tables import terminal_sessions


async def _running_terminal(env) -> str:
    command = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    first = await env.client.post("/api/terminal/run", json={
        "mode": "project_host", "cwd": str(env.settings.data_dir), "command": command})
    assert first.status_code == 202
    approval_id = first.json()["error"]["approval_id"]
    decision = await env.client.post(f"/api/approvals/{approval_id}",
                                     json={"approve": True, "by": "owner-test"})
    assert decision.status_code == 200
    started = await env.client.post("/api/terminal/run", json={
        "mode": "project_host", "cwd": str(env.settings.data_dir),
        "command": command, "approval_id": approval_id})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    assert (await env.client.get(f"/api/terminal/sessions/{session_id}")).json()["finished"] is False
    return session_id


async def test_owner_stop_reaches_terminal_without_core_task(env):
    session_id = await _running_terminal(env)
    try:
        preview = (await env.client.get("/api/control-plane/active")).json()
        assert preview["active"]["tasks"] == []
        assert session_id in preview["active"]["terminal"]

        stopped = (await env.client.post("/api/control-plane/stop-all")).json()
        assert stopped["ok"] is True, stopped
        assert session_id in stopped["stopped"]["terminal"]
        assert stopped["remaining"]["terminal"] == []
        assert stopped["computer"]["persisted"] is True
        status = (await env.client.get(f"/api/terminal/sessions/{session_id}")).json()
        assert status["finished"] is True
        async with env.svc.db.session() as s:
            row = (await s.execute(sa.select(terminal_sessions.c.status).where(
                terminal_sessions.c.id == session_id))).scalar_one()
        assert row == "killed"
    finally:
        if not env.svc.terminal.sessions[session_id].finished:
            await env.svc.terminal.kill(session_id)


async def test_owner_stop_reports_terminal_failure_and_keeps_computer_locked(env, monkeypatch):
    session_id = await _running_terminal(env)
    original = env.svc.terminal.kill

    async def fail_kill(_session_id):
        raise OSError("simulated stop failure")

    try:
        monkeypatch.setattr(env.svc.terminal, "kill", fail_kill)
        response = (await env.client.post("/api/control-plane/stop-all")).json()
        assert response["ok"] is False
        assert response["computer"]["persisted"] is True
        assert session_id in response["remaining"]["terminal"]
        assert any(error["plane"] == "terminal" for error in response["errors"])
    finally:
        monkeypatch.setattr(env.svc.terminal, "kill", original)
        await env.svc.terminal.kill(session_id)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill /T child-tree contract")
async def test_owner_stop_kills_terminal_shell_child(env):
    command = (f'"{sys.executable}" -c "import os,time;'
               'print(\'BOSSMAN_CHILD_PID=\'+str(os.getpid()),flush=True);time.sleep(60)"')
    first = await env.client.post("/api/terminal/run", json={
        "mode": "project_host", "cwd": str(env.settings.data_dir), "command": command})
    assert first.status_code == 202
    approval_id = first.json()["error"]["approval_id"]
    assert (await env.client.post(f"/api/approvals/{approval_id}",
                                  json={"approve": True, "by": "owner-test"})).status_code == 200
    started = await env.client.post("/api/terminal/run", json={
        "mode": "project_host", "cwd": str(env.settings.data_dir),
        "command": command, "approval_id": approval_id})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    child_pid = None
    try:
        for _ in range(100):
            status = (await env.client.get(f"/api/terminal/sessions/{session_id}")).json()
            match = re.search(r"BOSSMAN_CHILD_PID=(\d+)", "\n".join(status["output_tail"]))
            if match:
                child_pid = int(match.group(1))
                break
            await asyncio.sleep(0.05)
        assert child_pid is not None, "Terminal child did not announce its PID"
        assert psutil.pid_exists(child_pid)

        stopped = (await env.client.post("/api/control-plane/stop-all")).json()
        assert stopped["ok"] is True, stopped
        for _ in range(100):
            if not psutil.pid_exists(child_pid):
                break
            await asyncio.sleep(0.05)
        assert not psutil.pid_exists(child_pid), "STOP left the Terminal child running"
    finally:
        if session_id in env.svc.terminal.sessions and not env.svc.terminal.sessions[session_id].finished:
            await env.svc.terminal.kill(session_id)
