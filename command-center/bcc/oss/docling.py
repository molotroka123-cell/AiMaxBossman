"""Optional, model-free local document extraction using upstream Docling.

Only byte snapshots of owner-scoped files reach Docling. Native PDF conversion
does not download layout/OCR models; scanned PDFs report NO_TEXT explicitly.
Office files are bounded before decompression. Nothing is indexed or persisted.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import stat
import threading
from typing import Any
import zipfile

from ..file_intelligence.scope import ScopePolicy

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".csv"})
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_CHARS = 200_000
_CONVERSION_LOCK = threading.Lock()


class DoclingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _snapshot(path: str, policy: ScopePolicy) -> tuple[Path, bytes]:
    if "://" in path or path.startswith(("\\\\", "//")):
        raise DoclingError("LOCAL_FILE_REQUIRED", "Select a local file, not a URL or network share.")
    resolved = policy.check(path, mutating=False)
    if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise DoclingError("UNSUPPORTED_FORMAT", "Supported: PDF, DOCX, PPTX, XLSX, CSV.")
    try:
        fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            current = policy.check(path, mutating=False).stat()
            if not stat.S_ISREG(before.st_mode):
                raise DoclingError("LOCAL_FILE_REQUIRED", "Select a regular local file.")
            if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise DoclingError("SOURCE_CHANGED", "The selected file changed; select it again.")
            if before.st_size > MAX_FILE_BYTES:
                raise DoclingError("FILE_TOO_LARGE", "The document exceeds the 20 MiB limit.")
            content = source.read(MAX_FILE_BYTES + 1)
            after = os.fstat(source.fileno())
            policy.check(path, mutating=False)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise DoclingError("SOURCE_CHANGED", "The selected file changed while being read.")
    except OSError as exc:
        raise DoclingError("FILE_UNAVAILABLE", "The selected local file cannot be read.") from exc
    if len(content) > MAX_FILE_BYTES:
        raise DoclingError("FILE_TOO_LARGE", "The document exceeds the 20 MiB limit.")
    if not content:
        raise DoclingError("EMPTY_FILE", "The selected file is empty.")
    if resolved.suffix.lower() in {".docx", ".pptx", ".xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members) > 2000 or sum(m.file_size for m in members) > MAX_EXPANDED_BYTES:
                    raise DoclingError("DOCUMENT_TOO_LARGE", "The expanded Office document exceeds limits.")
                if any(m.flag_bits & 1 for m in members):
                    raise DoclingError("ENCRYPTED_DOCUMENT", "Encrypted Office documents are unsupported.")
        except zipfile.BadZipFile as exc:
            raise DoclingError("INVALID_DOCUMENT", "The Office document is not a valid container.") from exc
    return resolved, content


def _convert(content: bytes, name: str, max_pages: int) -> Any:
    # Import inside the action: the optional package must never block app startup.
    try:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import NativePdfPipelineOptions
        from docling.document_converter import DocumentConverter, NativePdfFormatOption
    except ImportError as exc:
        raise DoclingError(
            "DEPENDENCY_MISSING",
            "Install the Bossman 'documents' extra to enable local Docling extraction.",
        ) from exc
    formats = {
        ".pdf": InputFormat.PDF, ".docx": InputFormat.DOCX,
        ".pptx": InputFormat.PPTX, ".xlsx": InputFormat.XLSX, ".csv": InputFormat.CSV,
    }
    selected = formats[Path(name).suffix.lower()]
    options = {}
    if selected == InputFormat.PDF:
        options[selected] = NativePdfFormatOption(pipeline_options=NativePdfPipelineOptions(
            generate_page_images=False, enable_remote_services=False,
            allow_external_plugins=False,
        ))
    converter = DocumentConverter(allowed_formats=[selected], format_options=options)
    return converter.convert(
        DocumentStream(name=name, stream=io.BytesIO(content)),
        raises_on_error=False, max_num_pages=max_pages, max_file_size=MAX_FILE_BYTES,
    )


def extract_document(path: str, *, policy: ScopePolicy, max_pages: int = 100,
                     max_chars: int = MAX_OUTPUT_CHARS) -> dict[str, Any]:
    """Read one authorized document, return bounded Markdown and source identity.

    SUCCESS means upstream completed and text exists; partial/empty conversion
    never becomes a false successful extraction. Only one parser runs at a time.
    """
    if not 1 <= max_pages <= 100 or not 1 <= max_chars <= MAX_OUTPUT_CHARS:
        raise DoclingError("INVALID_LIMIT", "Use 1–100 pages and 1–200000 output characters.")
    if not _CONVERSION_LOCK.acquire(blocking=False):
        raise DoclingError("BUSY", "A document is already being extracted. Try again when it finishes.")
    try:
        resolved, content = _snapshot(path, policy)
        try:
            result = _convert(content, resolved.name, max_pages)
            raw_status = result.status
            status = str(getattr(raw_status, "value", raw_status)).lower()
            if status != "success":
                raise DoclingError("CONVERSION_INCOMPLETE", "Docling could not completely convert this document.")
            markdown = result.document.export_to_markdown()
            if not isinstance(markdown, str) or not markdown.strip():
                raise DoclingError("NO_TEXT", "No text found. Scanned documents require a separately configured OCR engine.")
            return {
                "ok": True, "provider": "docling", "mode": "native_no_models",
                "name": resolved.name, "source_sha256": hashlib.sha256(content).hexdigest(),
                "markdown": markdown[:max_chars], "truncated": len(markdown) > max_chars,
                "pages": len(getattr(result.document, "pages", {})),
                "ocr": False,
            }
        except DoclingError:
            raise
        except Exception as exc:
            # Parser diagnostics can contain private document content/paths.
            raise DoclingError("CONVERSION_FAILED", "Docling could not read this document.") from exc
    finally:
        _CONVERSION_LOCK.release()
