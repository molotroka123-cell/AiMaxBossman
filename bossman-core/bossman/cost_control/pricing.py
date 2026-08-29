from __future__ import annotations
from decimal import Decimal,InvalidOperation
from .models import money

class UnknownPricing(RuntimeError): pass

def _d(v)->Decimal:
    try:return Decimal(str(v))
    except (InvalidOperation,TypeError,ValueError) as exc:raise UnknownPricing(f"invalid provider price {v!r}") from exc

def normalize_per_token_price(value)->Decimal:
    p=_d(value)
    if p<0 or not p.is_finite():raise UnknownPricing("price must be finite and non-negative")
    return p

def estimate_usd(*,prompt_tokens_upper:int,completion_tokens_upper:int,
                 prompt_price_per_token,completion_price_per_token,fixed_request_usd=0):
    if prompt_tokens_upper<0 or completion_tokens_upper<0:raise ValueError("token bounds must be non-negative")
    return money(Decimal(prompt_tokens_upper)*normalize_per_token_price(prompt_price_per_token)
                 +Decimal(completion_tokens_upper)*normalize_per_token_price(completion_price_per_token)
                 +_d(fixed_request_usd))

def actual_usd(*,prompt_tokens:int,completion_tokens:int,
               prompt_price_per_token,completion_price_per_token,fixed_request_usd=0):
    return estimate_usd(prompt_tokens_upper=prompt_tokens,completion_tokens_upper=completion_tokens,
                        prompt_price_per_token=prompt_price_per_token,
                        completion_price_per_token=completion_price_per_token,
                        fixed_request_usd=fixed_request_usd)
