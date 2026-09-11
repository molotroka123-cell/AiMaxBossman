"""Windows profile-lock recovery observes processes without console signals."""
import ctypes
from types import SimpleNamespace

import pytest

from bossman.toolkit import browser


@pytest.mark.parametrize("wait_result,alive", [(258, True), (0, False), (0xffffffff, True)])
def test_windows_probe_uses_a_typed_handle_without_signalling(wait_result, alive, monkeypatch):
    handles = []
    queries = []
    # A handle beyond 32 bits exercises the contract requiring HANDLE restype.
    handle = 0x1_12345678

    def opened(access, inherit, pid):
        queries.append((access, inherit, pid))
        return handle

    def waited(received, timeout):
        assert received == handle and timeout == 0
        return wait_result

    def closed(received):
        handles.append(received)
        return True

    def forbidden(*args):
        raise AssertionError("profile lock lookup sent a console signal")

    kernel = SimpleNamespace(OpenProcess=opened, WaitForSingleObject=waited, CloseHandle=closed)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(browser, "os", SimpleNamespace(name="nt", kill=forbidden))
    assert browser._pid_alive(321) is alive
    assert queries == [(0x00100000, False, 321)]
    assert handles == [handle]
    assert opened.restype is ctypes.wintypes.HANDLE


@pytest.mark.parametrize("error,alive", [(87, False), (5, True), (6, True)])
def test_unknown_windows_pid_is_reclaimable_only_when_known_absent(error, alive, monkeypatch):
    def opened(*args):
        return None
    def unused(*args):
        raise AssertionError("invalid handle was used")
    kernel = SimpleNamespace(OpenProcess=opened, WaitForSingleObject=unused, CloseHandle=unused)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(browser, "os", SimpleNamespace(name="nt"))
    assert browser._pid_alive(321) is alive


def test_access_denied_does_not_break_a_live_posix_profile_lock(monkeypatch):
    def denied(*args):
        raise PermissionError("foreign owner")
    monkeypatch.setattr(browser, "os", SimpleNamespace(name="posix", kill=denied))
    assert browser._pid_alive(321) is True
