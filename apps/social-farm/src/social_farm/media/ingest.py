"""Приём медиа в библиотеку. Единственная дверь, через которую входит контент.

`12_CONTENT_STUDIO` требует: «Generation result is stored before publishing. No
ephemeral model URL is the only copy of a scheduled asset». Требование не
сводится к аккуратности — оно про то, что публикация назначена на завтра, а
ссылка поставщика модели живёт час.

Дверь одна, и она здесь. `MediaAsset` создаётся ТОЛЬКО этими функциями, и
каждая из них сначала кладёт байты в хранилище, потом измеряет их настоящим
прибором и лишь затем собирает ассет. Порядок именно такой: измеряется то, что
уже лежит у нас, а не то, что нам показали.

Отсюда же следует, почему `ingest_generated` не принимает одну лишь ссылку.
Ассет, у которого нет содержимого, — это обещание чужого сервиса, а не наш
файл. Если скачать нечем, приём отклоняется, и работа не уходит в расписание с
пустой рукой.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..domain.identity import new_id, utc_now
from .asset import (AssetSource, AssetType, GeneratedMedia, MediaAsset, MediaError,
                    checksum_of)
from .probe import CorruptMedia, ProbeResult, ProbeUnavailable, probe
from .store import MediaStore, NAMESPACE_DERIVED, NAMESPACE_ORIGINAL

#: Чем скачивать ссылку поставщика. По умолчанию — ничем: приложение не ходит в
#: сеть само по себе, загрузчик передаётся явно тем, кто за неё отвечает.
Fetcher = Callable[[str], bytes]


def _asset_from_probe(*, asset_id: str, storage_ref: str, checksum: str,
                      result: ProbeResult, source: AssetSource,
                      project_id: str | None, parent_asset_id: str | None,
                      generation_provider: str | None,
                      provenance: dict[str, Any] | None,
                      render_profile_ref: str | None, version: int) -> MediaAsset:
    return MediaAsset(
        id=asset_id, type=result.type, mime=result.mime, checksum_sha256=checksum,
        storage_ref=storage_ref, bytes=result.bytes, content_project_id=project_id,
        width=result.width, height=result.height, duration_ms=result.duration_ms,
        codec=result.codec, audio_codec=result.audio_codec, container=result.container,
        bitrate_bps=result.bitrate_bps, source=source,
        generation_provider=generation_provider, parent_asset_id=parent_asset_id,
        render_profile_ref=render_profile_ref, version=version,
        created_at=utc_now(), prober=result.prober, provenance=provenance or {})


def ingest_bytes(store: MediaStore, data: bytes, *,
                 source: AssetSource = AssetSource.UPLOAD,
                 project_id: str | None = None,
                 parent_asset_id: str | None = None,
                 generation_provider: str | None = None,
                 provenance: dict[str, Any] | None = None,
                 render_profile_ref: str | None = None,
                 namespace: str = NAMESPACE_ORIGINAL,
                 version: int = 1,
                 deduplicate: bool = True) -> MediaAsset:
    """Принять содержимое: записать, измерить, собрать ассет.

    Измерение обязательно. Ассет без измерений не проходит валидацию, а значит
    не публикуется, — так что «принять, а разберёмся потом» здесь невозможно:
    непригодный файл отклоняется на входе, а не в момент публикации.
    """
    if not data:
        raise MediaError("пустое содержимое в медиатеку не принимается")
    checksum = checksum_of(data)
    asset_id = new_id("ast")

    if deduplicate and namespace == NAMESPACE_ORIGINAL:
        # «Deduplicate by checksum where safe» (13_MEDIA_LIBRARY). Безопасно —
        # это про исходники: одинаковые байты и есть один и тот же файл.
        # Производные не дедуплицируются: у них разные родители и профили.
        existing = store.find_by_checksum(checksum)
        if existing is not None:
            asset_id = existing
        else:
            asset_id = store.register_checksum(checksum, asset_id)

    storage_ref = store.put(data, asset_id=asset_id, namespace=namespace)
    # Измеряется файл В ХРАНИЛИЩЕ, а не переданный буфер: если запись что-то
    # испортила, узнать об этом надо здесь.
    path = store.path_of(storage_ref)
    result = probe(path)
    return _asset_from_probe(
        asset_id=asset_id, storage_ref=storage_ref, checksum=checksum, result=result,
        source=source, project_id=project_id, parent_asset_id=parent_asset_id,
        generation_provider=generation_provider, provenance=provenance,
        render_profile_ref=render_profile_ref, version=version)


def ingest_file(store: MediaStore, path: str | Path, **kwargs: Any) -> MediaAsset:
    """Принять локальный файл. Оригинал на диске пользователя не трогается."""
    return ingest_bytes(store, Path(path).read_bytes(), **kwargs)


def ingest_generated(store: MediaStore, generated: GeneratedMedia, *,
                     project_id: str | None = None,
                     fetch: Fetcher | None = None,
                     deduplicate: bool = True) -> MediaAsset:
    """Превратить результат генерации в долговечный ассет. Другого пути нет.

    Это единственная функция, принимающая `GeneratedMedia`, и она обязательно
    проходит через хранилище. Обойти её нельзя не по договорённости, а потому
    что конвейер работает с `MediaAsset`, а `MediaAsset` без `storage_ref` не
    существует (`asset.py`) и без содержимого в хранилище не проходит сверку
    (`store.verify`).
    """
    data = generated.data
    if data is None:
        if fetch is None:
            raise MediaError(
                f"поставщик {generated.provider} вернул только ссылку "
                f"({generated.ephemeral_url}), а скачать её нечем. Ссылка чужого "
                f"сервиса не может быть единственной копией запланированного "
                f"ассета — приём отклонён")
        data = fetch(str(generated.ephemeral_url))
        if not data:
            raise MediaError(
                f"по ссылке поставщика {generated.provider} ничего не скачалось")
    provenance = dict(generated.provenance)
    provenance.setdefault("generation_provider", generated.provider)
    if generated.ephemeral_url:
        # Ссылку сохраняем как след происхождения, но она уже НЕ единственная
        # копия: содержимое лежит у нас.
        provenance.setdefault("source_url", generated.ephemeral_url)
    return ingest_bytes(store, data, source=AssetSource.GENERATED,
                        project_id=project_id,
                        generation_provider=generated.provider,
                        provenance=provenance, deduplicate=deduplicate)


def ingest_derived(store: MediaStore, data: bytes, *, parent: MediaAsset,
                   render_profile_ref: str | None = None,
                   provenance: dict[str, Any] | None = None) -> MediaAsset:
    """Записать производный ассет со ссылкой на родителя.

    Родитель не изменяется и не удаляется: `13_MEDIA_LIBRARY` требует
    «Immutable originals. Transform creates child asset». Версия наследуется
    с приращением, чтобы цепочка преобразований читалась.
    """
    trail = dict(provenance or {})
    trail.setdefault("parent_checksum_sha256", parent.checksum_sha256)
    return ingest_bytes(store, data, source=AssetSource.DERIVED,
                        project_id=parent.content_project_id,
                        parent_asset_id=parent.id,
                        generation_provider=parent.generation_provider,
                        provenance=trail, render_profile_ref=render_profile_ref,
                        namespace=NAMESPACE_DERIVED, version=parent.version + 1,
                        deduplicate=False)


__all__ = ["AssetSource", "AssetType", "CorruptMedia", "Fetcher", "ProbeUnavailable",
           "ingest_bytes", "ingest_derived", "ingest_file", "ingest_generated"]
