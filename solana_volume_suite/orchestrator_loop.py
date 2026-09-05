import os
import sys
import asyncio
import time
import signal
from typing import Dict, Any, List, Optional
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.hash import Hash

# Ensure root of solana_volume_suite is importable
SUITE_ROOT = os.path.dirname(os.path.abspath(__file__))
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)

from core.key_vault.vault import SecurityKeyVault, DEFAULT_VAULT_PATH
from core.liquidity_gate import LiquidityGate
from core.ai_orchestrator import AIOrchestrator, VolumeDecision
from core.treasury_guard import TreasuryGuard
from core.jito_client import JitoBundleClient
from core.funding_router import AntiClusteringFundingRouter


class VolumeOrchestratorLoop:
    """
    Autonomous End-to-End Market Maker Runner.
    Wires KeyVault, LiquidityGate, AIOrchestrator, TreasuryGuard, and JitoClient
    into a fail-closed execution loop.
    """

    def __init__(
        self,
        vault_path: Optional[str] = None,
        master_password: str = "SuperSecretMasterPass123!",
        target_token_mint: str = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public SPL token mint address (identifier, not a key)
        max_allowed_loss_usd: float = 40.0,
        test_mode: bool = False
    ):
        if vault_path is None:
            self.vault_path = os.path.join(SUITE_ROOT, "wallets_encrypted.json")
        else:
            self.vault_path = vault_path
        self.master_password = master_password
        self.target_token_mint = target_token_mint
        self.max_allowed_loss_usd = max_allowed_loss_usd
        self.test_mode = test_mode

        self.vault = SecurityKeyVault(storage_path=self.vault_path)
        self.liquidity_gate = LiquidityGate(max_impact_bps=120)
        self.ai_orchestrator = AIOrchestrator()
        if self.test_mode:
            self.ai_orchestrator.timeout = 0.05
        self.treasury_guard = TreasuryGuard(max_allowed_loss_usd=self.max_allowed_loss_usd)
        self.jito_client = JitoBundleClient()
        self.funding_router = AntiClusteringFundingRouter()

        self.is_running: bool = False
        self.iteration_count: int = 0
        self.event_journal: List[Dict[str, Any]] = []
        self.wallet_balances: Dict[str, float] = {}
        self.cached_keypairs: List[Keypair] = []
        self.sub_wallet_addresses: List[str] = []

    def log_event(self, event_type: str, message: str, meta: Optional[Dict[str, Any]] = None):
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "epoch": time.time(),
            "type": event_type,
            "message": message,
            "meta": meta or {}
        }
        self.event_journal.insert(0, entry)
        if len(self.event_journal) > 300:
            self.event_journal.pop()

    def initialize_vault_pool(self, count: int = 10):
        """Ensures encrypted sub-wallet pool exists; creates 10 wallets if absent."""
        need_create = not os.path.exists(self.vault_path)
        if not need_create:
            try:
                self.cached_keypairs = self.vault.load_keypairs(self.master_password)
                self.sub_wallet_addresses = [str(kp.pubkey()) for kp in self.cached_keypairs]
            except Exception:
                try:
                    os.remove(self.vault_path)
                except OSError:
                    pass
                need_create = True

        if need_create:
            self.log_event("VAULT_INIT", f"Vault file not found or invalid. Auto-generating {count} encrypted sub-wallets...")
            self.sub_wallet_addresses = self.vault.create_and_store_pool(count, self.master_password, mode="random")
            self.cached_keypairs = self.vault.load_keypairs(self.master_password)

        # Initialize simulated SOL balances
        for idx, addr in enumerate(self.sub_wallet_addresses):
            if addr not in self.wallet_balances:
                self.wallet_balances[addr] = round(0.42 + (idx % 4) * 0.18, 3)

        self.log_event("VAULT_READY", f"Loaded {len(self.cached_keypairs)} sub-wallets under Zero-Knowledge constraints.")

    async def step(self) -> Dict[str, Any]:
        """Executes a single step of the autonomous loop."""
        self.iteration_count += 1

        # 1. Fetch live pool liquidity
        if self.test_mode:
            reserves = {
                "model": "CONSTANT_PRODUCT",
                "input_asset": "SOL",
                "reserve_in": int(650.0 * 10**9),
                "reserve_out": int(1_000_000_000 * 10**6),
                "fee_bps": 25,
                "liquidity_usd": 117_000.0
            }
        else:
            reserves = await self.liquidity_gate.fetch_dexscreener_reserves(self.target_token_mint)

        # 2. Get decision from AI Orchestrator
        market_state = {
            "stage": "PAPER_TRADING_AMM",
            "token_mint": self.target_token_mint,
            "liquidity_usd": reserves.get("liquidity_usd", 120000.0),
            "sol_reserve": reserves.get("reserve_in", 650 * 10**9) / 1e9,
            "seconds_since_last_external_tx": round(self.funding_router.generate_poisson_interval(lam=18.0), 1),
            "recent_dump_size_sol": 0.0,
            "active_wallets_count": len(self.cached_keypairs)
        }

        decision: VolumeDecision = await self.ai_orchestrator.get_volume_decision(
            market_state=market_state,
            active_wallet_count=len(self.cached_keypairs)
        )

        # 3. Liquidity Gate Validation (Price Impact <= 1.2%)
        gate_evaluation = self.liquidity_gate.validate_and_slice_order(
            amount_sol=decision.amount_sol,
            pool_reserves=reserves
        )

        # 4. Treasury Guard Check
        if not self.treasury_guard.is_within_budget():
            self.is_running = False
            self.log_event("CIRCUIT_BREAKER", f"Treasury limit reached: {self.treasury_guard.pause_reason}")
            return {
                "iteration": self.iteration_count,
                "status": "CIRCUIT_BREAKER_TRIPPED",
                "decision": decision.model_dump(),
                "gate": gate_evaluation
            }

        # 5. Execute Slices or Direct Order
        if gate_evaluation["execution_allowed"] and decision.action in ["BUY", "SELL", "KOTH_PULSE", "FLOOR_DEFENSE"]:
            slices = gate_evaluation.get("slices_sol", [decision.amount_sol])
            selected_kp = self.cached_keypairs[decision.wallet_index % len(self.cached_keypairs)]
            wallet_addr = str(selected_kp.pubkey())

            for slice_sol in slices:
                # Record trade friction
                record = self.treasury_guard.record_trade(
                    volume_sol=slice_sol,
                    dex_type="raydium",
                    jito_tip_lamports=self.jito_client.calculate_dynamic_tip("medium")
                )

                # Simulated execution signature
                sig = f"sim_jito_sig_{int(time.time()*1000)}_{self.iteration_count}"
                decision.confirmed_onchain = True
                decision.tx_signature = sig

                # Update wallet balance
                current_bal = self.wallet_balances.get(wallet_addr, 0.5)
                delta = -slice_sol if decision.action == "BUY" else (slice_sol * 0.98)
                self.wallet_balances[wallet_addr] = max(0.01, round(current_bal + delta, 4))

                self.log_event(
                    "TRADE_EXECUTED",
                    f"[{decision.action}] {slice_sol:.4f} SOL | Wallet #{decision.wallet_index} ({wallet_addr[:4]}...{wallet_addr[-4:]}) | Impact: {gate_evaluation['estimated_impact_bps']} bps",
                    meta={
                        "action": decision.action,
                        "amount_sol": slice_sol,
                        "wallet_index": decision.wallet_index,
                        "wallet_address": wallet_addr,
                        "impact_bps": gate_evaluation["estimated_impact_bps"],
                        "delay_sec": decision.delay_sec,
                        "reason": decision.reason,
                        "sig": sig
                    }
                )
        else:
            self.log_event("TRADE_HELD", f"[{decision.action}] {decision.reason} | Gate: {gate_evaluation['status']}")

        return {
            "iteration": self.iteration_count,
            "status": "COMPLETED",
            "decision": decision.model_dump(),
            "gate": gate_evaluation
        }

    async def run(self, max_iterations: Optional[int] = None):
        """Infinite (or bounded) loop."""
        self.initialize_vault_pool()
        self.is_running = True
        self.log_event("RUNNER_START", f"Volume Suite Orchestrator started for mint {self.target_token_mint}")

        try:
            while self.is_running:
                step_result = await self.step()
                if max_iterations and self.iteration_count >= max_iterations:
                    break

                if not self.is_running:
                    break

                delay = 0.05 if self.test_mode else step_result["decision"]["delay_sec"]
                # In live mode clamp delay to 6.0 for responsive UI demonstration
                delay = min(delay, 5.0)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.log_event("RUNNER_CANCEL", "Runner task cancelled.")
        finally:
            self.is_running = False
            self.log_event("RUNNER_STOP", "Volume Suite Orchestrator stopped.")

    def stop(self):
        """Kill Switch: Immediately stops loop."""
        self.is_running = False
        self.log_event("KILL_SWITCH", "Emergency STOP triggered by operator.")
