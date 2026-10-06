"""The loop must wait for the live readiness handshake (a cold local model takes ~45 s)."""
from __future__ import annotations

import pytest

from bossman_v3.self_improvement.attempts import ApiError, BossmanCodingBackend, READINESS_TIMEOUT_S


class RecordingApi:
    def __init__(self):
        self.calls = []

    def request(self, method, path, payload=None, *, timeout=None):
        self.calls.append((method, path, timeout))
        return 200, {"available": False, "reason": "stop here"}

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload=None):
        return self.request("POST", path, payload)


def test_worker_model_selects_an_allowlisted_cloud_worker_for_the_attempt(tmp_path):
    import time as _time
    from types import SimpleNamespace
    backend = BossmanCodingBackend(RecordingApi(), project_id="p", model="worker:openrouter-free")
    ctx = SimpleNamespace(task={"editable": ["a.py"], "tests": ["tests/test_a.py"]}, instruction="fix",
                          deadline=_time.monotonic() + 600)
    body = backend.body(ctx, tmp_path)
    assert body["worker"] == "openrouter-free" and body["model"] is None
    plain = BossmanCodingBackend(RecordingApi(), project_id="p", model="some-local-model").body(ctx, tmp_path)
    assert "worker" not in plain and plain["model"] == "some-local-model"


def test_readiness_waits_longer_than_a_cold_model_handshake():
    api = RecordingApi()
    backend = BossmanCodingBackend(api, project_id="p")
    with pytest.raises(ApiError, match="stop here"):
        backend.prepare(loop=None)
    method, path, timeout = api.calls[0]
    assert (method, path) == ("GET", "/api/coding-tasks/readiness")
    assert timeout == READINESS_TIMEOUT_S and timeout >= 120  # measured cold handshake on the owner box: 44 s
