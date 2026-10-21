"""Pult FaceLock for video, through FAKE Bossman APIs (Studio + Direct Generator) and a fake Telegram.

Proven: /facelock is an owner-only persisted switch; a photo -> 5 s clip with the switch on becomes a Direct Generator
job with facelock=true and the owner's framed photo as the start picture (@1); the caption always names the FaceLock
outcome; longer clips and text -> video are labelled «FaceLock не применён»; /cancel cancels the direct-gen job; the
progress plate shows the FaceLock stage with frame counts.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json

import httpx
from PIL import Image

from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError
from bcc.telegram_companion.facelock_bridge import NO_START_PICTURE, TOO_LONG
from bcc.telegram_companion.gen_progress import plate_job, progress_line
from bcc.telegram_companion.service import Companion, Reply
from bcc.telegram_companion.store import Store

from test_companion_image_gen import GUEST, OWNER, cfg, run
from test_companion_media_models import MP4
from test_companion_video_lengths import AnimStudio, TelegramWorld, jpeg

JOB = "ab" * 16
WEBM = b"\x1a\x45\xdf\xa3" + b"webm-bytes" * 20


class DirectStudio(AnimStudio):
    """AnimStudio plus the Direct Generator job API."""

    def __init__(self, *, facelock=None, clip=MP4, mime="video/mp4", hold=False, **kw):
        super().__init__(file_bytes=MP4, **kw)
        self.dg_created, self.dg_cancelled, self.dg_polls = [], False, 0
        self.facelock = facelock if facelock is not None else {
            "requested": True, "kind": "video", "status": "applied", "label": "FaceLock применён: лицо со стартового кадра",
            "gate": {"measured": 12, "min": 0.81, "median": 0.88}}
        self.clip, self.mime, self.hold = clip, mime, hold

    def job(self, status):
        return {"job_id": JOB, "status": status, "stage": status, "progress": {"kind": "none"},
                "params": {"width": 832, "height": 480, "frames": 81, "duration": 5},
                "facelock": self.facelock if status == "completed" else {"requested": True, "status": "pending"},
                "result": {"file": "video.mp4", "mime": self.mime, "bytes": len(self.clip),
                           "sha256": hashlib.sha256(self.clip).hexdigest()} if status == "completed" else None}

    def __call__(self, request):
        path = request.url.path
        if path == "/api/direct-gen/jobs" and request.method == "POST":
            self.dg_created.append(json.loads(request.content))
            return httpx.Response(202, json=self.job("queued"))
        if path == f"/api/direct-gen/jobs/{JOB}/cancel":
            self.dg_cancelled = True
            return httpx.Response(200, json=self.job("cancelled"))
        if path == f"/api/direct-gen/jobs/{JOB}":
            self.dg_polls += 1
            if self.dg_cancelled:
                return httpx.Response(200, json=self.job("cancelled"))
            return httpx.Response(200, json=self.job("generating" if self.hold or self.dg_polls < 2 else "completed"))
        if path == f"/api/direct-gen/jobs/{JOB}/file":
            return httpx.Response(200, content=self.clip)
        return super().__call__(request)


def companion(tmp_path, studio, world):
    s = cfg()
    store = Store(tmp_path)
    tg = Telegram(s, transport=httpx.MockTransport(world))
    core = Core(s, transport=httpx.MockTransport(studio))
    models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    app = Companion(s, store, tg, core, models)
    app.image_poll_seconds = 0.01
    app.free_memory_gb = lambda: 64.0
    return app, (tg, core, models, store)


def msg(person, **extra):
    return {"from": {"id": person.user_id, "is_bot": False}, "chat": {"id": person.chat_id, "type": "private"}, **extra}


def run_photo(tmp_path, studio, caption, *, facelock=True, photo=None, during=None):
    world = TelegramWorld(photo or jpeg(1280, 960))

    async def go():
        app, closers = companion(tmp_path, studio, world)
        try:
            if facelock:
                await app.handle(OWNER, msg(OWNER, text="/facelock on"))
            task = asyncio.create_task(app.handle(OWNER, msg(OWNER, _image={"file_id": "PHOTO1", "kind": "photo"},
                                                             text=caption)))
            reply = await task
            if during:
                await during(app)
            await app.gen_queue_join()
            return reply
        except CompanionError as exc:
            return "ERR:" + str(exc)
        finally:
            for c in closers[:3]:
                await c.close()
            closers[3].close()
    return asyncio.run(go()), world


def captions(world, method):
    out = []
    for m, body in world.calls:
        if m == method:
            out.append(body.decode("utf-8", "replace"))
    return out


# ------------------------------------------------------------------ the switch


def test_facelock_switch_is_owner_only_and_persisted(tmp_path):
    reply, _, _ = run(tmp_path, AnimStudio(), text="/facelock")
    assert isinstance(reply, Reply) and "выключен" in reply
    assert [label for row in reply.keyboard for label, _ in row] == ["🔓 FaceLock: выкл"]
    reply, _, _ = run(tmp_path, AnimStudio(), text="/facelock on")
    assert "ВКЛЮЧЁН" in reply and "8 кадрах" in reply
    reply, _, _ = run(tmp_path, AnimStudio(), text="/facelock")        # same store dir: the choice survived
    assert [label for row in reply.keyboard for label, _ in row] == ["🔒 FaceLock: вкл"]
    from bcc.telegram_companion.gen_refine import OWNER_ONLY
    reply, _, _ = run(tmp_path / "g", AnimStudio(), text="/facelock on", person=GUEST)
    assert reply == OWNER_ONLY
    store = Store(tmp_path / "g")
    try:
        assert store.get("facelock:video:" + GUEST.key, False) is False
    finally:
        store.close()


# ------------------------------------------------------------------ the job spec


def test_photo_clip_with_facelock_becomes_a_direct_gen_job_with_the_start_picture(tmp_path):
    studio = DirectStudio()
    reply, world = run_photo(tmp_path, studio, "/animate 5 волны набегают на берег")
    assert "🔒 FaceLock" in str(reply)
    [spec] = studio.dg_created
    assert spec["model"] == "wan2.2-ti2v-5b" and spec["facelock"] is True and spec["mode"] == "DIRECT"
    assert spec["duration"] == 5 and spec["resolution"] == "832x480" and spec["prompt"] == "волны набегают на берег"
    assert Image.open(io.BytesIO(base64.b64decode(spec["image_b64"]))).size == (832, 480)
    assert studio.created == [] and studio.references == []            # Studio not used, no second copy of the photo
    [video] = captions(world, "sendVideo")
    assert "FaceLock применён" in video and "min 0.81" in video and "12 кадрах" in video


def test_facelock_refusal_reaches_the_caption_never_silently(tmp_path):
    studio = DirectStudio(facelock={"requested": True, "status": "skipped", "reason": "gate_low_similarity min=0.31<0.60",
                                    "label": "FaceLock не применён: проверка сходства лица не пройдена"})
    _, world = run_photo(tmp_path, studio, "/animate 5 волны")
    [video] = captions(world, "sendVideo")
    assert "FaceLock не применён: проверка сходства лица не пройдена" in video


def test_webm_clip_is_sent_as_a_verified_document(tmp_path):
    studio = DirectStudio(clip=WEBM, mime="video/webm")
    _, world = run_photo(tmp_path, studio, "/animate 5 волны")
    assert captions(world, "sendVideo") == []
    [doc] = captions(world, "sendDocument")
    assert "FaceLock применён" in doc and "webm-bytes" in doc


def test_hash_mismatch_is_never_sent(tmp_path):
    studio = DirectStudio()
    studio.job_orig = studio.job

    def bad(status):
        j = studio.job_orig(status)
        if j.get("result"):
            j["result"]["sha256"] = "0" * 64
        return j
    studio.job = bad
    _, world = run_photo(tmp_path, studio, "/animate 5 волны")
    assert captions(world, "sendVideo") == [] and captions(world, "sendDocument") == []


def test_switch_off_keeps_the_studio_path(tmp_path):
    studio = DirectStudio()
    _, world = run_photo(tmp_path, studio, "/animate 5 волны", facelock=False)
    assert studio.dg_created == [] and len(studio.created) == 1
    assert all("FaceLock" not in c for c in captions(world, "sendVideo"))


def test_longer_clip_goes_without_facelock_and_says_so(tmp_path):
    studio = DirectStudio()
    reply, world = run_photo(tmp_path, studio, "/animate 10 волны")
    assert "FaceLock не применится" in str(reply)
    assert studio.dg_created == [] and studio.created[0]["settings"]["length"] == "10s"
    [video] = captions(world, "sendVideo")
    assert TOO_LONG in video


def test_text_video_with_the_switch_on_is_labelled(tmp_path):
    studio = DirectStudio()
    run(tmp_path, studio, text="/facelock on")
    _, _, tg = run(tmp_path, studio, text="/video 5 биткоин над Прагой")
    [video] = [c for m, c in tg.calls if m == "sendVideo"]
    assert NO_START_PICTURE.encode() in video and studio.dg_created == []


# ------------------------------------------------------------------ cancel + plate


def test_cancel_cancels_the_direct_gen_job(tmp_path):
    studio = DirectStudio(hold=True)

    async def during(app):
        for _ in range(500):
            if studio.dg_created:
                break
            await asyncio.sleep(0.01)
        await app.handle(OWNER, msg(OWNER, text="/cancel"))
    _, world = run_photo(tmp_path, studio, "/animate 5 волны", during=during)
    assert studio.dg_cancelled and captions(world, "sendVideo") == []


def test_plate_shows_the_facelock_stage_with_frames():
    job = {"status": "postprocessing", "stage": "facelock", "progress": {"kind": "frames", "value": 33, "max": 81},
           "progress_detail": {"known": True, "stage": "facelock", "phase": "facelock", "pct": 40.7,
                               "frame": 33, "frames": 81}}
    line = progress_line(plate_job(job))
    assert "FaceLock" in line and "кадр 33/81" in line and "40%" in line
    early = progress_line(plate_job({"status": "postprocessing", "stage": "facelock", "progress": {"kind": "none"}}))
    assert "FaceLock" in early and "неизвестен" in early
