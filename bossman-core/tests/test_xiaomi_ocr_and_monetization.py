from __future__ import annotations

import io
from pathlib import Path
import pytest
from PIL import Image

from bossman.trading_learning.xiaomi_ocr import (
    build_kie_prompt,
    ensure_loopback_url,
    extract_kie,
    normalize_image_png,
    ocr_document,
)


def test_ensure_loopback_url_allows_local_and_rejects_remote():
    assert ensure_loopback_url("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434/v1"
    assert ensure_loopback_url("http://localhost:8000/v1") == "http://localhost:8000/v1"
    with pytest.raises(ValueError, match="loopback"):
        ensure_loopback_url("https://api.openai.com/v1")
    with pytest.raises(ValueError, match="loopback"):
        ensure_loopback_url("http://192.168.1.50:11434/v1")


def test_normalize_image_png_downscales_to_max_edge():
    img = Image.new("RGB", (3000, 1500), color=(255, 128, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    out_bytes = normalize_image_png(buf.getvalue(), max_edge=2048)
    out_img = Image.open(io.BytesIO(out_bytes))
    assert out_img.format == "PNG"
    assert out_img.width == 2048
    assert out_img.height == 1024


def test_build_kie_prompt_schema():
    p = build_kie_prompt(["entry_fee", "multiplier"])
    assert '"entry_fee": ""' in p
    assert '"multiplier": ""' in p


def test_live_xiaomi_ocr_receipt_and_fox_flight():
    receipt = Path(r"C:\Users\asd\Bossman\tools-local\Xiaomi-OCR-0\example_pics\receipt.jpg")
    fox_ui = Path(r"C:\Users\asd\Bossman\Projects\swapme-roblox\assets\references\fox-city\02-fox-flight-ui.png")
    if not receipt.is_file() or not fox_ui.is_file():
        pytest.skip("Sample images not present")

    kie_res = extract_kie(receipt, fields=["title", "totalprice"])
    assert isinstance(kie_res.get("json"), dict)
    assert "5.90" in str(kie_res["json"].get("totalprice", ""))

    ocr_res = ocr_document(fox_ui)
    assert "49" in ocr_res["markdown"]
    assert "FOX" in ocr_res["markdown"].upper() or "LIVE PLAYERS" in ocr_res["markdown"].upper()
