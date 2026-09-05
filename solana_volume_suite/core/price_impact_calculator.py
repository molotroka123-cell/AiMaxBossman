from typing import Tuple


class PriceImpactCalculator:
    """
    Mathematical price impact calculator for CPMM / AMM pools.
    Formula: (x + x_virtual) * (y + y_virtual) = k
    Protects against self-dumping and excessive slippage.
    """

    @staticmethod
    def calculate_price_impact_bps(
        amount_in: int,
        reserve_in: int,
        reserve_out: int,
        fee_bps: int = 25
    ) -> int:
        amount_in_with_fee = amount_in * (10000 - fee_bps)
        numerator = amount_in_with_fee * reserve_out
        denominator = (reserve_in * 10000) + amount_in_with_fee
        if denominator == 0:
            return 10000
        amount_out = numerator // denominator
        spot_price = reserve_out / reserve_in if reserve_in > 0 else 0
        execution_price = amount_out / amount_in if amount_in > 0 else 0
        if spot_price == 0:
            return 0
        impact_pct = (spot_price - execution_price) / spot_price
        return max(0, int(impact_pct * 10000))

    @staticmethod
    def is_order_safe(
        amount_lamports: int,
        pool_liquidity_lamports: int,
        max_impact_bps: int = 120
    ) -> Tuple[bool, int]:
        if pool_liquidity_lamports == 0:
            return False, 10000
        order_pct = (amount_lamports / pool_liquidity_lamports) * 100
        estimated_impact_bps = int(order_pct * 25)
        return estimated_impact_bps <= max_impact_bps, estimated_impact_bps
