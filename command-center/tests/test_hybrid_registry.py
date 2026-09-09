
import pytest
import os
from bcc.hybrid.registry import AdapterRegistry, CapabilityName, BackendType
from bcc.hybrid.capabilities import DesktopRuntime, RuntimeIdentity, EffectCorrelation, Observation, BackendUnavailableError

class DummyLegacyDesktop(DesktopRuntime):
    def get_runtime_identity(self):
        return RuntimeIdentity(name="legacy", version="1.0", backend_type="legacy")
    def launch_app(self, e, a, c): return {"status": "ok", "backend": "legacy"}
    def click_element(self, t, c): return Observation("c1", {}, "now", self.get_runtime_identity(), True)
    def get_window_state(self, t, c): return Observation("c1", {}, "now", self.get_runtime_identity(), True)
    def close_app(self, p, c): return True

class DummyMcpDesktop(DesktopRuntime):
    def get_runtime_identity(self):
        return RuntimeIdentity(name="windows_mcp", version="0.1", backend_type="windows_mcp")
    def launch_app(self, e, a, c): return {"status": "ok", "backend": "windows_mcp"}
    def click_element(self, t, c): return Observation("c1", {}, "now", self.get_runtime_identity(), False)
    def get_window_state(self, t, c): return Observation("c1", {}, "now", self.get_runtime_identity(), False)
    def close_app(self, p, c): return True

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
