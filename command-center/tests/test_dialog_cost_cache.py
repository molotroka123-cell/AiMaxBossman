"""Owner 29.10: «следи что бы цена диалога считалась правильно» — cached prompt tokens of OpenAI-compatible providers
(Outrun DeepSeek, OpenRouter) are billed at the cache price, not at the full input price."""
import pytest

from bcc.engine import _cost
from bcc.providers import OpenAICompatAdapter

DEEPSEEK = {"price_in": 0.66, "price_out": 2.64, "caps": {"price_cache_read": 0.02}}


def _res(usage):
    return OpenAICompatAdapter._result_from({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                             "usage": usage}, "m")


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 100_000, "completion_tokens": 10_000, "prompt_tokens_details": {"cached_tokens": 80_000}},   # OpenAI style
    {"prompt_tokens": 100_000, "completion_tokens": 10_000, "prompt_cache_hit_tokens": 80_000},                     # DeepSeek style
])
def test_cached_prompt_tokens_are_billed_at_the_cache_price(usage):
    r = _res(usage)
    assert r.cache_read_tokens == 80_000
    # 20k fresh x 0.66 + 80k cached x 0.02 + 10k out x 2.64 (per 1M)
    assert _cost(DEEPSEEK, r) == pytest.approx(0.0132 + 0.0016 + 0.0264)


def test_no_cache_report_means_full_input_price():
    r = _res({"prompt_tokens": 100_000, "completion_tokens": 10_000})
    assert r.cache_read_tokens == 0
    assert _cost(DEEPSEEK, r) == pytest.approx(0.066 + 0.0264)


def test_unknown_cache_price_stays_conservative_and_bogus_cache_count_is_clamped():
    r = _res({"prompt_tokens": 1000, "completion_tokens": 0, "prompt_cache_hit_tokens": 5000})
    assert r.cache_read_tokens == 1000                                   # never more cached than prompt tokens
    assert _cost({"price_in": 0.66, "price_out": 2.64}, r) == pytest.approx(0.00066)   # no cache price -> full input price
