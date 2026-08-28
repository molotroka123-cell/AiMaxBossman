"""V2.1 фаза E — Память/Obsidian как РЕАЛЬНЫЕ инструменты модели.

Что здесь есть:
  * канонические инструменты в `bcc.tools.REGISTRY` с `source="memory"`:
    `memory.search`, `memory.expand`, `memory.write`, `memory.index`,
    `memory.stats`;
  * HTTP `/api/memory/*` для человека (настройка vault, ручной поиск/индекс).

Правила, которые здесь соблюдаются буквально:
  * Путь к vault задаёт ТОЛЬКО человек — `settings_kv["memory.vault"]`
    (значение зашифровано `svc.vault`, как `terminal.roots`). Никакого
    автопоиска Obsidian по машине: чужой vault — это личные данные.
  * Память НЕ подмешивается в каждый вызов модели. Никакого хука инъекции
    контекста здесь нет и быть не должно: извлечение происходит ровно тогда,
    когда агент сам вызвал `memory.search`.
  * Результат из vault — ВНЕШНИЕ ДАННЫЕ (`external_output=True`): в заметке
    может лежать текст «сделай X», и это не команда.
  * `memory.write` пишет в пользовательский vault → `default_effect="ask"`
    плюс право `filesystem.write` (выдано право — идёт без вопроса).
  * Бюджет памяти считается ОТДЕЛЬНО от контекста задачи: `context_tokens`
    ограничивает только пакет памяти.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2.memory import (
    LexicalReranker,
    LocalMemoryBackend,
    MemSearchBridge,
    ObsidianMemoryService,
    ObsidianVault,
)
from . import Feature

CONFIG_KEY = "memory.vault"
DEFAULT_WRITE_FOLDER = "BOSSMAN Memory"
DEFAULT_CONTEXT_TOKENS = 4000       # бюджет ПАМЯТИ, не задачи
MAX_CONTEXT_TOKENS = 12000

router = APIRouter()


class MemoryNotConfigured(RuntimeError):
    pass


# ------------------------------------------------------------------ конфиг

async def load_config(svc) -> dict:
    """Конфиг памяти из settings_kv. Пусто → память не настроена."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == CONFIG_KEY))).first()
    if not row or not row[0]:
        return {}
    try:
        cfg = json.loads(svc.vault.decrypt(row[0]))
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


async def save_config(svc, cfg: dict) -> dict:
    enc = svc.vault.encrypt(json.dumps(cfg, ensure_ascii=False))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CONFIG_KEY))
        await s.execute(sa.insert(settings_kv).values(key=CONFIG_KEY, value_enc=enc))
        await s.commit()
    svc._memory_cache = None          # конфиг сменился — пересобрать сервис
    return cfg


def _fingerprint(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()
                          ).hexdigest()[:16]


def build_service(svc, cfg: dict) -> ObsidianMemoryService:
    """Собрать сервис памяти по конфигу. Backend выбирается ЯВНО:
    `local` (встроенный BM25, всегда доступен) или `memsearch` (внешний бинарь,
    если он реально установлен). `auto` = memsearch при наличии, иначе local."""
    root = cfg.get("root")
    if not root:
        raise MemoryNotConfigured(
            "память не настроена: задайте путь к Obsidian vault через "
            "POST /api/memory/config {\"root\": \"/путь/к/vault\"}")
    vault = ObsidianVault(
        root=Path(str(root)),
        index_folders=[str(x) for x in (cfg.get("index_folders") or ["."])],
        write_folder=str(cfg.get("write_folder") or DEFAULT_WRITE_FOLDER),
    )
    if cfg.get("excludes"):
        vault.excluded_dirs |= {str(x) for x in cfg["excludes"]}

    want = str(cfg.get("backend") or "auto")
    backend = None
    if want in ("auto", "memsearch"):
        bridge = MemSearchBridge(**(cfg.get("memsearch") or {}))
        if bridge.available():
            backend = bridge
        elif want == "memsearch":
            raise MemoryNotConfigured(
                "backend=memsearch выбран, но бинарь `memsearch` не найден в PATH; "
                "используйте backend=local")
    if backend is None:
        index_dir = Path(svc.settings.data_dir) / "memory"
        backend = LocalMemoryBackend(
            index_path=index_dir / f"index-{_fingerprint({'root': str(vault.root)})}.json",
            vault_root=vault.root,
            excluded_dirs=set(vault.excluded_dirs),
        )
    return ObsidianMemoryService(vault=vault, backend=backend,
                                 reranker=LexicalReranker())


async def get_service(svc) -> ObsidianMemoryService:
    """Кэшируем сервис на Services; при смене конфига пересобираем."""
    cfg = await load_config(svc)
    fp = _fingerprint(cfg)
    cached = getattr(svc, "_memory_cache", None)
    if cached and cached[0] == fp:
        return cached[1]
    service = build_service(svc, cfg)
    svc._memory_cache = (fp, service)
    return service


# ------------------------------------------------------------------ HTTP

@router.get("/memory/config")
async def get_config(request: Request):
    svc = request.app.state.svc
    cfg = await load_config(svc)
    backend = ""
    if cfg.get("root"):
        try:
            backend = type((await get_service(svc)).backend).__name__
        except Exception as exc:
            backend = f"error: {exc}"
    return {"configured": bool(cfg.get("root")),
            "root": cfg.get("root", ""),
            "index_folders": cfg.get("index_folders") or ["."],
            "write_folder": cfg.get("write_folder") or DEFAULT_WRITE_FOLDER,
            "backend": cfg.get("backend") or "auto",
            "backend_class": backend}


@router.post("/memory/config")
async def set_config(request: Request):
    """Явная настройка человеком. Автопоиска vault'ов НЕТ намеренно."""
    svc = request.app.state.svc
    body = await request.json()
    root = str(body.get("root") or "").strip()
    if not root:
        raise HTTPException(400, {"message": "нужен путь к vault (root)"})
    p = Path(root).expanduser()
    if not p.is_dir():
        raise HTTPException(400, {"message": f"каталог не найден: {p}"})
    cfg = {
        "root": str(p),
        "index_folders": [str(x) for x in (body.get("index_folders") or ["."])],
        "write_folder": str(body.get("write_folder") or DEFAULT_WRITE_FOLDER),
        "backend": str(body.get("backend") or "auto"),
        "excludes": [str(x) for x in (body.get("excludes") or [])],
    }
    if body.get("memsearch"):
        cfg["memsearch"] = dict(body["memsearch"])
    try:                              # проверяем ДО сохранения — не оставляем битый конфиг
        build_service(svc, cfg)
    except (MemoryNotConfigured, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(400, {"message": str(exc)})
    await save_config(svc, cfg)
    return await get_config(request)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(503, {"message": str(exc),
                               "hint": "настройте vault: POST /api/memory/config"})


@router.post("/memory/index")
async def http_index(request: Request):
    svc = request.app.state.svc
    body = await request.json() if await request.body() else {}
    try:
        service = await get_service(svc)
        return {"result": await service.index(force=bool(body.get("force")))}
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        raise _unavailable(exc)


@router.post("/memory/search")
async def http_search(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, {"message": "пустой запрос"})
    try:
        service = await get_service(svc)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        raise _unavailable(exc)
    pack = await service.search(
        query,
        candidate_k=int(body.get("candidate_k") or 16),
        rerank_k=int(body.get("rerank_k") or 8),
        context_tokens=min(int(body.get("max_context_tokens")
                               or DEFAULT_CONTEXT_TOKENS), MAX_CONTEXT_TOKENS))
    return {"query": pack.query, "estimated_tokens": pack.estimated_tokens,
            "items": [{"source": i.source, "heading": i.heading, "score": i.score,
                       "chunk_hash": i.chunk_hash, "content": i.content}
                      for i in pack.items]}


@router.post("/memory/expand")
async def http_expand(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    try:
        service = await get_service(svc)
        return await service.expand(str(body.get("chunk_hash") or ""))
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        raise _unavailable(exc)
    except KeyError as exc:
        raise HTTPException(404, {"message": str(exc)})


@router.post("/memory/write")
async def http_write(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    try:
        service = await get_service(svc)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        raise _unavailable(exc)
    try:
        path = await service.remember(
            title=str(body.get("title") or "").strip() or "memory",
            content=str(body.get("content") or ""),
            kind=str(body.get("kind") or "note"),
            project=str(body.get("project") or ""),
            tags=[str(t) for t in (body.get("tags") or [])])
    except PermissionError as exc:
        raise HTTPException(403, {"message": str(exc)})
    except FileExistsError as exc:
        raise HTTPException(409, {"message": f"заметка уже существует: {exc}"})
    return {"path": str(path)}


@router.get("/memory/stats")
async def http_stats(request: Request):
    svc = request.app.state.svc
    try:
        service = await get_service(svc)
        return {"stats": await service.stats()}
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        raise _unavailable(exc)


# ------------------------------------------------------------------ инструменты

def _err(message: str) -> ToolResult:
    return ToolResult(content=message, one_line=message[:120], error=True)


async def _svc_service(ctx: ToolContext):
    return await get_service(ctx.svc)


async def tool_search(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("memory.search: нужен непустой query")
    try:
        service = await _svc_service(ctx)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        return _err(f"память недоступна: {exc}")
    budget = min(int(args.get("max_context_tokens") or DEFAULT_CONTEXT_TOKENS),
                 MAX_CONTEXT_TOKENS)
    top_k = max(4, min(int(args.get("top_k") or 16), 40))
    pack = await service.search(query, candidate_k=top_k, rerank_k=8,
                                context_tokens=budget)
    if not pack.items:
        return ToolResult(content="в памяти ничего не найдено по этому запросу",
                          one_line="memory.search: 0 результатов",
                          data={"items": 0})
    header = ("Найдено в памяти (цитируйте источник; чтобы получить секцию "
              "целиком — memory.expand по chunk_hash):\n")
    body = "\n\n---\n\n".join(
        f"[{i + 1}] источник: {it.source}"
        + (f" — {it.heading}" if it.heading else "")
        + f" | chunk_hash: {it.chunk_hash}\n{it.content}"
        for i, it in enumerate(pack.items))
    return ToolResult(
        content=header + body,
        one_line=f"memory.search: {len(pack.items)} фрагм., ~{pack.estimated_tokens} т.",
        data={"items": len(pack.items), "estimated_tokens": pack.estimated_tokens,
              "sources": [it.source for it in pack.items],
              "chunk_hashes": [it.chunk_hash for it in pack.items]})


async def tool_expand(args: dict, ctx: ToolContext) -> ToolResult:
    chunk_hash = str(args.get("chunk_hash") or "").strip()
    if not chunk_hash:
        return _err("memory.expand: нужен chunk_hash из memory.search")
    try:
        service = await _svc_service(ctx)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        return _err(f"память недоступна: {exc}")
    try:
        detail = await service.expand(chunk_hash)
    except KeyError:
        return _err(f"memory.expand: фрагмент {chunk_hash} не найден (переиндексируйте)")
    source = str(detail.get("source") or "")
    heading = str(detail.get("heading") or "")
    content = str(detail.get("content") or "")
    return ToolResult(
        content=f"источник: {source}" + (f" — {heading}" if heading else "") + f"\n{content}",
        one_line=f"memory.expand: {source}",
        data={"source": source, "heading": heading})


async def tool_write(args: dict, ctx: ToolContext) -> ToolResult:
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    if not title or not content:
        return _err("memory.write: нужны title и content")
    try:
        service = await _svc_service(ctx)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        return _err(f"память недоступна: {exc}")
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        path = await service.remember(
            title=title, content=content,
            kind=str(args.get("kind") or "note"),
            project=str(args.get("project") or ""),
            tags=[str(t) for t in tags],
            source_run_id=ctx.run_id,
            filename=(str(args["filename"]) if args.get("filename") else None))
    except PermissionError as exc:
        return _err(f"memory.write отклонён: запись только внутрь "
                    f"{service.vault.write_folder}/ ({exc})")
    except FileExistsError as exc:
        return _err(f"memory.write: заметка уже существует ({exc})")
    try:
        rel = path.relative_to(service.vault.root).as_posix()
    except ValueError:                # недостижимо: vault уже проверил границы
        return _err("memory.write: путь вне vault")
    await ctx.svc.bus.emit("agent.tool_call", tool="memory.write", path=rel,
                           run_id=ctx.run_id)
    return ToolResult(content=f"заметка сохранена: {rel}",
                      one_line=f"memory.write: {rel}",
                      data={"path": rel, "absolute": str(path)})


async def tool_index(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        service = await _svc_service(ctx)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        return _err(f"память недоступна: {exc}")
    result = await service.index(force=bool(args.get("force")))
    return ToolResult(content=f"индекс обновлён: {result}",
                      one_line="memory.index: ок",
                      data=result if isinstance(result, dict) else {"raw": str(result)})


async def tool_stats(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        service = await _svc_service(ctx)
    except (MemoryNotConfigured, FileNotFoundError) as exc:
        return _err(f"память недоступна: {exc}")
    stats = await service.stats()
    return ToolResult(content=f"память: {stats}", one_line="memory.stats: ок",
                      data=stats if isinstance(stats, dict) else {"raw": str(stats)})


SPECS = [
    ToolSpec(
        name="memory.search", handler=tool_search, source="memory", category="read",
        default_effect="auto", external_output=True, timeout_seconds=60.0,
        description=("Искать в памяти проекта (Obsidian vault + заметки BOSSMAN). "
                     "Вызывать, когда задача опирается на прошлые решения, историю "
                     "проекта или договорённости — но НЕ на каждом шаге."),
        input_schema={
            "query": {"type": "string", "description": "вопрос на естественном языке"},
            "max_context_tokens": {"type": "integer",
                                   "description": f"бюджет памяти, по умолчанию "
                                                  f"{DEFAULT_CONTEXT_TOKENS}"},
            "top_k": {"type": "integer", "description": "сколько кандидатов брать (12–20)"},
        },
        required=["query"]),
    ToolSpec(
        name="memory.expand", handler=tool_expand, source="memory", category="read",
        default_effect="auto", external_output=True, timeout_seconds=60.0,
        description="Показать секцию заметки целиком по chunk_hash из memory.search.",
        input_schema={"chunk_hash": {"type": "string"}}, required=["chunk_hash"]),
    ToolSpec(
        name="memory.write", handler=tool_write, source="memory", category="write",
        permission="filesystem.write", default_effect="ask", idempotent=False,
        timeout_seconds=60.0,
        description=("Сохранить durable-заметку в папку BOSSMAN Memory внутри vault. "
                     "Только выводы, решения и уроки — не сырой лог и не секреты."),
        input_schema={
            "title": {"type": "string"},
            "content": {"type": "string", "description": "markdown-тело заметки"},
            "kind": {"type": "string",
                     "enum": ["decision", "lesson", "fact", "task", "session", "note"]},
            "project": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=["title", "content"]),
    ToolSpec(
        name="memory.index", handler=tool_index, source="memory", category="read",
        default_effect="auto", timeout_seconds=600.0,
        description="Переиндексировать vault (инкрементально по хэшу содержимого).",
        input_schema={"force": {"type": "boolean"}}),
    ToolSpec(
        name="memory.stats", handler=tool_stats, source="memory", category="read",
        default_effect="auto", timeout_seconds=30.0,
        description="Состояние индекса памяти: файлы, фрагменты, backend.",
        input_schema={}),
]


async def setup(svc) -> None:
    svc._memory_cache = None
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_memory", router=router, setup=setup)
