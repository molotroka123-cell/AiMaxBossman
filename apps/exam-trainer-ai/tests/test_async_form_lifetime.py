from pathlib import Path


def test_question_submit_keeps_form_reference_after_fetch():
    source = (Path(__file__).parents[1] / "src" / "exam_trainer_ai" / "ui.py").read_text(encoding="utf-8")
    handler = next(line for line in source.splitlines() if "$('#add-question').addEventListener('submit'" in line)
    first_await = handler.find("await ")

    assert "const formElement=event.currentTarget" in handler
    assert 0 <= handler.find("const formElement=event.currentTarget") < first_await
    assert "formElement.reset()" in handler
    assert "formElement.elements.prompt.focus()" in handler
    assert "event.currentTarget.elements" not in handler


def test_practice_report_keeps_its_readiness_caveat_in_russian():
    source = (Path(__file__).parents[1] / "src" / "exam_trainer_ai" / "ui.py").read_text(encoding="utf-8")

    assert "Оценка учитывает вес тем в вашем плане; это ориентир, а не гарантия результата экзамена." in source
    assert "caveat.textContent=report.readiness.basis" not in source
