"""The coding sidecar gets the owner's test interpreter, and nothing else new, from the backend's environment.

Owner PC 06.10.2026, task cecbba1c42a9 on the installed bundle c79eaec2: the backend's own check ran the zone tests
with BOSSMAN_VERIFY_PYTHON (pytest), but the sidecar - whose environment is explicit by contract - never received it,
so the worker's own `run_tests` fell back to unittest (exit 5) on pytest-style tests and the worker fixed blind.
"""
from __future__ import annotations

import asyncio
import sys

from bcc.features import coding_tasks, plugins


def _env(monkeypatch, **environ):
    async def no_cred(name, svc):  # noqa: ARG001
        return None
    monkeypatch.setattr(plugins, "resolve_cred", no_cred)
    for k in ("BOSSMAN_VERIFY_PYTHON", "BOSSMAN_OPENHANDS_MODEL", "SOME_OWNER_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in environ.items():
        monkeypatch.setenv(k, v)
    return asyncio.run(coding_tasks._sidecar_env(object()))


def test_the_owner_test_interpreter_reaches_the_sidecar(monkeypatch):
    env = _env(monkeypatch, BOSSMAN_VERIFY_PYTHON=sys.executable)
    assert env.get("BOSSMAN_VERIFY_PYTHON") == sys.executable


def test_a_missing_interpreter_path_is_not_forwarded(monkeypatch, tmp_path):
    env = _env(monkeypatch, BOSSMAN_VERIFY_PYTHON=str(tmp_path / "nope" / "python.exe"))
    assert "BOSSMAN_VERIFY_PYTHON" not in env


def test_the_rest_of_the_backend_environment_still_stays_out(monkeypatch):
    env = _env(monkeypatch, BOSSMAN_VERIFY_PYTHON=sys.executable, SOME_OWNER_SECRET="s3cr3t")
    assert "SOME_OWNER_SECRET" not in env and set(env) <= {"BOSSMAN_VERIFY_PYTHON"}


def test_a_cloud_worker_run_also_gets_the_test_interpreter_and_its_key(monkeypatch):
    """Cycles 10-13 (06.10): the cloud-worker branch of `_run` passed only the worker key, so the worker's own
    run_tests fell back to unittest and had no red/green signal."""
    captured = {}

    async def key(name, svc):  # noqa: ARG001
        return "k-123"

    def fake_execute(record, repo, body, context, env, command):  # noqa: ARG001
        captured["env"], captured["command"] = env, command
        return {**record, "status": "completed"}

    class Bus:
        async def emit(self, *a, **k):  # noqa: ARG002
            return None

    class Svc:
        bus = Bus()

    monkeypatch.setattr(coding_tasks, "_worker_key", key)
    monkeypatch.setattr(coding_tasks, "_execute", fake_execute)
    monkeypatch.setattr(coding_tasks, "_write", lambda svc, rec: None)
    monkeypatch.setenv("BOSSMAN_VERIFY_PYTHON", sys.executable)
    monkeypatch.setenv("SOME_OWNER_SECRET", "s3cr3t")
    worker = next(iter(coding_tasks.WORKERS))
    body = type("B", (), {"worker": worker})()
    asyncio.run(coding_tasks._run(Svc(), {"id": "t1"}, __import__("pathlib").Path("."), body))
    env = captured["env"]
    assert env.get("BOSSMAN_VERIFY_PYTHON") == sys.executable
    assert env.get(coding_tasks._WORKER_KEY_ENV) == "k-123"
    assert "SOME_OWNER_SECRET" not in env
