"""Optional local document extraction for Master Parser Telegram exports.

Docling is provided by the optional ``documents`` extra (included in the
owner runtime). PDF layout models must be
pre-fetched by the owner and exposed through ``DOCLING_ARTIFACTS_PATH``; this
adapter refuses to initialize without that local directory, so a first parse
cannot trigger an implicit model download. Plain text and Markdown are handled
by the source reader without this module.
"""
from __future__ import annotations

import os
import zipfile
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Callable

MAX_OFFICE_ARCHIVE_ENTRIES = 4096
MAX_OFFICE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_OFFICE_TOTAL_BYTES = 128 * 1024 * 1024


def _validate_office_archive(path: Path) -> None:
    """Bound OOXML expansion before passing an untrusted ZIP container to Docling."""
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_OFFICE_ARCHIVE_ENTRIES:
                raise RuntimeError("office archive has too many entries")
            for member in members:
                name = member.filename.replace("\\", "/")
                parts = name.split("/")
                if (name.startswith("/") or any(part in {"", ".."} for part in parts[:-1])
                        or member.flag_bits & 0x1):
                    raise RuntimeError("office archive contains an unsafe entry")
                if member.file_size > MAX_OFFICE_MEMBER_BYTES:
                    raise RuntimeError("office archive member is too large")
                total += member.file_size
                if total > MAX_OFFICE_TOTAL_BYTES:
                    raise RuntimeError("office archive expands beyond the size limit")
    except zipfile.BadZipFile as exc:
        raise RuntimeError("invalid Office Open XML archive") from exc


def build_docling_parser() -> Callable[[Path], str]:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise RuntimeError("install the optional master-parser-documents extra") from exc

    office_formats = [InputFormat.DOCX, InputFormat.PPTX, InputFormat.XLSX]
    office_converter = DocumentConverter(allowed_formats=office_formats)
    pdf_converter: DocumentConverter | None = None
    # Hugging Face offline switches are process-global. Serialize PDF conversion
    # and restore the previous values so a Master Parser call cannot silently
    # change networking policy for unrelated Bossman model adapters.
    pdf_lock = RLock()

    def get_pdf_converter() -> DocumentConverter:
        nonlocal pdf_converter
        if pdf_converter is not None:
            return pdf_converter
        artifacts = os.environ.get("DOCLING_ARTIFACTS_PATH", "").strip()
        if not artifacts or not Path(artifacts).is_dir():
            raise RuntimeError("local Docling PDF model artifacts not configured")
        options = PdfPipelineOptions(artifacts_path=Path(artifacts))
        pdf_converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
        return pdf_converter

    def parse(path: Path) -> str:
        # Only an already-resolved local path reaches Docling; URL input is never used.
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            with pdf_lock:
                old_hf = os.environ.get("HF_HUB_OFFLINE")
                old_transformers = os.environ.get("TRANSFORMERS_OFFLINE")
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                try:
                    # Converter construction may load model components, so it
                    # must also happen under the offline guard on the first PDF.
                    converter = get_pdf_converter()
                    converted = converter.convert(path, max_num_pages=100,
                                                  max_file_size=32 * 1024 * 1024)
                finally:
                    if old_hf is None:
                        os.environ.pop("HF_HUB_OFFLINE", None)
                    else:
                        os.environ["HF_HUB_OFFLINE"] = old_hf
                    if old_transformers is None:
                        os.environ.pop("TRANSFORMERS_OFFLINE", None)
                    else:
                        os.environ["TRANSFORMERS_OFFLINE"] = old_transformers
        elif path.suffix.lower() in {".docx", ".pptx", ".xlsx"}:
            _validate_office_archive(path)
            converted = office_converter.convert(path, max_file_size=32 * 1024 * 1024)
        else:
            raise RuntimeError("unsupported Docling input type")
        return converted.document.export_to_markdown()

    return parse


@lru_cache(maxsize=1)
def _cached_parser() -> Callable[[Path], str]:
    return build_docling_parser()


def parse_with_docling(path: Path) -> str:
    """Lazily initialize one converter per process and reuse it for attachments."""
    return _cached_parser()(path)
