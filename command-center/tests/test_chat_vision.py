"""Chat attachments must reach the model as content (owner 23.10, class-1): images, scanned PDFs, xlsx, pptx.

Synthetic files only; the local vision model is replaced by a fake `post`. The live proof against the real
local model is tools/prove_chat_attachments.py.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from bcc.features import chat_threads as ct
from bcc.features import chat_vision as cv

CODE = "BOSSMAN 7319"


def _png(tmp_path: Path, name: str = "pic.png") -> Path:
    img = Image.new("RGB", (480, 240), "white")
    ImageDraw.Draw(img).text((40, 100), CODE, fill="black")
    folder = tmp_path / "att1"
    folder.mkdir()
    p = folder / name
    img.save(p)
    return p


def _fake_post(answer: str = f"Белый фон, чёрная надпись. Текст: {CODE}"):
    calls = []

    def post(url, body, timeout):
        calls.append((url, body))
        return {"response": answer}
    return post, calls


def test_image_is_classified_as_image(tmp_path):
    p = _png(tmp_path)
    assert ct._classify(p, p.name, None)[0] == "image"


def test_vision_text_is_cached_and_sent_to_a_loopback_model(tmp_path):
    p = _png(tmp_path)
    post, calls = _fake_post()
    desc, err = cv.text_for(p, "image", wait_s=0, post=post)
    assert err is None and CODE in desc
    assert (p.parent / cv.VISION_FILE).read_text(encoding="utf-8") == desc
    url, body = calls[0]
    assert url.startswith("http://127.0.0.1") and body["images"] and body["model"] == cv.model()
    again, _ = cv.text_for(p, "image", wait_s=0, post=_fake_post("other")[0])
    assert again == desc, "second read must use the cache, not call the model again"


def test_prompt_block_embeds_the_description_not_just_a_path(tmp_path):
    p = _png(tmp_path)
    cv.text_for(p, "image", wait_s=0, post=_fake_post()[0])
    block = ct._attachment_block_sync([{"name": p.name, "kind": "image", "path": str(p), "size": p.stat().st_size}])
    assert CODE in block and "содержимое не встроено" not in block and cv.model() in block


def test_failure_is_explicit_in_the_prompt(tmp_path):
    p = _png(tmp_path)

    def broken(url, body, timeout):
        raise OSError("connection refused")
    desc, err = cv.text_for(p, "image", wait_s=0, post=broken)
    assert desc is None and "connection refused" in err
    assert (p.parent / cv.VISION_ERR).is_file()


def test_vision_refuses_a_non_local_endpoint(monkeypatch):
    monkeypatch.setenv("BOSSMAN_OLLAMA_URL", "https://api.example.com")
    with pytest.raises(ValueError):
        cv.ollama_url()


def test_scanned_pdf_goes_to_vision_and_text_pdf_does_not(tmp_path):
    folder = tmp_path / "att2"
    folder.mkdir()
    scan = folder / "scan.pdf"
    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((30, 90), CODE, fill="black")
    img.save(scan, "PDF")
    assert ct._classify(scan, scan.name, None)[0] == "scan"
    assert len(cv.pdf_page_images(scan)) == 1


def test_xlsx_and_pptx_are_read_as_text(tmp_path):
    import openpyxl
    folder = tmp_path / "att3"
    folder.mkdir()
    x = folder / "t.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Лист1"
    wb.active.append(["товар", "цена"])
    wb.active.append(["рама", 4200])
    wb.save(x)
    kind, _, chars = ct._classify(x, x.name, None)
    assert kind == "text" and chars and "4200" in (folder / "_extracted.txt").read_text(encoding="utf-8")

    folder2 = tmp_path / "att4"
    folder2.mkdir()
    pptx = folder2 / "s.pptx"
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Слайд про дрон</a:t></p:sld>')
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)
    kind, _, chars = ct._classify(pptx, pptx.name, None)
    assert kind == "text" and "дрон" in (folder2 / "_extracted.txt").read_text(encoding="utf-8")


def test_jpeg_downscale_keeps_long_side_within_limit():
    big = Image.new("RGB", (4000, 3000), "white")
    buf = io.BytesIO()
    big.save(buf, "PNG")
    out = Image.open(io.BytesIO(cv._jpeg(buf.getvalue())))
    assert max(out.size) <= cv.MAX_SIDE
