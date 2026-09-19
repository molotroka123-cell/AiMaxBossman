"""Owner-facing optional engine inventory and bounded local speech upload."""
from __future__ import annotations

import asyncio
import importlib.util

from fastapi import APIRouter, HTTPException, Request

from ..oss import whisper, uitars
from . import Feature

router = APIRouter(prefix="/oss", tags=["oss"])


def _package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@router.get("/status")
async def status() -> dict:
    # Configuration is not an inference or an upstream health test. No network
    # calls, model imports, secrets, or local absolute paths in this inventory.
    speech = whisper.status()
    from ..oss.comfyui import image_configuration
    try:
        comfy_state = "configured" if image_configuration() else "needs_setup"
    except ValueError:
        comfy_state = "invalid_configuration"
    from .web_research import config as web_config
    cards = [
        dict(id="llamacpp", name="llama.cpp", page="models", state="integrated",
             detail="Подключите локальный сервер в разделе моделей и проверьте ответ.",
             upstream="https://github.com/ggml-org/llama.cpp"),
        dict(id="docling", name="Docling", page="file-intelligence",
             state="installed" if _package("docling") else "needs_setup",
             detail="Чтение документов. Нужны пакет documents и разрешённые папки File Intelligence.",
             upstream="https://github.com/docling-project/docling"),
        dict(id="qdrant", name="Qdrant", page=None,
             state="installed" if _package("qdrant_client") else "needs_setup",
             detail="Дополнительный поиск по смыслу: выберите backend=qdrant и локальную модель эмбеддингов.",
             upstream="https://github.com/qdrant/qdrant"),
        dict(id="whisper", name="faster-whisper", page=None,
             state="configured" if speech["status"] == "configured" else "needs_setup",
             detail="Расшифровка WAV на CPU. Нужны пакет speech и заранее установленная модель.",
             upstream="https://github.com/SYSTRAN/faster-whisper"),
        dict(id="searxng", name="SearXNG", page="web_research",
             state="configured" if web_config.SEARXNG_URL else "needs_setup",
             detail="Интернет-поиск через свой сервер с включённой JSON-выдачей.",
             upstream="https://github.com/searxng/searxng"),
        dict(id="comfyui", name="ComfyUI", page="images",
             state=comfy_state,
             detail="Создание изображений. Нужны работающий ComfyUI и установленная модель.",
             upstream="https://github.com/Comfy-Org/ComfyUI"),
        dict(id="uitars", name="UI-TARS", page="control", state=uitars.status()["status"],
             detail="Наведение кликов по экрану. Нужны локальная визуальная модель и разрешения оператора.",
             upstream="https://github.com/bytedance/UI-TARS-desktop"),
        dict(id="grapesjs", name="GrapesJS", page="web_designer", state="integrated",
             detail="Конструктор блоков в существующем редакторе сайтов.",
             upstream="https://github.com/GrapesJS/grapesjs"),
    ]
    return {"items": cards, "inference_verified": False, "speech": speech}


@router.post("/speech/transcribe")
async def transcribe(request: Request, language: str = "auto") -> dict:
    # Streaming limit applies even if Content-Length is absent or forged.
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise HTTPException(400, "Некорректный размер записи")
        if size < 0 or size > whisper.MAX_AUDIO_BYTES:
            raise HTTPException(413, "Максимальный размер записи — 32 МиБ")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > whisper.MAX_AUDIO_BYTES:
            raise HTTPException(413, "Максимальный размер записи — 32 МиБ")
        content.extend(chunk)
    try:
        result = await asyncio.to_thread(whisper.transcribe_audio, bytes(content), language=language)
    except whisper.WhisperError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, **result}


FEATURE = Feature(name="oss_integrations", router=router)
