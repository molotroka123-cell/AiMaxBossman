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


# Минимальный PDF по СПЕЦИФИКАЦИИ, а не нашим кодом и не pypdf. Holdout того же
# рода, что openpyxl для xlsx: фикстура, написанная автором парсера, проверяет
# общее с парсером допущение, а не формат.
def _spec_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


def test_pdf_actually_returns_the_text_that_is_in_the_file(tmp_path):
    """Прежний тест PDF проверял только `kind == "pdf"` и `ref == "page=1"` на
    ПУСТОЙ странице — то есть остался бы зелёным, если бы извлечение текста
    возвращало пустоту для любого документа. Возможность «читать PDF» — это
    текст, а не заголовок раздела, поэтому проверяется текст.

    Пропуск здесь ЧЕСТНЫЙ и виден в реестре: без extra `documents` возможности
    в установке нет, и делать вид, что она есть, нельзя."""
    pytest.importorskip("pypdf", reason="разбор PDF ставится extra `documents`")
    p = tmp_path / "planted.pdf"
    p.write_bytes(_spec_pdf("MARKERpdfBODY"))
    art = parse_file(p)
    assert art.kind == "pdf"
    assert "MARKERpdfBODY" in art.sections[0].text, art.sections[0].text


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


# --------------------------------------------------------------------------
# BL-033: инлайновые строки в xlsx терялись МОЛЧА
# --------------------------------------------------------------------------
# OOXML хранит текст ячейки двумя равноправными способами: ссылкой в
# sharedStrings (`t="s"` + `<v>индекс`) и ПРЯМО в ячейке
# (`t="inlineStr"` + `<is><t>текст`). Парсер читал только `<v>`, поэтому второй
# способ давал пустую строку — без ошибки, без предупреждения, без пропуска.
# Таблица «разбиралась успешно» и приходила к модели пустой.
#
# Почему это дожило до сих пор: единственная фикстура xlsx в этом файле была
# написана руками и использовала ТОЛЬКО shared strings. Тест повторял
# допущение реализации, поэтому найти дефект не мог.

def _make_xlsx_inline(path) -> None:
    """Тот же лист, но текст лежит ПРЯМО в ячейках (t="inlineStr")."""
    wb = f'<workbook xmlns="{S_NS}"><sheets><sheet name="Отчёт" sheetId="1"/></sheets></workbook>'
    sheet = f"""<worksheet xmlns="{S_NS}"><sheetData>
 <row r="1"><c r="A1" t="inlineStr"><is><t>месяц</t></is></c>
            <c r="B1" t="inlineStr"><is><t>выручка</t></is></c></row>
 <row r="2"><c r="A2" t="inlineStr"><is><t>январь</t></is></c>
            <c r="B2"><v>1200</v></c></row>
</sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def test_xlsx_inline_strings_are_extracted_not_silently_dropped(tmp_path):
    """Текст, лежащий прямо в ячейке, обязан доехать.

    Молчаливая потеря хуже отказа: отказ видно, а пустую таблицу владелец
    примет за пустой файл.
    """
    p = tmp_path / "inline.xlsx"
    _make_xlsx_inline(p)
    sec = parse_file(p).sections[0]
    assert sec.table[0] == ("месяц", "выручка"), sec.table
    assert sec.table[1] == ("январь", "1200"), sec.table


def test_xlsx_shared_strings_still_work_after_the_inline_fix(tmp_path):
    """Обратный контроль: починка инлайновых строк не имеет права сломать
    разделяемые. Оба способа законны и встречаются в одном и том же файле."""
    p = tmp_path / "shared.xlsx"
    _make_xlsx(p)
    sec = parse_file(p).sections[0]
    assert sec.table[0] == ("месяц", "выручка")
    assert sec.table[1] == ("1", "1200")


def test_a_real_writer_produces_the_shape_that_was_broken(tmp_path):
    """Holdout: файл пишет НАСТОЯЩИЙ openpyxl, а не наша рукописная фикстура.

    Рукописный xml кодирует допущения того, кто его писал. Дефект и дожил
    потому, что фикстура и реализация сходились на одном способе. Настоящий
    сторонний писатель — единственная проверка, что мы понимаем формат, а не
    свой собственный диалект.
    """
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "real.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "МАРКЕР-ИЗВЛЕЧЕНИЯ"
    ws["B2"] = "вторая ячейка"
    wb.save(p)
    text = render_compact(parse_file(p))
    assert "МАРКЕР-ИЗВЛЕЧЕНИЯ" in text, text[:300]
    assert "вторая ячейка" in text, text[:300]
