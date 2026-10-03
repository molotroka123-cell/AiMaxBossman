"""UI-sweep findings on d3385ec2 (installed Windows gate): one console error and one dead button.

* Rave "Запустить" with an empty task sent POST /api/rave (422) and logged a console error;
* Jeff settings preset "Обычный" changed nothing visible when the values already were stock.
Both are fixed in the page code; these checks keep the guards in place (the sweep itself runs in the Windows gate).
"""
from __future__ import annotations

from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui" / "pages"


def test_rave_start_refuses_an_empty_task_without_a_request():
    source = (UI / "rave.js").read_text(encoding="utf-8")
    start = source.index("btn('Запустить'")
    body = source[start:start + 900]
    guard = body.index("if (!prompt)")
    request = body.index("api.raw('/api/rave', { method: 'POST'")
    assert guard < request, "the empty-task guard must run before the request"
    assert "toast('Сначала введите задачу" in body and "return;" in body[guard:request]


def test_jeff_settings_presets_always_give_visible_feedback():
    source = (UI / "jeff_settings.js").read_text(encoding="utf-8")
    click = source.index("const p = data.presets[id];")
    body = source[click:click + 700]
    assert "aria-pressed" in body and "toast(`Пресет" in body
