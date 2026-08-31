"""V2.6 Phase 1 — единый capability-словарь и DecisionSignals.

Обе вещи — библиотеки без побочных эффектов: словарь advisory (ничего не
запрещает, существующие free-form конфиги валидны), сигналы детерминированы
и без LLM (правило «не звать модель, чтобы выбрать уровень compute»).
"""
from __future__ import annotations

from bossman import capabilities as caps
from bossman.signals import DecisionSignals, derive_signals


def test_vocab_covers_required_model_and_tool_capabilities():
    for c in ("vision", "ocr", "stt", "tts", "embedding", "image_generation",
              "image_edit", "upscale", "code", "reasoning"):
        assert caps.is_known(c), c
    for c in ("web_search", "browser", "file_parse", "file_create",
              "computer_control", "python_analysis", "database"):
        assert caps.is_known(c), c


def test_vocab_normalizes_legacy_config_synonyms():
    # живые имена из gateway.example.yaml и tools/registry.yaml
    assert caps.normalize("embeddings") == caps.EMBEDDING
    assert caps.normalize("transcribe") == caps.STT
    assert caps.normalize("voiceover") == caps.TTS
    assert caps.normalize("t2v") == caps.VIDEO_GENERATION
    assert caps.normalize("VISION") == caps.VISION


def test_vocab_is_advisory_not_enforcing():
    unknown = caps.unknown_capabilities(["vision", "какая-то-опечатка", "tools"])
    assert unknown == ["какая-то-опечатка"]
    assert caps.unknown_capabilities(["text", "code"]) == []


def test_signals_trivial_task_is_cheap_and_low_risk():
    s = derive_signals("посчитай 2+2")
    assert s.task_complexity < 0.2
    assert s.risk == 0.0
    assert s.resource_budget == 1.0


def test_signals_multistep_research_scores_complex():
    s = derive_signals(
        "исследуй конкурентов, затем создай таблицу-отчёт, после этого сравни цены")
    assert s.task_complexity >= 0.4
    assert any("многошаговости" in r for r in s.reasons)


def test_signals_irreversible_actions_raise_risk():
    s = derive_signals("оплати счёт и отправь письмо клиенту")
    assert s.risk >= 0.5
    assert any("необратимости" in r for r in s.reasons)


def test_signals_failure_history_raises_complexity():
    base = derive_signals("почини тест")
    retried = derive_signals("почини тест", previous_failures=3)
    assert retried.task_complexity > base.task_complexity


def test_signals_frozen_updated_via_with_():
    s = derive_signals("задача")
    s2 = s.with_(uncertainty=0.9)
    assert s.uncertainty == 0.0 and s2.uncertainty == 0.9
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.uncertainty = 1.0  # type: ignore[misc]


def test_signals_is_deterministic_and_fast():
    import time
    t0 = time.perf_counter()
    for _ in range(1000):
        derive_signals("исследуй рынок, затем создай отчёт и отправь его")
    per_call_ms = (time.perf_counter() - t0)
    assert per_call_ms < 1.0, "1000 вызовов должны укладываться в <1s (нет LLM/IO)"
    a = derive_signals("оплати счёт")
    b = derive_signals("оплати счёт")
    assert a == b
