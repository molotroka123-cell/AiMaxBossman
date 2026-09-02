"""Канареечная проверка утечки секрета — сквозная, по ВСЕМ путям сразу.

`test_v22_browser_security.py` проверяет уровень браузерного рантайма: снимок
страницы, адрес, поля. Здесь проверяется то, что ВЫШЕ него и что переживает
процесс: журнал вызовов инструментов, история запусков, шина событий, строки в
базе, логи, тексты исключений и JSON, уходящий в интерфейс.

Секрет, попавший в снимок, виден один раз. Секрет, попавший в базу или в
журнал, остаётся там навсегда — отозвать его оттуда уже нельзя. Поэтому
проверка идёт по конечному состоянию хранилища, а не по одному ответу.

Канарейка взята из мастер-аудита дословно.
"""
from __future__ import annotations

import json
import logging
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import sqlalchemy as sa

from bcc.db import metadata
from bcc.tools import ToolContext

from .browser_support import chromium_available, reason as browser_reason
from .helpers import make_stack

CANARY = "BOSSMAN_CANARY_SECRET_91f03f_DO_NOT_LEAK"  # ci-secret-scan: allow (канарейка)

@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    """F-010: браузер по умолчанию не ходит на loopback; тестовый сервер живёт на
    127.0.0.1 — owner-override только для этого файла."""
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Вход</title></head><body>
<h1>Вход</h1>
<form id="f" method="GET" action="/login.html">
  <input id="user" name="user" type="text">
  <input id="pass" name="pass" type="password">
  <button id="go" type="submit">Войти</button>
</form>
</body></html>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "canary-site"
    root.mkdir()
    (root / "login.html").write_text(LOGIN_PAGE, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def _dump_every_table(svc) -> str:
    """Всё содержимое базы одной строкой. Ищем канарейку в конечном состоянии."""
    chunks: list[str] = []
    async with svc.db.session() as s:
        for table in metadata.sorted_tables:
            try:
                rows = (await s.execute(sa.select(table))).mappings().all()
            except Exception:
                continue
            for row in rows:
                chunks.append(f"{table.name}:{dict(row)!r}")
    return "\n".join(chunks)


async def _real_ctx(env, run_id: int = 1) -> ToolContext:
    """Настоящие агент и задача из базы: подделанные id не проходят внешний ключ,
    а значит и путь записи в browser_sessions остался бы непроверенным."""
    stack = await make_stack(env.client)
    return ToolContext(
        svc=env.svc,
        task={"id": stack["task"]["id"], "mission_id": None},
        run_id=run_id,
        agent={"id": stack["agent"]["id"],
               "permissions": {"browser.control": True, "browser.read": True}},
        call_id="canary-call")


@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
async def test_canary_never_reaches_any_persisted_or_model_visible_surface(
        env, site, monkeypatch):
    """Один прогон входа — и проверка сразу всех поверхностей.

    Проверяются: результат инструмента, шина событий, каждая таблица базы,
    логи приложения и JSON, который отдают HTTP-эндпоинты браузера.
    """
    from bcc.features import tools_browser as tb
    from bcc.features.browser import _patch_executable

    # Свой обработчик, а не caplog: тест не должен зависеть от того, включён ли
    # плагин логирования у запускающего.
    captured: list[str] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            try:
                captured.append(record.getMessage())
            except Exception:
                captured.append(str(record.msg))

    catcher = _Catch(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(catcher)
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    events: list[str] = []
    original_emit = env.svc.bus.emit

    # Имя события — позиционный параметр: события несут в полезной нагрузке
    # собственное поле `name`, и без этого перехватчик ломал бы вызов.
    async def recording_emit(kind, /, **payload):
        events.append(f"{kind}:{payload!r}")
        return await original_emit(kind, **payload)

    monkeypatch.setattr(env.svc.bus, "emit", recording_emit)

    # 1. Учётка с канарейкой кладётся в хранилище через боевой путь.
    saved = await env.client.post("/api/browser/credentials", json={
        "id": "canary", "login": "ivan", "password": CANARY,
        "domain": "127.0.0.1", "note": "тест"})
    assert saved.status_code == 200, saved.text
    assert CANARY not in saved.text, "API вернул пароль в ответе на сохранение"

    manager = tb._mgr(env.svc)
    _patch_executable(manager)
    ctx = await _real_ctx(env)

    try:
        # 2. Открытие страницы и вход по ССЫЛКЕ на учётку, не по значению.
        opened = await tb._open({"url": f"{site}/login.html"}, ctx)
        assert opened.error is False, opened.content

        login = await tb._login({"credential_id": "canary",
                                 "login_selector": "#user",
                                 "password_selector": "#pass",
                                 "submit_selector": "#go"}, ctx)

        # 3. Форма ушла методом GET — пароль оказался в адресе страницы.
        #    Это и есть самый тихий путь утечки: он идёт мимо снимка полей.
        after = await tb._read_dom({}, ctx)

        # 4. Модель пытается передать пароль строкой напрямую.
        sneaky = await tb._type({"selector": "#pass", "text": CANARY}, ctx)

        surfaces = {
            "результат login": login.render() + repr(login.data),
            "результат read_dom": after.render() + repr(after.data),
            "результат type": sneaky.render() + repr(sneaky.data),
            "результат open": opened.render() + repr(opened.data),
        }
    finally:
        try:
            await manager.close()
        except Exception:
            pass

    # 5. HTTP-поверхность: список учёток и состояние сессий.
    creds = await env.client.get("/api/browser/credentials")
    surfaces["JSON учёток"] = creds.text

    surfaces["шина событий"] = "\n".join(events)
    root.removeHandler(catcher)
    root.setLevel(previous_level)
    surfaces["логи"] = "\n".join(captured)
    surfaces["база целиком"] = await _dump_every_table(env.svc)

    leaked = {name: text for name, text in surfaces.items() if CANARY in text}
    assert not leaked, (
        "канарейка найдена в: " + ", ".join(sorted(leaked))
        + "\n\nОтрывок: "
        + next(iter(leaked.values()))[:400])


@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
async def test_failure_paths_do_not_echo_the_secret(env, site, monkeypatch):
    """Отказ — самый вероятный путь утечки: в текст ошибки попадает всё.

    Проверяются неудачный вход, отсутствующий элемент и обрыв навигации.
    """
    from bcc.features import tools_browser as tb
    from bcc.features.browser import _patch_executable

    await env.client.post("/api/browser/credentials", json={
        "id": "canary2", "login": "ivan", "password": CANARY, "domain": "127.0.0.1"})

    manager = tb._mgr(env.svc)
    _patch_executable(manager)
    ctx = await _real_ctx(env, run_id=2)

    texts: list[str] = []
    try:
        texts.append((await tb._open({"url": f"{site}/login.html"}, ctx)).render())
        # селектор, которого нет: ошибка обязана назвать причину, не значение
        texts.append((await tb._login({"credential_id": "canary2",
                                       "login_selector": "#user",
                                       "password_selector": "#нет-такого-поля",
                                       "submit_selector": "#go"}, ctx)).render())
        # переход на несуществующий адрес после того, как секрет уже введён
        texts.append((await tb._open({"url": "http://127.0.0.1:1/нет"}, ctx)).render())
    finally:
        try:
            await manager.close()
        except Exception:
            pass

    joined = "\n".join(texts)
    assert CANARY not in joined, f"секрет попал в текст ошибки:\n{joined[:400]}"

    dump = await _dump_every_table(env.svc)
    assert CANARY not in dump, "секрет осел в базе на пути отказа"


async def test_model_facing_schema_has_no_plaintext_password_field():
    """Модель не должна иметь возможности прислать пароль строкой.

    Если поле есть в схеме — рано или поздно модель его заполнит, и значение
    попадёт в журнал вызовов инструментов ещё до всякой редакции.
    """
    from bcc.features import tools_browser as tb

    for spec in tb.SPECS:
        fields = set(spec.input_schema or {})
        forbidden = {f for f in fields
                     if f in ("password", "pass", "secret", "token", "credential")}
        assert not forbidden, (
            f"инструмент {spec.name} принимает секрет аргументом: {sorted(forbidden)}")

    login = next(s for s in tb.SPECS if s.name == "browser.login")
    assert "credential_id" in (login.input_schema or {})
    assert "credential_id" in login.required
