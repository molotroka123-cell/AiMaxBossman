import os
import sys
import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

# Ensure root of solana_volume_suite is on path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.jito_client import JitoBundleClient
from core.price_impact_calculator import PriceImpactCalculator
from core.treasury_guard import TreasuryGuard


def test_jito_tip_calculation():
    client = JitoBundleClient()
    assert client.calculate_dynamic_tip("low") == 10_000
    assert client.calculate_dynamic_tip("medium") == 50_000
    assert client.calculate_dynamic_tip("high") == 100_000
    assert client.calculate_dynamic_tip("extreme") == 250_000


def test_price_impact_safe_order():
    safe, impact = PriceImpactCalculator.is_order_safe(100_000_000, 10_000_000_000, 120)
    assert safe is True
    assert impact < 120


def test_price_impact_dangerous_order():
    safe, impact = PriceImpactCalculator.is_order_safe(2_000_000_000, 10_000_000_000, 120)
    assert safe is False
    assert impact > 120


def test_treasury_guard_budget():
    guard = TreasuryGuard(max_allowed_loss_usd=50.0)
    guard.record_trade(volume_usd=1000, fee_usd=2.5, slippage_usd=1.2, jito_tip_sol=0.00005)
    assert guard.is_within_budget() is True
    guard.record_trade(volume_usd=5000, fee_usd=25.0, slippage_usd=30.0, jito_tip_sol=0.0001)
    assert guard.is_within_budget() is False
