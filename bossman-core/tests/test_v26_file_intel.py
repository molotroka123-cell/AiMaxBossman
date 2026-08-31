"""V2.6 модуль J — Universal File Intelligence: typed-разбор реальных файлов.

Без Postgres: file_intel — чистый stdlib + in-proc exec_cache. Фикстуры
OOXML (docx/xlsx/pptx) собираются крошечными рукописными xml в zip — ровно
те namespace'ы, что использует парсер. PDF: если pypdf не установлен —
проверяем ЧЕСТНЫЙ ParseUnavailable, а не тихую деградацию.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

from bossman.exec_cache import get_cache
from bossman.file_intel import ParseUnavailable, parse_file, render_compact

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


# ---------- фикстуры-конструкторы ----------

def _make_docx(path) -> None:
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}"><w:body>
 <w:p><w:r><w:t>Первый абзац договора</w:t></w:r></w:p>
 <w:p><w:r><w:t>Пункт 4.2: аренда 1200 в месяц</w:t></w:r></w:p>
 <w:tbl>
  <w:tr><w:tc><w:p><w:r><w:t>статья</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>сумма</w:t></w:r></w:p></w:tc></w:tr>
  <w:tr><w:tc><w:p><w:r><w:t>аренда</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>1200</w:t></w:r></w:p></w:tc></w:tr>
 </w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc)


def _make_xlsx(path) -> None:
    wb = f'<workbook xmlns="{S_NS}"><sheets><sheet name="Отчёт" sheetId="1"/></sheets></workbook>'
    sst = (f'<sst xmlns="{S_NS}" count="2" uniqueCount="2">'
           f"<si><t>выручка</t></si><si><t>месяц</t></si></sst>")
    sheet = f"""<worksheet xmlns="{S_NS}"><sheetData>
 <row r="1"><c r="A1" t="s"><v>1</v></c><c r="B1" t="s"><v>0</v></c></row>
 <row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>1200</v></c></row>
</sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def _make_pptx(path) -> None:
    slide = (f'<sld xmlns:a="{A_NS}"><a:t>Итоги квартала</a:t>'
             f"<a:t>выручка выросла</a:t></sld>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", slide)


def _png_1x1() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II5B", 1, 1, 8, 6, 0, 0, 0)
    return (sig + struct.pack(">I", 13) + b"IHDR" + ihdr
            + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))


# ---------- тесты по форматам ----------

def test_csv_rows_preserved_as_table(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("месяц,выручка\nянварь,1200\nфевраль,1300\n", encoding="utf-8")
    art = parse_file(p)
    assert art.kind == "csv"
    sec = art.sections[0]
    assert sec.kind == "table"
    assert sec.table[0] == ("месяц", "выручка")
    assert sec.table[1] == ("январь", "1200")
    assert sec.ref.startswith("rows=1:")            # provenance-ссылка
    assert art.provenance(sec)["content_hash"] == art.content_hash


def test_json_parsed(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text('{"budget": 500, "город": "Москва"}', encoding="utf-8")
    art = parse_file(p)
    assert art.kind == "json"
    assert '"budget"' in art.sections[0].text
    assert "Москва" in art.sections[0].text


def test_md_split_by_headings(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("вступление\n# План\nшаг один\n## Детали\nшаг два\n",
                 encoding="utf-8")
    art = parse_file(p)
    assert art.kind == "md"
    refs = [s.ref for s in art.sections]
    assert "section=начало" in refs
    assert any(r.startswith("section=# План") for r in refs)
    assert any(r.startswith("section=## Детали") for r in refs)


def test_zip_entries_listed_traversal_skipped(tmp_path):
    p = tmp_path / "bundle.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("good.txt", "ok")
        zf.writestr("../evil", "нет")            # traversal-член — должен быть пропущен
        zf.writestr("/abs.txt", "нет")           # абсолютный — тоже
    art = parse_file(p)
    assert art.kind == "zip"
    names = [s.text for s in art.sections]
    assert "good.txt" in names
    assert all(".." not in n for n in names)
    assert all(not n.startswith("/") for n in names)


def test_docx_paragraphs_and_table(tmp_path):
    p = tmp_path / "contract.docx"
    _make_docx(p)
    art = parse_file(p)
    assert art.kind == "docx"
    paras = [s for s in art.sections if s.kind == "text"]
    assert any("Первый абзац договора" in s.text for s in paras)
    assert any("Пункт 4.2" in s.text for s in paras)
    tables = [s for s in art.sections if s.kind == "table"]
    assert tables and ("аренда", "1200") in tables[0].table
    assert tables[0].ref == "table=1"


def test_xlsx_shared_strings_and_sheet_ref(tmp_path):
    p = tmp_path / "report.xlsx"
    _make_xlsx(p)
    art = parse_file(p)
    assert art.kind == "xlsx"
    sec = art.sections[0]
    assert sec.kind == "table"
    assert sec.ref == "sheet=Отчёт"               # имя листа в provenance
    assert sec.table[0] == ("месяц", "выручка")   # shared strings разрешены
    assert sec.table[1] == ("1", "1200")


def test_pptx_slide_text(tmp_path):
    p = tmp_path / "deck.pptx"
    _make_pptx(p)
    art = parse_file(p)
    assert art.kind == "pptx"
    sec = art.sections[0]
    assert sec.kind == "slide" and sec.ref == "slide=1"
    assert "Итоги квартала" in sec.text and "выручка выросла" in sec.text


def test_png_dimensions_meta(tmp_path):
    p = tmp_path / "pix.png"
    p.write_bytes(_png_1x1())
    art = parse_file(p)
    assert art.kind == "image"
    meta = art.sections[0].meta
    assert meta["width"] == 1 and meta["height"] == 1


def test_pdf_honest_unavailable_or_parses(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n%%EOF\n")
    try:
        import pypdf  # noqa: F401
    except ImportError:
        with pytest.raises(ParseUnavailable) as exc:
            parse_file(p)
        assert "pypdf" in str(exc.value)          # честный отказ с подсказкой
    else:
        # с установленным pypdf нужен настоящий минимальный pdf
        from pypdf import PdfWriter
        buf = io.BytesIO()
        w = PdfWriter()
        w.add_blank_page(width=72, height=72)
        w.write(buf)
        p.write_bytes(buf.getvalue())
        art = parse_file(p)
        assert art.kind == "pdf"
        assert art.sections[0].ref == "page=1"


def test_second_parse_served_from_cache(tmp_path):
    p = tmp_path / "cached.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    first = parse_file(p)
    hits_before = get_cache().stats()["hits"]
    second = parse_file(p)
    assert get_cache().stats()["hits"] == hits_before + 1
    assert second is first                        # тот же объект из кэша


def test_render_compact_keeps_provenance(tmp_path):
    p = tmp_path / "report.xlsx"
    _make_xlsx(p)
    art = parse_file(p)
    out = render_compact(art)
    assert "sheet=Отчёт" in out                   # provenance-ссылка в выхлопе
    assert art.content_hash[:12] in out
    assert "выручка" in out
