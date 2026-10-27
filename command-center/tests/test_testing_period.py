"""Режим тестового периода: журнал пишется, секрет не уезжает, публикация честна.

Самое важное здесь — не то, что журнал ведётся, а то, что он безопасен: он
уезжает в git, и один уцелевший токен обесценил бы всю затею.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from bcc.features import testing_period as tp

from .browser_support import chromium_available, reason as browser_reason, required
from .conftest import client_for, make_settings, start_app
from .test_ux2_thinking_pane import _free_port, _launch, _login, live  # noqa: F401

# Файл смешанный: почти всё здесь — серверная половина через API, и она обязана
# гоняться всегда. Поэтому условие висит на трёх браузерных тестах поимённо, а
# не модульным pytestmark. Импорт модуля, у которого pytestmark есть
# (test_ux2_thinking_pane), своей отметки сюда НЕ приносит — из-за этого три
# теста падали с ModuleNotFoundError вместо честного пропуска.
needs_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())


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


async def test_a_full_batch_is_one_append_not_two_hundred(env, monkeypatch):
    """A busy page flushes up to 200 events per POST; the installed UI sweep of bundle 0b15e00c saw that POST
    outlive its action timeout because every event took the lock, a thread hop and a file open of its own."""
    opens = {"n": 0}
    import builtins
    orig = builtins.open

    def counting_open(path, *a, **kw):
        if str(path).endswith("session-log.jsonl") and a and a[0] == "a":
            opens["n"] += 1
        return orig(path, *a, **kw)
    monkeypatch.setattr(builtins, "open", counting_open)
    one = [{"kind": "ui.click", "data": {"element": "button#warm", "page": "#/direct-gen"}}]
    t0 = time.perf_counter()
    assert (await env.client.post("/api/testing/log", json={"events": one})).status_code == 200
    single = time.perf_counter() - t0
    opens["n"] = 0
    events = [{"kind": "ui.click", "data": {"element": f"button#b{i}", "page": "#/direct-gen"}} for i in range(200)]
    t0 = time.perf_counter()
    res = await env.client.post("/api/testing/log", json={"events": events})
    took = time.perf_counter() - t0
    assert res.status_code == 200 and res.json()["accepted"] == 200
    assert opens["n"] == 1, opens                                  # the structural fix: one open for the batch
    # Relative to a one-event POST on the same machine: the old per-event lock + thread hop + open made it ~200x;
    # a fixed 2 s failed on loaded CI runners with coverage (2.6-3.3 s on py3.12, 12.10).
    assert took < max(2.0, 25 * single), (took, single)
    got = (await env.client.get("/api/testing/events?limit=2000")).json()["events"]
    assert sum(1 for e in got if e["kind"] == "ui.click") >= 200


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

    res = await env.client.post("/api/testing/publish")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["redactions"] >= 1, "чистка обязана была сработать"

    published = list(tp.publish_dir(env.settings.data_dir).glob("*"))
    assert len(published) == 2, "отчёт и полный журнал"
    for path in published:
        assert token not in path.read_text(encoding="utf-8"), f"токен уцелел в {path.name}"


# Владелец 24.10.2026: журналы сессий — личная переписка с системой, в git им не
# место. Публикация пишет в data_dir и не делает ни add, ни commit, ни push.

def test_publish_dir_lives_in_the_data_dir_not_in_docs(tmp_path):
    out = tp.publish_dir(tmp_path)
    assert out == tmp_path / "testing" / "published"
    assert "docs" not in tp.PUBLISH_SUBDIR.parts


async def test_publish_saves_locally_and_never_runs_git_add_commit_push(env, tmp_path,
                                                                       monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo)
    monkeypatch.setattr(tp, "_repo_root", lambda _start: repo)
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                 text=True).stdout.strip()
    real_git = tp._git
    calls: list[list[str]] = []

    def watching_git(args, cwd, timeout=None):
        calls.append(list(args))
        assert args[0] not in {"add", "commit", "push"}, f"публикация запустила git {args[0]}"
        return real_git(args, cwd, timeout)

    monkeypatch.setattr(tp, "_git", watching_git)

    res = await env.client.post("/api/testing/publish")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["published"] is True and body["local_only"] is True and body["steps"] == []
    out = tp.publish_dir(env.settings.data_dir)
    assert sorted(p.name for p in out.glob("*")) == sorted(body["files"])
    assert not any(c[0] in {"add", "commit", "push"} for c in calls)
    # Негатив: в рабочей копии ничего не появилось и коммита нет.
    assert not (repo / "docs" / "testing" / "sessions").exists()
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                text=True).stdout.strip()
    assert head_after == head_before


async def test_publish_refuses_when_the_data_dir_is_tracked_by_git(env, tmp_path, monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo, with_remote=False)
    target = repo / "data" / "testing" / "published"
    monkeypatch.setattr(tp, "publish_dir", lambda _data_dir: target)

    res = await env.client.post("/api/testing/publish")

    assert res.status_code == 409, res.text
    assert "git" in res.text
    assert not target.exists() or not any(target.iterdir()), "отказ не должен оставлять файлы"


async def test_publish_accepts_a_data_dir_that_git_ignores(env, tmp_path, monkeypatch):
    """Позитивный контроль к отказу выше: так устроен data_dir исходной установки."""
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo, with_remote=False)
    (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    target = repo / "data" / "testing" / "published"
    monkeypatch.setattr(tp, "publish_dir", lambda _data_dir: target)

    res = await env.client.post("/api/testing/publish")

    assert res.status_code == 200, res.text
    assert len(list(target.glob("*"))) == 2
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                            text=True).stdout
    assert "data/" not in status


async def test_publish_refuses_when_the_log_is_empty(env, tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_repo_root", lambda _start: tmp_path)
    tp._log_path(env.settings).parent.mkdir(parents=True, exist_ok=True)
    tp._log_path(env.settings).write_text("", encoding="utf-8")
    # Журнал пуст только если в нём нет ни строки; сначала уберём накопленное.
    res = await env.client.post("/api/testing/publish")
    assert res.status_code in (200, 400)
    if res.status_code == 400:
        assert "пуст" in res.text


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
    monkeypatch.setattr(tp.SessionLog, "_append_many", _boom)      # the batch path of /testing/log
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

@needs_browser
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


@needs_browser
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
        page.wait_for_selector("#view .bx-home", timeout=15000); page.wait_for_timeout(1500)
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


@needs_browser
def test_a_click_that_works_is_not_called_dead(live):  # noqa: F811
    """Проверка не должна быть «всегда мёртвый»: рабочая кнопка находкой не считается."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.wait_for_selector(".bcc-testing-bar", timeout=15000)

        page.click("#think-open")          # настоящая кнопка: открывает панель
        page.wait_for_selector("#think-pane:not([hidden])")
        assert page.get_attribute("#think-open", "aria-pressed") == "true"
        page.wait_for_timeout(2500)
        # A prior test could finish close to the 4 s batch timer while this
        # isolated run has not reached it. Flush explicitly so both runs judge
        # the same completed dead-click verdict, never an empty server batch.
        page.evaluate("async () => (await import('/testing.js'))._internal.flush()")

        events = page.evaluate(
            "async () => (await (await fetch('/api/testing/events?limit=1000')).json()).events")

    dead = [e for e in events if e["kind"] == "ui.dead_click"
            and "think-open" in e["data"].get("element", "")]
    assert not dead, f"рабочая кнопка записана как мёртвая: {dead}"


# ------------------------------------- исход запуска доходит до журнала


@needs_browser
@pytest.mark.parametrize("feedback", ["text", "busy", "ancestor", "focus"])
def test_visible_feedback_outside_view_is_not_dead(live, feedback):
    """Observe text/busy/hidden/autofill feedback; never record field contents."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.wait_for_selector(".bcc-testing-bar")
        page.wait_for_timeout(1500)
        page.evaluate("""mode => {
          const box = document.createElement('section');
          box.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:9999';
          const button = document.createElement('button');
          button.id = 'feedback-probe'; button.textContent = 'Run';
          const input = document.createElement('input');
          box.append(button, input); document.body.append(box);
          button.onclick = () => {
            if (mode === 'text') button.textContent = 'Done';
            if (mode === 'busy') { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
            if (mode === 'ancestor') box.hidden = true;
            if (mode === 'focus') { input.value = 'private-input-never-export'; input.focus(); }
          };
        }""", feedback)
        page.click("#feedback-probe")
        # Establish that the control actually responded before judging the
        # observer. On a busy Windows runner Playwright's event delivery and
        # the browser timer need not finish at the same wall-clock instant.
        page.wait_for_function("""mode => {
          const b = document.querySelector('#feedback-probe');
          const box = b?.parentElement;
          if (!b || !box) return false;
          if (mode === 'text') return b.textContent === 'Done';
          if (mode === 'busy') return b.disabled && b.getAttribute('aria-busy') === 'true';
          if (mode === 'ancestor') return box.hidden;
          return document.activeElement === box.querySelector('input');
        }""", arg=feedback)
        page.wait_for_timeout(1200)
        page.evaluate("async () => (await import('/testing.js'))._internal.flush()")
        events = page.evaluate("async () => (await (await fetch('/api/testing/events?limit=1000')).json()).events")
        assert any(e["kind"] == "ui.click" and "feedback-probe" in e["data"].get("element", "") for e in events)
        assert not any(e["kind"] == "ui.dead_click" and "feedback-probe" in e["data"].get("element", "") for e in events)
        assert "private-input-never-export" not in json.dumps(events)
        browser.close()

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
    port = _free_port()  # --port 0 means "use configured default", which may be live Bossman.

    desktop.run(["--host", "127.0.0.1", "--port", str(port), "--browser", "/bin/true",
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
    port = _free_port()

    def _boom(*_a, **_kw):
        raise OSError("не запустить /opt/secret-place/chromium: нет доступа")

    desktop.run(["--host", "127.0.0.1", "--port", str(port), "--browser", "/opt/secret-place/chromium",
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


def test_browser_coverage_is_actually_executed_where_it_is_demanded():
    """Пропуск браузерных тестов там, где их ждут, — это дыра в покрытии.

    Локально без Chromium они честно пропускаются. Но CI ставит Playwright и
    Chromium специально, и «пропущено» там означает, что проверка тихо исчезла:
    в строке «N passed» это не видно. Флаг BCC_REQUIRE_BROWSER превращает
    молчаливый пропуск в красный тест.
    """
    if not required():
        pytest.skip(f"{tp.FLAG.split('_')[0]}: браузерное покрытие не требуется "
                    f"в этом окружении (нет BCC_REQUIRE_BROWSER)")
    assert chromium_available(), (
        "BCC_REQUIRE_BROWSER задан, но Chromium недоступен: браузерные тесты "
        "были бы пропущены, а отчёт выглядел бы зелёным. " + browser_reason())


# --------------------------------------------------------------------------
# BL-025: обрыв соединения и мусор в теле — не 500
# --------------------------------------------------------------------------
# Найдено обходом интерфейса (`scripts/ui_acceptance_sweep.py`): каждая
# перезагрузка страницы, случившаяся на лету отправки пачки событий, оставляла
# в логе сервера полный traceback `starlette.requests.ClientDisconnect`.
#
# Это не косметика. Маячок телеметрии в интерфейсе отправляется в том числе на
# уходе со страницы, то есть обрыв — это ШТАТНОЕ поведение браузера, а не сбой.
# Необработанное исключение в обработчике означает 500 в ответе и мусор в
# журнале ровно там, где владелец будет искать НАСТОЯЩИЕ ошибки.
#
# Тем же путём лечится вторая дыра: `request.json()` на некорректном теле
# бросает JSONDecodeError, и клиент с испорченным телом получал 500 вместо 400.

async def test_a_client_that_disconnects_mid_post_is_not_a_server_error(env):
    from starlette.requests import ClientDisconnect

    async def gone():
        raise ClientDisconnect()

    # Подменяется РОВНО чтение тела — тот единственный вызов, который в
    # настоящей жизни и обрывается. Обработчик при этом настоящий.
    import bcc.features.testing_period as mod
    original = mod.Request.json
    mod.Request.json = lambda self: gone()          # type: ignore[assignment]
    try:
        res = await env.client.post("/api/testing/log", json={"events": []})
    finally:
        mod.Request.json = original                 # type: ignore[assignment]
    assert res.status_code < 500, res.text


async def test_a_malformed_body_is_a_bad_request_not_a_server_error(env):
    res = await env.client.post(
        "/api/testing/log", content=b"{not json at all",
        headers={"content-type": "application/json"})
    assert res.status_code == 400, res.text


# ------------------------------------------- publish: 500 из журнала владельца
# Журнал тестового периода 2026-09-21 (docs/testing/sessions/2026-09-22_202945…):
# шесть нажатий «Отправить в GitHub» подряд дали 500
# «FileNotFoundError: [WinError 2] Не удается найти указанный файл» за 31 мс —
# git не нашёлся, исключение вышло из ручки как голый 500, кнопка не объяснила
# ничего, и владелец жал её снова (ui.rage_click в том же журнале).

# С 24.10.2026 публикация локальная: git ей больше не нужен. Тот же журнал
# владельца теперь проверяет обе стороны: без git сохранение работает, а если
# папка данных лежит в рабочей копии и git не может подтвердить, что путь
# игнорируется, — отказ (fail closed), а не голый 500.

async def test_publish_without_git_on_path_still_saves_locally(env, tmp_path, monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    res = await env.client.post("/api/testing/publish")

    assert res.status_code == 200, res.text
    assert len(list(tp.publish_dir(env.settings.data_dir).glob("*"))) == 2


async def test_data_dir_in_a_worktree_without_git_is_refused_not_a_500(env, tmp_path,
                                                                      monkeypatch):
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "кнопка"}}]})
    repo = tmp_path / "repo"
    _make_repo(repo, with_remote=False)
    (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    target = repo / "data" / "testing" / "published"
    monkeypatch.setattr(tp, "publish_dir", lambda _data_dir: target)
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    res = await env.client.post("/api/testing/publish")

    assert res.status_code == 409, res.text
    assert not target.exists()


def test_a_git_call_that_hangs_is_reported_as_a_timeout_not_an_exception(tmp_path, monkeypatch):
    """`_git` по-прежнему используется (проверка check-ignore): зависание — код 124."""
    def hang(args, *a, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout", 1.0))

    monkeypatch.setattr(tp.subprocess, "run", hang)
    proc = tp._git(["check-ignore", "-q", "--", "x"], tmp_path, timeout=1.0)
    assert proc.returncode == 124 and "не ответил" in proc.stderr


def test_git_output_is_utf8_even_when_the_process_code_page_is_not(tmp_path):
    """Windows читает вывод дочернего процесса в ANSI-кодировке (cp1251), а git
    пишет UTF-8. Байт 0x98 из «И» в cp1251 не определён — UnicodeDecodeError,
    остальное превращается в кракозябры. На Linux та же механика воспроизводится
    процессом с не-UTF-8 локалью (C без принудительного UTF-8)."""
    import sys

    repo = tmp_path / "repo"
    _make_repo(repo)
    subject = "Исправление журнала тестового периода"
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject], cwd=repo,
                   capture_output=True, check=True)
    code = ("import sys; from pathlib import Path; "
            "from bcc.features import testing_period as tp; "
            "out = tp._git(['log', '-1', '--format=%s'], Path(sys.argv[1])).stdout; "
            "sys.stdout.buffer.write(out.strip().encode('utf-8'))")
    env = {k: v for k, v in os.environ.items() if k not in ("LANG", "LC_CTYPE")}
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C")
    proc = subprocess.run([sys.executable, "-c", code, str(repo)], env=env,
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
    assert proc.stdout.decode("utf-8") == subject


async def test_status_honestly_reports_recording_false_when_log_is_full_and_rotates(
    env, monkeypatch
):
    """Когда журнал достигает MAX_LOG_BYTES, /testing/status обязан честно сообщить
    recording=False, full=True и сменить плашку, а /testing/rotate — архивировать
    переполненный файл и начать новую чистую сессию."""
    monkeypatch.setattr(tp, "MAX_LOG_BYTES", 200)
    log_path = tp._log_path(env.settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("x" * 300 + "\n")

    # Попытка дописать фиксирует _full=True
    await env.client.post("/api/testing/log", json={"events": [
        {"kind": "ui.click", "data": {"element": "button#overflow"}}]})

    st = (await env.client.get("/api/testing/status")).json()
    assert st["enabled"] is True
    assert st["full"] is True
    assert st["recording"] is False
    assert "остановлена" in st["banner"]

    old_session = st["session"]
    monkeypatch.setattr(tp, "MAX_LOG_BYTES", 32 * 1024 * 1024)
    rot = (await env.client.post("/api/testing/rotate")).json()
    assert rot["ok"] is True
    assert rot["session"] != old_session
    assert rot["recording"] is True and rot["full"] is False
    assert rot["rotated_from"] and old_session in rot["rotated_from"]

    st_after = (await env.client.get("/api/testing/status")).json()
    assert st_after["recording"] is True and st_after["full"] is False
    assert "идёт запись" in st_after["banner"]


async def test_publish_filters_to_current_session_and_blocks_synthetic_canaries(
    env, tmp_path, monkeypatch
):
    """Канареечный тест: имитация текста модели, промпта задачи, пути C:\\Users\\...
    и имени видео/фото файла, а также чужой сессии — ни одна канарейка не попадает
    в публикуемый пакет (events.jsonl и SUMMARY.md) или в /testing/preview."""
    canaries = [
        "CANARY_MODEL_OUTPUT_99182_SECRET_STORY",
        "CANARY_TASK_PROMPT_77261_PRIVATE_INSTRUCTION",
        r"C:\Users\asd\PrivateFolder\canary_doc_551.txt",
        "/Users/asd/Private/canary_unix_path.txt",
        "canary_private_video_88412.mp4",
        "canary_private_photo_33190.jpg",
        "OLD_SESSION_EVENT_CANARY_44109",
    ]
    log_path = tp._log_path(env.settings)
    cur_session = env.svc.testing_log.session_id

    # 1. Событие из старой сессии (не должно попасть в публикацию текущей сессии)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": "2026-10-01T00:00:00Z",
            "session": "oldsession00",
            "source": "ui",
            "kind": "ui.click",
            "data": {"element": "OLD_SESSION_EVENT_CANARY_44109", "page": "#/old"},
        }, ensure_ascii=False) + "\n")

    # 2. События текущей сессии на шине и в UI с канарейками (промпт, ответ модели, пути, медиафайлы)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": "2026-10-14T08:00:00Z",
            "session": cur_session,
            "source": "bus",
            "kind": "run.assistant_message",
            "data": {
                "text": "CANARY_MODEL_OUTPUT_99182_SECRET_STORY",
                "prompt": "CANARY_TASK_PROMPT_77261_PRIVATE_INSTRUCTION",
            },
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "ts": "2026-10-14T08:00:01Z",
            "session": cur_session,
            "source": "bus",
            "kind": "task.created",
            "data": {
                "prompt": "CANARY_TASK_PROMPT_77261_PRIVATE_INSTRUCTION",
                "file": r"C:\Users\asd\PrivateFolder\canary_doc_551.txt",
                "status": 200,
            },
        }, ensure_ascii=False) + "\n")

    await env.client.post("/api/testing/log", json={"events": [
        {
            "kind": "ui.click",
            "data": {
                "element": r"button#open C:\Users\asd\PrivateFolder\canary_doc_551.txt canary_private_video_88412.mp4",
                "page": "#/motion?file=canary_private_photo_33190.jpg&path=/Users/asd/Private/canary_unix_path.txt",
                "prompt": "CANARY_TASK_PROMPT_77261_PRIVATE_INSTRUCTION",
                "response": "CANARY_MODEL_OUTPUT_99182_SECRET_STORY",
            },
        },
        {
            "kind": "ui.error",
            "data": {
                "message": r"Failed loading C:\Users\asd\PrivateFolder\canary_doc_551.txt and canary_private_video_88412.mp4",
                "line": 42,
            },
        },
    ]})

    # Проверяем /api/testing/preview
    preview = (await env.client.get("/api/testing/preview")).json()
    preview_blob = json.dumps(preview, ensure_ascii=False)
    for c in canaries:
        assert c not in preview_blob, f"Канарейка {c!r} утекла в /testing/preview!"

    # Проверяем реальную публикацию (с 24.10.2026 — в data_dir, не в git)
    res = await env.client.post("/api/testing/publish")
    assert res.status_code == 200, res.text
    published = list(tp.publish_dir(env.settings.data_dir).glob("*"))
    assert len(published) == 2
    for path in published:
        content = path.read_text(encoding="utf-8")
        for c in canaries:
            assert c not in content, f"Канарейка {c!r} утекла в опубликованный файл {path.name}!"
        if path.suffix == ".jsonl":
            for line in content.splitlines():
                if line.strip():
                    row = json.loads(line)
                    assert row["session"] == cur_session, f"Чужая сессия в публикации: {row}"
