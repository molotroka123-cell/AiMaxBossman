import asyncio
import json
import pytest
from solana_volume_suite.orchestrator_loop import VolumeOrchestratorLoop


@pytest.mark.asyncio
async def test_offline_lifecycle_gate_kill_switch_and_vault_preservation(tmp_path, monkeypatch):
    import httpx
    def forbidden(*args, **kwargs):
        raise AssertionError("Virtual bot attempted network access")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    vault = tmp_path / "existing.json"
    vault.write_text("existing user data must survive")
    runner = VolumeOrchestratorLoop(vault_path=vault, state_path=tmp_path / "state.json", test_mode=False)
    runner.initialize_vault_pool()
    result = await runner.step()
    assert result["decision"]["confirmed_onchain"] is False
    assert result["decision"]["tx_signature"] is None
    assert result["gate"]["execution_allowed"] is False
    assert runner.cached_keypairs == []
    assert runner.liquidity_gate.validate_and_slice_order(-1, {})["simulation_allowed"] is False
    task = asyncio.create_task(runner.run())
    await asyncio.sleep(0)
    before = runner.iteration_count
    runner.stop()
    await asyncio.wait_for(task, 0.5)
    assert runner.iteration_count == before
    assert vault.read_text() == "existing user data must survive"
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["events"][0]["type"] == "RUNNER_STOP"


def test_dashboard_start_stop_duplicate_and_restart(client):
    from solana_volume_suite.dashboard import safety_app
    assert client.post("/api/orchestrator/start").status_code == 200
    first = safety_app.orchestrator_task
    assert client.post("/api/orchestrator/start").status_code == 200
    assert safety_app.orchestrator_task is first
    assert client.post("/api/vault/generate", json={"count": 2}).status_code == 409
    assert client.post("/api/trading/kill-switch").status_code == 200
    assert first.done()
    assert client.post("/api/orchestrator/start").status_code == 200
    assert safety_app.orchestrator_task is not first
    assert client.get("/api/status").json()["confirmed_transactions"] == 0
