from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bcc.features import v15_economy as v15


def request_for(tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path)
    svc = SimpleNamespace(settings=settings)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=svc)))


def write_state(tmp_path, **overrides):
    root = tmp_path / "v1.5" / "economy"
    root.mkdir(parents=True, exist_ok=True)
    state = {"schema": "bossman.v1.5.economy-run/1", "run_id": "run-1",
             "pid": 12345, "status": "RUNNING", **overrides}
    (root / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_start_refuses_detached_live_worker_after_backend_restart(tmp_path, monkeypatch):
    write_state(tmp_path)
    monkeypatch.setattr(v15, "_pid_alive", lambda _pid: True)
    body = v15.StartBody(inbox=str(tmp_path))
    with pytest.raises(HTTPException) as caught:
        await v15.start(body, request_for(tmp_path))
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "V15_DETACHED_RUN_ACTIVE"


@pytest.mark.asyncio
async def test_start_requires_reconciliation_when_recorded_worker_is_dead(tmp_path, monkeypatch):
    write_state(tmp_path)
    monkeypatch.setattr(v15, "_pid_alive", lambda _pid: False)
    body = v15.StartBody(inbox=str(tmp_path))
    with pytest.raises(HTTPException) as caught:
        await v15.start(body, request_for(tmp_path))
    assert caught.value.detail["code"] == "V15_RECONCILE_REQUIRED"


@pytest.mark.asyncio
async def test_reconcile_marks_dead_run_and_status_stops_calling_it_running(tmp_path, monkeypatch):
    root = write_state(tmp_path)
    monkeypatch.setattr(v15, "_pid_alive", lambda _pid: False)
    out = await v15.reconcile(request_for(tmp_path))
    assert out["status"] == "INTERRUPTED_RECONCILED"
    saved = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
    assert saved["status"] == "INTERRUPTED_RECONCILED"
    status = await v15.status(request_for(tmp_path))
    assert status["running"] is False
    assert status.get("reconcile_required") is not True


@pytest.mark.asyncio
async def test_stop_is_durable_for_detached_worker(tmp_path):
    out = await v15.stop(request_for(tmp_path))
    assert out["status"] == "STOP_REQUESTED"
    assert (tmp_path / "v1.5" / "economy" / "STOP").read_text(encoding="utf-8") == "owner stop\n"
