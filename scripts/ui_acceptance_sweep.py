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
  real_effect        — ушёл запрос, меняющий состояние (POST/PUT/PATCH/DELETE)
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


@dataclass
class Click:
    page: str
    label: str
    selector: str
    verdict: str
    detail: str = ""
    requests: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)


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
    page.wait_for_timeout(PAGE_SETTLE_MS)


def _dom_fingerprint(page) -> str:
    html = page.evaluate("() => (document.querySelector('#view')||document.body).innerHTML")
    return hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()


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
        requests: list[str] = []
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
        page.on("request", lambda r: requests.append(f"{r.method} {r.url}")
                if r.method in ("POST", "PUT", "PATCH", "DELETE") else None)
        failures: list[str] = []
        page.on("response", lambda r: failures.append(f"{r.status} {r.url}")
                if r.status >= 500 else None)

        page.goto(app.url + "/", wait_until="domcontentloaded")
        page.fill("#login-token", app.svc.auth.token)
        page.click("#login-submit")
        page.wait_for_selector("#shell:not([hidden])", timeout=20000)

        for pid in pages:
            try:
                _fresh_page(page, app, pid)
                controls = _visible_controls(page)
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

                console.clear(); requests.clear(); failures.clear()
                before_dom = _dom_fingerprint(page)
                before_url = page.url
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

                after_dom = _dom_fingerprint(page)
                after_url = page.url
                dialog = page.evaluate(
                    "() => !!document.querySelector('dialog[open], .modal:not([hidden]), "
                    "[role=dialog]:not([hidden])')")

                if failures:
                    verdict, detail = "error", "; ".join(sorted(set(failures))[:3])
                elif console:
                    verdict, detail = "error", "; ".join(sorted(set(console))[:3])
                elif requests:
                    verdict, detail = "real_effect", "; ".join(sorted(set(requests))[:3])
                elif dialog or after_url != before_url:
                    verdict, detail = "opens_feature", (
                        "диалог" if dialog else f"переход {after_url.split('#')[-1]}")
                elif after_dom != before_dom:
                    verdict, detail = "works", "разметка изменилась"
                else:
                    verdict, detail = "dead", "ни разметки, ни адреса, ни запроса, ни консоли"

                results.append(Click(pid, label, ctl["cls"], verdict, detail,
                                     sorted(set(requests))[:5], sorted(set(console))[:5]))

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
