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


def test_readiness_waits_longer_than_a_cold_model_handshake():
    api = RecordingApi()
    backend = BossmanCodingBackend(api, project_id="p")
    with pytest.raises(ApiError, match="stop here"):
        backend.prepare(loop=None)
    method, path, timeout = api.calls[0]
    assert (method, path) == ("GET", "/api/coding-tasks/readiness")
    assert timeout == READINESS_TIMEOUT_S and timeout >= 120  # measured cold handshake on the owner box: 44 s
