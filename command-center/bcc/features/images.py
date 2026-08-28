"""BOSSMAN Images — library + generation jobs + collections + templates.

This is a native V2 feature:
- discovered automatically by bcc.features.load_features()
- router mounted automatically under /api with BOSSMAN token auth
- background `tick` processes queued image jobs
- tables register on canonical db.metadata before create_all()
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db import models as models_t, rows_dicts, utcnow
from ..v2.images_runtime import ImageStorage, MockImageProvider, safe_filename
from ..v2.images_tables import (
    image_assets as assets_t,
    image_collections as collections_t,
    image_jobs as jobs_t,
    image_templates as templates_t,
)
from . import Feature

router = APIRouter()
PROVIDER = MockImageProvider()


# ---------- request models ----------

class ImageJobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = ""
    model_alias: str = "mock-image"
    aspect_ratio: str = "1:1"
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int = Field(default=30, ge=1, le=200)
    seed: int | None = None
    count: int = Field(default=1, ge=1, le=8)
    collection_id: int | None = None
    source_asset_id: int | None = None
    reference_asset_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    kind: str = "generate"
    options: dict[str, Any] = Field(default_factory=dict)


class AssetPatch(BaseModel):
    title: str | None = None
    favorite: bool | None = None
    status: str | None = None
    collection_id: int | None = None
    tags: list[str] | None = None


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    parent_id: int | None = None


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = ""
    model_alias: str = "mock-image"
    aspect_ratio: str = "1:1"
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int = Field(default=30, ge=1, le=200)
    options: dict[str, Any] = Field(default_factory=dict)


class ImportAssetIn(BaseModel):
    filename: str
    data_base64: str
    title: str = ""
    collection_id: int | None = None
    tags: list[str] = Field(default_factory=list)


# ---------- helpers ----------

def _storage(svc) -> ImageStorage:
    return ImageStorage(svc.settings.data_dir / "images")


def _clean_tags(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = str(raw).strip()[:80]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:50]


def _job_public(row: dict) -> dict:
    out = dict(row)
    out["reference_asset_ids"] = list(out.get("reference_asset_ids") or [])
    out["tags"] = list(out.get("tags") or [])
    out["options"] = dict(out.get("options") or {})
    return out


def _asset_public(row: dict) -> dict:
    out = dict(row)
    out["tags"] = list(out.get("tags") or [])
    out["meta"] = dict(out.get("meta") or {})
    out["file_url"] = f"/api/images/assets/{out['id']}/file"
    return out


async def _find_one(svc, table, row_id: int) -> dict | None:
    async with svc.db.session() as s:
        res = await s.execute(sa.select(table).where(table.c.id == row_id))
        row = res.first()
    return dict(row._mapping) if row else None


async def _collection_exists(svc, collection_id: int | None) -> bool:
    if collection_id is None:
        return True
    return await _find_one(svc, collections_t, collection_id) is not None


async def _asset_exists(svc, asset_id: int | None) -> bool:
    if asset_id is None:
        return True
    return await _find_one(svc, assets_t, asset_id) is not None


# ---------- catalog / overview ----------

@router.get("/images/overview")
async def images_overview(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        assets = int((await s.execute(sa.select(sa.func.count()).select_from(assets_t)
                                      .where(assets_t.c.status != "deleted"))).scalar_one())
        fav = int((await s.execute(sa.select(sa.func.count()).select_from(assets_t)
                                   .where(assets_t.c.favorite.is_(True),
                                          assets_t.c.status != "deleted"))).scalar_one())
        queued = int((await s.execute(sa.select(sa.func.count()).select_from(jobs_t)
                                      .where(jobs_t.c.status.in_(("queued", "running"))))).scalar_one())
        failed = int((await s.execute(sa.select(sa.func.count()).select_from(jobs_t)
                                      .where(jobs_t.c.status == "failed"))).scalar_one())
    return {"assets": assets, "favorites": fav, "active_jobs": queued, "failed_jobs": failed}


@router.get("/images/models")
async def image_models(request: Request):
    """Return mock plus BOSSMAN models explicitly advertising image-generation caps."""
    svc = request.app.state.svc
    out = [{
        "alias": "mock-image",
        "name": "BOSSMAN Mock Image",
        "provider": "local",
        "status": "ok",
        "caps": {"image_generation": True, "mock": True},
    }]
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(sa.select(models_t))).fetchall())
    for m in rows:
        caps = dict(m.get("caps") or {})
        if any(caps.get(k) for k in ("image_generation", "image", "images", "text_to_image")):
            out.append({
                "alias": m.get("alias") or m.get("name"),
                "name": m.get("name") or m.get("alias"),
                "provider_id": m.get("provider_id"),
                "status": m.get("status"),
                "caps": caps,
            })
    return out


# ---------- assets ----------

@router.get("/images/assets")
async def list_assets(request: Request, search: str = "", collection_id: int | None = None,
                      favorite: bool | None = None, status: str = "ready",
                      limit: int = 200, offset: int = 0):
    svc = request.app.state.svc
    stmt = sa.select(assets_t)
    if status:
        stmt = stmt.where(assets_t.c.status == status)
    if collection_id is not None:
        stmt = stmt.where(assets_t.c.collection_id == collection_id)
    if favorite is not None:
        stmt = stmt.where(assets_t.c.favorite == favorite)
    if search:
        q = f"%{search.lower()}%"
        stmt = stmt.where(sa.or_(
            sa.func.lower(assets_t.c.title).like(q),
            sa.func.lower(assets_t.c.prompt).like(q),
            sa.func.lower(assets_t.c.model_alias).like(q),
        ))
    stmt = stmt.order_by(assets_t.c.id.desc()).limit(min(max(limit, 1), 500)).offset(max(offset, 0))
    count_stmt = sa.select(sa.func.count()).select_from(assets_t)
    if status:
        count_stmt = count_stmt.where(assets_t.c.status == status)
    if collection_id is not None:
        count_stmt = count_stmt.where(assets_t.c.collection_id == collection_id)
    if favorite is not None:
        count_stmt = count_stmt.where(assets_t.c.favorite == favorite)
    if search:
        q = f"%{search.lower()}%"
        count_stmt = count_stmt.where(sa.or_(
            sa.func.lower(assets_t.c.title).like(q),
            sa.func.lower(assets_t.c.prompt).like(q),
            sa.func.lower(assets_t.c.model_alias).like(q),
        ))
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(stmt)).fetchall())
        total = int((await s.execute(count_stmt)).scalar_one())
    return {"items": [_asset_public(x) for x in rows], "total": total}


@router.get("/images/assets/{asset_id}")
async def get_asset(asset_id: int, request: Request):
    row = await _find_one(request.app.state.svc, assets_t, asset_id)
    if row is None:
        raise HTTPException(404, {"message": "изображение не найдено"})
    return _asset_public(row)


@router.get("/images/assets/{asset_id}/file")
async def get_asset_file(asset_id: int, request: Request):
    svc = request.app.state.svc
    row = await _find_one(svc, assets_t, asset_id)
    if row is None:
        raise HTTPException(404, {"message": "изображение не найдено"})
    try:
        path = _storage(svc).resolve_existing(row["file_path"])
    except FileNotFoundError:
        raise HTTPException(404, {"message": "файл изображения отсутствует"})
    except PermissionError:
        raise HTTPException(403, {"message": "небезопасный путь изображения"})
    return FileResponse(path, media_type=row.get("mime_type") or _storage(svc).mime_for(path),
                        filename=path.name, headers={"Cache-Control": "private, max-age=60"})


@router.patch("/images/assets/{asset_id}")
async def patch_asset(asset_id: int, body: AssetPatch, request: Request):
    svc = request.app.state.svc
    if await _find_one(svc, assets_t, asset_id) is None:
        raise HTTPException(404, {"message": "изображение не найдено"})
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if "tags" in patch:
        patch["tags"] = _clean_tags(patch["tags"])
    if "collection_id" in patch and not await _collection_exists(svc, patch["collection_id"]):
        raise HTTPException(404, {"message": "коллекция не найдена"})
    if "status" in patch and patch["status"] not in ("ready", "archived", "deleted"):
        raise HTTPException(422, {"message": "status: ready|archived|deleted"})
    async with svc.db.session() as s:
        await s.execute(sa.update(assets_t).where(assets_t.c.id == asset_id).values(**patch))
        await s.commit()
    await svc.bus.emit("image.asset.updated", asset_id=asset_id)
    return _asset_public((await _find_one(svc, assets_t, asset_id)) or {})


@router.post("/images/assets/import")
async def import_asset(body: ImportAssetIn, request: Request):
    svc = request.app.state.svc
    if not await _collection_exists(svc, body.collection_id):
        raise HTTPException(404, {"message": "коллекция не найдена"})
    try:
        raw = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, {"message": "data_base64 повреждён"})
    if not raw:
        raise HTTPException(422, {"message": "пустой файл"})
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, {"message": "максимум 15 MB на импорт"})
    suffix = Path(body.filename).suffix.lower()
    allowed = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}
    if suffix not in allowed:
        raise HTTPException(422, {"message": "поддерживаются PNG/JPG/WEBP/GIF/SVG"})
    name = f"import-{secrets.token_hex(6)}-{safe_filename(Path(body.filename).stem)}{suffix}"
    path = _storage(svc).save(f"imports/{name}", raw)
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(assets_t).values(
            title=(body.title.strip() or Path(body.filename).stem)[:240],
            model_alias="import",
            file_path=str(path),
            file_bytes=len(raw),
            mime_type=allowed[suffix],
            tags=_clean_tags(body.tags),
            collection_id=body.collection_id,
            meta={"imported": True, "original_filename": body.filename},
            created_at=utcnow(),
        ))
        asset_id = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("image.asset.created", asset_id=asset_id, imported=True)
    return _asset_public((await _find_one(svc, assets_t, asset_id)) or {})


# ---------- jobs ----------

@router.get("/images/jobs")
async def list_jobs(request: Request, status: str = "", limit: int = 100):
    svc = request.app.state.svc
    stmt = sa.select(jobs_t)
    if status:
        stmt = stmt.where(jobs_t.c.status == status)
    stmt = stmt.order_by(jobs_t.c.id.desc()).limit(min(max(limit, 1), 300))
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(stmt)).fetchall())
    return {"items": [_job_public(x) for x in rows], "total": len(rows)}


@router.get("/images/jobs/{job_id}")
async def get_job(job_id: int, request: Request):
    row = await _find_one(request.app.state.svc, jobs_t, job_id)
    if row is None:
        raise HTTPException(404, {"message": "задача генерации не найдена"})
    return _job_public(row)


@router.post("/images/jobs")
async def create_job(body: ImageJobIn, request: Request):
    svc = request.app.state.svc
    if not await _collection_exists(svc, body.collection_id):
        raise HTTPException(404, {"message": "коллекция не найдена"})
    if not await _asset_exists(svc, body.source_asset_id):
        raise HTTPException(404, {"message": "исходное изображение не найдено"})
    for aid in body.reference_asset_ids:
        if not await _asset_exists(svc, aid):
            raise HTTPException(404, {"message": f"reference asset #{aid} не найден"})
    seed = body.seed if body.seed is not None else secrets.randbelow(2_147_483_646) + 1
    now = utcnow()
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(jobs_t).values(
            kind=body.kind,
            status="queued",
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            model_alias=body.model_alias,
            aspect_ratio=body.aspect_ratio,
            width=body.width,
            height=body.height,
            steps=body.steps,
            seed=seed,
            count=body.count,
            collection_id=body.collection_id,
            source_asset_id=body.source_asset_id,
            reference_asset_ids=list(dict.fromkeys(body.reference_asset_ids)),
            tags=_clean_tags(body.tags),
            options=body.options,
            progress=0.0,
            created_at=now,
            updated_at=now,
        ))
        job_id = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("image.job.queued", job_id=job_id, model=body.model_alias)
    return _job_public((await _find_one(svc, jobs_t, job_id)) or {})


@router.post("/images/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, request: Request):
    svc = request.app.state.svc
    row = await _find_one(svc, jobs_t, job_id)
    if row is None:
        raise HTTPException(404, {"message": "задача генерации не найдена"})
    if row["status"] in ("completed", "failed", "cancelled"):
        return _job_public(row)
    async with svc.db.session() as s:
        await s.execute(sa.update(jobs_t).where(jobs_t.c.id == job_id).values(
            status="cancelled", finished_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("image.job.cancelled", job_id=job_id)
    return _job_public((await _find_one(svc, jobs_t, job_id)) or {})


@router.post("/images/jobs/{job_id}/retry")
async def retry_job(job_id: int, request: Request):
    svc = request.app.state.svc
    old = await _find_one(svc, jobs_t, job_id)
    if old is None:
        raise HTTPException(404, {"message": "задача генерации не найдена"})
    clone = ImageJobIn(
        prompt=old["prompt"],
        negative_prompt=old.get("negative_prompt") or "",
        model_alias=old.get("model_alias") or "mock-image",
        aspect_ratio=old.get("aspect_ratio") or "1:1",
        width=int(old.get("width") or 1024),
        height=int(old.get("height") or 1024),
        steps=int(old.get("steps") or 30),
        seed=old.get("seed"),
        count=int(old.get("count") or 1),
        collection_id=old.get("collection_id"),
        source_asset_id=old.get("source_asset_id"),
        reference_asset_ids=list(old.get("reference_asset_ids") or []),
        tags=list(old.get("tags") or []),
        kind=old.get("kind") or "generate",
        options=dict(old.get("options") or {}),
    )
    return await create_job(clone, request)


# ---------- collections ----------

@router.get("/images/collections")
async def list_collections(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(sa.select(collections_t)
                                           .order_by(collections_t.c.name.asc()))).fetchall())
        counts_res = await s.execute(sa.select(
            assets_t.c.collection_id, sa.func.count(assets_t.c.id)
        ).where(assets_t.c.status != "deleted").group_by(assets_t.c.collection_id))
        counts = {r[0]: int(r[1]) for r in counts_res.fetchall()}
    for row in rows:
        row["count"] = counts.get(row["id"], 0)
    return rows


@router.post("/images/collections")
async def create_collection(body: CollectionIn, request: Request):
    svc = request.app.state.svc
    if body.parent_id is not None and not await _collection_exists(svc, body.parent_id):
        raise HTTPException(404, {"message": "родительская коллекция не найдена"})
    try:
        async with svc.db.session() as s:
            res = await s.execute(sa.insert(collections_t).values(
                name=body.name.strip(), parent_id=body.parent_id, created_at=utcnow()))
            cid = int(res.inserted_primary_key[0])
            await s.commit()
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, {"message": "коллекция с таким именем уже есть"})
        raise
    await svc.bus.emit("image.collection.created", collection_id=cid)
    row = await _find_one(svc, collections_t, cid)
    return {**(row or {}), "count": 0}


# ---------- templates ----------

@router.get("/images/templates")
async def list_templates(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        return rows_dicts((await s.execute(sa.select(templates_t)
                                           .order_by(templates_t.c.id.desc()))).fetchall())


@router.post("/images/templates")
async def create_template(body: TemplateIn, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(templates_t).values(
            **body.model_dump(), created_at=utcnow()))
        tid = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("image.template.created", template_id=tid)
    return await _find_one(svc, templates_t, tid)


# ---------- storage ----------

@router.get("/images/storage")
async def storage_stats(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = rows_dicts((await s.execute(sa.select(
            assets_t.c.file_bytes, assets_t.c.status
        ))).fetchall())
    used = sum(int(x.get("file_bytes") or 0) for x in rows if x.get("status") != "deleted")
    archive = sum(int(x.get("file_bytes") or 0) for x in rows if x.get("status") == "archived")
    deleted = sum(int(x.get("file_bytes") or 0) for x in rows if x.get("status") == "deleted")
    return {
        "used_bytes": used,
        "library_bytes": used - archive,
        "archive_bytes": archive,
        "deleted_bytes": deleted,
        "asset_count": len(rows),
    }


# ---------- background queue ----------

async def process_one(svc) -> int | None:
    """Atomically claim and execute one queued image job. Returns job id or None."""
    now = utcnow()
    async with svc.db.session() as s:
        row = (await s.execute(
            sa.select(jobs_t.c.id).where(jobs_t.c.status == "queued")
            .order_by(jobs_t.c.id.asc()).limit(1)
        )).first()
        if row is None:
            return None
        job_id = int(row[0])
        upd = await s.execute(sa.update(jobs_t).where(
            jobs_t.c.id == job_id, jobs_t.c.status == "queued"
        ).values(status="running", progress=0.05, started_at=now, updated_at=now))
        await s.commit()
        if not upd.rowcount:
            return None

    await svc.bus.emit("image.job.started", job_id=job_id)
    job = await _find_one(svc, jobs_t, job_id)
    if job is None:
        return None

    # First pass provider policy:
    # only the deterministic mock provider is executable here.
    # Real image providers are added later behind the same contract.
    if job.get("model_alias") != "mock-image":
        await _fail_job(svc, job_id,
                        f"реальный image provider для «{job.get('model_alias')}» ещё не подключён")
        return job_id

    storage = _storage(svc)
    created: list[int] = []
    count = max(1, min(int(job.get("count") or 1), 8))
    try:
        for index in range(count):
            # honour cancellation between produced assets
            latest = await _find_one(svc, jobs_t, job_id)
            if latest is None or latest.get("status") == "cancelled":
                return job_id

            data, mime, meta = await PROVIDER.render(job, index)
            suffix = ".svg" if mime == "image/svg+xml" else ".bin"
            rel = f"generated/job-{job_id}/image-{index + 1}{suffix}"
            path = storage.save(rel, data)
            title = (str(job.get("prompt") or "Image").strip()[:120]
                     or f"Image {job_id}-{index + 1}")

            async with svc.db.session() as s:
                res = await s.execute(sa.insert(assets_t).values(
                    source_job_id=job_id,
                    title=title,
                    prompt=job.get("prompt") or "",
                    negative_prompt=job.get("negative_prompt") or "",
                    model_alias=job.get("model_alias") or "mock-image",
                    aspect_ratio=job.get("aspect_ratio") or "1:1",
                    width=int(job.get("width") or 1024),
                    height=int(job.get("height") or 1024),
                    seed=int(meta.get("seed") or job.get("seed") or 1),
                    mime_type=mime,
                    file_path=str(path),
                    file_bytes=len(data),
                    favorite=False,
                    status="ready",
                    collection_id=job.get("collection_id"),
                    tags=list(job.get("tags") or []),
                    meta=meta,
                    created_at=utcnow(),
                ))
                asset_id = int(res.inserted_primary_key[0])
                progress = 0.10 + (0.85 * ((index + 1) / count))
                await s.execute(sa.update(jobs_t).where(jobs_t.c.id == job_id).values(
                    progress=progress, updated_at=utcnow()))
                await s.commit()
            created.append(asset_id)
            await svc.bus.emit("image.asset.created", asset_id=asset_id, job_id=job_id)

        async with svc.db.session() as s:
            await s.execute(sa.update(jobs_t).where(jobs_t.c.id == job_id).values(
                status="completed", progress=1.0, finished_at=utcnow(), updated_at=utcnow(),
                options={**dict(job.get("options") or {}), "asset_ids": created},
            ))
            await s.commit()
        await svc.bus.emit("image.job.completed", job_id=job_id, asset_ids=created)
    except Exception as exc:
        await _fail_job(svc, job_id, f"{type(exc).__name__}: {exc}")
    return job_id


async def _fail_job(svc, job_id: int, message: str) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(jobs_t).where(jobs_t.c.id == job_id).values(
            status="failed", error=message[:2000], finished_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("image.job.failed", job_id=job_id, message=message[:500])


async def _tick(svc) -> None:
    await process_one(svc)


FEATURE = Feature(name="images", router=router, tick=_tick, tick_seconds=0.7)
