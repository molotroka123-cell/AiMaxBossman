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
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import pytest

from bcc.v2.browser_control import (AmbiguousSelector, BrowserManager, BrowserPolicy,
                                    StaleElementReference, redact_secrets)

from .browser_support import chromium_available, reason as browser_reason

# Заведомо уникальная строка: если она найдётся хоть где-то в видимом модели
# выводе — это утечка, а не совпадение.
CANARY = "Pa55w0rd-KANAREYKA-7f3a91e2"   # ci-secret-scan: allow (тестовая канарейка)

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


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "login.html").write_text(LOGIN_PAGE, encoding="utf-8")
    (root / "twin.html").write_text(TWIN_PAGE, encoding="utf-8")
    (root / "mutating.html").write_text(MUTATING_PAGE, encoding="utf-8")
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
