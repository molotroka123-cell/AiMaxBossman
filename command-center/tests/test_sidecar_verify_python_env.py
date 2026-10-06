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
