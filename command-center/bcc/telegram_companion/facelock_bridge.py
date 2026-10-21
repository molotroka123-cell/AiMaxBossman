"""Pult FaceLock for video (owner 21.10: «Face lock в видео в пульте тоже должен быть»).

Owner-only switch ``/facelock on|off`` (persisted like /style and the presets). When it is on, a photo -> video clip
(«Оживить», /animate, /vidphoto, a photo with a video caption) is not sent to Studio but queued as a Direct Generator
job — the same job API and the same FaceLock the phone app uses: ``facelock: true`` with the owner's photo as @1 (the
start picture). The face is taken ONLY from that picture, exactly like photo FaceLock; nothing wider (no saved /ref
portraits, no other person's face).

Honest limits, always said in the caption, never silent:
  * text -> video has no start picture: «FaceLock не применён: нет стартового фото»;
  * a FaceLock clip is one direct-generator pass (≤ 5 s); a longer clip goes through Studio without FaceLock and says so;
  * the Direct Generator's own refusal (STOP, gate, no FaceFusion …) arrives as its label «FaceLock не применён: …».
The progress plate shows the FaceLock stage with real frame counts. /cancel and the pult STOP cancel the job.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import secrets
import time

from .config import CompanionError, Person
from .gen_progress import GenProgressPlate

FACELOCK_MODEL = "wan2.2-ti2v-5b"          # the Direct Generator's id of the same Wan2.2 TI2V-5B weights Studio uses
FACELOCK_LENGTHS = {"5s": 5}               # one direct-generator pass; Wan TI2V-5B max_seconds = 5
NO_START_PICTURE = "FaceLock не применён: нет стартового фото (лицо берётся только со стартового фото, @1)"
TOO_LONG = "FaceLock не применён: FaceLock-ролик в пульте — один проход до 5 с; этот снят без FaceLock"
STORE_KEY = "facelock:video:"


class FaceLockVideoMixin:
    """Mixed into Companion (needs: store, core, telegram, settings, image_job, button, image_poll_seconds)."""

    def facelock_on(self, person: Person) -> bool:
        return person.role == "owner" and self.store.get(STORE_KEY + person.key, False) is True

    def facelock_button(self, person: Person):
        on = self.facelock_on(person)
        return self.button(person, "🔒 FaceLock: вкл" if on else "🔓 FaceLock: выкл",
                           "/facelock off" if on else "/facelock on")

    def facelock_command(self, person: Person, arg: str):
        from .service import Reply
        if person.role != "owner":
            return "FaceLock доступен только владельцу."
        word = (arg or "").strip().lower()
        if word in {"on", "вкл", "1"}:
            self.store.put(STORE_KEY + person.key, True)
        elif word in {"off", "выкл", "0"}:
            self.store.put(STORE_KEY + person.key, False)
        elif word:
            return "Используйте /facelock on или /facelock off."
        on = self.facelock_on(person)
        text = ("🔒 FaceLock для видео: " + ("ВКЛЮЧЁН" if on else "выключен") + ".\n"
                "Работает для роликов из фото («Оживить», /animate, фото с подписью про видео): после генерации лицо "
                "со стартового фото вставляется в каждый кадр, сходство проверяется минимум на 8 кадрах по всему "
                "ролику; частота кадров, длительность и звук не меняются. Ролик до 5 с (один проход). Если проверка не "
                "пройдена или FaceLock недоступен — придёт ролик модели с пометкой «FaceLock не применён: причина».\n"
                "Для /video без фото FaceLock не применяется (лицо брать неоткуда) — это будет написано в подписи.")
        return Reply(text, [[self.facelock_button(person)]])

    def facelock_job_spec(self, prompt: str, framed: bytes, size: tuple[int, int], length: str, seed: int) -> dict:
        """The Direct Generator job the pult sends for a FaceLock clip (pure: tests check it as built)."""
        w, h = size
        return {"model": FACELOCK_MODEL, "prompt": prompt[:2000], "mode": "DIRECT", "resolution": f"{w}x{h}",
                "duration": FACELOCK_LENGTHS[length], "seed": seed, "image_b64": base64.b64encode(framed).decode(),
                "facelock": True}

    async def generate_facelock_video(self, person: Person, prompt: str, framed: bytes, size: tuple[int, int],
                                      length: str, timings: dict | None = None) -> None:
        """Photo -> clip through the Direct Generator with FaceLock. The clip is sent only after sha256 verification;
        its caption always carries the FaceLock outcome."""
        from .service import IMAGE_MODELS, VIDEO_MODEL, VIDEO_SEND_LIMIT, is_mp4
        s = self.settings
        if person.role != "owner":
            raise CompanionError("IMAGE_GEN_GUESTS_DISABLED")
        if not s.image_enabled:
            raise CompanionError("IMAGE_GEN_DISABLED")
        if self.store.get("delegation_locked", False):
            raise CompanionError("MEDIA_STOPPED")
        if self.image_job is not None:
            raise CompanionError("IMAGE_GEN_BUSY")
        self.image_job = {"id": None, "cancel": False, "who": person.key, "direct": True}
        try:
            seed = secrets.randbelow(2**31 - 1)
            started = time.monotonic()
            job = await self.core.direct_gen_create(self.facelock_job_spec(prompt, framed, size, length, seed))
            job_id = job["job_id"]
            self.image_job["id"] = job_id
            created_at, running_at = time.monotonic(), None
            label = IMAGE_MODELS.get(VIDEO_MODEL, FACELOCK_MODEL)
            status_text = (f"Оживляю фото с FaceLock локально ({label}, {size[0]}×{size[1]}, 5 с). После генерации — "
                           "FaceLock по кадрам и проверка сходства. /cancel — отменить.")
            keys = [[self.button(person, "✖️ Отмена", "/cancel")]]
            mid = None
            with contextlib.suppress(CompanionError):
                mid = await self.telegram.send(person, status_text, keys)
            plate = GenProgressPlate(self.telegram, person, mid, status_text, keys)
            deadline = 3 * 3600
            while True:
                if self.image_job["cancel"]:            # /cancel, «✖️ Отмена» or the pult ⛔ СТОП (console.stop)
                    stopped = await self._facelock_cancel(job_id)
                    raise CompanionError("IMAGE_GEN_CANCELLED" if stopped else "IMAGE_GEN_CANCEL_UNCONFIRMED")
                if time.monotonic() - started > deadline:
                    stopped = await self._facelock_cancel(job_id)
                    raise CompanionError("IMAGE_GEN_TIMEOUT" if stopped else "IMAGE_GEN_CANCEL_UNCONFIRMED")
                job = await self.core.direct_gen_job(job_id)
                status = job.get("status")
                if running_at is None and status not in {"queued"}:
                    running_at = time.monotonic()
                if status == "completed":
                    break
                if status in {"failed", "cancelled", "stopped"}:
                    raise CompanionError("IMAGE_GEN_CANCELLED" if status in {"cancelled", "stopped"} else "IMAGE_GEN_FAILED")
                await plate.tick(job)
                await asyncio.sleep(self.image_poll_seconds)
            result = job.get("result") or {}
            if type(result.get("bytes")) is int and result["bytes"] > VIDEO_SEND_LIMIT:
                raise CompanionError("VIDEO_TOO_LARGE_FOR_TELEGRAM")
            data = await self.core.direct_gen_file(job_id, VIDEO_SEND_LIMIT)
            if hashlib.sha256(data).hexdigest() != result.get("sha256"):
                raise CompanionError("IMAGE_BYTES_UNVERIFIED")
            lock = job.get("facelock") or {}
            lock_line = str(lock.get("label") or ("FaceLock применён" if lock.get("status") == "applied"
                                                  else "FaceLock не применён: Bossman не сообщил причину"))
            gate = lock.get("gate") or {}
            if lock.get("status") == "applied" and gate.get("measured"):
                lock_line += f" · сходство min {gate.get('min')} / медиана {gate.get('median')} на {gate['measured']} кадрах"
            elapsed = round(time.monotonic() - started)
            caption = (f"🎬 {prompt[:300]}\nМодель: {label} · seed {seed} · {elapsed} с · локально, Direct Generator "
                       f"(проверено: sha256 совпал)\n{'🔒' if lock.get('status') == 'applied' else '⚠️'} {lock_line}"
                       "\nОживлено из фото · 5 с")
            if timings is not None:
                done_at = time.monotonic()
                running_at = running_at or done_at
                p = job.get("params") or {}
                timings.update({"prep_s": round(created_at - started, 1), "queue_s": round(running_at - created_at, 1),
                                "gen_s": round(done_at - running_at, 1), "width": p.get("width"),
                                "height": p.get("height"), "frames": p.get("frames"), "steps": p.get("steps"),
                                "seconds": p.get("duration")})
            keyboard = [[self.facelock_button(person)]]
            if is_mp4(data):
                await self.telegram.send_video(person, data, caption, keyboard)
            else:
                await self.telegram.send_media_file(person, f"bossman-{job_id[:8]}.webm", data,
                                                    result.get("mime") or "video/webm", caption, keyboard)
            await plate.done(elapsed)
            self.store.remember(person.key, "[видео FaceLock] " + prompt[:500],
                                f"Сгенерировано локально ({label}), seed {seed}; {lock_line}")
        finally:
            self.image_job = None

    async def _facelock_cancel(self, job_id: str) -> bool:
        try:
            await self.core.direct_gen_cancel(job_id)
            return True
        except CompanionError:
            pass
        try:
            return (await self.core.direct_gen_job(job_id)).get("status") in {"cancelled", "failed", "completed", "stopped"}
        except CompanionError:
            return False
