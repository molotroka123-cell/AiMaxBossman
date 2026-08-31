"""V2.6 — Universal File Intelligence (модуль J): typed parse-router.

file → определение типа → подходящий парсер → структурное извлечение → typed
представление с provenance (файл / страница / лист / ячейка / слайд / hash).
Таблицы остаются таблицами, слайды — слайдами; НИКАКОЙ свалки в один гигантский
текст. Stdlib-first: CSV/JSON/TXT/MD/ZIP — всегда; DOCX/XLSX/PPTX — это
zip+xml, разбираются stdlib'ом; PDF — через pypdf, если установлен, иначе
ЧЕСТНЫЙ unavailable (не тихая деградация). PNG — метаданные (размеры) без
Pillow. Повторный разбор того же контента отдаётся из execution cache по
sha256 (ровно «GOOD»-случай модуля E). Контент файла — ДАННЫЕ, не команды:
наружу уходит через существующую границу ingest (rights=read → external-data
header + ingest_guard в runner).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_BYTES = 40 * 1024 * 1024          # жёсткий потолок размера
MAX_SECTIONS = 400                     # ограничение выхлопа
MAX_CELL_TEXT = 2000

TEXT_EXTS = {".txt", ".md", ".log", ".py", ".js", ".ts", ".yaml", ".yml", ".toml",
             ".html", ".css", ".sql", ".sh", ".ini", ".cfg"}


class ParseUnavailable(RuntimeError):
    """Формат распознан, но парсер недоступен в этой установке (честный отказ)."""


@dataclass(frozen=True, slots=True)
class ArtifactSection:
    ref: str                    # provenance: "page=3" / "sheet=Лист1!A1:C9" / "slide=2"
    kind: str                   # text | table | slide | code | media | entry
    text: str = ""
    table: tuple[tuple[str, ...], ...] = ()
    meta: dict = field(default=None)


@dataclass(frozen=True, slots=True)
class ParsedArtifact:
    kind: str                   # pdf|docx|xlsx|csv|pptx|txt|md|json|zip|image|code
    source_path: str
    content_hash: str
    sections: tuple[ArtifactSection, ...]
    warnings: tuple[str, ...] = ()

    def provenance(self, section: ArtifactSection) -> dict:
        return {"file": self.source_path, "ref": section.ref,
                "content_hash": self.content_hash}


def detect_kind(path: Path, head: bytes) -> str:
    ext = path.suffix.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        return {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}.get(ext, "zip")
    if ext == ".csv":
        return "csv"
    if ext == ".json":
        return "json"
    if ext in (".md",):
        return "md"
    if ext in TEXT_EXTS:
        return "code" if ext in (".py", ".js", ".ts", ".sql", ".sh") else "txt"
    return "txt"


# ---------- парсеры ----------

def _parse_text(data: bytes, kind: str) -> list[ArtifactSection]:
    text = data.decode("utf-8", "replace")
    # markdown: секции по заголовкам, обычный текст — одним куском
    if kind == "md":
        parts = re.split(r"(?m)^(#{1,4} .*)$", text)
        out, current_head = [], "начало"
        buf = parts[0]
        if buf.strip():
            out.append(ArtifactSection(ref="section=начало", kind="text", text=buf.strip()))
        for i in range(1, len(parts) - 1, 2):
            head, body = parts[i].strip(), parts[i + 1]
            out.append(ArtifactSection(ref=f"section={head[:80]}", kind="text",
                                       text=(head + "\n" + body).strip()))
        return out or [ArtifactSection(ref="section=пусто", kind="text", text="")]
    return [ArtifactSection(ref="text=1", kind="code" if kind == "code" else "text",
                            text=text)]


def _parse_csv(data: bytes) -> list[ArtifactSection]:
    text = data.decode("utf-8", "replace")
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        pass
    rows = [tuple(c[:MAX_CELL_TEXT] for c in r)
            for r in csv.reader(io.StringIO(text), dialect)]
    return [ArtifactSection(ref=f"rows=1:{len(rows)}", kind="table",
                            table=tuple(rows[:5000]),
                            meta={"columns": len(rows[0]) if rows else 0})]


def _safe_zip_names(zf: zipfile.ZipFile) -> list[str]:
    """Только безопасные члены: без traversal/абсолютных путей (та же дисциплина,
    что и sandbox ArtifactGate — не второй гейт, а тот же принцип на чтении)."""
    names = []
    for info in zf.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            continue
        names.append(name)
    return names


def _parse_zip(data: bytes) -> list[ArtifactSection]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = _safe_zip_names(zf)
        sections = [ArtifactSection(
            ref=f"entry={n}", kind="entry", text=n,
            meta={"size": zf.getinfo(n).file_size}) for n in entries[:MAX_SECTIONS]]
    return sections


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _parse_docx(data: bytes) -> list[ArtifactSection]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    out: list[ArtifactSection] = []
    for i, p in enumerate(root.iter(f"{_W}p"), start=1):
        text = "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()
        if text:
            out.append(ArtifactSection(ref=f"paragraph={i}", kind="text", text=text))
    # таблицы документа — таблицами
    for ti, tbl in enumerate(root.iter(f"{_W}tbl"), start=1):
        rows = []
        for tr in tbl.iter(f"{_W}tr"):
            rows.append(tuple("".join(t.text or "" for t in tc.iter(f"{_W}t")).strip()
                              for tc in tr.iter(f"{_W}tc")))
        if rows:
            out.append(ArtifactSection(ref=f"table={ti}", kind="table", table=tuple(rows)))
    return out[:MAX_SECTIONS]


def _col_letters(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _parse_xlsx(data: bytes) -> list[ArtifactSection]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        # имена листов из workbook.xml в порядке следования
        sheet_names: list[str] = []
        if "xl/workbook.xml" in zf.namelist():
            for sh in ET.fromstring(zf.read("xl/workbook.xml")).iter(f"{_S}sheet"):
                sheet_names.append(sh.get("name") or f"sheet{len(sheet_names) + 1}")
        out: list[ArtifactSection] = []
        idx = 0
        for name in sorted(n for n in zf.namelist()
                           if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
            idx += 1
            title = sheet_names[idx - 1] if idx - 1 < len(sheet_names) else f"sheet{idx}"
            root = ET.fromstring(zf.read(name))
            rows: list[tuple[str, ...]] = []
            for row in root.iter(f"{_S}row"):
                cells: dict[str, str] = {}
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    raw = v.text if v is not None else ""
                    if c.get("t") == "s" and raw and raw.isdigit() and int(raw) < len(shared):
                        raw = shared[int(raw)]
                    cells[_col_letters(c.get("r") or "")] = (raw or "")[:MAX_CELL_TEXT]
                if cells:
                    cols = sorted(cells)  # стабильный порядок колонок
                    rows.append(tuple(cells[k] for k in cols))
            out.append(ArtifactSection(
                ref=f"sheet={title}", kind="table", table=tuple(rows[:5000]),
                meta={"sheet": title, "rows": len(rows)}))
        return out[:MAX_SECTIONS]


def _parse_pptx(data: bytes) -> list[ArtifactSection]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        out: list[ArtifactSection] = []
        slides = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n).group()))
        for n in slides:
            num = int(re.search(r"\d+", n).group())
            root = ET.fromstring(zf.read(n))
            texts = [t.text for t in root.iter(f"{_A}t") if t.text and t.text.strip()]
            out.append(ArtifactSection(ref=f"slide={num}", kind="slide",
                                       text="\n".join(texts), meta={"slide": num}))
        return out[:MAX_SECTIONS]


def _parse_pdf(data: bytes) -> list[ArtifactSection]:
    try:
        from pypdf import PdfReader  # optional dependency — честно
    except ImportError:
        raise ParseUnavailable(
            "PDF-парсер недоступен: установите pypdf (pip install pypdf)")
    reader = PdfReader(io.BytesIO(data))
    out = []
    for i, page in enumerate(reader.pages[:MAX_SECTIONS], start=1):
        out.append(ArtifactSection(ref=f"page={i}", kind="text",
                                   text=(page.extract_text() or "").strip(),
                                   meta={"page": i}))
    return out


def _parse_image(data: bytes, path: Path) -> list[ArtifactSection]:
    meta: dict = {"bytes": len(data), "format": path.suffix.lstrip(".").lower()}
    if data.startswith(b"\x89PNG") and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        meta.update(width=w, height=h)
    return [ArtifactSection(ref="media=1", kind="media",
                            text=f"изображение {meta.get('width', '?')}x{meta.get('height', '?')}",
                            meta=meta)]


_PARSERS = {
    "csv": lambda d, p: _parse_csv(d),
    "json": lambda d, p: [ArtifactSection(
        ref="json=1", kind="text",
        text=json.dumps(json.loads(d.decode("utf-8", "replace")),
                        ensure_ascii=False, indent=1)[:200_000])],
    "zip": lambda d, p: _parse_zip(d),
    "docx": lambda d, p: _parse_docx(d),
    "xlsx": lambda d, p: _parse_xlsx(d),
    "pptx": lambda d, p: _parse_pptx(d),
    "pdf": lambda d, p: _parse_pdf(d),
    "image": _parse_image,
}


def parse_file(path: str | Path) -> ParsedArtifact:
    """Разобрать файл в typed-представление. Кэш по sha256 контента."""
    p = Path(path)
    data = p.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError(f"файл больше лимита {MAX_BYTES} байт")
    digest = hashlib.sha256(data).hexdigest()

    from .exec_cache import get_cache
    cache = get_cache()
    key = cache.key("parsed_file", digest)
    hit = cache.get(key)
    if hit is not None:
        return hit.result

    kind = detect_kind(p, data[:8])
    warnings: list[str] = []
    parser = _PARSERS.get(kind)
    if parser is None:
        sections = _parse_text(data, kind)
    else:
        sections = parser(data, p)
    art = ParsedArtifact(kind=kind, source_path=str(p), content_hash=digest,
                         sections=tuple(sections[:MAX_SECTIONS]),
                         warnings=tuple(warnings))
    cache.put(key, art, verified=True, evidence=f"parsed {p.name} sha256={digest[:12]}")
    return art


def render_compact(art: ParsedArtifact, *, max_chars: int = 8000) -> str:
    """Компактный текст для модели С СОХРАНЕНИЕМ provenance-ссылок."""
    lines = [f"[{art.kind}] {art.source_path} sha256={art.content_hash[:12]}"]
    for s in art.sections:
        if s.kind == "table":
            head = s.table[:8]
            body = "\n".join(" | ".join(r) for r in head)
            more = f" (+{len(s.table) - 8} строк)" if len(s.table) > 8 else ""
            lines.append(f"--- {s.ref} (таблица){more}\n{body}")
        else:
            lines.append(f"--- {s.ref}\n{s.text[:1500]}")
        if sum(len(x) for x in lines) > max_chars:
            lines.append(f"[обрезано: всего секций {len(art.sections)}]")
            break
    return "\n".join(lines)[:max_chars + 200]
