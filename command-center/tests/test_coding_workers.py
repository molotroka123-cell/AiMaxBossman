"""Cloud workers for coding tasks: fixed allowlist, key only to the sidecar, owner key-file fallback."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features import coding_tasks as ct


def test_worker_command_is_fixed_by_the_allowlist():
    argv = ct._worker_command("openrouter-free")
    assert argv[1:4] == ["-I", "-m", "bossman.apprentice.local_sidecar"]
    assert argv[argv.index("--endpoint") + 1] == "https://openrouter.ai/api/v1"
    assert argv[argv.index("--model") + 1].endswith(":free")
    assert argv[argv.index("--api-key-env") + 1] == "BOSSMAN_WORKER_API_KEY"
    nim = ct._worker_command("nvidia-nim")
    assert nim[nim.index("--endpoint") + 1] == "https://integrate.api.nvidia.com/v1"
    ultra = ct._worker_command("nemotron-ultra-free")
    assert ultra[ultra.index("--model") + 1] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_worker_key_prefers_the_owner_key_file_then_env(tmp_path, monkeypatch):
    keyfile = tmp_path / "provider-keys.env"
    keyfile.write_text("OTHER=x\nNVIDIA_API_KEY=nv-test-value\n", encoding="utf-8")
    monkeypatch.setattr(ct, "OWNER_KEYS_FILE", keyfile)
    monkeypatch.setenv("NVIDIA_API_KEY", "stale-env-value")
    assert asyncio.run(ct._worker_key("NVIDIA_API_KEY", None)) == "nv-test-value"  # the owner's file wins
    monkeypatch.setattr(ct, "OWNER_KEYS_FILE", tmp_path / "missing.env")
    assert asyncio.run(ct._worker_key("NVIDIA_API_KEY", None)) == "stale-env-value"
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert asyncio.run(ct._worker_key("NVIDIA_API_KEY", None)) is None


def _svc(tmp_path):
    async def emit(*_a, **_kw):
        return None
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), bus=SimpleNamespace(emit=emit))


def test_a_cloud_worker_task_gets_only_its_own_key_and_command(tmp_path, monkeypatch):
    seen = {}

    def fake_execute(record, repo, body, context, env, command):
        seen.update(env=env, command=command)
        return {**record, "status": "completed", "finished_at": 1.0}
    monkeypatch.setattr(ct, "_execute", fake_execute)

    async def key(name, _svc):
        return f"secret-for-{name}"
    monkeypatch.setattr(ct, "_worker_key", key)
    monkeypatch.delenv("BOSSMAN_VERIFY_PYTHON", raising=False)  # the owner PC sets it; its forwarding has its own test
    body = ct.TaskIn(instruction="x", source_repo=str(tmp_path), allowed_paths=["a.py"], worker="nvidia-nim")
    asyncio.run(ct._run(_svc(tmp_path), {"id": "abcdef123456"}, tmp_path, body))
    assert seen["env"] == {"BOSSMAN_WORKER_API_KEY": "secret-for-NVIDIA_API_KEY"}
    assert seen["command"] == ct._worker_command("nvidia-nim")


def test_missing_worker_key_fails_the_task_visibly(tmp_path, monkeypatch):
    async def no_key(_name, _svc):
        return None
    monkeypatch.setattr(ct, "_worker_key", no_key)
    body = ct.TaskIn(instruction="x", source_repo=str(tmp_path), allowed_paths=["a.py"], worker="glm-flash")
    svc = _svc(tmp_path)
    asyncio.run(ct._run(svc, {"id": "abcdef123456"}, tmp_path, body))
    rec = ct._read(svc, "abcdef123456")
    assert rec["status"] == "failed" and "OPENROUTER_API_KEY" in rec["error"]


def test_unknown_worker_is_refused_before_anything_runs(tmp_path):
    app = FastAPI()
    app.state.svc = _svc(tmp_path)
    app.include_router(ct.router, prefix="/api")
    with TestClient(app) as client:
        out = client.post("/api/coding-tasks", json={"instruction": "x", "source_repo": str(tmp_path),
                                                     "allowed_paths": ["a.py"], "worker": "evil-endpoint"})
    assert out.status_code == 422 and out.json()["detail"]["code"] == "UNKNOWN_WORKER"
