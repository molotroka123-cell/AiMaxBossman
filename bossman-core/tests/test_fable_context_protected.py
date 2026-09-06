"""Fable hardening — the context budget must not silently cut instructions.

`ContextBuilder._fit` used to slice the system/refs block by characters and
append "[обрезано по бюджету блока]": a long system prompt lost its tail (the
constraints), a long refs block lost the last project rules, and nothing but a
marker inside the prompt recorded it. Now trailing `## ` sections are dropped
whole, last first, the head is never cut, and the loss is observable through
`pruning_report()`.
"""
from __future__ import annotations

from bossman.context import BLOCK_SHARES, ContextBudget, ContextBuilder, estimate_tokens

WINDOW = 4_000                                  # system limit = 200 tokens ≈ 600 chars
RULES = "Никогда не отправляй письма без подтверждения. Запрещено удалять файлы вне workspace."


def _system(memory_chars: int, tools: int = 3) -> str:
    prompt = "Ты — ассистент проекта.\n" + RULES
    tool_lines = "\n".join(f"- tool_{i}: описание инструмента {i}" for i in range(tools))
    memory = "## Твоя память (memory.md)\n" + ("память " * (memory_chars // 7))
    return f"{prompt}\n\n## Доступные инструменты\n{tool_lines}\n\n{memory}"


def test_trailing_section_is_dropped_whole_and_rules_survive():
    b = ContextBuilder(ContextBudget(window=WINDOW), _system(memory_chars=3_000))
    assert "[обрезано" not in b.system
    assert RULES in b.system, "the constraints at the end of the head must survive"
    assert "## Доступные инструменты" in b.system and "tool_2" in b.system
    assert "## Твоя память" not in b.system
    assert b.dropped["system"] == ["## Твоя память (memory.md)"]
    assert b.pruning_report() == {"dropped": {"system": ["## Твоя память (memory.md)"]}}
    assert estimate_tokens(b.system) <= b.budget.limits["system"]


def test_sections_drop_last_first_memory_before_tools_before_prompt():
    tools = "\n".join(f"- tool_{i}: " + "x" * 40 for i in range(20))       # ~300 tokens of tools
    system = "Промпт.\n" + RULES + "\n\n## Доступные инструменты\n" + tools + "\n\n## Твоя память (memory.md)\nм"
    b = ContextBuilder(ContextBudget(window=WINDOW), system)
    assert b.dropped["system"] == ["## Доступные инструменты", "## Твоя память (memory.md)"]
    assert b.system.startswith("Промпт.") and RULES in b.system


def test_head_is_never_cut_even_when_it_alone_exceeds_the_budget():
    head = "Правило №1. " + ("Очень длинное правило политики. " * 60) + RULES
    b = ContextBuilder(ContextBudget(window=WINDOW), head)
    assert b.system == head, "a protected head is kept whole, not sliced mid-rule"
    assert "[обрезано" not in b.system
    assert b.over_budget["system"] == estimate_tokens(head) - b.budget.limits["system"]
    assert "over_budget" in b.pruning_report()
    assert b.build("задача")[0] == {"role": "system", "content": head}


def test_refs_keep_head_rule_and_report_dropped_sections():
    refs = "Правила проекта: стиль snake_case.\n\n## Раскадровка\n" + ("кадр " * 400) + "\n\n## Стиль-гайд\nкратко"
    b = ContextBuilder(ContextBudget(window=WINDOW), "sys", refs=refs)
    assert b.refs.startswith("Правила проекта: стиль snake_case.")
    assert "## Раскадровка" not in b.refs
    assert b.dropped["refs"][0] == "## Раскадровка"
    assert "[обрезано" not in b.refs


def test_within_budget_is_untouched_and_report_is_empty():
    system = _system(memory_chars=50)
    b = ContextBuilder(ContextBudget(window=64_000), system, refs="## Правила\nкоротко")
    assert b.system == system and b.refs == "## Правила\nкоротко"
    assert b.pruning_report() == {}
    assert b.budget.limits["system"] == int(64_000 * BLOCK_SHARES["system"])
