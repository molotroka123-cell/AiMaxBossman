"""Владельческие сценарии 61–65: правка сайта попадает туда, куда владелец ткнул.

Пять вопросов, на которые владелец обязан получить ответ прежде, чем доверит
Веб-дизайнеру свой сайт:

* 61 — правка попадает В ТОТ САМЫЙ узел с ПЕРВОГО клика настоящей мышью, а
  соседняя приманка остаётся нетронутой;
* 62 — съехавшая цель (чужой номер, чужой тег, чужая версия) даёт НАЗВАННЫЙ
  отказ и НОЛЬ изменений в сохранённом коде, а не тычок наугад;
* 63 — повтор той же правки не удваивает элемент;
* 64 — срыв посреди правки не оставляет проект полуразобранным, и убийство
  процесса не рвёт файл сайта пополам; остаток объявлен в реестре полем
  `owner_gap` и обязан дословно прозвучать в отказе;
* 65 — исходник, который дал владелец, сохраняет верность: комментарии,
  отступы и чужие атрибуты не переписываются целиком.

ГЛУБИНА УЛИКИ. Все пять идут через НАСТОЯЩИЙ HTTP продукта: в дочернем процессе
поднимается `bcc.api.create_app` на uvicorn, и сценарий разговаривает с ним по
сети ровно теми же запросами, что шлёт панель владельца. 61 вдобавок ведёт
НАСТОЯЩИЙ Chromium: вход в панель, живое превью в кадре, клик мышью по экранной
точке элемента. Вызовы функций `web_designer_dom` напрямую сюда не годятся:
между функцией и владельцем лежат маршрут, блокировка проекта, сверка версии и
атомарная запись — именно там и живут беды, которые он увидит.

ПОЧЕМУ КЛИК СЧИТАЕТСЯ НАСТОЯЩЕЙ МЫШЬЮ. Кадр превью отдан с
`sandbox allow-scripts`, поэтому Chromium уводит его в отдельный процесс, а
панель показывает кадр уменьшенным CSS-трансформом. Замер 19.09: `frame.click()`
в такой связке не доставляет события вовсе — инспектор остался пуст (0 строк).
Поэтому экранная точка считается из геометрии кадра и масштаба, а перед нажатием
указатель подводится к цели, пока кадр не подтвердит наведение своим
`data-bd-hover`. Это не обход проверки, а то, что делает рукой владелец.

Ключи и секреты здесь не нужны и не берутся. Токен входа в панель продукт
выписывает себе сам в каталог данных прогона и живёт он ровно столько, сколько
живёт временный каталог.

Тяжёлые зависимости (`bcc.*`, playwright, uvicorn) импортируются ВНУТРИ функций,
а способности объявлены в реестре: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности раннер обязан
отдать честный вердикт вместо исполнения.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "bossman-core"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402

# --------------------------------------------------------------------------
# НАСТОЯЩИЙ HTTP продукта в дочернем процессе.
#
# Скрипт ниже не подменяет ничего: он зовёт ту же фабрику `create_app`, что и
# `python -m bcc`, и отдаёт приложение uvicorn. `start_workers=False` — это НЕ
# урезание продукта, а отказ поднимать фоновые петли движка, которым в этих
# пяти сценариях нечего делать: ни один из них не создаёт задач. Маршруты
# Веб-дизайнера, вход, CSRF и шина событий при этом настоящие.
# --------------------------------------------------------------------------
_SERVE = """import os, pathlib, sys
from bcc.api import create_app
from bcc.config import Settings
data, port = pathlib.Path(sys.argv[1]), int(sys.argv[2])
# Хранилище — СВОЁ, а не общая база прогона: Settings по умолчанию подхватывает
# DATABASE_URL из окружения, и тогда эти строки писали бы одобрения и сессии в
# ту же базу, которую в этом же прогоне меряют соседние сценарии. sqlite в
# каталоге данных — то, что получает владелец при обычной установке, и оно не
# делит состояние ни с кем.
settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}")
app = create_app(settings, announce_token=False, start_workers=False)
fd = os.open(data / 'owner-token', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write(app.state.svc.auth.token)
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', timeout_graceful_shutdown=2)
"""

#: Образец сайта владельца. Нарочно неряшлив: комментарий, лишние пробелы,
#: собственный атрибут и кириллическое значение — всё то, что «умный»
#: переформатирующий сериализатор молча потерял бы.
OWNER_SITE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Кафе Владельца</title>
<!-- комментарий владельца: НЕ ТРОГАТЬ -->
<style>
  .hero   {  color : #102030 ;  }
</style>
</head>
<body>
  <section class="hero" data-owner="да">
    <h1 id="zagolovok" style="padding:40px;background:#eef;font:40px sans-serif">СТАРЫЙ ЗАГОЛОВОК</h1>
    <p ID='primanka'   class=lead data-keep style="padding:40px;background:#fee">ПРИМАНКА РЯДОМ С ЦЕЛЬЮ</p>
  </section>
  <footer>
     <span>   отступы     владельца   </span>
  </footer>
</body>
</html>
"""


class _Product:
    """Один живой экземпляр продукта на модуль. Никаких фоновых присмотров."""

    def __init__(self) -> None:
        self.data = Path(tempfile.mkdtemp(prefix="bossman-os61-"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log = None

    # --------------------------------------------------------------- жизнь
    def start(self, timeout: float = 60.0) -> "_Product":
        import httpx  # noqa: PLC0415

        env = dict(os.environ, BCC_DATA_DIR=str(self.data),
                   PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "bossman-core"),
                                               str(ROOT / "command-center")]))
        self._log = (self.data / "server.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-c", _SERVE, str(self.data), str(self.port)],
            env=env, cwd=str(ROOT), stdout=self._log, stderr=subprocess.STDOUT)
        until = time.monotonic() + timeout
        with httpx.Client(trust_env=False, timeout=3) as client:
            while time.monotonic() < until:
                if self.process.poll() is not None:
                    raise AssertionError("HTTP продукта не поднялся: "
                                         + self.log_tail())
                try:
                    if client.get(self.url + "/").status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise AssertionError(f"HTTP продукта не ответил за {timeout:g} с: {self.log_tail()}")

    def log_tail(self, limit: int = 1200) -> str:
        try:
            return (self.data / "server.log").read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return "журнал сервера недоступен"

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            self._log = None

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self.data, ignore_errors=True)

    # -------------------------------------------------------------- клиент
    def client(self, label: str):
        import httpx  # noqa: PLC0415

        client = httpx.Client(base_url=self.url, trust_env=False, timeout=30)
        auth = client.post("/api/login", json={"token": self.token(), "label": label})
        if auth.status_code != 200:
            raise AssertionError(f"вход в продукт отклонён: {auth.status_code} {auth.text[:200]}")
        client.headers["X-BCC-CSRF"] = auth.json()["csrf"]
        return client

    def token(self) -> str:
        return (self.data / "owner-token").read_text(encoding="utf-8")


_PRODUCT: _Product | None = None


def _runtime_missing() -> str:
    """Чего не хватает, чтобы поднять HTTP продукта. Пустая строка — всё есть."""
    import importlib.util  # noqa: PLC0415

    missing = [name for name in ("fastapi", "uvicorn", "httpx", "sqlalchemy", "pydantic")
               if importlib.util.find_spec(name) is None]
    return ", ".join(missing)


def _product(ctx) -> _Product:
    """Живой продукт. Нет рантайма — ЧЕСТНЫЙ тупик, а не заглушка на его месте."""
    global _PRODUCT
    missing = _runtime_missing()
    if missing:
        ctx.not_proven("в среде нет HTTP-рантайма продукта (" + missing
                       + "): цепочку владельца через настоящий HTTP пройти нечем, "
                         "а подменять его вызовом функции значит менять предмет замера")
    if _PRODUCT is None:
        _PRODUCT = _Product().start()
        atexit.register(_PRODUCT.close)
    return _PRODUCT


def _browser_missing() -> str:
    import importlib.util  # noqa: PLC0415

    if importlib.util.find_spec("playwright") is None:
        return "playwright"
    return ""


# ------------------------------------------------------------------ помощники
def _project(client, name: str, html: str) -> tuple[int, int]:
    """Создать проект и залить в него исходник владельца. Возвращает (id, версия)."""
    created = client.post("/api/web-designer/projects",
                          json={"name": name, "prompt": "", "template": "blank"})
    if created.status_code != 200:
        raise AssertionError(f"проект не создан: {created.status_code} {created.text[:200]}")
    pid = int(created.json()["meta"]["id"])
    saved = client.put(f"/api/web-designer/projects/{pid}/code",
                       json={"html": html, "note": "исходник владельца"})
    if saved.status_code != 200:
        raise AssertionError(f"исходник не сохранён: {saved.status_code} {saved.text[:200]}")
    return pid, int(saved.json()["meta"]["version"])


def _state(client, pid: int) -> dict:
    answer = client.get(f"/api/web-designer/projects/{pid}")
    if answer.status_code != 200:
        raise AssertionError(f"состояние проекта не прочитано: {answer.status_code}")
    return answer.json()


def _bd_id(client, pid: int, tag: str) -> str:
    """Номер, который продукт выдал тегу В ПРЕВЬЮ — ровно его вернёт пикер."""
    preview = client.get(f"/api/web-designer/projects/{pid}/preview")
    if preview.status_code != 200:
        raise AssertionError(f"превью не отдано: {preview.status_code}")
    opening = re.search(rf"<{tag}[^>]*>", preview.text)
    if opening is None:
        raise AssertionError(f"в превью нет тега <{tag}>")
    found = re.search(r'data-bd-id="(bd-\d+)"', opening.group(0))
    if found is None:
        raise AssertionError(f"продукт не пронумеровал <{tag}> в превью: {opening.group(0)[:120]}")
    return found.group(1)


def _edit(client, pid: int, **body):
    return client.post(f"/api/web-designer/projects/{pid}/edit", json=body)


def _detail(response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", ""))[:200]
    except ValueError:
        return response.text[:200]


# ================================================= OS-61 — ТОТ САМЫЙ УЗЕЛ
@scenario(id="OS-61", depth=INSTALLED_PRODUCT)
def os61_edit_lands_in_the_clicked_node(ctx) -> None:
    """Один клик настоящей мышью — и правка ушла В ТОТ узел, по которому кликнули.

    Цепочка ровно владельческая: вход в панель → страница Веб-дизайнера →
    живое превью в кадре → клик по заголовку → поле «Текст» → «Применить».
    Исход проверяется не ответом кнопки, а ПОСТ-СОСТОЯНИЕМ сохранённого кода,
    прочитанным отдельным HTTP-запросом мимо браузера.

    Отрицательные контроли, без которых «попало» ничего не значит: приманка,
    лежащая вплотную под целью, не выделена и не изменена, второго заголовка в
    коде не появилось, а до нажатия цель ещё несла старый текст.
    """
    missing = _browser_missing()
    if missing:
        ctx.owner_hardware_required(f"нет браузера владельца ({missing}): "
                                    "клик по живому превью выполнять нечем")
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from capabilities import browser_executable  # noqa: PLC0415

    product = _product(ctx)
    ctx.reached_installed_product(
        f"HTTP продукта {product.url} (bcc.api.create_app на uvicorn) + настоящий Chromium")
    client = product.client("OS-61")
    pid, base = _project(client, "Кафе OS-61", OWNER_SITE)

    before = _state(client, pid)
    ctx.negative("до нажатия цель ещё несёт СТАРЫЙ текст",
                 "СТАРЫЙ ЗАГОЛОВОК" in before["code"] and "ПРАВКА ВЛАДЕЛЬЦА" not in before["code"],
                 f"версия={before['meta']['version']}")

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=browser_executable())
        try:
            context = browser.new_context(viewport={"width": 1500, "height": 950})
            page = context.new_page()
            page.goto(product.url + "/", wait_until="domcontentloaded")
            page.locator("#login-token").fill(product.token())
            page.locator("#login-submit").click()
            page.locator("#shell:not([hidden])").wait_for(timeout=30000)
            page.goto(product.url + f"/#/web_designer?project={pid}",
                      wait_until="domcontentloaded")
            guest = _live_frame(page)
            ctx.positive("живое превью владельца ожило и пикер продукта в нём установлен",
                         bool(guest.evaluate("() => !!window.__bdPicker")),
                         f"кадр={guest.url.split('?')[0]}")

            picked = _click_in_preview(page, "h1", expect="h1")
            ctx.positive("клик настоящей мышью выделил ИМЕННО заголовок",
                         picked["info"].lower().startswith("h1"),
                         f"инспектор показал {picked['info']!r}, точка={picked['spot']}")
            marked = _live_frame(page).evaluate(
                """() => { const n = document.querySelectorAll('[data-bd-selected]');
                     return {count: n.length, who: n[0] ? n[0].tagName + '#' + n[0].id : null}; }""")
            ctx.negative("приманка рядом с целью НЕ выделена: помечен ровно один узел",
                         marked["count"] == 1 and marked["who"] == "H1#zagolovok",
                         f"помечено={marked}")

            applied = page.evaluate(
                """() => { for (const row of document.querySelectorAll('div.bd-row')) {
                     const label = row.querySelector('label');
                     if (!label || label.textContent.trim() !== 'Текст') continue;
                     const field = row.querySelector('input[type=text]');
                     if (!field) return 'нет поля';
                     field.value = 'ПРАВКА ВЛАДЕЛЬЦА';
                     field.dispatchEvent(new Event('input', {bubbles: true}));
                     const button = Array.from(row.querySelectorAll('button'))
                       .find((b) => b.textContent.trim() === 'Применить');
                     if (!button) return 'нет кнопки';
                     button.click();
                     return 'нажато'; }
                   return 'инспектор пуст'; }""")
            ctx.positive("в инспекторе есть поле «Текст» и кнопка «Применить»",
                         applied == "нажато", f"инспектор ответил {applied!r}")
        finally:
            browser.close()

    after = _wait_version(client, pid, base)
    ctx.positive("сохранённый код несёт правку ИМЕННО в выбранном узле",
                 '<h1 id="zagolovok"' in after["code"]
                 and ">ПРАВКА ВЛАДЕЛЬЦА</h1>" in after["code"],
                 f"версия {before['meta']['version']} → {after['meta']['version']}")
    ctx.negative("приманка, лежащая вплотную под целью, не тронута",
                 "ПРИМАНКА РЯДОМ С ЦЕЛЬЮ" in after["code"],
                 "текст соседнего <p> сохранён дословно")
    ctx.negative("второго заголовка от правки не появилось",
                 after["code"].count("<h1") == 1 and after["code"].count("ПРАВКА ВЛАДЕЛЬЦА") == 1,
                 f"<h1>={after['code'].count('<h1')}")
    ctx.negative("старый текст цели в коде НЕ остался",
                 "СТАРЫЙ ЗАГОЛОВОК" not in after["code"])


def _live_frame(page, selector: str = "h1", timeout: float = 20.0):
    """Кадр превью, который УЖЕ можно опрашивать.

    Кадр ищется по элементу панели, а не по подстроке в url: пока переход не
    зафиксирован, `frame.url` пуст, и поиск по url объявил бы «кадра нет» там,
    где кадр есть. Панель перезагружает кадр после правки, поэтому найденный
    кадр может отвалиться прямо в руках — тогда берём его заново.
    """
    from playwright.sync_api import Error as PWError  # noqa: PLC0415

    last = ""
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            handle = page.wait_for_selector("iframe.bd-frame", timeout=10000)
            frame = handle.content_frame()
            if frame is None or not frame.url:
                page.wait_for_timeout(200)
                continue
            frame.wait_for_selector(selector, timeout=5000)
            frame.evaluate("() => 1")
            return frame
        except PWError as exc:
            last = f"{type(exc).__name__}: {exc}"[:180]
            page.wait_for_timeout(300)
    raise AssertionError(f"кадр живого превью не устоялся за {timeout:g} с: {last}")


def _click_in_preview(page, selector: str, *, expect: str, tries: int = 5) -> dict:
    """Настоящий клик мышью по элементу ВНУТРИ кадра превью.

    Возвращает {'info': строка инспектора, 'spot': экранная точка}. Промах —
    это исключение с точкой и с тем, что выделилось: «строки нет» и «выбрано
    другое» — разные беды, и путать их нельзя.
    """
    from playwright.sync_api import Error as PWError  # noqa: PLC0415

    seen: list[str] = []
    spot: dict = {}
    for _ in range(tries):
        guest = _live_frame(page, selector)
        box = guest.evaluate(
            """(sel) => { const el = document.querySelector(sel);
                 if (!el) return null;
                 el.scrollIntoView({block: 'center'});
                 const r = el.getBoundingClientRect();
                 return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }""", selector)
        if not box:
            page.wait_for_timeout(200)
            continue
        page.evaluate("() => document.querySelector('iframe.bd-frame')"
                      ".scrollIntoView({block: 'center'})")
        spot = page.evaluate(
            """(box) => { const f = document.querySelector('iframe.bd-frame');
                 const r = f.getBoundingClientRect();
                 const scale = f.offsetWidth ? r.width / f.offsetWidth : 1;
                 return {x: r.x + box.x * scale, y: r.y + box.y * scale,
                         vw: window.innerWidth, vh: window.innerHeight, scale}; }""", box)
        if not (0 <= spot["x"] <= spot["vw"] and 0 <= spot["y"] <= spot["vh"]):
            page.evaluate("(s) => window.scrollBy(0, s.y - s.vh / 2)", spot)
            continue
        # Наведение ДО нажатия: кадр живёт в отдельном процессе, и первое
        # событие по свежей раскладке Chromium разрешает асинхронно — оно
        # теряется. Готовность объявляет сам кадр своим data-bd-hover.
        for nudge in range(40):
            page.mouse.move(spot["x"] + (nudge % 3) - 1, spot["y"] + ((nudge // 3) % 3) - 1)
            page.wait_for_timeout(50)
            try:
                if guest.evaluate("(sel) => { const el = document.querySelector(sel);"
                                  " return !!el && el.hasAttribute('data-bd-hover'); }", selector):
                    break
            except PWError:
                guest = _live_frame(page, selector)
        page.mouse.move(spot["x"], spot["y"])
        page.mouse.click(spot["x"], spot["y"])
        until = time.monotonic() + 3
        while time.monotonic() < until:
            info = page.evaluate("() => { const n = document.querySelector('div.bd-elinfo');"
                                 " return n ? n.textContent.trim() : ''; }")
            if info:
                seen.append(info)
                if info.lower().startswith(expect):
                    return {"info": info, "spot": spot}
                break
            page.wait_for_timeout(100)
    raise AssertionError(
        f"клик по {selector!r} не выделил {expect!r}: инспектор показывал {seen!r}, "
        f"последняя точка {spot}. Это НЕ «строки нет» — это промах выделения")


def _wait_version(client, pid: int, was: int, timeout: float = 15.0) -> dict:
    """Дождаться, пока продукт ПРИМЕТ правку: версия ушла вперёд."""
    until = time.monotonic() + timeout
    state = _state(client, pid)
    while time.monotonic() < until:
        if int(state["meta"]["version"]) != int(was):
            return state
        time.sleep(0.2)
        state = _state(client, pid)
    raise AssertionError(f"продукт не сохранил правку: версия осталась {was}")


# ==================================================== OS-62 — СЪЕХАВШАЯ ЦЕЛЬ
@scenario(id="OS-62", depth=INSTALLED_PRODUCT)
def os62_drifted_target_refuses_with_zero_edits(ctx) -> None:
    """Переименованная и съехавшая цель: НАЗВАННЫЙ отказ и НОЛЬ изменений.

    Три вида дрейфа, которые случаются у владельца по-настоящему:

    * номер из старого превью указывает в пустоту (элемент удалён);
    * номер жив, но показывает уже на ЧУЖОЙ тег (код переверстали);
    * код изменился во второй вкладке — выделение построено на старой версии.

    ИЗМЕРЕНО НА ЭТОЙ ВЕТКЕ через настоящий HTTP: 404 / 404 / 409, и после всех
    трёх сохранённый код БАЙТ В БАЙТ прежний, а версия не сдвинулась. Проверка
    «код не изменился» слепа сама по себе, поэтому парная половина обязательна:
    тем же запросом на верную цель правка ПРОХОДИТ.
    """
    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url}: POST /api/web-designer/.../edit")
    client = product.client("OS-62")
    pid, base = _project(client, "Дрейф OS-62", OWNER_SITE)
    target = _bd_id(client, pid, "h1")
    stored = _state(client, pid)["code"]

    lost = _edit(client, pid, op="text", bd_id="bd-999999", text="ВЗЛОМ", base_version=base)
    ctx.negative("номер из старого превью, указывающий в пустоту, отвергнут по имени",
                 lost.status_code == 404 and "не найден" in _detail(lost),
                 f"HTTP {lost.status_code}: {_detail(lost)}")

    renamed = _edit(client, pid, op="text", bd_id=target, tag="p", text="ВЗЛОМ",
                    base_version=base)
    ctx.negative("номер, съехавший на ЧУЖОЙ тег, отвергнут и назван устаревшим",
                 renamed.status_code == 404 and "устарело" in _detail(renamed),
                 f"HTTP {renamed.status_code}: {_detail(renamed)}")

    stale = _edit(client, pid, op="text", bd_id=target, tag="h1", text="ВЗЛОМ",
                  base_version=base - 1)
    ctx.negative("выделение, сделанное на СТАРОЙ версии кода, отвергнуто с 409",
                 stale.status_code == 409 and "код изменился" in _detail(stale),
                 f"HTTP {stale.status_code}: {_detail(stale)}")

    nowhere = _edit(client, pid, op="text", path="h1#takogo-net", text="ВЗЛОМ",
                    base_version=base)
    ctx.negative("путь на переименованный якорь не подбирает похожий элемент",
                 nowhere.status_code == 404 and "не найден" in _detail(nowhere),
                 f"HTTP {nowhere.status_code}: {_detail(nowhere)}")

    after_refusals = _state(client, pid)
    ctx.negative("после ЧЕТЫРЁХ отказов сохранённый код байт в байт прежний",
                 after_refusals["code"] == stored,
                 f"длина было={len(stored)} стало={len(after_refusals['code'])}")
    ctx.negative("и версия проекта не сдвинулась — тычка наугад не было",
                 int(after_refusals["meta"]["version"]) == base,
                 f"версия={after_refusals['meta']['version']} (была {base})")
    ctx.negative("«ВЗЛОМ» не встречается в коде ни разу",
                 "ВЗЛОМ" not in after_refusals["code"])

    # Парная половина: отказ не тотальный, законная правка проходит тем же путём.
    ok = _edit(client, pid, op="text", bd_id=target, tag="h1", text="ЗАКОННАЯ ПРАВКА",
               base_version=base)
    ctx.positive("та же ручка на ВЕРНУЮ цель принимает правку",
                 ok.status_code == 200 and ok.json()["element"]["id"] == "zagolovok",
                 f"HTTP {ok.status_code}, элемент={ok.json().get('element', {}).get('tag')}")
    final = _state(client, pid)
    ctx.positive("правка видна в сохранённом коде и ровно в выбранном узле",
                 ">ЗАКОННАЯ ПРАВКА</h1>" in final["code"]
                 and "ПРИМАНКА РЯДОМ С ЦЕЛЬЮ" in final["code"],
                 f"версия={final['meta']['version']}")


# ================================================= OS-63 — ПОВТОР НЕ УДВАИВАЕТ
@scenario(id="OS-63", depth=INSTALLED_PRODUCT)
def os63_repeated_edit_does_not_duplicate(ctx) -> None:
    """Та же правка дважды не даёт двух элементов и двух текстов.

    Владелец нажимает «Применить» второй раз — из недоверия, из-за подвисшей
    сети или просто мимо. Продукт обязан прийти в ТО ЖЕ состояние, а не
    пририсовать второй заголовок и второй подвал.

    Проверяются обе опасные операции: замена текста и замена поддерева целиком
    (`replace`), потому что именно она вставляет узлы, а не переписывает их.
    Счёт ведётся по сохранённому коду, а не по ответу ручки.
    """
    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url}: повтор правки через /edit")
    client = product.client("OS-63")
    pid, base = _project(client, "Повтор OS-63", OWNER_SITE)

    # ------------------------------------------------------------ текст
    target = _bd_id(client, pid, "h1")
    first = _edit(client, pid, op="text", bd_id=target, tag="h1",
                  text="ОДИН ЗАГОЛОВОК", base_version=base)
    ctx.positive("первая правка текста принята", first.status_code == 200,
                 f"HTTP {first.status_code}")
    once = _state(client, pid)

    again_id = _bd_id(client, pid, "h1")
    second = _edit(client, pid, op="text", bd_id=again_id, tag="h1",
                   text="ОДИН ЗАГОЛОВОК", base_version=int(once["meta"]["version"]))
    ctx.positive("повтор той же правки текста принят без ошибки",
                 second.status_code == 200, f"HTTP {second.status_code}")
    twice = _state(client, pid)
    ctx.negative("повтор НЕ удвоил ни заголовок, ни его текст",
                 twice["code"].count("<h1") == 1
                 and twice["code"].count("ОДИН ЗАГОЛОВОК") == 1,
                 f"<h1>={twice['code'].count('<h1')}, "
                 f"текстов={twice['code'].count('ОДИН ЗАГОЛОВОК')}")
    ctx.negative("и код после повтора совпадает с кодом после первой правки",
                 twice["code"] == once["code"],
                 f"длина {len(once['code'])} → {len(twice['code'])}")

    # ------------------------------------------- замена поддерева (вставка узлов)
    block = "<footer><span>НОВЫЙ ПОДВАЛ</span></footer>"
    version = int(twice["meta"]["version"])
    replaced_once = _edit(client, pid, op="replace", bd_id=_bd_id(client, pid, "footer"),
                          tag="footer", html=block, base_version=version)
    ctx.positive("замена подвала принята", replaced_once.status_code == 200,
                 f"HTTP {replaced_once.status_code}")
    after_one = _state(client, pid)
    replaced_twice = _edit(client, pid, op="replace", bd_id=_bd_id(client, pid, "footer"),
                           tag="footer", html=block,
                           base_version=int(after_one["meta"]["version"]))
    ctx.positive("повтор замены принят", replaced_twice.status_code == 200,
                 f"HTTP {replaced_twice.status_code}")
    after_two = _state(client, pid)
    ctx.negative("два одинаковых «Заменить» дают ОДИН подвал, а не два",
                 after_two["code"].count("<footer") == 1
                 and after_two["code"].count("НОВЫЙ ПОДВАЛ") == 1,
                 f"<footer>={after_two['code'].count('<footer')}, "
                 f"текстов={after_two['code'].count('НОВЫЙ ПОДВАЛ')}")
    ctx.negative("код после второй замены совпадает с кодом после первой",
                 after_two["code"] == after_one["code"])

    # КОНТРОЛЬ САМОГО СЧЁТЧИКА. «Удвоения нет» значит что-то только тогда,
    # когда тот же счёт умеет удвоение УВИДЕТЬ.
    doubled = _edit(client, pid, op="replace", bd_id=_bd_id(client, pid, "footer"),
                    tag="footer", html=block + block,
                    base_version=int(after_two["meta"]["version"]))
    ctx.positive("счёт не слеп: намеренно вставленный второй подвал ВИДЕН",
                 doubled.status_code == 200
                 and _state(client, pid)["code"].count("<footer") == 2,
                 f"HTTP {doubled.status_code}, "
                 f"<footer>={_state(client, pid)['code'].count('<footer')}")


# =============================================== OS-64 — СРЫВ НЕ РАЗБИРАЕТ ПРОЕКТ
@scenario(id="OS-64", depth=INSTALLED_PRODUCT)
def os64_aborted_edit_leaves_the_project_whole(ctx) -> None:
    """Срыв посреди правки не оставляет проект полуразобранным. Удаление корня — НАХОДКА.

    Три беды, каждая настоящая:

    * правка отвергнута на полпути (пустая замена, css-мусор, снос вложенных
      тегов без согласия) — сохранённый код обязан остаться прежним БАЙТ В БАЙТ;
    * процесс убит SIGKILL сразу после сохранения — файл сайта обязан быть
      целым документом, снимок версии обязан совпасть с текущим файлом, а
      временных огрызков `.tmp` в каталоге проекта быть не должно;
    * перезапуск обязан отдать владельцу тот же целый сайт.

    ИЗМЕРЕНО: всё перечисленное продукт держит. Осталось одно, названное в конце
    честным тупиком: «Удалить элемент» на КОРНЕ документа сносит весь сайт.
    """
    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url} + SIGKILL по живому процессу")
    client = product.client("OS-64")
    pid, base = _project(client, "Целость OS-64", OWNER_SITE)
    stored = _state(client, pid)["code"]

    aborts = {
        "пустая замена": {"op": "replace", "bd_id": _bd_id(client, pid, "h1"), "tag": "h1",
                          "html": ""},
        "css-свойство с мусором": {"op": "style", "bd_id": _bd_id(client, pid, "h1"),
                                   "tag": "h1", "props": {"color;background": "red"}},
        "имя атрибута с кавычкой": {"op": "attrs", "bd_id": _bd_id(client, pid, "h1"),
                                    "tag": "h1", "attrs": {'x" onmouseover="alert(1)': "1"}},
        "снос вложенных тегов без согласия": {"op": "text",
                                              "bd_id": _bd_id(client, pid, "section"),
                                              "tag": "section", "text": "снести всё"},
        "неизвестная операция": {"op": "взорвать", "bd_id": _bd_id(client, pid, "h1"),
                                 "tag": "h1"},
    }
    refused = {}
    for name, body in aborts.items():
        answer = _edit(client, pid, base_version=base, **body)
        refused[name] = (answer.status_code, _detail(answer))
    ctx.negative("каждый срыв отвергнут ШТАТНЫМ отказом, а не пятисотой",
                 all(400 <= code < 500 for code, _ in refused.values()),
                 json.dumps({k: v[0] for k, v in refused.items()}, ensure_ascii=False))
    ctx.negative("каждый отказ НАЗВАН словами, а не пустым телом",
                 all(detail.strip() for _, detail in refused.values()),
                 json.dumps({k: v[1][:60] for k, v in refused.items()}, ensure_ascii=False))
    survived = _state(client, pid)
    ctx.negative("после ПЯТИ срывов код байт в байт прежний и версия та же",
                 survived["code"] == stored and int(survived["meta"]["version"]) == base,
                 f"версия={survived['meta']['version']} (была {base}), "
                 f"длина={len(survived['code'])}")

    # Парная половина: согласие владельца снести вложенные теги РАБОТАЕТ.
    agreed = _edit(client, pid, op="text", bd_id=_bd_id(client, pid, "section"), tag="section",
                   text="владелец согласился", replace_children=True, base_version=base)
    ctx.positive("то же действие С ЯВНЫМ согласием владельца проходит",
                 agreed.status_code == 200
                 and "владелец согласился" in _state(client, pid)["code"],
                 f"HTTP {agreed.status_code}")

    # ----------------------------------------------- убийство процесса по живому
    pdir = product.data / "web_designer" / str(pid)
    version = int(_state(client, pid)["meta"]["version"])
    killed_edit = _edit(client, pid, op="text", bd_id=_bd_id(client, pid, "span"), tag="span",
                        text="ПЕРЕЖИЛО УБИЙСТВО", base_version=version)
    ctx.positive("правка принята прямо перед убийством процесса",
                 killed_edit.status_code == 200, f"HTTP {killed_edit.status_code}")
    saved_version = int(killed_edit.json()["meta"]["version"])
    os.kill(product.process.pid, signal.SIGKILL)
    product.process.wait(timeout=15)
    ctx.positive("процесс продукта убит по-настоящему (SIGKILL), а не остановлен мягко",
                 product.process.returncode in (-signal.SIGKILL, 137),
                 f"код возврата={product.process.returncode}")

    leftovers = [str(p.name) for p in pdir.rglob("*.tmp")]
    ctx.negative("в каталоге проекта не осталось ни одного огрызка .tmp",
                 not leftovers, f"найдено: {leftovers}")
    on_disk = (pdir / "current.html").read_text(encoding="utf-8")
    meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    snapshot = (pdir / "history" / f"v{int(meta['version'])}.html").read_text(encoding="utf-8")
    ctx.positive("файл сайта на диске — ЦЕЛЫЙ документ, а не половина",
                 on_disk.lstrip().lower().startswith("<!doctype html")
                 and on_disk.rstrip().endswith("</html>")
                 and "ПЕРЕЖИЛО УБИЙСТВО" in on_disk,
                 f"длина={len(on_disk)}")
    ctx.positive("снимок объявленной версии совпадает с текущим файлом",
                 snapshot == on_disk and int(meta["version"]) == saved_version,
                 f"meta.version={meta['version']}, ожидалась {saved_version}")

    product.start()
    restarted = product.client("OS-64-после-перезапуска")
    revived = _state(restarted, pid)
    ctx.positive("после перезапуска владелец получает тот же целый сайт",
                 revived["code"] == on_disk
                 and int(revived["meta"]["version"]) == saved_version,
                 f"версия={revived['meta']['version']}")
    ctx.positive("превью после перезапуска отдаётся, а не падает",
                 restarted.get(f"/api/web-designer/projects/{pid}/preview").status_code == 200)

    # ------------------------------ ОСТАВШИЙСЯ ВОПРОС, А НЕ ОБНАРУЖЕННАЯ ДЫРА
    # Измерено ПО ТОМУ ЖЕ ПУТИ, которым ходит владелец, а не по одному слою:
    # в настоящем Chromium клик в пустое место превью выделяет <html> (пикер
    # берёт `e.target`, web_designer_dom.py:810), кнопка «Удалить элемент»
    # (ui/pages/web_designer.js:526) шлёт op=delete, диалог обещает убрать
    # «тег», и весь сайт превращается в '<!doctype html>'.
    corpse = _project(restarted, "Корень OS-64", OWNER_SITE)
    root_pid, root_base = corpse
    root_delete = _edit(restarted, root_pid, op="delete",
                        bd_id=_bd_id(restarted, root_pid, "html"), tag="html",
                        base_version=root_base)
    wreck = _state(restarted, root_pid)["code"]
    restored = restarted.post(
        f"/api/web-designer/projects/{root_pid}/versions/{root_base}/restore")
    ctx.positive("откат к версии возвращает снесённый сайт целиком",
                 restored.status_code == 200
                 and _state(restarted, root_pid)["code"] == OWNER_SITE,
                 f"HTTP {restored.status_code}")
    if root_delete.status_code != 200 or len(wreck.strip()) > 40:
        ctx.negative("правка, сносящая ВЕСЬ документ, отвергается на рубеже продукта",
                     True, f"HTTP {root_delete.status_code}, осталось {len(wreck.strip())} симв. — "
                           "рубеж закрыт: убери owner_gap у OS-64 из канонического "
                           "реестра и обнови замороженную таблицу ожиданий")
        return
    # Провал ЛЮБОЙ проверки выше важнее невыбранного уровня строгости: честный
    # тупик заслонил бы сломанный продукт, и каркас назвал бы поломку
    # «не доказано». Поэтому сначала отдаём слово проверкам.
    broken = [check.name for check in ctx.checks if not check.ok]
    if broken:
        return
    ctx.not_proven(
        "«срыв не оставляет проект полуразобранным» выполнено НЕ ПОЛНОСТЬЮ: одно "
        f"подтверждённое нажатие «Удалить элемент» на корне документа оставляет "
        f"от сайта {wreck.strip()!r} ({len(wreck.strip())} символов). Измерено по "
        "владельческому пути целиком: клик в пустое место превью выделяет <html> "
        "(пикер берёт e.target, command-center/bcc/web_designer_dom.py:810-813), "
        "кнопка шлёт op=delete (command-center/ui/pages/web_designer.js:526-530), "
        "диалог обещает убрать «тег», а "
        "command-center/bcc/web_designer_dom.py:609-615 op_delete отказывает "
        "только при parent is None — у <html> родитель есть, это служебный корень "
        "разбора. Это НЕ тихая потеря: диалог называет тег, откат к версии "
        "возвращает сайт целиком (измерено выше), — а невыбранный уровень "
        "строгости. Отказать в сносе <html>/<body> или сказать в диалоге «будет "
        "удалён ВЕСЬ сайт» — решение владельца")


# ================================================== OS-65 — ВЕРНОСТЬ ИСХОДНИКУ
@scenario(id="OS-65", depth=INSTALLED_PRODUCT)
def os65_owner_source_keeps_its_shape(ctx) -> None:
    """Исходник владельца сохраняет верность: правится ОДНА строка, а не весь файл.

    Владелец приносит свой HTML — с комментарием, с собственными атрибутами, с
    отступами, которые ему дороги. Продукт обязан вернуть ему тот же файл с
    одним изменённым местом, а не «причёсанный» пересобранный документ.

    Мера строгая и проверяемая: построчный дифф сохранённого кода ДО и ПОСЛЕ
    правки обязан содержать ровно одну убранную и одну добавленную строку.
    """
    import difflib  # noqa: PLC0415

    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url}: PUT /code → POST /edit → GET /")
    client = product.client("OS-65")
    pid, base = _project(client, "Верность OS-65", OWNER_SITE)

    stored = _state(client, pid)["code"]
    ctx.positive("продукт вернул исходник владельца БАЙТ В БАЙТ тем, что тот дал",
                 stored == OWNER_SITE,
                 f"длина исходника={len(OWNER_SITE)}, сохранено={len(stored)}")

    edited = _edit(client, pid, op="text", bd_id=_bd_id(client, pid, "h1"), tag="h1",
                   text="НОВЫЙ ЗАГОЛОВОК", base_version=base)
    ctx.positive("точечная правка принята", edited.status_code == 200,
                 f"HTTP {edited.status_code}")
    after = _state(client, pid)["code"]

    changed = [line for line in difflib.unified_diff(stored.splitlines(),
                                                     after.splitlines(), n=0, lineterm="")
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    ctx.positive("изменилась РОВНО одна строка документа",
                 len(changed) == 2 and changed[0].startswith("-") and changed[1].startswith("+"),
                 f"изменённых строк={len(changed)}: {changed[:4]}")

    ctx.negative("комментарий владельца не переписан и не выброшен",
                 "<!-- комментарий владельца: НЕ ТРОГАТЬ -->" in after)
    ctx.negative("пробелы внутри чужого <style> не «причёсаны»",
                 "  .hero   {  color : #102030 ;  }" in after)
    ctx.negative("собственный атрибут владельца с кириллицей уцелел",
                 'data-owner="да"' in after)
    ctx.negative("отступы в подвале сохранены дословно",
                 "<span>   отступы     владельца   </span>" in after)
    ctx.negative("соседний тег отдан ДОСЛОВНО — с его кавычками, пробелами и "
                 "атрибутом без значения",
                 "<p ID='primanka'   class=lead data-keep "
                 'style="padding:40px;background:#fee">' in after,
                 "тег владельца не пересобран из полей")
    ctx.negative("объявление документа и язык страницы не переписаны",
                 after.startswith("<!DOCTYPE html>\n<html lang=\"ru\">"),
                 f"начало={after[:40]!r}")
    ctx.negative("служебная нумерация превью НЕ просочилась в сохранённый код",
                 "data-bd-id" not in after and "__bdPicker" not in after,
                 "ни одного data-bd-id в хранимом документе")

    # КОНТРОЛЬ САМОЙ МЕРЫ: дифф не слеп — он видит правку, которая ДОЛЖНА
    # затронуть много строк.
    wide = client.put(f"/api/web-designer/projects/{pid}/code",
                      json={"html": OWNER_SITE.replace("\n", "\n<!-- слой -->\n"),
                            "note": "широкая правка",
                            "base_version": int(_state(client, pid)["meta"]["version"])})
    wide_diff = [line for line in difflib.unified_diff(after.splitlines(),
                                                       _state(client, pid)["code"].splitlines(),
                                                       n=0, lineterm="")
                 if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    ctx.positive("мера не слепа: широкая правка даёт МНОГО изменённых строк",
                 wide.status_code == 200 and len(wide_diff) > 10,
                 f"HTTP {wide.status_code}, изменённых строк={len(wide_diff)}")
