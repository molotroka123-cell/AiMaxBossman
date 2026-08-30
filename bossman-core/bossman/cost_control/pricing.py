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

def cache_aware_actual_usd(*,prompt_tokens:int,completion_tokens:int,
                           cached_tokens:int=0,cache_write_tokens:int=0,
                           prompt_price_per_token,completion_price_per_token,
                           cache_read_price_per_token,cache_write_price_per_token,
                           fixed_request_usd=0):
    """Fallback billing estimate when the provider did not return ``usage.cost``.

    Cached and cache-write tokens are subsets of prompt tokens and are charged
    at their own non-zero rates.  Inconsistent provider counters fail closed
    instead of silently producing a negative fresh-token count.
    """
    counts=(prompt_tokens,completion_tokens,cached_tokens,cache_write_tokens)
    if any(not isinstance(v,int) or v<0 for v in counts):
        raise ValueError("token counts must be non-negative integers")
    if cached_tokens+cache_write_tokens>prompt_tokens:
        raise ValueError("cache token counters exceed prompt_tokens")
    fresh=prompt_tokens-cached_tokens-cache_write_tokens
    return money(
        Decimal(fresh)*normalize_per_token_price(prompt_price_per_token)
        +Decimal(cached_tokens)*normalize_per_token_price(cache_read_price_per_token)
        +Decimal(cache_write_tokens)*normalize_per_token_price(cache_write_price_per_token)
        +Decimal(completion_tokens)*normalize_per_token_price(completion_price_per_token)
        +_d(fixed_request_usd)
    )
