"""Зашифрованный архив чатов (22.10): долгое нажатие -> «архивировать в зашифрованную папку».

ТОЛЬКО синтетические файлы. В «чат» кладётся маркер CANARY; он не должен попасть
ни в один ответ API, ни в журнал, ни в текст ошибки, ни в сам файл архива.
Ключ не должен попасть никуда, кроме файла ключа (обёрнутого DPAPI на Windows).
Файлы реальных чатов тест не читает и не создаёт: все пути во временной папке.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bcc.features import chat_archive as ca

CANARY = "CANARY-7f3a9c-do-not-leak-private-chat-text"
TID = "20261022-101010-abc123"
OTHER = "20261022-111111-def456"


@pytest.fixture(autouse=True)
def _keys_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("BOSSMAN_KEYS_DIR", raising=False)


def _sessions(env) -> Path:
    return env.settings.data_dir / "terminal" / "sessions"


def _meta(env) -> dict:
    path = env.settings.data_dir / "terminal" / "threads-meta.json"
    return json.loads(path.read_text(encoding="utf-8"))["threads"] if path.exists() else {}


def _make(env, thread_id: str = TID, title: str | None = None) -> bytes:
    """Синтетический «чат»: байты с маркером. Возвращает содержимое файла."""
    root = _sessions(env)
    root.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"id": thread_id, "turns": [], "note": CANARY}, ensure_ascii=False).encode("utf-8")
    (root / f"{thread_id}.json").write_bytes(data)
    if title is not None:
        meta_path = env.settings.data_dir / "terminal" / "threads-meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"version": 1, "threads": {}}
        existing["threads"][thread_id] = {"title": title}
        meta_path.write_text(json.dumps(existing), encoding="utf-8")
    return data


def _arch(env, thread_id: str = TID) -> Path:
    return env.settings.data_dir / "chat-archive" / f"{thread_id}.bcarchive"


def _key_file(env) -> Path:
    return ca.key_path(env.svc)


async def _archive(env, thread_id: str = TID):
    return await env.client.post(f"/api/chat/threads/{thread_id}/archive-encrypted")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _assert_clean(*texts: str, env=None) -> None:
    for text in texts:
        assert CANARY not in text
    if env is not None:
        key = ca.load_key(env.svc, create_if_no_archives=False)
        for text in texts:
            assert key.hex() not in text.lower()


async def test_roundtrip_sha256_equal_and_no_plaintext_on_disk(env, caplog):
    caplog.set_level("DEBUG")
    original = _make(env, title="Рабочий чат")
    seen: list[str] = []

    r = await _archive(env)
    seen.append(r.text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["encrypted"] is True and body["id"] == TID
    assert not (_sessions(env) / f"{TID}.json").exists(), "живой файл убран только после проверки"
    blob = _arch(env).read_bytes()
    assert blob.startswith(ca.MAGIC) and blob[len(ca.MAGIC)] == ca.VERSION
    assert CANARY.encode() not in blob and original not in blob, "на диске не открытый текст"
    meta = _meta(env)[TID]
    assert meta["archived"] is True and meta["encrypted"] is True and meta["title"] == "Рабочий чат"

    lst = await env.client.get("/api/chat/archive")
    seen.append(lst.text)
    assert lst.status_code == 200
    item = lst.json()["items"][0]
    assert set(item) == {"id", "title", "archived_at", "size"}
    assert item["id"] == TID and item["title"] == "Рабочий чат" and item["size"] == len(blob)
    live = await env.client.get("/api/chat/threads", params={"archived": 1})
    assert TID not in live.text, "в обычном списке чатов зашифрованного архива нет"

    r = await env.client.post(f"/api/chat/archive/{TID}/restore")
    seen.append(r.text)
    assert r.status_code == 200 and r.json()["restored"] is True
    restored = (_sessions(env) / f"{TID}.json").read_bytes()
    assert _sha(restored) == _sha(original) and restored == original
    assert not _arch(env).exists()
    assert _meta(env)[TID]["archived"] is False and "encrypted" not in _meta(env)[TID]

    _make(env, OTHER)    # ещё один: ключ повторно используется, не создаётся заново
    key_before = _key_file(env).read_bytes()
    assert (await _archive(env, OTHER)).status_code == 200
    assert _key_file(env).read_bytes() == key_before

    _assert_clean(*seen, caplog.text, env=env)
    key = ca.load_key(env.svc, create_if_no_archives=False)
    if sys.platform == "win32":
        assert key not in _key_file(env).read_bytes(), "ключ на диске обёрнут DPAPI"


async def test_tampered_archive_fails_restore_and_loses_nothing(env):
    original = _make(env)
    assert (await _archive(env)).status_code == 200
    good = _arch(env).read_bytes()
    bad = bytearray(good)
    bad[-20] ^= 0x01                              # один байт шифртекста
    _arch(env).write_bytes(bytes(bad))
    r = await env.client.post(f"/api/chat/archive/{TID}/restore")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CHAT_ARCHIVE_CORRUPT"
    _assert_clean(r.text)
    assert not (_sessions(env) / f"{TID}.json").exists(), "битый архив не создаёт полупустой чат"
    assert _arch(env).read_bytes() == bytes(bad), "архив не тронут"
    _arch(env).write_bytes(good)                  # исправили -> всё открывается
    assert (await env.client.post(f"/api/chat/archive/{TID}/restore")).status_code == 200
    assert (_sessions(env) / f"{TID}.json").read_bytes() == original


async def test_wrong_thread_id_as_aad_fails(env):
    _make(env)
    assert (await _archive(env)).status_code == 200
    _arch(env, OTHER).write_bytes(_arch(env).read_bytes())    # чужой id: подмена имени файла
    r = await env.client.post(f"/api/chat/archive/{OTHER}/restore")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CHAT_ARCHIVE_CORRUPT"
    assert not (_sessions(env) / f"{OTHER}.json").exists()
    key = ca.load_key(env.svc, create_if_no_archives=False)
    with pytest.raises(ValueError):
        ca.decrypt_bytes(key, OTHER, _arch(env).read_bytes())
    assert ca.decrypt_bytes(key, TID, _arch(env).read_bytes())


async def test_failure_after_encrypt_before_delete_keeps_live_chat(env, monkeypatch):
    original = _make(env, title="Остаётся")
    # проверка чтения-обратно падает: живой чат не тронут, архива и пометок нет
    monkeypatch.setattr(ca, "decrypt_bytes", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    r = await _archive(env)
    assert r.status_code == 500 and r.json()["error"]["code"] == "CHAT_ARCHIVE_VERIFY_FAILED"
    _assert_clean(r.text)
    assert (_sessions(env) / f"{TID}.json").read_bytes() == original
    assert not _arch(env).exists()
    assert "encrypted" not in _meta(env).get(TID, {}) and _meta(env)[TID].get("archived") is not True
    monkeypatch.undo()


async def test_failure_removing_live_file_rolls_everything_back(env, monkeypatch):
    original = _make(env, title="Остаётся")
    real_unlink = Path.unlink

    def flaky(self, *a, **k):
        if self.name == f"{TID}.json":
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky)
    r = await _archive(env)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAT_THREAD_BUSY"
    assert (_sessions(env) / f"{TID}.json").read_bytes() == original
    assert not _arch(env).exists()
    assert _meta(env)[TID] == {"title": "Остаётся"}, "метаданные откатились к прежним"


async def test_chat_changed_during_archive_is_refused(env, monkeypatch):
    _make(env)
    real = ca._sha
    calls = {"n": 0}

    def changing(data: bytes) -> str:
        calls["n"] += 1
        if calls["n"] == 2:          # между проверкой архива и удалением живого файла чат дописали
            (_sessions(env) / f"{TID}.json").write_bytes(b'{"turns": [], "more": 1}')
        return real(data)

    monkeypatch.setattr(ca, "_sha", changing)
    r = await _archive(env)
    monkeypatch.setattr(ca, "_sha", real)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAT_ARCHIVE_CHANGED"
    assert (_sessions(env) / f"{TID}.json").exists() and not _arch(env).exists()


async def test_restore_conflict_is_409_and_archive_survives(env):
    _make(env)
    assert (await _archive(env)).status_code == 200
    blocker = _make(env)                          # такой id уже занят живым чатом
    r = await env.client.post(f"/api/chat/archive/{TID}/restore")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAT_THREAD_EXISTS"
    assert _arch(env).exists() and (_sessions(env) / f"{TID}.json").read_bytes() == blocker
    again = await _archive(env)
    assert again.status_code == 409 and again.json()["error"]["code"] == "CHAT_ARCHIVE_EXISTS"


async def test_missing_key_with_existing_archives_is_503_and_no_new_key(env):
    _make(env)
    _make(env, OTHER)
    assert (await _archive(env)).status_code == 200
    _key_file(env).unlink()
    r = await _archive(env, OTHER)
    assert r.status_code == 503 and r.json()["error"]["code"] == "CHAT_ARCHIVE_KEY_UNAVAILABLE"
    assert not _key_file(env).exists(), "новый ключ не создан: он осиротил бы архивы"
    assert (_sessions(env) / f"{OTHER}.json").exists() and not _arch(env, OTHER).exists()
    r = await env.client.post(f"/api/chat/archive/{TID}/restore")
    assert r.status_code == 503 and r.json()["error"]["code"] == "CHAT_ARCHIVE_KEY_UNAVAILABLE"
    assert _arch(env).exists()
    assert (await env.client.get("/api/chat/archive")).status_code == 200, "список ключа не требует"
    assert (await env.client.delete(f"/api/chat/archive/{TID}")).status_code == 200, "удалить архив можно и без ключа"


async def test_corrupt_key_file_fails_closed(env):
    _make(env)
    assert (await _archive(env)).status_code == 200
    original_key = _key_file(env).read_bytes()
    _key_file(env).write_bytes(b"garbage")
    r = await env.client.post(f"/api/chat/archive/{TID}/restore")
    assert r.status_code == 503 and r.json()["error"]["code"] == "CHAT_ARCHIVE_KEY_UNAVAILABLE"
    assert _key_file(env).read_bytes() == b"garbage", "повреждённый ключ не перезаписан"
    _key_file(env).write_bytes(original_key)
    assert (await env.client.post(f"/api/chat/archive/{TID}/restore")).status_code == 200


async def test_list_has_titles_from_metadata_only(env):
    _make(env, title=None)                         # названия в метаданных нет
    _make(env, OTHER, title="Поставки")
    assert (await _archive(env)).status_code == 200 and (await _archive(env, OTHER)).status_code == 200
    items = {i["id"]: i for i in (await env.client.get("/api/chat/archive")).json()["items"]}
    assert items[TID]["title"] is None and items[OTHER]["title"] == "Поставки"
    assert CANARY not in json.dumps(items, ensure_ascii=False)


async def test_delete_archive_and_bad_ids(env):
    _make(env)
    assert (await _archive(env)).status_code == 200
    assert (await env.client.delete(f"/api/chat/archive/{TID}")).json() == {"ok": True, "deleted": True}
    assert not _arch(env).exists() and TID not in _meta(env)
    assert (await env.client.delete(f"/api/chat/archive/{TID}")).status_code == 404
    assert (await env.client.post(f"/api/chat/archive/{TID}/restore")).status_code == 404
    assert (await _archive(env)).status_code == 404
    for bad in ("x", "COM1", "a b", "..%2Fx"):
        for call in (env.client.post(f"/api/chat/threads/{bad}/archive-encrypted"),
                     env.client.post(f"/api/chat/archive/{bad}/restore"),
                     env.client.delete(f"/api/chat/archive/{bad}")):
            assert (await call).status_code in (404, 405, 422)


async def test_purge_needs_typed_confirmation_from_the_phone(env):
    original = _make(env)
    mobile = {"X-Bossman-Surface": "mobile"}
    url = f"/api/chat/threads/{TID}"
    r = await env.client.delete(url, params={"purge": 1}, headers=mobile)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CHAT_PURGE_CONFIRM_REQUIRED"
    r = await env.client.delete(url, params={"purge": 1, "confirm": OTHER}, headers=mobile)
    assert r.status_code == 422
    r = await env.client.delete(url, params={"purge": 1, "confirm": OTHER})      # неверный confirm отвергается и без заголовка
    assert r.status_code == 422
    assert (_sessions(env) / f"{TID}.json").read_bytes() == original
    r = await env.client.delete(url, params={"purge": 1, "confirm": TID}, headers=mobile)
    assert r.status_code == 200 and r.json()["purged"] is True
    assert not (_sessions(env) / f"{TID}.json").exists()


def test_key_file_is_never_tracked_or_left_untracked_in_the_repo():
    root = Path(__file__).resolve().parents[2]
    try:
        tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                                 capture_output=True, text=True, timeout=60, check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git недоступен")
    bad = [p for p in tracked if p.endswith(ca.KEY_FILE_NAME) or p.endswith(ca.ARCHIVE_SUFFIX)]
    assert bad == [], f"ключ/архив не должен быть в git: {bad}"


def test_pure_crypto_properties():
    key = b"\x01" * ca.KEY_LEN
    a, b = ca.encrypt_bytes(key, TID, b"payload"), ca.encrypt_bytes(key, TID, b"payload")
    assert a != b, "nonce случайный"
    assert ca.decrypt_bytes(key, TID, a) == b"payload"
    for blob in (b"", b"BCARC", a[:20], b"XXXXX" + a[5:], a[:5] + b"\x09" + a[6:]):
        with pytest.raises(ValueError):
            ca.decrypt_bytes(key, TID, blob)
    with pytest.raises(ValueError):
        ca.decrypt_bytes(b"\x02" * ca.KEY_LEN, TID, a)
