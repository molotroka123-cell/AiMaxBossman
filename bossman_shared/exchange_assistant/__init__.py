"""Exchange assistant for the owner's SwapMe desk, inside the existing Bossman.

Rates with source and time -> deterministic Decimal quote (margin, expiry, formula) ->
deal only after an explicit "exchange done" -> two legs, reversal, dashboard.
No network writes, no transfers, no messages to clients. Money is Decimal; the language
model never does arithmetic. See docs/owner/BOSSMAN_AI_ASSISTANT_SPEC_RU.md.
"""
from .agent import Assistant
from .quote import Quote, QuoteError, build_quote, render_branded_document
from .rates import Rate, RateBook, RateError, licensed_xe_feed_status

__all__ = [
    "Assistant",
    "Quote",
    "QuoteError",
    "Rate",
    "RateBook",
    "RateError",
    "build_quote",
    "licensed_xe_feed_status",
    "render_branded_document",
]
