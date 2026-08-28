"""Приём в медиатеку и граница «сгенерированное сначала становится нашим».

Требование из `12_CONTENT_STUDIO` буквальное: «No ephemeral model URL is the
only copy of a scheduled asset». Здесь доказывается, что обойти его нельзя —
не по договорённости, а потому что типы и проверки не оставляют дороги.
"""
from __future__ import annotations

import pytest

from social_farm.media.asset import (AssetSource, AssetType, GeneratedMedia,
                                     MediaAsset, MediaError, checksum_of, redact)
from social_farm.media.ingest import (ingest_bytes, ingest_derived, ingest_file,
                                      ingest_generated)
from social_farm.media.store import NAMESPACE_DERIVED, NAMESPACE_ORIGINAL

from conftest import make_png


# --------------------------------------------------------------- обычный приём

def test_ingest_measures_what_it_stored(store, png):
    """Измеряется файл в хранилище, а не переданный буфер."""
    asset = ingest_bytes(store, png)
    assert asset.type is AssetType.IMAGE
    assert (asset.width, asset.height) == (1080, 1350)
    assert asset.checksum_sha256 == checksum_of(png)
    assert asset.bytes == len(png)
    assert asset.probed is True
    store.verify(asset)


def test_ingest_file_leaves_the_users_file_alone(store, tmp_path, png):
    source = tmp_path / "photo.png"
    source.write_bytes(png)
    before = source.read_bytes()
    ingest_file(store, source)
    assert source.read_bytes() == before


def test_empty_content_is_refused(store):
    with pytest.raises(MediaError, match="пуст"):
        ingest_bytes(store, b"")


def test_identical_uploads_share_one_asset(store, png):
    """Дедупликация исходников по сумме (`13_MEDIA_LIBRARY`)."""
    first, second = ingest_bytes(store, png), ingest_bytes(store, png)
    assert first.id == second.id
    assert first.storage_ref == second.storage_ref


def test_different_content_never_shares_an_asset(store, png):
    assert ingest_bytes(store, png).id != ingest_bytes(store, make_png(64, 64)).id


# --------------------------------------------------------------- главный инвариант

def test_generated_media_becomes_a_durable_asset_before_anything_else(store):
    """Результат генерации проходит через хранилище — и только через него."""
    generated = GeneratedMedia(provider="some-image-model", mime="image/png",
                               data=make_png(1080, 1350),
                               provenance={"prompt": "закат над полем"})
    assert generated.durable is False

    asset = ingest_generated(store, generated, project_id="prj_1")
    assert isinstance(asset, MediaAsset)
    assert asset.source is AssetSource.GENERATED
    assert asset.generation_provider == "some-image-model"
    assert asset.storage_ref.startswith(NAMESPACE_ORIGINAL + "/")
    # Содержимое действительно у нас: сумма сходится с тем, что лежит на диске.
    store.verify(asset)
    assert store.read(asset.storage_ref) == generated.data


def test_a_model_url_alone_cannot_become_an_asset(store):
    """Ссылка чужого сервиса не может быть единственной копией.

    Ровно тот случай, ради которого написано требование: публикация назначена
    на завтра, а ссылка живёт час.
    """
    generated = GeneratedMedia(provider="some-video-model", mime="video/mp4",
                               ephemeral_url="https://model.example/tmp/abc123")
    with pytest.raises(MediaError, match="единственной копией|скачать"):
        ingest_generated(store, generated)


def test_a_fetched_url_is_stored_and_the_link_becomes_mere_provenance(store):
    """Скачали — значит копия наша, а ссылка остаётся только следом."""
    payload = make_png(1080, 1350)
    generated = GeneratedMedia(provider="m", mime="image/png",
                               ephemeral_url="https://model.example/tmp/abc")
    asset = ingest_generated(store, generated, fetch=lambda url: payload)
    assert store.read(asset.storage_ref) == payload
    assert asset.provenance["source_url"] == "https://model.example/tmp/abc"


def test_a_fetch_that_returns_nothing_is_refused(store):
    generated = GeneratedMedia(provider="m", mime="image/png",
                               ephemeral_url="https://model.example/gone")
    with pytest.raises(MediaError, match="ничего не скачалось"):
        ingest_generated(store, generated, fetch=lambda url: b"")


def test_generated_media_cannot_be_constructed_without_content_or_link():
    with pytest.raises(MediaError, match="пуст"):
        GeneratedMedia(provider="m", mime="image/png")


def test_generated_media_is_not_a_media_asset():
    """Типовая граница: конвейер принимает `MediaAsset`, а это не он."""
    generated = GeneratedMedia(provider="m", mime="image/png", data=b"x")
    assert not isinstance(generated, MediaAsset)
    assert not hasattr(generated, "storage_ref")


# --------------------------------------------------------------- производные

def test_derived_asset_points_at_its_parent_and_leaves_it_alone(store, png):
    parent = ingest_bytes(store, png)
    child = ingest_derived(store, make_png(1080, 1080), parent=parent)

    assert child.parent_asset_id == parent.id
    assert child.id != parent.id
    assert child.checksum_sha256 != parent.checksum_sha256
    assert child.storage_ref.startswith(NAMESPACE_DERIVED + "/")
    assert child.version == parent.version + 1
    assert child.provenance["parent_checksum_sha256"] == parent.checksum_sha256
    # Родитель на месте и не изменился.
    store.verify(parent)
    assert store.read(parent.storage_ref) == png


def test_derived_assets_are_not_deduplicated_with_originals(store, png):
    """У производных разные родители и профили — сливать их нельзя."""
    parent = ingest_bytes(store, png)
    child = ingest_derived(store, png, parent=parent)
    assert child.id != parent.id


# --------------------------------------------------------------- секреты

def test_secrets_never_reach_asset_metadata(store):
    """Происхождение генерации хранить полезно, ключ доступа — нет.

    Редакция стоит на входе: в ассет не попадает то, чего в нём быть не должно,
    а не «попадает, но мы это не логируем».
    """
    generated = GeneratedMedia(
        provider="m", mime="image/png", data=make_png(200, 200),
        provenance={"prompt": "кот", "api_key": "sk-СЕКРЕТ",
                    "auth": {"bearer_token": "СЕКРЕТ2"}, "model": "v3"})
    asset = ingest_generated(store, generated)

    flat = repr(asset.provenance)
    assert "sk-СЕКРЕТ" not in flat and "СЕКРЕТ2" not in flat
    assert asset.provenance["prompt"] == "кот", "полезное происхождение потеряно"
    assert asset.provenance["model"] == "v3"


@pytest.mark.parametrize("key", ["token", "ACCESS_TOKEN", "api_key", "password",
                                 "client_secret", "Authorization", "session_id",
                                 "cookie", "private_key"])
def test_secret_shaped_keys_are_redacted(key):
    assert redact({key: "значение"})[key] == "[REDACTED]"


def test_redaction_does_not_partially_expose(store):
    """Усечённый секрет — всё ещё секрет. Значение заменяется целиком."""
    assert redact({"token": "abcdef123456"})["token"] == "[REDACTED]"


def test_an_asset_without_a_checksum_cannot_exist():
    with pytest.raises(MediaError, match="контрольной суммы"):
        MediaAsset(id="a", type=AssetType.IMAGE, mime="image/png",
                   checksum_sha256="", storage_ref="media/original/a/x")


def test_an_asset_without_storage_cannot_exist():
    """Ассет без хранилища — это не ассет, а обещание."""
    with pytest.raises(MediaError, match="ссылки на хранилище|нет"):
        MediaAsset(id="a", type=AssetType.IMAGE, mime="image/png",
                   checksum_sha256="abc", storage_ref="")
