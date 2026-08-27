"""http — вызовы API: статус + ключевые поля по схеме, не сырой JSON (≤2K токенов);
сырой ответ — в файл. Работает только у агентов, которым выдан, и только внутри
сети: наружу физически смотрит один LiteLLM (уровень 1 защиты)."""
from __future__ import annotations

import json
import uuid

import httpx

from . import ToolContext, ToolDef, ToolResult, clip, compact_json, register


def _pick(data, fields: list[str]):
    if not fields or not isinstance(data, dict):
        return data
    return {k: data.get(k) for k in fields if k in data}


async def http(args: dict, ctx: ToolContext) -> ToolResult:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(args.get("method", "GET"), args["url"],
                                    json=args.get("json"), params=args.get("params"))
    raw_path = ctx.workdir / "assets" / "logs" / f"http-{uuid.uuid4().hex[:8]}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(resp.text)
    try:
        data = _pick(resp.json(), args.get("fields") or [])
        body = compact_json(data)
    except json.JSONDecodeError:
        body = resp.text
    body, cut = clip(f"статус: {resp.status_code}\n{body}", 2000)
    rel = raw_path.relative_to(ctx.workdir)
    return ToolResult(body, one_line=f"http {args.get('method', 'GET')} {args['url'][:60]} → {resp.status_code}",
                      truncated=True, more=f"fs.read(path='{rel}')",
                      error=resp.status_code >= 400)


register(ToolDef("http", "HTTP-запрос: статус + ключевые поля (fields) вместо сырого JSON.",
                 "send", http,
                 params={"url": {"type": "string"}, "method": {"type": "string"},
                         "json": {"type": "object"}, "params": {"type": "object"},
                         "fields": {"type": "array", "items": {"type": "string"}}},
                 required=["url"], token_limit=2000))
