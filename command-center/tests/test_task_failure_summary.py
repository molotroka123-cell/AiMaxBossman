from bcc.task_failure_summary import failure_summary


def test_unverified_action_remains_visible_alongside_model_answer():
    result = failure_summary("action_contract/no_verified_action", "Я приступаю")
    assert "Я приступаю" in result
    assert "не подтвержден" in result.lower()
    assert "инструмент" in result.lower()
    assert "отклонён владельцем" not in result


def test_unverified_action_without_model_answer_is_explained():
    result = failure_summary("action_contract/no_verified_action", None)
    assert "не подтвержден" in result.lower()
    assert "инструмент" in result.lower()


def test_other_failures_preserve_existing_model_answer():
    assert failure_summary("unrelated error", "текст") == "текст"
    assert "unrelated error" in failure_summary("unrelated error", None)
