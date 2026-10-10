import json
import httpx
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field


class VolumeDecision(BaseModel):
    """
    Strictly-typed Volume Decision contract.
    """
    action: Literal["BUY", "SELL", "WAIT", "FLOOR_DEFENSE", "KOTH_PULSE", "MIGRATION_HOLD"]
    wallet_index: int = Field(ge=0, description="Virtual wallet index (Zero-Knowledge)")
    amount_sol: float = Field(description="Hypothetical paper-trade size in SOL")
    delay_sec: float = Field(ge=2.0, le=95.0, description="Delay before the next local simulation step")
    mode_tag: str = Field(description="Auditable paper-mode policy tag")
    reason: str = Field(description="Auditable decision justification")
    confirmed_onchain: bool = Field(default=False, description="Invariant: True ONLY if confirmed on-chain signature exists")
    tx_signature: Optional[str] = Field(default=None, description="On-chain confirmed signature")


class DeterministicFallbackEngine:
    """
    Deterministic fallback for paper-only risk evaluation.
    It refuses to generate artificial volume or price-support actions.
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

        wallet_idx = 0

        # Invariant 1: Migration threshold stop (Pump.fun >= 95%)
        if curve_progress >= 95.0:
            return VolumeDecision(
                action="MIGRATION_HOLD",
                wallet_index=wallet_idx,
                amount_sol=0.0,
                delay_sec=5.0,
                mode_tag="MIGRATION_THRESHOLD_SAFEGUARD",
                reason=f"Curve progress is {curve_progress:.1f}% >= 95%. Purchases frozen to avoid liquidity lock."
            )

        # A paper simulator must not respond to dumps with automated price support.
        if recent_dump_sol >= 2.0:
            return VolumeDecision(
                action="WAIT",
                wallet_index=wallet_idx,
                amount_sol=0.0,
                delay_sec=5.0,
                mode_tag="POLICY_HOLD_PRICE_MOVEMENT",
                reason=f"External dump of {recent_dump_sol:.2f} SOL detected; automated price support is disabled."
            )

        # Do not fabricate trades to increase volume or rankings during inactivity.
        if seconds_since_external > 25.0:
            return VolumeDecision(
                action="WAIT",
                wallet_index=wallet_idx,
                amount_sol=0.0,
                delay_sec=5.0,
                mode_tag="POLICY_HOLD_NO_ACTIVITY",
                reason=f"No external activity for {seconds_since_external:.1f}s; artificial volume is disabled."
            )

        # A model failure must not invent orders. Fail closed to no action.
        return VolumeDecision(
            action="WAIT",
            wallet_index=wallet_idx,
            amount_sol=0.0,
            delay_sec=5.0,
            mode_tag="POLICY_HOLD_MODEL_UNAVAILABLE",
            reason="Local model unavailable or invalid; no automatic paper order was generated."
        )


class AIOrchestrator:
    """
    Paper-only decision helper with fail-closed fallback.
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
        Requests a hypothetical decision from a local LLM.
        On errors, returns WAIT instead of inventing an order.
        """
        self.total_llm_calls += 1
        clean_state = self.sanitize_market_context(market_state)

        # Invariant Pre-check: If Pump.fun >= 95%, enforce immediate freeze without asking LLM
        if clean_state.get("curve_progress_pct", 0.0) >= 95.0:
            return DeterministicFallbackEngine.generate_fallback_decision(clean_state, active_wallet_count)

        system_prompt = (
            "You are a paper-trading risk evaluator. Never generate artificial volume, "
            "support or defend a token price, evade wallet-cluster detection, or optimize rankings. "
            "Recommend only hypothetical BUY, SELL, or WAIT decisions for local analysis. "
            "For inactivity or a large sell-off, choose WAIT. Never claim that a trade executed. "
            "Use only virtual wallet indices. "
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
