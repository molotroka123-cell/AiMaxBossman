"""Markdown chunking that preserves common semantic atoms.

Ideas taken from document-RAG research, implemented on stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


@dataclass(slots=True)
class Block:
    kind: str
    text: str
    atomic: bool = False


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and bool(_TABLE_SEPARATOR.match(lines[i + 1]))
    )


def parse_blocks(text: str) -> list[Block]:
    lines = text.splitlines()
    out: list[Block] = []
    paragraph: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        body = "\n".join(paragraph).strip()
        if body:
            out.append(Block("paragraph", body, atomic=False))
        paragraph = []

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            fence = stripped[:3]
            buf = [line]
            i += 1
            while i < len(lines):
                buf.append(lines[i])
                if lines[i].lstrip().startswith(fence):
                    i += 1
                    break
                i += 1
            out.append(Block("fence", "\n".join(buf), atomic=True))
            continue

        if _is_table_start(lines, i):
            flush_paragraph()
            buf = [lines[i], lines[i + 1]]
            i += 2
            while i < len(lines):
                row = lines[i]
                if not row.strip() or "|" not in row:
                    break
                buf.append(row)
                i += 1
            out.append(Block("table", "\n".join(buf), atomic=True))
            continue

        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph()
    return out


def _hard_split(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    result: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        piece = text[start:end]
        if end < len(text):
            local = max(piece.rfind("\n"), piece.rfind(" "))
            if local >= int(max_chars * 0.65):
                end = start + local
                piece = text[start:end]
        piece = piece.strip()
        if piece:
            result.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def split_long_markdown(
    text: str,
    *,
    max_chars: int = 1400,
    overlap: int = 160,
    max_atomic_chars: int = 8000,
) -> list[str]:
    """Pack markdown into chunks while keeping fences/tables atomic when possible."""
    if len(text) <= max_chars:
        return [text]

    blocks = parse_blocks(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current).strip())
        current = []
        current_len = 0

    for block in blocks:
        body = block.text.strip()
        if not body:
            continue

        if block.atomic:
            if len(body) <= max_atomic_chars:
                flush()
                chunks.append(body)
                continue

            flush()
            if block.kind == "fence":
                lines = body.splitlines()
                opener = lines[0] if lines else "```"
                fence = opener[:3] if opener[:3] in ("```", "~~~") else "```"
                inner = "\n".join(lines[1:-1] if len(lines) > 2 else lines[1:])
                pieces = _hard_split(inner, max_atomic_chars - 16, overlap)
                chunks.extend([f"{opener}\n{piece}\n{fence}" for piece in pieces])
            else:
                chunks.extend(_hard_split(body, max_atomic_chars, overlap))
            continue

        if len(body) > max_chars * 2:
            flush()
            chunks.extend(_hard_split(body, max_chars, overlap))
            continue

        cost = len(body) + (2 if current else 0)
        if current and current_len + cost > max_chars:
            flush()
        current.append(body)
        current_len += cost

    flush()
    return chunks or _hard_split(text, max_chars, overlap)
