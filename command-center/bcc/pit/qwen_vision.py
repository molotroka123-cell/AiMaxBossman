from __future__ import annotations

import base64
import ipaddress
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from bcc.telegram_companion.adapters import IMAGE_MAX_BYTES, image_mime, json_request


@dataclass(frozen=True, slots=True)
class QwenVisionConfig:
    base_url: str
    model: str
    token: str = ""
    fast_timeout: float = 20.0
    memory_timeout: float = 60.0
    fast_max_tokens: int = 1024
    memory_max_tokens: int = 1536

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            _ = parsed.port
        except ValueError:
            loopback = False
        if parsed.scheme not in {"http", "https"} or not loopback or parsed.username or parsed.password:
            raise ValueError("Qwen vision endpoint must be explicit loopback")
        if not self.model.strip():
            raise ValueError("Qwen vision model id is required")
        if not 1 <= self.fast_timeout <= 120 or not 1 <= self.memory_timeout <= 300:
            raise ValueError("invalid Qwen vision timeout")


class QwenVisionBackend:
    """Loopback OpenAI-compatible Qwen2.5-VL adapter.

    No filesystem paths are handed to the model: the caller provides already
    verified bytes, encoded only for this request.
    """

    def __init__(self, config: QwenVisionConfig, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=max(config.fast_timeout, config.memory_timeout),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _content(self, data: bytes, mime: str, prompt: str):
        if len(data) > IMAGE_MAX_BYTES or image_mime(data) != mime:
            raise ValueError("unverified image bytes")
        encoded = base64.b64encode(data).decode("ascii")
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]

    async def _chat(self, data: bytes, mime: str, prompt: str, *, timeout: float, max_tokens: int) -> str:
        headers = {"Authorization": "Bearer " + self.config.token} if self.config.token else {}
        body = await json_request(
            self.client,
            "POST",
            self.config.base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            payload={
                "model": self.config.model,
                "stream": False,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": self._content(data, mime, prompt)}],
            },
            timeout=timeout,
        )
        if not isinstance(body, dict) or body.get("model") != self.config.model:
            raise ValueError("Qwen vision model identity mismatch")
        try:
            choice = body["choices"][0]
            message = choice["message"]
            text = message["content"]
            if message.get("tool_calls") or not isinstance(text, str) or not text.strip():
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("Qwen vision reply invalid") from None
        return text.strip()[:8000]

    async def analyze_fast(self, data: bytes, mime: str, user_prompt: str) -> str:
        prompt = str(user_prompt or "").strip() or (
            "Кратко опиши, что на изображении, прочитай важный видимый текст и ответь по-русски. "
            "Не пытайся идентифицировать реального человека и не угадывай скрытые чувствительные признаки."
        )
        return await self._chat(
            data,
            mime,
            prompt,
            timeout=self.config.fast_timeout,
            max_tokens=self.config.fast_max_tokens,
        )

    async def analyze_for_memory(self, data: bytes, mime: str, caption: str) -> dict:
        prompt = (
            "Верни ТОЛЬКО JSON object для локальной памяти Bossman по фото пользователя. "
            "Поля: scene (коротко), objects (массив строк), visible_text (массив строк), "
            "style (массив строк), memory_hints (массив коротких нейтральных наблюдений), uncertain (массив строк). "
            "Не идентифицируй людей. Не угадывай возраст, расу/этничность, здоровье, религию, политические взгляды, "
            "сексуальную жизнь, точное местоположение, адрес или финансовые данные. "
            "memory_hints должны описывать только явно видимый нейтральный контекст/вкус, полезный будущему разговору. "
            f"Подпись пользователя, если есть: {str(caption or '')[:500]}"
        )
        raw = await self._chat(
            data,
            mime,
            prompt,
            timeout=self.config.memory_timeout,
            max_tokens=self.config.memory_max_tokens,
        )
        try:
            value = json.loads(raw)
        except ValueError:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Qwen memory vision did not return JSON") from None
            value = json.loads(raw[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("Qwen memory vision JSON must be object")
        clean: dict[str, object] = {}
        clean["scene"] = str(value.get("scene", ""))[:500]
        for key in ("objects", "visible_text", "style", "memory_hints", "uncertain"):
            rows = value.get(key, [])
            clean[key] = [str(v)[:300] for v in rows[:20]] if isinstance(rows, list) else []
        return clean
