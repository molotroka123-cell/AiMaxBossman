"""Local passport reading: image on the owner's drive -> local vision model on this PC -> MRZ check digits ->
identity.json next to the image. Nothing is sent off the machine and no value is printed.

    python -m apps.identity_fill.extract_local E:\\passport            (folder or a single image)
    env: BOSSMAN_OCR_URL (default http://127.0.0.1:11434), BOSSMAN_OCR_MODEL (default longwayxu/xiaomi-ocr-0:bf16)

Output on the console: field names, whether the check digits passed, and where the file was written. Values: never.
The model's reading is accepted only if every ICAO check digit matches, so a misread cannot become form data.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from .mrz import MrzError, parse_td3, repair_td3
from .vault import assert_local

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
PROMPT = "Text Recognition:"  # the small local OCR model reads the whole page; the MRZ lines are picked out below
MRZ_LINE = re.compile(r"[A-Z0-9<]{36,56}")  # models miscount the fillers; repair_td3 normalises the length


def read_mrz_lines(image: Path, url: str, model: str) -> tuple[str, str]:
    body = {"model": model, "prompt": PROMPT, "stream": False, "options": {"temperature": 0, "num_predict": 600},
            "images": [base64.b64encode(image.read_bytes()).decode()]}
    req = urllib.request.Request(assert_local(url).rstrip("/") + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    text = json.loads(urllib.request.urlopen(req, timeout=300).read()).get("response", "")
    text = re.sub(r"</?[A-Za-z][A-Za-z0-9_]*>", "\n", text)  # the OCR model wraps its answer in tags like </doc>
    lines = [x for x in MRZ_LINE.findall(text.upper().replace(" ", "")) if "<" in x]
    for i, first in enumerate(lines[:-1]):
        if first.startswith("P"):
            return repair_td3(first, lines[i + 1])
    raise MrzError("the model did not return the two MRZ lines")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    images = [target] if target.is_file() else sorted(p for p in target.iterdir() if p.suffix.lower() in IMAGE_EXT)
    url = os.environ.get("BOSSMAN_OCR_URL", "http://127.0.0.1:11434")
    model = os.environ.get("BOSSMAN_OCR_MODEL", "longwayxu/xiaomi-ocr-0:bf16")
    print(f"images to try: {len(images)} | local model: {model}")
    for i, image in enumerate(images, 1):
        try:
            record = parse_td3(*read_mrz_lines(image, url, model))
        except MrzError as e:
            print(f"  image {i}: not accepted ({e})")
            continue
        out = image.parent / "identity.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  image {i}: all check digits passed; fields written: {sorted(record)}")
        print(f"  saved next to the image as {out.name} (values are not shown here)")
        return 0
    print("no image produced a valid MRZ. Type the fields by hand into identity.json or take a sharper photo.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
