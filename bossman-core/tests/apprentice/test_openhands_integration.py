from __future__ import annotations

import sys
from pathlib import Path

from bossman.apprentice import flags
from bossman.apprentice.openhands_teacher_client import OpenHandsTeacherClient
from bossman.apprentice.teacher import FallbackReason
from bossman.apprentice.teacher_sandbox import hermetic_workspace, scrubbed_env
from bossman.apprentice.teacher_wiring_patch import OpenHandsFallback, build_openhands_fallback, openrouter_provider_env


def test_openhands_feature_flag_is_independent_and_off_by_default(monkeypatch):
    fallback = OpenHandsFallback.__new__(OpenHandsFallback)
    task = type("Task", (), {"owner_requested_fallback": False})()
    monkeypatch.delenv(flags.OPENHANDS_CODE_FALLBACK, raising=False)
    assert "off" in fallback.allowed(FallbackReason.ATTEMPTS_EXHAUSTED, task)
    monkeypatch.setenv(flags.OPENHANDS_CODE_FALLBACK, "1")
    assert fallback.allowed(FallbackReason.ATTEMPTS_EXHAUSTED, task) == ""


def test_openrouter_provider_env_filters_unrelated_secrets():
    out = openrouter_provider_env({
        "OPENROUTER_API_KEY":"k", "OPENROUTER_BASE_URL":"https://example.invalid",
        "AWS_SECRET_ACCESS_KEY":"must-not-cross", "OPENAI_API_KEY":"must-not-cross"})
    assert out == {"OPENROUTER_API_KEY":"k", "OPENROUTER_BASE_URL":"https://example.invalid"}


def test_builder_requires_openrouter_and_constructs_teacher_adapter():
    fallback = build_openhands_fallback(
        workspace=object(), verifier=object(), teacher=object(), command=[sys.executable, "-c", "pass"],
        model="openrouter/anthropic/test", provider_env={"OPENROUTER_API_KEY":"k", "AWS_SECRET_ACCESS_KEY":"no"})
    assert isinstance(fallback.client, OpenHandsTeacherClient)
    assert fallback.client.client.env == {"OPENROUTER_API_KEY":"k"}


def test_existing_hermetic_teacher_sandbox_is_preserved(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-cross")
    bundle = {"files":{"src/a.py":"VALUE = 1\n"},"constraints":["no push"],"failing_test":"assert VALUE == 2"}
    with hermetic_workspace(bundle) as hw:
        path = hw.path
        assert path.exists() and not (path / ".git").exists() and not (path / ".env").exists()
        assert "OPENROUTER_API_KEY" not in hw.env
        assert (path / "src/a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not path.exists()


def test_scrubbed_env_removes_bossman_and_provider_credentials():
    env = scrubbed_env({"PATH":"/bin", "BOSSMAN_SECRET":"x", "OPENROUTER_API_KEY":"y", "AWS_SECRET_ACCESS_KEY":"z"})
    assert env == {"PATH":"/bin"}
