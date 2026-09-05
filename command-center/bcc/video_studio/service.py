"""Video Studio host integration: canonical DB, tasks, commands and bounded uploads."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
import sqlalchemy as sa
from ..db import metadata, tasks as tasks_t, task_runs as runs_t, utcnow, fetch_one
from .store import ProjectStore

jobs = sa.Table("video_studio_jobs", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("operation_id", sa.String(100), unique=True, nullable=False),
    sa.Column("digest", sa.String(64), nullable=False),
    sa.Column("project_id", sa.String(100), nullable=False),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
    sa.Column("snapshot", sa.JSON, nullable=False), sa.Column("options", sa.JSON),
    sa.Column("result", sa.JSON), sa.Column("progress", sa.JSON),
    sa.Column("created_at", sa.DateTime, default=utcnow))
chats = sa.Table("video_studio_chats", metadata,
    sa.Column("operation_id", sa.String(100), primary_key=True),
    sa.Column("digest", sa.String(64), nullable=False),
    sa.Column("project_id", sa.String(100), nullable=False),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
    sa.Column("text", sa.Text, nullable=False), sa.Column("created_at", sa.DateTime, default=utcnow))

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("invalid identifier")
    return value

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def editing_intent(text):
    text = text.strip().lower()
    if re.match(r"^(как|зачем|почему|что такое|расскажи|объясни|how|why|what is|explain)\b", text):
        return False
    if re.search(r"(?:^|\s)(не|don.t|do not|never)\s+(склей|смонтир|создай|сделай|merge|edit|make|create)",text):
        return False
    action = r"(склей|смонтир|сделай|создай|добавь|убери|замени|открой|обрежь|merge|edit|make|create|add|remove|replace|open|trim)"
    topic = r"(видео|ролик|ролика|роликов|ролики|субтитр|музык|reels|video|clip|caption|subtitle|fresh vibes|проект)"
    return bool(re.search(action, text) and re.search(topic, text))

class VideoService:
    def __init__(self, svc):
        from .media import MediaLibrary
        self.svc = svc
        self.root = (svc.settings.data_dir / "video-studio").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = ProjectStore(svc.db)
        self.media = MediaLibrary(self.root)

    async def command(self, payload, actor="human", *, trusted_media=False):
        def check(command):
            if not isinstance(command,dict):
                raise ValueError("command must be an object")
            kind = str(command.get("type", "")).removeprefix("video.")
            if kind in ("effect.apply","audio.process"):
                from .render import validate_effect
                validate_effect(command.get("effect",{}))
            if kind == "project.import" and not trusted_media:
                raise PermissionError("document import requires host media validation")
            if kind in ("media.import", "media.relink") and not trusted_media:
                raise PermissionError("media references must be issued by upload/relink service")
            if kind == "timeline.apply":
                if not isinstance(command.get("operations"),list):
                    raise ValueError("operations must be a list")
                for op in command.get("operations", []):
                    check(op)
        check(payload["command"])
        if str(payload["command"].get("type","")).removeprefix("video.")=="project.duplicate":
            command=payload["command"]
            project=await self.store.get(payload["project_id"])
            if payload.get("dry_run"):
                if project["revision"]!=payload["expected_revision"]:
                    raise RuntimeError("source revision changed")
                return {"project_id":project["id"],"revision":project["revision"],"project":project,
                        "dry_run":True,"changed_ids":[],"warnings":["A new independent project will be created"],"artifacts":[],"undo":{"available":False}}
            target=uuid.uuid5(uuid.NAMESPACE_URL,"video-duplicate:"+project["id"]+":"+payload["operation_id"]).hex
            return await self.store.duplicate(project["id"],target,command.get("name") or "Копия проекта",
                                              payload["operation_id"],expected_revision=payload["expected_revision"])
        result = await self.store.apply(payload["project_id"], payload["expected_revision"],
            payload["operation_id"], payload["command"], actor=actor,
            dry_run=payload.get("dry_run", False))
        if not payload.get("dry_run"):
            await self.svc.bus.emit("video.project.changed", project_id=payload["project_id"],
                                    revision=result["revision"], changed_ids=result.get("changed_ids", []))
        return result

    async def upload(self, request, project_id, filename, expected_revision, operation_id):
        identifier(project_id); identifier(operation_id)
        project = await self.store.get(project_id)
        if not filename or len(filename) > 240 or Path(filename).name != filename or any(c in filename for c in "/\\:\0"):
            raise ValueError("invalid filename")
        incoming = self.root / "uploads"
        incoming.mkdir(exist_ok=True)
        path = incoming / (uuid.uuid4().hex + ".upload")
        limit = 8 * 1024**3
        length = request.headers.get("content-length")
        if length and int(length) > limit:
            raise ValueError("upload exceeds 8 GiB limit")
        count = 0
        try:
            with path.open("xb") as out:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > limit:
                        raise ValueError("upload exceeds 8 GiB limit")
                    if shutil.disk_usage(incoming).free < len(chunk) + 64*1024**2:
                        raise RuntimeError("insufficient disk space")
                    await asyncio.to_thread(out.write, chunk)
            if not count:
                raise ValueError("empty upload")
            media = await self.media.import_file(path, name=filename)
            result = await self.command(dict(project_id=project_id, expected_revision=expected_revision,
                operation_id=operation_id, command={"type":"media.import", "media":media}), trusted_media=True)
            result["media"] = media
            return result
        finally:
            path.unlink(missing_ok=True)

    async def media_file(self, project_id, media_id):
        project = await self.store.get(project_id)
        media = project["media"].get(media_id)
        if not media:
            raise KeyError(media_id)
        return await asyncio.to_thread(self.media.resolve, media), media

    async def export(self, payload, *, job_kind="video_render"):
        project = await self.store.get(payload["project_id"])
        op = identifier(payload["operation_id"])
        fingerprint = digest(payload)
        async with self.svc.db.session() as s:
            prior = (await s.execute(sa.select(jobs).where(jobs.c.operation_id == op))).mappings().first()
        if prior:
            if prior["digest"] != fingerprint:
                raise RuntimeError("operation id reused with different payload")
            return await self.job(prior["id"])
        if project["revision"] != payload["expected_revision"]:
            raise RuntimeError("revision conflict")
        jid = uuid.uuid4().hex
        options = dict(payload.get("options") or {})
        options["_preview"] = bool(payload.get("preview"))
        if payload.get("preview"):
            options.update(width=320,height=180)
        # The host issues paths, kind, retry policy and authority; requests cannot set them.
        async with self.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(title="Video preview" if payload.get("preview") else "Video export",
                prompt="Video Studio deterministic local job", kind=job_kind, status="draft", max_retries=0,
                meta={"video_job_id":jid,"video_project_id":project["id"],"allowed_tools":[]},
                created_at=utcnow(), updated_at=utcnow()))
            tid = int(res.inserted_primary_key[0])
            await s.execute(sa.insert(jobs).values(id=jid, operation_id=op, digest=fingerprint,
                project_id=project["id"], task_id=tid, snapshot=project, options=options))
            await s.commit()
        await self.svc.engine.enqueue(tid)
        return await self.job(jid)

    async def job(self, job_id):
        identifier(job_id)
        async with self.svc.db.session() as s:
            row = (await s.execute(sa.select(jobs).where(jobs.c.id == job_id))).mappings().first()
            if not row:
                raise KeyError(job_id)
            row = dict(row)
            task = await fetch_one(s, tasks_t, row["task_id"])
            run = (await s.execute(sa.select(runs_t).where(runs_t.c.task_id == row["task_id"])
                                  .order_by(runs_t.c.id.desc()).limit(1))).mappings().first()
        result = dict(row.get("result") or {})
        result.pop("path", None)
        return {"job_id":job_id,"task_id":row["task_id"],"project_id":row["project_id"],
            "revision":row["snapshot"]["revision"],"preview":bool((row.get("options") or {}).get("_preview")),"status":task["status"],"progress":row.get("progress"),
            "error":run.get("error") if run else None,
            "output_url":f"/api/video-studio/exports/{job_id}/file" if task["status"] == "completed" and (row.get("result") or {}).get("path") else None,
            **result}

    async def render_executor(self, task, run, engine):
        from .render import render_project
        jid = task["meta"]["video_job_id"]
        async with self.svc.db.session() as s:
            row = dict((await s.execute(sa.select(jobs).where(jobs.c.id == jid))).mappings().one())
        outdir = self.root / "exports" / jid / str(run["id"])
        outdir.mkdir(parents=True, exist_ok=True)
        async def progress(stage, details):
            await engine.assert_fence(run["id"])
            async with self.svc.db.session() as s:
                await s.execute(sa.update(jobs).where(jobs.c.id == jid).values(progress={"stage":stage,"details":details}))
                await s.commit()
            await self.svc.bus.emit("video.export.progress", job_id=jid, task_id=task["id"], stage=stage, details=details)
        result = await render_project(row["snapshot"], self.root, outdir / "output.mp4",
                                      options={k:v for k,v in row["options"].items() if not k.startswith("_")}, progress=progress)
        await engine.assert_fence(run["id"])
        from .media import digest_file
        result["sha256"] = await asyncio.to_thread(digest_file,Path(result["path"]))
        async with self.svc.db.session() as s:
            await s.execute(sa.update(jobs).where(jobs.c.id == jid).values(result=result))
            await s.commit()
        return json.dumps({"job_id":jid,"verification":result.get("verification")}, ensure_ascii=False)

    async def render_gate(self, task, run_id, answer):
        if task.get("kind") != "video_render":
            return {"verdict":"NOT_APPLICABLE"}
        from .render import verify_output
        jid = task["meta"]["video_job_id"]
        async with self.svc.db.session() as s:
            row = dict((await s.execute(sa.select(jobs).where(jobs.c.id == jid))).mappings().one())
        result = row.get("result") or {}
        if not result.get("path"):
            return {"verdict":"FAIL","requeue":False,"status":"failed","reasons":"missing render artifact"}
        path = Path(result["path"]).resolve()
        if not path.is_relative_to(self.root / "exports" / jid / str(run_id)):
            return {"verdict":"FAIL","requeue":False,"status":"failed","reasons":"artifact ownership mismatch"}
        from .media import digest_file
        if await asyncio.to_thread(digest_file,path) != result.get("sha256"):
            return {"verdict":"FAIL","requeue":False,"status":"failed","reasons":"output changed after render"}
        actual = await verify_output(path, {**(result.get("profile") or {}), **(result.get("verification") or {})})
        passed = actual.get("passed") is True
        return {"verdict":"PASS" if passed else "FAIL", "requeue":False,"status":"failed",
                "reasons":"independent media verification"}

    async def chat(self, text, operation_id, project_id=None):
        if not editing_intent(text):
            return {"handled":False}
        identifier(operation_id)
        fingerprint = digest({"text":text,"project_id":project_id})
        async with self.svc.db.session() as s:
            prior = (await s.execute(sa.select(chats).where(chats.c.operation_id == operation_id))).mappings().first()
        if prior:
            if prior["digest"] != fingerprint:
                raise RuntimeError("operation id payload conflict")
            return {"handled":True,"project_id":prior["project_id"],"task_id":prior["task_id"],"text":prior["text"]}
        pid = project_id or "chat-" + uuid.uuid5(uuid.NAMESPACE_URL, operation_id).hex
        if project_id:
            await self.store.get(pid)
        else:
            await self.store.create(pid, text[:100], "chat-" + operation_id[:90], links={"request_id":operation_id})
        async with self.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(title=text[:100],prompt=text,
                kind="video_edit",status="draft",max_retries=0,
                meta={"video_project_id":pid,"request_id":operation_id,"allowed_tools":[]},
                created_at=utcnow(),updated_at=utcnow()))
            tid = int(res.inserted_primary_key[0])
            await s.execute(sa.insert(chats).values(operation_id=operation_id,digest=fingerprint,
                project_id=pid,task_id=tid,text=text))
            await s.commit()
        await self.svc.bus.emit("video.project.open",project_id=pid,task_id=tid)
        return {"handled":True,"project_id":pid,"task_id":tid,"text":text}

    async def edit_executor(self, task, run, engine):
        from .model import sequence, clip_duration
        pid=task["meta"]["video_project_id"]
        project=await self.store.get(pid)
        text=task["prompt"].lower()
        if re.search(r"^(открой|open)\b",text):
            return json.dumps({"project_id":pid,"revision":project["revision"],"opened":True})
        vertical=bool(re.search(r"reels|вертикаль",text))
        stitch=bool(re.search(r"склей|смонтир|merge|stitch|edit.*video",text))
        if not (vertical or stitch):
            raise ValueError("this request requires an assigned video skill or supported explicit timeline commands")
        seq=sequence(project)
        operations=[]
        if vertical:
            operations.append({"type":"sequence.update","sequence_id":seq["id"],
                               "patch":{"width":720,"height":1280}})
        video=next(t for t in seq["tracks"] if t["kind"]=="video")
        audio=next(t for t in seq["tracks"] if t["kind"]=="audio")
        used={c.get("media_id") for t in seq["tracks"] for c in t["clips"]}
        cursor=max((c["start"]+clip_duration(c) for c in video["clips"]),default=0)
        for media in project["media"].values():
            if media["id"] in used:
                continue
            duration=media.get("duration_ticks",0)
            if duration <= 0:
                continue
            track=video if media.get("has_video") else audio
            operations.append({"type":"clip.add","track_id":track["id"],"clip":{
                "id":"chat-"+str(task["id"])+"-"+media["id"],"media_id":media["id"],
                "start":cursor,"source_in":0,"source_out":duration}})
            cursor+=duration
        if not operations:
            raise ValueError("attach media before editing; existing montage was preserved")
        await engine.assert_fence(run["id"])
        result=await self.command({"project_id":pid,"expected_revision":project["revision"],
            "operation_id":"chat-edit-"+str(task["id"]),
            "command":{"type":"timeline.apply","operations":operations}}, actor="agent:"+str(task["id"]))
        observed=await self.store.get(pid)
        if observed["revision"] != result["revision"] or digest(observed)!=digest(result["project"]):
            raise RuntimeError("timeline post-state verification failed")
        return json.dumps({"project_id":pid,"revision":observed["revision"],
            "timeline_verified":True,"note":"Timeline prepared; preview and export remain separate tasks."})

    async def verified_output(self, job_id):
        from .media import digest_file
        job=await self.job(job_id)
        if job["status"] != "completed":
            raise RuntimeError("output is not completed")
        async with self.svc.db.session() as s:
            result=(await s.execute(sa.select(jobs.c.result).where(jobs.c.id==job_id))).scalar_one()
        path=Path(result["path"]).resolve()
        if not path.is_relative_to(self.root / "exports" / job_id) or not path.is_file():
            raise ValueError("output unavailable")
        if await asyncio.to_thread(digest_file,path) != result.get("sha256"):
            raise RuntimeError("output changed after independent verification")
        return path

    async def analysis(self,payload):
        import os
        action=payload["action"]
        if action not in ("analyse","transcribe"):
            raise ValueError("unknown analysis action")
        project=await self.store.get(payload["project_id"])
        if payload["media_id"] not in project["media"]:
            raise ValueError("media must be attached to project")
        if action=="transcribe":
            model=os.environ.get("BOSSMAN_VIDEO_ASR_MODEL","")
            if not model or not Path(model).is_file():
                raise RuntimeError("ASR BLOCKED: host local model not configured")
        options={"action":action,"media_id":payload["media_id"],"language":payload.get("language","auto")}
        return await self.export({"project_id":payload["project_id"],"expected_revision":payload["expected_revision"],
            "operation_id":payload["operation_id"],"options":options},job_kind="video_analysis")

    async def analysis_executor(self,task,run,engine):
        import os
        from .analysis import analyse_media,transcribe
        jid=task["meta"]["video_job_id"]
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.id==jid))).mappings().one())
        media=row["snapshot"]["media"][row["options"]["media_id"]]
        path=await asyncio.to_thread(self.media.resolve,media)
        async def progress(stage,details):
            await engine.assert_fence(run["id"])
            async with self.svc.db.session() as session:
                await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(progress={"stage":stage,"details":details}))
                await session.commit()
            await self.svc.bus.emit("video.export.progress",job_id=jid,task_id=task["id"],stage=stage,details=details)
        if row["options"]["action"]=="analyse":
            value=await analyse_media(path,progress=progress)
        else:
            captions=await transcribe(path,model_path=os.environ.get("BOSSMAN_VIDEO_ASR_MODEL"),
                                     language=row["options"].get("language","auto"),progress=progress)
            await engine.assert_fence(run["id"])
            result=await self.command({"project_id":row["project_id"],"expected_revision":row["snapshot"]["revision"],
                "operation_id":"asr-"+jid,"command":{"type":"captions.replace","captions":captions}},
                actor="agent:"+str(task["id"]))
            value={"captions":captions,"revision":result["revision"]}
        await engine.assert_fence(run["id"])
        # Re-read original after processing; observations never authorize a changed source.
        await asyncio.to_thread(self.media.resolve,media)
        async with self.svc.db.session() as session:
            await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(result={"analysis":value,
                "source_sha256":media["sha256"],"verification":{"passed":True,"source_unchanged":True}}))
            await session.commit()
        return json.dumps({"job_id":jid,"local_analysis_verified":True},ensure_ascii=False)

    async def analysis_gate(self,task,run_id,answer):
        if task.get("kind")!="video_analysis":
            return {"verdict":"NOT_APPLICABLE"}
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.task_id==task["id"]))).mappings().one())
        media=row["snapshot"]["media"][row["options"]["media_id"]]
        await asyncio.to_thread(self.media.resolve,media)
        passed=(row.get("result") or {}).get("source_sha256")==media["sha256"]
        return {"verdict":"PASS" if passed else "FAIL","requeue":False,"status":"failed"}

    async def relink(self,payload,actor="human"):
        project=await self.store.get(payload["project_id"])
        replacement=project["media"].get(payload["replacement_media_id"])
        if not replacement or payload["media_id"] not in project["media"]:
            raise ValueError("both source and replacement must be owner-uploaded media IDs")
        await asyncio.to_thread(self.media.resolve,replacement)
        return await self.command({"project_id":payload["project_id"],"expected_revision":payload["expected_revision"],
            "operation_id":payload["operation_id"],"command":{"type":"media.relink","media_id":payload["media_id"],"media":replacement}},
            actor=actor,trusted_media=True)

    async def package(self,payload):
        return await self.export({"project_id":payload["project_id"],"expected_revision":payload["expected_revision"],
            "operation_id":payload["operation_id"],"options":{}},job_kind="video_package")

    async def package_executor(self,task,run,engine):
        import os
        import zipfile
        from .media import digest_file
        jid=task["meta"]["video_job_id"]
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.id==jid))).mappings().one())
        directory=self.root/"exports"/jid/str(run["id"])
        directory.mkdir(parents=True,exist_ok=True)
        temporary=directory/"package.partial"
        output=directory/"project.zip"
        project=row["snapshot"]
        try:
            with zipfile.ZipFile(temporary,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                archive.writestr("project.json",json.dumps(project,ensure_ascii=False,sort_keys=True))
                for media in project["media"].values():
                    source=await asyncio.to_thread(self.media.resolve,media)
                    with source.open("rb") as original,archive.open(media["relative_path"],"w",force_zip64=True) as target:
                        while chunk:=original.read(1024*1024):
                            if shutil.disk_usage(directory).free < len(chunk)+64*1024**2:
                                raise RuntimeError("insufficient package disk space")
                            target.write(chunk)
                            await asyncio.sleep(0)
                    await engine.assert_fence(run["id"])
            with temporary.open("rb+") as file:
                os.fsync(file.fileno())
            verification=await asyncio.to_thread(self.check_package,temporary,project)
            if not verification["passed"]:
                raise RuntimeError("portable package verification failed")
            await engine.assert_fence(run["id"])
            os.link(temporary,output)
            result={"path":str(output),"sha256":await asyncio.to_thread(digest_file,output),
                    "verification":verification,"kind":"portable_package"}
            async with self.svc.db.session() as session:
                await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(result=result))
                await session.commit()
            return json.dumps({"job_id":jid,"portable_package_verified":True})
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def check_package(path,project):
        import zipfile
        with zipfile.ZipFile(path) as archive:
            observed=json.loads(archive.read("project.json"))
            if observed != project:
                return {"passed":False,"reason":"project mismatch"}
            for media in project["media"].values():
                hash=hashlib.sha256()
                with archive.open(media["relative_path"]) as member:
                    while chunk:=member.read(1024*1024):
                        hash.update(chunk)
                if hash.hexdigest()!=media["sha256"]:
                    return {"passed":False,"reason":"media mismatch"}
        return {"passed":True,"media_count":len(project["media"]),"portable":True}

    async def package_gate(self,task,run_id,answer):
        if task.get("kind")!="video_package":
            return {"verdict":"NOT_APPLICABLE"}
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.task_id==task["id"]))).mappings().one())
        result=row.get("result") or {}
        if not result.get("path"):
            return {"verdict":"FAIL","requeue":False,"status":"failed"}
        path=Path(result["path"]).resolve()
        if not path.is_relative_to(self.root/"exports"/row["id"]/str(run_id)):
            return {"verdict":"FAIL","requeue":False,"status":"failed"}
        actual=await asyncio.to_thread(self.check_package,path,row["snapshot"])
        return {"verdict":"PASS" if actual["passed"] else "FAIL","requeue":False,"status":"failed"}

    async def proposal(self,payload):
        from .model import clip
        project=await self.store.get(payload["project_id"])
        clip(project,payload["clip_id"])
        return await self.export({"project_id":payload["project_id"],"expected_revision":payload["expected_revision"],
            "operation_id":payload["operation_id"],"options":{"objective":payload["objective"],"clip_id":payload["clip_id"]}},
            job_kind="video_proposal")

    async def proposal_executor(self,task,run,engine):
        from .trained import propose
        jid=task["meta"]["video_job_id"]
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.id==jid))).mappings().one())
        project=await self.store.get(row["project_id"])
        if project["revision"]!=row["snapshot"]["revision"]:
            raise RuntimeError("project changed before proposal")
        result=await propose(row["options"]["objective"],project,row["options"]["clip_id"])
        await engine.assert_fence(run["id"])
        current=await self.store.get(row["project_id"])
        if current["revision"]!=project["revision"]:
            raise RuntimeError("project changed during proposal; inspect again")
        async with self.svc.db.session() as session:
            await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(result={"proposal":result}))
            await session.commit()
        return json.dumps({"job_id":jid,"draft_only":True,"valid":bool(result.get("valid"))})

    async def proposal_gate(self,task,run_id,answer):
        if task.get("kind")!="video_proposal":
            return {"verdict":"NOT_APPLICABLE"}
        from .commands import apply_command
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.task_id==task["id"]))).mappings().one())
        current=await self.store.get(row["project_id"])
        if current["revision"]!=row["snapshot"]["revision"] or not row.get("result"):
            return {"verdict":"FAIL","requeue":False,"status":"failed","reasons":"proposal revision changed or result missing"}
        result=row["result"].get("proposal") or {}
        if result.get("valid") and result.get("applicable"):
            apply_command(current,result["command"],actor="agent")
        return {"verdict":"PASS","reasons":"draft evaluated; no edit was executed"}

    async def import_otio(self,payload):
        from .interchange import import_otio
        from .render import validate_effect
        current=await self.store.get(payload["project_id"])
        if current["revision"]!=payload["expected_revision"]:
            raise RuntimeError("project revision changed")
        parsed=await asyncio.to_thread(import_otio,payload["data"],current["id"])
        project=parsed["project"]
        for media in project["media"].values():
            await asyncio.to_thread(self.media.resolve,media)
        for sequence in project["sequences"]:
            for track in sequence["tracks"]:
                for owner in [track,*track["clips"]]:
                    for effect in owner.get("effects",[]):
                        validate_effect(effect)
        result=await self.command({"project_id":current["id"],"expected_revision":current["revision"],
            "operation_id":payload["operation_id"],"command":{"type":"project.import","project":project}},
            trusted_media=True)
        result["warnings"]+= ["OTIO media hashes validated inside owned storage; foreign-editor effect parity is not guaranteed"]
        return result
