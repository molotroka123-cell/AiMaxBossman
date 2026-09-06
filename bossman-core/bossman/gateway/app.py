"""Bossman Gateway - FastAPI application with auto-model discovery.

When an API key is configured, automatically loads available models
and exposes them via /v1/models endpoint.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict, List
import asyncio

from .config import load_provider_config, AVAILABLE_PROVIDERS
from .backends import get_backend
from .telemetry import record_api_call


app = FastAPI(title="Bossman Gateway", version="3.0")

_model_cache: Dict[str, List[str]] = {}


async def refresh_all_models():
    """Refresh model lists for all configured providers."""
    for provider in AVAILABLE_PROVIDERS:
        config = load_provider_config(provider)
        if config.get("api_key"):
            try:
                backend = get_backend(provider, config["api_key"], config.get("base_url"))
                models = await backend.list_models()
                _model_cache[provider] = models
                print(f"[Gateway] Loaded {len(models)} models from {provider}")
            except Exception as e:
                print(f"[Gateway] Failed to load models from {provider}: {e}")
                _model_cache[provider] = []


@app.on_event("startup")
async def startup_event():
    """Auto-load models on startup."""
    print("[Gateway] Starting up...")
    await refresh_all_models()
    print(f"[Gateway] Model cache: {sum(len(m) for m in _model_cache.values())} total models")


@app.get("/health")
async def health():
    """Overall health check."""
    return {
        "status": "ok",
        "providers_configured": len([p for p in AVAILABLE_PROVIDERS if load_provider_config(p).get("api_key")]),
        "models_cached": sum(len(m) for m in _model_cache.values())
    }


@app.get("/health/{provider}")
async def health_provider(provider: str):
    """Health check for specific provider."""
    if provider not in AVAILABLE_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    
    config = load_provider_config(provider)
    if not config.get("api_key"):
        return {"status": "warn", "message": "No API key configured"}
    
    try:
        backend = get_backend(provider, config["api_key"], config.get("base_url"))
        result = await backend.health_check()
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/v1/models")
async def list_models(provider: str = None):
    """List all available models from all providers or specific provider."""
    await record_api_call("list_models", {"provider": provider})
    
    if provider:
        if provider not in AVAILABLE_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
        
        config = load_provider_config(provider)
        if not config.get("api_key"):
            raise HTTPException(status_code=400, detail=f"No API key for {provider}")
        
        if provider not in _model_cache or not _model_cache[provider]:
            try:
                backend = get_backend(provider, config["api_key"], config.get("base_url"))
                models = await backend.list_models()
                _model_cache[provider] = models
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        return {
            "provider": provider,
            "models": _model_cache[provider],
            "count": len(_model_cache[provider])
        }
    else:
        all_models = {}
        for prov in AVAILABLE_PROVIDERS:
            if prov in _model_cache and _model_cache[prov]:
                all_models[prov] = _model_cache[prov]
        
        return {
            "providers": list(all_models.keys()),
            "models_by_provider": all_models,
            "total_models": sum(len(m) for m in all_models.values())
        }


@app.post("/v1/chat/completions")
async def chat_completions(request: dict):
    """Unified chat completions endpoint."""
    model = request.get("model", "")
    messages = request.get("messages", [])
    
    if not model or not messages:
        raise HTTPException(status_code=400, detail="model and messages required")
    
    provider = None
    model_name = model
    
    if model.startswith("openrouter/"):
        provider = "openrouter"
        model_name = model.replace("openrouter/", "")
    elif model.startswith("glm-") or model.startswith("zai/"):
        provider = "zai"
    elif model.startswith("claude-"):
        provider = "anthropic"
    elif model.startswith("gpt-") or model.startswith("o1"):
        provider = "openai"
    elif model.startswith("gemini-"):
        provider = "google"
    elif model.startswith("llama-") or model.startswith("mixtral"):
        provider = "groq"
    elif model.startswith("mistral-") or model.startswith("codestral"):
        provider = "mistral"
    elif model.startswith("hermes") or model.startswith("llama3"):
        provider = "ollama"
    
    if not provider:
        raise HTTPException(status_code=400, detail=f"Cannot determine provider for model: {model}")
    
    config = load_provider_config(provider)
    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail=f"No API key for {provider}")
    
    try:
        backend = get_backend(provider, config["api_key"], config.get("base_url"))
        result = await backend.chat_completions(model_name, messages, **request)
        await record_api_call("chat_completions", {"provider": provider, "model": model_name})
        return JSONResponse(content=result)
    except Exception as e:
        await record_api_call("chat_completions_error", {"provider": provider, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gateway/refresh-models")
async def refresh_models(provider: str = None):
    """Manually refresh model cache."""
    if provider:
        await refresh_all_models()
        return {"status": "ok", "message": f"Refreshed {provider}"}
    else:
        await refresh_all_models()
        return {"status": "ok", "message": "Refreshed all providers"}
