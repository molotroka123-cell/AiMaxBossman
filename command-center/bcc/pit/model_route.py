"""Live-price free routing verdicts for Jeff (Jeff 1.8).

A remote chat model is allowed only when the LIVE provider catalog lists it with a
known price of exactly 0 for both prompt and completion, and its id is an
OpenRouter ``:free`` id, and it is not a banned model family (``bcc.pit.model_policy``). The suffix alone is never proof, and neither is a stale
answer: the runtime rechecks prices every ``PRICE_RECHECK_SECONDS`` and fails
closed when the catalog cannot be read. The reason a route was refused is a stable
code, never provider text.
"""
from __future__ import annotations

import re

JEFF_MODEL_ROUTE_SCHEMA = "bossman.pit.model-route/1"

#: A verified route is trusted for at most this long before prices are read again.
PRICE_RECHECK_SECONDS = 120.0
#: A route that answered HTTP 402 stays blocked for this long, whatever the catalog says.
PAYMENT_BLOCK_SECONDS = 3600.0

NOT_LISTED = "not_listed"
PRICE_UNKNOWN = "price_unknown"
PRICE_POSITIVE = "price_positive"
NOT_FREE_ID = "not_free_id"
PAYMENT_REQUIRED = "payment_required"


def route_verdict(model: str, listed: bool, prices: dict | None) -> str:
    """'' when the model is a verified free remote route, else a stable reason code."""
    from .model_policy import BANNED_MODEL, is_banned_model
    if is_banned_model(model):
        return BANNED_MODEL           # owner ban: whatever the catalog or price says
    if not listed:
        return NOT_LISTED
    values = [(prices or {}).get("prompt"), (prices or {}).get("completion")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0
           for v in values):
        return PRICE_UNKNOWN
    if any(v > 0 for v in values):
        return PRICE_POSITIVE
    if not str(model).endswith(":free"):
        return NOT_FREE_ID
    return ""


#: The head our own error formatter puts in front of a provider's answer for a 402 (never anywhere else in the text).
_OWN_402_HEAD = re.compile(r"^(?:провайдер отказал|провайдер требует оплату) \(402\)")


def is_payment_required(exc: BaseException) -> bool:
    """True only for a payment demand: the HTTP status carried on the error. The BODY a provider sends is its own
    text (a 500 whose message mentions "(402)" must not close the free route for an hour). An error that carries no
    status at all (a wrapper that only has text) is matched by the fixed head our formatter writes, never by a
    substring of the rest."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 402
    return bool(_OWN_402_HEAD.match(str(exc)))
