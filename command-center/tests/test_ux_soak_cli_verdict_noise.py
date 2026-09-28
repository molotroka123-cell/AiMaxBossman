"""RC 1.9 soak (workstream F): one chat turn printed ten "⚠ проверка: NOT_APPLICABLE".

Reproduced in the owner's console (``bossman chat`` under a real pseudo console, stub
model): the engine emits one ``evaluation.completed`` per registered gate hook, and for
an ordinary answer every gate says NOT_APPLICABLE. The human view printed each of them
with the warning glyph, so every reply ended in a wall of identical warnings.

The machine stream (stream-json) keeps every record — that is the engine's truth. The
human view shows a verdict that says something (PASS/FAIL/...) once, and keeps
NOT_APPLICABLE for --verbose; the closing line still carries ``verifier: …``.
"""
from __future__ import annotations

import io

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc.terminal_cli.console import make_console  # noqa: E402
from bcc.terminal_cli.human import View  # noqa: E402


def _view(verbose: bool = False):
    buf = io.StringIO()
    return View(make_console(stream=buf, plain=True, width=120), plain=True, verbose=verbose), buf


def _eval(verdict: str, reasons: str = "", run_id: int = 6) -> dict:
    return {"type": "evaluation", "verdict": verdict, "reasons": reasons, "run_id": run_id}


def test_not_applicable_gates_do_not_flood_the_conversation():
    view, buf = _view()
    for _ in range(10):
        view.on_record(_eval("NOT_APPLICABLE"))
    assert buf.getvalue().count("проверка:") == 0
    # the verdict is still known for the closing result line
    assert view.state.verdict == "NOT_APPLICABLE"


def test_identical_meaningful_verdicts_are_shown_once():
    view, buf = _view()
    for _ in range(3):
        view.on_record(_eval("PASS"))
    view.on_record(_eval("FAIL", "тест не прошёл"))
    view.on_record(_eval("FAIL", "тест не прошёл"))
    out = buf.getvalue()
    assert out.count("проверка: PASS") == 1
    assert out.count("проверка: FAIL") == 1


def test_a_failing_gate_is_never_hidden_behind_not_applicable():
    view, buf = _view()
    view.on_record(_eval("NOT_APPLICABLE"))
    view.on_record(_eval("FAIL", "ревью не пройдено"))
    view.on_record(_eval("NOT_APPLICABLE"))
    out = buf.getvalue()
    assert "проверка: FAIL" in out and "ревью не пройдено" in out
    assert view.state.verdict == "FAIL"


def test_verbose_still_shows_not_applicable_once():
    view, buf = _view(verbose=True)
    for _ in range(10):
        view.on_record(_eval("NOT_APPLICABLE"))
    assert buf.getvalue().count("проверка: NOT_APPLICABLE") == 1
