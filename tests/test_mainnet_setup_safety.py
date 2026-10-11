"""The retired Mainnet setup wizard must stay a fail-closed stub.

Live Mainnet was removed by the owner (commit b9adda71).  The module is kept only so
old callers get a clear refusal: no network, no filesystem writes, no password prompt.
"""
import getpass
import importlib.util
import io
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "solana_volume_suite/setup_mainnet.py"


def _load(name="setup_mainnet_under_test"):
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    """Load the module, then make every side-channel record or explode on use."""
    module = _load()
    calls = []

    def trip(label):
        def _inner(*a, **k):
            calls.append(label)
            raise AssertionError(f"forbidden side effect: {label}")
        return _inner

    monkeypatch.setattr(socket, "socket", trip("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", trip("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", trip("socket.getaddrinfo"))
    monkeypatch.setattr(urllib.request, "urlopen", trip("urllib.request.urlopen"))
    monkeypatch.setattr(getpass, "getpass", trip("getpass.getpass"))
    monkeypatch.setattr("builtins.input", trip("input"))
    try:
        import requests
        monkeypatch.setattr(requests.sessions.Session, "request", trip("requests"))
    except ImportError:
        pass
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    vault = tmp_path / "existing.vault"
    env.write_bytes(b"preexisting configuration")
    vault.write_bytes(b"harmless preexisting ciphertext fixture")
    return module, calls, tmp_path, env, vault


def _snapshot(root: Path):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("args,kwargs", [
    ((), {}),
    ((), {"password": None, "non_interactive": True}),
    ((), {"password": "", "non_interactive": True}),
    ((), {"password": "short", "non_interactive": True}),
    ((), {"password": "x" * 32, "non_interactive": True}),
    (("x" * 32,), {"rpc_url": "https://invalid.example", "vault_path": "existing.vault"}),
])
def test_wizard_refuses_any_call_without_side_effects(guarded, args, kwargs):
    module, calls, root, env, vault = guarded
    before = _snapshot(root)
    with pytest.raises(RuntimeError) as err:
        module.run_setup_wizard(*args, **kwargs)
    assert str(err.value) == module.DISABLED_REASON
    assert calls == []
    assert _snapshot(root) == before
    assert env.read_bytes() == b"preexisting configuration"
    assert vault.read_bytes() == b"harmless preexisting ciphertext fixture"


def test_disabled_reason_states_the_refusal():
    module = _load("setup_mainnet_reason")
    text = module.DISABLED_REASON.lower()
    assert "disabled" in text and ".env" in text and "live trading" in text


def test_main_returns_2_and_prints_reason_to_stderr(guarded, capsys):
    module, calls, root, env, vault = guarded
    before = _snapshot(root)
    assert module.main([]) == 2
    out = capsys.readouterr()
    assert module.DISABLED_REASON in out.err
    assert out.out == ""
    assert calls == []
    assert _snapshot(root) == before


def test_module_import_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def boom(*a, **k):
        raise AssertionError("network on import")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    _load("setup_mainnet_import_only")
    assert list(tmp_path.iterdir()) == []
