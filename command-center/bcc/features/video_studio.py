"""Authenticated Video Studio API; feature discovery imports tables before create_all."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa
from ..video_studio.service import VideoService, jobs, chats, identifier
from ..db import tasks as tasks_t
from . import Feature

router = APIRouter(prefix="/video-studio", tags=["video-studio"])
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Create(Strict):
    name: str = Field(default="Новый проект",max_length=160)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    links: dict[str, Any] = Field(default_factory=dict)
class Command(Strict):
    project_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    command: dict[str, Any]
    dry_run: bool = False
class Export(Strict):
    project_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    preview: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
class OtioImport(Strict):
    project_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    data: str = Field(max_length=32*1024*1024)
class Proposal(Strict):
    project_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    objective: str = Field(min_length=1,max_length=2000)
    clip_id: str
class Analysis(Strict):
    project_id: str
    media_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    action: str = "analyse"
    language: str = Field(default="auto",pattern=r"^(auto|[a-z]{2,3})$")
class Captions(Strict):
    project_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str
    text: str = Field(max_length=8000000)
    format: str = "srt"
class Relink(Strict):
    project_id: str
    media_id: str
    replacement_media_id: str
    expected_revision: int = Field(ge=0)
    operation_id: str
class Lease(Strict):
    object_ids: list[str] = Field(min_length=1,max_length=256)
    ttl_seconds: int = Field(default=30,ge=1,le=300)
class Chat(Strict):
    text: str = Field(min_length=1,max_length=12000)
    operation_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    project_id: str | None = None

def service(request):
    return request.app.state.svc.video_studio

async def guarded(call):
    try:
        return await call
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(403,"operation denied") from None
    except (KeyError,TypeError):
        raise HTTPException(422,"invalid or missing command fields") from None
    except ValueError as exc:
        from ..video_studio.model import Conflict, MissingObject
        if isinstance(exc, Conflict):
            raise HTTPException(409,{"message":str(exc)[:300],"code":exc.code}) from None
        if isinstance(exc, MissingObject):
            raise HTTPException(404,{"message":str(exc)[:300],"code":exc.code}) from None
        raise HTTPException(422,{"message":str(exc)[:300]}) from None
    except (RuntimeError, OSError, sa.exc.IntegrityError):
        raise HTTPException(409,"operation conflict or local resource unavailable") from None

@router.get("/capabilities")
async def capabilities(request: Request):
    from ..video_studio.media import capabilities
    import os
    from ..video_studio.analysis import generation_status
    data=capabilities()
    model=os.environ.get("BOSSMAN_VIDEO_ASR_MODEL","")
    data["transcription"]={"status":"AVAILABLE" if model and Path(model).is_file() else "BLOCKED",
                           "reason":"Host local model configured" if model and Path(model).is_file() else "No host-approved local ASR model configured"}
    data["generation"]=generation_status()
    return data

@router.get("/projects")
async def projects(request: Request,archived: bool=False):
    return {"projects":await guarded(service(request).store.list(archived=archived))}

@router.post("/projects")
async def create(body: Create, request: Request):
    import uuid
    identifier(body.operation_id)
    pid = uuid.uuid5(uuid.NAMESPACE_URL,"video-project:"+body.operation_id).hex
    return await guarded(service(request).store.create(pid,body.name,body.operation_id,links=body.links))

@router.get("/projects/{project_id}")
async def project(project_id: str,request: Request):
    return await guarded(service(request).store.get(project_id))

@router.get("/projects/{project_id}/history")
async def history(project_id: str,request: Request):
    return {"history":await guarded(service(request).store.history(project_id))}

@router.post("/projects/{project_id}/lease")
async def lease(project_id: str,body: Lease,request: Request):
    return await guarded(service(request).store.lease(project_id,body.object_ids,"human",body.ttl_seconds))

@router.post("/projects/{project_id}/lease/release")
@router.delete("/projects/{project_id}/lease")
async def release_lease(project_id: str,request: Request):
    return await guarded(service(request).store.release_lease(project_id,"human"))

@router.post("/commands")
async def command(body: Command,request: Request):
    return await guarded(service(request).command(body.model_dump()))

@router.post("/media")
async def upload(request: Request, project_id: str,filename: str,expected_revision: int,operation_id: str):
    return await guarded(service(request).upload(request,project_id,filename,expected_revision,operation_id))

@router.get("/media/{media_id}/file")
async def media_file(media_id: str,project_id: str,request: Request):
    path, media = await guarded(service(request).media_file(project_id,media_id))
    return FileResponse(path,filename=media["name"])

@router.get("/media/{media_id}/thumbnail")
async def thumbnail(media_id: str,project_id: str,request: Request):
    svc = service(request)
    path, media = await guarded(svc.media_file(project_id,media_id))
    artifacts = await guarded(svc.media.prepare(media))
    relative = artifacts.get("thumbnail")
    if not relative:
        raise HTTPException(404,"thumbnail unavailable")
    path = (svc.root / relative).resolve()
    if not path.is_relative_to(svc.root):
        raise HTTPException(403,"artifact denied")
    return FileResponse(path,media_type="image/jpeg")

@router.get("/projects/{project_id}/exports")
async def project_exports(project_id: str,request: Request):
    video=service(request)
    await guarded(video.store.get(project_id))
    async with video.svc.db.session() as s:
        ids=(await s.execute(sa.select(jobs.c.id).where(jobs.c.project_id==project_id)
                            .order_by(jobs.c.created_at.desc()).limit(100))).scalars().all()
    return {"jobs":[await video.job(jid) for jid in ids]}

@router.post("/proposals")
async def proposal(body: Proposal,request: Request):
    return await guarded(service(request).proposal(body.model_dump()))

@router.post("/portable")
async def portable(body: Export,request: Request):
    if body.preview or body.options:
        raise HTTPException(422,"portable package takes no render options")
    return await guarded(service(request).package(body.model_dump()))

@router.post("/analysis")
async def analysis(body: Analysis,request: Request):
    return await guarded(service(request).analysis(body.model_dump()))

@router.post("/media/relink")
async def relink(body: Relink,request: Request):
    return await guarded(service(request).relink(body.model_dump()))

@router.post("/captions/import")
async def caption_import(body: Captions,request: Request):
    from ..video_studio.analysis import parse_captions
    async def execute():
        captions=parse_captions(body.text,body.format)
        return await service(request).command({"project_id":body.project_id,"expected_revision":body.expected_revision,
            "operation_id":body.operation_id,"command":{"type":"captions.replace","captions":captions}})
    return await guarded(execute())

@router.get("/projects/{project_id}/captions")
async def caption_export(project_id: str,request: Request,format: str="srt"):
    from ..video_studio.analysis import export_captions
    from fastapi.responses import PlainTextResponse
    project=await guarded(service(request).store.get(project_id))
    return PlainTextResponse(export_captions(project["captions"],format))

@router.post("/otio/import")
async def otio_import(body: OtioImport,request: Request):
    try:
        return await guarded(service(request).import_otio(body.model_dump()))
    except ImportError:
        raise HTTPException(409,"OpenTimelineIO is not installed") from None

@router.get("/projects/{project_id}/otio")
async def otio_export(project_id: str,request: Request):
    import asyncio,json
    from ..video_studio.interchange import export_otio
    from fastapi.responses import Response
    project=await guarded(service(request).store.get(project_id))
    try:
        result=await asyncio.to_thread(export_otio,project)
    except ImportError:
        raise HTTPException(409,"OpenTimelineIO is not installed") from None
    return Response(result["data"],media_type="application/json",headers={
        "Content-Disposition":f'attachment; filename="{result["filename"]}"',
        "X-Bossman-Interchange-Warnings":str(len(result["warnings"])),
        "X-Bossman-Parity-Claim":"false"})

@router.get("/projects/{project_id}/versions/{revision}")
async def version(project_id: str,revision: int,request: Request):
    return await guarded(service(request).store.version(project_id,revision))

@router.get("/projects/{project_id}/compare")
async def compare(project_id: str,request: Request,left: int,right: int):
    a=await guarded(service(request).store.version(project_id,left))
    b=await guarded(service(request).store.version(project_id,right))
    return {"left":a,"right":b,"changed_fields":[key for key in a if a.get(key)!=b.get(key)]}

@router.get("/projects/{project_id}/media-health")
async def media_health(project_id: str,request: Request):
    import asyncio
    video=service(request)
    project=await guarded(video.store.get(project_id))
    results=[]
    for media in project["media"].values():
        try:
            await asyncio.to_thread(video.media.resolve,media)
            results.append({"media_id":media["id"],"status":"AVAILABLE"})
        except (ValueError,OSError):
            results.append({"media_id":media["id"],"status":"MISSING_OR_CHANGED"})
    return {"media":results}

@router.post("/exports")
async def export(body: Export,request: Request):
    return await guarded(service(request).export(body.model_dump()))

@router.get("/exports/{job_id}")
async def job(job_id: str,request: Request):
    return await guarded(service(request).job(job_id))

@router.post("/exports/{job_id}/cancel")
async def cancel(job_id: str,request: Request):
    video=service(request)
    status=await guarded(video.job(job_id))
    await video.svc.engine.stop(status["task_id"])
    return await video.job(job_id)

@router.get("/exports/{job_id}/file")
async def output(job_id: str,request: Request):
    video=service(request)
    status=await guarded(video.job(job_id))
    if status["status"] != "completed":
        raise HTTPException(409,"export not independently verified and completed")
    path=await guarded(video.verified_output(job_id))
    return FileResponse(path,media_type="application/zip" if path.suffix==".zip" else "video/mp4",
                        filename="bossman-project.zip" if path.suffix==".zip" else "bossman-video.mp4")

@router.post("/chat")
async def chat(body: Chat,request: Request):
    return await guarded(service(request).chat(body.text,body.operation_id,body.project_id))

@router.get("/chat")
async def chat_history(request: Request):
    async with service(request).svc.db.session() as s:
        rows=(await s.execute(sa.select(chats).order_by(chats.c.created_at.desc()).limit(100))).mappings().all()
    return {"messages":[{k:r[k] for k in ("project_id","task_id","text","operation_id")} for r in rows]}

@router.post("/chat/{task_id}/run")
async def chat_run(task_id: int,request: Request):
    svc=service(request).svc
    async with svc.db.session() as s:
        row=(await s.execute(sa.select(tasks_t).where(tasks_t.c.id==task_id))).mappings().first()
    if not row or row["kind"] != "video_edit":
        raise HTTPException(404,"video task not found")
    if row["status"] == "draft":
        await svc.engine.enqueue(task_id)
    return {"task_id":task_id,"project_id":row["meta"]["video_project_id"]}

async def bind_skill(svc):
    async def before_run(task,run):
        meta=dict(task.get("meta") or {})
        if meta.get("skill") != "video-editing" or meta.get("video_project_id"):
            return None
        pid=(meta.get("skill_input") or {}).get("project_id")
        if not pid:
            return {"fail":"video-editing skill requires an owner-attached project"}
        await svc.video_studio.store.get(pid)
        meta["video_project_id"]=pid
        async with svc.db.session() as s:
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id==task["id"]).values(meta=meta))
            await s.commit()
        task["meta"]=meta
        return None
    return before_run

async def setup(svc):
    from ..video_studio.tools import register_tools
    svc.video_studio=VideoService(svc)
    svc.engine.register_executor("video_render",svc.video_studio.render_executor)
    svc.engine.register_executor("video_edit",svc.video_studio.edit_executor)
    svc.engine.register_executor("video_analysis",svc.video_studio.analysis_executor)
    svc.engine.add_hook("gate_completion",svc.video_studio.analysis_gate)
    svc.engine.register_executor("video_package",svc.video_studio.package_executor)
    svc.engine.add_hook("gate_completion",svc.video_studio.package_gate)
    svc.engine.register_executor("video_proposal",svc.video_studio.proposal_executor)
    svc.engine.add_hook("gate_completion",svc.video_studio.proposal_gate)
    svc.engine.add_hook("gate_completion",svc.video_studio.render_gate)
    svc.engine.add_hook("before_run",await bind_skill(svc))
    register_tools()

FEATURE=Feature(name="video_studio",router=router,setup=setup)
