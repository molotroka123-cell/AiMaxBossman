"""A cloud-worker coding task must not handshake the LOCAL sidecar.

Owner PC 06.10: every POST /api/coding-tasks with worker="glm-flash" first ran
readiness(), whose handshake starts the configured local sidecar and makes one
real tool call against the local Ollama model (27 GB into the GPU) — for a task
that then runs on OpenRouter. The local worker's handshake must stay.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features import coding_tasks as ct


class _FakeClient:
    pass


def _wire(monkeypatch, tmp_path, *, key="sk-test", runtime=True, handshake_ok=False):
    calls: list[str] = []

    def fake_handshake(command, env=None):
        calls.append(command)
        return {"ok": handshake_ok, "reason": "scripted handshake"}

    async def roots(_svc):
        return [tmp_path]

    async def worker_key(_name, _svc):
        return key

    monkeypatch.setattr(ct, "_handshake", fake_handshake)
    monkeypatch.setattr(ct, "allowed_roots", roots)
    monkeypatch.setattr(ct, "_worker_key", worker_key)
    monkeypatch.setattr(ct, "_sidecar_command", lambda: "local-sidecar --model qwen-27b")
    if runtime:
        monkeypatch.setattr(ct, "_runtime", lambda: (SimpleNamespace(OpenHandsClient=_FakeClient), object(), ""))
    else:
        monkeypatch.setattr(ct, "_runtime", lambda: (None, None, "runtime missing"))
    return calls


def _post(tmp_path, **extra):
    app = FastAPI()

    async def emit(*_a, **_kw):
        return None
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), bus=SimpleNamespace(emit=emit))
    app.include_router(ct.router, prefix="/api")
    # allowed_paths empty: the request stops with 422 right AFTER readiness, so nothing runs.
    with TestClient(app) as client:
        return client.post("/api/coding-tasks", json={"instruction": "x", "source_repo": str(tmp_path),
                                                      "allowed_paths": [], **extra})


def test_cloud_worker_task_does_not_handshake_the_local_sidecar(tmp_path, monkeypatch):
    calls = _wire(monkeypatch, tmp_path)
    out = _post(tmp_path, worker="glm-flash")
    assert calls == []                      # the local model was never touched
    assert out.status_code == 422           # readiness passed; the next check refused the body


def test_local_worker_task_still_handshakes(tmp_path, monkeypatch):
    # negative control: the same request without a cloud worker DOES run the local handshake
    calls = _wire(monkeypatch, tmp_path)
    out = _post(tmp_path)
    assert calls == ["local-sidecar --model qwen-27b"]
    assert out.status_code == 503 and out.json()["detail"]["code"] == "OPENHANDS_UNAVAILABLE"


def test_cloud_worker_without_a_key_is_not_ready_and_still_no_handshake(tmp_path, monkeypatch):
    calls = _wire(monkeypatch, tmp_path, key=None)
    out = _post(tmp_path, worker="glm-flash")
    assert calls == []
    assert out.status_code == 503
    assert "OPENROUTER_API_KEY" in out.json()["detail"]["message"]


def test_missing_worker_key_refusal_tells_the_owner_what_to_do(tmp_path, monkeypatch):
    # UX sweep 09.10 («🚀 Bossman, работай здесь»): the toast said «Подробности — в логах сервера»,
    # the generic 5xx hint, although nothing failed on the server — a key was simply not connected.
    _wire(monkeypatch, tmp_path, key=None)
    detail = _post(tmp_path, worker="glm-flash").json()["detail"]
    assert "OpenRouter" in detail["hint"] and "Локальная модель Bossman" in detail["hint"]
    _wire(monkeypatch, tmp_path, key=None)
    detail = _post(tmp_path, worker="nvidia-nim").json()["detail"]
    assert "NVIDIA_API_KEY" in detail["hint"] and "provider-keys.env" in detail["hint"]


def test_cloud_worker_without_runtime_is_not_ready(tmp_path, monkeypatch):
    calls = _wire(monkeypatch, tmp_path, runtime=False)
    out = _post(tmp_path, worker="glm-flash")
    assert calls == []
    assert out.status_code == 503 and out.json()["detail"]["message"] == "runtime missing"
