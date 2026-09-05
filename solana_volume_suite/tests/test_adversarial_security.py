"""Local adversarial regressions. No real credentials, providers or transactions."""
import asyncio
import os
from contextlib import suppress

import pytest
from solana_volume_suite.core.treasury_guard import TreasuryGuard


def test_budget_cannot_ignore_jito_and_network_costs():
    guard = TreasuryGuard(max_allowed_loss_usd=1)
    guard.record_trade(volume_sol=0, jito_tip_lamports=10_000_000)
    assert guard.get_total_burn_usd() > 1
    assert guard.is_within_budget() is False


def test_exact_budget_limit_is_closed():
    guard = TreasuryGuard(max_allowed_loss_usd=1)
    guard.record_trade(volume_usd=1, fee_usd=1)
    assert guard.is_within_budget() is False


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_negative_or_nonfinite_costs_cannot_credit_treasury(value):
    guard = TreasuryGuard()
    before = guard.get_status()
    with pytest.raises(ValueError):
        guard.record_trade(volume_usd=10, fee_usd=value)
    assert guard.get_status() == before
    assert guard.records == []


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_budget_configuration_is_rejected(value):
    with pytest.raises(ValueError):
        TreasuryGuard(max_allowed_loss_usd=value)


def test_failed_reset_cannot_clear_trip():
    guard = TreasuryGuard(max_allowed_loss_usd=1)
    guard.record_trade(volume_usd=1, fee_usd=2)
    with pytest.raises(ValueError):
        guard.reset_circuit_breaker(new_limit_usd=1)
    assert guard.is_circuit_breaker_tripped
    assert not guard.is_within_budget()


def test_duplicate_authentication_headers_rejected(client):
    token = os.environ["DASHBOARD_API_TOKEN"]
    response = client.get("/api/status", headers=[("Authorization", "Bearer wrong"),
                                                   ("Authorization", "Bearer " + token)])
    assert response.status_code == 401


def test_foreign_origin_cannot_use_local_control_plane(client):
    assert client.post("/api/orchestrator/start", headers={"Origin": "https://attacker.invalid"}).status_code == 403


def test_dns_rebinding_host_rejected(client):
    assert client.get("/", headers={"Host": "attacker.invalid"}).status_code == 400


def test_deep_json_is_rejected_before_route_parsing(client):
    response = client.post("/api/vault/generate", content="[" * 1200 + "0" + "]" * 1200,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_start_cannot_report_running_while_stop_is_in_progress(monkeypatch, tmp_path):
    from solana_volume_suite.dashboard import safety_app as api
    monkeypatch.setattr(api, "SUITE_ROOT", tmp_path)
    cancelling, release = asyncio.Event(), asyncio.Event()
    async with api.lifespan(api.app):
        async def delayed_cleanup():
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()
        old_task = asyncio.create_task(delayed_cleanup())
        api.orchestrator_task = old_task
        await asyncio.sleep(0)
        stopping = asyncio.create_task(api.stop_runner())
        await cancelling.wait()
        starting = asyncio.create_task(api.start_runner())
        await asyncio.sleep(0)
        try:
            assert not starting.done(), "Start falsely returned RUNNING during cancellation"
        finally:
            release.set()
            await stopping
            await starting
            # Clean up even when the vulnerable implementation loses the task handle.
            runner_task = api.orchestrator._task
            await api.stop_runner()
            if runner_task and not runner_task.done():
                runner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runner_task


@pytest.mark.asyncio
async def test_kill_switch_does_not_wait_for_request_body():
    from solana_volume_suite.dashboard.safety_app import APIProtection
    messages = []
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
    async def never_arrives():
        await asyncio.Event().wait()
    scope = {"type": "http", "path": "/api/trading/kill-switch", "method": "POST",
             "scheme": "http", "headers": [(b"host", b"127.0.0.1"),
             (b"authorization", ("Bearer " + os.environ["DASHBOARD_API_TOKEN"]).encode())]}
    async def send(message):
        messages.append(message)
    await asyncio.wait_for(APIProtection(downstream)(scope, never_arrives, send), 0.15)
    assert messages[0]["status"] == 200


def test_jito_cannot_sign_even_when_called_directly():
    from solana_volume_suite.core.jito_client import JitoBundleClient
    from solders.keypair import Keypair
    from solders.hash import Hash
    mock_key = Keypair.from_seed(bytes(range(32)))
    with pytest.raises(PermissionError):
        JitoBundleClient().compile_v0_transaction(mock_key.pubkey(), [], Hash.default(), [mock_key])


@pytest.mark.asyncio
async def test_start_received_before_stop_cannot_restart_afterwards(monkeypatch, tmp_path):
    from solana_volume_suite.dashboard import safety_app as api
    monkeypatch.setattr(api, "SUITE_ROOT", tmp_path)
    async with api.lifespan(api.app):
        old_epoch = api.control_epoch
        await api.stop_runner()
        response = await api.start_runner(old_epoch)
        assert response.status_code == 409
        assert api.orchestrator_task is None


@pytest.mark.asyncio
async def test_authenticated_slow_body_has_a_total_deadline(monkeypatch):
    from solana_volume_suite.dashboard import safety_app as api
    monkeypatch.setattr(api, "BODY_TIMEOUT_SECONDS", 0.01)
    messages = []
    async def forbidden(*args):
        pytest.fail("Incomplete request reached the handler")
    async def stalled():
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "path": "/api/orchestrator/start", "method": "POST", "scheme": "http",
             "headers": [(b"host", b"127.0.0.1"), (b"authorization",
                         ("Bearer " + os.environ["DASHBOARD_API_TOKEN"]).encode())]}
    await asyncio.wait_for(api.APIProtection(forbidden)(scope, stalled, send), 0.5)
    assert messages[0]["status"] == 408
