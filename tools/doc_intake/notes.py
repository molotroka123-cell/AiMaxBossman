"""Parsed units -> Markdown notes in the vault's existing source-note format.

Format matches the hand-made ``Sources/JPM-LTCMA-2027`` notes (front matter with
``kind: source``, ``pages: a-b``, ``ingested``, ``tags``; ``# Title — стр. a–b``;
one ``### Стр. N`` section per page) plus library identity fields. Each page is
its own heading, so the memory index keeps "Стр. N" as the chunk heading and an
answer can cite «стр. 12». A page larger than the note cap is split into parts
that keep the page number.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .parsers import ParseResult, Unit, split_text

DEFAULT_NOTE_CHARS = 20_000
DEFAULT_NOTE_UNITS = 6

#: unit kind -> (front-matter key, Russian range word plural, singular)
RANGE_WORDS = {
    "page": ("pages", "стр.", "стр."),
    "slide": ("slides", "слайды", "слайд"),
    "sheet": ("blocks", "блоки", "блок"),
    "rows": ("blocks", "блоки", "блок"),
    "section": ("sections", "разделы", "раздел"),
    "fragment": ("sections", "фрагменты", "фрагмент"),
    "chapter": ("sections", "главы", "глава"),
    "meta": ("sections", "описание", "описание"),
}


@dataclass
class Note:
    filename: str
    first: int
    last: int
    text: str


def one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def yaml_str(value: object) -> str:
    """A JSON string is a valid YAML double-quoted scalar; one line, quotes escaped."""
    return json.dumps(one_line(value), ensure_ascii=False)


def clean_tag(tag: str) -> str:
    return re.sub(r"[^\w\-./]+", "-", one_line(tag)).strip("-")[:40]


def _expand(units: list[Unit], limit: int) -> list[Unit]:
    out: list[Unit] = []
    for unit in units:
        if not unit.text.strip() and unit.kind != "meta":
            continue                       # empty pages stay listed in the manifest, not as empty notes
        parts = split_text(unit.text, limit) or [unit.text]
        if len(parts) == 1:
            out.append(unit)
            continue
        for i, part in enumerate(parts, start=1):
            out.append(Unit(unit.kind, unit.number, f"{unit.label} (часть {i}/{len(parts)})", part))
    return out


def _groups(units: list[Unit], max_chars: int, max_units: int) -> list[list[Unit]]:
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    size = 0
    for unit in units:
        numbers = {u.number for u in current}
        too_big = current and size + len(unit.text) > max_chars
        too_many = current and unit.number not in numbers and len(numbers) >= max_units
        if too_big or too_many:
            groups.append(current)
            current, size = [], 0
        current.append(unit)
        size += len(unit.text) + len(unit.label) + 8
    if current:
        groups.append(current)
    return groups


def span(first: int, last: int, dash: str = "–") -> str:
    return str(first) if first == last else f"{first}{dash}{last}"


def render_notes(entry: dict, result: ParseResult, *, max_chars: int = DEFAULT_NOTE_CHARS,
                 max_units: int = DEFAULT_NOTE_UNITS) -> list[Note]:
    """Notes for one library entry. ``entry`` supplies identity: id, title, slug, original_name,
    sha256, category, tags, created (ISO)."""
    units = _expand(result.units, max(1000, max_chars - 500))
    if not units:
        return []
    kind = units[0].kind
    fm_key, plural, singular = RANGE_WORDS.get(kind, ("sections", "разделы", "раздел"))
    prefix = "p" if kind == "page" else "s"
    slug = entry["slug"][:40]
    tags = []
    for tag in ["library", entry["category"], *entry.get("tags", [])]:
        tag = clean_tag(tag)
        if tag and tag not in tags:
            tags.append(tag)
    notes: list[Note] = []
    used: set[str] = set()
    for group in _groups(units, max_chars, max_units):
        first, last = min(u.number for u in group), max(u.number for u in group)
        name = f"{slug}_{prefix}{first:03d}-{last:03d}"
        filename, k = f"{name}.md", 2
        while filename in used:
            filename, k = f"{name}-{k}.md", k + 1
        used.add(filename)
        front = [
            "---",
            "kind: source",
            f"title: {yaml_str(entry['title'])}",
            f"source: {yaml_str(entry['original_name'])}",
            f"source_sha256: {entry['sha256']}",
            f"library_id: {entry['id']}",
            f"category: {entry['category']}",
            f"{fm_key}: {span(first, last, '-')}",
        ]
        if result.page_count:
            front.append(f"page_count: {result.page_count}")
        front += [
            f"parser: {yaml_str(result.parser)}",
            f"parse_status: {result.status}",
            f"ingested: {entry['created'][:10]}",
            "tags: [" + ", ".join(tags) + "]",
            "---",
            "",
        ]
        word = singular if first == last else plural
        body = [f"# {one_line(entry['title'])} — {word} {span(first, last)}", ""]
        body.append(f"> Источник: `{one_line(entry['original_name'])}` · sha256 `{entry['sha256'][:12]}` · "
                    f"категория {entry['category']} · разбор {result.status} ({one_line(result.parser)})")
        if result.tables == "flattened":
            body.append("> Таблицы извлечены как плоский текст (pypdf): столбцы могут быть слиты.")
        if result.status != "PASS" and result.reason:
            body.append(f"> Ограничение разбора: {one_line(result.reason)}")
        body.append("")
        for unit in group:
            body += [f"### {one_line(unit.label)}", unit.text.strip(), ""]
        notes.append(Note(filename, first, last, "\n".join(front + body).rstrip() + "\n"))
    return notes


def add_front_matter_line(text: str, line: str) -> str:
    """Insert ``line`` just before the closing '---' of the front matter."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[:end] + "\n" + line + text[end:]
