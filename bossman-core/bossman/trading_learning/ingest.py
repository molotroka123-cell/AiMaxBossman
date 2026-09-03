"""Приём материала: неизменяемый хеш источника и метаданные.

Почему хеш обязателен: без него «то же самое видео» через неделю может быть
перемонтировано, а все claim'ы останутся привязанными к имени файла. Хеш
превращает источник в неизменяемый предмет, на который можно ссылаться из
эпизода, памяти и отчёта.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .safety import (EvidenceClass, OwnerApproval, require_owner_approval, utcnow)

_CHUNK = 1 << 20
_ALLOWED_SCHEMES = ("https",)          # http и file по URL не принимаем


class IngestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Паспорт источника. Всё, на что дальше ссылаются claim'ы."""

    source_id: str
    kind: str                 # "local_file" | "url"
    locator: str              # путь или URL (без секретов в query)
    video_hash: str           # sha256 содержимого; для URL — пусто до скачивания
    size_bytes: int
    mtime: str
    ingested_at: str
    evidence_class: str
    approved_by: str
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def ingest_local(path: str | os.PathLike[str], *, approval: OwnerApproval | None,
                 notes: str = "") -> SourceRecord:
    """Локальный файл, одобренный владельцем. Без одобрения — отказ."""
    p = Path(path).expanduser().resolve()
    require_owner_approval(approval, subject=str(p), stage="historical_analysis")
    if not p.is_file():
        raise IngestError(f"not a file: {p}")
    video_hash, size = _hash_file(p)
    return SourceRecord(
        source_id=f"src_{video_hash[:16]}", kind="local_file", locator=str(p),
        video_hash=video_hash, size_bytes=size,
        mtime=datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(),
        ingested_at=utcnow().isoformat(),
        evidence_class=EvidenceClass.REAL_SANDBOX.value,
        approved_by=approval.granted_by if approval else "", notes=notes)


def ingest_url(url: str, *, approval: OwnerApproval | None, notes: str = "") -> SourceRecord:
    """URL регистрируется, но НЕ скачивается: загрузчика в окружении нет.

    Возвращается запись класса BLOCKED — честный статус вместо подделки. Такой
    источник не даёт ни кадров, ни транскрипта, и это видно из класса.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise IngestError(f"unsupported source URL scheme: {url!r}")
    require_owner_approval(approval, subject=url, stage="historical_analysis")
    # Идентификатор от URL без query: токены доступа в идентификатор не попадают.
    canonical = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    url_hash = hashlib.sha256(canonical.encode()).hexdigest()
    return SourceRecord(
        source_id=f"src_url_{url_hash[:16]}", kind="url", locator=canonical,
        video_hash="", size_bytes=0, mtime="", ingested_at=utcnow().isoformat(),
        evidence_class=EvidenceClass.BLOCKED.value,
        approved_by=approval.granted_by if approval else "",
        notes=notes or "no downloader available in this environment; nothing was fetched")


def ingest_video(locator: str, *, approval: OwnerApproval | None,
                 notes: str = "") -> SourceRecord:
    """Единая точка входа пайплайна: локальный файл или URL."""
    if locator.startswith(("http://", "https://")):
        return ingest_url(locator, approval=approval, notes=notes)
    return ingest_local(locator, approval=approval, notes=notes)


def write_manifest(record: SourceRecord, out_dir: str | os.PathLike[str]) -> Path:
    """Манифест источника рядом с артефактами — чтобы отчёт был воспроизводим."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{record.source_id}.json"
    target.write_text(json.dumps(record.as_dict(), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target
