"""Состязательный набор по веб-дизайнеру: правка не должна портить чужой код.

Проверяются не «фичи», а инварианты, нарушение которых означает необратимую
порчу сайта владельца или выход за границы панели:

* точечная правка минимальна и дословна — документ без правки не меняется НИ
  НА БАЙТ, а с правкой меняется ровно один тег;
* терпимая к ошибкам разметка не «чинится» молча в другую семантику;
* устаревшая версия и одновременная правка не затирают чужую работу молча;
* превью остаётся отрезанным от панели, а сообщение от чужого окна панель не
  принимает;
* имя проекта — ввод владельца — не становится живой разметкой в сохранённом
  сайте (экспорт уезжает за пределы песочницы превью);
* файлы проекта не читаются и не пишутся по символической ссылке наружу;
* приватный режим не уходит в облачную модель, а неизвестная цена не
  становится бесплатной.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bcc import web_designer_dom as dom
from bcc.features import web_designer as wd
from bcc.web_designer_dom import apply_edit, inject_preview, parse_document, serialize

from .conftest import FakeAdapter

# ---------------------------------------------------------------- корпус

# Каждый кусок — конструкция, которую html.parser либо теряет, либо
# нормализует, если сборка честно не хранит дословный исходник.
HOSTILE = {
    "doctype": '<!DOCTYPE html>\n<html><body><p>x</p></body></html>',
    "doctype_legacy": '<!DOCTYPE html SYSTEM "about:legacy-compat">\n<p>x</p>',
    "comment_with_dashes": '<!-- keep -- me --><p>x</p>',
    "conditional_comment": '<!--[if IE]><p>ie</p><![endif]--><p>x</p>',
    "script_with_markup": '<script>var a = "</div>"; if (a<b && c>d) {}</script>',
    "json_ld": '<script type="application/ld+json">{"@context":"https://schema.org","n":"A & B <x>"}</script>',
    "inline_svg": '<svg viewBox="0 0 10 10"><linearGradient id="g"/><clipPath id="c"/></svg>',
    "mathml": '<math><mspace width="1"/><mi>x</mi></math>',
    "template": '<template id="t"><tr><td>x</td></tr></template>',
    "unicode": '<p>Привет \U0001f600 日本語 ​</p>',
    "entities": '<p>&amp; &lt; &#x27; &nbsp; &copy; &#169;</p>',
    "processing_instruction": '<?php echo 1; ?><p>x</p>',
    "cdata": '<div><![CDATA[a<b]]></div>',
    "truncated_tail": '<div class="x"',
    "unquoted_attrs": '<div id=hero class=a>x</div>',
    "single_quoted_attrs": "<div id='hero'>x</div>",
    "uppercase_tags": '<DIV CLASS="a">x</DIV >',
    "boolean_attr": '<input disabled type=text>',
    "pre_whitespace": '<pre>  a\n   b  </pre>',
    "textarea_literal": '<textarea><p>not a tag</p></textarea>',
    "style_with_markup": '<style>a::before{content:"</style>"}</style>',
}

# Разметка, которую браузер терпит, но «чинить» её нельзя: исправленная
# структура — это ДРУГОЙ документ, а владелец просил поменять цвет.
MALFORMED = {
    "crossed_tags": '<b><i>x</b></i>',
    "implicit_li_close": '<ul><li>a<li>b</ul>',
    "stray_close": '<p>x</p></div>',
    "unclosed_table": '<table><tr><td>x',
    "unclosed_paragraphs": '<p>one<p>two',
    "unclosed_at_eof": '<main><span>hello</main>',
}

FULL = (
    '<!DOCTYPE html>\n'
    '<!-- owner comment -- with dashes -->\n'
    '<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
    '<title>Тест &amp; Проверка</title>\n'
    '<script type="application/ld+json">{"@type":"Org","n":"A & B <x>"}</script>\n'
    '<style>h1{color:#333}</style>\n</head>\n<body>\n'
    '<h1 id=hero>Заголовок \U0001f600</h1>\n'
    '<svg viewBox="0 0 24 24" preserveAspectRatio="xMidYMid">'
    '<linearGradient id="g"/><clipPath id="c"><path d="M0 0h1v1z"/></clipPath></svg>\n'
    '<template id="row"><tr><td>x</td></tr></template>\n'
    '<?php echo "hi"; ?>\n'
    '<div><![CDATA[raw<stuff]]></div>\n'
    "<p class='q'>&nbsp;&copy; 2026 &#169;</p>\n"
    '<script>if (a<b && c>d) { document.write("</p>") }</script>\n'
    '<textarea><b>literal</b></textarea>\n'
    '</body>\n</html>\n'
)


# ---------------------------------------------------------------- дословность

@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_roundtrip_without_editing_changes_no_byte(name):
    """Разбор → сборка без правки обязаны вернуть исходник байт в байт."""
    source = HOSTILE[name]
    assert serialize(parse_document(source)) == source


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_tolerated_malformed_markup_is_not_repaired(name):
    """Браузер терпит эту разметку; панель не имеет права её «починить».

    Выдуманный `<tbody>`, дописанный `</p>` или выброшенный лишний закрывающий
    тег — это другая семантика, а владелец просил поменять цвет.
    """
    source = MALFORMED[name]
    assert serialize(parse_document(source)) == source
    edited, _ = apply_edit(f"<html><body>{source}</body></html>",
                           {"op": "style", "path": "body", "props": {"color": "red"}})
    assert source in edited
    assert "<tbody" not in edited.lower()


def test_single_edit_rewrites_only_the_edited_tag():
    """Правка одного элемента не переписывает документ вокруг него."""
    edited, described = apply_edit(FULL, {"op": "style", "path": "h1", "props": {"color": "red"}})
    assert described["tag"] == "h1"
    before, after = FULL.split("<h1 id=hero>", 1)
    assert edited.startswith(before)
    assert edited.endswith(after.split("</h1>", 1)[1])
    # и всё, что легко потерять, осталось дословным
    for fragment in ('<!DOCTYPE html>', '<!-- owner comment -- with dashes -->',
                     '{"@type":"Org","n":"A & B <x>"}', 'viewBox="0 0 24 24"',
                     'preserveAspectRatio="xMidYMid"', '<linearGradient id="g"/>',
                     '<template id="row"><tr><td>x</td></tr></template>',
                     '<?php echo "hi"; ?>', '<![CDATA[raw<stuff]]>',
                     '&nbsp;&copy; 2026 &#169;', "class='q'",
                     'document.write("</p>")', '<textarea><b>literal</b></textarea>',
                     '\U0001f600'):
        assert fragment in edited, fragment


@pytest.mark.parametrize("edit", [
    {"op": "style", "path": "h1", "props": {"color": "red"}},
    {"op": "attrs", "path": "p", "attrs": {"data-x": "1"}},
    {"op": "text", "path": "title", "text": "новое"},
])
def test_edit_changes_exactly_one_line(edit):
    """Диff правки — ровно одна строка: всё остальное отдано дословно."""
    edited, _ = apply_edit(FULL, edit)
    src_lines, out_lines = FULL.splitlines(), edited.splitlines()
    assert len(src_lines) == len(out_lines)
    assert sum(1 for a, b in zip(src_lines, out_lines) if a != b) == 1


def test_preview_injection_leaves_the_stored_document_untouched():
    """Нумерация и пикер живут только в отданном превью."""
    injected = inject_preview(FULL)
    assert 'data-bd-id="bd-1"' in injected and "__bdPicker" in injected
    assert "data-bd-id" not in FULL
    # исходные конструкции пережили инжект
    for fragment in ('<?php echo "hi"; ?>', '<![CDATA[raw<stuff]]>',
                     'viewBox="0 0 24 24"', '<!DOCTYPE html>'):
        assert fragment in injected, fragment
    # и повторная правка исходника по-прежнему не тащит номера в хранимый код
    edited, _ = apply_edit(FULL, {"op": "style", "path": "h1", "props": {"color": "red"}})
    assert "data-bd-id" not in edited


def test_attribute_name_and_style_property_injection_stay_refused():
    """Вторая атака на тот же инвариант: имя атрибута — не значение."""
    src = '<html><body><h1>T</h1></body></html>'
    for hostile in ('x" onmouseover="alert(1)', 'x><script>alert(1)</script',
                    "x' onfocus='alert(1)", "x=y", "x/y", "x y", "x\nonload"):
        with pytest.raises(ValueError):
            apply_edit(src, {"op": "attrs", "path": "h1", "attrs": {hostile: "y"}})
    for hostile in ('color:red;" onload="alert(1)', 'color}a{x', 'color:red;}'):
        with pytest.raises(ValueError):
            apply_edit(src, {"op": "style", "path": "h1", "props": {hostile: "y"}})
    # значение уезжает экранированным, а не живым обработчиком
    out, _ = apply_edit(src, {"op": "style", "path": "h1",
                              "props": {"color": 'red" onmouseover="alert(1)'}})
    assert 'onmouseover="alert(1)"' not in out and "&quot;" in out
    assert serialize(parse_document(out)) == out


def test_replacement_that_would_swallow_the_document_is_refused():
    """Фрагмент, закрывающий чужую обёртку (в т.ч. служебную), — отказ."""
    src = '<html><body><div id="wrap"><span id="t">x</span><p>after</p></div></body></html>'
    for hostile in ("<b>NEW</b></div><p>LOST</p>",
                    "<b>NEW</b></bd-fragment-root><p>LOST</p>"):
        with pytest.raises(ValueError):
            apply_edit(src, {"op": "replace", "path": "span#t", "html": hostile})
    assert serialize(parse_document(src)) == src


# ---------------------------------------------------------------- имя проекта

async def _project(env, **body):
    body.setdefault("name", "проект")
    res = await env.client.post("/api/web-designer/projects", json=body)
    assert res.status_code == 200, res.text
    return res.json()


HOSTILE_NAME = 'Кафе</title><script>alert(1)</script>'


@pytest.mark.parametrize("template", ["blank", "auto"])
async def test_project_name_never_becomes_live_markup(env, template):
    """P1. Имя проекта уезжает В РАЗМЕТКУ сохранённого сайта.

    Генератор своё имя экранировал, пустой шаблон — нет: `</title><script>`
    закрывал заголовок и оставлял живой скрипт в коде проекта. Превью в
    песочнице это скрывает, а экспортированный сайт открывают без неё.
    """
    data = await _project(env, name=HOSTILE_NAME, template=template)
    code = data["code"]
    assert "<script>alert(1)</script>" not in code
    assert "&lt;script&gt;" in code or "&lt;/title&gt;" in code
    root = parse_document(code)
    assert not [n for n in dom.walk_elements(root)
                if n.is_element("script") and "alert(1)" in "".join(
                    c.raw for c in n.children if c.kind == "text")]


async def test_hostile_name_survives_a_later_edit_without_waking_up(env):
    """Вторая атака: экранированное имя не «оживает» после точечной правки."""
    data = await _project(env, name=HOSTILE_NAME, template="blank")
    pid = data["meta"]["id"]
    res = await env.client.post(f"/api/web-designer/projects/{pid}/edit",
                                json={"op": "attrs", "path": "title",
                                      "attrs": {"data-x": "1"},
                                      "base_version": data["meta"]["version"]})
    assert res.status_code == 200, res.text
    code = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    assert "<script>alert(1)</script>" not in code


# ---------------------------------------------------------------- версии и гонки

async def test_two_edits_on_the_same_version_cannot_both_land(env):
    """Одновременная правка: вторая обязана получить 409, а не затереть первую."""
    data = await _project(env, template="blank")
    pid, base = data["meta"]["id"], data["meta"]["version"]
    first = await env.client.post(f"/api/web-designer/projects/{pid}/edit",
                                  json={"op": "attrs", "path": "title",
                                        "attrs": {"data-a": "1"}, "base_version": base})
    assert first.status_code == 200, first.text
    second = await env.client.post(f"/api/web-designer/projects/{pid}/edit",
                                   json={"op": "attrs", "path": "title",
                                         "attrs": {"data-b": "2"}, "base_version": base})
    assert second.status_code == 409, second.text
    code = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    assert 'data-a="1"' in code and 'data-b' not in code


async def test_stale_code_write_is_refused_and_nothing_is_lost(env):
    data = await _project(env, template="blank")
    pid, base = data["meta"]["id"], data["meta"]["version"]
    mine = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                json={"html": "<html><body><p>МОЁ</p></body></html>",
                                      "base_version": base})
    assert mine.status_code == 200, mine.text
    late = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                json={"html": "<html><body><p>ПОЗДНЯЯ</p></body></html>",
                                      "base_version": base})
    assert late.status_code == 409, late.text
    assert "МОЁ" in (await env.client.get(
        f"/api/web-designer/projects/{pid}")).json()["code"]


async def test_model_answer_arriving_late_does_not_clobber_the_owner(env):
    """Ответ модели приходит через секунды. За это время владелец правил код."""
    data = await _project(env, template="blank")
    pid, base = data["meta"]["id"], data["meta"]["version"]

    async def steal(_calls, _messages):
        res = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                   json={"html": "<html><body><p>РУЧНАЯ</p></body></html>",
                                         "base_version": base})
        assert res.status_code == 200, res.text

    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "<html><body><p>МОДЕЛЬ</p></body></html>", on_chat=steal)
    provider = (await env.client.post("/api/providers", json={
        "name": "local", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1", "api_key": "k"})).json()
    await env.client.post("/api/models", json={"provider_id": provider["id"],
                                               "name": "m", "alias": "m"})
    late = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                 json={"prompt": "поменяй"})
    assert late.status_code == 409, late.text
    code = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    assert "РУЧНАЯ" in code and "МОДЕЛЬ" not in code


async def test_every_write_keeps_a_snapshot_so_nothing_is_unrecoverable(env):
    """Даже запись без сверки версии (откат, генерация) не теряет предыдущий код."""
    data = await _project(env, template="blank")
    pid = data["meta"]["id"]
    await env.client.put(f"/api/web-designer/projects/{pid}/code",
                         json={"html": "<html><body><p>A</p></body></html>",
                               "base_version": data["meta"]["version"]})
    versions = (await env.client.get(f"/api/web-designer/projects/{pid}/versions")).json()["items"]
    mine = versions[-1]["version"]
    await env.client.post(f"/api/web-designer/projects/{pid}/generate",
                          json={"prompt": "кафе"})
    restored = await env.client.post(
        f"/api/web-designer/projects/{pid}/versions/{mine}/restore")
    assert restored.status_code == 200, restored.text
    assert "<p>A</p>" in restored.json()["code"]


# ---------------------------------------------------------------- границы файлов

@pytest.mark.parametrize("pid", ["../../../etc", "1/../../..", "..%2f..", "%2e%2e", "0x1"])
async def test_project_id_cannot_leave_the_project_directory(env, pid):
    res = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert res.status_code in (404, 422), (pid, res.status_code, res.text)


async def test_writes_never_follow_a_symlink_out_of_the_project(env, tmp_path):
    """Подменённый `current.html` не должен превратиться в запись в чужой файл."""
    data = await _project(env, template="blank")
    pdir = wd._pdir(env.svc, int(data["meta"]["id"]))
    outside = tmp_path / "outside.txt"
    outside.write_text("ЧУЖОЕ", encoding="utf-8")
    current = wd._current_path(pdir)
    current.unlink()
    os.symlink(outside, current)

    res = await env.client.put(f"/api/web-designer/projects/{data['meta']['id']}/code",
                               json={"html": "<html><body><p>x</p></body></html>",
                                     "base_version": data["meta"]["version"]})
    # чтение по ссылке отклонено, чужой файл не тронут ни при каком исходе
    assert res.status_code in (200, 409), res.text
    assert outside.read_text(encoding="utf-8") == "ЧУЖОЕ"
    assert not current.is_symlink()


async def test_project_code_is_never_read_through_a_symlink(env, tmp_path):
    """P2. Чтение по ссылке отдавало владельцу содержимое ЧУЖОГО файла —
    и оно уезжало в редактор, а оттуда в следующую сохранённую версию."""
    data = await _project(env, template="blank")
    pid = data["meta"]["id"]
    pdir = wd._pdir(env.svc, int(pid))
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    current = wd._current_path(pdir)
    current.unlink()
    os.symlink(secret, current)

    res = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert res.status_code == 409, res.text
    assert "TOP SECRET" not in res.text

    # то же самое для снимка истории: откат не импортирует чужой файл
    snapshot = wd._version_path(pdir, 1)
    snapshot.unlink(missing_ok=True)
    os.symlink(secret, snapshot)
    restored = await env.client.post(f"/api/web-designer/projects/{pid}/versions/1/restore")
    assert restored.status_code == 409, restored.text
    assert "TOP SECRET" not in restored.text


# ---------------------------------------------------------------- песочница превью

async def test_preview_response_is_sandboxed_by_the_server_itself(env):
    """Защита не зависит от того, что клиент не забыл атрибут sandbox."""
    data = await _project(env, template="blank")
    res = await env.client.get(f"/api/web-designer/projects/{data['meta']['id']}/preview")
    assert res.status_code == 200, res.text
    csp = res.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp        # иначе кадр получил бы origin панели
    assert "form-action 'none'" in csp
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("cache-control") == "no-store"


def test_panel_accepts_preview_messages_only_from_the_preview_frame():
    """postMessage в родителя: поле `source` в теле — данные, а не удостоверение."""
    page = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "web_designer.js").read_text(
        encoding="utf-8")
    assert "ev.source !== frame.contentWindow" in page
    # атрибут песочницы без allow-same-origin
    assert "sandbox: 'allow-scripts'" in page
    # allow-same-origin встречается только в комментарии, объясняющем его отсутствие
    assert "'allow-scripts allow-same-origin'" not in page
    assert 'allow-scripts allow-same-origin' not in page


def test_picker_script_never_reaches_for_panel_credentials():
    """Скрипт пикера общается только через postMessage: ни cookie, ни fetch."""
    for forbidden in ("document.cookie", "localStorage", "fetch(", "XMLHttpRequest",
                      "top.location", "window.open"):
        assert forbidden not in dom.PICKER_JS, forbidden


# ---------------------------------------------------------------- модель и деньги

def _record(sink):
    async def on_chat(_calls, _messages):
        sink.append(1)
    return on_chat


async def _cloud_model(env, **values):
    provider = (await env.client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_compat",
        "base_url": "https://api.openai.com/v1", "api_key": "sk-x"})).json()
    body = {"provider_id": provider["id"], "name": "gpt-x", "alias": "gpt-x"}
    body.update(values)
    return (await env.client.post("/api/models", json=body)).json()


async def test_unknown_cloud_price_is_never_treated_as_free(env):
    """Модель без тарифа не запускается: неизвестная цена — не ноль."""
    called = []
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "<html><body><p>x</p></body></html>",
        on_chat=_record(called))
    model = await _cloud_model(env)
    assert not model.get("pricing_known")
    data = await _project(env, template="blank")
    pid = data["meta"]["id"]
    before = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "синим"})
    assert res.status_code == 502, res.text
    assert "pricing" in res.text
    assert called == []
    assert (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"] == before


async def test_private_execution_never_falls_back_to_a_cloud_model(env):
    """PRIVATE/LOCAL_ONLY: облачный провайдер — отказ, а не «ну ладно»."""
    from bossman_shared.privacy import execution_privacy

    called = []
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "<html><body><p>PWNED</p></body></html>",
        on_chat=_record(called))
    await _cloud_model(env, price_in=1.0, price_out=2.0)
    data = await _project(env, template="blank")
    pid = data["meta"]["id"]
    before = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]

    for level in ("private", "local_only"):
        with execution_privacy(level):
            res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                        json={"prompt": "синим"})
        assert res.status_code == 403, (level, res.status_code, res.text)
        assert called == [], level
        assert (await env.client.get(
            f"/api/web-designer/projects/{pid}")).json()["code"] == before

    # вне приватного режима та же модель работает — отказ адресный, не «всё сломано»
    ok = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                               json={"prompt": "синим"})
    assert ok.status_code == 200, ok.text
