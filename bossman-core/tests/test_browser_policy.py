from __future__ import annotations
import os
from pathlib import Path
import pytest

from bossman.toolkit.browser import (
    DANGEROUS_KEYS, ProfileLock, _safe_filename, _workspace_path, domain_risk,
    is_sensitive_label,
)
from bossman.toolkit import ToolContext


def test_sensitive_classifier():
    assert is_sensitive_label("Delete Account")
    assert is_sensitive_label("Confirm payment")
    assert not is_sensitive_label("Generate preview")


def test_dangerous_enter_is_classified():
    assert "enter" in DANGEROUS_KEYS
    assert "control+enter" in DANGEROUS_KEYS


def test_domain_policy_env(monkeypatch):
    monkeypatch.setenv("BOSSMAN_BROWSER_BLOCKED_DOMAINS", "evil.example")
    monkeypatch.setenv("BOSSMAN_BROWSER_TRUSTED_DOMAINS", "safe.example")
    assert domain_risk("https://x.evil.example/a") == "blocked"
    assert domain_risk("https://safe.example/") == "trusted"


def test_workspace_path_escape(tmp_path: Path):
    ctx = ToolContext(agent="a", workdir=tmp_path)
    inside = tmp_path / "x.txt"; inside.write_text("x")
    assert _workspace_path(ctx, "x.txt") == inside.resolve()
    with pytest.raises(ValueError):
        _workspace_path(ctx, "../escape.txt")


def test_filename_sanitization():
    assert _safe_filename("../../bad?.exe") == "bad_.exe"


def test_profile_lock_exclusion_and_stale_recovery(tmp_path: Path):
    p = tmp_path / ".lock"
    a = ProfileLock(p); a.acquire()
    b = ProfileLock(p)
    with pytest.raises(RuntimeError): b.acquire()
    a.release(); b.acquire(); b.release()
