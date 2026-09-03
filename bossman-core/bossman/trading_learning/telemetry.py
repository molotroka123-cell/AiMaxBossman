"""Телеметрия токенов, стоимости и попаданий в кэш. Без выдуманных цифр.

Правило: цена провайдера неизвестна — REFUSE. Оценка «примерно столько же,
сколько у похожей модели» в отчёте о расходах владельца неотличима от вранья,
поэтому её здесь нет.

Экономия токенов начинается с того, что в сильную модель уходит компактный
сегмент, а не транскрипт: prefix_tokens считаются отдельно от variable_tokens,
чтобы было видно, сколько реально экономит стабильный префикс.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .safety import UnknownProviderPrice, utcnow


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Цена за токен. Оба поля обязательны — половинчатой цены не бывает."""

    prompt_per_token: Decimal
    completion_per_token: Decimal
    cached_prompt_per_token: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("prompt_per_token", "completion_per_token"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or value < 0:
                raise UnknownProviderPrice(f"{name} must be a non-negative Decimal")


@dataclass
class CallRecord:
    step: str
    model: str
    prefix_tokens: int = 0        # неизменяемый префикс инструкций
    variable_tokens: int = 0      # то, что действительно меняется от вызова
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: Decimal | None = None
    priced: bool = False
    at: str = field(default_factory=lambda: utcnow().isoformat())

    @property
    def prompt_tokens(self) -> int:
        return self.prefix_tokens + self.variable_tokens


@dataclass
class TokenLedger:
    """Настоящая телеметрия: что посчитано — посчитано, что нет — помечено."""

    records: list[CallRecord] = field(default_factory=list)
    prices: dict[str, ModelPrice] = field(default_factory=dict)

    def set_price(self, model: str, price: ModelPrice) -> None:
        self.prices[model] = price

    def record(self, step: str, model: str, *, prefix_tokens: int = 0,
               variable_tokens: int = 0, completion_tokens: int = 0,
               cached_tokens: int = 0, require_price: bool = False) -> CallRecord:
        """Записать вызов. require_price=True — отказ, если цена неизвестна."""
        for name, value in (("prefix_tokens", prefix_tokens),
                            ("variable_tokens", variable_tokens),
                            ("completion_tokens", completion_tokens),
                            ("cached_tokens", cached_tokens)):
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if cached_tokens > prefix_tokens + variable_tokens:
            raise ValueError("cached_tokens exceed prompt tokens")
        rec = CallRecord(step=step, model=model, prefix_tokens=prefix_tokens,
                         variable_tokens=variable_tokens,
                         completion_tokens=completion_tokens, cached_tokens=cached_tokens)
        price = self.prices.get(model)
        if price is None:
            if require_price:
                raise UnknownProviderPrice(
                    f"no known price for model {model!r}: refusing to estimate cost")
        else:
            fresh = rec.prompt_tokens - rec.cached_tokens
            cached_rate = price.cached_prompt_per_token
            if cached_tokens and cached_rate is None:
                raise UnknownProviderPrice(
                    f"model {model!r} reported cached tokens but has no cache price")
            cost = (Decimal(fresh) * price.prompt_per_token
                    + Decimal(rec.completion_tokens) * price.completion_per_token)
            if cached_tokens and cached_rate is not None:
                cost += Decimal(cached_tokens) * cached_rate
            rec.cost_usd = cost
            rec.priced = True
        self.records.append(rec)
        return rec

    def summary(self) -> dict:
        prompt = sum(r.prompt_tokens for r in self.records)
        cached = sum(r.cached_tokens for r in self.records)
        priced = [r for r in self.records if r.priced]
        unpriced = [r for r in self.records if not r.priced]
        return {
            "calls": len(self.records),
            "prompt_tokens": prompt,
            "prefix_tokens": sum(r.prefix_tokens for r in self.records),
            "variable_tokens": sum(r.variable_tokens for r in self.records),
            "completion_tokens": sum(r.completion_tokens for r in self.records),
            "cached_tokens": cached,
            "cache_hit_ratio": (cached / prompt) if prompt else 0.0,
            "cost_usd_known": str(sum((r.cost_usd or Decimal(0) for r in priced), Decimal(0))),
            "calls_without_price": len(unpriced),
            "cost_status": "PARTIAL_UNKNOWN" if unpriced else ("KNOWN" if priced else "NO_CALLS"),
        }
