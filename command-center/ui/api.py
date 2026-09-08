"""Command Center API client with Gateway integration.

Connects to Bossman Gateway at /v1/models endpoint
to fetch available models from all providers.
"""
import httpx

GATEWAY_BASE = "http://localhost:8000"


def get_health():
    """Get overall Gateway health."""
    try:
        response = httpx.get(f"{GATEWAY_BASE}/health", timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_provider_health(provider: str):
    """Get health for specific provider."""
    try:
        response = httpx.get(f"{GATEWAY_BASE}/health/{provider}", timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_provider_models(provider: str):
    """Get models from specific provider."""
    try:
        response = httpx.get(f"{GATEWAY_BASE}/v1/models", params={"provider": provider}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return {
            "status": "ok",
            "provider": provider,
            "models": data.get("models", []),
            "count": data.get("count", 0)
        }
    except Exception as e:
        return {"status": "error", "provider": provider, "error": str(e)}


def get_all_models():
    """Get all models from all providers."""
    try:
        response = httpx.get(f"{GATEWAY_BASE}/v1/models", timeout=15.0)
        response.raise_for_status()
        data = response.json()
        return {
            "status": "ok",
            "providers": data.get("providers", []),
            "models_by_provider": data.get("models_by_provider", {}),
            "total_models": data.get("total_models", 0)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def refresh_models():
    """Trigger model cache refresh."""
    try:
        response = httpx.post(f"{GATEWAY_BASE}/gateway/refresh-models", timeout=30.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_tasks():
    try:
        response = httpx.get(f"{GATEWAY_BASE}/api/tasks", timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_active_task():
    try:
        response = httpx.get(f"{GATEWAY_BASE}/api/tasks/active", timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return None


def get_provider_stats():
    """Get stats for all configured providers."""
    from .config import AVAILABLE_PROVIDERS
    stats = {}
    for provider in AVAILABLE_PROVIDERS:
        health = get_provider_health(provider)
        models = get_provider_models(provider)
        stats[provider] = {
            "health": health,
            "models": models,
            "configured": health.get("status") != "error"
        }
    return stats


if __name__ == "__main__":
    print("Gateway Health:", get_health())
    print("All Models:", get_all_models())
