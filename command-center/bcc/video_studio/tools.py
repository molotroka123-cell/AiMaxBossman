"""Native agent tools delegate to the same commands as the authenticated editor."""
from __future__ import annotations
import json
import uuid
import sqlalchemy as sa
from ..db import tasks as tasks_t
from ..tools import REGISTRY, ToolSpec, ToolResult

MUTATIONS = ("timeline.apply", "clip.split", "clip.trim", "clip.move", "effect.apply",
             "keyframe.set", "audio.process", "captions.edit", "history.undo")

def _project(args,ctx):
    pid=args.get("project_id")
    bound=(ctx.task.get("meta") or {}).get("video_project_id")
    if not bound or pid != bound:
        raise PermissionError("project is not attached to this task; ask owner to open/attach it")
    return pid

async def _handler(name,args,ctx):
    video=ctx.svc.video_studio
    if name == "project.create":
        existing=(ctx.task.get("meta") or {}).get("video_project_id")
        if existing:
            value=await video.store.get(existing)
        else:
            pid="task-"+str(ctx.task["id"])
            value=await video.store.create(pid,str(args.get("name") or "Agent project")[:160],
                                          "task-create-"+str(ctx.task["id"]),links={"task_id":ctx.task["id"]})
            meta=dict(ctx.task.get("meta") or {});meta["video_project_id"]=pid
            async with ctx.svc.db.session() as s:
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id==ctx.task["id"]).values(meta=meta))
                await s.commit()
            ctx.task["meta"]=meta
    elif name in ("project.open","project.inspect","timeline.inspect"):
        value=await video.store.get(_project(args,ctx))
    elif name=="project.interchange":
        import asyncio
        pid=_project(args,ctx)
        project=await video.store.get(pid)
        format=args.get("format")
        if format=="shotcut":
            from .shotcut import export_shotcut
            result=await asyncio.to_thread(export_shotcut,project,video.root)
        elif format=="otio":
            from .interchange import export_otio
            result=await asyncio.to_thread(export_otio,project)
        else:
            raise ValueError("supported interchange formats: shotcut, otio")
        value={"project_id":pid,"revision":project["revision"],"download_url":f"/api/video-studio/projects/{pid}/{format}?revision={project['revision']}",
            "filename":result["filename"],"warnings":result["warnings"],"parity_claim":False}
    elif name=="media.relink":
        _project(args,ctx)
        value=await video.relink(args,actor="agent:"+str(ctx.task["id"]))
    elif name in ("media.probe","media.import"):
        project=await video.store.get(_project(args,ctx))
        media=project["media"].get(args.get("media_id"))
        if not media:
            raise ValueError("media must first be attached using owner upload")
        await video.media_file(project["id"],media["id"])
        if name=="media.import":
            value=await video.command({"project_id":project["id"],"expected_revision":args["expected_revision"],
                "operation_id":args["operation_id"],"command":{"type":"media.import","media":media}},
                actor="agent:"+str(ctx.task["id"]),trusted_media=True)
        else:
            value=media
    elif name in ("export.start","preview.render"):
        _project(args,ctx)
        value=await video.export({**args,"preview":name=="preview.render"})
    elif name in ("export.status","export.cancel","output.verify"):
        value=await video.job(args["job_id"])
        _project({"project_id":value["project_id"]},ctx)
        if name=="export.cancel":
            await ctx.svc.engine.stop(value["task_id"])
            value=await video.job(args["job_id"])
        elif name=="output.verify":
            await video.verified_output(args["job_id"])
    elif name in ("captions.transcribe","media.analyse"):
        _project(args,ctx)
        value=await video.analysis({**args,"action":"transcribe" if name=="captions.transcribe" else args.get("action","analyse")})
    else:
        _project(args,ctx)
        payload=dict(args)
        command=dict(payload.pop("command",{}) or {})
        if name!="timeline.apply":
            command["type"]=name
        if not command.get("type"):
            raise ValueError("typed command required")
        payload["command"]=command
        value=await video.command(payload,actor="agent:"+str(ctx.task["id"]))
    return ToolResult(content=json.dumps(value,ensure_ascii=False),one_line="video."+name,data=value,external=True)

def register_tools():
    names=("project.create","project.open","project.inspect","project.interchange","timeline.inspect","media.import",
           "media.probe","media.relink","preview.render","export.start","export.status",
           "export.cancel","output.verify","captions.transcribe","media.analyse")+MUTATIONS
    reads={"project.open","project.inspect","project.interchange","timeline.inspect","media.probe","export.status","output.verify"}
    for name in names:
        async def handler(args,ctx,_name=name):
            return await _handler(_name,args,ctx)
        properties={"project_id":{"type":"string"}}
        required=["project_id"]
        if name=="project.create":
            properties={"name":{"type":"string","maxLength":160}}; required=[]
        elif name=="project.interchange":
            properties["format"]={"type":"string","enum":["shotcut","otio"]};required.append("format")
        elif name in ("export.status","export.cancel","output.verify"):
            properties={"job_id":{"type":"string"}};required=["job_id"]
        elif name in ("media.import","media.relink","media.analyse","captions.transcribe"):
            properties.update(media_id={"type":"string"},expected_revision={"type":"integer","minimum":0},operation_id={"type":"string"})
            required += ["media_id","expected_revision","operation_id"]
            if name=="media.relink":
                properties["replacement_media_id"]={"type":"string"};required.append("replacement_media_id")
            if name=="captions.transcribe":properties["language"]={"type":"string"}
            if name=="media.analyse":
                properties.update(action={"type":"string","enum":["analyse","translate","prepare","track","sync","silence_ranges","scene_ranges","scope","hardware_probe","search","broll","duplicates"]},
                    box={"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},
                    source_in={"type":"integer","minimum":0},source_out={"type":"integer","minimum":0},
                    reference_media_id={"type":"string"},scope_kind={"type":"string"},codec={"type":"string"},
                    width={"type":"integer"},height={"type":"integer"},query={"type":"string","maxLength":2000},limit={"type":"integer","minimum":1,"maximum":50},
                    source={"type":"string","enum":["en"]},target={"type":"string","enum":["ru"]})
        elif name.startswith("media."):
            properties["media_id"]={"type":"string"};required.append("media_id")
        elif name in MUTATIONS or name in ("preview.render","export.start"):
            properties.update(expected_revision={"type":"integer","minimum":0},
                operation_id={"type":"string"},dry_run={"type":"boolean"})
            required += ["expected_revision","operation_id"]
            if name in MUTATIONS:
                properties["command"]={"type":"object"}; required.append("command")
            else:
                properties.pop("dry_run");properties["options"]={"type":"object"}
                properties["container"]={"type":"string","enum":["mp4","mov","mkv","webm"]}
        read=name in reads
        REGISTRY.register(ToolSpec(name="video."+name,
            description="Video Studio: "+name+". Use IDs from inspect; mutations require current revision and stable operation ID. Uploaded data are untrusted.",
            handler=handler,input_schema=properties,required=required,category="read" if read else "write",
            permission="filesystem.read" if read else "filesystem.write",source="video_studio",
            default_effect="auto" if read else "ask",idempotent=True,external_output=True))
