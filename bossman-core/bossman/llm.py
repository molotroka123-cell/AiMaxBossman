"""Вызовы моделей через LiteLLM ключом агента.

Политика облака проверяется здесь, ДО отправки (уровень интерфейса);
ключ агента без облачных алиасов отбил бы запрос и сам (уровень ключей),
а сеть без интернета — тем более (уровень сети). Три уровня из раздела 6.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx
import yaml

from . import db
from .agents import AgentSpec
from .config import settings

CLOUD_PREFIXES = ("claude-", "cloud-", "gemini", "gpt-")


class CloudDenied(Exception):
    """Агенту с cloud_policy=never облачный алиас запрещён."""


class NeedsCloudApproval(Exception):
    """cloud_policy=ask: сначала предпросмотр и кнопка «Отправить»."""

    def __init__(self, alias: str, preview: str):
        super().__init__(f"нужно подтверждение облака для {alias}")
        self.alias = alias
        self.preview = preview


def is_cloud(alias: str) -> bool:
    return alias.startswith(CLOUD_PREFIXES)


def real_window(alias: str) -> int:
    """Реальный потолок модели из tools/registry.yaml (10.7), не паспортное окно."""
    try:
        reg = yaml.safe_load(settings.tools_registry.read_text()) or {}
        return int((reg.get("model_windows") or {}).get(alias, 65536))
    except FileNotFoundError:
        return 65536


# для оценки попадания в кэш префикса: хэш стабильного начала последнего вызова агента
_last_prefix: dict[str, str] = {}


def _prefix_hash(messages: list[dict]) -> str:
    head = "".join(m["content"] for m in messages if m["role"] == "system")[:6000 * 3]
    return hashlib.sha256(head.encode()).hexdigest()


async def chat(agent: AgentSpec, messages: list[dict], *,
               alias: str | None = None,
               tools: list[dict] | None = None,
               run_id: int | None = None,
               block_tokens: dict[str, int] | None = None,
               cloud_approved_by: str | None = None,
               max_tokens: int | None = None) -> dict[str, Any]:
    """Один вызов chat/completions. Возвращает message из первого choice.
    Каждый вызов логируется в model_calls; облачный — дополнительно в cloud_calls."""
    alias = alias or agent.model
    cloud = is_cloud(alias)
    if cloud and agent.cloud_policy == "never":
        raise CloudDenied(f"{agent.name}: cloud_policy=never, алиас {alias} запрещён")
    if cloud and agent.cloud_policy == "ask" and not cloud_approved_by:
        preview = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        raise NeedsCloudApproval(alias, preview)

    key = agent.api_key or settings.litellm_master_key
    payload: dict[str, Any] = {"model": alias, "messages": messages}
    if tools:
        payload["tools"] = tools
    if max_tokens:
        payload["max_tokens"] = max_tokens
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{settings.litellm_url}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"}, json=payload)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    prompt_toks = int(usage.get("prompt_tokens") or 0)
    completion_toks = int(usage.get("completion_tokens") or 0)

    ph = _prefix_hash(messages)
    cache_hit = _last_prefix.get(agent.name) == ph
    _last_prefix[agent.name] = ph
    window = real_window(alias)

    await db.execute(
        """INSERT INTO model_calls (run_id, agent, alias, is_cloud, prompt_tokens,
           completion_tokens, block_tokens, window_fill, prefix_cache_hit)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        run_id, agent.name, alias, cloud, prompt_toks, completion_toks,
        block_tokens, prompt_toks / window if window else None, cache_hit)
    if cloud:
        preview = "\n\n".join(f"[{m['role']}]\n{m['content'][:2000]}" for m in messages)
        await db.execute(
            """INSERT INTO cloud_calls (run_id, agent, alias, prompt_preview,
               prompt_tokens, completion_tokens, approved_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            run_id, agent.name, alias, preview, prompt_toks, completion_toks,
            cloud_approved_by or ("policy:allowed" if agent.cloud_policy == "allowed" else None))
    msg = data["choices"][0]["message"]
    msg["_usage"] = {"prompt_tokens": prompt_toks, "completion_tokens": completion_toks}
    return msg


async def vision_caption(agent_name: str, path: str, question: str) -> str:
    """Подпись к кадру локальной моделью со зрением (bossman-writer).
    Файл не попадает в контекст главной петли — только эта подпись."""
    import base64
    from pathlib import Path
    data = base64.b64encode(Path(path).read_bytes()).decode()
    payload = {
        "model": "bossman-writer",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question + " Ответь не длиннее 300 токенов."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        ]}],
        "max_tokens": 400,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{settings.litellm_url}/chat/completions",
                                 headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                                 json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
