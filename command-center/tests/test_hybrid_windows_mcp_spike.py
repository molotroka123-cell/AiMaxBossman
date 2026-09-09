import time

import pytest

from bcc.hybrid.capabilities import (
    BackendUnavailableError,
    DesktopRuntime,
    EffectCorrelation,
    MalformedResponseError,
    Observation,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyRevokedError,
    RuntimeIdentity,
    StateVerificationFailedError,
)
from bcc.hybrid.adapters.windows_mcp import LegacyDesktopAdapter, WindowsMcpAdapter


class FakeMcpClient:
    def __init__(
        self,
        healthy=True,
        should_corrupt_response=False,
        *,
        close_value=True,
        state_delay_s=0.0,
    ):
        self.is_healthy = healthy
        self.should_corrupt_response = should_corrupt_response
        self.close_value = close_value
        self.state_delay_s = state_delay_s
        self.pid = 9999
        self.calls = []

    def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        if self.should_corrupt_response:
            return "MALFORMED_NON_DICT"

        if tool_name == "get_window_state":
            if self.state_delay_s:
                time.sleep(self.state_delay_s)
            return {"title": "Notepad", "fingerprint": "fp_notepad_v1", "active": True}
        if tool_name == "click":
            return {"status": "ok", "clicked": True, "target": args["target"]}
        if tool_name == "launch_app":
            return {"status": "ok", "pid": 12345}
        if tool_name == "close_app":
            return {"closed": self.close_value}
        return {"status": "ok"}


class RecordingLegacyAdapter(DesktopRuntime):
    """Test-only stand-in for an actually wired Bossman legacy runtime."""

    def __init__(self):
        self.calls = []

    def get_runtime_identity(self):
        return RuntimeIdentity(
            name="test_real_legacy",
            version="1",
            backend_type="legacy",
            is_healthy=True,
        )

    def launch_app(self, executable, args, correlation):
        self.calls.append(("launch_app", executable, list(args)))
        return {"status": "ok", "executor": "legacy-real", "pid": 4321}

    def click_element(self, target_spec, correlation):
        self.calls.append(("click_element", target_spec))
        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data={"clicked": True, "target": target_spec, "backend": "legacy-real"},
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

    def get_window_state(self, target_spec, correlation):
        self.calls.append(("get_window_state", target_spec))
        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data={"window_title": "Real legacy window", "active": True},
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

    def close_app(self, pid, correlation):
        self.calls.append(("close_app", pid))
        return True


def _corr(name, *, fingerprint=None, cancelled=False, deadline_s=10.0):
    return EffectCorrelation(
        correlation_id=name,
        task_id="task-spike",
        run_id="run-spike",
        deadline_epoch_s=time.time() + deadline_s,
        expected_target_fingerprint=fingerprint,
        cancellation_token=(lambda: True) if cancelled else None,
    )


def test_windows_mcp_spike_benign_operation_with_bossman_verification():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-01", fingerprint="fp_notepad_v1")

    verified_state_checked = False

    def independent_verifier(payload):
        nonlocal verified_state_checked
        verified_state_checked = True
        return True

    target = {"role": "button", "name": "File"}
    obs = adapter.click_element(
        target,
        corr,
        post_state_verifier=independent_verifier,
    )

    assert obs.raw_data["status"] == "ok"
    assert obs.verified_by_bossman is True
    assert verified_state_checked is True


def test_windows_mcp_spike_fingerprint_mismatch_aborts():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-02", fingerprint="fp_calc_v1")

    with pytest.raises(PolicyRevokedError) as exc:
        adapter.click_element({"role": "button"}, corr)
    assert "Target window fingerprint changed" in str(exc.value)


def test_windows_mcp_spike_in_flight_cancellation():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-03", cancelled=True)

    with pytest.raises(OperationCancelledError):
        adapter.click_element({"role": "button"}, corr)


def test_windows_mcp_rechecks_deadline_after_target_observation():
    client = FakeMcpClient(state_delay_s=0.08)
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-deadline-race", deadline_s=0.05)

    with pytest.raises(OperationTimeoutError, match="prior to effect commit"):
        adapter.click_element({"role": "button"}, corr)

    assert [name for name, _ in client.calls] == ["get_window_state"]


def test_windows_mcp_spike_malformed_response():
    client = FakeMcpClient(should_corrupt_response=True)
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-04")

    with pytest.raises(MalformedResponseError):
        adapter.click_element({"role": "button"}, corr)


def test_windows_mcp_spike_engine_claims_success_but_poststate_differs():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-05", fingerprint="fp_notepad_v1")

    def independent_verifier_fails(payload):
        return False

    with pytest.raises(StateVerificationFailedError):
        adapter.click_element(
            {"role": "button"},
            corr,
            post_state_verifier=independent_verifier_fails,
        )


def test_unconfigured_legacy_placeholder_is_unhealthy_and_fail_closed():
    legacy = LegacyDesktopAdapter()
    corr = _corr("spike-06")

    identity = legacy.get_runtime_identity()
    assert identity.backend_type == "legacy"
    assert identity.is_healthy is False
    assert identity.metadata["fail_closed"] is True

    with pytest.raises(BackendUnavailableError, match="Refusing to simulate"):
        legacy.click_element({"role": "button"}, corr)


def test_windows_mcp_disabled_without_real_legacy_blocks_instead_of_faking_effect():
    adapter = WindowsMcpAdapter(client=None, enabled=False)
    corr = _corr("spike-07")

    with pytest.raises(BackendUnavailableError, match="Refusing to simulate"):
        adapter.click_element({"role": "button"}, corr)


def test_windows_mcp_disabled_uses_explicitly_injected_legacy_runtime():
    legacy = RecordingLegacyAdapter()
    adapter = WindowsMcpAdapter(
        client=None,
        enabled=False,
        legacy_fallback=legacy,
    )
    corr = _corr("spike-08")

    obs = adapter.click_element({"role": "button"}, corr)

    assert obs.raw_data["backend"] == "legacy-real"
    assert obs.source_runtime.backend_type == "legacy"
    assert legacy.calls == [("click_element", {"role": "button"})]


def test_windows_mcp_fallback_honors_independent_poststate_verifier():
    legacy = RecordingLegacyAdapter()
    adapter = WindowsMcpAdapter(
        client=None,
        enabled=False,
        legacy_fallback=legacy,
    )
    corr = _corr("spike-fallback-proof")

    obs = adapter.click_element(
        {"role": "button", "name": "Save"},
        corr,
        post_state_verifier=lambda payload: payload.get("clicked") is True,
    )

    assert obs.verified_by_bossman is True
    assert obs.raw_data["backend"] == "legacy-real"


def test_windows_mcp_unhealthy_client_never_receives_effect_and_uses_real_fallback():
    client = FakeMcpClient(healthy=False)
    legacy = RecordingLegacyAdapter()
    adapter = WindowsMcpAdapter(
        client=client,
        enabled=True,
        legacy_fallback=legacy,
    )
    corr = _corr("spike-09")

    result = adapter.launch_app("notepad.exe", ["example.txt"], corr)

    assert result["executor"] == "legacy-real"
    assert client.calls == []
    assert legacy.calls == [("launch_app", "notepad.exe", ["example.txt"])]


def test_windows_mcp_unhealthy_client_without_real_fallback_blocks():
    client = FakeMcpClient(healthy=False)
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("spike-10")

    with pytest.raises(BackendUnavailableError):
        adapter.launch_app("notepad.exe", [], corr)
    assert client.calls == []


def test_windows_mcp_rejects_non_boolean_close_claim():
    client = FakeMcpClient(close_value="false")
    adapter = WindowsMcpAdapter(client=client, enabled=True)

    with pytest.raises(MalformedResponseError, match="closed='false'"):
        adapter.close_app(12345, _corr("spike-close-type"))


def test_windows_mcp_propagates_correlation_id_to_all_sidecar_calls():
    client = FakeMcpClient()
    adapter = WindowsMcpAdapter(client=client, enabled=True)
    corr = _corr("corr-all-ops")

    adapter.launch_app("notepad.exe", [], corr)
    adapter.get_window_state({"title": "Notepad"}, corr)
    adapter.close_app(12345, corr)

    assert [name for name, _ in client.calls] == [
        "launch_app",
        "get_window_state",
        "close_app",
    ]
    assert all(args["correlation_id"] == "corr-all-ops" for _, args in client.calls)
