import time
from typing import List, Dict, Any, Tuple
from pydantic import BaseModel, Field

MAX_PRICE_IMPACT_PCT = 1.2  # Maximum 1.2% price impact allowed per transaction


class SubImpactEngine:
    """
    Sub-Impact Engine:
    Guards against self-dumping and excessive price distortion.
    If trade impact exceeds 1.2% of pool liquidity, slices the order into micro-orders.
    """

    @staticmethod
    def calculate_price_impact(order_sol: float, pool_sol_liquidity: float) -> float:
        """
        Constant product / virtual curve price impact approximation:
        Price Impact % = (order_sol / (pool_sol_liquidity + order_sol)) * 100
        """
        if pool_sol_liquidity <= 0:
            return 100.0
        return (order_sol / (pool_sol_liquidity + order_sol)) * 100.0

    @classmethod
    def enforce_sub_impact_limits(
        cls,
        requested_sol: float,
        pool_sol_liquidity: float,
        max_impact_pct: float = MAX_PRICE_IMPACT_PCT
    ) -> List[float]:
        """
        Splits orders exceeding max_impact_pct into a sequence of smaller micro-orders.
        """
        current_impact = cls.calculate_price_impact(requested_sol, pool_sol_liquidity)
        if current_impact <= max_impact_pct:
            return [round(requested_sol, 4)]

        # Determine maximum slice size that produces <= max_impact_pct
        # impact = s / (L + s) <= I_max  =>  s <= L * I_max / (1 - I_max)
        i_ratio = (max_impact_pct / 100.0)
        max_slice_sol = max(0.01, pool_sol_liquidity * i_ratio / (1.0 - i_ratio))

        slices = []
        remaining = requested_sol
        while remaining > 0.005:
            take = min(remaining, max_slice_sol)
            slices.append(round(take, 4))
            remaining -= take

        return slices


class TreasuryMetrics(BaseModel):
    total_volume_generated_usd: float = 0.0
    total_fees_paid_usd: float = 0.0
    total_slippage_loss_usd: float = 0.0
    total_jito_tips_sol: float = 0.0
    burn_rate_ratio: float = 0.0


class TradeFrictionRecord(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    volume_sol: float
    sol_usd_price: float = 180.0
    dex_fee_usd: float = 0.0
    network_fee_usd: float = 0.0
    jito_tip_usd: float = 0.0
    slippage_usd: float = 0.0

    @property
    def total_friction_usd(self) -> float:
        return self.dex_fee_usd + self.network_fee_usd + self.jito_tip_usd + self.slippage_usd

    @property
    def volume_usd(self) -> float:
        return self.volume_sol * self.sol_usd_price


class TreasuryGuard:
    """
    Treasury Guard & Continuous Burn Rate Monitor.
    Tracks: Total_Burn = DEX_Fees + Network_Fees + Jito_Tips + Realized_Slippage.
    Enforces MAX_ALLOWED_LOSS_USD to safeguard user capital.
    """

    def __init__(
        self,
        max_allowed_loss_usd: float = 40.0,
        max_volume_target_usd: float = 10000.0,
        sol_usd_price: float = 180.0
    ):
        self.max_allowed_loss_usd = max_allowed_loss_usd
        self.max_volume_target_usd = max_volume_target_usd
        self.sol_usd_price = sol_usd_price
        self.records: List[TradeFrictionRecord] = []
        self.metrics = TreasuryMetrics()
        self.is_circuit_breaker_tripped: bool = False
        self.pause_reason: str = ""

    def record_trade(
        self,
        volume_sol: Optional[float] = None,
        dex_type: str = "pumpfun",  # "pumpfun" (1%) or "raydium" (0.25%)
        jito_tip_lamports: int = 100_000,
        slippage_bps: int = 15,
        network_fee_lamports: int = 5000,
        volume_usd: Optional[float] = None,
        fee_usd: Optional[float] = None,
        slippage_usd: Optional[float] = None,
        jito_tip_sol: Optional[float] = None
    ) -> TradeFrictionRecord:
        if volume_usd is not None or fee_usd is not None:
            # Stage 2 direct USD format
            vol_usd = volume_usd or 0.0
            f_usd = fee_usd or 0.0
            slip_usd = slippage_usd or 0.0
            tip_sol = jito_tip_sol or 0.0
            tip_usd = tip_sol * self.sol_usd_price
            vol_sol = vol_usd / self.sol_usd_price if self.sol_usd_price > 0 else 0.0

            record = TradeFrictionRecord(
                volume_sol=vol_sol,
                sol_usd_price=self.sol_usd_price,
                dex_fee_usd=f_usd,
                network_fee_usd=0.0,
                jito_tip_usd=tip_usd,
                slippage_usd=slip_usd
            )
            self.records.append(record)

            self.metrics.total_volume_generated_usd += vol_usd
            self.metrics.total_fees_paid_usd += f_usd
            self.metrics.total_slippage_loss_usd += slip_usd
            self.metrics.total_jito_tips_sol += tip_sol
            total_loss = self.metrics.total_fees_paid_usd + self.metrics.total_slippage_loss_usd
            self.metrics.burn_rate_ratio = (
                total_loss / self.metrics.total_volume_generated_usd
                if self.metrics.total_volume_generated_usd > 0 else 0.0
            )

            if not self.is_within_budget():
                self.is_circuit_breaker_tripped = True
                self.pause_reason = (
                    f"MAX_ALLOWED_LOSS_USD exceeded: Total loss ${total_loss:.2f} >= "
                    f"Limit ${self.max_allowed_loss_usd:.2f}. AUTO-PAUSE triggered."
                )

            return record

        # Default SOL volume format
        vol_sol = volume_sol if volume_sol is not None else 0.0
        dex_rate = 0.01 if dex_type.lower() == "pumpfun" else 0.0025
        trade_usd = vol_sol * self.sol_usd_price

        dex_fee_usd = trade_usd * dex_rate
        net_fee_usd = (network_fee_lamports / 1e9) * self.sol_usd_price
        tip_sol = jito_tip_lamports / 1e9
        tip_usd = tip_sol * self.sol_usd_price
        slip_usd = trade_usd * (slippage_bps / 10_000.0)

        record = TradeFrictionRecord(
            volume_sol=vol_sol,
            sol_usd_price=self.sol_usd_price,
            dex_fee_usd=dex_fee_usd,
            network_fee_usd=net_fee_usd,
            jito_tip_usd=tip_usd,
            slippage_usd=slip_usd
        )
        self.records.append(record)

        self.metrics.total_volume_generated_usd += trade_usd
        self.metrics.total_fees_paid_usd += dex_fee_usd
        self.metrics.total_slippage_loss_usd += slip_usd
        self.metrics.total_jito_tips_sol += tip_sol

        total_burn = self.get_total_burn_usd()
        if total_burn >= self.max_allowed_loss_usd:
            self.is_circuit_breaker_tripped = True
            self.pause_reason = (
                f"MAX_ALLOWED_LOSS_USD exceeded: Total burn ${total_burn:.2f} >= "
                f"Limit ${self.max_allowed_loss_usd:.2f}. AUTO-PAUSE triggered."
            )

        return record

    def is_within_budget(self) -> bool:
        total_loss = self.metrics.total_fees_paid_usd + self.metrics.total_slippage_loss_usd
        return total_loss <= self.max_allowed_loss_usd

    def get_status(self) -> Dict:
        return self.metrics.model_dump()

    def get_total_burn_usd(self) -> float:
        return sum(r.total_friction_usd for r in self.records)

    def get_total_volume_usd(self) -> float:
        return sum(r.volume_usd for r in self.records)

    def get_recent_metrics(self, window_seconds: float = 300.0) -> Dict[str, Any]:
        """
        Returns real-time 5m or 1h volume, burn metrics, and efficiency ratio ($ Volume per $1 fee).
        """
        now = time.time()
        recent = [r for r in self.records if (now - r.timestamp) <= window_seconds]
        recent_vol = sum(r.volume_usd for r in recent)
        recent_burn = sum(r.total_friction_usd for r in recent)

        total_vol = self.get_total_volume_usd()
        total_burn = self.get_total_burn_usd()
        efficiency_ratio = (total_vol / total_burn) if total_burn > 0 else 0.0

        return {
            "window_seconds": window_seconds,
            "recent_volume_usd": round(recent_vol, 2),
            "recent_burn_usd": round(recent_burn, 2),
            "total_volume_usd": round(total_vol, 2),
            "total_burn_usd": round(total_burn, 2),
            "efficiency_ratio": round(efficiency_ratio, 2),
            "circuit_breaker_tripped": self.is_circuit_breaker_tripped,
            "pause_reason": self.pause_reason
        }

    def reset_circuit_breaker(self, new_limit_usd: float = 50.0):
        self.max_allowed_loss_usd = new_limit_usd
        self.is_circuit_breaker_tripped = False
        self.pause_reason = ""
