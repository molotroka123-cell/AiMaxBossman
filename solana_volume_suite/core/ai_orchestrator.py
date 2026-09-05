import json
import random
import httpx
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field
from core.funding_router import AntiClusteringFundingRouter


class VolumeDecision(BaseModel):
    """
    Strictly-typed Volume Decision contract.
    """
    action: Literal["BUY", "SELL", "WAIT", "FLOOR_DEFENSE", "KOTH_PULSE", "MIGRATION_HOLD"]
    wallet_index: int = Field(ge=0, description="Virtual wallet index (Zero-Knowledge)")
    amount_sol: float = Field(description="Transaction size in SOL")
    delay_sec: float = Field(ge=2.0, le=95.0, description="Poisson-distributed inter-transaction delay")
    mode_tag: str = Field(description="Operational mode tag (e.g. KOTH_PUSH, MICRO_PULSE, FLOOR_DEFENSE)")
    reason: str = Field(description="Auditable decision justification")
    confirmed_onchain: bool = Field(default=False, description="Invariant: True ONLY if confirmed on-chain signature exists")
    tx_signature: Optional[str] = Field(default=None, description="On-chain confirmed signature")


class DeterministicFallbackEngine:
    """
    Fail-Safe Deterministic Fallback.
    If the AI LLM is down, overloaded, or hallucinates bad JSON, this engine generates
    safe, high-entropy, mathematically compliant volume actions without human intervention.
    """

    @classmethod
    def generate_fallback_decision(
        cls,
        market_state: Dict[str, Any],
        active_wallet_count: int
    ) -> VolumeDecision:
        curve_progress = market_state.get("curve_progress_pct", 0.0)
        seconds_since_external = market_state.get("seconds_since_last_external_tx", 10.0)
        recent_dump_sol = market_state.get("recent_dump_size_sol", 0.0)

        wallet_count = max(1, active_wallet_count)
        wallet_idx = random.randint(0, wallet_count - 1)
        delay = AntiClusteringFundingRouter.generate_poisson_interval(lam=18.0, min_sec=4.0, max_sec=95.0)

        # Invariant 1: Migration threshold stop (Pump.fun >= 95%)
        if curve_progress >= 95.0:
            return VolumeDecision(
                action="MIGRATION_HOLD",
                wallet_index=wallet_idx,
                amount_sol=0.0,
                delay_sec=delay,
                mode_tag="MIGRATION_THRESHOLD_SAFEGUARD",
                reason=f"Curve progress is {curve_progress:.1f}% >= 95%. Purchases frozen to avoid liquidity lock."
            )

        # Invariant 2: Floor Defense against large dumps (> 2.0 SOL)
        if recent_dump_sol >= 2.0:
            defense_amt = round(min(0.85, recent_dump_sol * random.uniform(0.30, 0.45)), 4)
            return VolumeDecision(
                action="FLOOR_DEFENSE",
                wallet_index=wallet_idx,
                amount_sol=defense_amt,
                delay_sec=max(3.0, delay * 0.4),  # faster ladder response
                mode_tag="FLOOR_DEFENSE_LADDER",
                reason=f"External dump of {recent_dump_sol:.2f} SOL detected. Buying back 30-45% dip ladder."
            )

        # Invariant 3: Micro-pulse / KOTH Push if trading is stagnant (> 25s)
        if seconds_since_external > 25.0:
            pulse_sol = round(random.uniform(0.0315, 0.0682), 4)
            return VolumeDecision(
                action="KOTH_PULSE",
                wallet_index=wallet_idx,
                amount_sol=pulse_sol,
                delay_sec=round(random.uniform(4.0, 12.0), 2),
                mode_tag="KOTH_MICRO_PULSE",
                reason=f"Silence for {seconds_since_external:.1f}s > 25s. Firing green micro-pulse for Photon/BullX top rank."
            )

        # Invariant 4: Standard Pareto trading volume
        action = "BUY" if random.random() < 0.65 else "SELL"
        amount = AntiClusteringFundingRouter.generate_pareto_volume(min_sol=0.04, max_sol=0.45)

        return VolumeDecision(
            action=action,
            wallet_index=wallet_idx,
            amount_sol=amount,
            delay_sec=delay,
            mode_tag="DETERMINISTIC_PARETO_FLOW",
            reason="Deterministic algorithmic engine active (LLM fallback mode). Realistic Pareto distribution."
        )


class AIOrchestrator:
    """
    AI Market Maker Orchestrator with Zero-Knowledge sandboxing and fail-closed fallback.
    """

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8000/v1",
        model_name: str = "qwen2.5-32b-instruct",
        timeout: float = 5.0
    ):
        self.api_url = f"{api_url.rstrip('/')}/chat/completions"
        self.model_name = model_name
        self.timeout = timeout
        self.total_llm_calls = 0
        self.total_fallback_calls = 0

    def sanitize_market_context(self, raw_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Zero-Knowledge Sandbox Enforcement:
        Strips any private keys, seed phrases, or sensitive identifiers.
        Only sanitized market metrics and virtual indices are visible.
        """
        forbidden_keys = {"secret", "private_key", "secret_base58", "seed", "password", "keypair"}
        sanitized = {}
        for k, v in raw_state.items():
            if any(fk in k.lower() for fk in forbidden_keys):
                continue
            if isinstance(v, dict):
                sanitized[k] = self.sanitize_market_context(v)
            else:
                sanitized[k] = v
        return sanitized

    async def get_volume_decision(
        self,
        market_state: Dict[str, Any],
        active_wallet_count: int,
        client: Optional[httpx.AsyncClient] = None
    ) -> VolumeDecision:
        """
        Requests structured VolumeDecision from local LLM.
        Falls back to DeterministicFallbackEngine if LLM fails, times out, or produces invalid schema.
        """
        self.total_llm_calls += 1
        clean_state = self.sanitize_market_context(market_state)

        # Invariant Pre-check: If Pump.fun >= 95%, enforce immediate freeze without asking LLM
        if clean_state.get("curve_progress_pct", 0.0) >= 95.0:
            return DeterministicFallbackEngine.generate_fallback_decision(clean_state, active_wallet_count)

        system_prompt = (
            "You are an expert autonomous Solana Quantitative Market Maker. "
            "You control volume generation avoiding cluster detection, sandwich MEV, and round numbers. "
            "Strict Zero-Knowledge: operate ONLY with virtual wallet indices. "
            "Never choose round numbers (like 0.1, 0.5, 1.0). "
            "Output STRICT JSON conforming to the VolumeDecision schema."
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(clean_state)}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.35
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            resp = await client.post(self.api_url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                # Clamp wallet index to valid range
                if "wallet_index" in parsed and active_wallet_count > 0:
                    parsed["wallet_index"] = parsed["wallet_index"] % active_wallet_count
                decision = VolumeDecision.model_validate(parsed)
                return decision
        except Exception:
            pass
        finally:
            if should_close:
                await client.aclose()

        # Deterministic Fallback on any failure
        self.total_fallback_calls += 1
        return DeterministicFallbackEngine.generate_fallback_decision(clean_state, active_wallet_count)
