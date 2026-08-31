"""Вызовы моделей через LiteLLM ключом агента.

Политика облака проверяется здесь, ДО отправки (уровень интерфейса);
ключ агента без облачных алиасов отбил бы запрос и сам (уровень ключей),
а сеть без интернета — тем более (уровень сети). Три уровня из раздела 6.
"""
from __future__ import annotations

from typing import Any

import httpx
import yaml

from . import db
from .agents import AgentSpec, auto_gateway_config, load_all, validate_agent_models
from .config import settings
from .gateway.client import GatewayClient, GatewayCloudDenied
from .gateway.prompt_cache import extract_cache_usage, stable_session_id

CLOUD_PREFIXES = ("claude-", "cloud-", "gemini", "gpt-")

# ЭТАП 3: один переиспользуемый клиент Gateway на процесс (пул соединений).
# Создаётся лениво при первом обращении, закрывается в aclose_gateway()
# из lifecycle FastAPI — чтобы не оставлять осиротевших HTTP-клиентов.
_gateway: GatewayClient | None = None


def _gateway_client() -> GatewayClient:
    global _gateway
    if _gateway is None:
        client = GatewayClient(base_url=settings.gateway_url,
                               api_key=settings.gateway_core_key)
        # Хук стартовой валидации (аудит 2026-08-29: alias-мисматч давал 404 на
        # первом же вызове модели). Вместо молчаливого RouteNotFound в сети —
        # ранний ValueError со списком неразрешённых пар агент→модель. Клиент
        # кэшируется только после успешной проверки, иначе ValidationError
        # можно было бы «переждать» повторными вызовами.
        validate_agent_models(load_all(), auto_gateway_config())
        _gateway = client
    return _gateway


async def aclose_gateway() -> None:
    """Закрыть клиент Gateway при остановке процесса (вызывается из shutdown API)."""
    global _gateway
    if _gateway is not None:
        await _gateway.close()
        _gateway = None


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
    """Реальный потолок модели из tools/registry.yaml (10.7), не паспортное окно.

    V2.6 модуль E: результат — детерминированный разбор статичного файла, ровно
    «GOOD»-случай execution cache. Ключ включает mtime файла: правка registry
    инвалидирует запись сама (environment fingerprint)."""
    try:
        mtime = settings.tools_registry.stat().st_mtime
    except FileNotFoundError:
        return 65536
    try:
        from .exec_cache import get_cache
        cache = get_cache()
        key = cache.key("parsed_registry", str(settings.tools_registry), mtime)
        rec = cache.get(key)
        if rec is not None:
            reg = rec.result
        else:
            reg = yaml.safe_load(settings.tools_registry.read_text(encoding="utf-8")) or {}
            cache.put(key, reg, verified=True,
                      evidence=f"parsed {settings.tools_registry} @mtime={mtime}")
    except Exception:  # noqa: BLE001 — кэш вторичен: прямой разбор как раньше
        try:
            reg = yaml.safe_load(settings.tools_registry.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return 65536
    return int((reg.get("model_windows") or {}).get(alias, 65536))


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

    # Облачная политика (never/ask) уже проверена выше — до сети. Ниже только
    # транспорт: при заданном BOSSMAN_GATEWAY_URL идём через приватный Gateway,
    # иначе — прежним путём напрямую к LiteLLM ключом агента (совместимость).
    if settings.gateway_url:
        # Ключевая правка облачной границы: для capability-алиаса (bossman-smart и
        # т.п.) ядро НЕ знает по имени, ведёт ли маршрут в облако — is_cloud() тут
        # бесполезен. Поэтому политику решает Gateway, а ядро лишь сообщает, можно
        # ли ему трогать облако для ЭТОГО агента. never → нельзя; ask → только с
        # подтверждением; allowed → можно.
        cloud_allowed = agent.cloud_policy == "allowed" or (
            agent.cloud_policy == "ask" and bool(cloud_approved_by))
        try:
            session_id = stable_session_id(agent.name, run_id) if run_id is not None else ""
            data = await _gateway_client().chat(
                model=alias, messages=messages, tools=tools, max_tokens=max_tokens,
                cloud_allowed=cloud_allowed, session_id=session_id,
                cache_ttl=settings.prompt_cache_ttl, run_id=run_id)
        except GatewayCloudDenied:
            # Gateway вырезал облако и локально обслужить не смог. Превращаем в тот
            # же исход, что и прямой облачный алиас: never → отказ, ask → нужно
            # подтверждение владельца.
            if agent.cloud_policy == "never":
                raise CloudDenied(
                    f"{agent.name}: cloud_policy=never, алиас {alias} требует облака")
            preview = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
            raise NeedsCloudApproval(alias, preview)
    else:
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
    cache_usage = extract_cache_usage(data)
    prompt_toks = int(cache_usage.get("prompt_tokens") or 0)
    completion_toks = int(cache_usage.get("completion_tokens") or 0)
    cache_hit = int(cache_usage.get("cached_tokens") or 0) > 0
    window = real_window(alias)

    await db.execute(
        """INSERT INTO model_calls (run_id, agent, alias, is_cloud, prompt_tokens,
           completion_tokens, block_tokens, window_fill, prefix_cache_hit)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        run_id, agent.name, alias, cloud, prompt_toks, completion_toks,
        block_tokens, prompt_toks / window if window else None, cache_hit)
    if cloud:
        # V2.6 D3: prompt_preview — журнал «каждый байт, который ушёл», но секреты
        # в нём оседать не должны (obs.py и создан ради этого пути).
        preview = obs.redact(
            "\n\n".join(f"[{m['role']}]\n{m['content'][:2000]}" for m in messages))
        await db.execute(
            """INSERT INTO cloud_calls (run_id, agent, alias, prompt_preview,
               prompt_tokens, completion_tokens, approved_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            run_id, agent.name, alias, preview, prompt_toks, completion_toks,
            cloud_approved_by or ("policy:allowed" if agent.cloud_policy == "allowed" else None))
    msg = data["choices"][0]["message"]
    msg["_usage"] = {
        "prompt_tokens": prompt_toks,
        "completion_tokens": completion_toks,
        "cached_tokens": int(cache_usage.get("cached_tokens") or 0),
        "cache_write_tokens": int(cache_usage.get("cache_write_tokens") or 0),
    }
    return msg


async def gateway_metrics() -> dict | None:
    if not settings.gateway_url:
        return None
    return await _gateway_client().metrics()


async def vision_caption(agent_name: str, path: str, question: str) -> str:
    """Подпись к кадру локальной моделью со зрением (bossman-writer).
    Файл не попадает в контекст главной петли — только эта подпись."""
    import base64
    from pathlib import Path
    data = base64.b64encode(Path(path).read_bytes()).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": question + " Ответь не длиннее 300 токенов."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
    ]}]
    # ЭТАП 3: через Gateway зрение запрашивается capability-алиасом bossman-vision
    # (роутер сам выберет vision-совместимую цель). Без Gateway — прежний путь.
    if settings.gateway_url:
        resp = await _gateway_client().chat(
            model="bossman-vision", messages=messages, max_tokens=400)
        return resp["choices"][0]["message"]["content"]
    payload = {"model": "bossman-writer", "messages": messages, "max_tokens": 400}
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{settings.litellm_url}/chat/completions",
                                 headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                                 json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
