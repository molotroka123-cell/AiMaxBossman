import secrets
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(autouse=True)
def virtual_environment(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("GEMINI_REAL_MONEY_READY", "false")
    for name in ("SOLANA_RPC_URL", "SOLANA_WSS_URL", "JITO_BLOCK_ENGINE_URL"):
        monkeypatch.setenv(name, "mock://offline")


@pytest.fixture
def client(monkeypatch, tmp_path):
    import os
    from fastapi.testclient import TestClient
    from solana_volume_suite.dashboard import safety_app
    monkeypatch.setattr(safety_app, "SUITE_ROOT", tmp_path)
    # Fresh middleware quotas for each independent test session.
    safety_app.app.middleware_stack = None
    with TestClient(safety_app.app, headers={"Authorization": "Bearer " + os.environ["DASHBOARD_API_TOKEN"]}) as client:
        yield client
