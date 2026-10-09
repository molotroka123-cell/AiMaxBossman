"""UX sweep must not paint honest behaviour red (09.10 desktop sweep e6011ba8: 71 DEAD/ERROR, almost all false).

Covered: controls inside a closed <details>, native file chooser, native form validation,
a 503 the UI explains in a toast, background polling 5xx, and the counter-cases that must stay red.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "tree_proof"))
import ux_sweep as U  # noqa: E402

CTRL = {"kind": "button", "selected": False}


def state(**kw):
    s = {"url": "http://x/#/p", "dlg": [], "toasts": [], "ls": "", "vals": "", "html": "<b/>", "scroll": "0,0"}
    s.update(kw)
    return s


def run(ev=None, before=None, after=None, idle=frozenset()):
    events = U.Session._fresh_events()
    events.update(ev or {})
    sess = SimpleNamespace(ev=events, base="http://x")
    return U.classify(sess, CTRL, False, before or state(), after or state(), False, set(idle), "a", "a", 0)


def test_503_explained_by_a_toast_is_an_honest_refusal():
    r = run({"responses": [("POST", "/api/capability-tree/work", 503)],
             "console": ["ApiError: нет ключа OPENROUTER_API_KEY для исполнителя openrouter-free\n    at rawRequest"]},
            after=state(toasts=["нет ключа OPENROUTER_API_KEY для исполнителя openrouter-free\nКлюч не подключён"]))
    assert r["verdict"] == "BLOCKED_BY_DESIGN", r


def test_503_without_visible_explanation_stays_error():
    r = run({"responses": [("POST", "/api/x", 503)], "console": ["ApiError: сервис не отвечает"]})
    assert r["verdict"] == "ERROR"


def test_500_with_a_toast_stays_error():
    r = run({"responses": [("POST", "/api/x", 500)], "console": ["ApiError: boom"]}, after=state(toasts=["boom"]))
    assert r["verdict"] == "ERROR"


def test_toast_for_a_different_error_does_not_excuse_a_503():
    r = run({"responses": [("POST", "/api/x", 503)], "console": ["ApiError: другая ошибка"]},
            after=state(toasts=["сохранено"]))
    assert r["verdict"] == "ERROR"


def test_background_polling_5xx_is_not_blamed_on_the_click():
    r = run({"responses": [("GET", "/api/poker-vision/overlay.json", 503), ("POST", "/api/ok", 200)]},
            idle={("GET", "/api/poker-vision/overlay.json")})
    assert r["verdict"] == "OK" and "POST /api/ok -> 200" in r["detail"]


def test_file_chooser_is_an_effect():
    assert run({"filechoosers": ["multiple"]})["verdict"] == "OK"


def test_native_validation_bubble_is_a_refusal_not_dead():
    r = run({"native_validation": ["Заполните это поле."]})
    assert r["verdict"] == "BLOCKED_BY_DESIGN" and "native form validation" in r["detail"]


def test_nothing_at_all_is_still_dead():
    assert run()["verdict"] == "DEAD"


@pytest.mark.skipif(not os.path.exists(U.EDGE), reason="Microsoft Edge is not installed")
def test_enumerator_skips_closed_details_but_keeps_the_summary_and_invalid_js_sees_required_fields():
    from playwright.sync_api import sync_playwright
    html = """<div id="view">
      <details><summary>Ветка</summary><button>скрытый лист</button></details>
      <details open><summary>Открытая</summary><button>видимый лист</button></details>
      <form><input required><button type="submit" id="go">Применить</button></form>
      <form novalidate><input required><button id="nv">Без проверки</button></form></div>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=U.EDGE, headless=True)
        try:
            p = b.new_page()
            p.set_content(html)
            labels = [c["label"] for c in p.evaluate(U.ENUM_JS, "view")]
            assert "Ветка" in labels and "видимый лист" in labels and "скрытый лист" not in labels
            p.click("summary >> text=Ветка")
            assert "скрытый лист" in [c["label"] for c in p.evaluate(U.ENUM_JS, "view")]
            assert p.locator("#go").evaluate(U.INVALID_JS)                      # required field empty
            assert p.locator("#nv").evaluate(U.INVALID_JS) == []               # novalidate: no bubble
        finally:
            b.close()
