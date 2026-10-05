from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from bcc.pit.master_parser.documents import (
    MAX_OFFICE_TOTAL_BYTES,
    _validate_office_archive,
    build_docling_parser,
)


def install_fake_docling(monkeypatch, converters):
    docling = ModuleType("docling")
    datamodel = ModuleType("docling.datamodel")
    base_models = ModuleType("docling.datamodel.base_models")
    pipeline_options = ModuleType("docling.datamodel.pipeline_options")
    converter_module = ModuleType("docling.document_converter")

    class InputFormat:
        PDF, DOCX, PPTX, XLSX = "pdf", "docx", "pptx", "xlsx"

    class PdfPipelineOptions:
        def __init__(self, *, artifacts_path):
            self.artifacts_path = artifacts_path

    class PdfFormatOption:
        def __init__(self, *, pipeline_options):
            self.pipeline_options = pipeline_options

    class DocumentConverter:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.calls = []
            self.init_offline_flags = (os.environ.get("HF_HUB_OFFLINE"),
                                       os.environ.get("TRANSFORMERS_OFFLINE"))
            converters.append(self)

        def convert(self, path, **kwargs):
            self.calls.append((Path(path), kwargs, os.environ.get("HF_HUB_OFFLINE"),
                               os.environ.get("TRANSFORMERS_OFFLINE")))
            return SimpleNamespace(document=SimpleNamespace(
                export_to_markdown=lambda: "parsed document"))

    base_models.InputFormat = InputFormat
    pipeline_options.PdfPipelineOptions = PdfPipelineOptions
    converter_module.DocumentConverter = DocumentConverter
    converter_module.PdfFormatOption = PdfFormatOption
    for name, module in {
        "docling": docling,
        "docling.datamodel": datamodel,
        "docling.datamodel.base_models": base_models,
        "docling.datamodel.pipeline_options": pipeline_options,
        "docling.document_converter": converter_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_office_conversion_does_not_require_pdf_models_or_change_offline_flags(monkeypatch, tmp_path):
    converters = []
    install_fake_docling(monkeypatch, converters)
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    parser = build_docling_parser()
    with zipfile.ZipFile(tmp_path / "brief.docx", "w") as archive:
        archive.writestr("word/document.xml", "<document />")

    assert parser(tmp_path / "brief.docx") == "parsed document"
    assert len(converters) == 1
    assert converters[0].options["allowed_formats"] == ["docx", "pptx", "xlsx"]
    assert converters[0].calls[0][1] == {"max_file_size": 32 * 1024 * 1024}
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_pdf_requires_configured_local_artifacts_before_converter_creation(monkeypatch, tmp_path):
    converters = []
    install_fake_docling(monkeypatch, converters)
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    parser = build_docling_parser()

    with pytest.raises(RuntimeError, match="local Docling PDF model artifacts"):
        parser(tmp_path / "scan.pdf")
    assert len(converters) == 1  # only the model-free Office converter exists


def test_pdf_conversion_forces_offline_mode_and_restores_process_environment(monkeypatch, tmp_path):
    converters = []
    install_fake_docling(monkeypatch, converters)
    artifacts = tmp_path / "models"
    artifacts.mkdir()
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(artifacts))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    parser = build_docling_parser()

    assert parser(tmp_path / "scan.pdf") == "parsed document"
    assert len(converters) == 2
    assert converters[1].init_offline_flags == ("1", "1")
    assert converters[1].calls[0][2:] == ("1", "1")
    assert os.environ["HF_HUB_OFFLINE"] == "0"
    assert "TRANSFORMERS_OFFLINE" not in os.environ


def test_office_zip_is_checked_for_traversal_and_valid_package_opens(tmp_path):
    safe = tmp_path / "safe.docx"
    with zipfile.ZipFile(safe, "w") as archive:
        archive.writestr("word/document.xml", "<document>hello</document>")
    _validate_office_archive(safe)

    unsafe = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../outside.txt", "must not be extracted")
    with pytest.raises(RuntimeError, match="unsafe entry"):
        _validate_office_archive(unsafe)


def test_office_zip_expansion_limit_is_checked_before_docling(monkeypatch, tmp_path):
    infos = [
        SimpleNamespace(filename=f"word/document-{i}.xml", flag_bits=0,
                         file_size=MAX_OFFICE_TOTAL_BYTES // 2)
        for i in range(2)
    ]
    infos.append(SimpleNamespace(filename="word/document-extra.xml", flag_bits=0,
                                 file_size=1))

    class FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def infolist(self):
            return infos

    monkeypatch.setattr("bcc.pit.master_parser.documents.zipfile.ZipFile", FakeArchive)
    with pytest.raises(RuntimeError, match="expands beyond"):
        _validate_office_archive(tmp_path / "oversized.docx")
