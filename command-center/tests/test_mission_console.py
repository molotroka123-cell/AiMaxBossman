"""Операторский канал (ui/pages/mission_console.js) в настоящем Chromium против живого сервера.

Проверяется не «нарисовалось ли», а четыре обещания страницы:

  * она открывается без падения (нет панели «Повторить», консоль чистая),
    у каждой видимой кнопки есть имя, текст без кракозябр, на 390px нет
    горизонтальной прокрутки;
  * НИ ОДНА цифра на экране не появляется без данных с сервера. Это
    проверяется структурно, а не на глаз: каждое значение обязано лежать
    в элементе с data-src (адрес ручки-источника), а всё, что источника
    не объявило, обязано не содержать цифр вообще. На пустом сервере
    плейсхолдеры — именно «нет данных», а не нули;
  * пустое состояние осмысленно: связный текст про то, что работы нет,
    а не таблица нулей;
  * карточка подтверждения помечена как требующая решения, показывает обе
    кнопки, и нажатие действительно доезжает до сервера.

Страница открывается переходом оболочки — так же, как её открывает владелец.
Раньше тест монтировал узел в #view сам (страницы ещё не было в реестре) и
проигрывал гонку домашней странице: её отрисовка асинхронна и приходила уже
ПОСЛЕ нашего узла, затирая его. У оболочки от этого есть защита (renderToken),
у ручного монтажа её не было — и в CI это дало падение «на экране домашняя
страница вместо канала».
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa

from bcc.auth import HEADER
from bcc.db import run_events as revents_t, task_runs as truns_t, tasks as tasks_t, utcnow

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(240),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

# Сетевой шум недоступных внешних сервисов ошибкой страницы не считается.
NETWORK_NOISE = re.compile(
    r"net::ERR_|Failed to load resource|the server responded with a status of (404|409|501|503)", re.I)
# «Ð»/«Ñ» + символ Latin-1/кириллицы = двойная UTF-8-кодировка; в русском UI их не бывает.
MOJIBAKE = re.compile("[ÐÑ][­-ÿ–-™Ѐ-џ]")
NO_DATA = "нет данных"

# Открываем страницу переходом оболочки, а контракт читаем из самого модуля:
# так проверяется ровно тот путь, которым страница откроется у владельца.
OPEN_JS = """async () => {
  const mod = await import('./pages/mission_console.js');
  const p = mod.default;
  // Перерисовка — тоже средствами оболочки: уходим и возвращаемся, как это
  // делает человек. Своей отрисовки поверх #view тест больше не заводит.
  window.__mcRender = async () => {
    location.hash = '#/home';
    await new Promise(r => setTimeout(r, 60));
    location.hash = '#/mission_console';
    await new Promise(r => setTimeout(r, 60));
  };
  location.hash = '#/mission_console';
  return {id: p.id, title: p.title, icon: p.icon, nav: p.nav,
          hasRender: typeof p.render === 'function',
          hasOnEvent: typeof p.onEvent === 'function'};
}"""

# Аудит того, что владелец реально видит.
AUDIT_JS = """() => {
  const view = document.getElementById('view');
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const buttons = Array.from(view.querySelectorAll('button'));
  const unnamed = buttons.filter(visible)
    .filter(b => !(b.textContent.trim() || b.getAttribute('aria-label') || b.getAttribute('title')))
    .map(b => b.outerHTML.slice(0, 140));
  const vals = Array.from(view.querySelectorAll('.mc-val')).map(v => ({
    text: v.textContent.trim(),
    src: v.getAttribute('data-src') || '',
    nodata: v.classList.contains('is-nodata'),
  }));
  // Всё, что объявило источник, вырезаем — в остатке цифр быть не должно.
  const rest = view.cloneNode(true);
  for (const el of rest.querySelectorAll('[data-src]')) el.remove();
  // подпись -> значение: значение стоит следующим соседом за своей подписью
  const cells = {};
  for (const label of view.querySelectorAll('.mc-label')) {
    const kids = Array.from(label.parentElement.children);
    const value = kids.slice(kids.indexOf(label) + 1).find(k => k.classList.contains('mc-val'));
    if (value) cells[label.textContent.trim()] = value.textContent.trim();
  }
  return {
    buttons: buttons.length,
    labels: buttons.map(b => b.textContent.trim()).filter(Boolean),
    unnamed,
    retry: buttons.some(b => b.textContent.trim() === 'Повторить'),
    vals, cells,
    cards: Array.from(view.querySelectorAll('[data-card]')).map(c => c.dataset.card),
    loose: rest.textContent,
    text: view.innerText,
  };
}"""


# --------------------------------------------------------------- вспомогательное


def _client(live) -> httpx.Client:  # noqa: F811
    """Клиент к живому серверу в обход прокси окружения (см. loopback_get)."""
    return httpx.Client(trust_env=False, base_url=live.url, timeout=15.0,
                        headers={HEADER: live.svc.auth.token})


def _db(live, fn):  # noqa: F811
    """Выполнить работу в БД живого сервера, в его собственном цикле событий."""
    async def go():
        async with live.svc.db.session() as s:
            out = await fn(s)
            await s.commit()
            return out
    return asyncio.run_coroutine_threadsafe(go(), live.loop).result(timeout=20)


def _seed(live) -> dict:  # noqa: F811
    """Живая миссия: модель, агент, план из трёх задач, начатый прогон с журналом
    и один запрос подтверждения. Всё — через настоящие ручки и настоящие таблицы,
    чтобы страница читала ровно то, что читала бы у владельца."""
    with _client(live) as c:
        prov = c.post("/api/providers", json={"name": "локальный", "kind": "openai_compat",
                                              "base_url": "http://127.0.0.1:1"})
        assert prov.status_code == 200, prov.text
        provider_id = prov.json()["id"]

        model = c.post("/api/models", json={"provider_id": provider_id, "name": "qwen-coder",
                                            "alias": "qwen-local", "kind": "local",
                                            "context_window": 32768})
        assert model.status_code == 200, model.text

        agent = c.post("/api/agents", json={"name": "Аналитик", "role": "разбор отчётов",
                                            "model_id": model.json()["id"]})
        assert agent.status_code == 200, agent.text

        mission = c.post("/api/missions", json={"title": "Разбор отчётов поставщика",
                                                "goal": "Свести отчёты поставщика в один вывод"})
        assert mission.status_code == 200, mission.text
        mission_id = mission.json()["id"]

        # сервер отдаёт список от новых к старым; первый шаг плана — минимальный id
        tasks = sorted([t for t in c.get("/api/tasks?limit=100").json()
                        if t.get("mission_id") == mission_id], key=lambda t: t["id"])
        assert len(tasks) == 3, tasks
        task_id = tasks[0]["id"]

    started = utcnow() - timedelta(seconds=42)
    journal = [
        ("run.step", "info", "шаг один: собраны исходные файлы отчёта"),
        ("tool.called", "info", "инструмент fs.read отработал"),
        ("run.step", "info", "шаг два: сверка сумм с накладной"),
    ]

    async def insert(s):
        res = await s.execute(sa.insert(truns_t).values(
            task_id=task_id, attempt=0, status="running", model_alias="qwen-local",
            tokens_in=1280, tokens_out=340, cost_usd=0.0125, started_at=started,
            route={"alias": "qwen-local", "reasons": ["локальная модель дешевле облачной"]}))
        run_id = int(res.inserted_primary_key[0])
        for i, (kind, level, message) in enumerate(journal):
            await s.execute(sa.insert(revents_t).values(
                run_id=run_id, ts=started + timedelta(seconds=3 * i), level=level,
                kind=kind, message=message))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(status="running"))
        return run_id

    run_id = _db(live, insert)

    with _client(live) as c:
        appr = c.post("/api/approvals", json={
            "kind": "terminal", "preview": "удалить каталог сборки перед пересборкой",
            "task_id": task_id, "run_id": run_id})
        assert appr.status_code == 200, appr.text
        approval_id = appr.json()["id"]

    return {"mission_id": mission_id, "task_id": task_id, "run_id": run_id,
            "approval_id": approval_id}


def _open(pw, live, *, viewport=None, mobile=False):  # noqa: F811
    """Браузер + вход + смонтированная страница. Возвращает (browser, page, errors, meta)."""
    errors: list[str] = []
    browser = _launch(pw)
    kw = {"viewport": viewport or {"width": 1440, "height": 900}}
    if mobile:
        kw.update(is_mobile=True, has_touch=True)
    page = browser.new_page(**kw)
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, live)
    page.wait_for_selector("#conn-dot.dot-ok", timeout=20000)
    meta = page.evaluate(OPEN_JS)
    page.wait_for_selector("#view .mc2030", timeout=15000)
    return browser, page, errors, meta


def _check_no_invented_numbers(audit: dict) -> list[str]:
    """Главная проверка проекта: цифра на экране обязана иметь источник."""
    problems: list[str] = []
    for v in audit["vals"]:
        if v["src"]:
            if v["text"] == NO_DATA:
                problems.append(f"значение с источником {v['src']} притворяется отсутствующим")
            continue
        if v["text"] != NO_DATA:
            problems.append(f"значение без источника: {v['text']!r}")
        if not v["nodata"]:
            problems.append(f"значение без источника не помечено как «нет данных»: {v['text']!r}")
    loose = re.findall(r"\d+", audit["loose"])
    if loose:
        problems.append(f"цифры вне элементов с data-src: {loose[:8]}")
    return problems


# --------------------------------------------------------------- пустой сервер


def test_empty_console_is_calm_and_shows_no_zeros(live):  # noqa: F811
    """Пустой сервер: страница рендерится, говорит человеческим текстом,
    а на месте неизвестных величин стоит «нет данных», а не нули."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors, meta = _open(pw, live)
        audit = page.evaluate(AUDIT_JS)
        # onEvent — фильтр живой ленты: обновляемся по делу и не дёргаемся на
        # служебном шуме соединения и метриках железа.
        wants = page.evaluate("""async () => {
          const p = (await import('./pages/mission_console.js')).default;
          const on = (kind) => Boolean(p.onEvent({kind}));
          return {progress: on('task.progress'), approval: on('approval.created'),
                  mission: on('mission.started'), ws: on('ws.open'),
                  metrics: on('system.metrics')};
        }""")
        browser.close()

    assert wants == {"progress": True, "approval": True, "mission": True,
                     "ws": False, "metrics": False}, wants

    # контракт страницы — тот же, что у соседей по ui/pages/
    assert meta["id"] == "mission_console" and meta["title"] == "Операторский канал"
    assert meta["hasRender"] and meta["hasOnEvent"] and meta["nav"] in ("primary", "more")

    assert not audit["retry"], "страница показала «Повторить» — рендер упал"
    assert audit["unnamed"] == [], f"кнопки без имени: {audit['unnamed']}"
    assert not MOJIBAKE.search(audit["text"]), "кракозябры в тексте страницы"

    assert "Канал молчит" in audit["text"], audit["text"][:600]
    assert "система просто ждёт работы" in audit["text"]
    assert NO_DATA in audit["text"], "неизвестные величины обязаны быть подписаны"

    problems = _check_no_invented_numbers(audit)
    assert not problems, "\n".join(problems)

    # На пустом сервере не знает никто, кроме железа: миссии, задачи, модели,
    # агенты, подтверждения и расход обязаны быть подписаны «нет данных».
    # Показания железа (/api/system) — настоящее измерение и существуют даже на
    # пустой базе, поэтому они исключены из правила, а не подогнаны под него.
    invented = [v for v in audit["vals"] if v["src"] and not v["src"].startswith("/api/system")]
    assert invented == [], f"на пустом сервере появились значения из ниоткуда: {invented}"
    assert audit["vals"], "страница обязана показать сами клетки значений, а не спрятать их"
    assert errors == [], "\n".join(errors)


# --------------------------------------------------------------- живая миссия


def test_console_with_a_live_mission_backs_every_number(live):  # noqa: F811
    """Живая миссия: появляются все четыре вида карточек, счётчики совпадают
    с тем, что отдал сервер, а то, чего в API нет (температура), честно
    показано как «нет данных»."""
    from playwright.sync_api import sync_playwright

    seeded = _seed(live)

    with sync_playwright() as pw:
        browser, page, errors, _meta = _open(pw, live)
        audit = page.evaluate(AUDIT_JS)

        # «развернуть рассуждения и верификацию» — раскрывает факты исполнения
        disclose = page.locator("#view .mc-disclose")
        assert disclose.get_attribute("aria-expanded") == "false"
        assert "Развернуть" in disclose.inner_text()
        disclose.click()
        page.wait_for_selector("#view .mc-details:not([hidden])", timeout=5000)
        details = page.inner_text("#view .mc-details")
        after = page.evaluate(AUDIT_JS)
        browser.close()

    assert not audit["retry"], "страница показала «Повторить» — рендер упал"
    assert audit["unnamed"] == [], f"кнопки без имени: {audit['unnamed']}"
    assert not MOJIBAKE.search(audit["text"]), "кракозябры в тексте страницы"

    # четыре вида карточек ленты
    assert set(audit["cards"]) == {"plan", "verify", "approval", "route"}, audit["cards"]

    # счётчик плана: три задачи миссии, первая в работе
    assert audit["cells"].get("шаг") == "1 / 3", audit["cells"]

    # честные цифры карточки проверки — токены и секунды из прогона
    assert audit["cells"].get("токенов") == "1.6k", audit["cells"]      # 1280 + 340
    seconds = audit["cells"].get("секунд работы", "")
    assert re.fullmatch(r"\d+,\d", seconds), audit["cells"]
    assert float(seconds.replace(",", ".")) >= 42, seconds              # прогон начат 42 с назад
    assert audit["cells"].get("записей в журнале") == "3", audit["cells"]

    # маршрут: модель есть, температуры в API нет — и она не выдумана
    assert audit["cells"].get("кто отвечал") == "qwen-local", audit["cells"]
    assert audit["cells"].get("температура") == NO_DATA, audit["cells"]
    assert audit["cells"].get("размер контекста") == "32k", audit["cells"]

    # ход проверки — факты исполнения из журнала прогона, а не рассуждения модели
    assert "сверка сумм с накладной" in audit["text"]
    assert "проверено" in audit["text"]
    assert "сырой ход её мыслей сервер наружу не отдаёт" in details
    assert "шаг один: собраны исходные файлы отчёта" in details

    problems = _check_no_invented_numbers(audit) + _check_no_invented_numbers(after)
    assert not problems, "\n".join(problems)
    assert errors == [], "\n".join(errors)
    assert seeded["run_id"] > 0


def test_approval_card_asks_for_a_decision_and_the_decision_reaches_the_server(live):  # noqa: F811
    """Карточка подтверждения: помечена как требующая решения, обе кнопки на месте,
    и «Подтвердить» действительно записывает решение на сервере."""
    from playwright.sync_api import sync_playwright

    seeded = _seed(live)

    with sync_playwright() as pw:
        browser, page, errors, _meta = _open(pw, live)

        card = page.locator(f'#view [data-card="approval"][data-approval="{seeded["approval_id"]}"]')
        card.wait_for(timeout=10000)
        # inner_text отдаёт подписи так, как их рисует CSS (они в верхнем регистре)
        text = card.inner_text().lower()
        assert "требует решения" in text, text
        assert "удалить каталог сборки перед пересборкой" in text, text
        assert "команда в терминале этой машины" in text, text          # влияние
        assert "цена ожидания" in text, text                            # цена по срокам

        labels = card.locator("button").all_inner_texts()
        assert [l.strip() for l in labels] == ["Подтвердить", "Отклонить"], labels

        card.locator("button", has_text=re.compile("^Подтвердить$")).click()
        page.wait_for_timeout(600)
        browser.close()

    with _client(live) as c:
        approved = c.get("/api/approvals", params={"status": "approved"}).json()
        pending = c.get("/api/approvals", params={"status": "pending"}).json()
    assert [a["id"] for a in approved] == [seeded["approval_id"]], approved
    assert pending == [], "решённое подтверждение обязано уйти из ожидающих"
    assert errors == [], "\n".join(errors)


# --------------------------------------------------------------- телефон


def test_console_fits_a_phone_without_horizontal_scroll(live):  # noqa: F811
    """390×844: ни пустой, ни наполненный экран не даёт горизонтальной прокрутки."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, errors, _meta = _open(pw, live, viewport={"width": 390, "height": 844},
                                             mobile=True)
        empty = page.evaluate("({sw: document.documentElement.scrollWidth,"
                              " cw: document.documentElement.clientWidth})")
        assert empty["sw"] <= empty["cw"] + 1, f"пустой экран шире телефона: {empty}"

        _seed(live)
        page.evaluate("() => window.__mcRender()")
        page.wait_for_selector("#view .mc2030", timeout=15000)
        page.wait_for_selector('#view [data-card="approval"]', timeout=10000)
        page.wait_for_timeout(200)
        full = page.evaluate("({sw: document.documentElement.scrollWidth,"
                             " cw: document.documentElement.clientWidth})")
        audit = page.evaluate(AUDIT_JS)

        # Тач-цели решения на телефоне не должны быть меньше пальца. Замер
        # делается одним заходом внутри страницы: оболочка перерисовывает #view
        # по событиям шины, и узел, найденный отдельным вызовом, к моменту
        # измерения успевает устареть — тогда высота приходит пустой.
        heights = page.evaluate("""() => {
          const out = {};
          for (const name of ['Подтвердить', 'Отклонить']) {
            const b = Array.from(document.querySelectorAll('#view [data-card="approval"] button'))
              .find((x) => x.textContent.trim() === name);
            out[name] = b ? b.getBoundingClientRect().height : null;
          }
          return out;
        }""")
        for name, height in heights.items():
            assert height and height >= 40, f"{name}: {height} (кнопки: {heights})"
        browser.close()

    assert full["sw"] <= full["cw"] + 1, f"наполненный экран шире телефона: {full}"
    assert audit["unnamed"] == [], f"кнопки без имени: {audit['unnamed']}"
    assert not audit["retry"]
    assert not MOJIBAKE.search(audit["text"])
    problems = _check_no_invented_numbers(audit)
    assert not problems, "\n".join(problems)
    assert errors == [], "\n".join(errors)
