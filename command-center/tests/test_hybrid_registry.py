import pytest

from bcc.hybrid.capabilities import (
    BackendUnavailableError,
    DesktopRuntime,
    Observation,
    RuntimeIdentity,
)
from bcc.hybrid.registry import AdapterRegistry


class DummyLegacyDesktop(DesktopRuntime):
    def __init__(self, *, healthy=True):
        self.healthy = healthy

    def get_runtime_identity(self):
        return RuntimeIdentity(
            name="legacy",
            version="1.0",
            backend_type="legacy",
            is_healthy=self.healthy,
        )

    def launch_app(self, e, a, c):
        return {"status": "ok", "backend": "legacy"}

    def click_element(self, t, c):
        return Observation("c1", {}, "now", self.get_runtime_identity(), True)

    def get_window_state(self, t, c):
        return Observation("c1", {}, "now", self.get_runtime_identity(), True)

    def close_app(self, p, c):
        return True


class DummyMcpDesktop(DesktopRuntime):
    def __init__(self, *, healthy=True):
        self.healthy = healthy

    def get_runtime_identity(self):
        return RuntimeIdentity(
            name="windows_mcp",
            version="0.1",
            backend_type="windows_mcp",
            is_healthy=self.healthy,
        )

    def launch_app(self, e, a, c):
        return {"status": "ok", "backend": "windows_mcp"}

    def click_element(self, t, c):
        return Observation("c1", {}, "now", self.get_runtime_identity(), False)

    def get_window_state(self, t, c):
        return Observation("c1", {}, "now", self.get_runtime_identity(), False)

    def close_app(self, p, c):
        return True


class ExplodingDesktop(DummyMcpDesktop):
    def get_runtime_identity(self):
        raise RuntimeError("health probe exploded")


def test_registry_default_is_legacy(monkeypatch):
    monkeypatch.delenv("BCC_DESKTOP_BACKEND", raising=False)
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", DummyMcpDesktop())

    runtime = reg.get_desktop_runtime()
    assert runtime.get_runtime_identity().backend_type == "legacy"


def test_registry_feature_flag_selection(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", DummyMcpDesktop())

    runtime = reg.get_desktop_runtime()
    assert runtime.get_runtime_identity().backend_type == "windows_mcp"


def test_registry_fallback_on_unregistered(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "non_existent_oss")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())

    runtime = reg.get_desktop_runtime()
    assert runtime.get_runtime_identity().backend_type == "legacy"


def test_registry_falls_back_when_selected_optional_backend_is_unhealthy(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    legacy = DummyLegacyDesktop(healthy=True)
    mcp = DummyMcpDesktop(healthy=False)
    reg.register_desktop("legacy", legacy)
    reg.register_desktop("windows_mcp", mcp)

    runtime = reg.get_desktop_runtime()

    assert runtime is legacy
    assert runtime.get_runtime_identity().is_healthy is True


def test_registry_falls_back_when_selected_backend_health_probe_raises(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    legacy = DummyLegacyDesktop(healthy=True)
    reg.register_desktop("legacy", legacy)
    reg.register_desktop("windows_mcp", ExplodingDesktop())

    assert reg.get_desktop_runtime() is legacy


def test_registry_fails_closed_when_legacy_is_unhealthy(monkeypatch):
    monkeypatch.delenv("BCC_DESKTOP_BACKEND", raising=False)
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop(healthy=False))

    with pytest.raises(BackendUnavailableError, match="legacy adapter is unhealthy"):
        reg.get_desktop_runtime()


def test_registry_fails_closed_when_optional_and_legacy_are_both_unhealthy(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop(healthy=False))
    reg.register_desktop("windows_mcp", DummyMcpDesktop(healthy=False))

    with pytest.raises(BackendUnavailableError, match="No healthy desktop runtime"):
        reg.get_desktop_runtime()


def test_registry_normalizes_whitespace_and_case_in_backend_flag(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "  WINDOWS_MCP  ")
    reg = AdapterRegistry()
    mcp = DummyMcpDesktop()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", mcp)

    assert reg.get_desktop_runtime() is mcp
