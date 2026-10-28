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
    if sys.platform == "win32":
        # resolve() already follows junctions; it does NOT refuse alternate data streams (`a.txt:x`), device names
        # (`NUL`, `CON`) or trailing dots/spaces that Windows drops silently: win_paths does (audit 28.10, CMP-UP-3).
        from ..security import win_paths
        try:
            win_paths._screen(str(path.expanduser()))      # the path as given: resolve() collapses `a.txt::$DATA` to `a.txt`
            win_paths._screen(str(rp))
        except win_paths.PathRefused:
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


async def _configured_roots(svc, key: str) -> list[Path]:
    raw = await _setting_json(svc, key, None)
    if isinstance(raw, list) and raw:
        roots = [Path(str(x)).expanduser().resolve() for x in raw]
        return [x for x in roots if x.exists()]
    return []


async def allowed_roots(svc) -> list[Path]:
    existing = await _configured_roots(svc, CODE_ROOTS_KEY)
    if existing:
        return existing

    existing = await _configured_roots(svc, TERMINAL_ROOTS_KEY)
    if existing:
        return existing

    # Never default to HOME, / or an entire drive.
    return [_repo_root()]


async def code_roots(svc) -> list[Path]:
    """Roots the read-only code.* tools search; the FIRST one is the default root.

    An explicit `code.roots` is used exactly. Without it, allowed_roots() falls back
    to `terminal.roots`, whose first entry is whatever the terminal was opened on —
    on the owner PC (23.10) the Python stdlib dir — so code.search without `root`
    searched the stdlib and returned 0 results 24 times for a Bossman code review.
    The Bossman repo (the same default allowed_roots() already uses when nothing is
    configured) is therefore put first. Never HOME / a whole drive; resolve_root()
    keeps the private-path and scratch checks for every candidate."""
    explicit = await _configured_roots(svc, CODE_ROOTS_KEY)
    if explicit:
        return explicit
    repo = _repo_root()
    return [repo] + [x for x in await allowed_roots(svc) if x != repo]


async def extra_ignores(svc) -> list[str]:
    raw = await _setting_json(svc, CODE_IGNORES_KEY, [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


async def resolve_root(svc, raw: str | None, workspace: str = "", ctx=None) -> Path:
    blocked_raw, reason_raw = is_private_chat_or_personal_path(raw)
    if blocked_raw:
        raise PermissionError(reason_raw)
    roots = await code_roots(svc)
    if ctx is not None and str(raw or "").strip() == SCRATCH_ALIAS:
        return scratch.ensure(scratch.for_context(ctx))
    candidate = Path(raw or workspace or str(roots[0])).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise PermissionError(f"code root недоступен: {candidate}: {exc}") from exc
    blocked_cand, reason_cand = is_private_chat_or_personal_path(candidate)
    if blocked_cand:
        raise PermissionError(reason_cand)
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
    # Audit 2026-10-05 #3: a root that is an ANCESTOR of <data_dir>/scratch (the project root, or the data dir itself) must not index the
    # agents' private scratch areas: the pool is shared, so a neighbour's drafts would become searchable by every agent. A root that
    # lies inside scratch (the agent's own alias) is indexed as before.
    scratch_base = scratch.base_dir(svc.settings)
    exclude = () if _within(root, [scratch_base]) else (scratch_base,)
    handle = CodeIndexHandle(
        key=key,
        root=root,
        index=CodeIndex(roots=[root], index_path=index_path, extra_ignores=ignores, exclude_dirs=exclude),
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
            f"chunk_hash={hit.get('chunk_hash')}, score={hit.get('score')})\n{hit.get('content') or ''}"
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
    path_prefix = str(body.get("path_prefix") or "")
    blocked_pfx, reason_pfx = is_private_chat_or_personal_path(path_prefix)
    if blocked_pfx:
        raise HTTPException(403, {"message": reason_pfx})
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
        path_prefix=path_prefix,
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
    path_prefix = str(args.get("path_prefix") or "")
    blocked_pfx, reason_pfx = is_private_chat_or_personal_path(path_prefix)
    if blocked_pfx:
        return _err(f"code.search: {reason_pfx}")
    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
        handle = await get_handle(ctx.svc, root)
        await ensure_index(handle)
        hits = await search_async(
            handle.index,
            query,
            top_k=max(1, min(int(args.get("top_k") or DEFAULT_RESULTS), MAX_RESULTS)),
            path_prefix=path_prefix,
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


# ------------------------------------------------------------------ Gemma 4 Autonomous Workspace Kit & Privacy Guard

import os
import re
import subprocess
import sys
from urllib.parse import urlparse

import httpx

PRIVATE_CHAT_AND_PERSONAL_DATA_DENY_RE = re.compile(
    r"(?i)(?:^|[/\\])(?:"
    r"telegram(?:\s*desktop)?|tdata|whatsapp|signal|viber|discord|skype|"
    r"user\s*data|login\s*data|web\s*data|cookies|browser[-_]?profile|"
    r"chat[-_]?archive[-_]?private|personal[-_]?chats?|local[-_]?chats?|"
    r"contacts|pictures|photos|videos|music|onedrive|"
    r"provider-keys(?:\.env|\.dpapi)?|free-provider-keys\.txt|"
    r"openrouter(?:-[a-z0-9_-]+)?\.env|\.dpapi$|\.env(?:\.[a-z0-9_-]+)?$"
    r")(?:$|[/\\])"
)

_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(?i)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|DPAPI|VAULT|AUTH|COOKIE|SESSION|SEED|PRIVATE)"
)

ALLOWED_LOCAL_DEV_PORTS = frozenset({3000, 4096, 5173, 8000, 8080, 8801, 8840, 11434})

GEMMA4_DEFAULT_MODEL = "gemma4-31b-uncensored:latest"
GEMMA4_KIT_TOOLS = [
    "code.search",
    "code.expand",
    "code.read",
    "code.write",
    "code.patch",
    "code.index",
    "code.status",
    "code.workspace_map",
    "code.probe_endpoint",
    "sandbox.run_check",
    "terminal.run",
    "terminal.status",
    "computer.observe",
    "computer.act",
    "browser.open",
    "browser.read_dom",
    "browser.screenshot",
    "opencode.status",
    "opencode.diff",
    "opencode.abort",
    "agents.list",
    "call_agent",
    "agents.result",
]
GEMMA4_KIT_PERMISSIONS = {
    "use_memory": False,
    "filesystem.read": True,
    "terminal.read": True,
    "terminal.run": True,
    "git.read": True,
    "browser.read": True,
    "computer.observe": True,
}


def is_private_chat_or_personal_path(raw: str | Path | None) -> tuple[bool, str]:
    """Hard T3 privacy invariant: local chats, messenger stores, browser profiles,
    personal media, and plaintext/DPAPI secret files are strictly off-limits."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return False, ""
    if PRIVATE_CHAT_AND_PERSONAL_DATA_DENY_RE.search(text):
        return True, (
            f"доступ к локальным чатам, личным данным или секретам запрещён политикой безопасности (T3): {text}"
        )
    return False, ""


def _parse_project_dependencies(root: Path) -> dict[str, Any]:
    """Extract project dependencies from pyproject.toml, package.json, and requirements*.txt."""
    deps: dict[str, Any] = {"pyproject": {}, "package_json": {}, "requirements": {}}
    candidates = [
        root / "pyproject.toml",
        root / "bossman-core" / "pyproject.toml",
        root / "command-center" / "pyproject.toml",
        root / "apps" / "war-terminal" / "pyproject.toml",
    ]
    dep_line_re = re.compile(r'^\s*"([A-Za-z0-9_.\-\[\]>=<~]+)"\s*,?\s*$')
    for p in candidates:
        if p.is_file():
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                extracted = []
                in_deps = False
                for ln in lines:
                    s = ln.strip()
                    if s.startswith("dependencies") and "[" in s:
                        in_deps = True
                        continue
                    if in_deps:
                        if s.startswith("]"):
                            in_deps = False
                            continue
                        m = dep_line_re.match(ln)
                        if m:
                            extracted.append(m.group(1))
                deps["pyproject"][rel] = extracted[:60]
            except OSError:
                continue

    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            doc = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps["package_json"]["package.json"] = {
                "dependencies": sorted((doc.get("dependencies") or {}).keys())[:50],
                "devDependencies": sorted((doc.get("devDependencies") or {}).keys())[:50],
            }
        except Exception:
            pass

    for req_file in sorted(root.glob("requirements*.txt"))[:5]:
        if req_file.is_file():
            try:
                rel = str(req_file.relative_to(root)).replace("\\", "/")
                reqs = [
                    ln.strip()
                    for ln in req_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                deps["requirements"][rel] = reqs[:60]
            except OSError:
                continue
    return deps


def _collect_workspace_tree(root: Path, max_depth: int = 2, max_entries: int = 120) -> list[str]:
    """Return a clean relative directory/file structure inside root, excluding ignored & private paths."""
    ignore_names = {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    }
    entries: list[str] = []

    def _walk(curr: Path, depth: int) -> None:
        if len(entries) >= max_entries or depth > max_depth:
            return
        try:
            children = sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for child in children:
            if len(entries) >= max_entries:
                break
            if child.name in ignore_names or child.name.startswith(".pytest"):
                continue
            rel = str(child.relative_to(root)).replace("\\", "/")
            blocked, _ = is_private_chat_or_personal_path(rel)
            if blocked:
                continue
            if child.is_dir():
                entries.append(f"{rel}/")
                if depth < max_depth:
                    _walk(child, depth + 1)
            else:
                entries.append(rel)

    _walk(root, 1)
    return entries


def _collect_git_history(root: Path, limit: int = 10) -> dict[str, Any]:
    """Read current branch, HEAD SHA, and recent commit log safely via argv (no shell)."""
    limit = max(1, min(int(limit or 10), 30))
    info: dict[str, Any] = {"branch": None, "head_sha": None, "recent_commits": []}
    try:
        br = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5, check=False,
        )
        if br.returncode == 0:
            info["branch"] = br.stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5, check=False,
        )
        if sha.returncode == 0:
            info["head_sha"] = sha.stdout.strip()
        log = subprocess.run(
            ["git", "log", f"-n{limit}", "--oneline", "--no-decorate"],
            cwd=str(root), capture_output=True, text=True, timeout=8, check=False,
        )
        if log.returncode == 0:
            info["recent_commits"] = [ln.strip() for ln in log.stdout.splitlines() if ln.strip()]
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


async def build_workspace_map(svc, raw_root: str | None = None, workspace: str = "",
                              ctx=None, max_depth: int = 2, git_commits: int = 10) -> dict[str, Any]:
    blocked, reason = is_private_chat_or_personal_path(raw_root)
    if blocked:
        raise PermissionError(reason)
    root = await resolve_root(svc, raw_root, workspace, ctx)
    blocked_root, reason_root = is_private_chat_or_personal_path(root)
    if blocked_root:
        raise PermissionError(reason_root)

    handle = await get_handle(svc, root)
    await ensure_index(handle)
    max_depth = max(1, min(int(max_depth or 2), 4))
    tree = _collect_workspace_tree(root, max_depth=max_depth)
    deps = _parse_project_dependencies(root)
    git_info = _collect_git_history(root, limit=git_commits)
    return {
        "root": str(root),
        "tree": tree,
        "dependencies": deps,
        "git": git_info,
        "bm25_index": handle.index.stats(),
        "privacy_guard": {
            "local_chats_blocked": True,
            "personal_data_blocked": True,
            "secrets_blocked": True,
        },
    }


async def tool_code_workspace_map(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        data = await build_workspace_map(
            ctx.svc,
            raw_root=args.get("root"),
            workspace=ctx.workspace,
            ctx=ctx,
            max_depth=int(args.get("max_depth") or 2),
            git_commits=int(args.get("git_commits") or 10),
        )
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"code.workspace_map: {exc}")
    except Exception as exc:
        return _err(f"code.workspace_map: {type(exc).__name__}: {exc}")

    text = json.dumps(data, ensure_ascii=False, indent=2)
    truncated = len(text) > OUTPUT_LIMIT
    return ToolResult(
        content=text[:OUTPUT_LIMIT] if truncated else text,
        one_line=(
            f"code.workspace_map: {len(data['tree'])} путей, "
            f"ветка {data['git'].get('branch')}, коммитов {len(data['git'].get('recent_commits') or [])}"
        ),
        truncated=truncated,
        more="уменьшите max_depth или используйте code.search" if truncated else "",
        data=data,
    )


def validate_probe_url(url: str) -> tuple[bool, str]:
    """Validate that an endpoint probe URL is either an allowed local project dev port
    or a safe public HTTP(S) endpoint passing SSRF checks."""
    raw = str(url or "").strip()
    if not raw:
        return False, "пустой URL"
    try:
        parsed = urlparse(raw)
    except Exception as exc:
        return False, f"некорректный URL: {exc}"
    if parsed.scheme not in ("http", "https"):
        return False, f"разрешены только схемы http/https (получено {parsed.scheme!r})"
    if parsed.username or parsed.password:
        return False, "учётные данные в URL запрещены"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "не указан хост"
    if host in ("127.0.0.1", "localhost", "::1"):
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in ALLOWED_LOCAL_DEV_PORTS:
            return False, (
                f"локальный порт {port} не входит в список разрешённых dev-портов проекта "
                f"({sorted(ALLOWED_LOCAL_DEV_PORTS)})"
            )
        return True, ""
    from ..v2.browser_control import resolved_target_refusal
    refusal = resolved_target_refusal(raw)
    if refusal:
        return False, f"запрос заблокирован сетевым фильтром (SSRF/LAN): {refusal}"
    return True, ""


async def tool_code_probe_endpoint(args: dict, ctx: ToolContext) -> ToolResult:
    url = str(args.get("url") or "").strip()
    method = str(args.get("method") or "GET").upper().strip()
    if method not in ("GET", "HEAD"):
        return _err(f"code.probe_endpoint: разрешены только безопасные методы GET и HEAD (получено {method!r})")
    ok, reason = validate_probe_url(url)
    if not ok:
        return _err(f"code.probe_endpoint: {reason}")
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, trust_env=False) as client:
            resp = await client.request(method, url, headers={"Accept": "application/json, text/plain, */*"})
            body_preview = resp.text[:4000] if method == "GET" else ""
            payload = {
                "url": url,
                "method": method,
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type", ""),
                "body_preview": body_preview,
            }
    except Exception as exc:
        return _err(f"code.probe_endpoint: {type(exc).__name__}: {exc}")

    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        one_line=f"code.probe_endpoint: {method} {url} -> HTTP {payload['status_code']}",
        data=payload,
        external=True,
    )


def _scrubbed_sandbox_env(scratch_dir: Path) -> dict[str, str]:
    """Build a secret-free, network-disabled environment for isolated sandbox test/syntax runs."""
    clean: dict[str, str] = {}
    for k, v in os.environ.items():
        if _SENSITIVE_ENV_KEY_RE.search(k):
            continue
        clean[k] = v
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    clean["PYTHONUTF8"] = "1"
    clean["NO_PROXY"] = "*"
    clean["HTTP_PROXY"] = "http://127.0.0.1:9"
    clean["HTTPS_PROXY"] = "http://127.0.0.1:9"
    clean["ALL_PROXY"] = "http://127.0.0.1:9"
    clean["PIP_NO_INDEX"] = "1"
    clean["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    clean["TMP"] = str(scratch_dir)
    clean["TEMP"] = str(scratch_dir)
    clean["TMPDIR"] = str(scratch_dir)
    return clean


async def tool_sandbox_run_check(args: dict, ctx: ToolContext) -> ToolResult:
    """Run an allowlisted verification check (pytest, compileall, ast_check) inside
    an isolated sandbox environment with scrubbed secrets and disabled network."""
    target_raw = str(args.get("target") or "").strip()
    check_kind = str(args.get("check_kind") or "pytest").strip().lower()
    if check_kind not in ("pytest", "compileall", "ast_check"):
        return _err(f"sandbox.run_check: недопустимый check_kind={check_kind!r} (разрешены pytest, compileall, ast_check)")
    if not target_raw:
        return _err("sandbox.run_check: нужен относительный путь target внутри проекта")
    if any(ch in target_raw for ch in (";", "|", "&", "`", "$", ">", "<", "\n", "\r")):
        return _err("sandbox.run_check: метасимволы оболочки в target запрещены")

    blocked, reason = is_private_chat_or_personal_path(target_raw)
    if blocked:
        return _err(f"sandbox.run_check: {reason}")

    try:
        root = await resolve_root(ctx.svc, args.get("root"), ctx.workspace, ctx)
    except (PermissionError, FileNotFoundError) as exc:
        return _err(f"sandbox.run_check: {exc}")

    candidate = (root / target_raw).resolve()
    if not _within(candidate, [root]):
        return _err(f"sandbox.run_check: путь {target_raw!r} выходит за пределы корня проекта {root}")
    blocked_cand, reason_cand = is_private_chat_or_personal_path(candidate)
    if blocked_cand:
        return _err(f"sandbox.run_check: {reason_cand}")
    if not candidate.exists():
        return _err(f"sandbox.run_check: путь не найден: {candidate}")

    own_scratch = scratch.ensure(scratch.for_context(ctx))
    sandbox_tmp = own_scratch / "sandbox_tmp"
    sandbox_tmp.mkdir(parents=True, exist_ok=True)
    env = _scrubbed_sandbox_env(sandbox_tmp)

    if check_kind == "pytest":
        argv = [
            sys.executable, "-m", "pytest", str(candidate),
            "-q", "--basetemp", str(sandbox_tmp / "pytest"),
        ]
    elif check_kind == "compileall":
        argv = [sys.executable, "-m", "compileall", "-q", str(candidate)]
    else:
        if not candidate.is_file():
            return _err("sandbox.run_check: ast_check требует путь к файлу .py")
        try:
            import ast as _ast
            _ast.parse(candidate.read_text(encoding="utf-8"), filename=str(candidate))
            return ToolResult(
                content=f"AST check PASS: {candidate.relative_to(root)}",
                one_line=f"sandbox.run_check: ast_check PASS ({candidate.name})",
                data={"exit_code": 0, "check_kind": "ast_check", "target": str(candidate.relative_to(root))},
            )
        except SyntaxError as exc:
            return ToolResult(
                content=f"AST SyntaxError: {exc}",
                one_line=f"sandbox.run_check: ast_check FAIL ({candidate.name})",
                error=True,
                data={"exit_code": 1, "check_kind": "ast_check", "error": str(exc)},
            )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except asyncio.TimeoutError:
            proc.kill()
            return _err("sandbox.run_check: превышен лимит времени 60с")
        exit_code = int(proc.returncode or 0)
        output = (out_bytes or b"").decode("utf-8", errors="replace")
    except Exception as exc:
        return _err(f"sandbox.run_check: {type(exc).__name__}: {exc}")

    truncated = len(output) > OUTPUT_LIMIT
    return ToolResult(
        content=output[:OUTPUT_LIMIT] if truncated else output,
        one_line=f"sandbox.run_check ({check_kind}): exit_code={exit_code}",
        truncated=truncated,
        more="сузьте target до конкретного тестового файла" if truncated else "",
        error=(exit_code != 0),
        data={
            "exit_code": exit_code,
            "check_kind": check_kind,
            "target": str(candidate.relative_to(root)).replace("\\", "/"),
            "network_isolated": True,
            "secrets_scrubbed": True,
        },
    )


@router.get("/code/workspace-map")
async def http_workspace_map(request: Request, root: str = "", max_depth: int = 2, git_commits: int = 10):
    svc = request.app.state.svc
    try:
        return await build_workspace_map(
            svc, raw_root=root or None, max_depth=max_depth, git_commits=git_commits,
        )
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(403, {"message": str(exc)})


@router.post("/code/probe-endpoint")
async def http_probe_endpoint(request: Request):
    body = await request.json()
    url = str(body.get("url") or "").strip()
    method = str(body.get("method") or "GET").upper().strip()
    if method not in ("GET", "HEAD"):
        raise HTTPException(400, {"message": "разрешены только GET и HEAD"})
    ok, reason = validate_probe_url(url)
    if not ok:
        raise HTTPException(403, {"message": reason})
    res = await tool_code_probe_endpoint({"url": url, "method": method}, ToolContext(svc=request.app.state.svc, task={}, run_id=0, agent={}))
    if res.error:
        raise HTTPException(502, {"message": res.content})
    return res.data


@router.get("/code/gemma-kit")
async def http_gemma_kit_status(request: Request):
    svc = request.app.state.svc
    roots = [str(x) for x in await allowed_roots(svc)]
    return {
        "model": GEMMA4_DEFAULT_MODEL,
        "tools": GEMMA4_KIT_TOOLS,
        "permissions": GEMMA4_KIT_PERMISSIONS,
        "allowed_roots": roots,
        "allowed_local_dev_ports": sorted(ALLOWED_LOCAL_DEV_PORTS),
        "policy_boundaries": {
            "auto": [
                "code.search (BM25)", "code.expand", "code.index", "code.status",
                "code.workspace_map (tree + dependencies + git log)",
                "code.probe_endpoint (safe HTTP GET/HEAD readiness)",
                "sandbox.run_check (isolated pytest/compileall/ast_check, no secrets, no network)",
                "terminal.run (git status/diff/log, pytest, ruff, npm test in project root)",
                "computer.observe (allowlisted project windows)",
                "browser.open / browser.read_dom / browser.screenshot",
            ],
            "ask_owner_approval": [
                "terminal.run: pip/npm/uv install, docker compose up, git push, network in sandbox",
                "computer.act: one-shot approval per desktop action (bound to generation + hwnd + pid, instant STOP)",
                "coding-tasks apply: applying verified isolated worktree patch to canonical branch",
            ],
            "hard_deny": [
                "local chats & messengers (Telegram tdata, WhatsApp, Signal, Viber, Discord, private chat archives)",
                "personal data & browser profiles (User Data, cookies, Pictures, Videos, Contacts)",
                "secrets & keys (.env, .dpapi, provider-keys, wallets, SSH keys)",
                "destructive & offensive commands (git push --force, git reset --hard, disk format, nmap/exploits)",
            ],
        },
    }


@router.post("/code/gemma-kit/provision")
async def http_gemma_kit_provision(request: Request):
    """Provision or update the local Gemma 4 agent with the full governed Workspace Kit."""
    from ..db import agents as agents_t, models as models_t, providers as providers_t, utcnow

    svc = request.app.state.svc
    body = await request.json() if await request.body() else {}
    model_name = str(body.get("model") or GEMMA4_DEFAULT_MODEL).strip()
    agent_name = str(body.get("agent_name") or "Gemma 4 Workspace Engineer").strip()

    repo = _repo_root()
    current_roots = [str(x) for x in await allowed_roots(svc)]
    if str(repo) not in current_roots:
        current_roots.append(str(repo))
    await _write_setting_json(svc, CODE_ROOTS_KEY, current_roots)
    await _write_setting_json(svc, TERMINAL_ROOTS_KEY, current_roots)

    async with svc.db.session() as s:
        prov_row = (await s.execute(
            sa.select(providers_t.c.id).where(providers_t.c.name == "Local Ollama")
        )).first()
        if prov_row:
            provider_id = int(prov_row[0])
        else:
            res_p = await s.execute(sa.insert(providers_t).values(
                name="Local Ollama",
                kind="openai_compat",
                base_url="http://127.0.0.1:11434/v1",
                created_at=utcnow(),
            ))
            provider_id = int(res_p.inserted_primary_key[0])

        mod_row = (await s.execute(
            sa.select(models_t.c.id).where(models_t.c.name == model_name)
        )).first()
        if mod_row:
            model_id = int(mod_row[0])
            await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                caps={"chat": True, "tools": True, "code": True},
                context_window=32768,
            ))
        else:
            res_m = await s.execute(sa.insert(models_t).values(
                provider_id=provider_id,
                name=model_name,
                alias=model_name,
                caps={"chat": True, "tools": True, "code": True},
                context_window=32768,
            ))
            model_id = int(res_m.inserted_primary_key[0])

        ag_row = (await s.execute(
            sa.select(agents_t.c.id).where(agents_t.c.name == agent_name)
        )).first()
        sys_prompt = (
            "Ты — Gemma 4 Workspace Engineer в составе команды Bossman. "
            "Работай автономно в рамках разрешённого workspace проекта: используй code.workspace_map и code.search (BM25) "
            "для анализа архитектуры, зависимостей и истории коммитов; sandbox.run_check и terminal.run — для запуска тестов; "
            "code.probe_endpoint и browser.* — для проверки API и фронтенда; computer.observe — для мониторинга окон. "
            "Строго запрещено обращаться к локальным чатам, мессенджерам, личным данным и секретам владельца."
        )
        if ag_row:
            agent_id = int(ag_row[0])
            await s.execute(sa.update(agents_t).where(agents_t.c.id == agent_id).values(
                model_id=model_id,
                system_prompt=sys_prompt,
                tools=GEMMA4_KIT_TOOLS,
                permissions=GEMMA4_KIT_PERMISSIONS,
                max_steps=12,
                max_tokens=4096,
            ))
        else:
            res_a = await s.execute(sa.insert(agents_t).values(
                name=agent_name,
                model_id=model_id,
                system_prompt=sys_prompt,
                tools=GEMMA4_KIT_TOOLS,
                permissions=GEMMA4_KIT_PERMISSIONS,
                max_steps=12,
                max_tokens=4096,
                created_at=utcnow(),
            ))
            agent_id = int(res_a.inserted_primary_key[0])
        await s.commit()

    return {
        "provisioned": True,
        "provider_id": provider_id,
        "model_id": model_id,
        "model_name": model_name,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "tools": GEMMA4_KIT_TOOLS,
        "permissions": GEMMA4_KIT_PERMISSIONS,
        "allowed_roots": current_roots,
        "privacy_guard": {
            "local_chats_blocked": True,
            "personal_data_blocked": True,
            "secrets_blocked": True,
        },
    }


async def _resolve_code_file_target(
    ctx: ToolContext,
    raw_path: str,
    raw_root: str | None = None,
    *,
    for_write: bool = False,
) -> tuple[Path, Path]:
    from .pc_agent_policy_paths import is_t3_secret_or_protected_path

    path_s = str(raw_path or "").strip()
    if not path_s:
        raise ValueError("нужен путь path к файлу")
    blocked_priv, reason_priv = is_private_chat_or_personal_path(path_s)
    if blocked_priv:
        raise PermissionError(reason_priv)
    root = await resolve_root(ctx.svc, raw_root, ctx.workspace, ctx)
    data_dir = Path(ctx.svc.settings.data_dir) if getattr(ctx.svc, "settings", None) else None
    p = Path(path_s).expanduser()
    candidate = (p if p.is_absolute() else (root / p)).resolve()
    if not _within(candidate, [root]):
        raise PermissionError(f"путь {candidate} выходит за пределы разрешённого корня {root}")
    blocked_cand_priv, reason_cand_priv = is_private_chat_or_personal_path(candidate)
    if blocked_cand_priv:
        raise PermissionError(reason_cand_priv)
    blocked_t3, reason_t3 = is_t3_secret_or_protected_path(
        candidate, for_write=for_write, repo_root=root, data_dir=data_dir
    )
    if blocked_t3:
        raise PermissionError(reason_t3)
    return root, candidate


async def tool_code_read(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        root, target = await _resolve_code_file_target(
            ctx, str(args.get("path") or ""), args.get("root"), for_write=False
        )
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return _err(f"code.read: {exc}")
    if not target.is_file():
        return _err(f"code.read: файл не найден: {target}")
    try:
        raw_bytes = target.read_bytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8", errors="replace")
    except OSError as exc:
        return _err(f"code.read: {exc}")

    lines = text.splitlines()
    total_lines = len(lines)
    start_line = max(1, int(args.get("start_line") or 1))
    end_line = min(total_lines, int(args.get("end_line") or total_lines)) if total_lines else 0
    selected = "\n".join(lines[start_line - 1 : end_line]) if total_lines else ""
    truncated = len(selected) > OUTPUT_LIMIT
    rel = str(target.relative_to(root)).replace("\\", "/")
    return ToolResult(
        content=selected[:OUTPUT_LIMIT] if truncated else selected,
        one_line=f"code.read: {rel} ({start_line}-{end_line}/{total_lines} строк, sha256={sha256[:12]})",
        truncated=truncated,
        more="укажите start_line/end_line для чтения по частям" if truncated else "",
        data={
            "root": str(root),
            "path": rel,
            "abs_path": str(target),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "sha256": sha256,
        },
    )


async def tool_code_write(args: dict, ctx: ToolContext) -> ToolResult:
    content = args.get("content")
    if not isinstance(content, str):
        return _err("code.write: нужен строковый аргумент content (UTF-8)")
    try:
        root, target = await _resolve_code_file_target(
            ctx, str(args.get("path") or ""), args.get("root"), for_write=True
        )
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return _err(f"code.write: {exc}")
    try:
        before_sha = (
            hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        raw_bytes = content.encode("utf-8")
        target.write_bytes(raw_bytes)
        after_sha = hashlib.sha256(raw_bytes).hexdigest()
        try:
            handle = await get_handle(ctx.svc, root)
            await start_background_index(handle, force=True)
        except Exception:
            pass
    except OSError as exc:
        return _err(f"code.write: {exc}")
    rel = str(target.relative_to(root)).replace("\\", "/")
    return ToolResult(
        content=f"Записан файл {rel} ({len(raw_bytes)} байт UTF-8, sha256={after_sha[:12]})",
        one_line=f"code.write: {rel} ({len(raw_bytes)} байт, sha256={after_sha[:12]})",
        data={
            "root": str(root),
            "path": rel,
            "abs_path": str(target),
            "bytes_written": len(raw_bytes),
            "before_sha256": before_sha,
            "after_sha256": after_sha,
        },
    )


async def tool_code_patch(args: dict, ctx: ToolContext) -> ToolResult:
    old_text = args.get("old_text") if "old_text" in args else args.get("target_text")
    new_text = args.get("new_text") if "new_text" in args else args.get("replacement_text")
    if not isinstance(old_text, str) or not old_text:
        return _err("code.patch: нужен непустой строковый old_text")
    if not isinstance(new_text, str):
        return _err("code.patch: нужен строковый new_text")
    allow_multiple = bool(args.get("allow_multiple", False))
    try:
        root, target = await _resolve_code_file_target(
            ctx, str(args.get("path") or ""), args.get("root"), for_write=True
        )
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return _err(f"code.patch: {exc}")
    if not target.is_file():
        return _err(f"code.patch: файл не найден: {target}")
    try:
        before_bytes = target.read_bytes()
        before_sha = hashlib.sha256(before_bytes).hexdigest()
        current = before_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _err(f"code.patch: ошибка чтения UTF-8: {exc}")
    occurrences = current.count(old_text)
    if occurrences == 0:
        return _err("code.patch: фрагмент old_text не найден в файле")
    if occurrences > 1 and not allow_multiple:
        return _err(
            f"code.patch: фрагмент old_text встретился {occurrences} раз(а); "
            "добавьте контекст или передайте allow_multiple=true"
        )
    updated = (
        current.replace(old_text, new_text)
        if allow_multiple
        else current.replace(old_text, new_text, 1)
    )
    try:
        after_bytes = updated.encode("utf-8")
        target.write_bytes(after_bytes)
        after_sha = hashlib.sha256(after_bytes).hexdigest()
        try:
            handle = await get_handle(ctx.svc, root)
            await start_background_index(handle, force=True)
        except Exception:
            pass
    except OSError as exc:
        return _err(f"code.patch: {exc}")
    rel = str(target.relative_to(root)).replace("\\", "/")
    replaced_count = occurrences if allow_multiple else 1
    return ToolResult(
        content=f"Обновлён файл {rel}: замен={replaced_count}, sha256={after_sha[:12]}",
        one_line=f"code.patch: {rel} (замен={replaced_count}, sha256={after_sha[:12]})",
        data={
            "root": str(root),
            "path": rel,
            "abs_path": str(target),
            "replaced_count": replaced_count,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
        },
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
        name="code.read", handler=tool_code_read, source="builtin", category="read",
        permission="filesystem.read", default_effect="auto", timeout_seconds=30.0,
        description="Прочитать файл (UTF-8) внутри разрешённого workspace с проверкой защищённых путей.",
        input_schema={
            "path": {"type": "string"},
            "root": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }, required=["path"]),
    ToolSpec(
        name="code.write", handler=tool_code_write, source="builtin", category="write",
        permission="terminal.run", default_effect="auto", timeout_seconds=30.0,
        description="Записать текстовый файл (UTF-8) внутри разрешённого workspace (защищённое ядро заблокировано).",
        input_schema={
            "path": {"type": "string"},
            "content": {"type": "string"},
            "root": {"type": "string"},
        }, required=["path", "content"]),
    ToolSpec(
        name="code.patch", handler=tool_code_patch, source="builtin", category="write",
        permission="terminal.run", default_effect="auto", timeout_seconds=30.0,
        description="Точечно заменить фрагмент текста (UTF-8) в файле внутри разрешённого workspace (защищённое ядро заблокировано).",
        input_schema={
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "allow_multiple": {"type": "boolean"},
            "root": {"type": "string"},
        }, required=["path", "old_text", "new_text"]),
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
    ToolSpec(
        name="code.workspace_map", handler=tool_code_workspace_map, source="builtin", category="read",
        permission="filesystem.read", default_effect="auto", timeout_seconds=60.0,
        description="Карта workspace проекта: структура папок, зависимости (pyproject/package.json), ветка и история коммитов Git, статус BM25-индекса.",
        input_schema={
            "root": {"type": "string"},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
            "git_commits": {"type": "integer", "minimum": 1, "maximum": 30},
        }),
    ToolSpec(
        name="code.probe_endpoint", handler=tool_code_probe_endpoint, source="builtin", category="read",
        permission="browser.read", default_effect="auto", timeout_seconds=10.0, external_output=True,
        description="Безопасная проверка доступности (HTTP GET/HEAD) локальных dev-серверов проекта или публичных HTTPS API с защитой от SSRF.",
        input_schema={
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "HEAD"]},
        }, required=["url"]),
    ToolSpec(
        name="sandbox.run_check", handler=tool_sandbox_run_check, source="builtin", category="exec",
        permission="terminal.run", default_effect="auto", timeout_seconds=65.0,
        description="Запустить изолированную проверку (pytest, compileall, ast_check) в песочнице проекта без секретов в окружении и без доступа к сети.",
        input_schema={
            "target": {"type": "string"},
            "check_kind": {"type": "string", "enum": ["pytest", "compileall", "ast_check"]},
            "root": {"type": "string"},
        }, required=["target"]),
]


async def setup(svc) -> None:
    svc._code_index_pool = {}
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_code", router=router, setup=setup)
