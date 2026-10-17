"""FastAPI app, API v1 (loopback). Roles: viewer < editor < owner (pattern of apps/war-terminal).

Owner-only: rights approval of a source, approve/reject of a queue item, publication, schedule settings.
Never implemented on purpose: bought views/subscribers, sub4sub, auto-comments/likes, re-upload of third-party
videos without a recorded licence (see /actions/*: always 403)."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import API_VERSION, VERSION, media, news, pipeline, rights, schedule
from .llm import make_chat, pick_model
from .rights import RightsError
from .store import StateError, Store, now

ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}
P = f"/api/{API_VERSION}"
UI_DIR = Path(__file__).parent / "ui"
GOAL = {"subscribers": 1000, "shorts_views_90d": 10_000_000, "watch_hours_365d": 4000}
FORBIDDEN = {"buy-views": "покупка просмотров", "buy-subscribers": "покупка подписчиков", "sub4sub": "взаимные подписки",
             "auto-comment": "автокомментарии", "auto-like": "автолайки", "reupload-third-party": "перезалив чужих видео без лицензии",
             "mass-upload": "массовая однотипная загрузка"}


def _load_tokens(data_dir: Path) -> dict[str, str]:
    path = data_dir / "tokens.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    tokens = {r: secrets.token_urlsafe(24) for r in ROLE_RANK}
    path.write_text(json.dumps(tokens), encoding="utf-8")
    return tokens


def _http_get_json(url: str, params: dict) -> dict:
    import httpx
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _optional(name: str):
    try:
        return __import__(f"shortsfactory.{name}", fromlist=[name])
    except Exception:  # noqa: BLE001 - module from the parallel agent may be absent -> NOT_CONNECTED
        return None


class SourceIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    kind: str = Field(pattern=r"^(own|generated|licensed)$")
    path: str | None = None
    rights_note: str = Field(min_length=3, max_length=2000)
    licence_url: str | None = Field(default=None, pattern=r"^https?://")
    licence_doc: str | None = None
    ai_generated: bool = False
    credit: str | None = Field(default=None, max_length=300)
    origin_url: str | None = None
    people_in_frame: bool = False
    has_burned_captions: bool = Field(default=False, description="source already shows the speech as on-screen text -> no second caption layer")
    caption_margin_v: float | None = Field(default=None, ge=0.03, le=0.85, description="captions height from the bottom, fraction of frame")


class SourcePatch(BaseModel):
    changes: dict


class RightsIn(BaseModel):
    approve: bool
    confirm: str = Field(description="владелец печатает ДА / YES")
    note: str = Field(min_length=3, max_length=1000)


class ConsentIn(BaseModel):
    consent: bool
    confirm: str = Field(default="", description="владелец печатает ДА / YES")
    persons: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(min_length=3, max_length=1000)


class ProcessIn(BaseModel):
    source_id: str
    k: int = Field(default=3, ge=1, le=10)
    whisper_model: str = Field(default="small", pattern=r"^(tiny|base|small|medium|large-v3|large-v3-turbo)$")


class NewsIn(BaseModel):
    source_id: str
    query: str = Field(min_length=2, max_length=300)
    max_images: int = Field(default=2, ge=0, le=2)
    story_index: int = Field(default=0, ge=0, le=20)
    rss_keywords: str | None = Field(default=None, max_length=200, description="regex for the RSS fallback when GDELT throttles")


class MetaPatch(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4900)
    hashtags: list[str] | None = None


class DecideIn(BaseModel):
    note: str = Field(default="", max_length=500)
    privacy: str = Field(default="private", pattern=r"^(private|unlisted|public)$")


class PublishIn(BaseModel):
    privacy: str | None = Field(default=None, pattern=r"^(private|unlisted|public)$")
    publish_at: str | None = None
    use_slot: bool = False
    dry_run: bool = False


class ScheduleIn(BaseModel):
    per_day: int = Field(ge=1, le=6)
    times: list[str] = Field(min_length=1, max_length=6)
    tz: str = "Europe/Prague"


class TrendsIn(BaseModel):
    region: str = Field(default="CZ", pattern=r"^[A-Z]{2}$")
    max_results: int = Field(default=50, ge=1, le=50)
    analyze: bool = True


def create_app(data_dir: str | Path | None = None, *, tokens: dict | None = None, chat=None, trends_mod=None, youtube_mod=None,
               http_get=None, gdelt_get_text=None, rss_get_text=None, image_fn="default", tts=None, transcriber=None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("SHORTS_FACTORY_DATA") or Path.home() / ".shorts-factory")
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "shorts.db")
    tokens = tokens or _load_tokens(data_dir)
    yt_dir = data_dir / "youtube"
    yt_dir.mkdir(exist_ok=True)
    trends_mod = (trends_mod if trends_mod is not None else _optional("trends")) or None      # False = explicitly disabled
    youtube_mod = (youtube_mod if youtube_mod is not None else _optional("youtube")) or None
    http_get = http_get or _http_get_json
    gdelt_get_text = gdelt_get_text or news.http_get_text
    rss_get_text = rss_get_text or news.http_get_text
    image_fn = news.generate_image if image_fn == "default" else image_fn
    tts = tts or news.tts_lines
    _chat = {"fn": chat, "model": getattr(chat, "model", None)}
    jobs: dict[str, dict] = {}
    job_lock = threading.Lock()          # one heavy job at a time: modest CPU/GPU load

    def get_chat():
        if _chat["fn"] is None:
            m = pick_model()
            if m:
                _chat.update(fn=make_chat(m), model=m)
        return _chat["fn"]

    app = FastAPI(title="Bossman Shorts Factory", version=VERSION, description="API v1. Публикация только после «да» владельца.")
    app.state.store, app.state.tokens, app.state.jobs, app.state.data_dir = store, tokens, jobs, data_dir

    def role_of(request: Request, allow_query: bool = False) -> str:
        h = request.headers.get("authorization", "")
        tok = h[7:] if h.lower().startswith("bearer ") else request.headers.get("x-shorts-token", "")
        if not tok and allow_query:
            tok = request.query_params.get("t", "")
        for role, t in tokens.items():
            if tok and secrets.compare_digest(tok, t):
                return role
        raise HTTPException(401, {"code": "TOKEN_REQUIRED", "message": "нужен токен роли (Authorization: Bearer ...)"})

    def need(min_role: str, allow_query: bool = False):
        def dep(request: Request) -> str:
            role = role_of(request, allow_query)
            if ROLE_RANK[role] < ROLE_RANK[min_role]:
                raise HTTPException(403, {"code": "ROLE_TOO_LOW", "message": f"роль {role} ниже требуемой {min_role}"})
            return role
        return dep

    viewer, editor, owner = need("viewer"), need("editor"), need("owner")
    media_viewer = need("viewer", allow_query=True)        # <video src> cannot send headers: ?t=<viewer token>

    def audit(role, event, payload=None):
        store.audit(role, event, payload)

    def rights_http(exc: RightsError):
        return HTTPException(403, {"code": exc.code, "message": exc.message})

    def item_or_404(iid: str) -> dict:
        it = store.get_short(iid)
        if not it:
            raise HTTPException(404, {"message": "элемент очереди не найден"})
        return it

    def public_item(it: dict) -> dict:
        out = {k: v for k, v in it.items() if k not in ("file", "poster")}
        src = store.get_source(it["source_id"])
        out["blocked_consent"] = rights.consent_blocked(src)
        out["blocked"] = "consent" if out["blocked_consent"] else None
        out["video_url"] = f"{P}/queue/{it['id']}/video"
        out["poster_url"] = f"{P}/queue/{it['id']}/poster"
        out["file_name"] = Path(it.get("file", "")).name
        return out

    # ------------------------------------------------------------------ basics
    @app.get(f"{P}/health")
    def health():
        a = store.verify_audit()
        return {"status": "ok", "version": VERSION, "api": API_VERSION, "audit_chain_ok": a["ok"],
                "llm_model": _chat["model"], "trends_module": trends_mod is not None, "youtube_module": youtube_mod is not None,
                "queue": {s: len(store.shorts(s)) for s in ("draft", "ready", "approved_by_owner", "scheduled", "published", "rejected")}}

    @app.get(f"{P}/capabilities")
    def capabilities(_=Depends(viewer)):
        return {"roles": list(ROLE_RANK), "owner_only": ["sources.rights", "sources.consent", "queue.approve", "queue.reject", "queue.publish", "schedule.set"],
                "never_implemented": sorted(FORBIDDEN), "bossman_calls": ["sources.list", "process_source", "make_news_short",
                "queue.list", "trends.fetch", "channel.stats"], "default_privacy": "private", "max_short_s": media.MAX_S}

    # ------------------------------------------------------------------ sources + rights
    @app.get(f"{P}/sources")
    def list_sources(_=Depends(viewer)):
        return {"items": store.sources()}

    @app.post(f"{P}/sources")
    def add_source(body: SourceIn, role=Depends(editor)):
        doc = body.model_dump()
        try:
            rights.validate_registration(doc)
        except RightsError as exc:
            raise HTTPException(422, {"code": exc.code, "message": exc.message}) from None
        if doc["path"]:
            p = Path(doc["path"])
            if not p.is_file():
                raise HTTPException(422, {"message": "файл не найден"})
            doc["path"] = str(p.resolve())
            doc["probe"] = media.probe(p)
        src = store.add_source(doc)
        audit(role, "source.registered", {"id": src["id"], "kind": src["kind"], "path": src.get("path"), "ai_generated": src["ai_generated"]})
        return {"source": src, "next": "права подтверждает только владелец: POST /sources/{id}/rights"}

    @app.patch(P + "/sources/{sid}")
    def patch_source(sid: str, body: SourcePatch, role=Depends(editor)):
        try:
            src = store.update_source(sid, body.changes)
        except KeyError:
            raise HTTPException(404, {"message": "источник не найден"}) from None
        except PermissionError as exc:
            raise HTTPException(403, {"code": "OWNER_ACTION_REQUIRED", "message": str(exc)}) from None
        audit(role, "source.updated", {"id": sid, "fields": sorted(body.changes), "approval_voided": not src["approved_by_owner"]})
        return {"source": src}

    @app.post(P + "/sources/{sid}/rights")
    def approve_rights(sid: str, body: RightsIn, role=Depends(owner)):
        src = store.get_source(sid)
        if not src:
            raise HTTPException(404, {"message": "источник не найден"})
        if body.approve and body.confirm.strip().upper() not in ("ДА", "YES"):
            raise HTTPException(422, {"code": "CONFIRM_REQUIRED", "message": "для одобрения прав впишите confirm=ДА"})
        try:
            rights.validate_registration(src)
        except RightsError as exc:
            raise HTTPException(422, {"code": exc.code, "message": exc.message}) from None
        approval = {"by": role, "at": now(), "note": body.note, "kind": src["kind"], "rights_note": src["rights_note"],
                    "licence_url": src.get("licence_url"), "fingerprint": rights.fingerprint(src["path"]) if src.get("path") else None} if body.approve else None
        src = store._set_rights(sid, body.approve, approval)
        audit(role, "source.rights", {"id": sid, "approve": body.approve, "note": body.note, "fingerprint": (approval or {}).get("fingerprint")})
        return {"source": src}

    @app.post(P + "/sources/{sid}/consent")
    def record_consent(sid: str, body: ConsentIn, role=Depends(owner)):
        src = store.get_source(sid)
        if not src:
            raise HTTPException(404, {"message": "источник не найден"})
        if body.consent and (body.confirm.strip().upper() not in ("ДА", "YES") or not body.persons):
            raise HTTPException(422, {"code": "CONFIRM_REQUIRED", "message": "для согласия впишите confirm=ДА и перечислите людей в кадре (persons)"})
        consent = {"by": role, "at": now(), "persons": body.persons, "note": body.note,
                   "fingerprint": rights.fingerprint(src["path"]) if src.get("path") else None} if body.consent else None
        src = store._set_consent(sid, body.consent, consent)
        audit(role, "source.consent", {"id": sid, "consent": body.consent, "persons": body.persons, "note": body.note})
        return {"source": src}

    # ------------------------------------------------------------------ jobs (background, one at a time)
    def start_job(kind: str, role: str, fn) -> dict:
        jid = "job_" + uuid.uuid4().hex[:8]
        jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "stage": "queued", "started_at": None, "finished_at": None, "result": None, "error": None}

        def runner():
            with job_lock:
                j = jobs[jid]
                j.update(status="running", started_at=now())
                t0 = time.time()
                try:
                    j["result"] = fn(lambda s: j.__setitem__("stage", s))
                    j["status"] = "completed"
                except RightsError as exc:
                    j.update(status="refused", error={"code": exc.code, "message": exc.message})
                except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
                    j.update(status="failed", error={"code": type(exc).__name__, "message": str(exc)[:800], "trace": traceback.format_exc()[-1500:]})
                j.update(finished_at=now(), wall_s=round(time.time() - t0, 1))
                audit(role, f"job.{j['status']}", {"id": jid, "kind": kind, "wall_s": j["wall_s"], "error": (j["error"] or {}).get("code")})
        threading.Thread(target=runner, daemon=True).start()
        return jobs[jid]

    @app.post(f"{P}/jobs/process")
    def job_process(body: ProcessIn, role=Depends(editor)):
        try:
            rights.check(store.get_source(body.source_id))
        except RightsError as exc:
            audit(role, "rights.refused", {"source": body.source_id, "code": exc.code})
            raise rights_http(exc) from None
        return start_job("process", role, lambda say: _slim(pipeline.process_source(
            store, body.source_id, data_dir, k=body.k, chat=get_chat(), whisper_model=body.whisper_model, transcriber=transcriber,
            actor=role, progress=say)))

    @app.post(f"{P}/jobs/news")
    def job_news(body: NewsIn, role=Depends(editor)):
        try:
            rights.check(store.get_source(body.source_id))
        except RightsError as exc:
            audit(role, "rights.refused", {"source": body.source_id, "code": exc.code})
            raise rights_http(exc) from None
        ch = get_chat()
        if ch is None:
            raise HTTPException(409, {"code": "NOT_CONNECTED", "message": "локальная LLM (Ollama) недоступна"})
        return start_job("news", role, lambda say: public_item(news.make_news_short(
            store, body.source_id, data_dir, query=body.query, chat=ch, get_text=gdelt_get_text, image_fn=image_fn,
            max_images=body.max_images, tts=tts, actor=role, story_index=body.story_index, rss_keywords=body.rss_keywords,
            rss_get_text=rss_get_text, progress=say)))

    def _slim(res: dict) -> dict:
        res = dict(res)
        res["items"] = [public_item(i) for i in res["items"]]
        res["highlights"] = {k: v for k, v in res["highlights"].items() if k != "highlights"} | {
            "chosen": [{k: h.get(k) for k in ("start", "end", "dur", "score", "why")} for h in res["highlights"]["highlights"]]}
        return res

    @app.get(P + "/jobs/{jid}")
    def job_get(jid: str, _=Depends(viewer)):
        if jid not in jobs:
            raise HTTPException(404, {"message": "задача не найдена"})
        return jobs[jid]

    @app.get(f"{P}/jobs")
    def job_list(_=Depends(viewer)):
        return {"items": list(jobs.values())[-50:]}

    # ------------------------------------------------------------------ queue + approvals
    @app.get(f"{P}/queue")
    def queue(state: str | None = None, _=Depends(viewer)):
        return {"items": [public_item(i) for i in reversed(store.shorts(state))]}

    @app.get(P + "/queue/{iid}")
    def queue_item(iid: str, _=Depends(viewer)):
        return public_item(item_or_404(iid))

    @app.get(P + "/queue/{iid}/video")
    def queue_video(iid: str, _=Depends(media_viewer)):
        it = item_or_404(iid)
        if not Path(it["file"]).is_file():
            raise HTTPException(410, {"message": "файл удалён"})
        return FileResponse(it["file"], media_type="video/mp4")

    @app.get(P + "/queue/{iid}/poster")
    def queue_poster(iid: str, _=Depends(media_viewer)):
        it = item_or_404(iid)
        if not Path(it.get("poster", "")).is_file():
            raise HTTPException(404, {"message": "нет постера"})
        return FileResponse(it["poster"], media_type="image/jpeg")

    @app.patch(P + "/queue/{iid}/metadata")
    def edit_meta(iid: str, body: MetaPatch, role=Depends(editor)):
        it = item_or_404(iid)
        if it["state"] not in ("draft", "ready", "approved_by_owner"):
            raise HTTPException(409, {"code": "LOCKED", "message": f"в состоянии {it['state']} метаданные не меняются"})
        meta = dict(it["metadata"])
        from . import metadata as md
        raw = {"title": body.title or meta["title"],
               "description": body.description if body.description is not None else meta.get("description_core", ""),
               "hashtags": body.hashtags if body.hashtags is not None else meta["hashtags"]}
        src = store.get_source(it["source_id"]) or {}
        # the disclosure flag can only be switched ON by an edit, never off (it follows the source / item facts)
        synthetic = bool(meta["contains_synthetic_media"] or md.needs_disclosure(src, it))
        new = md.normalize(raw, lang=meta.get("language", "ru"), synthetic=synthetic, fallback_text=meta["title"], credit=meta.get("credit"))
        it = store.update_short(iid, {"metadata": {**meta, **new, "edited_by": role}})
        if it["state"] == "approved_by_owner":           # changed after approval -> needs a new "да"
            it = store.transition(iid, "ready", role, "метаданные изменены после одобрения")
        audit(role, "queue.metadata", {"id": iid, "title": new["title"]})
        return public_item(it)

    @app.post(P + "/queue/{iid}/approve")
    def approve(iid: str, body: DecideIn, role=Depends(owner)):
        it = item_or_404(iid)
        try:
            rights.check(store.get_source(it["source_id"]))
            rights.check_consent(store.get_source(it["source_id"]))
        except RightsError as exc:
            audit(role, "approve.refused", {"id": iid, "code": exc.code})
            raise rights_http(exc) from None
        if not it.get("validation", {}).get("ok"):
            raise HTTPException(409, {"code": "NOT_VALID", "message": "рендер не прошёл проверку ffprobe", "problems": it.get("validation", {}).get("problems")})
        try:
            it = store.transition(iid, "approved_by_owner", role, body.note, privacy=body.privacy, approved_at=now())
        except StateError as exc:
            raise HTTPException(409, {"code": "BAD_STATE", "message": str(exc)}) from None
        audit(role, "queue.approved", {"id": iid, "privacy": body.privacy, "note": body.note})
        return public_item(it)

    @app.post(P + "/queue/{iid}/reject")
    def reject(iid: str, body: DecideIn, role=Depends(owner)):
        item_or_404(iid)
        try:
            it = store.transition(iid, "rejected", role, body.note)
        except StateError as exc:
            raise HTTPException(409, {"code": "BAD_STATE", "message": str(exc)}) from None
        audit(role, "queue.rejected", {"id": iid, "note": body.note})
        return public_item(it)

    @app.post(P + "/queue/{iid}/withdraw")
    def withdraw(iid: str, body: DecideIn, role=Depends(editor)):
        """Editors may withdraw an UNAPPROVED item (nothing is published by this); approved ones need the owner."""
        it = item_or_404(iid)
        if it["state"] not in ("draft", "ready"):
            raise HTTPException(409, {"code": "BAD_STATE", "message": "снять можно только неодобренный элемент; иначе — решение владельца"})
        it = store.transition(iid, "rejected", role, body.note or "снят редактором")
        audit(role, "queue.withdrawn", {"id": iid, "note": body.note})
        return public_item(it)

    @app.post(P + "/queue/{iid}/rerender")
    def rerender(iid: str, role=Depends(editor)):
        it = item_or_404(iid)
        if it["state"] not in ("draft", "ready") or it.get("kind") != "clip":
            raise HTTPException(409, {"code": "BAD_STATE", "message": "перерендер только для неодобренных нарезок"})
        try:
            rights.check(store.get_source(it["source_id"]))
        except RightsError as exc:
            raise rights_http(exc) from None
        return start_job("rerender", role, lambda say: public_item(pipeline.rerender(store, iid, data_dir, chat=get_chat(),
                                                                                       transcriber=transcriber, actor=role)))

    @app.post(P + "/queue/{iid}/publish")
    def publish(iid: str, body: PublishIn, role=Depends(owner)):
        it = item_or_404(iid)
        if it["state"] != "approved_by_owner":
            audit(role, "publish.refused", {"id": iid, "state": it["state"]})
            raise HTTPException(409, {"code": "NOT_APPROVED", "message": f"публикация только после одобрения владельца (сейчас {it['state']})"})
        try:
            rights.check(store.get_source(it["source_id"]))
            rights.check_consent(store.get_source(it["source_id"]))
        except RightsError as exc:
            audit(role, "publish.refused", {"id": iid, "code": exc.code})
            raise rights_http(exc) from None
        if youtube_mod is None:
            raise HTTPException(409, {"code": "NOT_CONNECTED", "message": "модуль youtube недоступен"})
        privacy = body.privacy or it.get("privacy") or "private"
        publish_at = body.publish_at
        if body.use_slot and not publish_at:
            taken = [x.get("publish_at") for x in store.shorts("scheduled") if x.get("publish_at")]
            publish_at = schedule.next_slots(store.kv_get("schedule", {}), taken, 1)[0]
        if publish_at:
            privacy = "private"                          # YouTube: publishAt only for private videos
        m = it["metadata"]
        res = youtube_mod.upload(yt_dir, it["file"], title=m["title"], description=m["description"], tags=m["tags"], privacy=privacy,
                                 publish_at=publish_at, contains_synthetic_media=bool(m["contains_synthetic_media"]), dry_run=body.dry_run)
        audit(role, "publish.attempt", {"id": iid, "status": res.get("status"), "privacy": privacy, "publish_at": publish_at,
                                        "synthetic": m["contains_synthetic_media"], "dry_run": body.dry_run})
        if res.get("status") == "DRY_RUN":
            return {"status": "DRY_RUN", "request": res.get("request"), "item": public_item(it)}
        if res.get("status") != "OK":
            raise HTTPException(409 if res.get("status") == "NOT_CONNECTED" else 502,
                                {"code": res.get("status", "ERROR"), "message": res.get("reason", "загрузка не удалась")})
        to = "scheduled" if publish_at else "published"
        it = store.transition(iid, to, role, "youtube.upload OK", youtube={k: res.get(k) for k in ("video_id", "url", "stored_status", "warning")},
                              publish_at=publish_at, privacy=privacy)
        return {"status": "OK", "item": public_item(it), "youtube": {k: res.get(k) for k in ("video_id", "url", "warning")}}

    # ------------------------------------------------------------------ schedule
    @app.get(f"{P}/schedule")
    def get_schedule(_=Depends(viewer)):
        cfg = {**schedule.DEFAULT, **store.kv_get("schedule", {})}
        approved = store.shorts("approved_by_owner")
        taken = [x.get("publish_at") for x in store.shorts("scheduled") if x.get("publish_at")]
        slots = schedule.next_slots(cfg, taken, max(1, len(approved)))
        return {"config": cfg, "scheduled": [{"id": x["id"], "publish_at": x.get("publish_at")} for x in store.shorts("scheduled")],
                "plan": [{"id": a["id"], "slot": s} for a, s in zip(approved, slots)],
                "note": "слот занимается только при публикации (owner, use_slot=true)"}

    @app.put(f"{P}/schedule")
    def put_schedule(body: ScheduleIn, role=Depends(owner)):
        for t in body.times:
            h, _, m = t.partition(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise HTTPException(422, {"message": f"время {t}: нужно HH:MM"})
        store.kv_set("schedule", body.model_dump())
        audit(role, "schedule.set", body.model_dump())
        return {"config": body.model_dump()}

    # ------------------------------------------------------------------ trends + channel
    @app.get(f"{P}/trends")
    def trends_get(_=Depends(viewer)):
        return store.kv_get("trends") or {"status": "NOT_RUN", "items": [], "analysis": None,
                                          "message": "тренды ещё не загружались: POST /trends/fetch (нужен YOUTUBE_API_KEY)"}

    @app.post(f"{P}/trends/fetch")
    def trends_fetch(body: TrendsIn, role=Depends(editor)):
        if trends_mod is None:
            return {"status": "NOT_CONNECTED", "items": [], "message": "модуль trends недоступен"}
        res = trends_mod.fetch_trends(http_get, api_key=os.environ.get("YOUTUBE_API_KEY"), region=body.region, max_results=body.max_results)
        analysis = None
        if body.analyze and res.get("status") == "OK" and res.get("items"):
            ch = get_chat()
            analysis = trends_mod.analyze_trends(res["items"], (lambda msgs: ch(msgs)) if ch else None) if ch else {"status": "NOT_CONNECTED"}
        doc = {**res, "analysis": analysis, "region": body.region, "stored_at": now()}
        store.kv_set("trends", doc)
        audit(role, "trends.fetch", {"status": res.get("status"), "items": len(res.get("items") or []), "region": body.region})
        return doc

    @app.get(f"{P}/channel")
    def channel(_=Depends(viewer)):
        if youtube_mod is None:
            return {"status": {"connected": False, "reason": "модуль youtube недоступен"}, "stats": None, "goal": goal_progress(None)}
        st = youtube_mod.status(yt_dir)
        stats = youtube_mod.channel_stats(yt_dir) if st.get("connected") else None
        return {"status": st, "stats": stats, "goal": goal_progress(stats)}

    def goal_progress(stats: dict | None) -> dict:
        nd = "нет данных"
        out = {"targets": GOAL, "disclaimer": "пороги YouTube Partner Program — цели, а не обещание дохода"}
        if not stats or stats.get("status") != "OK":
            return out | {"subscribers": nd, "shorts_views_90d": nd, "watch_hours_365d": nd}
        subs = stats.get("subscribers")
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        recent = [v for v in stats.get("recent_shorts") or [] if v.get("published_at") and v.get("views") is not None
                  and datetime.fromisoformat(v["published_at"].replace("Z", "+00:00")) >= cutoff]
        views = sum(v["views"] for v in recent) if recent else None
        return out | {"subscribers": subs if subs is not None else nd,
                      "subscribers_pct": round(100 * subs / GOAL["subscribers"], 2) if subs is not None else None,
                      "shorts_views_90d": views if views is not None else nd,
                      "shorts_views_90d_note": f"нижняя оценка: просмотры {len(recent)} последних Shorts за 90 дней (Data API); точное — YouTube Studio" if recent else None,
                      "shorts_views_pct": round(100 * views / GOAL["shorts_views_90d"], 4) if views is not None else None,
                      "watch_hours_365d": nd}

    # ------------------------------------------------------------------ audit + forbidden actions
    @app.get(f"{P}/audit/events")
    def audit_events(limit: int = 100, _=Depends(owner)):
        return {"items": store.audit_events(min(limit, 500))}

    @app.get(f"{P}/audit/verify")
    def audit_verify(_=Depends(owner)):
        return store.verify_audit()

    @app.post(P + "/actions/{name}")
    def forbidden(name: str, role=Depends(viewer)):
        audit(role, "action.refused", {"name": name})
        raise HTTPException(403, {"code": "FORBIDDEN_BY_POLICY", "message": f"{FORBIDDEN.get(name, name)}: запрещено политикой YouTube и владельцем; не реализовано"})

    # ------------------------------------------------------------------ UI
    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/index.html")

    return app
