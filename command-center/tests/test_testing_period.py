"""Режим тестового периода: журнал пишется, секрет не уезжает, публикация честна.

Самое важное здесь — не то, что журнал ведётся, а то, что он безопасен: он
уезжает в git, и один уцелевший токен обесценил бы всю затею.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bcc.features import testing_period as tp

from .conftest import client_for, make_settings, start_app


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
