"""Ассет медиатеки и то, что ассетом ещё НЕ является.

В этом файле проведена граница, ради которой существует весь поток: между
результатом генерации и долговечным ассетом. `12_CONTENT_STUDIO` требует
буквально: «Generation result is stored before publishing. No ephemeral model
URL is the only copy of a scheduled asset».

Требование выполняется не соглашением, а типами. `GeneratedMedia` — то, что
вернул поставщик модели: байты либо ссылка, живущая ровно столько, сколько
захочет чужой сервис. У него нет `storage_ref`, и он никогда его не получит.
`MediaAsset` — то, что уже лежит в нашем хранилище под своей контрольной
суммой. Конвейер принимает только второе, и превратить первое во второе можно
единственным способом — записав содержимое к себе (`media/ingest.py`).

Отсюда же неизменяемость. Ассет — `frozen`, а его контрольная сумма считается
от содержимого и проверяется при каждом чтении. Правка файла невозможна: любое
преобразование рождает НОВЫЙ ассет со ссылкой на родителя
(`13_MEDIA_LIBRARY`: «Immutable originals. Transform creates child asset»).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssetType(str, Enum):
    """Перечень из `media_asset.schema.json`, закрытый."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    DOCUMENT = "DOCUMENT"


class AssetSource(str, Enum):
    """Откуда ассет взялся. Важно для происхождения, а не для валидации."""

    UPLOAD = "UPLOAD"
    GENERATED = "GENERATED"
    DERIVED = "DERIVED"
    IMPORTED = "IMPORTED"


class MediaError(ValueError):
    """Нарушение правил медиатеки: состав ассета, происхождение, неизменяемость."""


# Ключи, значения которых не попадают ни в метаданные ассета, ни в логи.
# Происхождение генерации («какой моделью и по какому запросу») хранить
# полезно и спека это разрешает, но вместе с запросом поставщику обычно
# передают и ключ доступа. Редакция здесь, в одном месте, на входе.
_SECRET_KEY = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|apikey|authorization|"
    r"auth|credential|cookie|session|bearer|private[_-]?key|signature)")


def redact(payload: Any) -> Any:
    """Убрать секретоподобные значения из произвольной структуры.

    Значение не маскируется частично и не укорачивается: усечённый секрет —
    это всё ещё секрет в логе. Ключ остаётся, чтобы было видно, что поле было,
    а значение заменяется меткой.
    """
    if isinstance(payload, dict):
        return {key: ("[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(value))
                for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(item) for item in payload]
    return payload


def checksum_of(data: bytes) -> str:
    """Контрольная сумма ассета — sha256 от содержимого, и только от него.

    Не от имени, не от размера, не от времени. Именно поэтому подменённый файл
    ловится: сумма перестаёт сходиться с записанной в ассете и в хеше ревизии.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class GeneratedMedia:
    """Сырой результат поставщика модели. Публиковать это нельзя.

    Намеренно НЕ имеет `storage_ref` и не приводится к `MediaAsset` иначе как
    через запись в хранилище. Тип — это и есть барьер: функция, принимающая
    `MediaAsset`, физически не примет `GeneratedMedia`.
    """

    provider: str
    mime: str
    data: bytes | None = None
    ephemeral_url: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider:
            raise MediaError("результат генерации без указания поставщика не принимается")
        if self.data is None and not self.ephemeral_url:
            raise MediaError(
                "результат генерации пуст: нет ни содержимого, ни ссылки на него")
        # Редакция происходит здесь, до того как объект куда-либо попадёт.
        object.__setattr__(self, "provenance", redact(dict(self.provenance)))

    @property
    def durable(self) -> bool:
        """Всегда `False`. Свойство существует, чтобы это было видно в коде."""
        return False


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """Ассет медиатеки: содержимое, лежащее у нас, и всё, что о нём известно.

    Набор полей шире, чем `media_asset.schema.json`: схема — контракт API, а
    внутри нужны кодек, контейнер, происхождение и отметка о том, каким
    прибором ассет измерен. Тот же приём, что и в решении C3 — таблица
    авторитетна для хранилища, схема для API.
    """

    id: str
    type: AssetType
    mime: str
    checksum_sha256: str
    storage_ref: str
    bytes: int = 0
    content_project_id: str | None = None
    width: int | None = None
    height: int | None = None
    # C12: внутри — миллисекунды целым числом, наружу — секунды. Конверсия
    # живёт в одном месте, в `to_schema_dict`.
    duration_ms: int | None = None
    codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    bitrate_bps: int | None = None
    source: AssetSource = AssetSource.UPLOAD
    generation_provider: str | None = None
    parent_asset_id: str | None = None
    render_profile_ref: str | None = None
    version: int = 1
    created_at: str = ""
    # Каким прибором получены измерения. Пустая строка означает, что ассет не
    # измерен ничем — такой ассет не валидируется и не публикуется.
    prober: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise MediaError("ассет без идентификатора")
        if not self.checksum_sha256:
            raise MediaError(
                f"ассет {self.id} без контрольной суммы: проверить его нечем")
        if not self.storage_ref:
            raise MediaError(
                f"ассет {self.id} без ссылки на хранилище — значит его у нас нет")
        if self.parent_asset_id == self.id:
            raise MediaError(f"ассет {self.id} объявлен родителем самому себе")
        object.__setattr__(self, "provenance", redact(dict(self.provenance)))

    @property
    def probed(self) -> bool:
        return bool(self.prober)

    @property
    def duration_seconds(self) -> float | None:
        return None if self.duration_ms is None else self.duration_ms / 1000.0

    @property
    def is_derived(self) -> bool:
        return self.parent_asset_id is not None

    def to_schema_dict(self) -> dict[str, Any]:
        """Проекция на `media_asset.schema.json` (`additionalProperties: false`).

        Наружу уходят только объявленные схемой поля. Внутренние — кодек,
        происхождение, прибор — остаются внутри.
        """
        return {
            "id": self.id,
            "type": self.type.value,
            "mime": self.mime,
            "checksum": self.checksum_sha256,
            "bytes": int(self.bytes),
            "width": self.width,
            "height": self.height,
            "duration_seconds": self.duration_seconds,
            "source": self.source.value,
            "parent_asset_id": self.parent_asset_id,
            "storage_ref": self.storage_ref,
        }

    def hash_entry(self) -> dict[str, str]:
        """То, чем ассет входит в хеш ревизии: идентификатор И сумма.

        Без суммы подмена файла под тем же идентификатором прошла бы под
        старым одобрением (`domain/content.py`).
        """
        return {"id": self.id, "checksum_sha256": self.checksum_sha256}


__all__ = ["AssetSource", "AssetType", "GeneratedMedia", "MediaAsset", "MediaError",
           "checksum_of", "redact"]
