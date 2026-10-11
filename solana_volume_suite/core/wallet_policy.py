"""Role separation and per-wallet limits (policy only; no keys, no signing).

Rules enforced before any action is even planned:
  * every wallet has exactly one role, assigned once and immutable;
  * a TRADE wallet may only perform trade actions (swap/quote) - never
    withdraw or transfer;
  * a TREASURY wallet performs no trading at all;
  * per-order and per-day notional caps apply per wallet;
  * unassigned wallets are denied (default deny).
"""
import time
from enum import Enum
from typing import Dict, Optional


class PolicyViolation(PermissionError):
    pass


class WalletRole(str, Enum):
    TRADE = "trade"
    TREASURY = "treasury"


TRADE_ACTIONS = frozenset({"swap", "quote", "cancel"})


class WalletPolicy:
    def __init__(self, per_order_limit_usd: float, per_day_limit_usd: float,
                 clock=time.time):
        if per_order_limit_usd <= 0 or per_day_limit_usd < per_order_limit_usd:
            raise ValueError("limits must be positive and day >= order")
        self.per_order_limit_usd = per_order_limit_usd
        self.per_day_limit_usd = per_day_limit_usd
        self._clock = clock
        self._roles: Dict[str, WalletRole] = {}
        self._spent: Dict[str, Dict[int, float]] = {}

    def assign(self, wallet: str, role: WalletRole) -> None:
        existing: Optional[WalletRole] = self._roles.get(wallet)
        if existing is not None and existing != role:
            raise PolicyViolation(f"wallet {wallet!r} already has role {existing.value}")
        self._roles[wallet] = role

    def _day(self) -> int:
        return int(self._clock() // 86400)

    def spent_today(self, wallet: str) -> float:
        return self._spent.get(wallet, {}).get(self._day(), 0.0)

    def check(self, wallet: str, action: str, notional_usd: float) -> bool:
        role = self._roles.get(wallet)
        if role is None:
            raise PolicyViolation(f"wallet {wallet!r} has no assigned role (default deny)")
        if role is WalletRole.TREASURY:
            raise PolicyViolation("treasury wallets never trade")
        if action not in TRADE_ACTIONS:
            raise PolicyViolation(f"action {action!r} not permitted for a trade wallet")
        if notional_usd < 0 or notional_usd > self.per_order_limit_usd:
            raise PolicyViolation("order exceeds per-wallet per-order limit")
        if self.spent_today(wallet) + notional_usd > self.per_day_limit_usd:
            raise PolicyViolation("order exceeds per-wallet daily limit")
        return True

    def record(self, wallet: str, notional_usd: float) -> None:
        d = self._spent.setdefault(wallet, {})
        d[self._day()] = d.get(self._day(), 0.0) + notional_usd
