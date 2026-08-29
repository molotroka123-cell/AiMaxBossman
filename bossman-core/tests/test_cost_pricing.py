from decimal import Decimal
import pytest
from bossman.cost_control.pricing import UnknownPricing,estimate_usd

def test_openrouter_style_per_token_pricing_exact():
    cost=estimate_usd(prompt_tokens_upper=1000,completion_tokens_upper=500,
        prompt_price_per_token="0.000001",completion_price_per_token="0.000002")
    assert cost==Decimal("0.002000")

def test_unknown_price_rejected():
    with pytest.raises(UnknownPricing):
        estimate_usd(prompt_tokens_upper=1,completion_tokens_upper=1,
            prompt_price_per_token="not-a-price",completion_price_per_token="0")
