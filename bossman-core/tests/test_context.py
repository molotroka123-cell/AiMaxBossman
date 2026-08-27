"""Раздел 10: бюджет окна, порядок блоков, схлопывание истории, уплотнение."""
from bossman.context import (BLOCK_SHARES, COLLAPSE_AFTER, ContextBudget, ContextBuilder,
                             estimate_tokens)


def make_builder(window=64_000):
    budget = ContextBudget(window=window)
    return ContextBuilder(budget, system="Ты — тестовый агент.", refs="Стиль-гайд: краткость.",
                          key_constraint="только чтение")


def test_budget_shares_scale_with_window():
    b64 = ContextBudget(window=64_000)
    b32 = ContextBudget(window=32_000)
    for block in BLOCK_SHARES:
        assert b64.limits[block] == int(64_000 * BLOCK_SHARES[block])
        assert b32.limits[block] * 2 == b64.limits[block]  # те же доли для другого окна


def test_block_order_fixed_and_constraint_last():
    b = make_builder()
    b.set_retrieved(["сводка этапа 1"])
    b.add_assistant("думаю")
    b.add_tool_result("fs.read", "содержимое файла", "прочитан файл")
    msgs = b.build("Проверь письмо")
    assert msgs[0]["role"] == "system" and "тестовый агент" in msgs[0]["content"]
    assert "Стиль-гайд" in msgs[1]["content"]           # справочники после системного
    assert "Подтянутое" in msgs[2]["content"]           # сводки/RAG после справочников
    assert msgs[-1]["role"] == "user"                   # задача — последней
    assert msgs[-1]["content"].endswith("Ключевое ограничение задачи: только чтение")


def test_old_tool_results_collapse_to_one_line():
    b = make_builder()
    for i in range(COLLAPSE_AFTER + 3):
        b.add_tool_result("fs.read", f"длинное содержимое номер {i} " * 50, f"итог {i}")
    msgs = b._history_messages()
    assert msgs[0]["content"] == "fs.read: итог 0"       # старое — одной строкой
    assert "длинное содержимое" in msgs[-1]["content"]   # свежее — целиком


def test_compaction_replaces_history():
    b = make_builder(window=4_000)  # маленькое окно, чтобы переполнить
    for i in range(30):
        b.add_tool_result("run", "вывод " * 200, f"шаг {i}")
    assert b.needs_compaction("задача")
    b.apply_compaction("## Сводка\nСделано: всё")
    assert b.history == []
    assert "Сводка" in "".join(m["content"] for m in b.build("задача"))


def test_block_tokens_reported_per_call():
    b = make_builder()
    blocks = b.block_tokens("задача")
    assert set(blocks) == {"system", "refs", "retrieved", "history", "task"}
    assert blocks["system"] == estimate_tokens(b.system)
