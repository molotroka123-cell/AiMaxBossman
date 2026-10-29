"""Smoke-тесты канонизации безопасных Windows-путей (bcc.features.pc_agent_policy_guards)."""
from __future__ import annotations

import pytest

from bcc.features.pc_agent_policy_guards import canonicalize_windows_path_safe


@pytest.mark.parametrize("raw", ["", None])
def test_canonicalize_empty_path_returns_empty_string(raw):
    """Пустая строка и None дают пустую строку без исключений."""
    assert canonicalize_windows_path_safe(raw) == ""


@pytest.mark.parametrize("raw", ["", None])
def test_canonicalize_empty_path_reject_dangerous_returns_empty_string(raw):
    """Флаг reject_dangerous не меняет пустой результат (экран не вызывается)."""
    assert canonicalize_windows_path_safe(raw, reject_dangerous=True) == ""
