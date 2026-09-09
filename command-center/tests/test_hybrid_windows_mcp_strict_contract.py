"""Negative controls for the Windows-MCP mechanics boundary.

Provider-shaped values must never be converted into successful Bossman effects
by Python truthiness or by accepting an error status as a valid response.
"""

import time

import pytest

from bcc.hybrid.adapters.windows_mcp import WindowsMcpAdapter
from bcc.hybrid.capabilities import (
    BackendUnavailableError,
    EffectCorrelation,
    MalformedResponseError,
)


class ContractClient:
    def __init__(self, responses, *, healthy=True):
        self.is_healthy = healthy
        self.responses = dict(responses)
        self.calls = []

    def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        return self.responses[tool_name]


def _corr(name):
    return EffectCorrelation(
        correlation_id=name,
        task_id="task-strict-contract",
        run_id="run-strict-contract",
        deadline_epoch_s=time.time() + 10.0,
    )


def test_launch_rejects_provider_error_status():
    client = ContractClient({"launch_app": {"status": "error", "pid": 1234}})
    adapter = WindowsMcpAdapter(client=client)

    with pytest.raises(MalformedResponseError, match="status='ok'"):
        adapter.launch_app("notepad.exe", [], _corr("launch-error"))


def test_click_rejects_provider_error_status_before_evidence_creation():
    client = ContractClient(
        {
            "get_window_state": {"active": True, "fingerprint": "fp"},
            "click": {"status": "error", "clicked": False},
        }
    )
    adapter = WindowsMcpAdapter(client=client)

    with pytest.raises(MalformedResponseError, match="status='ok'"):
        adapter.click_element({"role": "button", "name": "File"}, _corr("click-error"))


def test_close_rejects_truthy_string_false_instead_of_converting_to_true():
    client = ContractClient({"close_app": {"closed": "false"}})
    adapter = WindowsMcpAdapter(client=client)

    with pytest.raises(MalformedResponseError, match="close_app response schema"):
        adapter.close_app(1234, _corr("close-string-false"))


def test_non_boolean_health_claim_cannot_activate_optional_backend():
    client = ContractClient({"launch_app": {"status": "ok", "pid": 1234}}, healthy="true")
    adapter = WindowsMcpAdapter(client=client)

    with pytest.raises(BackendUnavailableError, match="Refusing to simulate"):
        adapter.launch_app("notepad.exe", [], _corr("health-string"))

    assert client.calls == []
