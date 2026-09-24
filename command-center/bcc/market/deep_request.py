"""Read-only on-demand deep-analysis request contract.

Execution wiring belongs to the Telegram Companion/1.5 workflow layer. This
module validates owner-request parameters and builds a typed request without
granting any trading capability.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_SYMBOLS = frozenset({"BTC"})
ALLOWED_CONTEXT_HOURS = frozenset({1, 4, 6, 12, 24})


@dataclass(frozen=True)
class DeepAnalysisRequest:
    symbol: str = "BTC"
    context_hours: int = 6
    bypass_notification_cooldown: bool = True
    require_fresh: bool = True
    read_only: bool = True

    def validate(self) -> list[str]:
        errors = []
        if self.symbol.upper() not in ALLOWED_SYMBOLS:
            errors.append("unsupported_symbol")
        if self.context_hours not in ALLOWED_CONTEXT_HOURS:
            errors.append("unsupported_context_window")
        if not self.read_only:
            errors.append("must_be_read_only")
        return errors


def parse_deep_command(text: str) -> DeepAnalysisRequest | None:
    raw = " ".join((text or "").strip().split())
    low = raw.lower()
    triggers = ("/market_deep", "глубокий анализ btc", "анализ btc", "разбери рынок сейчас")
    if not any(low.startswith(t) for t in triggers):
        return None
    hours = 6
    for token in low.split():
        if token.endswith(("h", "ч")) and token[:-1].isdigit():
            hours = int(token[:-1])
    req = DeepAnalysisRequest(symbol="BTC", context_hours=hours)
    return req if not req.validate() else None
