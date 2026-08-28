"""Хранилище, адресуемое содержимым. Раскладка — из `64_STORAGE_LAYOUT`.

```
media/original/{asset_id}/{checksum}
media/derived/{asset_id}/{checksum}
media/previews/{revision_id}/...
media/by-checksum/{checksum}          — наш индекс дедупликации
```

Контрольная сумма стоит В ПУТИ, а не рядом с ним. Это не украшение: путь,
который зависит от содержимого, невозможно перезаписать другим содержимым, не
изменив путь. Неизменяемость получается из раскладки, а не из дисциплины
вызывающего.

Проверка при чтении обязательна и не отключается флагом. Хранилище — это диск,
а диск портится, и файлы на нём правят руками. Ассет, чья сумма разошлась с
записанной, не отдаётся вызывающему вообще: отдать его «с предупреждением»
значит опубликовать не то, что одобрено.

Отдельно — запрет из того же документа: «Never store plaintext password files,
token JSON exports, raw cookie jars in ordinary media/object namespace».
Проверяется на входе, а не ревью.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from .asset import MediaAsset, checksum_of

NAMESPACE_ORIGINAL = "media/original"
NAMESPACE_DERIVED = "media/derived"
NAMESPACE_PREVIEW = "media/previews"
NAMESPACE_INDEX = "media/by-checksum"

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# Признаки материала, которому в медиапространстве не место. Список намеренно
# узкий и ищет структуру, а не слово: файл с подписью «cookie» в тексте статьи
# — это статья, а экспорт cookie-jar имеет опознаваемую форму.
_CREDENTIAL_MARKERS = (
    (re.compile(rb'"(access_token|refresh_token|id_token|client_secret)"\s*:'),
     "экспорт токенов"),
    (re.compile(rb'"(httpOnly|httponly)"\s*:\s*(true|false)'), "выгрузка cookie"),
    (re.compile(rb"^#\s*Netscape HTTP Cookie File", re.MULTILINE), "cookie-jar"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
)


class MediaStorageError(RuntimeError):
    """Отказ хранилища. На границе конвейера отображается на `STORAGE_ERROR`."""


class ChecksumMismatch(MediaStorageError):
    """Содержимое разошлось с контрольной суммой. Файл подменён или испорчен."""


def looks_like_credential_material(data: bytes) -> str | None:
    """Вернуть причину, если это учётные данные, а не медиа."""
    head = data[:65536]
    for pattern, reason in _CREDENTIAL_MARKERS:
        if pattern.search(head):
            return reason
    return None


class MediaStore:
    """Файловое хранилище с раскладкой из `64_STORAGE_LAYOUT`.

    Объектное хранилище подставляется на то же место: наружу торчат только
    логические ключи вида `media/original/{id}/{checksum}`, а то, что сейчас
    под ними лежит локальный каталог, — деталь этого класса.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- пути

    def _resolve(self, storage_ref: str) -> Path:
        """Логический ключ → путь на диске, с защитой от выхода за корень."""
        parts = [p for p in str(storage_ref).split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or not _SAFE_SEGMENT.match(p) for p in parts):
            raise MediaStorageError(f"недопустимая ссылка на хранилище: {storage_ref!r}")
        path = (self.root / Path(*parts)).resolve()
        if not path.is_relative_to(self.root):
            raise MediaStorageError(f"ссылка выводит за пределы хранилища: {storage_ref!r}")
        return path

    @staticmethod
    def ref_for(namespace: str, asset_id: str, checksum: str) -> str:
        return f"{namespace}/{asset_id}/{checksum}"

    def preview_ref(self, revision_id: str, name: str) -> str:
        return f"{NAMESPACE_PREVIEW}/{revision_id}/{name}"

    # ------------------------------------------------------------- запись

    def put(self, data: bytes, *, asset_id: str, namespace: str = NAMESPACE_ORIGINAL,
            allow_documents: bool = False) -> str:
        """Положить содержимое и вернуть логический ключ.

        Повторная запись того же содержимого по тому же ключу — не ошибка:
        путь зависит от суммы, значит там уже лежит ровно это. Запись ДРУГОГО
        содержимого по существующему ключу невозможна по построению.
        """
        if not asset_id:
            raise MediaStorageError("запись без идентификатора ассета")
        if not allow_documents:
            reason = looks_like_credential_material(data)
            if reason is not None:
                raise MediaStorageError(
                    f"отказ записи в медиапространство: похоже на {reason}. "
                    f"64_STORAGE_LAYOUT запрещает хранить учётные данные здесь — "
                    f"для них есть хранилище секретов")
        checksum = checksum_of(data)
        ref = self.ref_for(namespace, asset_id, checksum)
        path = self._resolve(ref)
        if path.exists():
            # Сумма в пути равна сумме содержимого — значит это то же самое.
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        # Запись через временный файл и атомарное переименование: оборванная
        # запись не должна оставить полуфайл под именем, обещающим сумму.
        tmp = path.parent / f".{path.name}.partial"
        tmp.write_bytes(data)
        written = checksum_of(tmp.read_bytes())
        if written != checksum:
            tmp.unlink(missing_ok=True)
            raise ChecksumMismatch(
                f"содержимое изменилось в процессе записи ассета {asset_id}")
        os.replace(tmp, path)
        # Только чтение: неизменяемость видна и на уровне файловой системы.
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return ref

    def put_original(self, data: bytes, *, asset_id: str, **kw) -> str:
        return self.put(data, asset_id=asset_id, namespace=NAMESPACE_ORIGINAL, **kw)

    def put_derived(self, data: bytes, *, asset_id: str, **kw) -> str:
        return self.put(data, asset_id=asset_id, namespace=NAMESPACE_DERIVED, **kw)

    # ------------------------------------------------------------- чтение

    def exists(self, storage_ref: str) -> bool:
        return self._resolve(storage_ref).is_file()

    def path_of(self, storage_ref: str) -> Path:
        """Путь к файлу для внешних программ (ffprobe/ffmpeg).

        Сумма проверяется здесь же: отдать путь к подменённому файлу —
        то же самое, что отдать подменённое содержимое.
        """
        path = self._resolve(storage_ref)
        if not path.is_file():
            raise MediaStorageError(f"в хранилище нет объекта {storage_ref}")
        expected = str(storage_ref).rsplit("/", 1)[-1]
        actual = checksum_of(path.read_bytes())
        if actual != expected:
            raise ChecksumMismatch(
                f"контрольная сумма {storage_ref} не сходится: ожидалась {expected}, "
                f"получена {actual}. Файл подменён или испорчен — он не будет "
                f"использован")
        return path

    def read(self, storage_ref: str, *, expected_checksum: str | None = None) -> bytes:
        """Прочитать содержимое с обязательной проверкой суммы."""
        path = self._resolve(storage_ref)
        if not path.is_file():
            raise MediaStorageError(f"в хранилище нет объекта {storage_ref}")
        data = path.read_bytes()
        actual = checksum_of(data)
        expected = expected_checksum or str(storage_ref).rsplit("/", 1)[-1]
        if actual != expected:
            raise ChecksumMismatch(
                f"контрольная сумма {storage_ref} не сходится: ожидалась {expected}, "
                f"получена {actual}. Файл подменён или испорчен — он не будет "
                f"использован")
        return data

    def verify(self, asset: MediaAsset) -> None:
        """Ассет действительно лежит у нас и совпадает со своей суммой.

        Вызывается конвейером для КАЖДОГО ассета перед рендером и перед
        созданием ревизии. Это та проверка, из-за которой ассет, придуманный в
        обход хранилища, не проходит дальше: у него нет содержимого, которое
        могло бы сойтись.
        """
        if not asset.storage_ref:
            raise MediaStorageError(f"ассет {asset.id} не имеет ссылки на хранилище")
        self.read(asset.storage_ref, expected_checksum=asset.checksum_sha256)

    # ------------------------------------------------------------- дедупликация

    def register_checksum(self, checksum: str, asset_id: str) -> str:
        """Занять сумму за ассетом. Вернуть того, кто занял её первым.

        «Deduplicate by checksum where safe» (`13_MEDIA_LIBRARY`). Безопасно —
        это когда одинаковое содержимое действительно одно и то же: тогда
        второй загрузчик получает идентификатор первого, а не копию файла.
        Запись через `O_EXCL`, поэтому гонка двух загрузок разрешается ядром,
        а не удачей.
        """
        path = self._resolve(f"{NAMESPACE_INDEX}/{checksum}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
        except FileExistsError:
            return json.loads(path.read_text(encoding="utf-8"))["asset_id"]
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"asset_id": asset_id, "checksum": checksum}, stream)
        return asset_id

    def find_by_checksum(self, checksum: str) -> str | None:
        path = self._resolve(f"{NAMESPACE_INDEX}/{checksum}")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["asset_id"]


__all__ = ["ChecksumMismatch", "MediaStorageError", "MediaStore", "NAMESPACE_DERIVED",
           "NAMESPACE_INDEX", "NAMESPACE_ORIGINAL", "NAMESPACE_PREVIEW",
           "looks_like_credential_material"]
