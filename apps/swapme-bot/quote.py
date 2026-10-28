"""SwapMe Quote wrapper and strict margin enforcement.

Re-exports core exchange assistant quote facilities while enforcing
COM-UP-1 strict manager margin validation.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure bossman_shared is in sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bossman_shared.exchange_assistant.quote import (
    CURRENCIES,
    DEFAULT_MARGIN,
    SOFT_MARGIN_LIMIT,
    DEFAULT_TTL,
    CENT,
    MAX_AMOUNT,
    Quote,
    QuoteError,
    margin_pct,
    build_quote,
    compute_quote_strict,
    render_branded_document,
)

__all__ = [
    "CURRENCIES",
    "DEFAULT_MARGIN",
    "SOFT_MARGIN_LIMIT",
    "DEFAULT_TTL",
    "CENT",
    "MAX_AMOUNT",
    "Quote",
    "QuoteError",
    "margin_pct",
    "build_quote",
    "compute_quote_strict",
    "render_branded_document",
]
