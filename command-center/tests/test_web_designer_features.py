"""Тесты возможностей веб-дизайнера (Epoch 4): страницы, компоненты, токены,
ассеты, отзывчивость, линт и экспорт.

Каждый тест проверяет ПОСЛЕДСТВИЕ, а не возврат функции: файл на диске,
перечитанный документ или отданный ответ. «Функция вернула ok» ничего не
говорит владельцу, чей сайт лежит файлами в каталоге проекта.

Сети здесь нет вовсе: все семь возможностей детерминированы и ни одна не
ходит к модели — AI-путь остаётся один (`/ai-edit`) и живёт в соседнем наборе.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

from bcc.features import web_designer as wd

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


async def _project(env, **kw):
    """Пустой проект: содержимое страниц ставят сами тесты."""
    body = {"name": "Сайт", "prompt": "", "template": "blank", **kw}
    res = await env.client.post("/api/web-designer/projects", json=body)
    assert res.status_code == 200, res.text
    return int(res.json()["meta"]["id"])


async def _put(env, pid, html, page="index"):
    res = await env.client.put(f"/api/web-designer/projects/{pid}/code",
                               json={"html": html, "page": page})
    assert res.status_code == 200, res.text
    return res.json()["meta"]


async def _code(env, pid, page="index"):
    res = await env.client.get(f"/api/web-designer/projects/{pid}?page={page}")
    assert res.status_code == 200, res.text
    return res.json()["code"]


async def _version(env, pid) -> int:
    res = await env.client.get(f"/api/web-designer/projects/{pid}")
    return int(res.json()["meta"]["version"])


# ================================================================
# 1. Страницы
# ================================================================

async def test_page_is_created_as_a_real_file_and_listed(env):
    pid = await _project(env)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                                json={"slug": "contacts", "title": "Контакты"})
    assert res.status_code == 200, res.text

    pdir = wd._pdir(env.svc, pid)
    assert (pdir / "pages" / "contacts.html").is_file(), "страница не легла файлом на диск"
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/pages")).json()["items"]
    assert [p["slug"] for p in listed] == ["index", "contacts"]
    assert "Контакты" in await _code(env, pid, "contacts")


async def test_page_slugs_that_would_leave_the_project_are_refused(env):
    """Слаг уезжает в ИМЯ ФАЙЛА и в ссылку экспорта, значит проверяется до
    склейки путей: `../../project.json` — это чужой файл, а не страница."""
    pid = await _project(env)
    pdir = wd._pdir(env.svc, pid)
    before = (pdir / "project.json").read_text(encoding="utf-8")
    for hostile in ("../../project", "../evil", "pages/../../x", "history", "assets",
                    "export", "с-кириллицей", "-leading", "a" * 49):
        res = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                                    json={"slug": hostile})
        assert res.status_code == 422, f"{hostile}: {res.status_code}"
    assert (pdir / "project.json").read_text(encoding="utf-8") == before
    assert not list(pdir.parent.glob("evil*"))
    # ни одной страницы так и не завелось
    assert [p["slug"] for p in
            (await env.client.get(f"/api/web-designer/projects/{pid}/pages")).json()["items"]] \
        == ["index"]
    # регистр — не враждебность, а описка: слаг приводится к нижнему регистру
    ok = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                               json={"slug": "Contacts"})
    assert ok.status_code == 200 and ok.json()["page"]["slug"] == "contacts"
    assert (pdir / "pages" / "contacts.html").is_file()


async def test_duplicate_slug_and_page_limit_are_refused(env):
    pid = await _project(env)
    first = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                                  json={"slug": "about"})
    assert first.status_code == 200
    again = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                                  json={"slug": "about"})
    assert again.status_code == 409

    pdir = wd._pdir(env.svc, pid)
    meta = wd._load_meta(pdir)
    meta["pages"] = [{"slug": f"p{i}", "title": f"p{i}"} for i in range(wd.MAX_PAGES)]
    wd._save_meta(pdir, meta)
    over = await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                                 json={"slug": "one-more"})
    assert over.status_code == 409 and "предел" in over.json()["error"]["message"]
    assert not (pdir / "pages" / "one-more.html").exists()


async def test_rename_moves_the_file_and_keeps_the_code(env):
    pid = await _project(env)
    await env.client.post(f"/api/web-designer/projects/{pid}/pages", json={"slug": "old"})
    await _put(env, pid, "<html><body><p>содержимое страницы</p></body></html>", page="old")

    res = await env.client.post(f"/api/web-designer/projects/{pid}/pages/old/rename",
                                json={"slug": "new", "title": "Новая"})
    assert res.status_code == 200, res.text
    pdir = wd._pdir(env.svc, pid)
    assert not (pdir / "pages" / "old.html").exists()
    assert (pdir / "pages" / "new.html").is_file()
    assert "содержимое страницы" in await _code(env, pid, "new")
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/pages")).json()["items"]
    assert {p["slug"]: p["title"] for p in listed}["new"] == "Новая"


async def test_home_page_keeps_its_slug_and_cannot_be_deleted(env):
    """Домашняя — точка входа сайта: без index.html экспорт открывается пустым
    каталогом, а все ссылки навигации ведут в никуда."""
    pid = await _project(env)
    renamed = await env.client.post(f"/api/web-designer/projects/{pid}/pages/index/rename",
                                    json={"slug": "start"})
    assert renamed.status_code == 409
    deleted = await env.client.delete(f"/api/web-designer/projects/{pid}/pages/index")
    assert deleted.status_code == 409
    pdir = wd._pdir(env.svc, pid)
    assert (pdir / "current.html").is_file()
    assert [p["slug"] for p in
            (await env.client.get(f"/api/web-designer/projects/{pid}/pages")).json()["items"]] \
        == ["index"]
    # заголовок домашней при этом менять можно
    titled = await env.client.post(f"/api/web-designer/projects/{pid}/pages/index/rename",
                                   json={"title": "Главная страница"})
    assert titled.status_code == 200, titled.text
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/pages")).json()["items"]
    assert listed[0]["title"] == "Главная страница"


async def test_duplicate_copies_the_code_and_delete_removes_the_file(env):
    pid = await _project(env)
    await _put(env, pid, "<html><body><h1>Оригинал</h1></body></html>")
    copy = await env.client.post(f"/api/web-designer/projects/{pid}/pages/index/duplicate",
                                 json={"slug": "copy", "title": "Копия"})
    assert copy.status_code == 200, copy.text
    assert "Оригинал" in await _code(env, pid, "copy")

    # копия независима: правка копии не трогает оригинал
    await _put(env, pid, "<html><body><h1>Изменено</h1></body></html>", page="copy")
    assert "Оригинал" in await _code(env, pid, "index")

    pdir = wd._pdir(env.svc, pid)
    assert (pdir / "pages" / "copy.html").is_file()
    gone = await env.client.delete(f"/api/web-designer/projects/{pid}/pages/copy")
    assert gone.status_code == 200
    assert not (pdir / "pages" / "copy.html").exists()
    assert (await env.client.get(f"/api/web-designer/projects/{pid}?page=copy")).status_code == 404


# ================================================================
# 2. Компоненты
# ================================================================

CARD = ('<html><body><main><div class="card"><h3 data-bd-slot="title">Тариф</h3>'
        '<p data-bd-slot="text">описание</p></div></main></body></html>')


async def _define_card(env, pid, name="card"):
    """Определение компонента снимается с ЖИВОГО элемента страницы."""
    await _put(env, pid, CARD)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/components", json={
        "name": name, "title": "Карточка", "page": "index",
        "path": "html > body > main > div", "tag": "div"})
    assert res.status_code == 200, res.text
    return res.json()["component"]


async def test_component_definition_is_stored_and_listed(env):
    pid = await _project(env)
    component = await _define_card(env, pid)
    assert component["version"] == 1

    pdir = wd._pdir(env.svc, pid)
    stored = json.loads((pdir / "components.json").read_text(encoding="utf-8"))
    # определение легло ДОСЛОВНО: разметку владельца никто не переписывает
    assert stored["items"]["card"]["html"] == \
        '<div class="card"><h3 data-bd-slot="title">Тариф</h3><p data-bd-slot="text">описание</p></div>'
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/components")).json()["items"]
    assert [c["name"] for c in listed] == ["card"]
    assert listed[0]["instances"] == 0


async def test_component_instance_lands_in_the_page_and_carries_its_definition(env):
    pid = await _project(env)
    await _define_card(env, pid)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/components/card/insert",
                                json={"page": "index", "path": "html > body > main",
                                      "tag": "main", "position": "append",
                                      "base_version": await _version(env, pid)})
    assert res.status_code == 200, res.text

    code = await _code(env, pid)
    assert code.count('data-bd-component="card"') == 1
    assert 'data-bd-cver="1"' in code
    # исходная карточка на месте: вставка добавляет, а не заменяет
    assert code.count("Тариф") == 2


async def test_updating_a_definition_rewrites_every_instance_and_keeps_slots(env):
    """Обещание компонента: одно определение — все страницы. Слоты переживают
    пересборку, иначе «обновить компонент» значило бы «стереть текст везде»."""
    pid = await _project(env)
    await _define_card(env, pid)
    await env.client.post(f"/api/web-designer/projects/{pid}/pages",
                          json={"slug": "prices", "title": "Цены"})
    await _put(env, pid, "<html><body><main></main></body></html>", page="prices")
    for page in ("index", "prices"):
        res = await env.client.post(f"/api/web-designer/projects/{pid}/components/card/insert",
                                    json={"page": page, "path": "html > body > main",
                                          "tag": "main", "position": "append"})
        assert res.status_code == 200, res.text
    # владелец правит текст в слоте одного экземпляра
    edit = await env.client.post(f"/api/web-designer/projects/{pid}/edit", json={
        "op": "text", "page": "prices",
        "path": "html > body > main > div:nth-of-type(1) > h3", "text": "Мой тариф"})
    assert edit.status_code == 200, edit.text

    upd = await env.client.put(f"/api/web-designer/projects/{pid}/components/card", json={
        "html": '<section class="card v2"><h3 data-bd-slot="title">Тариф</h3>'
                '<p data-bd-slot="text">описание</p><a href="#buy">Купить</a></section>',
        "base_version": await _version(env, pid)})
    assert upd.status_code == 200, upd.text
    assert upd.json()["component"]["version"] == 2

    index_code = await _code(env, pid, "index")
    prices_code = await _code(env, pid, "prices")
    for code in (index_code, prices_code):
        assert '<section class="card v2"' in code, "экземпляр не пересобран из нового определения"
        assert 'data-bd-cver="2"' in code
        assert 'href="#buy"' in code
    # правка в слоте пережила пересборку, а исходный текст другого экземпляра — нет причин терять
    assert "Мой тариф" in prices_code
    assert "Тариф" in index_code


async def test_deleting_a_definition_leaves_the_markup_alone(env):
    """Удаление определения — это не удаление вёрстки: экземпляры остаются
    обычной разметкой страницы и просто перестают обновляться."""
    pid = await _project(env)
    await _define_card(env, pid)
    await env.client.post(f"/api/web-designer/projects/{pid}/components/card/insert",
                          json={"page": "index", "path": "html > body > main", "tag": "main"})
    before = await _code(env, pid)

    res = await env.client.delete(f"/api/web-designer/projects/{pid}/components/card")
    assert res.status_code == 200, res.text
    assert res.json()["instances_left"] == 1
    assert await _code(env, pid) == before, "удаление определения переписало страницу"
    pdir = wd._pdir(env.svc, pid)
    assert json.loads((pdir / "components.json").read_text(encoding="utf-8"))["items"] == {}
    assert (await env.client.post(
        f"/api/web-designer/projects/{pid}/components/card/insert",
        json={"page": "index", "path": "html > body > main"})).status_code == 404


async def test_component_names_and_shapes_that_would_break_markup_are_refused(env):
    """Имя компонента уезжает В АТРИБУТ разметки, а определение — в страницу.
    И то и другое проверяется до записи, а не после."""
    pid = await _project(env)
    await _put(env, pid, CARD)
    pdir = wd._pdir(env.svc, pid)
    before = await _code(env, pid)
    for hostile in ('x" onmouseover="alert(1)', "x><script>alert(1)</script", "Карточка",
                    "x y", "-нет"):
        res = await env.client.post(f"/api/web-designer/projects/{pid}/components",
                                    json={"name": hostile, "html": "<div>x</div>"})
        assert res.status_code == 422, f"{hostile}: {res.status_code}"
    # определение из двух корней — честный отказ, а не «первый победил»
    two = await env.client.post(f"/api/web-designer/projects/{pid}/components",
                                json={"name": "two", "html": "<div>a</div><div>b</div>"})
    assert two.status_code == 422
    assert not _components_file(pdir), "негодное определение всё-таки записалось"
    assert await _code(env, pid) == before
    assert "onmouseover" not in await _code(env, pid)


def _components_file(pdir) -> dict:
    path = pdir / "components.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("items") or {}


async def test_stale_selection_and_stale_version_are_reported_not_applied(env):
    """Две половины одного правила: правка несёт echo-тег и версию документа,
    и несовпадение любой из них — 409, а не запись в чужой тег."""
    pid = await _project(env)
    await _define_card(env, pid)
    base = await _version(env, pid)
    await _put(env, pid, CARD.replace("Тариф", "Тариф 2"))   # версия ушла вперёд
    before = await _code(env, pid)

    stale_version = await env.client.post(
        f"/api/web-designer/projects/{pid}/components/card/insert",
        json={"page": "index", "path": "html > body > main", "tag": "main",
              "base_version": base})
    assert stale_version.status_code == 409, stale_version.text

    stale_tag = await env.client.post(
        f"/api/web-designer/projects/{pid}/components/card/insert",
        json={"page": "index", "path": "html > body > main", "tag": "section"})
    assert stale_tag.status_code == 409, stale_tag.text
    assert await _code(env, pid) == before, "отклонённая вставка всё-таки записалась"


# ================================================================
# 3. Токены
# ================================================================

PAGE = ('<!doctype html>\n<html lang="ru">\n<head><title>t</title></head>\n<body>\n'
        '<h1 CLASS="Hero" Data-Role=\'title\'>Заголовок</h1>\n'
        "<p class='a' data-n=7>текст &amp; ещё</p>\n"
        '<!-- комментарий --><br/>\n</body></html>\n')


def _style_block(html: str, style_id: str) -> tuple[str, str, str]:
    """Документ → (до блока, сам блок, после блока). Блока нет — пустая середина."""
    marker = f'<style id="{style_id}">'
    start = html.find(marker)
    if start < 0:
        return html, "", ""
    end = html.index("</style>", start) + len("</style>")
    return html[:start], html[start:end], html[end:]


async def test_changing_an_accent_touches_one_node_not_every_element(env):
    """Главное обещание токенов: смена акцента идёт через CSS-переменные.

    Если бы панель применяла тему, переписывая инлайновый стиль каждого
    элемента, первая же смена цвета стёрла бы дословное написание всей
    вёрстки владельца — регистр атрибутов, кавычки, комментарии."""
    pid = await _project(env)
    await _put(env, pid, PAGE)

    first = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                                 json={"tokens": {"color": {"accent": "#112233"}}})
    assert first.status_code == 200, first.text
    once = await _code(env, pid)
    assert "--bd-color-accent: #112233;" in once
    # мост в старые переменные: сайт, собранный до токенов, перекрашивается целиком
    assert "--accent: var(--bd-color-accent);" in once

    second = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                                  json={"tokens": {"color": {"accent": "#445566"}}})
    assert second.status_code == 200, second.text
    twice = await _code(env, pid)
    assert "--bd-color-accent: #445566;" in twice

    before_a, block_a, after_a = _style_block(once, "bd-tokens")
    before_b, block_b, after_b = _style_block(twice, "bd-tokens")
    assert block_a != block_b, "блок токенов не изменился — тема не применилась"
    assert (before_a, after_a) == (before_b, after_b), \
        "смена акцента переписала документ за пределами блока токенов"
    # и дословная вёрстка владельца на месте
    for fragment in ('<h1 CLASS="Hero" Data-Role=\'title\'>', "<p class='a' data-n=7>",
                     "<!-- комментарий --><br/>", '<html lang="ru">'):
        assert fragment in twice, fragment
    # набор слит, а не заменён: шрифты никто не просил трогать
    tokens = (await env.client.get(f"/api/web-designer/projects/{pid}/tokens")).json()["tokens"]
    assert tokens["color"]["accent"] == "#445566"
    assert tokens["font"]["sans"].startswith("'Inter'")


async def test_tokens_reach_every_page_of_the_project(env):
    pid = await _project(env)
    await env.client.post(f"/api/web-designer/projects/{pid}/pages", json={"slug": "second"})
    await _put(env, pid, PAGE)
    await _put(env, pid, PAGE, page="second")
    res = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                               json={"tokens": {"color": {"ink": "#010203"}}})
    assert res.status_code == 200, res.text
    assert set(res.json()["pages"]) == {"index", "second"}
    for page in ("index", "second"):
        assert "--bd-color-ink: #010203;" in await _code(env, pid, page)


async def test_token_values_that_would_escape_the_style_block_are_refused(env):
    """Значение токена уезжает ВНУТРЬ <style>: там нет экранирования, там сырой
    текст, и `}` закрывает :root, а `</style>` — сам элемент."""
    pid = await _project(env)
    await _put(env, pid, PAGE)
    pdir = wd._pdir(env.svc, pid)
    before_tokens = wd._load_tokens(pdir)
    before_code = await _code(env, pid)
    hostile_values = ["red} body{display:none", "red</style><script>alert(1)</script>",
                      "red;color:blue", "url(x)/*", ""]
    for value in hostile_values:
        res = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                                   json={"tokens": {"color": {"accent": value}}})
        assert res.status_code == 422, f"{value!r}: {res.status_code}"
    for name in ('accent" x', "Акцент", "-bad", "a" * 60):
        res = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                                   json={"tokens": {"color": {name: "#fff"}}})
        assert res.status_code == 422, f"{name!r}: {res.status_code}"
    res = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                               json={"tokens": {"цвет": {"accent": "#fff"}}})
    assert res.status_code == 422
    assert wd._load_tokens(pdir) == before_tokens, "негодный токен всё-таки записан"
    assert await _code(env, pid) == before_code
    assert "</style><script>" not in await _code(env, pid)


async def test_token_write_on_a_stale_version_is_a_conflict(env):
    pid = await _project(env)
    await _put(env, pid, PAGE)
    base = await _version(env, pid)
    await _put(env, pid, PAGE.replace("Заголовок", "Другой"))
    stale = await env.client.put(f"/api/web-designer/projects/{pid}/tokens",
                                 json={"tokens": {"color": {"accent": "#000000"}},
                                       "base_version": base})
    assert stale.status_code == 409, stale.text
    assert "--bd-color-accent" not in await _code(env, pid)


# ================================================================
# 5. Отзывчивость
# ================================================================

async def test_breakpoint_rule_becomes_a_real_media_query_in_the_page(env):
    pid = await _project(env)
    await _put(env, pid, PAGE)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/responsive", json={
        "breakpoint": "md", "selector": ".hero h1", "props": {"font-size": "24px"}})
    assert res.status_code == 200, res.text
    rule_id = res.json()["rule"]["id"]

    code = await _code(env, pid)
    assert "@media (max-width: 768px)" in code
    assert ".hero h1 { font-size: 24px; }" in code
    _, block, _ = _style_block(code, "bd-responsive")
    assert "@media" in block, "медиазапрос лёг мимо управляемого блока"

    # правила проекта видны и через свой список
    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/responsive")).json()
    assert [r["id"] for r in listed["items"]] == [rule_id]

    gone = await env.client.delete(f"/api/web-designer/projects/{pid}/responsive/{rule_id}")
    assert gone.status_code == 200, gone.text
    after = await _code(env, pid)
    assert "@media (max-width: 768px)" not in after
    assert "Заголовок" in after, "удаление правила снесло вёрстку"


async def test_narrow_breakpoints_are_serialised_after_wide_ones(env):
    """При `max-width` побеждает последнее подошедшее правило: телефон обязан
    идти ПОСЛЕ планшета, иначе на телефоне выигрывает планшетный стиль."""
    pid = await _project(env)
    await _put(env, pid, PAGE)
    for bp, size in (("sm", "12px"), ("lg", "20px"), ("md", "16px")):
        res = await env.client.post(f"/api/web-designer/projects/{pid}/responsive", json={
            "breakpoint": bp, "selector": "body", "props": {"font-size": size}})
        assert res.status_code == 200, res.text
    code = await _code(env, pid)
    order = [code.index(f"@media (max-width: {w}px)") for w in (1024, 768, 480)]
    assert order == sorted(order), "медиазапросы идут не от широкого к узкому"


async def test_hostile_rules_and_the_rule_limit_are_refused(env):
    pid = await _project(env)
    await _put(env, pid, PAGE)
    pdir = wd._pdir(env.svc, pid)
    bad = [
        {"breakpoint": "phone", "selector": "body", "props": {"color": "red"}},
        {"breakpoint": "md", "selector": "body { } html", "props": {"color": "red"}},
        {"breakpoint": "md", "selector": "body</style><script>", "props": {"color": "red"}},
        {"breakpoint": "md", "selector": "body", "props": {"color": "red} *{display:none"}},
        {"breakpoint": "md", "selector": "body", "props": {"color:red;x": "1"}},
        {"breakpoint": "md", "selector": "body", "props": {}},
    ]
    for payload in bad:
        res = await env.client.post(f"/api/web-designer/projects/{pid}/responsive", json=payload)
        assert res.status_code == 422, f"{payload}: {res.status_code}"
    assert wd._load_responsive(pdir)["rules"] == []
    assert "@media" not in await _code(env, pid)

    state = wd._load_responsive(pdir)
    state["rules"] = [{"id": f"r{i}", "breakpoint": "md", "selector": "body",
                       "props": {"color": "red"}} for i in range(wd.MAX_RESPONSIVE_RULES)]
    state["seq"] = wd.MAX_RESPONSIVE_RULES
    wd._save_responsive(pdir, state)
    over = await env.client.post(f"/api/web-designer/projects/{pid}/responsive", json={
        "breakpoint": "sm", "selector": "body", "props": {"color": "blue"}})
    assert over.status_code == 409 and "предел" in over.json()["error"]["message"]
    assert len(wd._load_responsive(pdir)["rules"]) == wd.MAX_RESPONSIVE_RULES


# ================================================================
# 4. Ассеты
# ================================================================

WOFF = b"wOFF" + b"\x00" * 60


async def _upload(env, pid, raw: bytes, name: str = "logo.png"):
    return await env.client.post(
        f"/api/web-designer/projects/{pid}/assets",
        json={"name": name, "data": base64.b64encode(raw).decode("ascii")})


async def test_asset_lands_in_the_project_directory_and_is_served_back(env):
    pid = await _project(env)
    res = await _upload(env, pid, PNG)
    assert res.status_code == 200, res.text
    asset = res.json()["asset"]

    pdir = wd._pdir(env.svc, pid)
    on_disk = pdir / "assets" / f"{asset['id']}.png"
    assert on_disk.is_file() and on_disk.read_bytes() == PNG
    assert not list((pdir / "assets").glob(".*tmp")), "рядом остался временный файл"

    served = await env.client.get(asset["url"])
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["x-content-type-options"] == "nosniff"

    listed = (await env.client.get(f"/api/web-designer/projects/{pid}/assets")).json()["items"]
    assert [a["id"] for a in listed] == [asset["id"]]
    assert listed[0]["kind"] == "image" and listed[0]["bytes"] == len(PNG)

    font = await _upload(env, pid, WOFF, name="шрифт")
    assert font.status_code == 200, font.text
    assert font.json()["asset"]["kind"] == "font"
    assert (pdir / "assets" / f"{font.json()['asset']['id']}.woff").is_file()


async def test_content_type_comes_from_the_bytes_not_from_the_name(env):
    """Имя из браузера — утверждение загрузившего, а не факт. `evil.html`,
    названный картинкой (и наоборот), отдавался бы как HTML ровно до первого
    браузера, решившего доверять имени."""
    pid = await _project(env)
    res = await _upload(env, pid, PNG, name="evil.html")
    assert res.status_code == 200, res.text
    asset = res.json()["asset"]
    assert asset["ext"] == "png" and asset["content_type"] == "image/png"
    pdir = wd._pdir(env.svc, pid)
    assert (pdir / "assets" / f"{asset['id']}.png").is_file()
    assert not list((pdir / "assets").glob("*.html"))
    served = await env.client.get(asset["url"])
    assert served.headers["content-type"].startswith("image/png")

    # а вот HTML и SVG — не картинки, сколько их ни называй логотипом
    for hostile in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                    b"<!doctype html><script>alert(1)</script>"):
        bad = await _upload(env, pid, hostile, name="logo.png")
        assert bad.status_code == 422, bad.text
    assert [p.suffix for p in sorted((pdir / "assets").iterdir())] == [".png"]


async def test_same_bytes_are_stored_once_and_delete_removes_the_file(env):
    pid = await _project(env)
    first = (await _upload(env, pid, PNG)).json()["asset"]
    second = (await _upload(env, pid, PNG, name="другое имя.png")).json()["asset"]
    assert first["id"] == second["id"]
    pdir = wd._pdir(env.svc, pid)
    assert len(list((pdir / "assets").iterdir())) == 1
    assert len(wd._load_assets(pdir)) == 1

    gone = await env.client.delete(f"/api/web-designer/projects/{pid}/assets/{first['id']}")
    assert gone.status_code == 200
    assert not (pdir / "assets" / f"{first['id']}.png").exists()
    assert wd._load_assets(pdir) == []
    assert (await env.client.get(first["url"])).status_code == 404


async def test_asset_limits_and_identifiers_are_enforced(env, monkeypatch):
    pid = await _project(env)
    pdir = wd._pdir(env.svc, pid)

    monkeypatch.setattr(wd, "MAX_ASSET_BYTES", 64)
    big = await _upload(env, pid, PNG * 4)
    assert big.status_code == 413, big.text
    assert not (pdir / "assets").exists() or not list((pdir / "assets").iterdir())
    monkeypatch.undo()

    wd._save_assets(pdir, [{"id": f"{i:024x}", "ext": "png", "name": "x",
                            "content_type": "image/png", "kind": "image", "bytes": 1}
                           for i in range(wd.MAX_ASSETS)])
    over = await _upload(env, pid, PNG)
    assert over.status_code == 409 and "предел" in over.json()["error"]["message"]
    assert len(wd._load_assets(pdir)) == wd.MAX_ASSETS

    # идентификатор — хэш содержимого: `..` шестнадцатеричным не бывает
    assert (await env.client.get(
        f"/api/web-designer/projects/{pid}/assets/not-a-hash")).status_code == 422
    # `%2e%2e` доходит до обработчика уже как «..» — проверка формы ловит его там
    assert (await env.client.delete(
        f"/api/web-designer/projects/{pid}/assets/%2e%2e")).status_code == 422
    assert (await env.client.get(
        f"/api/web-designer/projects/{pid}/assets/{'ab' * 12}")).status_code == 404
    assert (pdir / "project.json").is_file()
