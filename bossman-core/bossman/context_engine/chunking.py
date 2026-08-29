from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from .models import Chunk, Document
from .utils import normalize_space, sha256_text, stable_id, token_estimate

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
_CODE_SYMBOL = re.compile(
    r"^(?:async\s+def|def|class|function|export\s+(?:async\s+)?function|const|let|interface|type|struct|enum)\s+([A-Za-z_$][\w$]*)",
    re.M,
)


def _split_markdown(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text)]
    out: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        out.append(("", text[: matches[0].start()]))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((match.group(2).strip(), text[start:end]))
    return out


def _split_code(text: str) -> list[tuple[str, str]]:
    matches = list(_CODE_SYMBOL.finditer(text))
    if not matches:
        return [("", text)]
    out: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        out.append(("preamble", text[: matches[0].start()]))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((match.group(1), text[match.start():end]))
    return out


def _window(text: str, max_tokens: int, overlap_tokens: int) -> Iterable[str]:
    text = normalize_space(text)
    if not text:
        return []
    approx_chars = max_tokens * 3
    overlap_chars = overlap_tokens * 3
    if len(text) <= approx_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + approx_chars)
        if end < len(text):
            candidate = text.rfind("\n", start + approx_chars // 2, end)
            if candidate < 0:
                candidate = text.rfind(". ", start + approx_chars // 2, end)
            if candidate > start:
                end = candidate + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def chunk_document(doc: Document, max_tokens: int = 900, overlap_tokens: int = 80) -> list[Chunk]:
    source = doc.source_type.lower()
    if source in {"md", "markdown", "chat", "memory", "audit"}:
        sections = _split_markdown(doc.text)
    elif source in {"py", "python", "js", "javascript", "ts", "typescript", "code", "source"}:
        sections = _split_code(doc.text)
    else:
        sections = [("", doc.text)]

    out: list[Chunk] = []
    ordinal = 0
    for heading, body in sections:
        for piece in _window(body, max_tokens, overlap_tokens):
            h = sha256_text(piece)
            out.append(
                Chunk(
                    chunk_id=stable_id("chk", doc.document_id, str(ordinal), h),
                    document_id=doc.document_id,
                    text=piece,
                    ordinal=ordinal,
                    heading=heading,
                    token_count=token_estimate(piece),
                    project=doc.project,
                    source_type=doc.source_type,
                    source_uri=doc.source_uri,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                    sensitivity=doc.sensitivity,
                    content_hash=h,
                    metadata=dict(doc.metadata),
                )
            )
            ordinal += 1
    return out
