"""Зашифрованный архив чатов — `/api/chat/archive*` и `/api/chat/threads/{id}/archive-encrypted`.

Владелец (22.10): «Долгим нажатием на чат можно навсегда удалить его или
архивировать в зашифрованную папку».

Файл сессии чата для этого модуля — НЕПРОЗРАЧНЫЕ БАЙТЫ: он не разбирается, не
логируется и не попадает ни в один ответ. Архив = AES-256-GCM (nonce 12 байт
случайный, id чата как AAD) в `<data_dir>/chat-archive/<id>.bcarchive`:
`MAGIC(5) | VERSION(1) | NONCE(12) | CIPHERTEXT+TAG`.

Порядок архивации (чат не теряется ни при каком сбое): зашифровать -> записать
рядом атомарно -> прочитать с диска и расшифровать обратно, сверить sha256 ->
убедиться, что живой файл не менялся -> пометить метаданные -> только потом
удалить живой файл. Пока проверка не прошла, живой файл и есть резервная копия;
любой сбой откатывает метаданные и удаляет только новый архив.

Ключ: 32 случайных байта, `%LOCALAPPDATA%\\Bossman\\keys\\chat-archive.key`,
обёрнут Windows DPAPI (область текущего пользователя). Вне Windows (тесты) —
файл 0600 и предупреждение в журнале. Ключ не логируется, не возвращается API и
не в git. Нет ключа при существующих архивах — 503, новый ключ НЕ создаётся
(он осиротил бы архивы).

Названия в списке архива — только из метаданных окна (threads-meta.json), не из
расшифрованного содержимого. Вложения чата (`chat/attachments`) этот модуль не
шифрует.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Request

from . import Feature
from .chat_threads import (THREAD_ID_RE, _entry, _fail, _iso, _meta_path, _mutate_meta,
                           _mutate_meta_sync, _read_meta, _session_path, _state, _svc,
                           _WINDOWS_DEVICE_NAMES)

log = logging.getLogger("bcc.chat_archive")
router = APIRouter()

MAGIC = b"BCARC"
VERSION = 1
NONCE_LEN = 12
KEY_LEN = 32
HEADER_LEN = len(MAGIC) + 1 + NONCE_LEN
ARCHIVE_SUFFIX = ".bcarchive"
KEY_FILE_NAME = "chat-archive.key"
KEY_MAGIC_DPAPI = b"BCKEY-DPAPI1\n"
KEY_MAGIC_PLAIN = b"BCKEY-PLAIN1\n"
_DPAPI_ENTROPY = b"bossman-chat-archive-key-v1"
KEY_UNAVAILABLE = "CHAT_ARCHIVE_KEY_UNAVAILABLE"
_KEY_LOCK = threading.Lock()


# ======================================================================
# Пути
# ======================================================================

def archive_dir(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "chat-archive"


def keys_dir(svc: Any) -> Path:
    """BOSSMAN_KEYS_DIR (тест/переопределение) -> %LOCALAPPDATA%\\Bossman\\keys -> <data_dir>/keys."""
    override = os.environ.get("BOSSMAN_KEYS_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Bossman" / "keys"
    return Path(svc.settings.data_dir) / "keys"


def key_path(svc: Any) -> Path:
    return keys_dir(svc) / KEY_FILE_NAME


def _check_id(thread_id: str) -> str:
    if (not isinstance(thread_id, str) or not THREAD_ID_RE.fullmatch(thread_id)
            or thread_id.upper() in _WINDOWS_DEVICE_NAMES):
        raise _fail(422, "неверный id чата", code="CHAT_THREAD_ID_INVALID")
    return thread_id


def _archive_path(svc: Any, thread_id: str) -> Path:
    _check_id(thread_id)
    root = archive_dir(svc)
    path = root / f"{thread_id}{ARCHIVE_SUFFIX}"
    if path.resolve().parent != root.resolve():
        raise _fail(422, "неверный id чата", code="CHAT_THREAD_ID_INVALID")
    return path


def _has_archives(svc: Any) -> bool:
    try:
        return any(archive_dir(svc).glob(f"*{ARCHIVE_SUFFIX}"))
    except OSError:
        return False


# ======================================================================
# DPAPI и ключ
# ======================================================================

def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData, область текущего пользователя."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    buf = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_ent = Blob(len(_DPAPI_ENTROPY), ctypes.cast(ent, ctypes.c_void_p))
    blob_out = Blob()
    if not fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, 0x1,  # UI_FORBIDDEN
              ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _wrap_key(key: bytes) -> bytes:
    if sys.platform == "win32":
        return KEY_MAGIC_DPAPI + _dpapi(key, protect=True)
    log.warning("chat-archive: DPAPI недоступен на этой ОС, ключ хранится файлом 0600")
    return KEY_MAGIC_PLAIN + key


def _unwrap_key(blob: bytes) -> bytes:
    if blob.startswith(KEY_MAGIC_DPAPI):
        if sys.platform != "win32":
            raise ValueError("dpapi key on non-windows")
        key = _dpapi(blob[len(KEY_MAGIC_DPAPI):], protect=False)
    elif blob.startswith(KEY_MAGIC_PLAIN):
        key = blob[len(KEY_MAGIC_PLAIN):]
    else:
        raise ValueError("unknown key file")
    if len(key) != KEY_LEN:
        raise ValueError("bad key length")
    return key


def _key_unavailable(hint: str) -> Exception:
    return _fail(503, "ключ шифрованного архива недоступен", code=KEY_UNAVAILABLE, hint=hint)


def load_key(svc: Any, *, create_if_no_archives: bool) -> bytes:
    """Ключ из файла. Нет файла: если архивы уже есть — 503; если нет — создаётся первый."""
    path = key_path(svc)
    with _KEY_LOCK:
        if path.exists():
            try:
                return _unwrap_key(path.read_bytes())
            except (OSError, ValueError):
                raise _key_unavailable("файл ключа повреждён или принадлежит другому пользователю Windows; "
                                       "существующие архивы без него не открыть") from None
        if not create_if_no_archives:
            raise _key_unavailable("файл ключа отсутствует")
        if _has_archives(svc):
            raise _key_unavailable("файл ключа отсутствует, а архивы уже есть: новый ключ не создаётся, "
                                   "он осиротил бы их")
        key = secrets.token_bytes(KEY_LEN)
        try:
            blob = _wrap_key(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except OSError:
            raise _key_unavailable("ключ не удалось сохранить") from None
        log.info("chat-archive: создан новый ключ шифрования")
        return key


# ======================================================================
# Шифрование (чистые функции)
# ======================================================================

def encrypt_bytes(key: bytes, thread_id: str, data: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_LEN)
    return MAGIC + bytes([VERSION]) + nonce + AESGCM(key).encrypt(nonce, data, thread_id.encode("utf-8"))


def decrypt_bytes(key: bytes, thread_id: str, blob: bytes) -> bytes:
    """ValueError на любой порче, подмене id или чужом ключе (текст ошибки без данных)."""
    if len(blob) < HEADER_LEN + 16 or not blob.startswith(MAGIC) or blob[len(MAGIC)] != VERSION:
        raise ValueError("not a chat archive")
    nonce = blob[len(MAGIC) + 1:HEADER_LEN]
    try:
        return AESGCM(key).decrypt(nonce, blob[HEADER_LEN:], thread_id.encode("utf-8"))
    except InvalidTag:
        raise ValueError("authentication failed") from None


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ======================================================================
# Архивация и восстановление (синхронно, под замком чатов)
# ======================================================================

def _archive_sync(svc: Any, thread_id: str, live: Path, target: Path) -> dict:
    if target.exists():
        raise _fail(409, "архив этого чата уже есть", code="CHAT_ARCHIVE_EXISTS")
    try:
        data = live.read_bytes()
    except FileNotFoundError:
        raise _fail(404, "чат не найден", code="CHAT_THREAD_NOT_FOUND") from None
    except OSError:
        raise _fail(409, "файл чата сейчас занят", code="CHAT_THREAD_BUSY") from None
    digest = _sha(data)
    key = load_key(svc, create_if_no_archives=True)
    created = False
    meta_changed = False
    previous: Any = None
    try:
        _write_bytes_atomic(target, encrypt_bytes(key, thread_id, data))
        created = True
        try:
            back = decrypt_bytes(key, thread_id, target.read_bytes())
        except (OSError, ValueError):
            back = None
        if back is None or _sha(back) != digest:
            raise _fail(500, "проверка архива не прошла: чат оставлен как был", code="CHAT_ARCHIVE_VERIFY_FAILED")
        try:
            if _sha(live.read_bytes()) != digest:
                raise _fail(409, "чат изменился во время архивации: повторите", code="CHAT_ARCHIVE_CHANGED")
        except OSError:
            raise _fail(409, "файл чата сейчас занят", code="CHAT_THREAD_BUSY") from None
        now = time.time()

        def mark(threads: dict) -> None:
            nonlocal previous
            entry = threads.get(thread_id)
            previous = dict(entry) if isinstance(entry, dict) else None
            if not isinstance(entry, dict):
                entry = threads[thread_id] = {}
            entry.update(archived=True, encrypted=True, archived_at=now, updated_at=now)

        _mutate_meta_sync(_meta_path(svc), mark)
        meta_changed = True
        try:
            live.unlink()
        except OSError:
            raise _fail(409, "не удалось убрать живой файл чата: архив отменён, чат на месте",
                        code="CHAT_THREAD_BUSY") from None
    except BaseException:
        if meta_changed:
            def rollback(threads: dict) -> None:
                if previous is None:
                    threads.pop(thread_id, None)
                else:
                    threads[thread_id] = previous
            with contextlib.suppress(Exception):
                _mutate_meta_sync(_meta_path(svc), rollback)
        if created:
            with contextlib.suppress(OSError):
                target.unlink()
        raise
    log.info("chat-archive: чат %s зашифрован (%d байт)", thread_id, len(data))
    return {"id": thread_id, "archived_at": _iso(now), "size": target.stat().st_size}


def _restore_sync(svc: Any, thread_id: str, source: Path, live: Path) -> dict:
    try:
        blob = source.read_bytes()
    except FileNotFoundError:
        raise _fail(404, "архив не найден", code="CHAT_ARCHIVE_NOT_FOUND") from None
    except OSError:
        raise _fail(409, "файл архива сейчас занят", code="CHAT_ARCHIVE_BUSY") from None
    key = load_key(svc, create_if_no_archives=False)
    try:
        data = decrypt_bytes(key, thread_id, blob)
    except ValueError:
        raise _fail(422, "архив повреждён или не подходит к ключу: он не тронут",
                    code="CHAT_ARCHIVE_CORRUPT") from None
    if live.exists():
        raise _fail(409, "чат с таким id уже существует", code="CHAT_THREAD_EXISTS")
    _write_bytes_atomic(live, data)
    try:
        ok = _sha(live.read_bytes()) == _sha(data)
    except OSError:
        ok = False
    if not ok:
        with contextlib.suppress(OSError):
            live.unlink()
        raise _fail(500, "проверка восстановленного чата не прошла: архив не тронут",
                    code="CHAT_ARCHIVE_VERIFY_FAILED")

    def unmark(threads: dict) -> None:
        entry = threads.get(thread_id)
        if isinstance(entry, dict):
            entry.pop("encrypted", None)
            entry.pop("archived_at", None)
            entry.update(archived=False, updated_at=time.time())

    _mutate_meta_sync(_meta_path(svc), unmark)
    with contextlib.suppress(OSError):
        source.unlink()
    log.info("chat-archive: чат %s восстановлен", thread_id)
    return {"ok": True, "id": thread_id, "restored": True}


# ======================================================================
# Маршруты
# ======================================================================

@router.post("/chat/threads/{thread_id}/archive-encrypted")
async def archive_encrypted(thread_id: str, request: Request):
    svc = _svc(request)
    live = _session_path(svc, thread_id)
    if not live.is_file():
        raise _fail(404, "чат не найден", code="CHAT_THREAD_NOT_FOUND")
    target = _archive_path(svc, thread_id)
    state = _state(svc)
    async with state.lock:
        result = await asyncio.to_thread(_archive_sync, svc, thread_id, live, target)
        state.sessions.pop(thread_id, None)
    return {"ok": True, "encrypted": True, **result}


@router.post("/chat/archive/{thread_id}/restore")
async def restore_archive(thread_id: str, request: Request):
    svc = _svc(request)
    source = _archive_path(svc, thread_id)
    live = _session_path(svc, thread_id)
    state = _state(svc)
    async with state.lock:
        result = await asyncio.to_thread(_restore_sync, svc, thread_id, source, live)
        state.sessions.pop(thread_id, None)
    return result


@router.get("/chat/archive")
async def list_archive(request: Request):
    """Только метаданные: id, название из threads-meta.json, время, размер файла."""
    svc = _svc(request)
    meta = _read_meta(svc)
    items = []
    try:
        files = sorted(archive_dir(svc).glob(f"*{ARCHIVE_SUFFIX}"))
    except OSError:
        files = []
    for path in files:
        thread_id = path.name[:-len(ARCHIVE_SUFFIX)]
        if not THREAD_ID_RE.fullmatch(thread_id) or not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        raw = meta.get(thread_id) or {}
        at = raw.get("archived_at")
        at = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else st.st_mtime
        items.append({"id": thread_id, "title": _entry(meta, thread_id)["title"],
                      "archived_at": _iso(at), "size": st.st_size, "_at": at})
    items.sort(key=lambda it: -it["_at"])
    for it in items:
        it.pop("_at")
    return {"items": items, "total": len(items)}


@router.delete("/chat/archive/{thread_id}")
async def delete_archive(thread_id: str, request: Request):
    svc = _svc(request)
    path = _archive_path(svc, thread_id)
    state = _state(svc)
    async with state.lock:
        if not path.is_file():
            raise _fail(404, "архив не найден", code="CHAT_ARCHIVE_NOT_FOUND")
        try:
            await asyncio.to_thread(path.unlink)
        except OSError:
            raise _fail(409, "файл архива сейчас занят", code="CHAT_ARCHIVE_BUSY") from None

    def forget(threads: dict) -> None:
        entry = threads.get(thread_id)
        if isinstance(entry, dict) and entry.get("encrypted") is True:
            threads.pop(thread_id, None)

    await _mutate_meta(svc, forget)
    return {"ok": True, "deleted": True}


FEATURE = Feature(name="chat_archive", router=router)
