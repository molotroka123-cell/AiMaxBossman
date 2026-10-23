"""Chat vision: images and scanned PDFs attached in the chat become text the chat model can read.

Owner 23.10 (class-1 red): «Gemma не видит изображения». Root cause: the chat prompt carried only
«Вложение …: image, путь … (содержимое не встроено)» and the local Gemma 4 build in ollama has no vision
projector. Fix without touching the engine: a LOCAL vision model (ollama, loopback only, never cloud)
describes every attached picture and transcribes its visible text; the result is cached next to the
attachment (`_vision.txt`) and embedded in the prompt as DATA with an honest label. A failure is written
as `_vision.err` and shown to the model as «не распознано: <причина>» — never a silent miss.

Env: BOSSMAN_CHAT_VISION_MODEL (default bossman-fast-qwen36-vision:latest), BOSSMAN_OLLAMA_URL (loopback).
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

VISION_FILE = "_vision.txt"
VISION_ERR = "_vision.err"
DEFAULT_MODEL = "bossman-fast-qwen36-vision:latest"
MAX_SIDE = 1568
MAX_PDF_PAGES = 8
WAIT_S = 150.0
PROMPT = ("Опиши это изображение подробно и по делу для ассистента, который сам его не видит: что изображено, "
          "люди/объекты, композиция, цвета, стиль, фон, детали, важные для переделки или генерации похожего. "
          "Если на изображении есть текст — перепиши его дословно в разделе «Текст:». Не выдумывай того, чего нет.")
_lock = threading.Lock()
_running: set[str] = set()


def model() -> str:
    return os.environ.get("BOSSMAN_CHAT_VISION_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ollama_url() -> str:
    url = os.environ.get("BOSSMAN_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    if (urlsplit(url).hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("vision model must be local (loopback): owner photos never leave the PC")
    return url


def _jpeg(data: bytes) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def pdf_page_images(path: Path, max_pages: int = MAX_PDF_PAGES) -> list[bytes]:
    """Scanned PDFs keep each page as an embedded image: take the largest image of each page."""
    import pypdf
    out: list[bytes] = []
    for page in pypdf.PdfReader(str(path)).pages[:max_pages]:
        imgs = list(getattr(page, "images", []) or [])
        if imgs:
            out.append(max(imgs, key=lambda i: len(i.data)).data)
    return out


def describe(images: list[bytes], *, timeout: float = 600.0, post=None) -> str:
    """One local vision call per image; returns the joined descriptions (page/image numbered when > 1)."""
    post = post or _post
    parts = []
    for n, raw in enumerate(images, 1):
        body = {"model": model(), "prompt": PROMPT, "stream": False, "keep_alive": "10m",
                "images": [base64.b64encode(_jpeg(raw)).decode()], "options": {"temperature": 0.1, "num_predict": 900}}
        text = str(post(ollama_url() + "/api/generate", body, timeout).get("response") or "").strip()
        if not text:
            raise RuntimeError("vision-модель вернула пустой ответ")
        parts.append(text if len(images) == 1 else f"[страница/изображение {n}]\n{text}")
    return "\n\n".join(parts)


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback only (ollama_url)
        return json.load(resp)


def _images_for(path: Path, kind: str) -> list[bytes]:
    return pdf_page_images(path) if kind == "scan" else [path.read_bytes()]


def enabled() -> bool:
    """BOSSMAN_CHAT_VISION=off disables the model call (the test suite sets it so no test touches ollama)."""
    return os.environ.get("BOSSMAN_CHAT_VISION", "on").strip().lower() not in ("off", "0", "false", "no")


def run(path: Path, kind: str, *, post=None) -> str:
    """Describe now (blocking) and cache; failures are cached too so the prompt can say why."""
    folder = path.parent
    try:
        if post is None and not enabled():
            raise RuntimeError("распознавание изображений выключено (BOSSMAN_CHAT_VISION=off)")
        images = _images_for(path, kind)
        if not images:
            raise RuntimeError("в PDF нет ни текста, ни изображений страниц")
        text = describe(images, post=post)
    except Exception as exc:  # noqa: BLE001 - the reason goes to the model and the owner, not a traceback
        (folder / VISION_ERR).write_text(f"{type(exc).__name__}: {str(exc)[:300]}", encoding="utf-8")
        raise
    (folder / VISION_FILE).write_text(text, encoding="utf-8")
    (folder / VISION_ERR).unlink(missing_ok=True)
    return text


def start_background(path: Path, kind: str) -> None:
    """Called right after upload so the description is usually ready before the owner presses «send»."""
    key = str(path)
    with _lock:
        if key in _running:
            return
        _running.add(key)

    def _job() -> None:
        try:
            run(path, kind)
        except Exception:  # noqa: BLE001 - recorded in _vision.err by run()
            pass
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=_job, name=f"chat-vision-{path.parent.name}", daemon=True).start()


def text_for(path: Path, kind: str, *, wait_s: float = WAIT_S, post=None) -> tuple[str | None, str | None]:
    """(description, error). Waits for a running background job, else describes inline."""
    folder = path.parent
    deadline = time.monotonic() + wait_s
    while True:
        done = folder / VISION_FILE
        if done.is_file():
            return done.read_text(encoding="utf-8", errors="replace"), None
        with _lock:
            busy = str(path) in _running
        if not busy:
            break
        if time.monotonic() > deadline:
            return None, f"распознавание не успело за {int(wait_s)} с"
        time.sleep(1.0)
    try:
        return run(path, kind, post=post), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"
