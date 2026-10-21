"""Owner-only API: Jeff's manual («методичка», bcc.pit.jeff_kb) and Jeff's local model choice (bcc.pit.jeff_model).

Mounted under /api with the normal owner session/CSRF or token auth (participants have no Command Center session):
  GET    /jeff-kb                 — manual state: present, name (owner only), size, sections, sha prefix, strict, scope
  PUT    /jeff-kb/manual          — upload / replace: {"name": "x.pdf", "data_b64": "..."} (.txt .md .pdf .docx, <= 8 MB)
  DELETE /jeff-kb/manual          — remove (strict mode goes off)
  GET    /jeff-kb/sections        — numbered section titles (no text)
  PUT    /jeff-kb/mode            — {"strict": bool, "scope_all": bool, "cloud_fallback": bool, "local_timeout_s": int,
                                     "cloud_daily_max": int} (scope_all = every participant, default off;
                                     cloud_fallback = free cloud answers by the manual when the local model is
                                     unavailable, default ON — owner 21.10)
  POST   /jeff-kb/check           — owner test: {"question": "...", "use_manual": true|false} -> answer + cited sections
                                    + provenance: engine local|cloud, provider, model (bcc.pit.jeff_kb_cloud)
  GET    /jeff-model              — configured / chosen / effective local model of Jeff
  PUT    /jeff-model              — {"model": "gemma4-31b-uncensored:latest"}: Jeff answers with it from the next message
  DELETE /jeff-model              — back to the configured model

Nothing here logs manual content, its name, a section title or a question: the store's own audit has counts and hashes.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..pit import jeff_kb as kb
from ..pit import jeff_kb_cloud as kc
from ..pit import jeff_model as jm
from ..pit.config import DEFAULT_LOCAL_URL
from . import Feature
from .jeff_settings import _cc_data_dir, _jeff_data_dir, _pit_config

router = APIRouter()
CHECK_MAX_TOKENS = 900
CHECK_TIMEOUT_S = 180.0


class ManualIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data_b64: str = Field(min_length=1, max_length=(kb.MAX_UPLOAD_BYTES * 4) // 3 + 16)


class ModeIn(BaseModel):
    strict: bool | None = None
    scope_all: bool | None = None
    cloud_fallback: bool | None = None
    local_timeout_s: int | None = Field(default=None, ge=kc.MIN_LOCAL_TIMEOUT_S, le=kc.MAX_LOCAL_TIMEOUT_S)
    cloud_daily_max: int | None = Field(default=None, ge=0, le=kc.MAX_DAILY_MAX)


class CheckIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    use_manual: bool = True


class ModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=120)


def _jeff(request: Request) -> tuple[Path, dict]:
    cc_dir = _cc_data_dir(request)
    cfg = _pit_config(cc_dir)
    return _jeff_data_dir(cc_dir, cfg), cfg


def _configured_models(cfg: dict) -> list[str]:
    env = [m.strip() for m in os.environ.get("BOSSMAN_PIT_LOCAL_MODELS", "").split(",") if m.strip()]
    return env or [m for m in (cfg.get("local_models") or []) if isinstance(m, str)]


def _local_url(cfg: dict) -> str:
    return (os.environ.get("BOSSMAN_PIT_LOCAL_URL", "").strip() or str(cfg.get("local_url") or "").strip()
            or DEFAULT_LOCAL_URL)


def _loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def default_chat_factory(url: str, model: str):
    """Jeff's own local adapters (native Ollama when it is the default Ollama URL). Never a cloud route."""
    from ..pit.ollama_native import LocalOpenAICompatChat, OllamaNativeChatAdapter, is_native_ollama_url
    adapter = OllamaNativeChatAdapter(url) if is_native_ollama_url(url) else LocalOpenAICompatChat(base_url=url)

    async def chat(messages: list[dict]) -> str:
        result = await adapter.chat(model, messages, max_tokens=CHECK_MAX_TOKENS, timeout=CHECK_TIMEOUT_S)
        return str(getattr(result, "text", "") or "")
    return chat


#: Tests replace this with a fake LLM; production uses Jeff's local adapters.
chat_factory = default_chat_factory


def default_cloud_route(svc):
    """The strict-manual cloud fallback goes through Bossman's own registry (governed, free-only, health-ranked)."""
    return kc.RegistryRoute(svc.registry)


cloud_route_factory = default_cloud_route


def _local_provider(url: str) -> str:
    from ..pit.ollama_native import is_native_ollama_url
    return "ollama" if is_native_ollama_url(url) else "local"


def _kb_error(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status, {"message": str(exc)[:200], "code": "JEFF_KB"})


@router.get("/jeff-kb")
async def kb_state(request: Request):
    jeff_dir, cfg = _jeff(request)
    return {**kb.info(jeff_dir), "model": jm.status(jeff_dir, _configured_models(cfg))}


@router.put("/jeff-kb/manual")
async def kb_upload(body: ManualIn, request: Request):
    import asyncio
    jeff_dir, _cfg = _jeff(request)
    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise _kb_error(ValueError("файл повреждён при передаче (base64)")) from None
    try:
        return await asyncio.to_thread(kb.save_manual, jeff_dir, body.name, data)
    except kb.KBError as exc:
        raise _kb_error(exc) from None


@router.delete("/jeff-kb/manual")
async def kb_delete(request: Request):
    jeff_dir, _cfg = _jeff(request)
    removed = kb.delete_manual(jeff_dir)
    return {**kb.info(jeff_dir), "removed": removed}


@router.get("/jeff-kb/sections")
async def kb_sections(request: Request):
    jeff_dir, _cfg = _jeff(request)
    return {"sections": kb.section_list(jeff_dir)}


@router.put("/jeff-kb/mode")
async def kb_mode(body: ModeIn, request: Request):
    jeff_dir, _cfg = _jeff(request)
    try:
        return kb.set_mode(jeff_dir, strict=body.strict, scope_all=body.scope_all,
                           cloud_fallback=body.cloud_fallback, local_timeout_s=body.local_timeout_s,
                           cloud_daily_max=body.cloud_daily_max)
    except kb.KBError as exc:
        raise _kb_error(exc, 409) from None


@router.post("/jeff-kb/check")
async def kb_check(body: CheckIn, request: Request):
    jeff_dir, cfg = _jeff(request)
    manual = None
    if body.use_manual:
        manual = kb.load_manual(jeff_dir)
        if manual is None:
            raise _kb_error(kb.KBError("методичка не загружена или не читается"), 409)
    url = _local_url(cfg)
    if not _loopback(url):
        raise _kb_error(ValueError("у Jeff не локальная модель — проверка не запускается"), 409)
    model = jm.status(jeff_dir, _configured_models(cfg))["effective"]
    local_chat = chat_factory(url, model)
    fallback = None
    state = kb.read_state(jeff_dir)
    if manual is not None and state["cloud_fallback"]:
        # Owner 21.10: the local model first, with a SHORT timeout that decides «unavailable»; then the same strict
        # request to a free cloud model of Bossman's registry. Without the switch nothing here changes.
        limit = kc.local_timeout_s(state)
        unbounded = local_chat

        async def local_chat(messages: list[dict]) -> str:
            return await asyncio.wait_for(unbounded(messages), timeout=limit)

        route = cloud_route_factory(request.app.state.svc)

        async def fallback(messages: list[dict], reason: str) -> kc.CloudOutcome:
            return await kc.ask_cloud(route, messages, data_dir=jeff_dir, state=state, local_reason=reason,
                                      budget_s=kc.CHECK_BUDGET_S, stop_dir=_cc_data_dir(request), surface="check")
    try:
        out = await kb.check_answer(local_chat, body.question, manual=manual, fallback=fallback)
    except kb.KBError as exc:
        raise _kb_error(exc) from None
    except kc.ModelsUnavailable as exc:
        kb.audit(jeff_dir, "check", use_manual=True, engine="none", reason=exc.cloud_reason,
                 local_reason=exc.local_reason)
        raise HTTPException(503, {"message": str(exc), "code": "JEFF_NO_MODELS",
                                  "local_reason": exc.local_reason, "cloud_reason": exc.cloud_reason}) from None
    except Exception:  # noqa: BLE001 - provider text never reaches the phone
        raise HTTPException(502, {"message": "локальная модель Jeff не ответила (Ollama запущена?)",
                                  "code": "JEFF_MODEL_DOWN"}) from None
    if out.get("engine") != "cloud":
        out = {**out, **kc.provenance("local", provider=_local_provider(url), model=model)}
    kb.audit(jeff_dir, "check", use_manual=bool(body.use_manual), cited=len(out.get("cited") or []),
             no_match=bool(out.get("no_match")), engine=str(out.get("engine")), provider=out.get("provider") or "",
             model=out.get("model") or "")
    return {**out, "local_model": model}


@router.get("/jeff-model")
async def model_get(request: Request):
    jeff_dir, cfg = _jeff(request)
    return jm.status(jeff_dir, _configured_models(cfg))


@router.put("/jeff-model")
async def model_put(body: ModelIn, request: Request):
    jeff_dir, cfg = _jeff(request)
    try:
        jm.set_override(jeff_dir, body.model)
    except jm.ModelChoiceError as exc:
        raise HTTPException(422, {"message": str(exc), "code": "JEFF_MODEL"}) from None
    return jm.status(jeff_dir, _configured_models(cfg))


@router.delete("/jeff-model")
async def model_delete(request: Request):
    jeff_dir, cfg = _jeff(request)
    jm.clear_override(jeff_dir)
    return jm.status(jeff_dir, _configured_models(cfg))


FEATURE = Feature(name="jeff_kb", router=router)
