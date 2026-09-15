"""A status/recovery query must never signal a Windows shared console."""
from types import SimpleNamespace

import pytest

from bcc.features import apps_control as ctl


def test_pid_probe_uses_process_query_without_a_console_signal(monkeypatch):
    queries = []

    def forbidden_signal(*args):
        raise AssertionError("status probe sent a console signal")

    monkeypatch.setattr(ctl, "os", SimpleNamespace(name="nt", kill=forbidden_signal))
    monkeypatch.setattr(ctl.psutil, "pid_exists", lambda pid: queries.append(pid) or pid == 321)
    assert ctl._pid_alive(321) is True
    assert ctl._pid_alive(322) is False
    assert queries == [321, 322]


@pytest.mark.parametrize("pid", [0, -1, True, None, "123", 1.5])
def test_invalid_pid_cannot_be_queried_or_adopted(pid, monkeypatch):
    def invalid_query(*args):
        raise AssertionError("invalid PID reached the process API")
    monkeypatch.setattr(ctl.psutil, "pid_exists", invalid_query)
    assert ctl._pid_alive(pid) is False


def test_unavailable_process_query_does_not_grant_ownership(monkeypatch):
    def denied(pid):
        raise ctl.psutil.AccessDenied(pid)
    monkeypatch.setattr(ctl.psutil, "pid_exists", denied)
    assert ctl._pid_alive(321) is False
