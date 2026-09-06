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
        return await self.media.resolve_for_read(media), media

    async def export(self, payload, *, job_kind="video_render"):
        project = await self.store.get(payload["project_id"])
        op = identifier(payload["operation_id"])
        fingerprint = digest(payload)
        async with self.svc.db.session() as s:
            prior = (await s.execute(sa.select(jobs).where(jobs.c.operation_id == op))).mappings().first()
        if prior:
            if prior["digest"] != fingerprint:
                raise RuntimeError("operation id reused with different payload")
            await self.svc.engine.enqueue(prior["task_id"],only_if_draft=True)
            return await self.job(prior["id"])
        if project["revision"] != payload["expected_revision"]:
            raise RuntimeError("revision conflict")
        jid = uuid.uuid4().hex
        options = dict(payload.get("options") or {})
        options["_preview"] = bool(payload.get("preview"))
        container=payload.get("container","mp4")
        if container not in ("mp4","mov","mkv","webm"):
            raise ValueError("unsupported export container")
        options["_container"]="mp4" if payload.get("preview") else container
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
        await self.svc.engine.enqueue(tid,only_if_draft=True)
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
        result.pop("_render_receipt", None)  # host evidence, not a model/UI credential
        return {"job_id":job_id,"task_id":row["task_id"],"project_id":row["project_id"],
            "revision":row["snapshot"]["revision"],"preview":bool((row.get("options") or {}).get("_preview")),"status":task["status"],"progress":row.get("progress"),
            "error":run.get("error") if run else None,
            "output_url":f"/api/video-studio/exports/{job_id}/file" if task["status"] == "completed" and (row.get("result") or {}).get("path") else None,
            **result}

    async def render_executor(self, task, run, engine):
        from ..engine import FencedOut
        from .errors import render_failure
        try:
            return await self._render_job(task, run, engine)
        except (asyncio.CancelledError, FencedOut):
            raise
        except Exception as exc:
            # Persist bounded diagnostics before the generic engine failure path.
            # Never let a zombie executor replace the current owner's result.
            await engine.assert_fence(run["id"])
            jid = task["meta"]["video_job_id"]
            async with self.svc.db.session() as session:
                progress = (await session.execute(sa.select(jobs.c.progress).where(jobs.c.id == jid))).scalar_one()
                failure = render_failure(exc, (progress or {}).get("stage", "rendering"))
                await session.execute(sa.update(jobs).where(jobs.c.id == jid,
                    jobs.c.task_id == task["id"], sa.exists(sa.select(runs_t.c.id).where(
                        runs_t.c.id == run["id"], engine._fence_clause(run["id"]))))
                    .values(result={"error_detail": failure}, progress={"stage":"failed", "details":failure}))
                await session.commit()
            raise

    async def _render_job(self, task, run, engine):
        from .render import render_project
        from .model import Conflict
        jid = task["meta"]["video_job_id"]
        async with self.svc.db.session() as s:
            row = dict((await s.execute(sa.select(jobs).where(jobs.c.id == jid))).mappings().one())
        # Ревизия в export проверяется до INSERT и без _write_lock, так что правка
        # успевает встать между ними. Без этой сверки готовый файл выдавался бы за
        # текущий проект, а render_gate подтверждал бы его: гейт привязан к снапшоту.
        if (await self.store.get(row["project_id"]))["revision"] != row["snapshot"]["revision"]:
            raise Conflict("project changed after this export was queued")
        outdir = self.root / "exports" / jid / str(run["id"])
        outdir.mkdir(parents=True, exist_ok=True)
        async def progress(stage, details):
            await engine.assert_fence(run["id"])
            async with self.svc.db.session() as s:
                await s.execute(sa.update(jobs).where(jobs.c.id == jid).values(progress={"stage":stage,"details":details}))
                await s.commit()
            await self.svc.bus.emit("video.export.progress", job_id=jid, task_id=task["id"], stage=stage, details=details)
        result = await render_project(row["snapshot"], self.root, outdir / ("output."+row["options"].get("_container","mp4")),
                                      options={k:v for k,v in row["options"].items() if not k.startswith("_")}, progress=progress)
        await engine.assert_fence(run["id"])
        from .export_receipt import certify, RECEIPT_KEY
        # render_project already independently decoded/probed and hashed the
        # artifact. Bind that proof to published bytes outside the hook timeout.
        result["sha256"] = (result.get("verification") or {}).get("sha256")
        result[RECEIPT_KEY] = await certify(self.root, row, task["id"], run["id"], result)
        await engine.assert_fence(run["id"])
        async with self.svc.db.session() as s:
            await s.execute(sa.update(jobs).where(jobs.c.id == jid).values(result=result))
            await s.commit()
        return json.dumps({"job_id":jid,"verification":result.get("verification")}, ensure_ascii=False)

    async def render_gate(self, task, run_id, answer):
        if task.get("kind") != "video_render":
            return {"verdict":"NOT_APPLICABLE"}
        from .export_receipt import validate_for_gate, RenderReceiptInvalid
        jid = task["meta"]["video_job_id"]
        async with self.svc.db.session() as s:
            row = dict((await s.execute(sa.select(jobs).where(jobs.c.id == jid))).mappings().one())
        try:
            await validate_for_gate(self.root, row, task["id"], run_id, row.get("result") or {})
        except (RenderReceiptInvalid, OSError, TypeError, KeyError, ValueError):
            return {"verdict":"FAIL", "requeue":False, "status":"failed",
                    "reasons":"missing, changed or wrong-run render evidence; re-verification required"}
        return {"verdict":"PASS", "requeue":False,
                "reasons":"current signed independent media verification"}

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
        from .read_verification import open_verified
        job=await self.job(job_id)
        if job["status"] != "completed":
            raise RuntimeError("output is not completed")
        async with self.svc.db.session() as s:
            result=(await s.execute(sa.select(jobs.c.result).where(jobs.c.id==job_id))).scalar_one()
        path=Path(result["path"]).resolve()
        if not path.is_relative_to(self.root / "exports" / job_id) or not path.is_file():
            raise ValueError("output unavailable")
        # Descriptor-bound: the artifact is hashed through the descriptor the
        # download then streams from, so re-pointing the pathname afterwards --
        # unlink and recreate, rename, symlink swap -- cannot change the bytes
        # that leave. An in-place rewrite of that same inode still can.
        return await open_verified(path, result.get("sha256"),
            "output changed after independent verification")

    async def analysis(self,payload):
        import os
        action=payload["action"]
        if action not in ("analyse","transcribe","translate","prepare","track","sync","silence_ranges","scene_ranges","scope","hardware_probe","search","broll","duplicates"):
            raise ValueError("unknown analysis action")
        project=await self.store.get(payload["project_id"])
        if payload["media_id"] not in project["media"]:
            raise ValueError("media must be attached to project")
        if action=="transcribe":
            model=os.environ.get("BOSSMAN_VIDEO_ASR_MODEL","")
            if not model or not Path(model).is_file():
                raise RuntimeError("ASR BLOCKED: host local model not configured")
        if action=="translate":
            model=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_MODEL","")
            runtime=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_PYTHON","")
            if not model or not (Path(model)/"config.json").is_file() or not runtime or not Path(runtime).is_file():
                raise RuntimeError("Translation BLOCKED: host local model/runtime not configured")
            if not project.get("captions"):
                raise ValueError("translation requires supplied or transcribed captions")
        options={"action":action,"media_id":payload["media_id"],"language":payload.get("language","auto")}
        for key in ("box","source_in","source_out","reference_media_id","scope_kind","codec","width","height","source","target","query","limit"):
            if payload.get(key) is not None:
                options[key]=payload[key]
        if action=="sync" and options.get("reference_media_id") not in project["media"]:
            raise ValueError("reference media must be attached to project")
        return await self.export({"project_id":payload["project_id"],"expected_revision":payload["expected_revision"],
            "operation_id":payload["operation_id"],"options":options},job_kind="video_analysis")

    async def analysis_executor(self,task,run,engine):
        import os
        from .analysis import analyse_media,transcribe,track_object,synchronize_audio,silence_keep_ranges,scene_ranges,scope_media,hardware_probe
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
        action=row["options"]["action"]
        if action in ("analyse","silence_ranges","scene_ranges"):
            value=await analyse_media(path,progress=progress)
            if action=="silence_ranges":
                value["keep_ranges"]=silence_keep_ranges(value,source_in=row["options"].get("source_in",0),
                    source_out=row["options"].get("source_out"))
            elif action=="scene_ranges":
                value["segments"]=scene_ranges(value)
        elif action=="prepare":
            value={"artifacts":await self.media.prepare(media)}
        elif action=="track":
            value=await track_object(path,row["options"].get("box"),start=row["options"].get("source_in",0),
                end=row["options"].get("source_out"))
        elif action=="sync":
            reference=row["snapshot"]["media"][row["options"]["reference_media_id"]]
            reference_path=await asyncio.to_thread(self.media.resolve,reference)
            value=await synchronize_audio(reference_path,path)
            await asyncio.to_thread(self.media.resolve,reference)
        elif action=="scope":
            output=self.root/"exports"/jid/str(run["id"])/"scope.png"
            value=await scope_media(path,self.root,output,kind=row["options"].get("scope_kind","waveform"),
                time=row["options"].get("source_in",0))
        elif action=="hardware_probe":
            value=await hardware_probe(row["options"].get("width",1280),row["options"].get("height",720),
                row["options"].get("codec","libx264"))
        elif action=="translate":
            from .language import translate_captions
            value=await translate_captions(row["snapshot"]["captions"],self.root,
                model_path=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_MODEL"),
                python_executable=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_PYTHON"),
                source=row["options"].get("source","en"),target=row["options"].get("target","ru"),progress=progress)
            value.update(project_id=row["project_id"],expected_revision=row["snapshot"]["revision"])
        elif action in ("search","broll","duplicates"):
            from .retrieval import search_project,suggest_broll,find_duplicates
            if action=="search":
                value=search_project(row["snapshot"],row["options"].get("query",""),limit=row["options"].get("limit",10))
            elif action=="broll":
                value=suggest_broll(row["snapshot"],row["options"].get("query",""),limit=row["options"].get("limit",5))
            else:
                value=await find_duplicates(row["snapshot"],self.root,progress=progress)
        else:
            captions=await transcribe(path,model_path=os.environ.get("BOSSMAN_VIDEO_ASR_MODEL"),
                                     language=row["options"].get("language","auto"),progress=progress)
            await engine.assert_fence(run["id"])
            await asyncio.to_thread(self.media.resolve,media)
            result=await self.command({"project_id":row["project_id"],"expected_revision":row["snapshot"]["revision"],
                "operation_id":"asr-"+jid,"command":{"type":"captions.replace","captions":captions}},
                actor="agent:"+str(task["id"]))
            value={"captions":captions,"revision":result["revision"]}
        await engine.assert_fence(run["id"])
        # Re-read original after processing; observations never authorize a changed source.
        await asyncio.to_thread(self.media.resolve,media)
        stored={"analysis":value,"source_sha256":media["sha256"],"verification":{"passed":True,"source_unchanged":True}}
        if action=="scope":
            from .media import digest_file
            stored.update(path=value.pop("path"),sha256=await asyncio.to_thread(digest_file,output))
        async with self.svc.db.session() as session:
            await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(result=stored))
            await session.commit()
        return json.dumps({"job_id":jid,"local_analysis_verified":True},ensure_ascii=False)

    async def analysis_gate(self,task,run_id,answer):
        if task.get("kind")!="video_analysis":
            return {"verdict":"NOT_APPLICABLE"}
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.task_id==task["id"]))).mappings().one())
        media=row["snapshot"]["media"][row["options"]["media_id"]]
        await asyncio.to_thread(self.media.resolve,media)
        if row["options"].get("reference_media_id"):
            await asyncio.to_thread(self.media.resolve,row["snapshot"]["media"][row["options"]["reference_media_id"]])
        passed=(row.get("result") or {}).get("source_sha256")==media["sha256"]
        if row["options"]["action"]=="scope":
            from .media import digest_file,probe
            result=row.get("result") or {}
            path=Path(result.get("path","")).resolve()
            passed=passed and path.is_relative_to(self.root/"exports"/row["id"]/str(run_id))
            passed=passed and await asyncio.to_thread(digest_file,path)==result.get("sha256")
            if passed:
                await probe(path)
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

    async def import_package(self,request,project_id,expected_revision,operation_id):
        """Stream owner upload; parsing/probing/commit run under canonical admission."""
        from .media import digest_file
        import os
        identifier(project_id);identifier(operation_id)
        await self.store.get(project_id)
        incoming=self.root/"uploads";incoming.mkdir(exist_ok=True)
        temporary=incoming/(uuid.uuid4().hex+".partial")
        limit=8*1024**3;count=0;hash=hashlib.sha256()
        if int(request.headers.get("content-length") or 0)>limit:
            raise ValueError("portable upload exceeds 8 GiB limit")
        try:
            with temporary.open("xb") as stream:
                async for chunk in request.stream():
                    count+=len(chunk)
                    if count>limit:raise ValueError("portable upload exceeds 8 GiB limit")
                    if shutil.disk_usage(incoming).free<len(chunk)+64*1024**2:
                        raise RuntimeError("insufficient portable upload disk space")
                    hash.update(chunk);await asyncio.to_thread(stream.write,chunk)
                stream.flush();os.fsync(stream.fileno())
            if not count:raise ValueError("empty portable upload")
            key=hash.hexdigest();target=incoming/(key+".portable.zip")
            try:os.link(temporary,target)
            except FileExistsError:
                if await asyncio.to_thread(digest_file,target)!=key:
                    raise RuntimeError("existing staged archive changed")
            return await self.export({"project_id":project_id,"expected_revision":expected_revision,"operation_id":operation_id,
                "options":{"archive":target.relative_to(self.root).as_posix(),"archive_sha256":key}},job_kind="video_import")
        finally:
            temporary.unlink(missing_ok=True)

    async def import_executor(self,task,run,engine):
        import tempfile,zipfile
        from .media import digest_file
        from .model import validate_project
        jid=task["meta"]["video_job_id"]
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.id==jid))).mappings().one())
        archive_path=(self.root/row["options"]["archive"]).resolve()
        if not archive_path.is_relative_to(self.root/"uploads"):
            raise PermissionError("archive outside owned upload directory")
        if await asyncio.to_thread(digest_file,archive_path)!=row["options"]["archive_sha256"]:
            raise ValueError("staged archive changed")
        with zipfile.ZipFile(archive_path) as archive:
            entries=archive.infolist();names=[x.filename for x in entries]
            if len(entries)>10001 or len(set(names))!=len(names):
                raise ValueError("portable archive contains duplicate/excessive entries")
            manifest=archive.getinfo("project.json")
            if manifest.file_size>32*1024**2 or sum(x.file_size for x in entries)>8*1024**3:
                raise ValueError("portable archive expands beyond import limits")
            project=json.loads(await asyncio.to_thread(archive.read,manifest))
            validate_project(project)
            expected={"project.json",*(m["relative_path"] for m in project["media"].values())}
            if set(names)!=expected:
                raise ValueError("portable archive contains unexpected entries")
            with tempfile.TemporaryDirectory(prefix="portable-",dir=self.root) as directory:
                for key,media in list(project["media"].items()):
                    # Never extract names/paths from the archive onto the filesystem.
                    # The only output is a new random owned temporary filename.
                    temporary=Path(directory)/(uuid.uuid4().hex+".upload")
                    hash=hashlib.sha256()
                    with archive.open(media["relative_path"]) as member,temporary.open("xb") as output:
                        while chunk:=await asyncio.to_thread(member.read,1024*1024):
                            if shutil.disk_usage(directory).free<len(chunk)+64*1024**2:
                                raise RuntimeError("insufficient import disk space")
                            await asyncio.to_thread(output.write,chunk);hash.update(chunk)
                            await engine.assert_fence(run["id"])
                    if hash.hexdigest()!=media["sha256"]:
                        raise ValueError("portable media digest mismatch")
                    verified=await self.media.import_file(temporary,name=media["name"])
                    if verified["sha256"]!=media["sha256"]:
                        raise ValueError("media changed during portable admission")
                    verified.update(id=key,tags=media.get("tags",[]),folder=media.get("folder",""))
                    project["media"][key]=verified
                    temporary.unlink()
        await engine.assert_fence(run["id"])
        result=await self.command({"project_id":row["project_id"],"expected_revision":row["snapshot"]["revision"],
            "operation_id":"portable-import-"+jid,"command":{"type":"project.import","project":project}},
            actor="agent:"+str(task["id"]),trusted_media=True)
        observed=await self.store.get(row["project_id"])
        if digest(observed)!=digest(result["project"]):
            raise RuntimeError("import post-state changed")
        stored={"kind":"portable_import","revision":result["revision"],"project_digest":digest(observed),
            "verification":{"passed":True,"media_count":len(project["media"]),"source_hashes_verified":True}}
        async with self.svc.db.session() as session:
            await session.execute(sa.update(jobs).where(jobs.c.id==jid).values(result=stored));await session.commit()
        return json.dumps({"job_id":jid,"portable_import_verified":True})

    async def import_gate(self,task,run_id,answer):
        if task.get("kind")!="video_import":return {"verdict":"NOT_APPLICABLE"}
        async with self.svc.db.session() as session:
            row=dict((await session.execute(sa.select(jobs).where(jobs.c.task_id==task["id"]))).mappings().one())
        observed=await self.store.get(row["project_id"])
        for media in observed["media"].values():await asyncio.to_thread(self.media.resolve,media)
        passed=digest(observed)==(row.get("result") or {}).get("project_digest")
        return {"verdict":"PASS" if passed else "FAIL","status":"failed","requeue":False}

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
                written=set()
                for media in project["media"].values():
                    source=await asyncio.to_thread(self.media.resolve,media)
                    if media["relative_path"] in written:
                        continue
                    written.add(media["relative_path"])
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

    async def prepared_file(self,project_id,media_id,kind):
        from .read_verification import open_verified
        handle,media=await self.media_file(project_id,media_id)
        # Проверка исходника авторизует запрос, но отдаём мы ДРУГОЙ файл.
        # Производная не адресуется содержимым: containment plus is_file() says
        # nothing about its bytes, so it is checked against the digest recorded
        # by prepare, from the descriptor that will actually be served.
        handle.close()
        name={"thumbnail":"thumb.jpg","proxy":"proxy.mp4","waveform":"wave.png"}[kind]
        path=(self.root/"cache"/media["sha256"] / "v1" / name).resolve()
        if not path.is_relative_to(self.root/"cache"):
            raise PermissionError("cache artifact escaped storage")
        if not path.is_file():
            raise RuntimeError("queue analysis action prepare before requesting this derivative")
        entry=self.media.derived_manifest(media["sha256"]).get(kind)
        if not isinstance(entry,dict) or entry.get("name")!=name:
            raise RuntimeError("derivative has no recorded digest; re-run the prepare action")
        return await open_verified(path,entry.get("sha256"),
            "prepared derivative does not match the digest recorded when it was created",
            size=entry.get("bytes"))
