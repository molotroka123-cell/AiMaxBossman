"""Local Xiaomi-OCR-0 (0.8B VLM, OmniDocBench 96.83) adapter for Bossman.

Runs 100% offline against a loopback endpoint (Ollama `longwayxu/xiaomi-ocr-0:bf16`
at `http://127.0.0.1:11434/v1` or local SGLang/vLLM) and delegates Markdown/OTSL/LaTeX
postprocessing to the official `Xiaomi-OCR-0` repository at
`C:\\Users\\asd\\Bossman\\tools-local\\Xiaomi-OCR-0`.

Capabilities:
  1. `ocr_document` — Full-page or region document OCR -> structured Markdown + HTML tables + LaTeX formulas.
  2. `extract_kie` — Key Information Extraction (receipts, invoices, game UI screens, monetization tables) -> JSON.
  3. `ask_vqa` — OCR-centric Visual Question Answering on documents, slides, and charts.
  4. `ocr_pdf_document` — Multi-page PDF parsing into structured Markdown.
  5. `extract_monetization_signals` — Extracts structured game/platform monetization mechanics from UI screens or video frames.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_XIAOMI_REPO = Path(
    os.environ.get("XIAOMI_OCR_REPO", r"C:\Users\asd\Bossman\tools-local\Xiaomi-OCR-0")
)
DEFAULT_LOCAL_URL = os.environ.get("XIAOMI_OCR_LOCAL_URL", "http://127.0.0.1:11434/v1").strip().rstrip("/")
DEFAULT_MODEL = os.environ.get("XIAOMI_OCR_MODEL", "longwayxu/xiaomi-ocr-0:bf16").strip()
DEFAULT_MAX_TOKENS = int(os.environ.get("XIAOMI_OCR_MAX_TOKENS", "4096"))

PROMPTS = {
    "document": (
        "Extract all information from the main body of the document image and represent it "
        "in markdown format, ignoring headers and footers. Tables should be expressed in OTSL "
        "format, formulas in the document should be represented using LATEX format, and the "
        "parsing should be organized according to the reading order."
    ),
    "text": "Extract the text in the image.",
    "table": "Parse the table in the image into OTSL.",
    "formula": "Identify the formula in the image and represent it using LATEX format.",
}

MONETIZATION_KIE_FIELDS = [
    "product_or_game_title",
    "monetization_mechanics",
    "entry_fee_or_price",
    "currency_or_token",
    "reward_or_bonus_structure",
    "progression_or_season_pass",
    "retention_hooks",
    "how_it_works_steps",
    "visible_metrics_or_multipliers",
]


def ensure_loopback_url(base_url: str | None = None) -> str:
    """Enforce strictly local inference (127.0.0.1 / localhost / ::1). Cloud endpoints are rejected."""
    url = (base_url or DEFAULT_LOCAL_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"Xiaomi-OCR-0 requires a local loopback URL, got: {url!r}")
    return url


def _load_postprocess():
    """Import official Xiaomi-OCR-0 postprocessors if the local repo is present."""
    if DEFAULT_XIAOMI_REPO.is_dir() and str(DEFAULT_XIAOMI_REPO) not in sys.path:
        sys.path.insert(0, str(DEFAULT_XIAOMI_REPO))
    try:
        from postprocess.document import finalize_document
        from postprocess.otsl import convert_otsl_to_html
        return finalize_document, convert_otsl_to_html
    except ImportError:
        return (lambda text: text), (lambda text: text)


def normalize_image_png(image_source: str | os.PathLike[str] | bytes, max_edge: int = 2048) -> bytes:
    """Load an image from path or bytes, resize to <= 2048px long edge, and encode as RGB PNG."""
    from PIL import Image

    if isinstance(image_source, (bytes, bytearray)):
        raw = bytes(image_source)
    else:
        p = Path(image_source).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Image file not found: {p}")
        raw = p.read_bytes()

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width > max_edge or img.height > max_edge:
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def chat_xiaomi_ocr(
    image_png: bytes,
    prompt: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Send a single-image non-thinking request to the local Xiaomi-OCR-0 server."""
    endpoint = ensure_loopback_url(base_url) + "/chat/completions"
    encoded = base64.b64encode(image_png).decode("ascii")
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_p": 0.8,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback verified above
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Xiaomi-OCR-0 HTTP {exc.code}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Xiaomi-OCR-0 unavailable at {endpoint}: {exc.reason}") from exc

    msg = data["choices"][0]["message"]["content"]
    if isinstance(msg, list):
        msg = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in msg)
    text = str(msg or "").strip()
    if not text:
        raise RuntimeError("Xiaomi-OCR-0 returned an empty response")
    return {"text": text, "usage": data.get("usage") or {}, "model": model or DEFAULT_MODEL}


def ocr_document(
    image_source: str | os.PathLike[str] | bytes,
    *,
    prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Parse a document/slide/UI image into structured Markdown (tables -> HTML, formulas -> LaTeX)."""
    png = normalize_image_png(image_source)
    finalize_document, _ = _load_postprocess()
    res = chat_xiaomi_ocr(
        png,
        prompt or PROMPTS["document"],
        max_tokens=max_tokens,
        base_url=base_url,
        model=model,
    )
    markdown = finalize_document(res["text"])
    return {
        "markdown": markdown,
        "raw_text": res["text"],
        "usage": res["usage"],
        "model": res["model"],
    }


def build_kie_prompt(fields: list[str] | None = None) -> str:
    if not fields:
        return (
            "Extract the key information from the document image as a JSON object. "
            "Use concise field names. Return JSON only; use an empty string for missing values."
        )
    schema = {str(field): "" for field in fields}
    return (
        "Extract key information in the image\n\n"
        "Please output the key information in JSON format according to the following schema:\n"
        + json.dumps(schema, ensure_ascii=False, indent=4)
    )


def extract_kie(
    image_source: str | os.PathLike[str] | bytes,
    *,
    fields: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Extract structured JSON key-value fields from receipts, invoices, tables, or UI screens."""
    png = normalize_image_png(image_source)
    res = chat_xiaomi_ocr(
        png,
        build_kie_prompt(fields),
        max_tokens=max_tokens,
        base_url=base_url,
        model=model,
    )
    raw = res["text"]
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {"json": None, "raw": raw, "warning": "No JSON object found in model output"}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"json": None, "raw": raw, "warning": "Invalid JSON in model output"}
    if fields and isinstance(parsed, dict):
        parsed = {field: parsed.get(field, "") for field in fields}
    return {"json": parsed, "raw": raw, "usage": res["usage"], "model": res["model"]}


def ask_vqa(
    image_source: str | os.PathLike[str] | bytes,
    question: str,
    *,
    concise: bool = True,
    max_tokens: int = 1024,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Answer a question about a document, slide, chart, or game UI image."""
    q = (question or "").strip()
    if not q:
        raise ValueError("question must not be empty")
    png = normalize_image_png(image_source)
    full_prompt = f"{q}\nAnswer the question using a single word or phrase." if concise else q
    res = chat_xiaomi_ocr(
        png,
        full_prompt,
        max_tokens=max_tokens,
        base_url=base_url,
        model=model,
    )
    return {"question": q, "answer": res["text"], "usage": res["usage"], "model": res["model"]}


def ocr_pdf_document(
    pdf_path: str | os.PathLike[str],
    *,
    max_pages: int = 10,
    dpi: int = 150,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Render PDF pages with pypdfium2 and run Xiaomi-OCR-0 on each page."""
    import pypdfium2 as pdfium
    from contextlib import closing
    from PIL import Image

    p = Path(pdf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"PDF not found: {p}")
    raw = p.read_bytes()
    if not raw.startswith(b"%PDF"):
        raise ValueError(f"Not a valid PDF file: {p}")

    page_markdowns: list[str] = []
    with pdfium.PdfDocument(raw) as doc:
        count = min(len(doc), max(1, max_pages))
        for i in range(count):
            with closing(doc[i]) as page:
                bitmap = page.render(scale=dpi / 72.0)
                try:
                    pil_img = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            if pil_img.width > 2048 or pil_img.height > 2048:
                pil_img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            page_res = ocr_document(
                buf.getvalue(),
                max_tokens=max_tokens,
                base_url=base_url,
                model=model,
            )
            page_markdowns.append(f"<!-- page {i + 1} -->\n\n{page_res['markdown']}")

    return {
        "pdf_path": str(p),
        "pages": len(page_markdowns),
        "markdown": "\n\n---\n\n".join(page_markdowns),
    }


def extract_monetization_signals(
    image_source: str | os.PathLike[str] | bytes,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Combined OCR + KIE + VQA pass to extract monetization & economy mechanics from an image/frame."""
    doc = ocr_document(image_source, base_url=base_url, model=model)
    kie = extract_kie(image_source, fields=MONETIZATION_KIE_FIELDS, base_url=base_url, model=model)
    vqa = ask_vqa(
        image_source,
        "What are the main monetization, entry fee, reward, and player retention mechanics shown in this image?",
        concise=False,
        base_url=base_url,
        model=model,
    )
    return {
        "markdown": doc["markdown"],
        "kie_json": kie["json"],
        "monetization_summary": vqa["answer"],
        "model": doc["model"],
    }
