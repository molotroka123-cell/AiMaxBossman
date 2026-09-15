#!/usr/bin/env python3
"""Раздел 23: обход ВСЕХ видимых органов управления настоящим Chromium.

Задание формулирует требование без двусмысленности: «владельцу неинтересна
теоретически блестящая система с мёртвыми кнопками». Значит проверять надо
не наличие кнопки в разметке, а то, что происходит после нажатия.

Что делает этот инструмент:

* поднимает НАСТОЯЩИЙ Command Center на временном каталоге данных;
* входит настоящим токеном через форму входа;
* открывает каждую страницу реестра `ui/pages/index.js`;
* находит каждый видимый орган управления и НАЖИМАЕТ его;
* по последствиям относит нажатие к одному из классов.

Классы (ровно те, что перечислены в разделе 23):

  works              — интерфейс ответил: изменилась разметка или адрес
  disabled_reason    — выключен И объясняет, почему (title/aria/подпись)
  opens_feature      — открылся диалог, панель или другая страница
  request_accepted   — изменяющий запрос получил 2xx; результат требует проверки
  input_refused      — пустое поле отклонено с проверенной видимой подсказкой
  refresh_observed   — после клика получен GET 2xx; изменение данных не заявлено
  dialog_opened      — нативный диалог открыт и безопасно отменён обходом
  already_selected   — измеренное выбранное состояние сохранилось
  already_empty      — список вложений был и остался пустым
  error              — ошибка в консоли, исключение страницы или 5xx
  dead               — НИЧЕГО: ни разметки, ни адреса, ни запроса, ни консоли

Отдельно:

  disabled_silent    — выключен и не объясняет причину. Это НЕ «мёртвый клик»,
                       но для владельца это тупик без подсказки, поэтому класс
                       свой и в отчёт он попадает отдельной строкой.
  skipped_harness    — нажатие убило бы сам обход (выход, остановка сервера).
                       Пропуск назван, а не спрятан.

Честность измерения: «мёртвый» здесь означает «наблюдаемых последствий нет
ни по одному из четырёх каналов». Это не доказательство отсутствия эффекта
вообще — кнопка могла записать что-то в localStorage. Поэтому класс называется
`dead`, а не `broken`, и каждый такой случай разбирается руками.

Запуск:

    python scripts/ui_acceptance_sweep.py --json docs/final/ui_acceptance.json

    --pages video-studio,web_designer   только эти страницы
    --headed                            показать окно (для отладки)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import socket
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "command-center"
if str(CC) not in sys.path:
    sys.path.insert(0, str(CC))

# Органы управления, нажатие которых оборвало бы сам обход. Пропуск честный:
# он попадает в отчёт отдельным классом, а не исчезает из знаменателя.
KILLS_HARNESS = re.compile(
    r"вы[йх]ти|выход|logout|останов(ить|ка) сервер|заверш(ить|ение) работ|"
    r"перезапуст(ить|ка) bossman|shutdown",
    re.IGNORECASE,
)

SETTLE_MS = 700          # сколько ждать последствий нажатия
PAGE_SETTLE_MS = 900     # сколько ждать первой отрисовки страницы
APP_START_ROUTE = re.compile(r'/api/apps/[^/?#]+/start(?:\?[^#]*)?$')
APP_REFRESH_ROUTE = re.compile(r'/api/apps\?refresh=true(?:&[^#]*)?$')


def _tracked_request(method, url):
    return method in ('POST', 'PUT', 'PATCH', 'DELETE') or (
        method == 'GET' and bool(APP_REFRESH_ROUTE.search(url)))


def _app_start_problem(payload):
    if isinstance(payload, dict) and payload.get('ok') is True and payload.get('ready') is True:
        return None
    reason = payload.get('reason') if isinstance(payload, dict) else None
    # Never copy child logs, commands or arbitrary provider details into proof.
    return reason if reason in ('exited', 'not_ready') else 'readiness_not_confirmed'


@dataclass
class Click:
    page: str
    label: str
    selector: str
    verdict: str
    detail: str = ""
    requests: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    initial_state: dict = field(default_factory=dict)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveApp:
    """Тот же способ подъёма, что в браузерных тестах, без их фикстур."""

    def __init__(self, data_dir: Path) -> None:
        import uvicorn
        from bcc.app import create_app
        from bcc.config import Settings

        data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
            ui_dir=CC / "ui",
        )
        self.app = create_app(self.settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port,
            log_level="warning", loop="none", timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.serve())

    def start(self) -> "LiveApp":
        import httpx
        self.thread.start()
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with httpx.Client(trust_env=False, timeout=1.0) as c:
                    if c.get(f"{self.url}/").status_code < 500:
                        return self
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("сервер не поднялся за 30 с")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def page_ids() -> list[str]:
    """Идентификаторы страниц берутся из САМОГО реестра, а не из копии списка.

    Список, записанный здесь руками, разошёлся бы с `pages/index.js` при первой
    же новой странице, и обход тихо перестал бы её проверять.
    """
    src = (CC / "ui" / "pages" / "index.js").read_text(encoding="utf-8")
    return re.findall(r"lazyPage\(\{\s*id:\s*'([^']+)'", src)


def _visible_controls(page) -> list[dict]:
    """Видимые органы управления страницы с устойчивым признаком для повторного поиска."""
    return page.evaluate(
        """() => {
        const view = document.querySelector('#view') || document.body;
        const out = [];
        const seen = new Set();
        const nodes = view.querySelectorAll("button, [role='button'], input[type='submit']");
        for (const el of nodes) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            const style = getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            const label = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
            const key = label + '|' + (el.className || '');
            if (seen.has(key)) continue;      /* строки списков дают один класс на все */
            seen.add(key);
            out.push({
                label: label || '(без подписи)',
                cls: String(el.className || ''),
                disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
                reason: (el.title || el.getAttribute('aria-label')
                         || el.getAttribute('data-reason') || '').trim(),
            });
        }
        return out;
    }"""
    )


def _find(page, control: dict):
    """Найти тот же орган управления заново после повторной отрисовки страницы.

    Искать надо именно КНОПКУ, а не узел с текстом: `get_by_text` охотно
    возвращает внешний контейнер, и клик по нему либо промахивается, либо
    упирается в перехватчик указателя. Поэтому фильтр идёт по роли.
    """
    label = control["label"]
    # Подпись кнопки со счётчиком приходит многострочной («Все изображения\n0»);
    # точное совпадение по такой строке не найдёт ничего.
    first_line = label.split("\n")[0].strip()
    if first_line and first_line != "(без подписи)":
        for loc in (page.locator("#view").get_by_role("button", name=first_line, exact=True),
                    page.locator("#view").get_by_role("button", name=first_line)):
            if loc.count():
                return loc.first
    if control["cls"]:
        sel = "#view button." + ".".join(c for c in control["cls"].split() if c)
        loc = page.locator(sel)
        if loc.count():
            return loc.first
    return None


def _fresh_page(page, app: "LiveApp", pid: str) -> None:
    """Открыть страницу с НУЛЯ: полная перезагрузка, вход при необходимости."""
    page.goto(f"{app.url}/#/{pid}", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    if page.locator("#login:not([hidden])").count():
        page.fill("#login-token", app.svc.auth.token)
        page.click("#login-submit")
        page.wait_for_selector("#shell:not([hidden])", timeout=20000)
        page.goto(f"{app.url}/#/{pid}", wait_until="domcontentloaded")
    _wait_rendered(page, pid)
    page.wait_for_timeout(PAGE_SETTLE_MS)


def _wait_rendered(page, pid):
    # renderPage replaces its skeleton only after awaited page.render().
    # DOMContentLoaded alone happens before lazy imports and API responses.
    page.wait_for_function("""pid => {
      const view = document.querySelector('#view');
      if (!view || !view.childElementCount || view.querySelector('.skeleton')) return false;
      if (pid !== 'apps') return true;
      return !!view.querySelector('.bx-apps-grid') ||
        view.innerText.includes('Приложений пока нет') ||
        view.innerText.includes('Список приложений не загрузился');
    }""", arg=pid, timeout=20000)
    if pid == 'apps':
        error = page.locator('#view').get_by_text('Список приложений не загрузился', exact=True)
        if error.count() and error.first.is_visible():
            raise RuntimeError('Application registry failed to load')


def _dom_fingerprint(page) -> str:
    # Input value/checked properties are not reflected into innerHTML when
    # a shortcut fills a command or a checkbox changes through JavaScript.
    html = page.evaluate("""() => {
        const root = document.querySelector('#view') || document.body;
        return JSON.stringify({html: root.innerHTML,
          fields: [...root.querySelectorAll('input, textarea, select')].map(el =>
            ({value: el.value, checked: el.checked, selected: el.selectedIndex}))});
    }""")
    return hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()


def _visible_texts(page, selector: str) -> set[str]:
    return set(page.locator(selector).evaluate_all("""elements => elements.filter(el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    }).map(el => el.innerText.trim())"""))


def _validation_case(page, pid, label):
    cases = {
        ('openrouter', 'Connect'): ('#view input[type=password]', 'Вставьте ключ',
            '{message: Вставьте ключ, hint: без ключа подключаться нечем}'),
        ('mission_console', 'Отправить'): ('#mc-command-input', 'Команда пустая',
            'Error: Команда пустая'),
    }
    case = cases.get((pid, label))
    if case and page.locator(case[0]).count() == 1 and not page.locator(case[0]).input_value().strip():
        return case[1:]
    return None


def _known_state(target, pid):
    return target.evaluate("""(el, pid) => {
      const result = {};
      for (const attr of ['aria-selected', 'aria-pressed'])
        if (el.getAttribute(attr) === 'true') result.selected = attr + '=true';
      if (pid === 'terminal' && el.matches('.seg > button.on')) result.selected = 'terminal mode: .seg > button.on';
      if (pid === 'images' && el.matches('.images-tabs button.active, .images-collection-row.active'))
        result.selected = 'image selection: ' + el.className;
      if (el.innerText.trim() === 'Убрать вложения') {
        const parent = el.parentElement;
        const input = parent.querySelector('input[type=file][aria-label="Прикрепить медиа"]');
        const names = parent.querySelector('small');
        if (input && names && input.files.length === 0 && names.innerText.trim() === '')
          result.empty_attachments = 'file input has 0 files; attachment names empty';
      }
      return result;
    }""", pid, timeout=1000)


def _classify(*, failures, console, page_errors, requests, responses,
              new_dialogs, new_toasts, validation, dom_changed, url_changed,
              read_responses=(), native_dialogs=(), unchanged_state=None, incomplete_requests=()):
    refusal = (validation and validation[0] in new_toasts and not requests
               and not failures and not page_errors and console
               and all(line.splitlines()[0] == validation[1] for line in console))
    if refusal:
        return 'input_refused', 'Пустое поле: показана проверенная подсказка «' + validation[0] + '», запрос не отправлен'
    if failures or page_errors or console:
        return 'error', '; '.join(sorted(set(failures + page_errors + console))[:3])
    if incomplete_requests:
        return 'error', 'Не получен ответ в пределах тайм-аута действия: ' + '; '.join(sorted(incomplete_requests))
    if responses:
        return 'request_accepted', '; '.join(responses[:3]) + ' (ответ 2xx; результат отдельно не проверен)'
    if requests:
        return 'error', 'Изменяющий запрос отправлен, но успешный ответ не наблюдался'
    if native_dialogs:
        return 'dialog_opened', '; '.join(native_dialogs) + ' (диалог отменён обходом)'
    if new_dialogs or url_changed:
        return 'opens_feature', 'новый видимый диалог' if new_dialogs else 'адрес изменился'
    if dom_changed or new_toasts:
        return 'works', 'видимое состояние изменилось'
    if read_responses:
        return 'refresh_observed', '; '.join(read_responses[:3]) + ' (GET после клика; изменение данных не заявлено)'
    if unchanged_state and unchanged_state.get('selected'):
        return 'already_selected', unchanged_state['selected'] + ' до и после клика'
    if unchanged_state and unchanged_state.get('empty_attachments'):
        return 'already_empty', unchanged_state['empty_attachments'] + ' до и после клика'
    return 'dead', 'ни разметки, ни адреса, ни запроса, ни консоли'


def sweep(app: LiveApp, pages: list[str], *, headed: bool) -> list[Click]:
    from playwright.sync_api import sync_playwright
    from bcc.browser_runtime import chromium_executable

    exe = chromium_executable(preinstalled="/opt/pw-browsers/chromium")
    if exe is None:
        raise SystemExit("Chromium не найден — обход интерфейса невозможен. "
                         "Раздел 23 требует НАСТОЯЩЕГО браузера; подделывать нечем.")

    results: list[Click] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe, headless=not headed)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        console: list[str] = []
        page_errors: list[str] = []
        requests: list[str] = []
        responses: list[str] = []
        read_responses: list[str] = []
        native_dialogs: list[str] = []
        pending: set[str] = set()
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("request", lambda r: requests.append(f"{r.method} {r.url}")
                if r.method in ("POST", "PUT", "PATCH", "DELETE") else None)
        failures: list[str] = []
        page.on("response", lambda r: failures.append(f"{r.status} {r.url}")
                if r.status >= 500 or (r.status >= 400 and _tracked_request(r.request.method, r.url)) else None)
        page.on("response", lambda r: responses.append(f"{r.status} {r.request.method} {r.url}")
                if 200 <= r.status < 300 and r.request.method in ("POST", "PUT", "PATCH", "DELETE") else None)
        page.on('response', lambda r: read_responses.append(f'{r.status} GET {r.url}')
                if r.request.method == 'GET' and 200 <= r.status < 300 else None)
        page.on('request', lambda r: pending.add(f'{r.method} {r.url}')
                if _tracked_request(r.method, r.url) else None)
        page.on('response', lambda r: pending.discard(f'{r.request.method} {r.url}'))
        def verify_app_start(response):
            if response.request.method == 'POST' and APP_START_ROUTE.search(response.url) and response.ok:
                try:
                    problem = _app_start_problem(response.json())
                except Exception:
                    problem = 'invalid_readiness_response'
                if problem:
                    failures.append('App start: ' + problem)
        page.on('response', verify_app_start)
        def request_failed(request):
            key = f'{request.method} {request.url}'
            if key in pending:
                failures.append('network failure: ' + key)
                pending.discard(key)
        page.on('requestfailed', request_failed)
        def native_dialog(dialog):
            native_dialogs.append(dialog.type + ': ' + dialog.message)
            dialog.dismiss()
        page.on('dialog', native_dialog)

        page.goto(app.url + "/", wait_until="domcontentloaded")
        page.fill("#login-token", app.svc.auth.token)
        page.click("#login-submit")
        page.wait_for_selector("#shell:not([hidden])", timeout=20000)

        for pid in pages:
            try:
                _fresh_page(page, app, pid)
                controls = _visible_controls(page)
                # Template selection is local UI state; creating a project
                # changes the database and removes this empty-state chooser.
                # Exercise the chooser before its create action, not after it.
                if pid == 'web_designer':
                    controls.sort(key=lambda control: 'bd-tpl' not in control['cls'].split())
            except Exception as exc:                      # noqa: BLE001
                results.append(Click(pid, "(страница)", "", "error", f"не открылась: {exc}"))
                continue

            if not controls:
                results.append(Click(pid, "(страница)", "", "no_controls",
                                     "видимых органов управления нет"))
                continue

            for ctl in controls:
                label = ctl["label"]
                if KILLS_HARNESS.search(label):
                    results.append(Click(pid, label, ctl["cls"], "skipped_harness",
                                         "нажатие оборвало бы сам обход"))
                    continue

                if ctl["disabled"]:
                    verdict = "disabled_reason" if ctl["reason"] else "disabled_silent"
                    results.append(Click(pid, label, ctl["cls"], verdict, ctl["reason"]))
                    continue

                # Каждое нажатие — с чистой страницы: иначе последствия
                # предыдущего клика приписались бы следующему.
                #
                # Переход по ОДНОМУ ТОЛЬКО якорю страницу не перезагружает, и
                # первый же открывшийся диалог остаётся висеть поверх: дальше
                # каждый клик упирается в его подложку и истекает по времени.
                # Первый прогон этой развёртки так и выглядел — 70 «ошибок»
                # подряд после единственного диалога на первой странице.
                # Настоящая перезагрузка — единственный честный сброс.
                _fresh_page(page, app, pid)
                target = _find(page, ctl)
                if target is None:
                    results.append(Click(pid, label, ctl["cls"], "vanished",
                                         "после повторной отрисовки элемент не найден"))
                    continue

                if pid == 'web_designer' and 'bd-tpl' in ctl['cls'].split():
                    # Clicking the default selection is legitimately a no-op.
                    # Select a different card first, then verify this card.
                    alternatives = page.locator('#view button.bd-tpl').filter(has_not_text=label.split('\n')[0])
                    if alternatives.count():
                        alternatives.first.click()

                # Кнопка может быть ВЫКЛЮЧЕНА, пока страница дочитывает данные,
                # и включиться через долю секунды. Нажатие в это окно даёт
                # «TimeoutError — element is not enabled», и в отчёте это
                # выглядит как сломанная кнопка. Владелец в такой ситуации
                # просто ждёт; развёртка обязана вести себя так же.
                if not target.is_enabled():
                    try:
                        page.wait_for_timeout(1200)
                        target = _find(page, ctl) or target
                    except Exception:                      # noqa: BLE001
                        pass
                    if not target.is_enabled():
                        verdict = "disabled_reason" if ctl["reason"] else "disabled_silent"
                        results.append(Click(pid, label, ctl["cls"], verdict,
                                             ctl["reason"] or "выключена и после ожидания"))
                        continue

                console.clear(); requests.clear(); failures.clear(); page_errors.clear(); responses.clear()
                read_responses.clear(); native_dialogs.clear(); pending.clear()
                before_dom = _dom_fingerprint(page)
                before_url = page.url
                before_dialogs = _visible_texts(page, 'dialog[open], .modal, [role=dialog]')
                before_toasts = _visible_texts(page, '#toast-root .toast-msg')
                validation = _validation_case(page, pid, label)
                initial_state = _known_state(target, pid)
                try:
                    target.click(timeout=5000)
                except Exception as exc:                  # noqa: BLE001
                    # Журнал вызова Playwright называет ПРИЧИНУ («intercepts
                    # pointer events», «element is not stable», «outside of the
                    # viewport»). Без неё в отчёте остаётся голое
                    # «TimeoutError», по которому чинить нечего: именно так
                    # BL-027 полдня выглядел как «кнопка не нажимается».
                    log = str(exc).split("Call log:")[-1]
                    why = [ln.strip(" -\t") for ln in log.splitlines()
                           if ln.strip() and "waiting for" not in ln
                           and "retrying" not in ln and "attempting" not in ln
                           and not ln.strip().startswith("- waiting ")]
                    # Если журнал вызова причины не назвал, спросим у страницы
                    # САМИ: кто лежит в точке клика и где вообще элемент. Ровно
                    # эти два числа и опознали BL-027.
                    seen = ""
                    try:
                        seen = page.evaluate(
                            """(name) => { const b=[...document.querySelectorAll('#view button')]
                            .find(x=>(x.innerText||'').trim().startsWith(name));
                            if(!b) return 'элемента уже нет';
                            const r=b.getBoundingClientRect();
                            const e=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
                            return `в точке клика: ${e?e.tagName+'.'+(e.className||''):'ничего'};`
                                 + ` y=${Math.round(r.top)} при окне ${window.innerHeight}`
                                 + `; видим=${r.width>0&&r.height>0}`; }""",
                            label.split("\n")[0][:20])
                    except Exception:                      # noqa: BLE001
                        pass
                    results.append(Click(pid, label, ctl["cls"], "error",
                                         f"клик не прошёл: {type(exc).__name__}"
                                         + (f" — {why[-1][:120]}" if why else "")
                                         + (f" | {seen}" if seen else "")))
                    continue
                page.wait_for_timeout(SETTLE_MS)
                # App startup deliberately waits READY_TIMEOUT (25 seconds)
                # for cold imports. Allow its declared deadline plus transport
                # margin; other mutations retain the normal ten-second bound.
                timeout = 10
                if any(APP_START_ROUTE.search(request) for request in pending):
                    from bcc.features.apps_control import READY_TIMEOUT
                    timeout = READY_TIMEOUT + 5
                elif any(APP_REFRESH_ROUTE.search(request) for request in pending):
                    # Registry probes run concurrently; each can perform
                    # health and optional metrics sequentially.
                    from bcc.features.apps import PROBE_TIMEOUT
                    timeout = 2 * PROBE_TIMEOUT + 5
                deadline = time.monotonic() + timeout
                while pending and time.monotonic() < deadline:
                    page.wait_for_timeout(100)

                after_dom = _dom_fingerprint(page)
                after_url = page.url
                try:
                    after_state = _known_state(target, pid) if initial_state else {}
                except Exception:
                    after_state = {}
                verdict, detail = _classify(failures=failures, console=console,
                    page_errors=page_errors, requests=requests, responses=responses,
                    new_dialogs=_visible_texts(page, 'dialog[open], .modal, [role=dialog]') - before_dialogs,
                    new_toasts=_visible_texts(page, '#toast-root .toast-msg') - before_toasts,
                    validation=validation, dom_changed=after_dom != before_dom,
                    url_changed=after_url != before_url, read_responses=read_responses,
                    native_dialogs=native_dialogs, incomplete_requests=pending,
                    unchanged_state=initial_state if initial_state == after_state else None)

                results.append(Click(pid, label, ctl["cls"], verdict, detail,
                                     sorted(set(requests))[:5], sorted(set(console + page_errors))[:5],
                                     sorted(set(responses + read_responses))[:5], initial_state))

        browser.close()
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--pages", default="")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args()

    import tempfile
    tmp = None
    if args.data_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="bcc-ui-sweep-")
        data_dir = Path(tmp.name) / "data"
    else:
        data_dir = args.data_dir

    wanted = [p.strip() for p in args.pages.split(",") if p.strip()] or page_ids()

    app = LiveApp(data_dir).start()
    try:
        clicks = sweep(app, wanted, headed=args.headed)
    finally:
        app.stop()
        if tmp is not None:
            tmp.cleanup()

    counts: dict[str, int] = {}
    for c in clicks:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1

    print(f"страниц: {len(wanted)}   нажатий: {len(clicks)}")
    for verdict in sorted(counts):
        print(f"  {verdict:18s} {counts[verdict]}")

    dead = [c for c in clicks if c.verdict == "dead"]
    errs = [c for c in clicks if c.verdict == "error"]
    if dead:
        print("\nМЁРТВЫЕ НАЖАТИЯ:")
        for c in dead:
            print(f"  {c.page:20s} {c.label}")
    if errs:
        print("\nОШИБКИ:")
        for c in errs:
            print(f"  {c.page:20s} {c.label}: {c.detail[:120]}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "pages": wanted,
            "counts": counts,
            "clicks": [asdict(c) for c in clicks],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nзаписано: {args.json}")

    # Инструмент НЕ выносит вердикт приёмки сам: «мёртвая» кнопка может
    # оказаться законной (запись в localStorage), а «works» — косметикой.
    # Решение принимает человек по отчёту; код возврата говорит только о том,
    # нашлось ли что разбирать.
    return 1 if (dead or errs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
