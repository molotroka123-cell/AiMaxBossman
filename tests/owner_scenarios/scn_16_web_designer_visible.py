"""Владельческие сценарии 66–70: что владелец ВИДИТ на своём экране.

Пять вопросов про экран, а не про ответ ручки:

* 66 — визуальная проверка ловит НАСТОЯЩЕЕ расхождение в пикселях, а не
  сравнивает нули с нулями;
* 67 — чужая страница в превью не дотягивается ни до API владельца, ни до его
  файлов, ни до локальных адресов;
* 68 — на телефонной ширине владельческие органы управления не уезжают за край
  и остаются нажимаемыми;
* 69 — неудавшееся действие показано КАК ОТКАЗ, с кодом и причиной, и оно
  действительно не состоялось;
* 70 — живая лента доносит ход работы до открытой панели и не несёт в себе
  ключ владельца.

ГЛУБИНА УЛИКИ. Все пять — настоящий HTTP продукта в дочернем процессе плюс
настоящий Chromium: вход в панель, живой кадр превью, настоящие снимки экрана,
настоящий WebSocket. Ни один из ответов не подделан и ни один не вычислен
функцией в этом же процессе.

ПОЧЕМУ ПИКСЕЛИ СЧИТАЕТ БРАУЗЕР. В корневом CI нет ни PIL, ни другого
декодировщика PNG (BL-085: ставятся только pytest/pytest-timeout/psutil/httpx/
pyyaml). Снимок вносится в холст ТОГО ЖЕ Chromium и сравнивается поканально —
это те самые пиксели, которые видит владелец, а не пересказ о них. Сравнение
сопровождается счётом «чернил»: картинка без единого непустого пикселя
объявляется негодной уликой, потому что ноль против нуля совпадает всегда.

Тяжёлые зависимости импортируются ВНУТРИ функций, способности объявлены в
реестре: без браузера или без продукта раннер обязан отдать честный вердикт,
а не исполнить сценарий на заглушке.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
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

# Тот же запуск НАСТОЯЩЕГО продукта, что и в scn_15. Повторён намеренно:
# общий модуль-помощник рядом со сценариями каркас загрузил бы как второй
# экземпляр с собственным реестром реализаций — и привязка сценариев
# разъехалась бы на ровном месте.
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

#: Сайт владельца для снимков: крупные плашки, чтобы смена цвета была видна
#: глазом, а не только измерителем.
PIXEL_SITE = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
              "<title>Пиксели</title><style>body{margin:0;background:#ffffff}"
              "h1{margin:0;padding:40px;color:#102030;font:44px/1.2 sans-serif}"
              "p{margin:0;padding:40px;background:#eef;font:22px sans-serif}</style>"
              "</head><body><h1 id=\"z\">ЗАГОЛОВОК ВЛАДЕЛЬЦА</h1>"
              "<p id=\"d\">строка ниже</p></body></html>")

#: Страница-нарушитель. Это ЧУЖОЙ код: его пишет модель, приносит владелец,
#: генерирует шаблон. Она честно докладывает в DOM, что у неё получилось.
ESCAPE_PROBE = (
    "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
    "<title>Побег</title></head><body><h1 id=\"z\">ПРОБА ПЕСОЧНИЦЫ</h1>"
    "<pre id=\"out\">ждём</pre><script>"
    "(async () => { const r = {};"
    " try { r.cookie = document.cookie; } catch (e) { r.cookie = 'THROW:' + e.name; }"
    " try { r.storage = String(!!window.localStorage); } catch (e) { r.storage = 'THROW:' + e.name; }"
    " try { const q = await fetch('/api/web-designer/projects', {credentials: 'include'});"
    "       r.api = q.status + ':' + (await q.text()).slice(0, 80); }"
    " catch (e) { r.api = 'THROW:' + e.name; }"
    " try { const q = await fetch('/api/settings', {credentials: 'include'});"
    "       r.settings = q.status + ':' + (await q.text()).slice(0, 80); }"
    " catch (e) { r.settings = 'THROW:' + e.name; }"
    " try { const q = await fetch('file:///etc/passwd');"
    "       r.file = q.status + ':' + (await q.text()).slice(0, 60); }"
    " catch (e) { r.file = 'THROW:' + e.name; }"
    " try { const q = await fetch('__LOCAL__/api/health');"
    "       r.local = q.status + ':' + (await q.text()).slice(0, 60); }"
    " catch (e) { r.local = 'THROW:' + e.name; }"
    " try { r.parent = String(window.parent.location.href); }"
    " catch (e) { r.parent = 'THROW:' + e.name; }"
    " document.getElementById('out').textContent = JSON.stringify(r); })();"
    "</script></body></html>")

#: Поканальный счёт различий ДВУХ снимков в холсте того же Chromium.
#: `ink` — сколько пикселей первой картинки вообще не белые: улика о том, что
#: сравнивалось изображение, а не пустота.
_DIFF_JS = """async ([first, second]) => {
  const load = (src) => new Promise((ok, no) => {
    const img = new Image(); img.onload = () => ok(img); img.onerror = no; img.src = src; });
  const [a, b] = await Promise.all([load(first), load(second)]);
  const w = Math.min(a.width, b.width), h = Math.min(a.height, b.height);
  if (!w || !h) return {w, h, pixels: 0, diff: 0, maxd: 0, ink: 0};
  const pull = (img) => { const c = document.createElement('canvas');
    c.width = w; c.height = h; const g = c.getContext('2d');
    g.drawImage(img, 0, 0); return g.getImageData(0, 0, w, h).data; };
  const A = pull(a), B = pull(b);
  let diff = 0, maxd = 0, ink = 0;
  for (let i = 0; i < A.length; i += 4) {
    const d = Math.max(Math.abs(A[i] - B[i]), Math.abs(A[i+1] - B[i+1]), Math.abs(A[i+2] - B[i+2]));
    if (d > 8) diff++;
    if (d > maxd) maxd = d;
    if (A[i] < 240 || A[i+1] < 240 || A[i+2] < 240) ink++;
  }
  return {w, h, pixels: w * h, diff, maxd, ink};
}"""

def _settled(page, url: str) -> None:
    """Открыть страницу и дождаться, пока она УСТОЯЛАСЬ, прежде чем снимать.

    `networkidle` и `document.fonts.ready` — сигналы самого движка, а не пауза:
    ждать «ещё немного» значило бы подгонять тайм-аут, а это запрещено.

    Зачем вообще. На чистой машине CI первый кадр успевает нарисовать текст
    ЗАПАСНЫМ шрифтом, пока fontconfig разбирается с `sans-serif`, и два снимка
    ОДНОЙ И ТОЙ ЖЕ страницы расходятся тысячами пикселей — не потому, что
    продукт что-то сделал. Измерено прогоном CI 105869147936: расхождений
    5379, максимум по каналу 238 (тёмный текст против белого фона — ровно
    «буквы есть / букв нет»). На тёплой машине разработчика этого не видно, и
    сценарий трижды подряд давал ноль.
    """
    page.goto(url, wait_until="networkidle")
    page.evaluate("() => document.fonts.ready.then(() => true)")


#: Геометрия владельческих органов управления в ОТКРЫТОЙ панели.
#: Ящик бокового меню, уехавший за край экрана, — это раскладка телефона, а не
#: поломка, поэтому в счёт идут только органы РАБОЧЕЙ области (`main`).
_CONTROLS_JS = """() => {
  const vw = window.innerWidth, vh = window.innerHeight, rows = [];
  for (const el of document.querySelectorAll(
      'main button, main select, main input, main textarea, main a[href]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.right <= 0 || r.left >= vw) continue;
    el.scrollIntoView({block: 'center'});
    const box = el.getBoundingClientRect();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    const onscreen = cx >= 0 && cx <= vw && cy >= 0 && cy <= vh;
    const hit = onscreen ? document.elementFromPoint(cx, cy) : null;
    rows.push({
      name: (el.textContent || el.getAttribute('aria-label') || el.type || el.tagName)
              .trim().slice(0, 30),
      clipped: r.right > vw + 1 || r.left < -1,
      reachable: !!hit && (hit === el || el.contains(hit) || hit.contains(el)),
    });
  }
  return {vw, docWidth: document.documentElement.scrollWidth,
          bodyWidth: document.body.scrollWidth, controls: rows};
}"""


class _Product:
    """Один живой экземпляр продукта на модуль. Никаких фоновых присмотров."""

    def __init__(self) -> None:
        self.data = Path(tempfile.mkdtemp(prefix="bossman-os66-"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log = None

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
                    raise AssertionError("HTTP продукта не поднялся: " + self.log_tail())
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

    def close(self) -> None:
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
        shutil.rmtree(self.data, ignore_errors=True)

    def token(self) -> str:
        return (self.data / "owner-token").read_text(encoding="utf-8")

    def client(self, label: str):
        import httpx  # noqa: PLC0415

        client = httpx.Client(base_url=self.url, trust_env=False, timeout=30)
        auth = client.post("/api/login", json={"token": self.token(), "label": label})
        if auth.status_code != 200:
            raise AssertionError(f"вход в продукт отклонён: {auth.status_code} {auth.text[:200]}")
        client.headers["X-BCC-CSRF"] = auth.json()["csrf"]
        return client


_PRODUCT: _Product | None = None


def _product(ctx) -> _Product:
    """Живой продукт. Нет рантайма — ЧЕСТНЫЙ тупик, а не заглушка на его месте."""
    import importlib.util  # noqa: PLC0415

    global _PRODUCT
    missing = [name for name in ("fastapi", "uvicorn", "httpx", "sqlalchemy", "pydantic")
               if importlib.util.find_spec(name) is None]
    if missing:
        ctx.not_proven("в среде нет HTTP-рантайма продукта (" + ", ".join(missing)
                       + "): экран владельца показать нечем, а рисовать его "
                         "заглушкой значит менять предмет замера")
    if _PRODUCT is None:
        _PRODUCT = _Product().start()
        atexit.register(_PRODUCT.close)
    return _PRODUCT


def _need_browser(ctx):
    """Браузер владельца или ЧЕСТНЫЙ тупик. Возвращает (sync_playwright, путь)."""
    import importlib.util  # noqa: PLC0415

    if importlib.util.find_spec("playwright") is None:
        ctx.owner_hardware_required("нет браузера владельца (playwright): "
                                    "экран, который он видит, показать нечем")
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from capabilities import browser_executable  # noqa: PLC0415

    return sync_playwright, browser_executable()


def _project(client, name: str, html: str) -> tuple[int, int]:
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


def _login(page, product: _Product) -> None:
    page.goto(product.url + "/", wait_until="domcontentloaded")
    if page.locator("#login-token").is_visible():
        page.locator("#login-token").fill(product.token())
        page.locator("#login-submit").click()
    page.locator("#shell:not([hidden])").wait_for(timeout=30000)


def _live_frame(page, selector: str, timeout: float = 20.0):
    """Кадр живого превью, который УЖЕ можно опрашивать (см. scn_15)."""
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


# ============================================ OS-66 — ПИКСЕЛИ, А НЕ ОБЕЩАНИЕ
@scenario(id="OS-66", depth=INSTALLED_PRODUCT)
def os66_visual_check_sees_real_pixel_difference(ctx) -> None:
    """Визуальная проверка видит настоящую разницу и молчит там, где её нет.

    Владельцу обещают «визуальную проверку». Проверка, которая всегда кричит,
    и проверка, которая всегда молчит, одинаково бесполезны, поэтому меряются
    ОБЕ стороны на одном и том же измерителе:

    * два снимка НЕИЗМЕНЁННОЙ страницы совпадают до пикселя;
    * снимок после настоящей правки цвета расходится с первым;
    * на обоих снимках есть непустые пиксели — сравнивались изображения, а не
      два пустых холста.
    """
    sync_playwright, executable = _need_browser(ctx)
    product = _product(ctx)
    ctx.reached_installed_product(
        f"HTTP продукта {product.url} + снимки экрана настоящего Chromium")
    client = product.client("OS-66")
    pid, base = _project(client, "Пиксели OS-66", PIXEL_SITE)
    preview_url = product.url + f"/api/web-designer/projects/{pid}/preview"

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable)
        try:
            context = browser.new_context(viewport={"width": 900, "height": 400})
            page = context.new_page()
            _login(page, product)

            shot = context.new_page()
            shot.set_viewport_size({"width": 800, "height": 300})
            # ПЕРВЫЙ кадр выбрасывается, и это не поблажка, а исправление
            # измерения. Прогон CI 105875345717 дал шум РОВНО ТОТ ЖЕ, что и до
            # ожидания шрифтов: 5379 из 240000, максимум по каналу 238 — до
            # пикселя столько же. Случайный шум так не повторяется; значит,
            # разница детерминированная, и это разогрев: самый первый рендер на
            # чистой машине рисует текст, пока система ещё разбирается со
            # шрифтом `sans-serif`, а `document.fonts.ready` о системном
            # подборе ничего не знает — он про `@font-face`, которого здесь нет.
            #
            # Продукт сравнивает ДВА состояния уже открытой страницы, а не
            # «самую первую отрисовку в жизни машины» с прогретой. Сравнивать
            # холодный кадр с тёплым — значит мерить разогрев браузера, а не
            # проверку продукта.
            frames = []
            for _ in range(4):
                _settled(shot, preview_url)
                frames.append(base64.b64encode(shot.screenshot()).decode())
            cold, still = frames[0], frames[1:]

            changed = client.post(f"/api/web-designer/projects/{pid}/edit",
                                  json={"op": "style", "path": "h1#z", "tag": "h1",
                                        "props": {"color": "#c0392b"},
                                        "base_version": base})
            ctx.positive("продукт принял настоящую правку цвета заголовка",
                         changed.status_code == 200, f"HTTP {changed.status_code}")
            _settled(shot, preview_url)
            after = base64.b64encode(shot.screenshot()).decode()

            canvas = context.new_page()
            canvas.goto("about:blank")
            def _diff(left: str, right: str) -> dict:
                return canvas.evaluate(_DIFF_JS, ["data:image/png;base64," + left,
                                                  "data:image/png;base64," + right])

            quiet = [_diff(still[0], still[1]), _diff(still[1], still[2]),
                     _diff(still[0], still[2])]
            same = max(quiet, key=lambda row: row["diff"])
            moved = _diff(still[0], after)
            # Разогрев меряется и ДОКЛАДЫВАЕТСЯ, а не просто выбрасывается:
            # так отчёт сам скажет, верна ли догадка про холодный первый кадр,
            # каким бы ни вышел итог.
            warmup = _diff(cold, still[0])
        finally:
            browser.close()

    ctx.negative("измеритель не сравнивает пустоту с пустотой: на снимке есть чернила",
                 same["ink"] > 0 and same["pixels"] > 10000,
                 f"непустых пикселей={same['ink']} из {same['pixels']}")
    ctx.negative("устоявшаяся НЕИЗМЕНЁННАЯ страница не шумит сама по себе",
                 same["diff"] * 200 < same["pixels"],
                 f"шум={same['diff']} из {same['pixels']}, максимум по каналу={same['maxd']}; "
                 f"разогрев первого кадра={warmup['diff']} (выброшен из счёта)")
    ctx.positive("после настоящей правки снимок РАСХОДИТСЯ с прежним",
                 moved["diff"] > 100 and moved["maxd"] > 16,
                 f"расхождений={moved['diff']} из {moved['pixels']}, "
                 f"максимум по каналу={moved['maxd']}")
    # Главное утверждение о ПОЛЬЗЕ проверки: настоящая правка обязана быть
    # видна НА ФОНЕ измеренного шума, а не в предположении, что шума нет.
    # Проверка, у которой сигнал сравним с шумом, бесполезна, чем бы ни
    # объяснялся шум.
    ctx.positive("настоящая правка на порядок заметнее измеренного шума",
                 moved["diff"] > max(100, same["diff"] * 10),
                 f"сигнал={moved['diff']}, шум={same['diff']}")
    ctx.positive("расхождение локально — сменился цвет, а не перерисовался весь экран",
                 moved["diff"] < moved["pixels"] // 2,
                 f"{moved['diff']} < {moved['pixels'] // 2}")


# ================================================ OS-67 — ГРАНИЦЫ ПЕСОЧНИЦЫ
@scenario(id="OS-67", depth=INSTALLED_PRODUCT)
def os67_preview_cannot_reach_owner_files_or_api(ctx) -> None:
    """Чужая страница в превью не дотягивается до владельца.

    Превью — это ЧУЖОЙ код: его пишет модель, приносит владелец, генерирует
    шаблон. Он отдаётся с /api, то есть с origin самой панели, и без песочницы
    получил бы её права: cookie сессии уезжает с любым fetch, CSRF-токен лежит
    в localStorage того же origin.

    Меряется не заголовок в ответе, а ПОВЕДЕНИЕ настоящего Chromium: страница
    внутри живого кадра панели сама пытается сбежать шестью способами и
    докладывает в свой DOM, что у неё вышло.
    """
    sync_playwright, executable = _need_browser(ctx)
    product = _product(ctx)
    ctx.reached_installed_product(
        f"HTTP продукта {product.url} + живой кадр превью в настоящем Chromium")
    client = product.client("OS-67")
    pid, _ = _project(client, "Побег OS-67", ESCAPE_PROBE.replace("__LOCAL__", product.url))

    headers = client.get(f"/api/web-designer/projects/{pid}/preview").headers
    ctx.positive("продукт отдаёт превью с песочницей в заголовке CSP",
                 "sandbox allow-scripts" in (headers.get("content-security-policy") or ""),
                 f"CSP={headers.get('content-security-policy')}")

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable)
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
            _login(page, product)
            page.goto(product.url + f"/#/web_designer?project={pid}",
                      wait_until="domcontentloaded")
            guest = _live_frame(page, "#out")
            until = time.monotonic() + 25
            raw = "ждём"
            while raw.strip() == "ждём" and time.monotonic() < until:
                page.wait_for_timeout(200)
                guest = _live_frame(page, "#out")
                raw = guest.inner_text("#out")
            # Панель владельца тем временем СВОИМ origin к API ходит: контроль
            # обратной стороны, иначе «не дотянулась» значило бы «сервер лёг».
            own = page.evaluate("""async () => { const r = await fetch('/api/web-designer/projects',
                 {credentials: 'include'}); return r.status; }""")
        finally:
            browser.close()

    try:
        escape = json.loads(raw)
    except ValueError:
        raise AssertionError(f"страница превью не доложила о попытках побега: {raw[:200]!r}")

    ctx.positive("панель самого владельца при этом к своему API ходит свободно",
                 own == 200, f"HTTP {own} на /api/web-designer/projects из панели")
    ctx.negative("страница превью НЕ читает cookie сессии владельца",
                 str(escape.get("cookie", "")).startswith("THROW:"),
                 f"cookie → {escape.get('cookie')}")
    ctx.negative("страница превью НЕ читает localStorage панели (там CSRF-токен)",
                 str(escape.get("storage", "")).startswith("THROW:"),
                 f"localStorage → {escape.get('storage')}")
    ctx.negative("страница превью НЕ дозванивается до /api продукта",
                 str(escape.get("api", "")).startswith("THROW:"),
                 f"fetch /api/web-designer/projects → {escape.get('api')}")
    ctx.negative("и до настроек владельца тоже не дозванивается",
                 str(escape.get("settings", "")).startswith("THROW:"),
                 f"fetch /api/settings → {escape.get('settings')}")
    ctx.negative("страница превью НЕ читает файлы владельца через file://",
                 str(escape.get("file", "")).startswith("THROW:"),
                 f"fetch file:///etc/passwd → {escape.get('file')}")
    ctx.negative("страница превью НЕ достаёт локальный адрес продукта по имени",
                 str(escape.get("local", "")).startswith("THROW:"),
                 f"fetch {product.url}/api/health → {escape.get('local')}")
    ctx.negative("страница превью НЕ читает адрес окна владельца",
                 str(escape.get("parent", "")).startswith("THROW:"),
                 f"parent.location → {escape.get('parent')}")


# ============================================== OS-68 — ТЕЛЕФОННАЯ ШИРИНА
@scenario(id="OS-68", depth=INSTALLED_PRODUCT)
def os68_narrow_window_keeps_owner_controls_usable(ctx) -> None:
    """На телефонной ширине органы управления не уезжают за край и нажимаются.

    Владелец открывает панель с телефона (390×844 — ширина обычного аппарата).
    Меряются три вещи, каждая на живой странице:

    * страница не едет вбок: ширина документа равна ширине окна;
    * ни один орган рабочей области не обрезан краем окна;
    * каждый орган РЕАЛЬНО нажимаем: `elementFromPoint` в его середине
      возвращает его самого, а не то, что лежит поверх.

    Отрицательный контроль обязателен: тот же замер применяется к НАРОЧНО
    сломанной раскладке, и если он её не заметит — он не мерил ничего.
    """
    sync_playwright, executable = _need_browser(ctx)
    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url} + окно 390×844 в Chromium")
    client = product.client("OS-68")
    pid, _ = _project(client, "Телефон OS-68", PIXEL_SITE)

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable)
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            _login(page, product)
            page.goto(product.url + f"/#/web_designer?project={pid}",
                      wait_until="domcontentloaded")
            _live_frame(page, "h1")
            page.wait_for_timeout(1200)
            narrow = page.evaluate(_CONTROLS_JS)

            # НАРОЧНАЯ ПОЛОМКА раскладки — только в этом окне и только стилем
            # страницы: продукт не правится, меняется то, что видит браузер.
            page.evaluate("""() => { const st = document.createElement('style');
                 st.id = 'bossman-os68-lom';
                 st.textContent = 'main button{min-width:3000px !important}';
                 document.head.appendChild(st); }""")
            page.wait_for_timeout(500)
            broken = page.evaluate(_CONTROLS_JS)
            page.evaluate("() => { const n = document.getElementById('bossman-os68-lom');"
                          " if (n) n.remove(); }")
            page.wait_for_timeout(500)
            healed = page.evaluate(_CONTROLS_JS)
        finally:
            browser.close()

    clipped = [row["name"] for row in narrow["controls"] if row["clipped"]]
    unreachable = [row["name"] for row in narrow["controls"] if not row["reachable"]]
    ctx.positive("на телефонной ширине панель вообще показывает органы управления",
                 len(narrow["controls"]) >= 8,
                 f"органов в рабочей области={len(narrow['controls'])}")
    ctx.positive("страница не едет вбок: документ по ширине окна",
                 narrow["docWidth"] <= narrow["vw"] and narrow["bodyWidth"] <= narrow["vw"],
                 f"окно={narrow['vw']}, документ={narrow['docWidth']}, "
                 f"тело={narrow['bodyWidth']}")
    ctx.positive("ни один орган не обрезан краем окна",
                 not clipped, f"обрезаны: {clipped}")
    ctx.positive("каждый орган действительно нажимаем в своей середине",
                 not unreachable, f"перекрыты: {unreachable}")

    broken_clipped = [row["name"] for row in broken["controls"] if row["clipped"]]
    ctx.negative("замер НЕ слеп: нарочно сломанную раскладку он называет сломанной",
                 broken["docWidth"] > narrow["vw"] and len(broken_clipped) >= 3,
                 f"документ стал {broken['docWidth']} при окне {broken['vw']}, "
                 f"обрезанных={len(broken_clipped)}")
    ctx.negative("и после снятия поломки замер снова чист — он смотрит на страницу, "
                 "а не на свою память",
                 healed["docWidth"] <= healed["vw"]
                 and not [row for row in healed["controls"] if row["clipped"]],
                 f"документ={healed['docWidth']}, окно={healed['vw']}")


# ================================================ OS-69 — ОТКАЗ ВИДЕН КАК ОТКАЗ
@scenario(id="OS-69", depth=INSTALLED_PRODUCT)
def os69_failed_action_is_shown_as_failure(ctx) -> None:
    """Неудавшееся действие показано владельцу отказом, а не «готово».

    Беда настоящая: владелец печатает в «Коде сайта», а сайт в это же время
    меняют из другой вкладки. Автосохранение уезжает с УСТАРЕВШЕЙ версией.
    Худшее, что может случиться, — зелёная надпись «сохранено» поверх
    незаписанной правки.

    ИЗМЕРЕНО в настоящем браузере: панель поднимает полосу `role="alert"` с
    кодом HTTP 409 и словами сервера, набранное владельцем остаётся в
    редакторе, а сохранённый код остаётся тем, что записала другая вкладка.

    Парная половина обязательна: успешное сохранение полосы НЕ поднимает, иначе
    «полоса есть» доказывало бы лишь то, что она есть всегда.
    """
    sync_playwright, executable = _need_browser(ctx)
    product = _product(ctx)
    ctx.reached_installed_product(f"HTTP продукта {product.url} + панель владельца в Chromium")
    client = product.client("OS-69")
    other_tab = product.client("OS-69-другая-вкладка")
    pid, base = _project(client, "Отказ OS-69", PIXEL_SITE)

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable)
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 950})
            page = context.new_page()
            _login(page, product)
            page.goto(product.url + f"/#/web_designer?project={pid}",
                      wait_until="domcontentloaded")
            _live_frame(page, "h1")
            page.wait_for_selector("textarea.bd-code", timeout=20000)

            # --- ПАРНАЯ ПОЛОВИНА: обычное сохранение проходит и молчит ---
            typed_ok = page.evaluate(
                """() => { const area = document.querySelector('textarea.bd-code');
                     area.value = area.value.replace('строка ниже', 'ПЕРВАЯ ПРАВКА ВЛАДЕЛЬЦА');
                     area.dispatchEvent(new Event('input', {bubbles: true}));
                     return area.value.includes('ПЕРВАЯ ПРАВКА ВЛАДЕЛЬЦА'); }""")
            page.wait_for_timeout(3000)
            quiet = page.evaluate(
                """() => { const n = document.querySelector('[data-testid="bd-recovery"]');
                     return {present: !!n, hidden: n ? n.hidden : null}; }""")
            saved_ok = client.get(f"/api/web-designer/projects/{pid}").json()

            # --- ОТКАЗ: другая вкладка уводит версию вперёд ---
            fresh = saved_ok["code"].replace("ЗАГОЛОВОК ВЛАДЕЛЬЦА", "ПРАВКА ДРУГОЙ ВКЛАДКИ")
            moved = other_tab.put(f"/api/web-designer/projects/{pid}/code",
                                  json={"html": fresh, "note": "другая вкладка",
                                        "base_version": int(saved_ok["meta"]["version"])})
            page.evaluate(
                """() => { const area = document.querySelector('textarea.bd-code');
                     area.value = area.value.replace('ПЕРВАЯ ПРАВКА ВЛАДЕЛЬЦА',
                                                     'ВТОРАЯ ПРАВКА ВЛАДЕЛЬЦА');
                     area.dispatchEvent(new Event('input', {bubbles: true})); }""")
            until = time.monotonic() + 20
            banner = {"present": False}
            while time.monotonic() < until:
                banner = page.evaluate(
                    """() => { const n = document.querySelector('[data-testid="bd-recovery"]');
                         if (!n) return {present: false};
                         return {present: true, hidden: n.hidden,
                                 role: n.getAttribute('role'),
                                 text: (n.innerText || '').slice(0, 600)}; }""")
                if banner.get("present") and not banner.get("hidden"):
                    break
                page.wait_for_timeout(300)
            draft = page.evaluate(
                "() => (document.querySelector('textarea.bd-code') || {}).value || ''")
        finally:
            browser.close()

    ctx.positive("владелец действительно набрал правку в «Коде сайта»",
                 bool(typed_ok), "текст редактора изменён")
    ctx.positive("законное сохранение прошло и дошло до сохранённого кода",
                 "ПЕРВАЯ ПРАВКА ВЛАДЕЛЬЦА" in saved_ok["code"],
                 f"версия={saved_ok['meta']['version']}")
    ctx.negative("на законном сохранении полоса отказа НЕ поднимается",
                 quiet["present"] and quiet["hidden"] is True,
                 f"полоса={quiet}")
    ctx.positive("другая вкладка действительно увела версию вперёд",
                 moved.status_code == 200, f"HTTP {moved.status_code}")

    ctx.positive("отказ показан владельцу полосой тревоги, а не тишиной",
                 banner.get("present") and banner.get("hidden") is False
                 and banner.get("role") == "alert",
                 f"полоса={ {k: v for k, v in banner.items() if k != 'text'} }")
    text = str(banner.get("text") or "")
    ctx.positive("в полосе названы и код ответа, и причина словами сервера",
                 "409" in text and "код изменился" in text,
                 f"текст={text[:160]!r}")
    ctx.positive("полоса говорит владельцу, ЧТО делать дальше",
                 "Обновите состояние" in text or "другой вкладке" in text,
                 f"подсказка={text[:200]!r}")
    ctx.negative("слова «сохранено»/«готово» в полосе отказа НЕ появляются",
                 "Код сохранён" not in text and "готово" not in text.lower(),
                 "успеха поверх отказа не нарисовано")
    ctx.negative("набранное владельцем не выброшено — черновик остался в редакторе",
                 "ВТОРАЯ ПРАВКА ВЛАДЕЛЬЦА" in draft,
                 f"длина черновика={len(draft)}")

    final = client.get(f"/api/web-designer/projects/{pid}").json()
    ctx.negative("отказ НЕ состоялся как правка: в коде осталась чужая версия",
                 "ПРАВКА ДРУГОЙ ВКЛАДКИ" in final["code"]
                 and "ВТОРАЯ ПРАВКА ВЛАДЕЛЬЦА" not in final["code"],
                 f"версия={final['meta']['version']}")


# ============================================= OS-70 — ЖИВАЯ ЛЕНТА НА ЭКРАНЕ
@scenario(id="OS-70", depth=INSTALLED_PRODUCT)
def os70_live_feed_reaches_the_screen_without_secrets(ctx) -> None:
    """Живая лента доносит ход работы до открытой панели и не несёт ключ.

    Лента — единственное, по чему владелец видит, что продукт работает. Два
    требования сразу, и оба меряются на ЭКРАНЕ, а не на шине внутри процесса:

    * до открытой панели доходит КАЖДОЕ событие и в том же порядке;
    * ключ владельца, попавший внутрь ЗНАЧЕНИЯ события, до экрана не доходит.

    Ключ здесь ФИКТИВНЫЙ и собирается из кусков в момент прогона: ни одно
    значение не лежит в исходнике целиком. Поиск по ленте контролируется
    отдельно — «не нашли» значит что-то только тогда, когда тем же поиском
    находится метка, которая там ТОЧНО есть.
    """
    sync_playwright, executable = _need_browser(ctx)
    product = _product(ctx)
    ctx.reached_installed_product(
        f"HTTP продукта {product.url} + WebSocket /api/events в настоящем Chromium")
    client = product.client("OS-70")

    fake_key = "sk-" + "or-v1-" + "fake0000deadbeef0000fake0000deadbeef00"
    marks = [f"ШАГ-ЛЕНТЫ-{n}" for n in (1, 2, 3)]

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable)
        try:
            context = browser.new_context(viewport={"width": 1200, "height": 800})
            page = context.new_page()
            _login(page, product)
            opened = page.evaluate(
                """() => new Promise((ok) => { window.__feed = [];
                     const ws = new WebSocket(location.origin.replace(/^http/, 'ws')
                                              + '/api/events');
                     ws.onmessage = (e) => window.__feed.push(e.data);
                     ws.onopen = () => ok('открыт');
                     ws.onerror = () => ok('ошибка');
                     setTimeout(() => ok('не открылся'), 8000); })""")
            page.wait_for_timeout(400)

            sent = []
            for index, mark in enumerate(marks):
                preview = (f"[sandbox] {mark} curl -H \"Authorization: Bearer {fake_key}\" "
                           f"https://api.example/v1/models")
                answer = client.post("/api/approvals",
                                     json={"kind": "terminal", "preview": preview})
                sent.append(answer.status_code)
                if index == 0:
                    page.wait_for_timeout(200)
            until = time.monotonic() + 15
            feed = []
            while time.monotonic() < until:
                feed = page.evaluate("() => window.__feed")
                if sum(1 for row in feed if "approval.created" in row) >= len(marks):
                    break
                page.wait_for_timeout(300)
        finally:
            browser.close()

    ctx.positive("панель владельца открыла живую ленту продукта настоящим WebSocket",
                 opened == "открыт", f"сокет: {opened}")
    ctx.positive("продукт принял все объявленные события",
                 sent == [200] * len(marks), f"коды ответов={sent}")

    events = [json.loads(row) for row in feed]
    created = [row for row in events if row.get("kind") == "approval.created"]
    ctx.positive("до экрана владельца дошло КАЖДОЕ событие, ни одного не потеряно",
                 len(created) == len(marks),
                 f"дошло {len(created)} из {len(marks)}")
    order = [next((mark for mark in marks if mark in str(row.get("preview", ""))), "?")
             for row in created]
    ctx.positive("события пришли в том порядке, в котором продукт их объявил",
                 order == marks, f"порядок на экране={order}")
    ctx.positive("первым лента здоровается — владелец видит, что связь живая",
                 bool(events) and events[0].get("kind") == "hello",
                 f"первое событие={events[0].get('kind') if events else None}")

    screen = "\n".join(feed)
    ctx.negative("поиск по ленте НЕ слеп: метка, которая там есть, находится",
                 all(mark in screen for mark in marks),
                 f"меток найдено={sum(1 for mark in marks if mark in screen)} из {len(marks)}")
    ctx.negative("ключ владельца, уехавший внутри ЗНАЧЕНИЯ события, до экрана не дошёл",
                 fake_key not in screen and fake_key[8:] not in screen,
                 "ни целиком, ни хвостом")
    ctx.negative("события, которого продукт не объявлял, на экране не появилось",
                 "ШАГ-ЛЕНТЫ-4" not in screen)
