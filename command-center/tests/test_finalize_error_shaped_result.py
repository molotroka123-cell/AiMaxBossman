"""Task 1088 (02.11.2026): a tool-less local agent answered with a self-made error object and the run was completed."""
from bcc import finalize

ANSWER_1088 = '```json\n{\n  "error": {\n    "type": "llm_call_failed",\n    "message": "{\\"message\\":\\"Operation not allowed\\"}\\n"\n  }\n}\n```'  # verbatim task_runs.result of run 1092


def test_task_1088_answer_is_error_shaped():
    assert finalize._error_only_answer(ANSWER_1088)
    assert finalize._error_only_answer('{"error": "boom"}')


def test_real_answers_are_not_error_shaped():
    assert not finalize._error_only_answer("Отчёт: 5 идей, источники ниже.")
    assert not finalize._error_only_answer('{"error": null, "data": [1, 2]}')        # payload that merely has an error key
    assert not finalize._error_only_answer("Ошибка: {\"error\": 1} в логе, исправил так: ...")
    assert not finalize._error_only_answer("")
    assert not finalize._error_only_answer(None)
