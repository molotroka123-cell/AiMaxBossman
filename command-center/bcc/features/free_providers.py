"""Бесплатные облачные провайдеры для разгрузки OpenRouter: NVIDIA NIM, Groq, Google AI Studio.

Все — OpenAI-совместимые API с бесплатным (rate-limited) уровнем. Подключение
одной ручкой: ключ проверяется чтением каталога (GET /models, без инференса),
только потом шифруется в vault; чат-модели каталога регистрируются с ценой
0/0 и пометкой caps.free_tier — это явное заявление «бесплатный уровень
провайдера», а не догадка по неизвестной цене. Ключ не возвращается и не
попадает в события; наружу — только маска.

Бесплатность — условие аккаунта: у Groq с привязанной картой (Developer tier)
запросы платные. Владелец подключает сюда только аккаунт без биллинга.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import providers as providers_t
from ..providers import ProviderError, build_adapter
from ..secrets import mask
from . import Feature

router = APIRouter()


@dataclass(frozen=True)
class Preset:
    name: str
    title: str
    base_url: str
    key_prefix: str
    signup_url: str
    free_terms: str
    allow: str | None = None   # regex: только модели бесплатного уровня (остальные платные)


PRESETS: dict[str, Preset] = {
    "nvidia_nim": Preset(
        "nvidia_nim", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "nvapi-",
        "https://build.nvidia.com/explore/discover",
        "бесплатный доступ разработчика build.nvidia.com, лимит запросов в минуту"),
    "groq": Preset(
        "groq", "Groq", "https://api.groq.com/openai/v1", "gsk_",
        "https://console.groq.com/keys",
        "Free plan без привязанной карты, лимиты запросов/токенов в минуту и в день"),
    "google_ai_studio": Preset(
        "google_ai_studio", "Google AI Studio", "https://generativelanguage.googleapis.com/v1beta/openai",
        "", "https://aistudio.google.com/apikey",
        "free tier Gemini API для проекта без биллинга: только flash/flash-lite/gemma, лимиты RPM/RPD",
        allow=r"^(models/)?(gemini-[0-9.]+-flash(-lite)?|gemini-flash(-lite)?-latest|gemma-[0-9][\w.-]*-it)$"),
}

# Не чат-модели каталога: эмбеддинги, реранкеры, модерация, речь, reward.
_NON_CHAT = ("embed", "rerank", "safety", "guard", "reward", "clip", "parse",
             "whisper", "tts", "orpheus", "playai", "distil-whisper", "retriever",
             "nemoretriever", "ocr", "detector", "vila", "neva", "kosmos", "deplot",
             "paligemma", "fuyu")
DEFAULT_CONTEXT = 32768


def is_chat_model(model_id: str, preset: Preset | None = None) -> bool:
    low = model_id.lower()
    if preset is not None and preset.allow:
        return re.match(preset.allow, low) is not None
    return not any(part in low for part in _NON_CHAT)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class ConnectIn(BaseModel):
    api_key: str


def _preset_or_404(name: str) -> Preset:
    preset = PRESETS.get(name)
    if preset is None:
        raise HTTPException(404, {"message": f"неизвестный провайдер «{name}»",
                                  "hint": "доступны: " + ", ".join(PRESETS)})
    return preset


async def _find_provider(svc, preset: Preset) -> dict | None:
    for row in await svc.registry.list_providers():
        if _host(row.get("base_url") or "") == _host(preset.base_url):
            return row
    return None


@router.get("/free-providers")
async def list_presets(request: Request):
    """Пресеты и состояние: подключён ли, сколько моделей зарегистрировано."""
    svc = request.app.state.svc
    models = await svc.registry.list_models()
    out = []
    for preset in PRESETS.values():
        row = await _find_provider(svc, preset)
        pid = row.get("id") if row else None
        out.append({"name": preset.name, "title": preset.title, "base_url": preset.base_url,
                    "signup_url": preset.signup_url, "free_terms": preset.free_terms,
                    "provider_id": pid, "has_key": bool(row and row.get("api_key_masked")),
                    "key": (row or {}).get("api_key_masked"),
                    "models_registered": sum(1 for m in models if pid is not None
                                             and m.get("provider_id") == pid)})
    return {"providers": out}


@router.post("/free-providers/{name}/connect")
async def connect(name: str, body: ConnectIn, request: Request):
    """Проверить ключ каталогом, сохранить в vault, зарегистрировать чат-модели по 0/0."""
    svc = request.app.state.svc
    preset = _preset_or_404(name)
    key = body.api_key.strip()
    if not key or any(ch in key for ch in "\r\n\t "):
        raise HTTPException(422, {"message": "пустой или многострочный ключ"})
    try:
        remote = await build_adapter("openai_compat", preset.base_url, key).list_models()
    except ProviderError as exc:
        if exc.kind == "network":
            # Сеть недоступна: про сам ключ ничего не известно, «не принял ключ» было бы неправдой.
            raise HTTPException(502, {"message": f"Не удалось связаться с {preset.title}: {exc}. Ключ не проверен.",
                                      "hint": "проверьте интернет и повторите; ключ берётся на " + preset.signup_url}) from None
        raise HTTPException(400, {"message": f"{preset.title} не принял ключ: {exc}",
                                  "hint": f"ключ берётся на {preset.signup_url}"}) from None
    row = await _find_provider(svc, preset)
    created = row is None
    if created:
        row = await svc.registry.create_provider(preset.title, "openai_compat", preset.base_url, key)
    else:
        async with svc.db.session() as s:
            await s.execute(sa.update(providers_t).where(providers_t.c.id == int(row["id"]))
                            .values(api_key_enc=svc.vault.encrypt(key)))
            await s.commit()
    provider_id = int(row["id"])
    await svc.bus.emit("provider.key_updated", id=provider_id)

    existing = await svc.registry.list_models()
    taken = {m.get("alias") for m in existing}
    registered = {m.get("name") for m in existing if m.get("provider_id") == provider_id}
    chat = [mid for mid in remote if is_chat_model(mid, preset)]
    added = []
    for mid in chat:
        if mid in registered:
            continue
        alias = mid if mid not in taken else f"{preset.name}:{mid}"
        if alias in taken:
            continue
        await svc.registry.create_model(
            provider_id=provider_id, name=mid, alias=alias, kind="cloud",
            context_window=DEFAULT_CONTEXT, price_in=0.0, price_out=0.0,
            caps={"tools": True, "coding": True, "free_tier": preset.name})
        taken.add(alias)
        added.append(alias)
    await svc.bus.emit("free_provider.connected", provider=preset.name,
                       provider_id=provider_id, models_added=len(added))
    return {"ok": True, "provider": preset.name, "provider_id": provider_id, "created": created,
            "key": mask(key), "models_available": len(chat), "models_added": added,
            "free_terms": preset.free_terms}


FEATURE = Feature(name="free_providers", router=router)
