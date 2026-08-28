"""V2.2 — canonical code-search tools over the existing local CodeIndex.

No embeddings, no network, no Milvus, no extra service.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..config import ROOT
from ..db import settings_kv
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2 import scratch
from ..v2.code_index import CodeIndex, index_async, search_async
from . import Feature

router = APIRouter()
CODE_ROOTS_KEY = "code.roots"
TERMINAL_ROOTS_KEY = "terminal.roots"
CODE_IGNORES_KEY = "code.extra_ignores"
SCRATCH_ALIAS = "scratch"          # личная рабочая область агента (V2.2 §9)
MAX_RESULTS = 20
DEFAULT_RESULTS = 8
OUTPUT_LIMIT = 12000


@dataclass
class CodeIndexHandle:
    key: str
    root: Path
    index: CodeIndex
    task: asyncio.Task | None = None


def _repo_root() -> Path:
    return ROOT.parent.resolve()


def _within(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.expanduser().resolve()
    except OSError:
        return False
    for root in roots:
        try:
            rr = root.expanduser().resolve()
            if rp == rr or rr in rp.parents:
                return True
        except OSError:
            continue
    return False


async def _setting_json(svc, key: str, default: Any) -> Any:
    async with svc.db.session() as s:
        row = (await s.execute(
            sa.select(settings_kv.c.value_enc).where(settings_kv.c.key == key)
        )).first()
    if not row or not row[0]:
        return default
    try:
        return json.loads(svc.vault.decrypt(row[0]))
    except Exception:
        return default


async def _write_setting_json(svc, key: str, value: Any) -> None:
    enc = svc.vault.encrypt(json.dumps(value, ensure_ascii=False))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == key))
        await s.execute(sa.insert(settings_kv).values(key=key, value_enc=enc))
        await s.commit()


async def allowed_roots(svc) -> list[Path]:
    raw = await _setting_json(svc, CODE_ROOTS_KEY, None)
    if isinstance(raw, list) and raw:
        roots = [Path(str(x)).expanduser().resolve() for x in raw]
        existing = [x for x in roots if x.exists()]
        if existing:
            return existing

    raw = await _setting_json(svc, TERMINAL_ROOTS_KEY, None)
    if isinstance(raw, list) and raw:
        roots = [Path(str(x)).expanduser().resolve() for x in raw]
        existing = [x for x in roots if x.exists()]
        if existing:
            return existing

    # Never default to HOME, / or an entire drive.
    return [_repo_root()]


async def extra_ignores(svc) -> list[str]:
    raw = await _setting_json(svc, CODE_IGNORES_KEY, [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


async def resolve_root(svc, raw: str | None, workspace: str = "", ctx=None) -> Path:
    roots = await allowed_roots(svc)
    if ctx is not None and str(raw or "").strip() == SCRATCH_ALIAS:
        return scratch.ensure(scratch.for_context(ctx))
    candidate = Path(raw or workspace or str(roots[0])).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise PermissionError(f"code root недоступен: {candidate}: {exc}") from exc
    # V2.2 §9: рабочая область соседнего агента лежит внутри разрешённого корня,
    # поэтому общая проверка корней её пропустила бы — закрываем до неё.
    if ctx is not None:
        blocked = scratch.check(ctx, candidate)
        if blocked:
            raise PermissionError(blocked)
        own = scratch.for_context(ctx)
        if _within(candidate, [own]):
            return candidate
    if not _within(candidate, roots):
        raise PermissionError(
            f"code root {candidate} вне разрешённых корней: "
            + ", ".join(str(x) for x in roots)
        )
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def _handle_key(root: Path, ignores: list[str]) -> str:
    blob = json.dumps(
        {"root": str(root.resolve()), "ignores": sorted(ignores)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


async def get_handle(svc, root: Path) -> CodeIndexHandle:
    ignores = await extra_ignores(svc)
    key = _handle_key(root, ignores)
    pool = getattr(svc, "_code_index_pool", None)
    if pool is None:
        pool = {}
        svc._code_index_pool = pool
    if key in pool:
        return pool[key]

    index_path = Path(svc.settings.data_dir) / "code-index" / f"{key}.json"
    handle = CodeIndexHandle(
        key=key,
        root=root,
        index=CodeIndex(roots=[root], index_path=index_path, extra_ignores=ignores),
    )
    pool[key] = handle
    return handle


async def start_background_index(handle: CodeIndexHandle, *, force: bool = False) -> bool:
    if handle.task is not None and not handle.task.done():
        return False

    async def run() -> None:
        try:
            await index_async(handle.index, force=force)
        except Exception as exc:
            handle.index.status = {
                "phase": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    handle.task = asyncio.create_task(run(), name=f"code-index:{handle.key}")
    return True


async def ensure_index(handle: CodeIndexHandle) -> None:
    handle.index.load()
    if handle.index.chunks:
        return
    if handle.task is not None and not handle.task.done():
        await handle.task
        return
    await index_async(handle.index, force=False)


def _compact_hits(hits: list[dict]) -> tuple[str, bool]:
    if not hits:
        return "ничего не найдено", False
    blocks = []
    for i, hit in enumerate(hits, 1):
        blocks.append(
            f"[{i}] {hit.get('source')} :: {hit.get('qualname')} "
            f"({hit.get('kind')}, строки {hit.get('start_line')}-{hit.get('end_line')}, "
            f"score={hit.get('score')})\n{hit.get('content') or ''}"
        )
    text = "\n\n---\n\n".join(blocks)
    truncated = len(text) > OUTPUT_LIMIT
    return (text[:OUTPUT_LIMIT] if truncated else text), truncated


@router.get("/code/config")
async def http_config(request: Request):
    svc = request.app.state.svc
    return {
        "roots": [str(x) for x in await allowed_roots(svc)],
        "extra_ignores": await extra_ignores(svc),
        "default_repo_root": str(_repo_root()),
    }


@router.post("/code/config")
async def http_set_config(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    requested = body.get("roots")
    if not isinstance(requested, list) or not requested:
        raise HTTPException(400, {"message": "roots должен быть непустым массивом"})

    current = await allowed_roots(svc)
    resolved = []
    for raw in requested:
        p = Path(str(raw)).expanduser().resolve()
        if not p.exists():
            raise HTTPException(400, {"message": f"root не найден: {p}"})
        if not _within(p, current):
            raise HTTPException(403, {
                "message": f"root {p} расширяет текущую область code/terminal",
                "hint": "сначала явно расширьте terminal.roots через permission flow",
            })
        resolved.append(str(p))

    await _write_setting_json(svc, CODE_ROOTS_KEY, resolved)
    await _write_setting_json(
        svc, CODE_IGNORES_KEY, [str(x) for x in (body.get("extra_ignores") or [])]
    )
    svc._code_index_pool = {}
    return await http_config(request)


@router.post("/code/index")
async def http_index(request: Request):
    svc = request.app.state.svc
    body = await request.json() if await request.body() else {}
    try:
        root = await resolve_root(svc, body.get("root"))
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(403, {"message": str(exc)})
    handle = await get_handle(svc, root)
    started = await start_background_index(handle, force=bool(body.get("force")))
    return {"started": started, "root": str(root), "status": handle.index.stats()["status"]}


@router.get("/code/status")
async def http_status(request: Request, root: str = ""):
    svc = request.app.state.svc
    try:
        resolved = await resolve_root(svc, root or None)
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(403, {"message": str(exc)})
    handle = await get_handle(svc, resolved)
    stats = handle.index.stats()
    stats["running"] = bool(handle.task and not handle.task.done())
    return stats


@router.post("/code/search")
async def http_search(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, {"message": "query пуст"})
    try:
        root = await resolve_root(svc, body.get("root"))
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(403, {"message": str(exc)})
    handle = await get_handle(svc, root)
    await ensure_index(handle)
    hits = await search_async(
        handle.index,
        query,
        top_k=max(1, min(int(body.get("top_k") or DEFAULT_RESULTS), MAX_RESULTS)),
        path_prefix=str(body.get("path_prefix") or ""),
    )
    return {"root": str(root), "query": query, "items": hits, "total": len(hits)}


@router.post("/code/expand")
async def http_expand(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    chunk_hash = str(body.get("chunk_hash") or "").strip()
    if not chunk_hash:
        raise HTTPException(400, {"message": "chunk_hash пуст"})
    try:
        root = await resolve_root(svc, body.get("root"))
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(403, {"message": str(exc)})
    handle = await get_handle(svc, root)
    handle.index.load()
    chunk = handle.index.chunks.get(chunk_hash)
    if chunk is None:
        raise HTTPException(404, {"message": "code chunk не найден; переиндексируйте"})
    return {
        "chunk_hash": chunk.chunk_hash,
        "source": chunk.source,
        "qualname": chunk.qualname,
        "kind": chunk.kind,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "content": chunk.content,
    }


def _err(text: str) -> ToolResult:
    return ToolResult(content=text, one_line=text[:140], error=True)


async def tool_code_search(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("code.search: нужен query")
    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
        handle = await get_handle(ctx.svc, root)
        await ensure_index(handle)
        hits = await search_async(
            handle.index,
            query,
            top_k=max(1, min(int(args.get("top_k") or DEFAULT_RESULTS), MAX_RESULTS)),
            path_prefix=str(args.get("path_prefix") or ""),
        )
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"code.search: {exc}")
    except Exception as exc:
        return _err(f"code.search: {type(exc).__name__}: {exc}")

    text, truncated = _compact_hits(hits)
    return ToolResult(
        content=text,
        one_line=f"code.search: {len(hits)} результатов",
        truncated=truncated,
        more="сузьте query/path_prefix или используйте code.expand" if truncated else "",
        data={
            "root": str(root),
            "items": len(hits),
            "chunk_hashes": [h.get("chunk_hash") for h in hits],
            "sources": [h.get("source") for h in hits],
        },
    )


async def tool_code_expand(args: dict, ctx: ToolContext) -> ToolResult:
    chunk_hash = str(args.get("chunk_hash") or "").strip()
    if not chunk_hash:
        return _err("code.expand: нужен chunk_hash")
    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
        handle = await get_handle(ctx.svc, root)
        handle.index.load()
        chunk = handle.index.chunks.get(chunk_hash)
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"code.expand: {exc}")
    if chunk is None:
        return _err(f"code.expand: {chunk_hash} не найден")

    text = (
        f"{chunk.source} :: {chunk.qualname}\n"
        f"строки {chunk.start_line}-{chunk.end_line}\n{chunk.content}"
    )
    truncated = len(text) > OUTPUT_LIMIT
    return ToolResult(
        content=text[:OUTPUT_LIMIT] if truncated else text,
        one_line=f"code.expand: {chunk.source}:{chunk.start_line}-{chunk.end_line}",
        truncated=truncated,
        more="прочитайте конкретный файл через разрешённый filesystem tool" if truncated else "",
        data={
            "root": str(root), "source": chunk.source,
            "start_line": chunk.start_line, "end_line": chunk.end_line,
            "qualname": chunk.qualname,
        },
    )


async def tool_code_index(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
        handle = await get_handle(ctx.svc, root)
        started = await start_background_index(handle, force=bool(args.get("force")))
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"code.index: {exc}")
    return ToolResult(
        content=f"индексация {'запущена' if started else 'уже выполняется'} для {root}",
        one_line=f"code.index: {'started' if started else 'running'}",
        data={"root": str(root), "started": started},
    )


async def tool_code_status(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
        handle = await get_handle(ctx.svc, root)
        stats = handle.index.stats()
        stats["running"] = bool(handle.task and not handle.task.done())
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"code.status: {exc}")
    return ToolResult(
        content=json.dumps(stats, ensure_ascii=False),
        one_line=f"code.status: {stats.get('status', {}).get('phase', 'unknown')}",
        data=stats,
    )


SPECS = [
    ToolSpec(
        name="code.search", handler=tool_code_search, source="builtin", category="read",
        default_effect="auto", timeout_seconds=180.0,
        description="Искать релевантные фрагменты кода в разрешённом workspace локальным BM25.",
        input_schema={
            "query": {"type": "string"},
            "root": {"type": "string"},
            "path_prefix": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
        }, required=["query"]),
    ToolSpec(
        name="code.expand", handler=tool_code_expand, source="builtin", category="read",
        default_effect="auto", timeout_seconds=30.0,
        description="Развернуть code chunk по chunk_hash.",
        input_schema={"chunk_hash": {"type": "string"}, "root": {"type": "string"}},
        required=["chunk_hash"]),
    ToolSpec(
        name="code.index", handler=tool_code_index, source="builtin", category="read",
        default_effect="auto", timeout_seconds=30.0,
        description="Фоново обновить локальный индекс кода.",
        input_schema={"root": {"type": "string"}, "force": {"type": "boolean"}}),
    ToolSpec(
        name="code.status", handler=tool_code_status, source="builtin", category="read",
        default_effect="auto", timeout_seconds=30.0,
        description="Статус локального индекса кода.",
        input_schema={"root": {"type": "string"}}),
]


async def setup(svc) -> None:
    svc._code_index_pool = {}
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_code", router=router, setup=setup)
