"""Тесты веб-дизайнера: генерация, точечные правки, версии, превью с пикером.

Сеть не трогается: AI-правка проверяется только в честном отказе «нет модели»
(реестр в тестах пуст) — сами операции правки детерминированы и покрыты ниже.
"""
from __future__ import annotations

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
