"""Projects, project files and the memory browser for the phone (owner 31.10: «функция проекты», «хороший файл
менеджер памяти»).

A fixed allowlist of /m/api routes proxied to the SAME Bossman — nothing is stored or decided here:

  GET|POST /m/api/projects, PATCH /m/api/projects/{slug}          -> /api/projects (features/chat_projects)
  GET  /m/api/projects/{slug}/files?path=                          -> list one folder of the project
  GET  /m/api/projects/{slug}/files/download?path=&inline=         -> streamed file (nosniff; html/svg never inline)
  POST /m/api/projects/{slug}/files?filename=&dir=&overwrite=      -> streamed upload into the project folder
  DELETE /m/api/projects/{slug}/files?path=&confirm=<name>         -> delete a file / empty folder (typed-name confirm)
  GET  /m/api/memory/status                                        -> is memory set up (no vault path to the phone)
  GET  /m/api/memory/facts?query=                                  -> /api/memory/facts (current facts)
  POST /m/api/memory/facts/{id}/forget                             -> /api/memory/facts/revoke (fact ends, history kept)
  POST /m/api/memory/search, /m/api/memory/expand                  -> /api/memory/search|expand (Obsidian notes, read)

Bossman confines every path to the project folder and refuses secrets / its own data; the gateway additionally
validates ids and paths before anything reaches Bossman, so a refused request never travels.
"""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .bossman import BossmanUnavailable

SLUG = re.compile(r"^(?:_inbox|[^\W_][\w-]{0,39})$", re.UNICODE)
MAX_PATH = 1000


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


class MemorySearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class MemoryExpandIn(BaseModel):
    chunk_hash: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")


def register(app: FastAPI, *, call: Callable, session: Callable, fail: Callable, bm: Any) -> None:
    """Add the routes; must run before the /m/api catch-all 404 route."""

    def slug_of(slug: str) -> str:
        if not SLUG.fullmatch(slug or ""):
            raise fail(404, "проект не найден", "BM_NOT_FOUND")
        return quote(slug, safe="")

    def rel_of(path: str | None, *, required: bool = False) -> str:
        text = str(path or "")
        if required and not text.strip():
            raise fail(422, "не указан путь", "BM_PATH")
        if len(text) > MAX_PATH or "\x00" in text:
            raise fail(422, "слишком длинный путь", "BM_PATH")
        norm = text.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in norm.split("/"):
            raise fail(422, "путь должен быть внутри папки проекта", "BM_PATH")
        return text

    # ------------------------------------------------------------------ projects
    @app.get("/m/api/projects")
    async def m_projects(archived: bool = False, _=Depends(session)):
        return await call("GET", "/api/projects", params={"archived": "true"} if archived else None)

    @app.post("/m/api/projects", status_code=201)
    async def m_project_create(body: ProjectIn, _=Depends(session)):
        return await call("POST", "/api/projects", json={"name": body.name})

    @app.patch("/m/api/projects/{slug}")
    async def m_project_patch(slug: str, body: ProjectPatchIn, _=Depends(session)):
        return await call("PATCH", f"/api/projects/{slug_of(slug)}", json=body.model_dump(exclude_unset=True))

    # ------------------------------------------------------------------ files
    @app.get("/m/api/projects/{slug}/files")
    async def m_project_files(slug: str, path: str | None = None, _=Depends(session)):
        params = {"path": rel_of(path)} if path else None
        return await call("GET", f"/api/projects/{slug_of(slug)}/files", params=params)

    @app.get("/m/api/projects/{slug}/files/download")
    async def m_project_download(slug: str, path: str, inline: bool = False, _=Depends(session)):
        params: dict[str, Any] = {"path": rel_of(path, required=True)}
        if inline:
            params["inline"] = "true"
        try:
            r = await bm.send("GET", f"/api/projects/{slug_of(slug)}/files/download", params=params, stream=True,
                              timeout=httpx.Timeout(60, read=3600))
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE") from None
        if r.status_code != 200:
            await r.aread()
            await r.aclose()
            try:
                err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            except ValueError:
                err = {}
            raise fail(r.status_code if r.status_code in (403, 404, 422) else 502,
                       err.get("message") or "файл недоступен", err.get("code") or "BM_NOT_FOUND")
        out = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        for k in ("content-disposition", "content-length"):
            if r.headers.get(k):
                out[k] = r.headers[k]

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()

        return StreamingResponse(gen(), media_type=r.headers.get("content-type", "application/octet-stream"),
                                 headers=out)

    @app.post("/m/api/projects/{slug}/files", status_code=201)
    async def m_project_upload(slug: str, request: Request, filename: str, dir: str | None = None,
                               overwrite: bool = False, _=Depends(session)):
        name = filename.strip()[:255]
        if not name:
            raise fail(422, "не указано имя файла", "BM_PATH")
        params: dict[str, Any] = {"filename": name}
        if dir:
            params["dir"] = rel_of(dir)
        if overwrite:
            params["overwrite"] = "true"
        hdrs = {"content-type": "application/octet-stream"}
        if request.headers.get("content-length", "").isdigit():
            hdrs["content-length"] = request.headers["content-length"]
        try:
            r = await bm.send("POST", f"/api/projects/{slug_of(slug)}/files", params=params, content=request.stream(),
                              headers=hdrs, timeout=httpx.Timeout(60, read=3600, write=3600))
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE") from None
        try:
            res = r.json()
        except ValueError:
            res = {}
        if r.status_code >= 400:
            err = (res.get("error") or res.get("detail") or {}) if isinstance(res, dict) else {}
            err = err if isinstance(err, dict) else {"message": str(err)}
            raise fail(r.status_code, err.get("message") or "файл не принят", err.get("code") or "BM_UPLOAD",
                       err.get("hint") or "")
        keep = ("ok", "project", "name", "path", "size", "sha256", "mtime")
        return {k: res.get(k) for k in keep if isinstance(res, dict) and k in res}

    @app.delete("/m/api/projects/{slug}/files")
    async def m_project_delete(slug: str, path: str, confirm: str = "", _=Depends(session)):
        if not confirm:
            raise fail(422, "удаление требует подтверждения", "BM_CONFIRM")
        return await call("DELETE", f"/api/projects/{slug_of(slug)}/files",
                          params={"path": rel_of(path, required=True), "confirm": confirm[:255]})

    # ------------------------------------------------------------------ memory (the existing memory API only)
    @app.get("/m/api/memory/status")
    async def m_memory_status(_=Depends(session)):
        cfg = await call("GET", "/api/memory/config")
        return {"configured": bool(cfg.get("configured")), "backend": cfg.get("backend") or "",
                "write_folder": cfg.get("write_folder") or ""}

    @app.get("/m/api/memory/facts")
    async def m_memory_facts(query: str = "", limit: int = 50, _=Depends(session)):
        return await call("GET", "/api/memory/facts",
                          params={"query": query[:300], "limit": max(1, min(int(limit), 200)), "current_only": "true"})

    @app.post("/m/api/memory/facts/{fact_id}/forget")
    async def m_memory_forget(fact_id: int, _=Depends(session)):
        if fact_id <= 0:
            raise fail(404, "факт не найден", "BM_NOT_FOUND")
        return await call("POST", "/api/memory/facts/revoke", json={"fact_id": int(fact_id)})

    @app.post("/m/api/memory/search")
    async def m_memory_search(body: MemorySearchIn, _=Depends(session)):
        return await call("POST", "/api/memory/search", json={"query": body.query, "candidate_k": 16, "rerank_k": 8},
                          timeout=60)

    @app.post("/m/api/memory/expand")
    async def m_memory_expand(body: MemoryExpandIn, _=Depends(session)):
        return await call("POST", "/api/memory/expand", json={"chunk_hash": body.chunk_hash})
