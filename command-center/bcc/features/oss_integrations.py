"""Owner-facing optional engine inventory and bounded local speech upload."""
from __future__ import annotations

import asyncio
import importlib.util

from fastapi import APIRouter, Body, HTTPException, Request

from ..oss import blender_mcp, fincept_terminal, uitars, vibe_trading, whisper
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
    blender_st = blender_mcp.status()
    fincept_st = fincept_terminal.status()
    vibe_st = vibe_trading.status()
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
    community_integrations = [
        dict(
            id="blender_mcp",
            name="MCP for Blender (mcp-for-blender)",
            page="studio",
            license=blender_st["license"],
            state=blender_st["status"],
            activation_status="NOT_ACTIVATED",
            enabled=False,
            detail="Статус: NOT_ACTIVATED (внешний аддон не активирован по умолчанию). Управление 3D-сценой Blender (v2.0.3 MIT) с AST-защитой скриптов bpy (только loopback 127.0.0.1).",
            upstream=blender_st["upstream"],
        ),
        dict(
            id="fincept_terminal",
            name="Fincept Terminal (AGPL-3.0 Research)",
            page="market",
            license=fincept_st["license"],
            state=fincept_st["status"],
            activation_status="NOT_ACTIVATED",
            enabled=False,
            detail="Статус: NOT_ACTIVATED (внешний пакет не активирован по умолчанию; лицензия AGPL-3.0 распространяется на использующий его сценарий). Платные Enterprise-тарифы и живой брокер заблокированы T3.",
            upstream=fincept_st["upstream"],
        ),
        dict(
            id="vibe_trading",
            name="Vibe-Trading (Shadow Account PAPER_ONLY)",
            page="market",
            license=vibe_st["license"],
            state=vibe_st["status"],
            activation_status="NOT_ACTIVATED",
            enabled=False,
            detail="Статус: NOT_ACTIVATED (внешний агент не активирован по умолчанию). Персональный торговый агент (v0.1.15 MIT) строго в режиме Shadow Account (PAPER_ONLY, real_money=0, фильтр S4_regime_trend). Живые ордера заблокированы T3.",
            upstream=vibe_st["upstream"],
        ),
    ]
    return {
        "items": cards,
        "community_integrations": community_integrations,
        "inference_verified": False,
        "speech": speech,
    }


@router.post("/blender/scene")
async def blender_scene(body: dict = Body(default_factory=dict)) -> dict:
    prompt = str(body.get("prompt") or "").strip()
    preset = str(body.get("preset") or "studio_product").strip()
    custom_script = body.get("bpy_script")
    if custom_script is not None:
        ok, issues = blender_mcp.validate_bpy_script(str(custom_script))
        if not ok:
            raise HTTPException(403, f"T3 DENY: небезопасный скрипт bpy заблокирован: {', '.join(issues)}")
    try:
        return blender_mcp.build_scene_plan(prompt, preset=preset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/fincept/analyze")
async def fincept_analyze(body: dict = Body(default_factory=dict)) -> dict:
    import math
    symbol = str(body.get("symbol") or "SOL/USDT").strip()
    raw_closes = body.get("closes")
    is_demo = not isinstance(raw_closes, list)
    closes = raw_closes if isinstance(raw_closes, list) else [142.0, 142.3, 141.9, 142.1, 142.0, 142.2]
    edition = str(body.get("edition") or fincept_terminal.ALLOWED_EDITION)
    action = str(body.get("action") or "research")
    try:
        raw_fee = body.get("fee_bps_rt")
        fee_bps_rt = 28.0 if raw_fee is None else float(raw_fee)
        if isinstance(raw_fee, bool) or not math.isfinite(fee_bps_rt) or fee_bps_rt < 0.0:
            raise ValueError("fee_bps_rt должен быть конечным неотрицательным числом")
        res = fincept_terminal.analyze_series(
            symbol,
            closes,
            fee_bps_rt=fee_bps_rt,
            edition=edition,
            action=action,
        )
        res["data_source"] = "synthetic_demo" if is_demo else "caller_provided"
        res["is_demo"] = is_demo
        return res
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/vibe-trading/shadow")
async def vibe_trading_shadow(body: dict = Body(default_factory=dict)) -> dict:
    import math
    symbol = str(body.get("symbol") or "SOL/USDT").strip()
    raw_closes = body.get("closes")
    is_demo = not isinstance(raw_closes, list)
    closes = raw_closes if isinstance(raw_closes, list) else [142.0, 142.3, 141.9, 142.1, 142.0, 142.2]
    mode = str(body.get("mode") or "shadow")
    live_api_key = str(body.get("live_api_key") or "")
    try:
        raw_rm = body.get("real_money")
        raw_eq = body.get("equity_usd")
        raw_fee = body.get("fee_bps_rt")
        if isinstance(raw_rm, bool) or isinstance(raw_eq, bool) or isinstance(raw_fee, bool):
            raise ValueError("Числовые параметры не могут быть boolean")
        real_money = 0.0 if raw_rm is None else float(raw_rm)
        equity_usd = 100.0 if raw_eq is None else float(raw_eq)
        fee_bps_rt = 28.0 if raw_fee is None else float(raw_fee)
        if not (math.isfinite(real_money) and math.isfinite(equity_usd) and math.isfinite(fee_bps_rt)):
            raise ValueError("Числовые параметры должны быть конечными (finite)")
        if equity_usd <= 0.0 or fee_bps_rt < 0.0:
            raise ValueError("equity_usd > 0 и fee_bps_rt >= 0 обязательны")
        res = vibe_trading.simulate_shadow_step(
            symbol,
            closes,
            equity_usd=equity_usd,
            fee_bps_rt=fee_bps_rt,
            mode=mode,
            real_money=real_money,
            live_api_key=live_api_key,
        )
        res["data_source"] = "synthetic_demo" if is_demo else "caller_provided"
        res["is_demo"] = is_demo
        return res
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


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

