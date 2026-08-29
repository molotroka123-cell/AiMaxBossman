"""ЭТАП 2.222 — Compact как structured continuation state, а не summarize.

Compact обязан превращать историю в структурированное состояние продолжения
(objective, constraints, active files/branch, decisions, versions/numbers,
bugs/failed approaches, test status, open threads, next actions, важные
сообщения verbatim) и гарантировать выживание критических якорей. При слишком
маленьком budget critical info НЕ теряется — поднимается overflow indicator.
"""
from bossman.context_engine import CompactSkill, Message


def _conversation() -> list[Message]:
    return [
        Message("user", "Работаем над bossman-core, ветка claude/bossman-control-v03. "
                        "Цель: интегрировать context engine v0.3."),
        Message("assistant", "Decision: используем hybrid retrieval после reranking."),
        Message("user", "Обязательно: нельзя удалять raw source после distillation. "
                        "Это security constraint."),
        Message("assistant", "Bug: старый компрессор терял числа. Пробовал наивный "
                             "summarizer — failed, числа снова терялись."),
        Message("assistant", "Правлю bossman/runner.py и bossman/context_engine/compact.py."),
        Message("user", "Не забудь: держи 128 GB как якорь и версию v0.3."),
        Message("assistant", "TODO: подключить ContextCompiler в петлю runner."),
        Message("assistant", "Прогон тестов: 37 passed, 0 failed."),
        Message("user", "Открытый вопрос: как хранить negative memory отдельно?"),
        Message("assistant", "Понял, продолжаю. Ответ остаётся verbatim."),
    ]


def test_structured_sections_and_anchors():
    out = CompactSkill().compact(_conversation(), project="bossman",
                                 target_tokens=6000, keep_recent=2,
                                 query="интеграция context engine")
    t = out.text
    assert "## Constraints" in t
    assert "нельзя удалять raw source" in t
    assert "## Decisions" in t
    assert "hybrid retrieval" in t
    assert "## Bugs & failed approaches" in t
    assert "failed" in t.lower()
    assert "## Test status" in t
    assert "37 passed" in t
    assert "## Next actions" in t
    assert "## Open threads" in t
    assert "## Critical anchors" in t
    # Критические якоря
    for anchor in ("128 GB", "v0.3", "bossman/runner.py", "claude/bossman-control-v03"):
        assert anchor in t, f"anchor lost: {anchor}"
        assert anchor in out.anchors, f"anchor not extracted: {anchor}"
    assert out.overflow is False
    assert out.quality_checks["anchors_preserved"] is True
    assert out.quality_checks["recent_preserved"] is True


def test_small_budget_raises_overflow_but_keeps_critical():
    out = CompactSkill().compact(_conversation(), project="bossman",
                                 target_tokens=40, keep_recent=2,
                                 query="интеграция context engine")
    # Слишком маленький бюджет: НЕ теряем critical info, поднимаем overflow.
    assert out.overflow is True
    assert out.quality_checks["within_budget"] is False
    # Критические якоря переживают compaction даже при overflow.
    for anchor in ("128 GB", "v0.3", "claude/bossman-control-v03"):
        assert anchor in out.text, f"critical anchor lost under tight budget: {anchor}"
    assert out.quality_checks["anchors_preserved"] is True
    # Свежий хвост вербатим сохранён прежде extractive-сигналов.
    assert out.quality_checks["recent_preserved"] is True


def test_trimming_never_drops_constraints_or_test_status():
    out = CompactSkill().compact(_conversation(), project="bossman",
                                 target_tokens=120, keep_recent=1,
                                 query="интеграция")
    # Даже при агрессивной обрезке низкосигнальной истории — constraint, test
    # status и якоря остаются.
    assert "нельзя удалять raw source" in out.text
    assert "37 passed" in out.text
    assert "128 GB" in out.text
