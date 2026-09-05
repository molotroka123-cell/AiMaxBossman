"""TZ-09 §2 — TR-01 (актуальные цены), TR-02 (верхняя граница токенов по скрипту),
TR-03 (более точный, но всё ещё верхний потолок стоимости)."""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman_shared import fable_budget as fb  # noqa: E402

_MODEL_ID = re.compile(r"claude-(?:fable|opus|sonnet|haiku)-[0-9][0-9a-z.-]*")
_SCAN_DIRS = ("command-center/bcc", "bossman-core/bossman", "bossman_shared")


def _configured_anthropic_models() -> set[str]:
    found: set[str] = set()
    for d in _SCAN_DIRS:
        for py in (ROOT / d).rglob("*.py"):
            if "tests" in py.parts:
                continue
            for m in _MODEL_ID.findall(py.read_text(encoding="utf-8", errors="ignore")):
                found.add(m.rstrip(".-"))
    return found


def test_price_table_covers_configured_models():
    """TR-01: каждая модель Anthropic, на которую продукт может маршрутизировать,
    обязана иметь цену — иначе платная работа молча отказывает."""
    missing = sorted(m for m in _configured_anthropic_models()
                     if m not in fb.PRICE_TABLE and not any(m.startswith(k) for k in fb.PRICE_TABLE))
    assert not missing, f"models without a price: {missing}"
    for required in ("claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"):
        assert required in fb.PRICE_TABLE
    assert fb.PRICE_TABLE_AS_OF == "2026-09-05"
    with pytest.raises(fb.BudgetExhausted):
        fb.estimate_worst_case_usd("skynet-9", 1000, 100)          # fail-closed сохранён


@pytest.mark.parametrize("text,min_tokens", [
    ("a" * 3000, 1000),                       # латиница: 3 симв/токен
    ("привет мир " * 91, 500),                # кириллица 1001 симв → ≥ 556 (1.8 симв/токен)
    ("日本語のテキスト" * 38, 434),            # CJK 304 симв → ≥ 434 (0.7)
    ("Заказ №42: deploy to prod 今日", 15),   # смешанный
])
def test_token_upper_bound_cyrillic_cjk(text, min_tokens):
    """TR-02: оценка ≥ реалистичного максимума токенов для скрипта; для не-латиницы
    строго выше старой оценки chars/3."""
    est = fb.estimate_tokens_upper(text)
    assert est >= min_tokens
    legacy = len(text) / 3.0
    if any(fb._script(c) != "latin" for c in text):
        assert est > legacy


def test_token_upper_bound_never_below_legacy_for_latin():
    for n in (1, 10, 100, 1000):
        assert fb.estimate_tokens_upper("x" * n) >= n / 3.0


def test_worst_case_tighter_but_still_upper():
    """TR-03: новая оценка ≤ старой и ≥ фактической стоимости при ЛЮБОМ
    распределении входа по корзинам (input / cache_read / cache_write)."""
    rng = random.Random(9)
    models = list(fb.PRICE_TABLE)
    for _ in range(200):
        model = rng.choice(models)
        chars = rng.randint(0, 200_000)
        out = rng.randint(0, 8_000)
        new = fb.estimate_worst_case_usd(model, chars, out)
        old = fb._legacy_worst_case_usd(model, chars, out)
        assert new <= old + 1e-9
        tokens_in = int(chars / 3.0) + fb._PER_MESSAGE_OVERHEAD_TOKENS
        # любое разбиение входа по корзинам не дороже потолка
        a = rng.randint(0, tokens_in); b = rng.randint(0, tokens_in - a); c = tokens_in - a - b
        actual = fb.actual_usd(model, input_tokens=a, output_tokens=out, cache_read_tokens=b, cache_write_tokens=c)
        assert actual <= new + 1e-6


def test_worst_case_with_cyrillic_text_reserves_more_than_char_count_alone():
    ru = "Проверь отчёт и отправь владельцу " * 200
    by_chars = fb.estimate_worst_case_usd("claude-opus-5", len(ru), 100)
    by_text = fb.estimate_worst_case_usd("claude-opus-5", len(ru), 100, prompt_text=ru)
    assert by_text > by_chars


def test_reserve_records_price_version(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "LEDGER_PATH", tmp_path / "ledger.json")
    budget = fb.canonical_budget() if hasattr(fb, "canonical_budget") else None
    if budget is None:
        pytest.skip("canonical_budget not exposed")
    rid = budget.reserve(0.01, purpose="test")
    import json
    raw = json.loads((tmp_path / "ledger.json").read_text())
    text = json.dumps(raw)
    assert fb.PRICE_TABLE_AS_OF in text and rid in text
