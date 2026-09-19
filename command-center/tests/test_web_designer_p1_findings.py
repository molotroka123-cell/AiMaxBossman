"""P1/P2-находки аудита Web Designer (audit-09-web-designer.md).

Все четыре молча УНИЧТОЖАЛИ содержимое сайта владельца: ошибки не было, версия
росла, а часть документа исчезала.

A9-01: правка всего документа отбрасывала всё после 120 000 символов.
A9-02: `_extract_html` резал документ по ПЕРВОМУ `</html>` — включая тот, что
       лежит строкой внутри `<script>`.
A9-03: замена элемента фрагментом с незакрытым тегом поглощала соседей.
A9-04: значение style с `;` внутри `url(...)` или кавычек резалось пополам.
"""
from __future__ import annotations

import pytest

from bcc import web_designer_dom as dom
from bcc.features.web_designer import AI_DOCUMENT_LIMIT, _extract_html

PAGE = '<html><body><p id="t">old</p><p>after</p></body></html>'


# ------------------------------------------------------------------ A9-02

def test_a_close_tag_inside_a_script_string_does_not_end_the_document():
    """Воспроизведение аудита: середина сайта терялась на литерале в скрипте."""
    raw = ('<!DOCTYPE html><html><head><script>var s="</html>";</script></head>'
           '<body><p>tail</p></body></html>')
    out = _extract_html(raw, False)
    assert out == raw
    assert "<p>tail</p>" in out


def test_json_ld_containing_a_close_tag_survives():
    raw = ('<!DOCTYPE html><html><head><script type="application/ld+json">'
           '{"x":"</html>"}</script></head><body><h1>kept</h1></body></html>')
    assert _extract_html(raw, False) == raw


def test_chatter_around_the_document_is_still_stripped():
    """Положительный контроль: жадный поиск не должен тащить болтовню модели."""
    doc = '<!DOCTYPE html><html><body><p>x</p></body></html>'
    assert _extract_html(f"Вот документ:\n{doc}\nГотово.", False) == doc
    assert _extract_html(f"```html\n{doc}\n```", False) == doc


def test_a_document_without_a_doctype_is_still_found():
    doc = '<html><body><p>x</p></body></html>'
    assert _extract_html(f"текст\n{doc}", False) == doc


def test_a_fragment_answer_is_returned_verbatim():
    assert _extract_html("```html\n<p>f</p>\n```", True) == "<p>f</p>"


# ------------------------------------------------------------------ A9-03

def test_a_fragment_with_an_unclosed_tag_is_refused():
    """Раньше `<p>after</p>` молча оказывался ВНУТРИ подставленного div."""
    with pytest.raises(ValueError, match="незакрытый тег <div>"):
        dom.apply_edit(PAGE, {"op": "replace", "path": "p", "html": '<div class="x">text'})


def test_a_nested_unclosed_tag_is_refused_too():
    with pytest.raises(ValueError, match="незакрытый тег <span>"):
        dom.apply_edit(PAGE, {"op": "replace", "path": "p",
                              "html": '<div><span>text</div>'})


def test_a_balanced_fragment_replaces_only_its_own_element():
    """Положительный контроль: сосед остаётся снаружи и на месте."""
    out, _ = dom.apply_edit(PAGE, {"op": "replace", "path": "p",
                                   "html": '<div class="x">text</div>'})
    assert out == '<html><body><div class="x">text</div><p>after</p></body></html>'


def test_void_elements_are_not_mistaken_for_unclosed_ones():
    """`<img>` и `<br>` закрывающего тега не имеют по определению."""
    out, _ = dom.apply_edit(PAGE, {"op": "replace", "path": "p",
                                   "html": '<img src="a.png"><br>'})
    assert out == '<html><body><img src="a.png"><br><p>after</p></body></html>'
    out, _ = dom.apply_edit(PAGE, {"op": "replace", "path": "p", "html": '<hr/>'})
    assert "<p>after</p>" in out


def test_a_stray_closing_tag_is_still_refused():
    """Прежняя проверка не потеряна."""
    with pytest.raises(ValueError, match="лишний закрывающий тег"):
        dom.apply_edit(PAGE, {"op": "replace", "path": "p", "html": 'text</div>'})


# ------------------------------------------------------------------ A9-04

@pytest.mark.parametrize("raw,expected", [
    ('background:url(data:image/png;base64,AAA) no-repeat;color:red',
     {"background": "url(data:image/png;base64,AAA) no-repeat", "color": "red"}),
    ('font-family:"a;b";color:red', {"font-family": '"a;b"', "color": "red"}),
    ("font-family:'x;y'", {"font-family": "'x;y'"}),
    ('background:url("a;b");margin:0', {"background": 'url("a;b")', "margin": "0"}),
    ('color:red;;margin:0', {"color": "red", "margin": "0"}),
    ('color: red', {"color": "red"}),
    ('', {}),
])
def test_a_semicolon_inside_a_value_no_longer_splits_the_declaration(raw, expected):
    assert dom._parse_style(raw) == expected


def test_editing_one_property_does_not_destroy_a_data_uri_next_to_it():
    """Воспроизведение аудита: правка font-size уничтожала фоновую картинку."""
    page = ('<html><body><p id="t" style="background:url(data:image/png;base64,AAA) '
            'no-repeat;color:red">x</p></body></html>')
    out, _ = dom.apply_edit(page, {"op": "style", "path": "p",
                                   "props": {"font-size": "20px"}})
    assert "url(data:image/png;base64,AAA) no-repeat" in out
    assert "font-size: 20px" in out and "color: red" in out


# ------------------------------------------------------------------ A9-01

def test_the_document_limit_is_a_refusal_boundary_not_a_silent_cut():
    """Предел обязан быть границей ОТКАЗА: усечение здесь означало бы, что
    ответ модели сохраняется как «полный документ» без выброшенного хвоста."""
    import inspect

    from bcc.features import web_designer
    source = inspect.getsource(web_designer)
    assert "html[:120000]" not in source, "документ снова режется молча"
    assert "status_code=413" in source
    assert AI_DOCUMENT_LIMIT == 120_000
