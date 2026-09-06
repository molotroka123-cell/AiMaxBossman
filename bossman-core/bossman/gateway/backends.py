"""Multi-provider LLM backends for Bossman Gateway.

Supports: OpenAI, Anthropic, Z.ai (GLM), OpenRouter, Google, Groq, Mistral, Together, Ollama/Hermes.
Lazy-loaded, no new dependencies beyond httpx (already in bossman-core).
"""
import os
import httpx
from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod


class CircuitOpenError(RuntimeError):
    """Raised when a backend's circuit breaker is open and the attempt is skipped."""


class BaseBackend(ABC):
    """Base class for all LLM backends."""
    
    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._client
    
    @abstractmethod
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        pass
    
    @abstractmethod
    async def list_models(self) -> List[str]:
        pass
    
    async def health_check(self) -> Dict:
        try:
            models = await self.list_models()
            return {"status": "ok", "models_count": len(models)}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class OpenAIBackend(BaseBackend):
    """OpenAI API backend."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.openai.com/v1")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


class AnthropicBackend(BaseBackend):
    """Anthropic API backend."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.anthropic.com")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        system_msg = ""
        new_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                new_messages.append({"role": msg["role"], "content": msg["content"]})
        
        response = await self.client.post(
            "/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": model,
                "system": system_msg,
                "messages": new_messages,
                "max_tokens": kwargs.get("max_tokens", 1024),
                **{k: v for k, v in kwargs.items() if k != "max_tokens"}
            }
        )
        response.raise_for_status()
        data = response.json()
        return {
            "id": data["id"],
            "choices": [{
                "message": {"role": "assistant", "content": data["content"][0]["text"]},
                "finish_reason": data["stop_reason"]
            }],
            "usage": data.get("usage", {})
        }
    
    async def list_models(self) -> List[str]:
        return ["claude-3-5-sonnet-20260620", "claude-3-opus-20260229", "claude-3-haiku-20260307"]


class ZaiBackend(BaseBackend):
    """Z.ai (GLM) backend for GLM-5.3 and other Z.ai models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.z.ai/api/coding/paas/v4")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        return ["glm-5.3", "glm-5.3-flash", "glm-4"]


class OpenRouterBackend(BaseBackend):
    """OpenRouter backend - aggregates 100+ models."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://openrouter.ai/api/v1")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


class GoogleBackend(BaseBackend):
    """Google AI (Gemini) backend."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://generativelanguage.googleapis.com/v1beta")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        contents = []
        for msg in messages:
            contents.append({"role": msg["role"], "parts": [{"text": msg["content"]}]})
        
        response = await self.client.post(
            f"/models/{model}:generateContent",
            params={"key": self.api_key},
            json={"contents": contents, **kwargs}
        )
        response.raise_for_status()
        data = response.json()
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": data["candidates"][0]["content"]["parts"][0]["text"]
                },
                "finish_reason": "stop"
            }]
        }
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models", params={"key": self.api_key})
        response.raise_for_status()
        data = response.json()
        return [m["name"].replace("models/", "") for m in data.get("models", [])]


class GroqBackend(BaseBackend):
    """Groq backend for ultra-fast inference."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.groq.com/openai/v1")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


class MistralBackend(BaseBackend):
    """Mistral AI backend."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.mistral.ai/v1")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


class TogetherBackend(BaseBackend):
    """Together AI backend."""
    
    def __init__(self, api_key: str):
        super().__init__(api_key, "https://api.together.xyz/v1")
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]


class OllamaBackend(BaseBackend):
    """Ollama backend for local models including Hermes Agent."""
    
    def __init__(self, api_key: str = "", base_url: str = None):
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        super().__init__(api_key or "ollama", base_url or ollama_host)
    
    async def chat_completions(self, model: str, messages: List[Dict], **kwargs) -> Dict:
        response = await self.client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": messages, **kwargs}
        )
        response.raise_for_status()
        return response.json()
    
    async def list_models(self) -> List[str]:
        response = await self.client.get("/v1/models")
        response.raise_for_status()
        data = response.json()
        return [m["id"] for m in data.get("data", [])]
    
    async def pull_model(self, model: str) -> Dict:
        """Pull a model from Ollama registry (e.g., hermes3, hermes-agent)."""
        response = await self.client.post(
            "/api/pull",
            json={"name": model, "stream": False}
        )
        response.raise_for_status()
        return response.json()


def get_backend(provider: str, api_key: str = "", base_url: str = None) -> BaseBackend:
    """Get backend instance by provider name."""
    backends = {
        "openai": OpenAIBackend,
        "anthropic": AnthropicBackend,
        "zai": ZaiBackend,
        "openrouter": OpenRouterBackend,
        "google": GoogleBackend,
        "groq": GroqBackend,
        "mistral": MistralBackend,
        "together": TogetherBackend,
        "ollama": OllamaBackend,
    }
    if provider not in backends:
        raise ValueError(f"Unknown provider: {provider}. Available: {list(backends.keys())}")
    return backends[provider](api_key, base_url) if provider == "ollama" else backends[provider](api_key)
