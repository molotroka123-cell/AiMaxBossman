"""Хранилище: неизменяемость, сверка по контрольной сумме, раскладка.

Инвариант, который здесь доказывается: **подменённое содержимое не доедет до
публикации**. Не «будет замечено в логе», а именно не доедет — чтение
подменённого файла завершается отказом, и вызывающий не получает байтов вовсе.
"""
from __future__ import annotations

import pytest

from social_farm.media.asset import AssetType, MediaAsset, checksum_of
from social_farm.media.store import (ChecksumMismatch, MediaStorageError,
                                     NAMESPACE_DERIVED, NAMESPACE_ORIGINAL,
                                     looks_like_credential_material)

from conftest import make_png


# --------------------------------------------------------------- раскладка

def test_layout_follows_the_storage_document(store, png):
    """`media/original/{asset_id}/{checksum}` из `64_STORAGE_LAYOUT`."""
    ref = store.put_original(png, asset_id="ast_1")
    assert ref == f"{NAMESPACE_ORIGINAL}/ast_1/{checksum_of(png)}"
    assert store.exists(ref)


def test_derived_assets_live_in_their_own_namespace(store, png):
    ref = store.put_derived(png, asset_id="ast_2")
    assert ref.startswith(NAMESPACE_DERIVED + "/")


def test_preview_ref_is_keyed_by_revision(store):
    assert store.preview_ref("rev_9", "cover.png") == "media/previews/rev_9/cover.png"


# --------------------------------------------------------------- неизменяемость

def test_the_stored_file_is_read_only(store, png):
    """Неизменяемость видна и на уровне файловой системы, а не только в коде."""
    path = store.path_of(store.put_original(png, asset_id="ast_1"))
    assert path.stat().st_mode & 0o222 == 0, "файл ассета доступен на запись"


def test_writing_the_same_content_twice_is_not_an_error(store, png):
    """Путь зависит от содержимого, значит повтор кладёт ровно то же самое."""
    first = store.put_original(png, asset_id="ast_1")
    assert store.put_original(png, asset_id="ast_1") == first


def test_different_content_cannot_take_an_existing_key(store, png):
    """Перезаписать ассет другим содержимым нельзя ПО ПОСТРОЕНИЮ.

    Другое содержимое — другая сумма — другой путь. Это не проверка, которую
    можно забыть выполнить; это свойство раскладки.
    """
    first = store.put_original(png, asset_id="ast_1")
    second = store.put_original(make_png(64, 64), asset_id="ast_1")
    assert first != second
    assert store.exists(first) and store.exists(second)


# --------------------------------------------------------------- подмена

def test_tampered_file_is_caught_on_read(store, png):
    """Главный тест файла: подменённый ассет не отдаётся вызывающему."""
    ref = store.put_original(png, asset_id="ast_1")
    path = store.path_of(ref)
    path.chmod(0o644)
    path.write_bytes(make_png(32, 32))          # подмена мимо нашего API

    with pytest.raises(ChecksumMismatch, match="не сходится"):
        store.read(ref)
    with pytest.raises(ChecksumMismatch):
        store.path_of(ref)


def test_tampered_file_is_caught_when_verifying_an_asset(store, png):
    """`verify` — та самая проверка, которую конвейер делает перед рендером."""
    ref = store.put_original(png, asset_id="ast_1")
    asset = MediaAsset(id="ast_1", type=AssetType.IMAGE, mime="image/png",
                       checksum_sha256=checksum_of(png), storage_ref=ref,
                       bytes=len(png), prober="header")
    store.verify(asset)                          # пока всё сходится

    path = store.path_of(ref)
    path.chmod(0o644)
    path.write_bytes(make_png(32, 32))
    with pytest.raises(ChecksumMismatch):
        store.verify(asset)


def test_an_asset_whose_content_was_never_stored_fails_verification(store, png):
    """Ассет с выдуманной ссылкой не проходит: сходиться нечему.

    Это тот барьер, из-за которого нельзя собрать «ассет» в обход хранилища.
    """
    ghost = MediaAsset(id="ast_ghost", type=AssetType.IMAGE, mime="image/png",
                       checksum_sha256=checksum_of(png),
                       storage_ref=f"{NAMESPACE_ORIGINAL}/ast_ghost/{checksum_of(png)}",
                       bytes=len(png), prober="header")
    with pytest.raises(MediaStorageError, match="нет объекта"):
        store.verify(ghost)


def test_declared_checksum_must_match_the_key(store, png):
    """Ассет, объявивший чужую сумму, не проходит, даже если файл цел."""
    ref = store.put_original(png, asset_id="ast_1")
    with pytest.raises(ChecksumMismatch):
        store.read(ref, expected_checksum=checksum_of(b"nonsense"))


# --------------------------------------------------------------- дедупликация

def test_identical_originals_collapse_onto_one_asset_id(store, png):
    """«Deduplicate by checksum where safe» (`13_MEDIA_LIBRARY`)."""
    checksum = checksum_of(png)
    assert store.register_checksum(checksum, "ast_first") == "ast_first"
    assert store.register_checksum(checksum, "ast_second") == "ast_first"
    assert store.find_by_checksum(checksum) == "ast_first"


def test_unknown_checksum_is_not_claimed_by_anyone(store):
    assert store.find_by_checksum("0" * 64) is None


# --------------------------------------------------------------- границы

@pytest.mark.parametrize("bad", ["../../etc/passwd", "media/../../../etc/passwd",
                                 "/etc/passwd", "media/original/../../x"])
def test_paths_cannot_escape_the_store(store, bad):
    with pytest.raises(MediaStorageError):
        store.read(bad)


def test_credential_material_is_refused_by_the_media_namespace(store):
    """`64_STORAGE_LAYOUT` запрещает держать секреты в медиапространстве.

    Проверяется на входе, а не ревью: экспорт токенов, попавший сюда по
    ошибке, пережил бы ревью и остался бы на диске.
    """
    token_export = b'{"access_token": "EAAG...", "expires_in": 3600}'
    with pytest.raises(MediaStorageError, match="учётные данные|токен"):
        store.put_original(token_export, asset_id="ast_leak")


@pytest.mark.parametrize("blob,expected", [
    (b'{"access_token": "x"}', "экспорт токенов"),
    (b'[{"name":"sessionid","httpOnly":true}]', "выгрузка cookie"),
    (b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/", "cookie-jar"),
    (b"-----BEGIN RSA PRIVATE KEY-----\nMIIE", "приватный ключ"),
])
def test_credential_shapes_are_recognised(blob, expected):
    assert looks_like_credential_material(blob) == expected


def test_ordinary_media_is_not_mistaken_for_a_secret(png):
    """Список признаков узкий: он ищет структуру, а не слово."""
    assert looks_like_credential_material(png) is None
    assert looks_like_credential_material(
        "Пост про то, как хранить пароли и токены".encode("utf-8")) is None


def test_empty_asset_id_is_refused(store, png):
    with pytest.raises(MediaStorageError):
        store.put_original(png, asset_id="")
