"""Code intelligence (LSP) как read-only capability в существующем registry.

Закрывает слабое место «Code intelligence»: definition/references/hover/
document-symbols/diagnostics через безопасный LSP-мост (argv-only, timeout,
bounded). Второго реестра/движка нет — регистрируемся в bcc.tools.REGISTRY.

Языковой сервер задаётся конфигом `LSP_SERVERS` (JSON: {lang: [argv...]}). Без
конфигурации capability честно возвращает "no LSP server configured" и НЕ падает.
Всё read-only (default_effect=auto) — код не изменяется.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from ..lsp_bridge import LSPClient, LSPConfig, LSPError
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()

CAPS = ["definition", "references", "hover", "symbols", "diagnostics"]


def _servers() -> dict[str, list[str]]:
    raw = os.environ.get("LSP_SERVERS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    out: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for lang, argv in data.items():
            if isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv):
                out[str(lang)] = argv
    return out


def _server_for(lang: str) -> list[str] | None:
    return _servers().get(lang)


async def _run(cap: str, args: dict, ctx: ToolContext) -> ToolResult:
    lang = str(args.get("lang") or "")
    argv = _server_for(lang)
    if not argv:
        return ToolResult(content=f"no LSP server configured for lang={lang!r} "
                          f"(set LSP_SERVERS)", one_line=f"code.{cap}: no server", error=True)
    workspace = str(args.get("workspace") or ctx.workspace or ".")
    try:
        ws = Path(workspace).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return ToolResult(content=f"bad workspace: {exc}", one_line=f"code.{cap} bad ws", error=True)
    client = LSPClient(LSPConfig(argv=tuple(argv), workspace=ws))
    try:
        await client.start()
        uri = str(args.get("uri") or "")
        line = int(args.get("line") or 0)
        char = int(args.get("char") or 0)
        if cap == "definition":
            data = await client.definition(uri, line, char)
        elif cap == "references":
            data = await client.references(uri, line, char)
        elif cap == "hover":
            data = await client.hover(uri, line, char)
        elif cap == "symbols":
            data = await client.symbols(uri)
        elif cap == "diagnostics":
            data = client.diagnostics(uri)
        else:
            return ToolResult(content="unknown capability", one_line="code.? unknown", error=True)
    except (LSPError, ValueError, TimeoutError) as exc:
        return ToolResult(content=f"lsp error: {exc}", one_line=f"code.{cap} error", error=True)
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return ToolResult(content=json.dumps(data, ensure_ascii=False)[:200_000],
                      one_line=f"code.{cap} ok", external=True, data={"result": data})


def _make_handler(cap: str):
    async def handler(args, ctx):
        return await _run(cap, args, ctx)
    return handler


_SCHEMA = {
    "lang": {"type": "string"}, "workspace": {"type": "string"},
    "uri": {"type": "string"}, "line": {"type": "integer"}, "char": {"type": "integer"},
}

REGISTERED: list[str] = []


async def setup(svc) -> None:
    REGISTERED.clear()
    for cap in CAPS:
        req = ["lang", "uri"] if cap != "symbols" and cap != "diagnostics" else ["lang", "uri"]
        REGISTRY.register(ToolSpec(
            name=f"code:{cap}", description=f"LSP {cap} (read-only code intelligence)",
            handler=_make_handler(cap), input_schema=_SCHEMA, required=req,
            category="read", permission="filesystem.read", source="plugin",
            default_effect="auto", idempotent=True, external_output=True))
        REGISTERED.append(f"code:{cap}")


@router.get("/code-intel")
async def code_intel_status():
    servers = _servers()
    return {"capabilities": CAPS, "configured_languages": sorted(servers),
            "enabled": bool(servers)}


FEATURE = Feature(name="code_intel", router=router, setup=setup)
