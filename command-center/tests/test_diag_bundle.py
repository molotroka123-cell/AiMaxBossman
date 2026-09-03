"""«Отправить отчёт о сбое»: архив собирается, а секретов в нём нет.

Проверяем не форму ответа, а обещание модуля: настоящий токен инсталляции не
встречается в архиве НИ РАЗУ (побайтово, по всему содержимому), чистка честно
считает свои срабатывания, отсутствие файлов сбор не ломает, выключенный флаг
не оставляет в data_dir ничего, а большой лог кладётся хвостом, а не целиком.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bcc.features.diag_bundle import (BUNDLE_DIRNAME, EVENTS_NAME, FILES_NAME, FLAG,
                                      LOG_TAIL_BYTES, MARK, MAX_BUNDLE_BYTES, REPORT_NAME)

# Похоже на ключ провайдера, но ключом не является: ищем его в архиве как
# канарейку для паттернов (сам токен инсталляции ищем отдельно).
FAKE_KEY = "sk-live-DIAGBUNDLE-CANARY-QZWX-0123456789"  # ci-secret-scan: allow


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


async def _post(env, expect: int = 200):
    resp = await env.client.post("/api/diag/bundle")
    assert resp.status_code == expect, resp.text
    return resp.json()


def _bundle_dir(env) -> Path:
    return Path(env.settings.data_dir) / BUNDLE_DIRNAME


def _zips(env) -> list[Path]:
    return sorted(Path(env.settings.data_dir).rglob("*.zip"))


async def _seed_secrets(env) -> str:
    """Токен инсталляции в логе, в замке и в ленте событий — как в жизни."""
    token = env.svc.auth.token
    data_dir = Path(env.settings.data_dir)
    # Файл токена заводит сам сервер: убеждаемся, что это тот самый токен.
    assert (data_dir / "token").read_text(encoding="utf-8").strip() == token
    (data_dir / "desktop-run.log").write_text(
        "\n".join([
            "2026-09-03T10:00:00 старт окна",
            f"2026-09-03T10:00:01 вход по адресу http://127.0.0.1:8800/?token={token}",
            f"2026-09-03T10:00:02 X-BCC-Token {token}",
            f"2026-09-03T10:00:03 Authorization: Bearer {token}",
            f"2026-09-03T10:00:04 api_key={FAKE_KEY}",
            "2026-09-03T10:00:05 окно не открылось: chrome not found",
        ]), encoding="utf-8")
    (data_dir / "desktop-console.log").write_text(
        f"=== запуск без консоли ===\n[bcc] токен доступа: {token}\n", encoding="utf-8")
    (data_dir / "desktop.lock").write_text(
        json.dumps({"pid": 424242, "port": 8800, "note": f"token {token}"}), encoding="utf-8")
    (data_dir / "secret.key").write_bytes(b"fernet-key-must-never-be-shipped-0123456789=")
    (data_dir / ".env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    await env.svc.bus.emit("diag.test", note=f"окно упало, токен был {token}")
    return token


def _members(path: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None            # архив открывается и не бит
        return {name: zf.read(name) for name in zf.namelist()}


# ---------------------------------------------------------------- сборка

async def test_bundle_is_built_and_opens_with_expected_parts(env, on):
    await _seed_secrets(env)
    made = await _post(env)

    path = Path(made["path"])
    assert path.is_file() and path.stat().st_size == made["size_bytes"] > 0
    assert path.parent == _bundle_dir(env), "архив кладётся в data_dir, не в репозиторий"

    members = _members(made["path"])
    for name in (REPORT_NAME, EVENTS_NAME, FILES_NAME,
                 "desktop-run.log", "desktop-console.log", "desktop.lock"):
        assert name in members, f"часть {name} не попала в архив: {sorted(members)}"

    report = json.loads(members[REPORT_NAME])
    assert report["environment"]["app_version"] and report["environment"]["python"]
    assert report["environment"]["platform"] and report["environment"]["os_name"]
    assert report["settings"]["port"] == env.settings.port
    assert report["settings"]["host"] == env.settings.host
    # порт проверяется в обход прокси из окружения — иначе ответ описывал бы прокси
    assert report["port_probe"]["checked"] is True
    assert report["port_probe"]["proxy_bypassed"] is True
    assert isinstance(report["port_probe"]["answering"], bool)
    # замок: и содержимое, и вердикт о живости записанного pid
    assert report["lock"]["pid"] == 424242 and report["lock"]["pid_alive"] is False
    # перечень файлов — имена и размеры, без содержимого
    listing = json.loads(members[FILES_NAME])
    names = {f["path"] for f in listing["files"]}
    assert "token" in names and all(isinstance(f["bytes"], int) for f in listing["files"])
    assert "files" in listing and all(set(f) == {"path", "bytes"} for f in listing["files"])


async def test_events_from_db_are_included(env, on):
    await env.svc.bus.emit("diag.marker", note="ровно это событие ищем в архиве")
    made = await _post(env)
    events = json.loads(_members(made["path"])[EVENTS_NAME])
    kinds = [e["kind"] for e in events]
    assert "diag.marker" in kinds, kinds


# ---------------------------------------------------- главное свойство: секретов нет

async def test_installation_token_never_appears_in_any_byte_of_archive(env, on):
    token = await _seed_secrets(env)
    made = await _post(env)

    raw = Path(made["path"]).read_bytes()
    assert token.encode() not in raw, "токен нашёлся в сыром файле архива"

    members = _members(made["path"])
    for name, blob in members.items():
        assert token.encode() not in blob, f"токен нашёлся в части {name}"
        assert FAKE_KEY.encode() not in blob, f"ключ провайдера нашёлся в части {name}"
        assert b"fernet-key-must-never-be-shipped" not in blob, f"secret.key утёк в {name}"

    # секретные файлы и база не кладутся вовсе
    assert "token" not in members and "secret.key" not in members and ".env" not in members
    assert not [n for n in members if n.endswith((".db", ".sqlite", ".sqlite3"))]
    # и это не «пустой архив»: лог на месте, просто вычищенный
    assert b"chrome not found" in members["desktop-run.log"]
    assert MARK.encode() in members["desktop-run.log"]


async def test_redaction_counter_reports_what_was_cleaned(env, on):
    await _seed_secrets(env)
    made = await _post(env)
    # в логах, замке и событии токен упомянут много раз — счётчик обязан это показать
    assert made["redactions"] >= 5, made["redactions"]
    per_part = {p["name"]: p.get("redactions", 0) for p in made["parts"]}
    assert per_part["desktop-run.log"] > 0 and per_part["desktop-console.log"] > 0
    assert per_part[EVENTS_NAME] > 0, "токен из ленты событий остался незамеченным"


async def test_clean_data_dir_reports_zero_redactions(env, on):
    """Счётчик не «всегда больше нуля»: чистить нечего — ноль."""
    (Path(env.settings.data_dir) / "desktop-run.log").write_text(
        "2026-09-03T10:00:00 окно открылось\n", encoding="utf-8")
    made = await _post(env)
    assert made["redactions"] == 0, made["parts"]


# ---------------------------------------------------------------- устойчивость

async def test_missing_files_do_not_break_the_build(env, on):
    """Ни логов, ни замка — сбор всё равно даёт архив и честно это отмечает."""
    data_dir = Path(env.settings.data_dir)
    for name in ("desktop-run.log", "desktop-console.log", "desktop.lock"):
        assert not (data_dir / name).exists()

    made = await _post(env)
    members = _members(made["path"])
    assert REPORT_NAME in members and FILES_NAME in members
    assert "desktop-run.log" not in members and "desktop.lock" not in members

    parts = {p["name"]: p for p in made["parts"]}
    assert parts["desktop-run.log"]["present"] is False
    assert parts["desktop-run.log"]["included"] is False
    assert parts["desktop-run.log"]["reason"]
    assert json.loads(members[REPORT_NAME])["lock"]["present"] is False


async def test_large_log_is_truncated_to_tail_and_bundle_stays_bounded(env, on):
    line = "2026-09-03T10:00:00 " + "x" * 100 + "\n"
    big = line * (2 * 1024 * 1024 // len(line))          # ~2 МБ
    (Path(env.settings.data_dir) / "desktop-run.log").write_text(big, encoding="utf-8")

    made = await _post(env)
    with zipfile.ZipFile(made["path"]) as zf:
        info = zf.getinfo("desktop-run.log")
    assert info.file_size <= LOG_TAIL_BYTES, "лог положили целиком вместо хвоста"
    assert info.file_size < len(big.encode()) / 2
    assert made["size_bytes"] < MAX_BUNDLE_BYTES

    part = {p["name"]: p for p in made["parts"]}["desktop-run.log"]
    assert part["truncated"] is True and part["file_bytes"] >= len(big.encode())


# ---------------------------------------------------------------- флаг

async def test_disabled_flag_refuses_and_leaves_no_archive(env, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert not _zips(env)
    await _post(env, expect=409)
    assert not _zips(env), "при выключенном флаге архив появляться не должен"
    assert not _bundle_dir(env).exists()


async def test_preview_works_with_flag_off_and_names_what_is_excluded(env, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    resp = await env.client.get("/api/diag/bundle")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    included = {p["name"] for p in body["will_include"]}
    assert {"desktop-run.log", "desktop-console.log", EVENTS_NAME, REPORT_NAME} <= included
    excluded = {p["path"] for p in body["will_exclude"]}
    assert "token" in excluded and "secret.key" in excluded and ".env" in excluded
    assert all(p["reason"] for p in body["will_exclude"])
    assert not _zips(env), "просмотр ничего не собирает"


async def test_preview_reports_enabled_when_flag_is_on(env, on):
    resp = await env.client.get("/api/diag/bundle")
    assert resp.status_code == 200 and resp.json()["enabled"] is True
