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


# --------------------------------------------------------------------------
# Раздел 22, режим отказа «неверная версия» (BL-028)
# --------------------------------------------------------------------------
# `BackendVersionMismatchError` был ОБЪЯВЛЕН и ЭКСПОРТИРОВАН, но не возбуждался
# нигде. То есть точные SHA из `docs/hybrid/sources.lock.json` оставались
# утверждением в документе: рантайм ни разу не проверял, что запущено именно
# то, что разбиралось по лицензии и поведению.
#
# Решение при несовпадении — НЕ использовать бэкенд: несовпадение версии это
# утверждение о происхождении, а не временная неисправность. Продукт при этом
# не ломается, потому что откат на проверенную реализацию тот же, что и для
# больного бэкенда.

from bcc.hybrid.capabilities import BackendVersionMismatchError


class VersionedDesktop(DummyMcpDesktop):
    def __init__(self, version, *, healthy=True):
        super().__init__(healthy=healthy)
        self._version = version

    def get_runtime_identity(self):
        return RuntimeIdentity(name="windows_mcp", version=self._version,
                               backend_type="windows_mcp", is_healthy=self.healthy)


def test_a_backend_matching_its_pin_is_used(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", VersionedDesktop("08ddee78c261"),
                         pinned_version="08ddee78c261")
    assert reg.get_desktop_runtime().launch_app("x", [], None)["backend"] == "windows_mcp"


def test_a_backend_that_drifted_off_its_pin_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", VersionedDesktop("deadbeef0000"),
                         pinned_version="08ddee78c261")
    assert reg.get_desktop_runtime().launch_app("x", [], None)["backend"] == "legacy"


def test_a_pinned_backend_that_cannot_name_its_version_is_not_trusted(monkeypatch):
    """«Версия неизвестна» — это не «версия та самая»."""
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", VersionedDesktop(""),
                         pinned_version="08ddee78c261")
    assert reg.get_desktop_runtime().launch_app("x", [], None)["backend"] == "legacy"


def test_a_version_mismatch_with_no_fallback_is_raised_not_swallowed(monkeypatch):
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("windows_mcp", VersionedDesktop("deadbeef0000"),
                         pinned_version="08ddee78c261")
    with pytest.raises(BackendVersionMismatchError) as err:
        reg.get_desktop_runtime()
    assert "pinned version" in str(err.value)


def test_a_drifted_fallback_is_refused_too(monkeypatch):
    """Запасной бэкенд не освобождён от проверки просто потому, что запасной."""
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "legacy")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", VersionedDesktop("не та"), pinned_version="1.0")
    with pytest.raises(BackendVersionMismatchError):
        reg.get_desktop_runtime()


def test_an_unpinned_backend_is_still_allowed(monkeypatch):
    """Обратный контроль: пин необязателен.

    Без этого теста «починка» вида «требовать пин всегда» прошла бы и сделала
    бы нерабочим любой незакреплённый адаптер, включая legacy.
    """
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("legacy", DummyLegacyDesktop())
    reg.register_desktop("windows_mcp", VersionedDesktop("что угодно"))
    assert reg.get_desktop_runtime().launch_app("x", [], None)["backend"] == "windows_mcp"


def test_an_unhealthy_backend_is_reported_as_unhealthy_not_as_a_version_problem(monkeypatch):
    """Порядок проверок: больной процесс не обязан уметь называть версию."""
    monkeypatch.setenv("BCC_DESKTOP_BACKEND", "windows_mcp")
    reg = AdapterRegistry()
    reg.register_desktop("windows_mcp", VersionedDesktop("deadbeef", healthy=False),
                         pinned_version="08ddee78c261")
    with pytest.raises(BackendUnavailableError):
        reg.get_desktop_runtime()


def test_an_empty_pin_is_refused_at_registration():
    reg = AdapterRegistry()
    with pytest.raises(ValueError):
        reg.pin_version("desktop", "windows_mcp", "   ")
