"""V2.6 — Artifact Engine (модуль M): first-class артефакты-результаты.

Реестр deliverable-метаданных над таблицей artifact_registry: каждый созданный
файл-результат получает artifact_id (sha-префикс + версия), явную историю версий
по path и статус верификации. Это НЕ второй security-гейт — импорт файлов внутрь
по-прежнему идёт только через sandbox/artifacts.py; здесь только учёт того,
что агент ПРОИЗВЁЛ, с provenance (source_evidence) и content_hash.

Генерация контента — stdlib-first и честная: md/txt/json/csv/zip собираются
без внешних зависимостей; xlsx/docx/pdf/pptx требуют внешних библиотек и
НЕ подделываются (ValueError вместо мусорного файла с нужным расширением).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime

from . import db

# Форматы, которые собираем сами (stdlib, без деградации качества).
SUPPORTED_CREATE_FORMATS = {"md", "txt", "json", "csv", "zip"}

# Известные форматы, для которых честно отказываем: нужна внешняя библиотека.
_NEEDS_LIBRARY = {"xlsx": "openpyxl", "docx": "python-docx",
                  "pdf": "reportlab/fpdf2", "pptx": "python-pptx"}

_TABLE = "artifact_registry"   # `artifacts` занята медиа-конвейером (см. schema.sql)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str            # "<sha256[:12]>-v<version>"
    type: str                   # формат/тип результата: md|txt|json|csv|zip|...
    path: str                   # куда записан файл (инструменты возвращают ссылки)
    creator_task: str | None    # какая задача/ран породила артефакт
    source_evidence: list       # provenance-ссылки на исходные данные
    content_hash: str           # sha256 контента целиком
    version: int                # явная история: та же path → следующая версия
    verification: str           # unverified | verified | ... (mark_verified)
    created_at: datetime | None = None


def _record(row: dict) -> ArtifactRecord:
    ev = row.get("source_evidence")
    return ArtifactRecord(
        artifact_id=row["artifact_id"], type=row["type"], path=row["path"],
        creator_task=row.get("creator_task"),
        source_evidence=list(ev) if isinstance(ev, (list, tuple)) else [],
        content_hash=row["content_hash"], version=row["version"],
        verification=row["verification"], created_at=row.get("created_at"))


async def register_artifact(*, type: str, path: str, content: bytes | str,
                            creator_task: str | None = None,
                            source_evidence: list | None = None) -> ArtifactRecord:
    """Зарегистрировать артефакт: sha256 контента + следующая версия по path.

    Повторная регистрация того же контента по тому же path СОЗНАТЕЛЬНО даёт
    новую версию: версии — это явная история публикаций, а не дедупликация.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    digest = hashlib.sha256(data).hexdigest()
    prev = await db.fetchval(
        f"SELECT COALESCE(MAX(version), 0) FROM {_TABLE} WHERE path = $1", path)
    version = int(prev or 0) + 1
    # id уникален по (path, content, version): одинаковый контент по РАЗНЫМ путям —
    # это разные артефакты, и оба получают version=1. Без отпечатка пути их id
    # совпали бы и вторая регистрация падала бы на UNIQUE (проявилось на живом PG).
    path_tag = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    artifact_id = f"{digest[:12]}-{path_tag}-v{version}"
    row = await db.fetchrow(
        f"INSERT INTO {_TABLE} (artifact_id, type, path, creator_task, "
        f"source_evidence, content_hash, version) "
        f"VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
        artifact_id, type, path, creator_task,
        list(source_evidence or []), digest, version)
    return _record(row)


async def get_artifact(artifact_id: str) -> ArtifactRecord | None:
    row = await db.fetchrow(
        f"SELECT * FROM {_TABLE} WHERE artifact_id = $1", artifact_id)
    return _record(row) if row else None


async def list_versions(path: str) -> list[ArtifactRecord]:
    """Вся история версий по path, по возрастанию версии."""
    rows = await db.fetch(
        f"SELECT * FROM {_TABLE} WHERE path = $1 ORDER BY version", path)
    return [_record(r) for r in rows]


async def mark_verified(artifact_id: str, verification: str) -> bool:
    """Отметить статус проверки; False — артефакта нет."""
    status = await db.execute(
        f"UPDATE {_TABLE} SET verification = $2 WHERE artifact_id = $1",
        artifact_id, verification)
    return status.endswith("1")


# ---------- сборка контента ----------

def _build_zip(payload: dict) -> bytes:
    """zip из {имя: строковый контент}; traversal/абсолютные имена — отказ
    (та же дисциплина, что _safe_zip_names в file_intel, только на записи)."""
    if not isinstance(payload, dict):
        raise ValueError("zip ожидает dict {имя_файла: строковый контент}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in payload.items():
            norm = str(name).replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/") or (
                    len(norm) > 1 and norm[1] == ":"):
                raise ValueError(f"небезопасное имя в zip: {name!r}")
            zf.writestr(norm, body if isinstance(body, (bytes, str)) else str(body))
    return buf.getvalue()


def _build_csv(payload) -> bytes:
    if not isinstance(payload, (list, tuple)):
        raise ValueError("csv ожидает список строк (каждая — список ячеек)")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    for row in payload:
        w.writerow(row if isinstance(row, (list, tuple)) else [row])
    return out.getvalue().encode("utf-8")


def build_content(fmt: str, payload) -> bytes:
    """Собрать байты артефакта в поддерживаемом формате.

    md/txt — str payload как есть; json — json.dumps(ensure_ascii=False);
    csv — список строк; zip — dict {имя: контент}. Для форматов, требующих
    внешних библиотек, — честный ValueError, а не подделка."""
    fmt = (fmt or "").lower().lstrip(".")
    if fmt in _NEEDS_LIBRARY:
        raise ValueError(
            f"формат {fmt} требует внешней библиотеки — не реализовано "
            f"(нужна {_NEEDS_LIBRARY[fmt]})")
    if fmt not in SUPPORTED_CREATE_FORMATS:
        raise ValueError(
            f"неизвестный формат {fmt!r}; поддерживаются: "
            + ", ".join(sorted(SUPPORTED_CREATE_FORMATS)))
    if fmt in ("md", "txt"):
        return str(payload).encode("utf-8")
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    if fmt == "csv":
        return _build_csv(payload)
    return _build_zip(payload)
