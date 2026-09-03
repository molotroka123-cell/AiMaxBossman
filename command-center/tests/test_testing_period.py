"""Режим тестового периода: журнал пишется, секрет не уезжает, публикация честна.

Самое важное здесь — не то, что журнал ведётся, а то, что он безопасен: он
уезжает в git, и один уцелевший токен обесценил бы всю затею.
"""
from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from bcc.features import testing_period as tp

from .conftest import client_for, make_settings, start_app
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401


@pytest.fixture(autouse=True)
def _mode_on(monkeypatch):
    """Режим включён по умолчанию; тесты, которым нужно иначе, гасят его сами."""
    monkeypatch.delenv(tp.FLAG, raising=False)


async def test_status_reports_the_running_session(env):
    res = await env.client.get("/api/testing/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["banner"] and "TESTING" in body["banner"]
    assert len(body["session"]) == 12
    assert body["log_path"].endswith("session-log.jsonl")


async def test_clicks_from_the_browser_are_written(env):
    res = await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "button#run «Запустить»", "page": "#/tasks"}},
        {"kind": "ui.navigate", "data": {"to": "#/agents"}},
    ]})
    assert res.status_code == 200 and res.json()["accepted"] == 2

    events = (await env.client.get("/api/testing/events")).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "ui.click" in kinds and "ui.navigate" in kinds
    click = next(e for e in events if e["kind"] == "ui.click")
    assert click["source"] == "ui" and click["data"]["element"].startswith("button#run")


async def test_every_http_request_is_logged_with_its_status(env):
    await env.client.get("/api/system")
    await env.client.get("/api/definitely-not-a-route")

    events = (await env.client.get("/api/testing/events", params={"limit": 500})).json()["events"]
    server = [e for e in events if e["source"] == "server"]
    paths = {e["data"].get("path") for e in server}
    assert "/api/system" in paths, "обычный запрос обязан попасть в журнал"

    bad = [e for e in server if e["data"].get("path") == "/api/definitely-not-a-route"]
    assert bad and bad[0]["kind"] == "http.error" and bad[0]["data"]["status"] == 404
    assert isinstance(bad[0]["data"]["ms"], (int, float))


async def test_secrets_are_never_written_even_if_the_browser_sends_them(env):
    token = env.svc.auth.token
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "вход", "token": token,
                                      "authorization": f"Bearer {token}",
                                      "nested": {"api_key": "sk-secret-value-123456"}}},
    ]})
    raw = tp._log_path(env.settings).read_text(encoding="utf-8")
    assert token not in raw, "токен не должен попадать в журнал вообще"
    assert "sk-secret-value-123456" not in raw
    assert "[не записывается]" in raw


async def test_request_bodies_and_auth_headers_are_not_logged(env):
    await env.client.post("/api/testing/log",
                          json={"events": [{"kind": "ui.click", "data": {"element": "кнопка"}}]},
                          headers={"X-BCC-Token": "supersecrettokenvalue1234"})
    raw = tp._log_path(env.settings).read_text(encoding="utf-8")
    assert "supersecrettokenvalue1234" not in raw


def test_scrub_removes_exact_secrets_and_counts_them():
    secrets = {"AbCdEf1234567890xyz"}
    text = "вход AbCdEf1234567890xyz и ещё раз AbCdEf1234567890xyz"
    clean, count = tp.scrub(text, secrets)
    assert "AbCdEf1234567890xyz" not in clean and count >= 2


def test_scrub_catches_a_secret_it_was_not_told_about():
    """Точные значения — первая линия, но не единственная."""
    clean, count = tp.scrub("api_key: QQQQwwwwEEEErrrrTTTT1234", set())
    assert "QQQQwwwwEEEErrrrTTTT1234" not in clean and count >= 1


def test_scrub_counter_is_zero_on_clean_text():
    """Счётчик не «всегда положительный»: иначе он ничего не доказывает."""
    clean, count = tp.scrub("владелец нажал кнопку Запустить", set())
    assert count == 0 and clean == "владелец нажал кнопку Запустить"


async def test_publish_writes_a_clean_bundle_and_never_leaks_the_token(env, tmp_path,
                                                                      monkeypatch):
    token = env.svc.auth.token
    (Path(env.settings.data_dir) / "token").write_text(token, encoding="utf-8")
    # В журнале секрет всё же оказался — например, его напечатала чужая библиотека.
    tp._log_path(env.settings).parent.mkdir(parents=True, exist_ok=True)
    with open(tp._log_path(env.settings), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-09-03T00:00:00Z", "source": "server",
                             "kind": "http.error",
                             "data": {"error": f"провал с токеном {token}"}},
                            ensure_ascii=False) + "\n")

    repo = tmp_path / "repo"
    _make_repo(repo)
    monkeypatch.setattr(tp, "_repo_root", lambda _start: repo)

    res = await env.client.post("/api/testing/publish")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["redactions"] >= 1, "чистка обязана была сработать"

    published = list((repo / tp.PUBLISH_SUBDIR).glob("*"))
    assert len(published) == 2, "отчёт и полный журнал"
    for path in published:
        assert token not in path.read_text(encoding="utf-8"), f"токен уцелел в {path.name}"


async def test_publish_commits_to_the_current_branch_without_force(env, tmp_path, monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo)
    monkeypatch.setattr(tp, "_repo_root", lambda _start: repo)

    body = (await env.client.post("/api/testing/publish")).json()

    steps = {s["step"] for s in body["steps"]}
    assert {"add", "commit", "push"} <= steps
    push = next(s for s in body["steps"] if s["step"] == "push")
    assert "--force" not in json.dumps(body), "force не делаем никогда"
    # Тестовый remote — обычный каталог, поэтому push проходит по-настоящему.
    assert push["code"] == 0, push
    assert body["published"] is True and len(body["sha"]) == 12

    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "журнал тестового периода" in log


async def test_publish_refuses_when_the_log_is_empty(env, tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_repo_root", lambda _start: tmp_path)
    tp._log_path(env.settings).parent.mkdir(parents=True, exist_ok=True)
    tp._log_path(env.settings).write_text("", encoding="utf-8")
    # Журнал пуст только если в нём нет ни строки; сначала уберём накопленное.
    res = await env.client.post("/api/testing/publish")
    assert res.status_code in (200, 400)
    if res.status_code == 400:
        assert "пуст" in res.text


async def test_publish_reports_a_failed_push_instead_of_forcing(env, tmp_path, monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo, with_remote=False)          # remote нет — push обязан провалиться
    monkeypatch.setattr(tp, "_repo_root", lambda _start: repo)

    body = (await env.client.post("/api/testing/publish")).json()

    assert body["published"] is False and body.get("committed") is True
    assert "force" in body["reason"], "отказ обязан объяснить, почему не форсим"


async def test_mode_off_disables_everything(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.FLAG, "0")
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            status = (await client.get("/api/testing/status")).json()
            assert status["enabled"] is False and status["session"] is None
            assert (await client.post("/api/testing/log", json={"events": []})).status_code == 409
            assert (await client.post("/api/testing/publish")).status_code == 409
        assert not (Path(settings.data_dir) / tp.LOG_DIRNAME).exists(), \
            "выключенный режим не должен ничего писать"
    finally:
        await svc.stop()


async def test_logging_never_breaks_the_app(env, monkeypatch):
    """Наблюдатель не имеет права уронить то, за чем наблюдает."""
    def _boom(*_a, **_kw):
        raise OSError("диск недоступен")

    monkeypatch.setattr(tp.SessionLog, "_append", _boom)
    res = await env.client.get("/api/system")
    assert res.status_code == 200
    res = await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    assert res.status_code == 200 and res.json()["accepted"] == 0


def _make_repo(repo: Path, *, with_remote: bool = True) -> None:
    """Настоящий git-репозиторий с настоящим удалённым каталогом-приёмником."""
    repo.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    (repo / "README.md").write_text("тест\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-qm", "первый")
    if with_remote:
        bare = repo.parent / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True)
        run("remote", "add", "origin", str(bare))
        run("push", "-q", "-u", "origin", "main")


# --------------------------------------------------------------- живой браузер

def test_banner_and_click_recording_work_in_a_real_browser(live):  # noqa: F811
    """Настоящий Chromium: плашка видна, клик доехал до журнала, ошибок консоли нет.

    Всё остальное в этом файле проверяет серверную половину через API. Здесь
    проверяется то, что владелец увидит глазами, и то, что запись действительно
    работает из браузера, а не только когда события шлёт тест.
    """
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)

        bar = page.wait_for_selector(".bcc-testing-bar", timeout=15000)
        assert bar.is_visible(), "плашка тестового периода должна быть видна"
        assert "TESTING PERIOD" in bar.inner_text()
        session = page.inner_text("#bcc-testing-session").strip()
        assert len(session) == 12, f"в плашке должен стоять номер сессии, получено {session!r}"
        assert page.is_visible("#bcc-testing-publish"), "кнопка отправки должна быть на месте"

        page.click("#think-open")                 # обычное действие владельца
        page.click("#think-open")
        # Очередь уходит пачкой раз в несколько секунд — ждём саму запись, а не время.
        # wait_for_function здесь не годится: асинхронный предикат возвращает
        # Promise, а он истинный сам по себе, и ожидание завершалось мгновенно.
        # page.evaluate обещание дожидается по-настоящему, поэтому опрашиваем им.
        fetch_events = ("async () => (await (await fetch('/api/testing/events?limit=1000',"
                        " {cache: 'no-store'})).json()).events")
        deadline = time.monotonic() + 30
        events = []
        while time.monotonic() < deadline:
            events = page.evaluate(fetch_events)
            if any(e["kind"] == "ui.click" for e in events):
                break
            page.wait_for_timeout(500)

    kinds = {e["kind"] for e in events}
    assert "ui.session_open" in kinds, "открытие сессии обязано быть записано"
    assert "ui.click" in kinds
    clicks = [e for e in events if e["kind"] == "ui.click"]
    assert any("think-open" in c["data"].get("element", "") for c in clicks), \
        f"клик по кнопке не опознан: {[c['data'] for c in clicks]}"
    server_paths = {e["data"].get("path") for e in events if e["source"] == "server"}
    assert any(p and p.startswith("/api/") for p in server_paths), \
        "работа UI обязана попасть и в серверную половину журнала"
    # Сам приём журнала исключён намеренно: иначе каждая отправка пачки рождала
    # бы свою запись, и журнал наполнялся бы рассказом о себе.
    assert "/api/testing/log" not in server_paths
    assert not errors, f"ошибки консоли: {errors}"


async def test_environment_snapshot_makes_two_journals_comparable(env):
    """Без снимка окружения два присланных журнала несопоставимы."""
    events = (await env.client.get("/api/testing/events", params={"limit": 500})).json()["events"]
    snap = next((e for e in events if e["kind"] == "session.env"), None)
    assert snap, "снимок окружения обязан быть в начале сессии"
    data = snap["data"]
    assert data["version"] and data["python"] and data["platform"]
    assert isinstance(data["flags_on"], list) and isinstance(data["features"], list)
    assert "testing_period" in data["features"], "список фич берётся из живого приложения"


async def test_environment_snapshot_records_flag_state_not_values(env, monkeypatch):
    """Пишем только «включено или нет»: значения переменных бывают путями и адресами."""
    from bcc.features import testing_period as mod

    monkeypatch.setenv("BOSSMAN_WATCHDOG_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_SECRET_PATH_ENABLED", "/home/owner/private/path")
    snap = mod._env_snapshot(env.svc)

    assert "BOSSMAN_WATCHDOG_ENABLED" in snap["flags_on"]
    assert "/home/owner/private/path" not in json.dumps(snap, ensure_ascii=False)
    assert snap["flags_off_count"] >= 1


async def test_stop_is_recorded_so_its_absence_becomes_evidence(tmp_path, monkeypatch):
    """Штатная остановка видна; её отсутствие говорит, что процесс до неё не дожил."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        await client.post("/api/testing/log", json={"events": [
            {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    await svc.stop()

    lines = tp._log_path(settings).read_text(encoding="utf-8").splitlines()
    stops = [json.loads(l) for l in lines if '"session.stop"' in l]
    assert len(stops) == 1, "остановка обязана быть записана ровно один раз"
    data = stops[0]["data"]
    assert data["events"] > 0 and data["uptime_s"] >= 0 and isinstance(data["top"], dict)


def test_dead_click_is_recorded_in_a_real_browser(live):  # noqa: F811
    """Владелец нажал — и ничего. Именно это должно попасть в журнал.

    Ровно эта жалоба и была: кнопка на дашборде не включает приложения. Клик,
    после которого не ушёл запрос, не сменился адрес и не изменился экран,
    записывается отдельным видом, а не теряется среди обычных нажатий.
    """
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.wait_for_selector(".bcc-testing-bar", timeout=15000)

        # Кнопка, которая заведомо ничего не делает: вешаем сами, чтобы проверять
        # механизм, а не конкретную страницу. Не в #view — его перерисовщик
        # приложения заменяет целиком, и кнопка исчезает до клика.
        page.wait_for_timeout(1500)          # дать первой отрисовке улечься
        page.evaluate("""() => {
            const b = document.createElement('button');
            b.id = 'dead-on-purpose';
            b.className = 'btn';
            b.textContent = 'Кнопка без действия';
            b.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:9999';
            b.addEventListener('click', (e) => e.preventDefault());
            document.body.appendChild(b);
        }""")
        page.click("#dead-on-purpose")

        fetch_events = ("async () => (await (await fetch('/api/testing/events?limit=1000',"
                        " {cache: 'no-store'})).json()).events")
        deadline = time.monotonic() + 30
        events = []
        while time.monotonic() < deadline:
            events = page.evaluate(fetch_events)
            if any(e["kind"] == "ui.dead_click" for e in events):
                break
            page.wait_for_timeout(500)

    dead = [e for e in events if e["kind"] == "ui.dead_click"]
    assert dead, f"мёртвый клик не записан; виды: {sorted({e['kind'] for e in events})}"
    assert "dead-on-purpose" in dead[0]["data"]["element"]
    assert dead[0]["data"]["checked"], "запись обязана называть, что именно проверяли"
    assert not errors, f"ошибки консоли: {errors}"


def test_a_click_that_works_is_not_called_dead(live):  # noqa: F811
    """Проверка не должна быть «всегда мёртвый»: рабочая кнопка находкой не считается."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.wait_for_selector(".bcc-testing-bar", timeout=15000)

        page.click("#think-open")          # настоящая кнопка: открывает панель
        page.wait_for_timeout(2500)

        events = page.evaluate(
            "async () => (await (await fetch('/api/testing/events?limit=1000')).json()).events")

    dead = [e for e in events if e["kind"] == "ui.dead_click"
            and "think-open" in e["data"].get("element", "")]
    assert not dead, f"рабочая кнопка записана как мёртвая: {dead}"


# ------------------------------------- исход запуска доходит до журнала

def _launch_records(settings) -> list[dict]:
    path = tp._log_path_for(settings.data_dir)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if '"desktop.launch"' in l]


def test_failed_launch_reaches_the_published_journal(tmp_path, monkeypatch):
    """Главный пробел: если окно не открылось, сервера нет — и журнала тоже.

    Следы такого запуска лежали только в desktop-run.log, который в git не
    уезжает. Теперь исход ложится в тот же jsonl и уедет со следующей удачной
    сессией — иначе присланный файл про этот запуск молчит вообще.
    """
    from bcc import desktop
    from bcc.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'data' / 'x.db'}")
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: None)   # браузера нет

    code = desktop.run(["--host", "127.0.0.1", "--port", "0", "--no-show-token"],
                       out=io.StringIO())

    assert code == 2
    records = _launch_records(settings)
    assert records, "исход запуска обязан попасть в журнал тестового периода"
    reasons = {r["data"]["reason"] for r in records}
    assert "no-browser-found" in reasons
    assert all(r["source"] == "desktop" for r in records)
    assert all(r["data"]["reason"] in tp.LAUNCH_REASONS for r in records), \
        "причина — код из закрытого списка, а не свободный текст"


def test_launch_record_ties_start_and_outcome(tmp_path, monkeypatch):
    """Начало и исход одного запуска обязаны сшиваться одним идентификатором."""
    from bcc import desktop
    from bcc.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'data' / 'x.db'}")
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: "/bin/true")

    desktop.run(["--host", "127.0.0.1", "--port", "0", "--browser", "/bin/true",
                 "--profile", str(tmp_path / "prof"), "--no-show-token"],
                launcher=lambda *a, **k: 0, out=io.StringIO())

    records = _launch_records(settings)
    ids = {r["data"]["launch_id"] for r in records if r["data"]["reason"] != "no-browser-found"}
    assert len(ids) == 1, f"начало и исход должны нести один launch_id: {ids}"
    reasons = [r["data"]["reason"] for r in records]
    assert reasons[0] == "start" and "ok" in reasons


def test_launch_record_carries_no_paths_and_no_free_text_of_others(tmp_path, monkeypatch):
    """Пути и чужие тексты в журнал не уезжают: там видно имя пользователя."""
    from bcc import desktop
    from bcc.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'data' / 'x.db'}")
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: "/opt/secret-place/chromium")

    def _boom(*_a, **_kw):
        raise OSError("не запустить /opt/secret-place/chromium: нет доступа")

    desktop.run(["--host", "127.0.0.1", "--port", "0", "--browser", "/opt/secret-place/chromium",
                 "--profile", str(tmp_path / "prof"), "--no-show-token"],
                launcher=_boom, out=io.StringIO())

    raw = tp._log_path_for(settings.data_dir).read_text(encoding="utf-8")
    assert "desktop.launch" in raw
    assert "/opt/secret-place" not in raw, "полный путь к браузеру в журнал не пишем"
    assert "chromium" in raw, "имя браузера остаётся — оно и нужно для разбора"
    failed = [r for r in _launch_records(settings) if r["data"]["reason"] == "browser-launch-failed"]
    assert failed and failed[0]["data"]["detail"] == "OSError", \
        "чужой текст исключения не пишем, только его класс"


def test_launch_record_respects_the_switched_off_mode(tmp_path, monkeypatch):
    """Выключенный режим обязан гасить и запись лаунчера, а не только серверную."""
    from bcc import desktop
    from bcc.config import settings

    monkeypatch.setenv(tp.FLAG, "0")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: None)

    desktop.run(["--host", "127.0.0.1", "--port", "0", "--no-show-token"], out=io.StringIO())

    assert not tp._log_path_for(settings.data_dir).exists()


def test_launch_record_respects_the_size_limit(tmp_path, monkeypatch):
    """Процесс лаунчера не должен обходить предел размера журнала."""
    monkeypatch.setattr(tp, "MAX_LOG_BYTES", 64)
    data_dir = tmp_path / "data"
    path = tp._log_path_for(data_dir)
    path.parent.mkdir(parents=True)
    path.write_text("x" * 200 + "\n", encoding="utf-8")

    assert tp.record_launch(data_dir, "desktop.launch", {"reason": "ok"}) is False


def test_the_closed_list_of_reasons_matches_what_the_launcher_actually_writes():
    """Список причин и код обязаны сходиться в обе стороны.

    Причина, которой нет в списке, при разборе журнала выглядит опечаткой;
    причина, которую никто не пишет, — как «такого не случалось». И то и другое
    вводит в заблуждение того, кто читает присланный журнал, поэтому сверяем
    список с настоящими вызовами в desktop.py, а не с намерением.
    """
    import ast

    source = (Path(__file__).resolve().parents[1] / "bcc" / "desktop.py").read_text(encoding="utf-8")
    written = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_record_launch"):
            for arg in node.args[2:3]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    written.add(arg.value)
                elif isinstance(arg, ast.IfExp):    # "window-not-ready" if ... else "ok"
                    for branch in (arg.body, arg.orelse):
                        if isinstance(branch, ast.Constant):
                            written.add(branch.value)

    assert written, "вызовы _record_launch в desktop.py не найдены — тест перестал что-либо проверять"
    assert written - set(tp.LAUNCH_REASONS) == set(), \
        f"лаунчер пишет причину вне закрытого списка: {sorted(written - set(tp.LAUNCH_REASONS))}"
    assert set(tp.LAUNCH_REASONS) - written == set(), \
        f"в списке есть причины, которых никто не пишет: {sorted(set(tp.LAUNCH_REASONS) - written)}"
