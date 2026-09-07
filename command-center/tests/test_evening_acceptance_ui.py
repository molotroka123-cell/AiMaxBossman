"""Вечерняя приёмка: главные элементы управления обязаны работать и не врать.

Каждый тест здесь воспроизводит дефект, который владелец увидел бы руками:
мёртвая главная кнопка, спокойный экран поверх отказавшего запроса, строка
«N задач с ошибкой» без причины, ссылка в никуда, два «Пульта» в меню,
одинаковый глиф у разных приложений, неизвестный адрес, показывающий главную.

Всё проверяется в настоящем Chromium против живого сервера: перечисленное —
поведение браузера и живых данных, unit-тест здесь ничего не доказывает.
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t, task_runs as runs_t, tasks as tasks_t, utcnow

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _call(srv, factory, timeout: float = 10.0):
    return asyncio.run_coroutine_threadsafe(factory(), srv.loop).result(timeout=timeout)


def _new_agent(srv, name: str) -> int:
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.insert(agents_t).values(
                name=name, system_prompt="отвечай коротко", enabled=True,
                max_steps=1, created_at=utcnow()))
            aid = int(res.inserted_primary_key[0])
            await s.commit()
            return aid
    return _call(srv, go)


def _new_failed_task(srv, title: str, error: str) -> int:
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title=title, prompt="проверка главной", status="failed", priority=5,
                max_retries=0, created_at=utcnow(), updated_at=utcnow()))
            tid = int(res.inserted_primary_key[0])
            await s.execute(sa.insert(runs_t).values(
                task_id=tid, attempt=1, status="failed", error=error, finished_at=utcnow()))
            await s.commit()
            return tid
    return _call(srv, go)


def _tasks(srv) -> list[dict]:
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.select(tasks_t))
            return [dict(r._mapping) for r in res.fetchall()]
    return _call(srv, go)


def _open_home(page, srv):
    page.goto(srv.url + "/#/home-v3", wait_until="domcontentloaded")
    page.wait_for_selector("#attention", timeout=20000)


# ---------------------------------------------------------------- B1


def test_landing_cta_creates_a_task_with_two_agents(live):  # noqa: F811
    """Главная кнопка страницы обязана запускать работу, а не отбивать владельца.

    Раньше агент вычислялся как `state.agentId ?? (agents.length === 1 ? ...)`,
    а `state.agentId` не присваивался НИГДЕ: при двух и более агентах «ЗАПУСТИТЬ»
    всегда показывала «Выберите агента» и уводила на #/agents. Выбирать было
    негде — селектора на главной не было вовсе.
    """
    from playwright.sync_api import sync_playwright

    first = _new_agent(live, "Аналитик")
    second = _new_agent(live, "Монтажёр")

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        _open_home(page, live)

        picker = page.locator("select[data-role='home-agent']")
        assert picker.count() == 1, "на главной нет выбора агента"
        names = picker.locator("option").all_inner_texts()
        assert "Аналитик" in names and "Монтажёр" in names, names

        picker.select_option(str(second))
        page.fill(".bx-command-input", "Собери отчёт по вчерашним прогонам")
        page.locator(".bx-command button.bx-btn-xl").click()

        # Задача создана — и создана НА ВЫБРАННОГО агента.
        page.wait_for_function(
            "() => !document.querySelector('.bx-command-input').value", timeout=15000)
        page.wait_for_timeout(400)
        created = [t for t in _tasks(live) if t["title"].startswith("Собери отчёт")]
        assert created, "кнопка «ЗАПУСТИТЬ» не создала задачу"
        assert created[0]["agent_id"] == second, created[0]
        assert created[0]["agent_id"] != first
        # И владельца никуда не уводили.
        assert "home-v3" in page.evaluate("location.hash")

        browser.close()
    assert errors == [], errors


def test_landing_cta_asks_for_an_agent_without_leaving_the_page(live):  # noqa: F811
    """Два агента и ни один не выбран — подсказка на месте, страница та же."""
    from playwright.sync_api import sync_playwright

    _new_agent(live, "Первый")
    _new_agent(live, "Второй")

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        _open_home(page, live)
        assert page.locator("select[data-role='home-agent']").input_value() == ""
        page.fill(".bx-command-input", "Проверь почту")
        page.locator(".bx-command button.bx-btn-xl").click()
        page.wait_for_selector(".toast-msg:text('Выберите агента')", timeout=10000)
        page.wait_for_timeout(500)
        assert "home-v3" in page.evaluate("location.hash"), "кнопка увела со страницы"
        assert _tasks(live) == []
        browser.close()


def test_landing_cta_preselects_the_only_agent(live):  # noqa: F811
    """Один агент — выбирать не из чего, поручение уходит сразу."""
    from playwright.sync_api import sync_playwright

    only = _new_agent(live, "Единственный")
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        _open_home(page, live)
        assert page.locator("select[data-role='home-agent']").input_value() == str(only)
        browser.close()


# ---------------------------------------------------------------- B19


def test_command_bar_has_no_mode_chip_without_an_effect(live):  # noqa: F811
    """Переключатель обязан переключать.

    Чипы «Умно» и «Авто» меняли один и тот же флаг run_now, а «С агентами» не
    делал вообще ничего: POST /api/tasks про режимы не знает. Осталась одна
    пилюля, названная тем, что она делает, — и она действительно меняет исход.
    """
    from playwright.sync_api import sync_playwright

    agent = _new_agent(live, "Исполнитель")
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        _open_home(page, live)

        labels = page.locator(".bx-modes .bx-mode").all_inner_texts()
        assert "С агентами" not in labels, labels
        assert not ("Умно" in labels and "Авто" in labels), \
            f"две подписи на один флаг run_now: {labels}"

        # выключаем единственный режим — задача обязана остаться черновиком
        page.locator(".bx-modes .bx-mode").first.click()
        page.fill(".bx-command-input", "Черновик без запуска")
        page.locator(".bx-command button.bx-btn-xl").click()
        page.wait_for_function(
            "() => !document.querySelector('.bx-command-input').value", timeout=15000)
        page.wait_for_timeout(400)
        drafts = [t for t in _tasks(live) if t["title"].startswith("Черновик без запуска")]
        assert drafts, "задача не создана"
        assert drafts[0]["status"] == "draft", drafts[0]
        assert drafts[0]["agent_id"] == agent
        browser.close()


# ---------------------------------------------------------------- B2


def test_home_says_data_is_missing_instead_of_reporting_calm(live):  # noqa: F811
    """Отказавший запрос не превращается в хорошую новость.

    Прежде Promise.allSettled глотал отказы /api/approvals, /api/missions и
    /api/tasks, и страница спокойно писала «Ничего не ждёт вашего решения» и
    «Сейчас ничего не выполняется» без единого предупреждения.
    """
    from playwright.sync_api import sync_playwright

    _new_agent(live, "Исполнитель")
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/approvals*", lambda route: route.abort())
        page.route("**/api/missions*", lambda route: route.abort())
        page.route("**/api/tasks*", lambda route: route.abort())
        _login(page, live)
        _open_home(page, live)

        attention = page.locator("#attention")
        assert attention.get_attribute("data-failed") != "0", \
            "отказ источников не доехал до блока внимания"
        text = attention.inner_text().lower()
        assert "данные не получены" in text, text
        assert "ничего не ждёт вашего решения" not in text, text

        now = page.locator("#now-card").inner_text().lower()
        assert "данные не получены" in now, now
        assert "сейчас ничего не выполняется" not in now, now
        browser.close()


def test_home_stays_calm_when_the_server_really_answered(live):  # noqa: F811
    """Обратная сторона: пустая и здоровая система молчит, как и раньше."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        _open_home(page, live)
        assert page.locator("#attention.is-calm").count() == 1
        assert "ничего не ждёт вашего решения" in page.locator("#attention").inner_text().lower()
        assert "сейчас ничего не выполняется" in page.locator("#now-card").inner_text().lower()
        browser.close()


# ---------------------------------------------------------------- B9


def test_failed_task_row_shows_the_reason_and_opens_that_task(live):  # noqa: F811
    """Строка «задача с ошибкой» обязана нести ПРИЧИНУ и вести к самой задаче.

    Раньше note предпочитала заголовок задачи (его владелец писал сам), а клик
    открывал неотфильтрованный список — причина падения не попадалась на глаза
    ни на главной, ни после перехода.
    """
    from playwright.sync_api import sync_playwright

    task_id = _new_failed_task(live, "Ночной отчёт", "провайдер недоступен: 502 от шлюза")

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        _open_home(page, live)

        row = page.locator('#attention .bx-attn-row[data-kind="task-failed"]')
        row.wait_for(timeout=15000)
        assert "провайдер недоступен" in row.inner_text(), row.inner_text()

        row.click()
        page.wait_for_function(
            f"() => location.hash.includes('tasks') && location.hash.includes('task={task_id}')",
            timeout=10000)
        # Deep-link открыл именно эту задачу, а не общий список.
        page.wait_for_selector(".task.open .task-body", timeout=15000)
        assert "Ночной отчёт" in page.locator(".task.open").inner_text()
        browser.close()


# ---------------------------------------------------------------- B6 и B5


def test_navigation_has_no_duplicate_titles_and_no_shared_glyphs(live):  # noqa: F811
    """Два «Пульта» неразличимы в сайдбаре и в палитре; ⓘ вместо иконки — тоже.

    icon() подставляет ICONS.info любому незнакомому имени, поэтому Video Studio
    и Web Designer рисовались одинаковым кружком с буквой i.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        pages = page.evaluate("window.__bxPages")
        visible = [p for p in pages if not p["superseded"]]

        titles = [p["title"] for p in visible]
        dupes = sorted({t for t in titles if titles.count(t) > 1})
        assert dupes == [], f"страницы неразличимы по названию: {dupes}"

        known = page.evaluate(
            "async () => Object.keys((await import('/components.js')).ICONS)")
        missing = sorted({p["icon"] for p in pages if p["icon"] and p["icon"] not in known})
        assert missing == [], f"иконки нет — страница получит ⓘ: {missing}"
        browser.close()


# ---------------------------------------------------------------- B12


def test_unknown_hash_is_an_explicit_dead_end(live):  # noqa: F811
    """Неизвестный адрес не подменяется главной молча."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.goto(live.url + "/#/такой-страницы-нет", wait_until="domcontentloaded")
        page.wait_for_selector("text=не найдена", timeout=15000)
        view = page.locator("#view").inner_text().lower()
        assert "не найдена" in view, view
        # главную не показали и подсветку в доке не поставили
        assert page.locator("#attention").count() == 0, view
        assert page.locator(".nav-item.is-active, .nav-item.active").count() == 0

        page.locator("#view button", has_text="На главную").first.click()
        page.wait_for_selector("#attention", timeout=15000)
        assert "home-v3" in page.evaluate("location.hash")
        browser.close()


# ---------------------------------------------------------------- B11


def test_long_task_result_is_reachable_in_the_chat_history(live):  # noqa: F811
    """Длинный однострочный результат не должен уезжать за край панели.

    `.bx-panel` режет по горизонтали (overflow:hidden), а голый <pre> не
    переносит строк: текст становился физически недостижим — ни прокрутки,
    ни переноса.
    """
    from playwright.sync_api import sync_playwright

    long_line = "РЕЗУЛЬТАТ " + "x" * 4000 + " КОНЕЦ"
    code_block = "\n".join(f"def step_{i}(value):  # шаг {i}" for i in range(200))

    async def seed():
        async with live.svc.db.session() as s:
            ids = []
            for title, result in (("Однострочный", long_line), ("Блок кода", code_block)):
                res = await s.execute(sa.insert(tasks_t).values(
                    title=title, prompt="проверка вывода", status="completed", priority=5,
                    max_retries=0, created_at=utcnow(), updated_at=utcnow()))
                tid = int(res.inserted_primary_key[0])
                await s.execute(sa.insert(runs_t).values(
                    task_id=tid, attempt=1, status="completed", result=result,
                    finished_at=utcnow()))
                ids.append(tid)
            await s.commit()
            return ids

    task_ids = _call(live, seed)

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        # Страница чата строится из /api/video-studio/chat; здесь нас интересует
        # только вёрстка блока результата, поэтому историю подставляем ответом.
        page.route("**/api/video-studio/chat", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"messages": [' + ", ".join(
                f'{{"task_id": {tid}, "project_id": "p{tid}", "text": "результат"}}'
                for tid in task_ids) + ']}'))
        page.goto(live.url + "/#/bossman-chat", wait_until="domcontentloaded")
        page.wait_for_selector("#view pre", timeout=20000)

        blocks = page.locator("#view pre")
        assert blocks.count() == 2, blocks.count()
        overflow = blocks.evaluate_all("""nodes => nodes.map(n => ({
          cls: n.className,
          hiddenRight: n.scrollWidth - n.clientWidth,
          wrap: getComputedStyle(n).whiteSpace,
          overflowX: getComputedStyle(n).overflowX,
          insidePanel: n.getBoundingClientRect().right
                       <= n.closest('.bx-panel').getBoundingClientRect().right + 1,
        }))""")
        for item in overflow:
            assert "bx-code" in item["cls"], item
            # либо перенос, либо собственная прокрутка — но не молчаливое обрезание
            assert item["wrap"].startswith("pre-wrap") or item["overflowX"] in ("auto", "scroll"), item
            assert item["hiddenRight"] == 0 or item["overflowX"] in ("auto", "scroll"), item
            assert item["insidePanel"], item

        # текст действительно доступен целиком
        assert "КОНЕЦ" in blocks.nth(0).inner_text()
        assert "step_199" in blocks.nth(1).inner_text()
        browser.close()


# ---------------------------------------------------------------- B3


def test_app_without_an_entrypoint_cannot_be_started_from_the_dashboard(live):  # noqa: F811
    """Кнопка, которая гарантированно откажет, хуже честной надписи.

    apps/<имя> без pyproject-скрипта и без пакета с __main__.py запустить нельзя:
    POST /apps/{id}/start ответит 409. Карточка при этом рисовала живую
    «Запустить», а команда для терминала печаталась плейсхолдером
    `python -m <модуль приложения>`, который нельзя набрать.
    """
    from playwright.sync_api import sync_playwright
    from bcc.features import apps_control

    broken = [a for a in apps_control.known_app_dirs()
              if apps_control.command_for(a)["problem"]]
    if not broken:
        pytest.skip("все установленные приложения имеют точку запуска")
    app_id = broken[0]
    reason = apps_control.command_for(app_id)["problem"]

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.goto(live.url + "/#/apps", wait_until="domcontentloaded")
        page.wait_for_selector(".bx-app", timeout=20000)

        blocked = page.locator(".bx-app button[data-problem]")
        blocked.first.wait_for(timeout=15000)
        assert blocked.first.is_disabled(), "кнопка запуска осталась живой"
        assert reason[:24] in blocked.first.get_attribute("title")

        # На странице самого приложения — причина и никакого плейсхолдера.
        page.goto(live.url + f"/#/apps?open={app_id}", wait_until="domcontentloaded")
        page.wait_for_selector("[data-role='start-problem']", timeout=20000)
        text = page.locator("#view").inner_text()
        assert reason[:24] in text, text
        assert "<модуль приложения>" not in text, text
        start = page.locator("#view button", has_text="Запустить").first
        assert start.is_disabled(), "кнопка «Запустить» не выключена"
        browser.close()


# ---------------------------------------------------------------- B10


def test_fleet_row_is_marked_experimental_while_enabled(live):  # noqa: F811
    """Флот не сертифицирован для распределённого production — и это видно.

    Строка «узлов N · очередь M» без пометки читается как готовая функция.
    Ответ сервера подменяем: включённый флот в этой среде не поднять, а проверяем
    мы поведение экрана, а не наличие узлов.
    """
    from playwright.sync_api import sync_playwright

    body = """{"queue": {}, "treasury": {}, "latency": {},
      "owner_view": {"rows": [], "rule": ""},
      "fleet": {"enabled": true, "nodes": [{"node_id": "n1"}], "queue_depth": 2,
                "experimental": true,
                "experimental_reason": "флот не сертифицирован для распределённого production",
                "remote_transport_production_ready": false,
                "node_auth_production_ready": false}}"""

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/control-plane", lambda route: route.fulfill(
            status=200, content_type="application/json", body=body))
        _login(page, live)
        page.goto(live.url + "/#/control", wait_until="domcontentloaded")
        page.wait_for_selector("[data-fleet='experimental']", timeout=20000)

        pill = page.locator("[data-fleet='experimental']")
        assert pill.count() == 1
        assert "ЭКСПЕРИМЕНТ" in pill.inner_text().upper(), pill.inner_text()
        assert "не сертифицирован" in page.locator("#view").inner_text()
        browser.close()


def test_fleet_row_has_no_experimental_pill_while_off(live):  # noqa: F811
    """Выключённый флот ничего не обещает — и пилюлю не рисует."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.goto(live.url + "/#/control", wait_until="domcontentloaded")
        page.wait_for_selector("text=Состояние системы", timeout=20000)
        assert "флот" in page.locator("#view").inner_text()
        assert page.locator("[data-fleet='experimental']").count() == 0
        browser.close()


# ---------------------------------------------------------------- B7, B8, B14


def _video_project(page, name: str, operation_id: str) -> str:
    """Проект Video Studio без ffmpeg: рендера здесь нет, нужна только вёрстка."""
    created = page.evaluate("""async ([name, op]) => {
      const headers = {'Content-Type':'application/json',
                       'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''};
      const r = await fetch('/api/video-studio/projects', {method: 'POST', headers,
        body: JSON.stringify({name, operation_id: op})});
      return {status: r.status, body: await r.json()};
    }""", [name, operation_id])
    assert created["status"] == 200, created
    return created["body"]["project_id"]


def test_video_studio_search_keeps_the_caret_where_it_was(live):  # noqa: F811
    """Правка запроса в середине не должна отбрасывать курсор в конец.

    Восстановление каретки было закрыто проверкой `input.type === 'text'`, а поле
    объявлено как `search`: условие не выполнялось никогда, и каждая буква
    отправляла курсор в конец строки.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        _login(page, live)
        pid = _video_project(page, "Каретка", "acceptance-caret")
        page.goto(live.url + f"/#/video-studio?project_id={pid}", wait_until="domcontentloaded")
        page.wait_for_selector(".vs-search", timeout=20000)

        search = page.locator(".vs-search")
        search.fill("абвгде")
        search.evaluate("el => { el.focus(); el.setSelectionRange(3, 3); }")
        page.keyboard.type("ХY")

        assert page.locator(".vs-search").input_value() == "абвХYгде", \
            page.locator(".vs-search").input_value()
        assert page.locator(".vs-search").evaluate("el => el.selectionStart") == 5
        browser.close()


def test_video_studio_links_point_at_pages_that_exist(live):  # noqa: F811
    """Подпись «Bossman Chat» обязана вести в Bossman Chat, а не на #/home.

    #/home вытеснена страницей home-v3 и скрыта из навигации; deep-link задачи
    читается как params.task, а не params.id — иначе открывается общий список.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        _login(page, live)
        pid = _video_project(page, "Ссылки", "acceptance-links")
        page.goto(live.url + f"/#/video-studio?project_id={pid}", wait_until="domcontentloaded")
        page.wait_for_selector(".vs-brand", timeout=20000)

        superseded = {p["id"] for p in page.evaluate("window.__bxPages") if p["superseded"]}
        assert "home" in superseded, "проверка потеряла смысл: #/home снова в навигации"

        hrefs = page.locator("a.vs-brand, a.vs-chat-link").evaluate_all(
            "ns => ns.map(n => n.getAttribute('href'))")
        assert hrefs, hrefs
        for href in hrefs:
            target = href.replace("#/", "").split("?")[0]
            assert target not in superseded, f"ссылка ведёт на вытесненную страницу: {href}"
        assert "#/home-v3" in hrefs, hrefs
        assert "#/bossman-chat" in hrefs, hrefs

        # Задача открывается deep-link'ом, который TasksPage действительно читает.
        page.route(f"**/api/video-studio/projects/{pid}", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=_with_task_link(route)))
        page.reload()
        page.wait_for_selector("a.vs-chat-link[href*='tasks']", timeout=20000)
        task_link = page.get_attribute("a.vs-chat-link[href*='tasks']", "href")
        assert task_link == "#/tasks?task=77", task_link
        browser.close()


def _with_task_link(route) -> str:
    """Ответ сервера + связь с задачей: связь ставится фоновым прогоном чата,
    а проверяем мы правило построения ссылки, а не работу самого прогона."""
    import json

    response = route.request.frame.page.request.get(route.request.url)
    payload = response.json()
    project = payload.get("project", payload)
    project["links"] = dict(project.get("links") or {}, task_id=77)
    return json.dumps(payload, ensure_ascii=False)


def test_failed_export_offers_a_retry_that_resubmits_the_same_settings(live):  # noqa: F811
    """После провала экспорта нужен способ повторить — иначе владелец заново
    заполняет всю форму.

    Отдельного маршрута «retry» на сервере нет: повтор — это тот же
    POST /api/video-studio/exports с теми же настройками. Поэтому кнопка
    показывается ТОЛЬКО когда настройки известны клиенту; после перезагрузки
    страницы задания приходят из истории без них, и кнопки нет.
    """
    from playwright.sync_api import sync_playwright

    posts: list[str] = []

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        _login(page, live)
        pid = _video_project(page, "Экспорт", "acceptance-export")

        page.on("request", lambda r: posts.append(r.url)
                if r.method == "POST" and r.url.endswith("/api/video-studio/exports") else None)
        # Рендер в этой среде невозможен (ffmpeg нет), а движок задач в тестовом
        # сервере не крутится: провал задания подставляем ответом опроса.
        page.route("**/api/video-studio/exports/*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"status": "failed", "progress": null, "preview": false,'
                 f' "project_id": "{pid}",'
                 ' "error_detail": {"message": "ffmpeg не найден", "code": "RENDER_FAILED"}}'))

        page.goto(live.url + f"/#/video-studio?project_id={pid}", wait_until="domcontentloaded")
        page.wait_for_selector(".vs-header", timeout=20000)
        page.locator(".vs-header button", has_text="Экспорт").first.click()
        page.wait_for_selector("dialog.vs-dialog", timeout=10000)
        page.locator("dialog.vs-dialog button[type=submit]").click()

        retry = page.locator(".vs-job button", has_text="Повторить")
        retry.wait_for(timeout=20000)
        assert "ffmpeg не найден" in page.locator(".vs-jobs").inner_text()
        assert len(posts) == 1, posts

        retry.first.click()
        page.wait_for_timeout(1000)
        assert len(posts) == 2, f"повтор не отправил новый экспорт: {posts}"
        browser.close()
