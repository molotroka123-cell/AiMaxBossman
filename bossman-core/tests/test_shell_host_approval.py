"""Security Hardening V1.1 (H3): host/local shell = ALWAYS ASK; docker = AUTO.

mandatory_confirm вычисляется в момент вызова и НЕ переотменяется грантом агента.
"""
import importlib

from bossman.config import settings
from bossman.toolkit import REGISTRY


def _reg(monkeypatch, mode):
    monkeypatch.setattr(settings, "sandbox_mode", mode, raising=False)


def test_docker_mode_is_auto_no_mandatory_confirm(monkeypatch):
    _reg(monkeypatch, "docker")
    for name in ("run", "tests"):
        tool = REGISTRY[name]
        assert tool.mandatory_confirm is not None
        assert tool.mandatory_confirm() is False, "изолированный docker → AUTO"


def test_local_mode_forces_confirm(monkeypatch):
    _reg(monkeypatch, "local")
    for name in ("run", "tests"):
        assert REGISTRY[name].mandatory_confirm() is True, "host/local exec → ALWAYS ASK"


def test_unknown_mode_fails_closed_to_confirm(monkeypatch):
    _reg(monkeypatch, "weird")
    assert REGISTRY["run"].mandatory_confirm() is True
