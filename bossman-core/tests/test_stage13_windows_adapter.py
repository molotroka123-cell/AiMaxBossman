from __future__ import annotations

import asyncio
import platform
import sys
import types

import pytest

from bossman.computer_operator.adapters.windows import WindowsDesktop


class _FakeElementInfo:
    name = "Notepad"


class _FakeWrapper:
    def __init__(self) -> None:
        self.handle = 4242
        self.element_info = _FakeElementInfo()

    def window_text(self) -> str:
        return "BOSSMAN-LIVE-FOREGROUND"

    def descendants(self, title=None):  # pragma: no cover - used via ui_tree
        return []


class _FakeDesktop:
    last_backend: str | None = None
    called_get_active = False

    def __init__(self, backend: str = "win32") -> None:
        type(self).last_backend = backend

    def window(self, handle=None):  # pywinauto >=0.6.9 path used by the fix
        return _FakeWrapper()

    def top_window(self):
        return _FakeWrapper()

    def get_active(self):  # must NOT be called anymore
        type(self).called_get_active = True
        raise AttributeError("get_active must not be used")


@pytest.fixture()
def fake_pywinauto(monkeypatch):
    mod = types.ModuleType("pywinauto")
    mod.Desktop = _FakeDesktop
    monkeypatch.setitem(sys.modules, "pywinauto", mod)
    return _FakeDesktop


@pytest.mark.skipif(
    platform.system().lower() != "windows",
    reason="SKIP_HOST: requires Windows — WindowsDesktop.foreground() calls "
           "ctypes.windll.user32.GetForegroundWindow(), which does not exist off Windows "
           "(the fake pywinauto does not mock user32). Runs and validates on a Windows host.",
)
def test_windows_foreground_uses_os_foreground_handle_not_get_active(fake_pywinauto):
    """Live reproducer regression: pywinauto 0.6.9 has no Desktop.get_active().

    The old implementation raised AttributeError on every real Windows host
    (masked in CI by mocks), so Stage13 observation never saw a real window.
    """
    d = WindowsDesktop()
    fg = asyncio.run(d.foreground())
    assert fg["title"] == "BOSSMAN-LIVE-FOREGROUND"
    assert fg["error"] is None if "error" in fg else True
    assert _FakeDesktop.called_get_active is False
    assert _FakeDesktop.last_backend == "uia"


def test_windows_adapter_source_has_no_get_active():
    import inspect

    src = inspect.getsource(WindowsDesktop)
    assert "get_active" not in src
