"""Owner scope, truthful conversion state, limits and real optional extraction."""
from __future__ import annotations

import hashlib
from pathlib import Path
import socket
from types import SimpleNamespace
import zipfile

import pytest

from bcc.file_intelligence.models import Denied
from bcc.file_intelligence.scope import ScopePolicy
from bcc.oss import docling


def _result(text="Owner text", status="success"):
    return SimpleNamespace(status=status, document=SimpleNamespace(
        export_to_markdown=lambda: text, pages={1: object()}))


def test_scoped_snapshot_reaches_converter_and_output_is_bounded(tmp_path, monkeypatch):
    file = tmp_path / "report.pdf"
    file.write_bytes(b"%PDF-owner-data")
    def convert(content, name, pages):
        assert content == b"%PDF-owner-data"
        assert name == "report.pdf"
        assert pages == 3
        return _result("Owner text too long")
    monkeypatch.setattr(docling, "_convert", convert)
    result = docling.extract_document(str(file), policy=ScopePolicy([tmp_path]), max_pages=3, max_chars=10)
    assert result["markdown"] == "Owner text"
    assert result["truncated"] is True
    assert result["source_sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert result["ocr"] is False
    assert file.read_bytes() == b"%PDF-owner-data"


@pytest.mark.parametrize("text,status,code", [
    ("", "success", "NO_TEXT"), ("partial", "partial_success", "CONVERSION_INCOMPLETE"),
    ("ignored", "failure", "CONVERSION_INCOMPLETE"),
])
def test_empty_or_partial_conversion_is_not_success(tmp_path, monkeypatch, text, status, code):
    file = tmp_path / "report.pdf"; file.write_bytes(b"%PDF")
    monkeypatch.setattr(docling, "_convert", lambda *_: _result(text, status))
    with pytest.raises(docling.DoclingError) as error:
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert error.value.code == code


@pytest.mark.parametrize("path", ["https://example.org/report.pdf", "//server/share/report.pdf", "\\\\server\\share\\report.pdf"])
def test_remote_sources_never_reach_converter(monkeypatch, path):
    monkeypatch.setattr(docling, "_convert", lambda *_: pytest.fail("unscoped conversion"))
    with pytest.raises(docling.DoclingError, match="local file"):
        docling.extract_document(path, policy=ScopePolicy([]))


def test_outside_roots_and_symlink_escape_are_denied(tmp_path, monkeypatch):
    authorized = tmp_path / "allowed"; authorized.mkdir()
    file = tmp_path / "private.pdf"; file.write_bytes(b"%PDF")
    monkeypatch.setattr(docling, "_convert", lambda *_: pytest.fail("unscoped conversion"))
    with pytest.raises(Denied):
        docling.extract_document(str(file), policy=ScopePolicy([authorized]))
    link = authorized / "link.pdf"
    try:
        link.symlink_to(file)
    except OSError:
        return  # Windows owner does not always have symlink creation rights.
    with pytest.raises(Denied):
        docling.extract_document(str(link), policy=ScopePolicy([authorized]))


def test_secret_directory_is_denied_even_in_authorized_root(tmp_path):
    secret = tmp_path / ".ssh"; secret.mkdir()
    file = secret / "private.pdf"; file.write_bytes(b"%PDF")
    with pytest.raises(Denied):
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))


def test_office_decompression_limit_checked_before_conversion(tmp_path, monkeypatch):
    file = tmp_path / "bomb.docx"
    with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.xml", b"0" * 1024)
    monkeypatch.setattr(docling, "MAX_EXPANDED_BYTES", 128)
    monkeypatch.setattr(docling, "_convert", lambda *_: pytest.fail("oversized conversion"))
    with pytest.raises(docling.DoclingError) as error:
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert error.value.code == "DOCUMENT_TOO_LARGE"


def test_file_limit_and_invalid_office_container(tmp_path, monkeypatch):
    file = tmp_path / "bad.docx"; file.write_bytes(b"invalid")
    with pytest.raises(docling.DoclingError) as error:
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert error.value.code == "INVALID_DOCUMENT"
    monkeypatch.setattr(docling, "MAX_FILE_BYTES", 3)
    with pytest.raises(docling.DoclingError) as error:
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert error.value.code == "FILE_TOO_LARGE"


def test_parser_failure_is_redacted_and_lock_released(tmp_path, monkeypatch):
    file = tmp_path / "report.pdf"; file.write_bytes(b"%PDF")
    def fail(*_):
        raise RuntimeError("secret document text")
    monkeypatch.setattr(docling, "_convert", fail)
    with pytest.raises(docling.DoclingError) as error:
        docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert error.value.code == "CONVERSION_FAILED"
    assert "secret" not in str(error.value)
    monkeypatch.setattr(docling, "_convert", lambda *_: _result())
    assert docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))["ok"]


async def test_api_preserves_feature_gate_and_forwards_existing_scope(tmp_path, monkeypatch):
    from bcc import file_intelligence as fi
    from bcc.features import file_intelligence as feature
    policy = ScopePolicy([tmp_path])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=object())))
    body = feature.ExtractRequest(path=str(tmp_path / "report.pdf"))
    monkeypatch.setenv(fi.FLAG_ENV, "0")
    assert (await feature.extract(body, request))["refused"] == "FEATURE_DISABLED"
    monkeypatch.setenv(fi.FLAG_ENV, "1")
    monkeypatch.setattr(feature, "_service", lambda _: SimpleNamespace(policy=policy))
    def convert(path, **kwargs):
        assert kwargs["policy"] is policy
        raise docling.DoclingError("DEPENDENCY_MISSING", "Install documents extra")
    monkeypatch.setattr(docling, "extract_document", convert)
    result = await feature.extract(body, request)
    assert result["ok"] is False
    assert result["refused"] == "DEPENDENCY_MISSING"


def test_real_docling_office_extraction_without_models(tmp_path):
    pytest.importorskip("docling.document_converter")
    word = pytest.importorskip("docx")
    file = tmp_path / "owner.docx"
    document = word.Document()
    document.add_heading("Bossman owner document", 0)
    document.add_paragraph("Prague acceptance text 21 September")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Service"
    table.cell(0, 1).text = "Verified"
    document.save(file)
    result = docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert result["ok"] is True
    assert "Prague acceptance text" in result["markdown"]
    assert "Service" in result["markdown"] and "Verified" in result["markdown"]
    assert result["mode"] == "native_no_models"


def test_real_native_pdf_without_models(tmp_path):
    pytest.importorskip("docling.document_converter")
    # Tiny valid single-page PDF, generated without a second PDF dependency.
    stream = b"BT /F1 18 Tf 40 100 Td (Bossman local PDF proof) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    file = tmp_path / "proof.pdf"; file.write_bytes(data)
    result = docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert "Bossman local PDF proof" in result["markdown"]
    assert result["pages"] == 1
    assert result["ocr"] is False


@pytest.mark.parametrize("extension", [".xlsx", ".pptx", ".csv"])
def test_real_office_and_csv_with_network_denied(tmp_path, monkeypatch, extension):
    pytest.importorskip("docling.document_converter")
    file = tmp_path / ("owner" + extension)
    if extension == ".xlsx":
        spreadsheet = pytest.importorskip("openpyxl").Workbook()
        spreadsheet.active.append(["Bossman", "Verified"])
        spreadsheet.save(file)
    elif extension == ".pptx":
        slides = pytest.importorskip("pptx").Presentation()
        slide = slides.slides.add_slide(slides.slide_layouts[1])
        slide.shapes.title.text = "Bossman Verified"
        slides.save(file)
    else:
        file.write_text("Service,Status\nBossman,Verified\n", encoding="utf-8")
    def no_network(*_):
        pytest.fail("Native document extraction attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    result = docling.extract_document(str(file), policy=ScopePolicy([tmp_path]))
    assert "Bossman" in result["markdown"] and "Verified" in result["markdown"]
