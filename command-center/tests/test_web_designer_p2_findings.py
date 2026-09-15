"""P2/P3-находки аудита Web Designer (audit-09-web-designer.md), строки A9-05…A9-12.

A9-05: имя проекта уезжало в `<title>` blank-шаблона БЕЗ экранирования —
       живой `<script>` внутри СОХРАНЁННОГО сайта, который потом экспортируют
       без всякой песочницы.
A9-10: у элемента с собственным `data-bd-id` превью получало ДВА одинаковых
       атрибута; браузер брал первый, и клик выделял не тот элемент, который
       сервер потом найдёт по номеру.
A9-12: AI-правка ЭЛЕМЕНТА вставляла в сайт болтовню модели («Вот замена: …»)
       как текстовый узел страницы.
A9-09: template/palette дописывались в project.json ВТОРОЙ записью, уже вне
       блокировки проекта.
A9-08: сообщения пикера отличались только по окну-отправителю, а кадр превью
       волен увести СЕБЯ на чужую страницу — и та шлёт свой «выделен элемент».
"""
from __future__ import annotations

import json
import re

from bcc import web_designer_dom as dom

from .conftest import FakeAdapter

PAGE = '<html><body><h1 data-bd-id="mine">T</h1><p>x</p></body></html>'

XSS_NAME = 'Кафе"></title><script>alert(document.cookie)</script>'


# ------------------------------------------------------------------ A9-05

async def test_the_project_name_cannot_smuggle_a_script_into_a_blank_site(env):
    """Воспроизведение аудита: blank-путь не экранировал имя, gen-путь — да."""
    res = await env.client.post("/api/web-designer/projects",
                                json={"name": XSS_NAME, "template": "blank"})
    assert res.status_code == 200, res.text
    code = res.json()["code"]
    assert "<script>alert" not in code
    assert "&lt;script&gt;" in code
    # закрытие <title> тоже подделать нельзя — иначе скрипт оказался бы снаружи
    assert code.count("</title>") == 1


async def test_a_plain_name_is_still_readable_in_the_title(env):
    """Положительный контроль: обычное имя не превращается в мешанину."""
    res = await env.client.post("/api/web-designer/projects",
                                json={"name": "Кофейня Север", "template": "blank"})
    assert "<title>Кофейня Север</title>" in res.json()["code"]


async def test_the_generated_path_stays_escaped_too(env):
    """Прежняя защита gen-пути не потеряна."""
    res = await env.client.post("/api/web-designer/projects",
                                json={"name": XSS_NAME, "prompt": "", "template": "landing"})
    assert "<script>alert" not in res.json()["code"]


# ------------------------------------------------------------------ A9-10

def test_an_owners_own_bd_id_does_not_get_a_second_one_in_the_preview():
    """Дубль атрибута: браузер брал ПЕРВЫЙ, сервер искал по ВТОРОМУ."""
    out = dom.inject_preview(PAGE, script="/*picker*/")
    h1 = re.search(r"<h1[^>]*>", out).group(0)
    assert h1.count("data-bd-id") == 1
    assert 'data-bd-id="mine"' in h1


def test_elements_without_their_own_marker_are_still_numbered():
    """Положительный контроль: пикер обязан продолжать работать."""
    out = dom.inject_preview(PAGE, script="/*picker*/")
    assert re.search(r'<p[^>]*data-bd-id="bd-4"', out)
    assert 'data-bd-id="bd-1"' in out            # <html> пронумерован как прежде


def test_the_element_with_its_own_marker_is_still_reachable_by_path():
    """Выход есть: пикер шлёт и path, и по нему правка доходит."""
    out, desc = dom.apply_edit(PAGE, {"op": "style", "path": "html > body > h1",
                                      "props": {"color": "red"}})
    assert desc["tag"] == "h1" and "color: red" in out
    assert 'data-bd-id="mine"' in out            # чужой атрибут не тронут


# ------------------------------------------------------------------ A9-12

async def _project_with_model(env, html: str, answer: str):
    """Проект с кодом + подставной моделью в реестре, отвечающей `answer`."""
    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    await env.client.put(f"/api/web-designer/projects/{pid}/code", json={"html": html})
    provider = (await env.client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
    await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(answer)
    return pid


BUTTON_PAGE = '<html><body><button id="b">старая</button></body></html>'


async def test_model_chatter_around_a_fragment_never_becomes_page_content(env):
    """Воспроизведение аудита: прелюдия и постлюдия уезжали в сайт текстом."""
    pid = await _project_with_model(
        env, BUTTON_PAGE, "Вот обновлённая кнопка:\n<button>ОК</button>\nГотово!")
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "сделай заметнее", "path": "button"})
    assert res.status_code == 422, res.text
    assert "фрагмент" in res.json()["error"]["message"].lower()
    code = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    assert code == BUTTON_PAGE, "негодный ответ модели не должен сохраняться"


async def test_a_clean_fragment_from_the_model_is_applied(env):
    """Положительный контроль: нормальный ответ модели по-прежнему проходит."""
    pid = await _project_with_model(env, BUTTON_PAGE,
                                    '```html\n<button class="big">ОК</button>\n```')
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "сделай заметнее", "path": "button"})
    assert res.status_code == 200, res.text
    code = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()["code"]
    assert code == '<html><body><button class="big">ОК</button></body></html>'


async def test_a_whole_document_answer_is_not_held_to_the_single_root_rule(env):
    """Правка ВСЕГО документа — другой путь: там корней законно несколько."""
    pid = await _project_with_model(
        env, BUTTON_PAGE,
        "<!DOCTYPE html><html><body><button>ОК</button></body></html>")
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "перевёрстывай"})
    assert res.status_code == 200, res.text


# ------------------------------------------------------------------ A9-09

async def test_generation_writes_the_project_file_once_under_the_lock(env, monkeypatch):
    """template/palette дописывались ВТОРОЙ записью meta уже вне блокировки:
    цикл «прочитал — изменил — записал» жил там, где чужая правка не ждёт."""
    from bcc.features import web_designer

    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    pdir = web_designer._pdir(env.svc, pid)

    writes: list[bool] = []
    real_save_meta = web_designer._save_meta

    def counting_save_meta(target, meta):
        if target == pdir:
            writes.append(web_designer._project_lock(pdir).locked())
        real_save_meta(target, meta)

    monkeypatch.setattr(web_designer, "_save_meta", counting_save_meta)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/generate",
                                json={"prompt": "кафе с доставкой"})
    assert res.status_code == 200, res.text
    assert writes == [True], "meta проекта пишется один раз и только под блокировкой"

    # Положительный контроль: поля всё так же попадают и в ответ, и на диск.
    stored = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    assert res.json()["meta"]["template"] == stored["template"] == res.json()["template"]
    assert res.json()["meta"]["palette"] == stored["palette"] == res.json()["palette"]
    assert stored["version"] == res.json()["meta"]["version"] == 2


async def test_generation_keeps_the_history_of_the_version_it_wrote(env):
    """Положительный контроль: снимок и список версий не пострадали."""
    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    await env.client.post(f"/api/web-designer/projects/{pid}/generate",
                          json={"prompt": "портфолио фотографа"})
    items = (await env.client.get(f"/api/web-designer/projects/{pid}/versions")).json()["items"]
    assert [v["version"] for v in items] == [1, 2]
    restored = await env.client.post(f"/api/web-designer/projects/{pid}/versions/2/restore")
    assert restored.status_code == 200


# ------------------------------------------------------------------ A9-08

async def test_the_preview_picker_carries_the_one_time_pass_of_this_load(env):
    """Кадр с sandbox=allow-scripts может увести СЕБЯ на чужую страницу, и та
    шлёт панели свой 'select' от имени пикера (воспроизведено в Chromium).
    Отличить свой пикер можно только по пропуску, которого у чужой страницы нет."""
    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    res = await env.client.get(f"/api/web-designer/projects/{pid}/preview?nonce=abc123XY")
    assert res.status_code == 200
    assert "var NONCE = 'abc123XY'" in res.text
    assert "__BD_NONCE__" not in res.text
    assert "nonce: NONCE" in res.text


async def test_a_nonce_that_would_break_out_of_the_js_string_is_refused(env):
    """Пропуск уезжает внутрь строкового литерала: кавычка стала бы кодом."""
    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    bad = await env.client.get(
        f"/api/web-designer/projects/{pid}/preview?nonce=x';alert(1);//")
    assert bad.status_code == 422
    assert "nonce" in bad.json()["error"]["message"]


async def test_a_preview_without_a_nonce_still_renders(env):
    """Положительный контроль: превью в отдельной вкладке открывается как прежде."""
    created = await env.client.post("/api/web-designer/projects",
                                    json={"name": "сайт", "template": "blank"})
    pid = created.json()["meta"]["id"]
    res = await env.client.get(f"/api/web-designer/projects/{pid}/preview")
    assert res.status_code == 200
    assert "var NONCE = ''" in res.text and "__BD_NONCE__" not in res.text
