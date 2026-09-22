"""Hard UX attack on a live Bossman instance (test data dir, never the owner's DB)."""
import json
import re
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8800"
TOKEN = Path(__import__("os").environ["BCC_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
XSS = '<img src=x onerror="window.__xss=1;alert(1)">"><svg onload=window.__xss=2>'
LONG = "Ж" * 50_000
UNI = "тест 🚀🔥 مرحبا بالعالم \u202e evil \u200b zero-width ‮"
SKIP_SUBMIT = {"Терминал", "Браузер", "Coding-сессии", "Локальные инструменты", "Восстановление", "Система"}
BAD_TEXT = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|Traceback \(most recent|Internal Server Error")

findings: list[dict] = []


def finding(sev, area, what, evidence=""):
    findings.append({"severity": sev, "area": area, "what": what, "evidence": str(evidence)[:600]})
    print(f"[{sev}] {area}: {what} :: {str(evidence)[:200]}", flush=True)


def instrument(page, sink):
    page.on("console", lambda m: sink.append(("console", m.text)) if m.type == "error" else None)
    page.on("pageerror", lambda e: sink.append(("pageerror", str(e))))
    page.on("response", lambda r: sink.append(("http", f"{r.status} {r.request.method} {r.url}")) if r.status >= 500 else None)
    page.on("dialog", lambda d: (sink.append(("dialog", d.message)), d.dismiss()))


def login(page, token=TOKEN):
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector("input", timeout=15000)
    page.locator("input").first.fill(token)
    page.get_by_text("Войти", exact=True).click()
    page.wait_for_timeout(1500)


def nav_items(page):
    return page.evaluate("""() => [...new Map([...document.querySelectorAll('button.nav-item[data-page]')]
        .map(e => [e.dataset.page, e.innerText.trim()])).entries()]""")


def open_section(page, name):
    """name is (data-page, label) or a visible label."""
    page_id = name[0] if isinstance(name, (list, tuple)) else None
    t = time.time()
    if page_id is None:
        page_id = page.evaluate("(l) => { const e = [...document.querySelectorAll('button.nav-item[data-page]')].find(x => x.innerText.trim() === l); return e && e.dataset.page }", name)
    target = page.locator(f"button.nav-item[data-page='{page_id}']").first
    box = target.bounding_box()
    if not target.is_visible() or box is None or box['x'] < 0:
        menu = page.locator("#mobile-menu")
        if menu.count() and menu.is_visible():
            menu.click()
            page.wait_for_timeout(400)
    target.click(timeout=5000)
    page.wait_for_timeout(1800)
    return time.time() - t


def sweep(page, label, sink):
    names = nav_items(page)
    for entry in names:
        name = entry[1]
        sink.clear()
        try:
            dt = open_section(page, entry)
        except Exception as exc:  # noqa: BLE001
            finding("P2", f"{label}/{name}", "раздел не открывается кликом", exc)
            continue
        body = page.inner_text("body")
        m = BAD_TEXT.search(body)
        if m:
            ctx = body[max(0, m.start() - 80): m.end() + 80].replace("\n", " ")
            finding("P2", f"{label}/{name}", f"на экране технический мусор «{m.group(0)}»", ctx)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if overflow > 4:
            finding("P3" if label == "desktop" else "P2", f"{label}/{name}", f"горизонтальная прокрутка страницы на {overflow}px")
        for kind, msg in sink:
            if kind in ("pageerror",):
                finding("P1", f"{label}/{name}", "исключение JS на странице", msg)
            elif kind == "http":
                finding("P1", f"{label}/{name}", "ответ сервера 5xx", msg)
            elif kind == "console" and "favicon" not in msg:
                finding("P3", f"{label}/{name}", "ошибка в консоли", msg)
        if dt > 4:
            finding("P3", f"{label}/{name}", f"раздел открывался {dt:.1f} с")
        safe = re.sub(r"[^\w-]+", "_", name)[:40]
        page.screenshot(path=str(OUT / f"{label}-{safe}.png"))
    return names


def attack_login(pw, sink):
    b = pw.chromium.launch(headless=True)
    page = b.new_page(locale="ru-RU")
    instrument(page, sink)
    for label, tok in (("пустой", ""), ("неверный", "wrong-token"), ("10k", "A" * 10_000), ("xss", XSS)):
        sink.clear()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("input")
        page.locator("input").first.fill(tok)
        resp = None
        try:
            with page.expect_response(lambda r: "/api/login" in r.url, timeout=5000) as ri:
                page.get_by_text("Войти", exact=True).click()
            resp = ri.value.status
        except Exception:  # noqa: BLE001
            resp = "no-request"
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        inside = page.locator("nav, aside").count() > 0 and "Операторский канал" in body and "Войти" not in body
        if inside:
            finding("P0", "login", f"вход пустил с токеном «{label}»")
        if page.evaluate("() => window.__xss") or any(k == "dialog" for k, _ in sink):
            finding("P0", "login", "XSS исполнился на странице входа")
        if resp == 500 or any(k == "http" for k, _ in sink):
            finding("P1", "login", f"сервер упал на токене «{label}»", sink)
        if label != "пустой" and not re.search(r"невер|ошиб|не подош|не подход|отказ|не удалось|invalid", body, re.I):
            finding("P2", "login", f"нет понятного сообщения при токене «{label}» (HTTP {resp})", body[:300])
    b.close()


def count_tasks(client):
    d = client.get("/api/tasks").json()
    rows = d if isinstance(d, list) else d.get("tasks") or d.get("items") or []
    return len(rows), rows


def attack_inputs(page, sink, client):
    # Operator channel: hostile title/prompt + double submit
    sink.clear()
    open_section(page, "Операторский канал")
    before, _ = count_tasks(client)
    cmd = page.locator("input[placeholder*='Команда'], textarea").first
    if cmd.count() == 0:
        finding("P2", "operator", "нет поля команды")
        return
    cmd.fill(XSS + " ответь одним словом ок")
    send = page.get_by_text("Отправить", exact=True).first
    send.dblclick()
    page.wait_for_timeout(3000)
    after, rows = count_tasks(client)
    if after - before > 1:
        finding("P1", "operator", f"двойной клик «Отправить» создал {after - before} задачи")
    elif after - before == 0:
        finding("P2", "operator", "отправка не создала задачу (или создала не задачу)", page.inner_text("body")[:300])
    page.wait_for_timeout(1500)
    if page.evaluate("() => window.__xss") or any(k == "dialog" for k, _ in sink):
        finding("P0", "operator", "XSS из текста задачи исполнился в интерфейсе")
    # the same text shown in Tasks section
    open_section(page, "Задачи")
    page.wait_for_timeout(1500)
    if page.evaluate("() => window.__xss") or any(k == "dialog" for k, _ in sink):
        finding("P0", "tasks", "XSS из текста задачи исполнился в списке задач")
    # 50k and unicode
    for label, text in (("50k", LONG), ("unicode", UNI)):
        sink.clear()
        open_section(page, "Операторский канал")
        cmd = page.locator("input[placeholder*='Команда'], textarea").first
        t = time.time()
        cmd.fill(text)
        page.get_by_text("Отправить", exact=True).first.click()
        page.wait_for_timeout(2500)
        if time.time() - t > 8:
            finding("P2", "operator", f"ввод {label} подвешивает интерфейс ({time.time() - t:.1f} с)")
        for kind, msg in sink:
            if kind in ("pageerror", "http"):
                finding("P1", "operator", f"{label}: {kind}", msg)
        body = page.inner_text("body")
        if BAD_TEXT.search(body):
            finding("P2", "operator", f"{label}: технический мусор на экране", BAD_TEXT.search(body).group(0))
        page.screenshot(path=str(OUT / f"operator-{label}.png"))


def attack_every_text_field(page, sink):
    """Type hostile text into every visible field of every section WITHOUT submitting."""
    for entry in nav_items(page):
        name = entry[1]
        try:
            open_section(page, entry)
        except Exception:  # noqa: BLE001
            continue
        sink.clear()
        fields = page.locator("input[type=text]:visible, input:not([type]):visible, textarea:visible")
        n = min(fields.count(), 8)
        for i in range(n):
            try:
                fields.nth(i).fill(XSS + UNI, timeout=1500)
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(500)
        if page.evaluate("() => window.__xss") or any(k == "dialog" for k, _ in sink):
            finding("P0", name, "XSS исполнился при вводе в поле (без отправки)")
        for kind, msg in sink:
            if kind == "pageerror":
                finding("P1", name, "исключение JS при вводе в поле", msg)


def attack_session_kill(pw, sink):
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(locale="ru-RU")
    page = ctx.new_page()
    instrument(page, sink)
    login(page)
    ctx.clear_cookies()
    page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
    sink.clear()
    try:
        open_section(page, "Задачи")
    except Exception as exc:  # noqa: BLE001
        finding("P2", "session", "после потери сессии навигация не работает", exc)
    page.wait_for_timeout(2500)
    body = page.inner_text("body")
    if "Войти" not in body and not re.search(r"войдите|сессия|аутентиф|токен", body, re.I):
        finding("P2", "session", "потеря сессии не объяснена пользователю (нет входа и нет сообщения)", body[:300])
    for kind, msg in sink:
        if kind == "pageerror":
            finding("P1", "session", "исключение JS после потери сессии", msg)
    page.screenshot(path=str(OUT / "session-killed.png"))
    b.close()


def attack_palette(page, sink):
    sink.clear()
    page.keyboard.press("Control+K")
    page.wait_for_timeout(700)
    page.keyboard.type(XSS[:40] + "zzzz несуществующее")
    page.wait_for_timeout(700)
    page.keyboard.press("Enter")
    page.wait_for_timeout(700)
    page.keyboard.press("Escape")
    if page.evaluate("() => window.__xss") or any(k == "dialog" for k, _ in sink):
        finding("P0", "palette", "XSS в палитре команд")
    for kind, msg in sink:
        if kind == "pageerror":
            finding("P1", "palette", "исключение JS в палитре", msg)


def main():
    sink: list = []
    client = httpx.Client(base_url=BASE, headers={"X-BCC-Token": TOKEN}, timeout=20)
    with sync_playwright() as pw:
        if "--phone-only" not in sys.argv:
            attack_login(pw, sink)
        for label, vp in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
            if "--phone-only" in sys.argv and label == "desktop":
                continue
            b = pw.chromium.launch(headless=True)
            page = b.new_page(viewport=vp, locale="ru-RU")
            instrument(page, sink)
            login(page)
            if label == "phone":
                # on a phone the nav may hide behind a menu button
                pass
            names = sweep(page, label, sink)
            print(f"{label}: sections swept {len(names)}", flush=True)
            if label == "desktop":
                attack_palette(page, sink)
                attack_inputs(page, sink, client)
                attack_every_text_field(page, sink)
            b.close()
        attack_session_kill(pw, sink)
    _, rows = count_tasks(client)
    for r in rows:
        t = r.get("task", r)
        if str(t.get("title", "")).startswith("Telegram:"):
            continue
        if t.get("status") in ("queued", "running", "pending"):
            client.post(f"/api/tasks/{t['id']}/stop")
            print("stopped attack task", t["id"], flush=True)
    (OUT / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    by = {}
    for f in findings:
        by[f["severity"]] = by.get(f["severity"], 0) + 1
    print("SUMMARY", json.dumps(by, ensure_ascii=False))


if __name__ == "__main__":
    main()
