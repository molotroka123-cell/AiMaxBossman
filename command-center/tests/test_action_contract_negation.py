"""Astra/Codex F7 (2026-09-08, reproduced 2/2 on 45027d3): a prohibition is not
a request.

"Calculate 17*23. Do not use tools or write any file." was classified as
TERMINAL_FILE_ACTION, so an answer that obeyed the owner failed the gate with
`action_contract/no_verified_action` (owner-live run #33). The fix drops only
the negated SPAN, never the sentence, so a real request sharing a sentence with
a prohibition survives — the failure mode the task explicitly forbids.
"""
from __future__ import annotations

import pytest

from bcc.features.action_contract import classify_all, positive_request_text


def names(prompt: str) -> list[str]:
    return [c.name for c in classify_all(prompt)]


# ------------------------------------------------------------ prohibitions

@pytest.mark.parametrize("prompt", [
    "Calculate 17*23. Do not use tools or write any file.",
    "Calculate 17*23. Don't run terminal.",
    "Calculate 17*23. Don't run the terminal or any script.",
    "Calculate 17*23 without terminal.",
    "Calculate 17*23 without using tools or writing files.",
    "Answer in text. Never write files.",
    "Calculate 17*23. You must not use the terminal.",
    "Calculate 17*23. Avoid creating files.",
    # Russian, the owner's actual phrasing (run #33)
    "Посчитай 17*23. Не используй инструменты и не пиши файлы.",
    "Посчитай 17*23. Без терминала.",
    "Посчитай 17*23. Без терминала и без файлов.",
    "Посчитай 17*23. Нельзя запускать команды и создавать файлы.",
    "Посчитай 17*23. Никаких файлов, никакого терминала.",
    "Ответь текстом, не создавай файл и не запускай скрипт.",
])
def test_a_prohibited_action_is_not_a_required_action(prompt):
    assert names(prompt) == []


def test_the_positive_control_still_requires_nothing():
    assert names("Calculate 17*23.") == []


# -------------------------------------------------------- real requests kept

@pytest.mark.parametrize("prompt", [
    "Create a file notes.txt with the result",
    "Run the script build.sh",
    "Создай файл result.txt с ответом",
    "Запусти скрипт build.sh в терминале",
])
def test_a_positive_request_is_still_a_contract(prompt):
    assert "TERMINAL_FILE_ACTION" in names(prompt)


@pytest.mark.parametrize("prompt", [
    # one action prohibited, another explicitly requested — the request wins
    "Do not use the terminal, but create a file result.txt with 391",
    "Never run scripts. Create the file result.txt though.",
    "Create the file result.txt without using the terminal",
    "Не используй терминал, но создай файл result.txt",
    "Не используй терминал и создай файл result.txt",
    "Без терминала: создай файл result.txt",
    # a reminder is not a prohibition
    "Don't forget to create the file result.txt",
    "Не забудь создай файл result.txt",
])
def test_a_mixed_sentence_keeps_its_requested_action(prompt):
    assert names(prompt) == ["TERMINAL_FILE_ACTION"], positive_request_text(prompt)


def test_other_capabilities_respect_negation_too():
    assert names("Do not push to git, just describe the change") == []
    assert names("Не пуш, просто объясни") == []
    assert names("Закоммить и запушь изменения") == ["GITHUB_ACTION"]
    assert names("Запомни это: не пиши файлы") == ["MEMORY_ACTION"]


def test_negation_removal_is_span_local_not_sentence_wide():
    text = positive_request_text("Do not use the terminal, but create a file result.txt")
    assert "create a file result.txt" in text and "terminal" not in text


# ------------------------------------------ owner task 44: a document is a file

def test_a_new_text_document_is_a_file_obligation():
    assert names("Перечисли что ты умеешь делать на компе моём в новом текстовом документе") \
        == ["TERMINAL_FILE_ACTION"]
    assert names("List what you can do on my PC in a new text document") == ["TERMINAL_FILE_ACTION"]
    assert names("Перечисли что ты умеешь делать на компе") == []
