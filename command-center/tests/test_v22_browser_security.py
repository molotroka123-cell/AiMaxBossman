"""browser-use, этап 1 — три дефекта безопасности браузера закрыты.

Каждый тест падал до соответствующей правки. Источник находок:
`docs/research/browser-use.md` §4.2 (г), (д), (б), (и) — они были воспроизведены
на нашем рантайме, а не взяты из чужого README.

Проверка идёт на НАСТОЯЩЕМ Chromium по локальной странице: подделать здесь
нечего — если пароль утечёт, тест это увидит в реальном снимке реальной страницы.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import pytest

from bcc.v2.browser_control import (AmbiguousSelector, BrowserManager, BrowserPolicy,
                                    StaleElementReference, redact_secrets)

from .browser_support import chromium_available, reason as browser_reason

# Заведомо уникальная строка: если она найдётся хоть где-то в видимом модели
# выводе — это утечка, а не совпадение.
CANARY = "Pa55w0rd-KANAREYKA-7f3a91e2"   # ci-secret-scan: allow (тестовая канарейка)

@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    """F-010: по умолчанию браузер не ходит на loopback/приватные адреса; эти
    тесты поднимают тестовый HTTP-сервер на 127.0.0.1 — включаем owner-override
    только для них (сама политика проверяется в test_secrem_browser_policy.py)."""
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Вход</title></head><body>
<h1>Вход в систему</h1>
<form id="f">
  <input id="user" name="user" type="text" placeholder="логин">
  <input id="pass" name="pass" type="password" placeholder="пароль">
  <button id="go" type="button">Войти</button>
</form>
</body></html>"""

TWIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Две кнопки</title></head><body>
<p id="result">ничего не нажато</p>
<button class="act" onclick="document.getElementById('result').innerText='нажата ПЕРВАЯ'">Ок</button>
<button class="act" onclick="document.getElementById('result').innerText='нажата ВТОРАЯ'">Ок</button>
</body></html>"""

MUTATING_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Перерисовка</title></head><body>
<p id="result">ничего не нажато</p>
<div id="list">
  <button class="row" onclick="document.getElementById('result').innerText='нажата УДАЛИТЬ'">Удалить</button>
  <button class="row" onclick="document.getElementById('result').innerText='нажата СОХРАНИТЬ'">Сохранить</button>
</div>
<script>
  function mutate() {
    const list = document.getElementById('list');
    list.removeChild(list.firstElementChild);   // «Удалить» исчезает
  }
</script>
</body></html>"""


SECRET_FIELDS_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Не только пароль</title></head><body>
<form>
  <input type="hidden" name="csrf" value="TOKEN-SKRYTOE-POLE-9911">
  <input type="text" name="otp" autocomplete="one-time-code" value="CODE-SMS-4242">
  <input type="text" name="pw2" autocomplete="current-password" value="Pa55-TEXT-POLE-3131">
  <input type="text" name="comment" value="обычный текст">
  <button id="ok" type="button">Ок</button>
</form>
</body></html>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "login.html").write_text(LOGIN_PAGE, encoding="utf-8")
    (root / "twin.html").write_text(TWIN_PAGE, encoding="utf-8")
    (root / "mutating.html").write_text(MUTATING_PAGE, encoding="utf-8")
    (root / "captcha.html").write_text(CAPTCHA_PAGE, encoding="utf-8")
    (root / "homemade.html").write_text(HOMEMADE_CAPTCHA_PAGE, encoding="utf-8")
    (root / "secretfields.html").write_text(SECRET_FIELDS_PAGE, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def mgr(tmp_path):
    """Тот же менеджер, что в бою, с той же подстановкой предустановленного
    Chromium (`_patch_executable`) — иначе тест проверял бы другой рантайм."""
    from bcc.features.browser import _patch_executable
    manager = BrowserManager(tmp_path / "browser")
    _patch_executable(manager)
    yield manager
    try:
        await manager.close()
    except Exception:
        pass


async def _session(mgr, sid: int = 1):
    await mgr.start(sid, BrowserPolicy.from_dict(None), headless=True)
    return sid


# ------------------------------------------------------------------ без браузера

def test_redact_secrets_covers_nested_structures():
    """Вторая линия обороны работает и на вложенных данных, а не только на строке."""
    payload = {"text": f"введено {CANARY}", "items": [{"value": CANARY}, "чисто"],
               "n": 5, "flag": True}
    clean = redact_secrets(payload, {CANARY})
    assert CANARY not in json.dumps(clean, ensure_ascii=False)
    assert clean["n"] == 5 and clean["flag"] is True        # не портим остальное
    assert clean["items"][1] == "чисто"


def test_redact_secrets_without_secrets_is_identity():
    payload = {"text": "обычный текст"}
    assert redact_secrets(payload, set()) is payload


# ----------------------------------------- враждебная перепроверка (V2.3, §1.1)
# Ниже — четыре сценария, каждый из которых ПАДАЛ до правки. Утверждалось, что
# пароль модели больше не доходит; доходил, просто не через `read_dom`.

def test_redact_secrets_survives_whitespace_collapse():
    """Снимок сам схлопывает пробелы (`clean()`), и точное совпадение отваливается.

    Секрет с табом или двойным пробелом уходил модели почти в открытом виде:
    `redact_secrets` искал исходную строку, а в тексте лежала схлопнутая.
    """
    secret = "Pa55w0rd  KANAREYKA\t7f3a91e2"     # ci-secret-scan: allow (канарейка)
    as_page_shows_it = "введено Pa55w0rd KANAREYKA 7f3a91e2"
    assert "KANAREYKA" not in redact_secrets(as_page_shows_it, {secret})


def test_redact_secrets_survives_truncation():
    """`raw.slice(0, 220)` в снимке и `[:500]` в превью режут секрет.

    Обрезанный секрет — это по-прежнему секрет: 220 символов ключа API хватает.
    """
    secret = "SEKRET-" + "x" * 300                # ci-secret-scan: allow (канарейка)
    truncated = "поле: " + secret[:220]
    assert "SEKRET-" + "x" * 100 not in redact_secrets(truncated, {secret})


def test_redact_secrets_survives_url_and_html_encoding():
    """Тот же секрет в адресе и в разметке выглядит иначе, а секретом быть не перестаёт."""
    import html
    from urllib.parse import quote
    secret = "Pa55 w0rd&<KANAREYKA>"              # ci-secret-scan: allow (канарейка)
    assert "KANAREYKA" not in redact_secrets(f"?p={quote(secret, safe='')}", {secret})
    assert "KANAREYKA" not in redact_secrets(html.escape(secret), {secret})


def test_redaction_does_not_eat_unrelated_text():
    """Слишком короткий общий кусок — не улика: маскировать его нельзя.

    Иначе секрет вида «password2026» вычистил бы со страницы любое слово,
    начинающееся на «pass», и модель осталась бы без текста страницы.
    """
    secret = "SuperSecret-2026-KANAREYKA"        # ci-secret-scan: allow (канарейка)
    page = "нажмите Super, затем SuperSec — раздел справки"
    assert redact_secrets(page, {secret}) == page


# ------------------------------------------------------------------ на Chromium

pytestmark_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())


@pytestmark_browser
async def test_password_value_never_reaches_the_model(mgr, site):
    """Дефект (г): `el.innerText || el.value` отдавал введённый пароль в снимок."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/login.html", actor="agent", approved=True)
    # Заполняем поле пароля НАСТОЯЩИМ значением прямо на странице.
    await mgr.type_text(sid, "#pass", CANARY, actor="agent", approved=True)

    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    blob = json.dumps(snap, ensure_ascii=False)
    assert CANARY not in blob, "значение поля пароля утекло в снимок для модели"

    field = next(el for el in snap["interactive"] if el.get("name") == "pass")
    assert field["secret"] is True
    assert field["filled"] is True      # модель знает, что поле заполнено,
    assert field["text"] == ""          # но не знает, чем именно

    # соседнее текстовое поле по-прежнему читается — правило не сломало обычную работу
    await mgr.type_text(sid, "#user", "тимур", actor="agent", approved=True)
    snap2 = await mgr.snapshot(sid, actor="agent", approved=True)
    user = next(el for el in snap2["interactive"] if el.get("name") == "user")
    assert user["text"] == "тимур"


@pytestmark_browser
async def test_secret_typed_into_plain_field_is_scrubbed(mgr, site):
    """Пароль, введённый рантаймом в обычное поле, тоже не доходит до модели."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/login.html", actor="agent", approved=True)
    await mgr.fill_secret(sid, "#user", secret=CANARY, actor="agent", approved=True)

    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert CANARY not in json.dumps(snap, ensure_ascii=False)
    assert "***" in json.dumps(snap, ensure_ascii=False)


@pytestmark_browser
async def test_secret_in_page_url_never_reaches_the_model(mgr, site):
    """Форма входа с `method=GET` уносит пароль в адресную строку.

    `snapshot()` секреты вычищал, а `status()` — нет. Между тем `status()`
    возвращают click / type / select / back / reload, и оттуда адрес уходит
    модели (`_render` печатает строку «URL:»), в шину событий
    (`agent.tool_call ... url=`) и в колонку `browser_sessions.current_url`.
    То есть пароль оседал ещё и в журнале, откуда его уже не отозвать.
    """
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/login.html", actor="agent", approved=True)
    await mgr.fill_secret(sid, "#pass", secret=CANARY, actor="agent", approved=True)

    # ровно то, что делает отправка формы с method=GET
    await mgr.navigate(sid, f"{site}/twin.html?user=t&pass={CANARY}",
                       actor="agent", approved=True)

    fresh = await mgr.snapshot(sid, actor="agent", approved=True)
    assert CANARY not in json.dumps(fresh, ensure_ascii=False)   # снимок и так был чист

    ref = [el for el in fresh["interactive"] if el.get("tag") == "button"][0]["ref"]
    for name, payload in (("status", await mgr.status(sid)),
                          ("click", await mgr.click(sid, ref=ref, actor="agent",
                                                    approved=True)),
                          ("reload", await mgr.reload(sid, actor="agent"))):
        assert CANARY not in json.dumps(payload, ensure_ascii=False), (
            f"{name}() отдал пароль из адреса страницы")


@pytestmark_browser
async def test_non_password_secret_fields_are_not_read_out(mgr, site):
    """Секрет живёт не только в `type=password`.

    Признаком «секретного поля» был ровно `type === 'password'`. Поэтому
    CSRF-токен в `type=hidden`, код из SMS (`autocomplete=one-time-code`) и
    пароль в `type=text` с `autocomplete=current-password` уходили модели
    значением — при том, что ни для одного сценария они ей не нужны.
    """
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/secretfields.html", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    blob = json.dumps(snap, ensure_ascii=False)

    for needle in ("TOKEN-SKRYTOE-POLE-9911", "CODE-SMS-4242", "Pa55-TEXT-POLE-3131"):
        assert needle not in blob, f"значение секретного поля утекло модели: {needle}"

    by_name = {el.get("name"): el for el in snap["interactive"]}
    for field_name in ("csrf", "otp", "pw2"):
        assert by_name[field_name]["secret"] is True
        assert by_name[field_name]["filled"] is True     # «поле заполнено» модель знает
        assert by_name[field_name]["text"] == ""         # чем именно — нет

    # обычное поле не пострадало: правило не должно ослепить модель
    assert by_name["comment"]["secret"] is False
    assert by_name["comment"]["text"] == "обычный текст"


@pytestmark_browser
async def test_secret_field_stays_clickable_after_broadened_rule(mgr, site):
    """Признак секретности одинаков в снимке и в проверке ссылки.

    Если снимок прячет значение, а проверка `ref` его читает, отпечатки
    расходятся и КАЖДЫЙ клик по такому полю падает ложным
    StaleElementReference. Это сломало бы работу, а не защитило бы её.
    """
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/secretfields.html", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    otp = next(el for el in snap["interactive"] if el.get("name") == "otp")
    await mgr.click(sid, ref=otp["ref"], actor="agent", approved=True)


@pytestmark_browser
async def test_secret_with_whitespace_does_not_reach_the_model(mgr, site):
    """Тот же дефект схлопывания пробелов, но на настоящей странице."""
    spaced = "Pa55w0rd  KANAREYKA\t7f3a91e2"     # ci-secret-scan: allow (канарейка)
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/login.html", actor="agent", approved=True)
    await mgr.fill_secret(sid, "#user", secret=spaced, actor="agent", approved=True)

    blob = json.dumps(await mgr.snapshot(sid, actor="agent", approved=True),
                      ensure_ascii=False)
    assert "KANAREYKA" not in blob, "секрет утёк в схлопнутом виде"


@pytestmark_browser
async def test_ambiguous_selector_is_refused_not_guessed(mgr, site):
    """Дефект (б): `.first` молча нажимал первую из двух одинаковых кнопок."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/twin.html", actor="agent", approved=True)

    with pytest.raises(AmbiguousSelector) as err:
        await mgr.click(sid, ".act", actor="agent", approved=True)
    assert err.value.count == 2

    # ГЛАВНОЕ: ничего не нажато
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert "ничего не нажато" in snap["text"]

    # по ref — работает и попадает ровно туда, куда показала модель
    second = [el for el in snap["interactive"] if el.get("tag") == "button"][1]
    await mgr.click(sid, ref=second["ref"], actor="agent", approved=True)
    after = await mgr.snapshot(sid, actor="agent", approved=True)
    assert "нажата ВТОРАЯ" in after["text"]


@pytestmark_browser
async def test_stale_ref_after_dom_change_does_not_click_neighbour(mgr, site):
    """Дефект (и): ссылка из старого снимка не должна попасть в другой элемент."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/mutating.html", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    delete_btn = next(el for el in snap["interactive"] if el.get("text") == "Удалить")

    # Страница перерисовалась: «Удалить» исчезла, на её месте теперь «Сохранить».
    await mgr._session(sid).page.evaluate("mutate()")
    fresh = await mgr.snapshot(sid, actor="agent", approved=True)   # новое поколение

    with pytest.raises(StaleElementReference):
        await mgr.click(sid, ref=delete_btn["ref"], actor="agent", approved=True)

    after = await mgr.snapshot(sid, actor="agent", approved=True)
    assert "ничего не нажато" in after["text"], "нажали соседний элемент вместо исчезнувшего"
    assert fresh["generation"] > snap["generation"]


@pytestmark_browser
async def test_ref_from_previous_snapshot_is_stale_even_if_element_survived(mgr, site):
    """Поколение — это и есть инвалидация: между снимками страница могла измениться."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/twin.html", actor="agent", approved=True)
    old = await mgr.snapshot(sid, actor="agent", approved=True)
    old_ref = [el for el in old["interactive"] if el.get("tag") == "button"][0]["ref"]

    await mgr.snapshot(sid, actor="agent", approved=True)      # новое поколение

    with pytest.raises(StaleElementReference):
        await mgr.click(sid, ref=old_ref, actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert "ничего не нажато" in snap["text"]


# ------------------------------------------------------------------ капча

CAPTCHA_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Проверка</title></head><body>
<h1>Подтвердите, что вы не робот</h1>
<div class="g-recaptcha" data-sitekey="test"></div>
<button id="go">Продолжить</button>
</body></html>"""

HOMEMADE_CAPTCHA_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Код</title></head><body>
<p>Введите символы с картинки</p><input id="code"><button id="ok">Ок</button>
</body></html>"""


def test_captcha_detection_recognises_providers_and_ignores_normal_pages():
    from bcc.v2.browser_control import detect_captcha
    assert detect_captcha('<div class="g-recaptcha">', "")["provider"] == "Google reCAPTCHA"
    assert detect_captcha('<div class="cf-turnstile">', "")["provider"] == "Cloudflare Turnstile"
    assert detect_captcha("<div id=hcaptcha>", "")["provider"] == "hCaptcha"
    # самописная капча без известного провайдера ловится по тексту
    assert detect_captcha("<p>x</p>", "Введите символы с картинки")["present"] is True
    # обычная страница — не капча; слово «robot» в тексте статьи не считается
    assert detect_captcha("<p>О роботах в промышленности</p>",
                          "Роботы на производстве")["present"] is False


@pytestmark_browser
async def test_captcha_stops_the_agent_and_hands_over_to_human(mgr, site, tmp_path):
    """Капчу агент НЕ решает: он её распознаёт, останавливается и зовёт человека.

    Проверяется, что после обнаружения действия агента реально отклоняются, а
    не что он «постарался и не смог» — иначе он бился бы о страницу до таймаута.
    """
    from bcc.v2.browser_control import CaptchaBlocked

    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/captcha.html", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)

    assert snap["captcha"]["present"] is True
    assert snap["captcha"]["provider"] == "Google reCAPTCHA"

    # ГЛАВНОЕ: агент не взаимодействует со страницей
    with pytest.raises(CaptchaBlocked):
        await mgr.click(sid, "#go", actor="agent", approved=True)
    with pytest.raises(CaptchaBlocked):
        await mgr.type_text(sid, "#go", "x", actor="agent", approved=True)

    # но ЧИТАТЬ может — иначе он не узнал бы причину остановки
    again = await mgr.snapshot(sid, actor="agent", approved=True)
    assert again["captcha"]["present"] is True

    # человек прошёл проверку → страница сменилась → блокировка снимается сама
    await mgr.navigate(sid, f"{site}/twin.html", actor="agent", approved=True)
    after = await mgr.snapshot(sid, actor="agent", approved=True)
    assert after["captcha"]["present"] is False
    ref = [el for el in after["interactive"] if el.get("tag") == "button"][0]["ref"]
    await mgr.click(sid, ref=ref, actor="agent", approved=True)


@pytestmark_browser
async def test_page_without_captcha_is_not_taken_over(mgr, site):
    """Ложное срабатывание дороже пропуска: обычная страница не блокируется."""
    sid = await _session(mgr)
    await mgr.navigate(sid, f"{site}/twin.html", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert snap["captcha"]["present"] is False
    ref = [el for el in snap["interactive"] if el.get("tag") == "button"][0]["ref"]
    await mgr.click(sid, ref=ref, actor="agent", approved=True)   # работает как обычно
