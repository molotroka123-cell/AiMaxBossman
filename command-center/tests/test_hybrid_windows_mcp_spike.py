
import pytest
import time
from bcc.hybrid.capabilities import (
    EffectCorrelation,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyRevokedError,
    MalformedResponseError,
    StateVerificationFailedError,
)
from bcc.hybrid.adapters.windows_mcp import WindowsMcpAdapter, LegacyDesktopAdapter

class FakeMcpClient:
    def __init__(self, healthy=True, should_corrupt_response=False):
        self.is_healthy = healthy
        self.should_corrupt_response = should_corrupt_response
        self.pid = 9999
        self.calls = []

    def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        if self.should_corrupt_response:
            return "MALFORMED_NON_DICT"

        if tool_name == "get_window_state":
            return {"title": "Notepad", "fingerprint": "fp_notepad_v1", "active": True}
        if tool_name == "click":
            return {"status": "ok", "clicked": True, "target": args["target"]}
        if tool_name == "launch_app":
            return {"status": "ok", "pid": 12345}
        if tool_name == "close_app":
            return {"closed": True}
        return {"status": "ok"}

def test_windows_mcp_spike_benign_operation_with_bossman_verification():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = EffectCorrelation(
        correlation_id="spike-01",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
        expected_target_fingerprint="fp_notepad_v1",
    )

    verified_state_checked = False
    def independent_verifier(payload):
        nonlocal verified_state_checked
        verified_state_checked = True
        return True

    target = {"role": "button", "name": "File"}
    obs = adapter.click_element(target, corr, post_state_verifier=independent_verifier)

    assert obs.raw_data["status"] == "ok"
    assert obs.verified_by_bossman is True
    assert verified_state_checked is True

def test_windows_mcp_spike_fingerprint_mismatch_aborts():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = EffectCorrelation(
        correlation_id="spike-02",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
        expected_target_fingerprint="fp_calc_v1", # Mismatch! Window is Notepad
    )

    with pytest.raises(PolicyRevokedError) as exc:
        adapter.click_element({"role": "button"}, corr)
    assert "Target window fingerprint changed" in str(exc.value)

def test_windows_mcp_spike_in_flight_cancellation():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    is_cancelled = True
    corr = EffectCorrelation(
        correlation_id="spike-03",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
        cancellation_token=lambda: is_cancelled,
    )

    with pytest.raises(OperationCancelledError):
        adapter.click_element({"role": "button"}, corr)

def test_windows_mcp_spike_malformed_response():
    client = FakeMcpClient(should_corrupt_response=True)
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = EffectCorrelation(
        correlation_id="spike-04",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
    )

    with pytest.raises(MalformedResponseError):
        adapter.click_element({"role": "button"}, corr)

def test_windows_mcp_spike_engine_claims_success_but_poststate_differs():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = EffectCorrelation(
        correlation_id="spike-05",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
        expected_target_fingerprint="fp_notepad_v1",
    )

    def independent_verifier_fails(payload):
        return False  # Target element did not change or file did not write!

    with pytest.raises(StateVerificationFailedError):
        adapter.click_element({"role": "button"}, corr, post_state_verifier=independent_verifier_fails)

def test_windows_mcp_spike_fallback_to_legacy_when_disabled():
    adapter = WindowsMcpAdapter(client=None, enabled=False)
    corr = EffectCorrelation(
        correlation_id="spike-06",
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + 10.0,
    )
    obs = adapter.click_element({"role": "button"}, corr)
    assert obs.raw_data["backend"] == "legacy"
    assert obs.source_runtime.backend_type == "legacy"
