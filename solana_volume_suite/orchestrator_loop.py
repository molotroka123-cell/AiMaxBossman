"""Offline assessment loop. No key loading, signing, AI calls or RPC transport."""
import asyncio
import json
import math
import os
import signal
import time
from pathlib import Path

from solana_volume_suite.core.liquidity_gate import LiquidityGate
from solana_volume_suite.core.security import audit, generate_password, require_virtual_mode, validate_password
from solana_volume_suite.core.treasury_guard import TreasuryGuard


class VolumeOrchestratorLoop:
    def __init__(self, vault_path=None, master_password=None, target_token_mint="MOCK_TOKEN",
                 max_allowed_loss_usd=40.0, test_mode=True, state_path=None):
        require_virtual_mode()
        self.master_password = master_password or generate_password()
        validate_password(self.master_password)
        # Compatibility argument only: never inspect, overwrite or decrypt this file.
        self.vault_path = vault_path
        self.target_token_mint = "MOCK_TOKEN"
        self.test_mode = test_mode
        if not math.isfinite(max_allowed_loss_usd) or max_allowed_loss_usd <= 0:
            raise ValueError("max_allowed_loss_usd must be positive and finite")
        self.treasury_guard = TreasuryGuard(max_allowed_loss_usd=max_allowed_loss_usd)
        self.liquidity_gate = LiquidityGate()
        self.is_running = False
        self.iteration_count = 0
        self.event_journal = []
        self.wallet_balances = {}
        self.sub_wallet_addresses = []
        self.cached_keypairs = []  # Always empty; retained for legacy callers.
        self.state_path = Path(state_path) if state_path else None
        self._stop_event = asyncio.Event()
        self._task = None

    def log_event(self, event_type, message, meta=None):
        entry = {"timestamp": time.strftime("%H:%M:%S"), "epoch": time.time(),
                 "type": event_type, "message": message, "meta": meta or {}}
        self.event_journal.insert(0, entry)
        del self.event_journal[300:]
        audit(event_type, message=message, meta=meta or {})

    def initialize_vault_pool(self, count=10):
        require_virtual_mode()
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("Virtual wallet count must be between 1 and 100")
        self.sub_wallet_addresses = [f"mock:wallet:{idx}" for idx in range(count)]
        self.wallet_balances = {addr: 0.5 for addr in self.sub_wallet_addresses}
        self.log_event("VAULT_READY", "Created fictitious wallet labels; no keys exist", {"count": count})

    async def step(self):
        require_virtual_mode()
        self.iteration_count += 1
        reserves = {"model": "CONSTANT_PRODUCT", "input_asset": "SOL",
                    "reserve_in": 650 * 10**9, "reserve_out": 10**15, "fee_bps": 25}
        gate = self.liquidity_gate.validate_and_slice_order(0.1, reserves)
        decision = {"action": "WAIT", "amount_sol": 0.1, "delay_sec": 2.0,
                    "confirmed_onchain": False, "tx_signature": None,
                    "reason": "OFFLINE_HYPOTHETICAL_ASSESSMENT"}
        if not self.treasury_guard.is_within_budget():
            self.stop()
            self.log_event("CIRCUIT_BREAKER", "Simulation budget exhausted")
        self.log_event("TRADE_HELD", "Hypothetical assessment only", {"gate": gate["status"]})
        return {"iteration": self.iteration_count, "status": "SIMULATED", "decision": decision, "gate": gate}

    async def run(self, max_iterations=None):
        require_virtual_mode()
        if self._task is not None and not self._task.done():
            raise RuntimeError("Orchestrator already running")
        if not self.wallet_balances:
            self.initialize_vault_pool()
        self._task = asyncio.current_task()
        self._stop_event.clear()
        self.is_running = True
        self.log_event("RUNNER_START", "Offline virtual loop started")
        try:
            for_iteration = 0
            while not self._stop_event.is_set():
                await self.step()
                for_iteration += 1
                if max_iterations is not None and for_iteration >= max_iterations:
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=0.05 if self.test_mode else 2.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.is_running = False
            self._task = None
            self.log_event("RUNNER_STOP", "Offline virtual loop stopped")
            self.save_state()

    def stop(self):
        self.is_running = False
        self._stop_event.set()
        self.log_event("KILL_SWITCH", "Simulation stop requested")

    def handle_signal(self, signum, frame=None):
        self.log_event("SIGNAL_RECEIVED", signal.Signals(signum).name)
        self.stop()

    def install_signal_handlers(self):
        # Standalone only. Uvicorn owns dashboard signals and invokes lifespan cleanup.
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in previous:
            signal.signal(sig, self.handle_signal)
        return previous

    def save_state(self):
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": "PAPER_TRADING_ONLY", "running": False,
            "iterations": self.iteration_count, "wallets": self.wallet_balances,
            "events": self.event_journal}, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)


async def main():
    runner = VolumeOrchestratorLoop(state_path=Path(__file__).parent / "runtime" / "state.json")
    previous = runner.install_signal_handlers()
    try:
        await runner.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    asyncio.run(main())
