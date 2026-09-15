
import os
import pytest
import sys
import time
from bcc.hybrid.sidecar import SidecarProcessManager, SidecarConfig, SidecarStatus


def test_sidecar_lifecycle_normal():
    # Run a simple python echo server/subprocess as sidecar test
    config = SidecarConfig(
        name="test_sidecar",
        command=[sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(2)"],
        startup_timeout_s=5.0,
        heartbeat_interval_s=1.0,
    )
    mgr = SidecarProcessManager(config)
    started = mgr.start()
    assert started is True
    assert mgr.status == SidecarStatus.HEALTHY
    assert mgr.pid is not None

    mgr.stop()
    assert mgr.status == SidecarStatus.STOPPED


def test_sidecar_missing_binary():
    config = SidecarConfig(
        name="missing_binary",
        command=["/non/existent/binary/path/12345"],
        startup_timeout_s=1.0,
    )
    mgr = SidecarProcessManager(config)
    started = mgr.start()
    assert started is False
    assert mgr.status == SidecarStatus.CRASHED


def test_sidecar_environment_does_not_inherit_parent_secrets_by_default(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-cross-sidecar-boundary")
    monkeypatch.setenv("BOSSMAN_TEST_SECRET", "also-must-not-cross")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") or "safe-test-path")

    config = SidecarConfig(
        name="env_isolation",
        command=[sys.executable, "-c", "pass"],
        env={"SIDECAR_LOCAL_MODE": "1"},
    )
    child_env = SidecarProcessManager(config)._build_process_env()

    assert "OPENROUTER_API_KEY" not in child_env
    assert "BOSSMAN_TEST_SECRET" not in child_env
    assert child_env["SIDECAR_LOCAL_MODE"] == "1"
    assert "PATH" in {key.upper() for key in child_env}


def test_sidecar_environment_explicit_values_are_allowed(monkeypatch):
    monkeypatch.setenv("BOSSMAN_TEST_SECRET", "ambient-secret")
    config = SidecarConfig(
        name="explicit_env",
        command=[sys.executable, "-c", "pass"],
        env={"LOCAL_MODEL_ENDPOINT": "http://127.0.0.1:1234"},
    )

    child_env = SidecarProcessManager(config)._build_process_env()

    assert child_env["LOCAL_MODEL_ENDPOINT"] == "http://127.0.0.1:1234"
    assert "BOSSMAN_TEST_SECRET" not in child_env


def test_sidecar_parent_environment_inheritance_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("BOSSMAN_TEST_SECRET", "explicitly-inherited-secret")
    config = SidecarConfig(
        name="trusted_sidecar",
        command=[sys.executable, "-c", "pass"],
        inherit_parent_env=True,
    )

    child_env = SidecarProcessManager(config)._build_process_env()

    assert child_env["BOSSMAN_TEST_SECRET"] == "explicitly-inherited-secret"
