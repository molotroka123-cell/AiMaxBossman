"""Конвейер Video Factory: создание, атомарный чекпоинт, возобновление, генерация.

`job.json` — единственный durable-чекпоинт (пишется атомарно tmp+os.replace+fsync
после каждого изменения сцены). На рестарте `load()` читает состояние, а
`run_job()` пропускает сцены со статусом `complete` (возобновление без повтора).

Каждая генерация сцены ОБЯЗАТЕЛЬНО проходит допуск Resource Brain: аренда
берётся ДО вызова провайдера и снимается в `finally`. Провайдер НИКОГДА не
вызывается без удержанной аренды. Ретрай пишет новый дубль `take-NNN.mp4` и не
затирает предыдущий.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .. import correlation, errors, events
from ..obs import get_logger
from ..resource_brain import BRAIN, ResourceBrain, WorkloadRequest
from .ffmpeg import validate_video_output
from .model import (
    SCENE_COMPLETE,
    SCENE_FAILED,
    SCENE_PLANNED,
    SCENE_RUNNING,
    JobState,
    Scene,
    VideoJob,
)
from .providers import SyntheticFFmpegProvider, VideoProvider

_log = get_logger("bossman.video_factory")

# Оценка стоимости видео-сцены по умолчанию (единицы совпадают со снимком; в
# проде — байты). Инъектируемы, чтобы тесты сеяли маленький снимок.
_DEFAULT_EST_RAM = 2 * 1024 ** 3      # ~2 ГиБ
_DEFAULT_EST_DISK = 1 * 1024 ** 3     # ~1 ГиБ
_DEFAULT_LEASE_TTL = 600.0            # сек: сцена не должна держать бронь вечно
_DEFAULT_MAX_ATTEMPTS = 3


class VideoFactory:
    """Управляет джобами на диске и исполняет генерацию сцен под допуском Brain.

    Конструктор совместим с прототипом: `VideoFactory(root)` (позиционный корень).
    Остальное инъектируется по имени — `brain` (для тестов с посеянным снимком),
    `provider`, оценки ресурсов, лимит попыток, политика остановки."""

    def __init__(
        self,
        root: str | Path,
        *,
        brain: ResourceBrain | None = None,
        provider: VideoProvider | None = None,
        est_ram: int = _DEFAULT_EST_RAM,
        est_disk: int = _DEFAULT_EST_DISK,
        lease_ttl: float = _DEFAULT_LEASE_TTL,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        halt_on_failure: bool = False,
        db_mirror: bool | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.brain = brain if brain is not None else BRAIN
        self.provider = provider if provider is not None else SyntheticFFmpegProvider()
        self.est_ram = est_ram
        self.est_disk = est_disk
        self.lease_ttl = lease_ttl
        self.max_attempts = max(1, int(max_attempts))
        self.halt_on_failure = halt_on_failure
        if db_mirror is None:
            db_mirror = os.getenv("BOSSMAN_VIDEO_DB_MIRROR", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.db_mirror = db_mirror
        # Учёт удержанных аренд — чтобы stop() мог снять всё без осиротевших броней.
        self._active_leases: set[str] = set()

    # --- пути ---------------------------------------------------------------

    def _job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _scene_dir(self, job: VideoJob, scene_id: str) -> Path:
        return self._job_dir(job.id) / scene_id

    # --- создание / персистентность ----------------------------------------

    def create(self, title: str, prompts, *, duration_s: float = 5.0) -> VideoJob:
        """Создать джобу со сценами `s001..sNNN` и сохранить чекпоинт."""
        scenes = [
            Scene(id=f"s{i + 1:03d}", prompt=str(p), duration_s=float(duration_s))
            for i, p in enumerate(prompts)
        ]
        job = VideoJob(id=uuid.uuid4().hex, title=str(title), scenes=scenes, state=JobState.PLANNED)
        self.save(job)
        events.emit("video.job", job_id=job.id, state=job.state.value, scenes=len(scenes))
        return job

    def save(self, job: VideoJob) -> None:
        """Атомарно записать `job.json` (tmp + fsync + os.replace).

        os.replace атомарен в пределах одной ФС, а fsync гарантирует, что данные
        дошли до диска до подмены — это и есть crash-safe чекпоинт."""
        import time as _time

        job.updated_at = _time.time()
        d = self._job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        data = job.to_public()
        tmp = d / "job.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, d / "job.json")
        if self.db_mirror:
            self._mirror_db(job)

    def load(self, job_id: str) -> VideoJob:
        """Прочитать джобу с диска или бросить `errors.NotFound`."""
        p = self._job_file(job_id)
        if not p.exists():
            raise errors.NotFound(f"video job not found: {job_id}")
        return VideoJob.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def iter_jobs(self) -> list[VideoJob]:
        """Все джобы на диске (для сверки после рестарта и листинга)."""
        out: list[VideoJob] = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            jf = d / "job.json"
            if jf.is_file():
                try:
                    out.append(VideoJob.from_dict(json.loads(jf.read_text(encoding="utf-8"))))
                except Exception as exc:  # noqa: BLE001 — битый чекпоинт не должен рушить листинг
                    _log.warning("skip unreadable job.json in %s: %s", d.name, exc)
        return out

    def list_jobs(self) -> list[dict]:
        return [
            {"id": j.id, "title": j.title, "state": j.state.value, "scenes": len(j.scenes)}
            for j in self.iter_jobs()
        ]

    def mark_queued(self, job: VideoJob) -> None:
        job.state = JobState.QUEUED
        self.save(job)

    # --- публичный чекпоинт сцены (контракт прототипа) ----------------------

    def checkpoint_scene(
        self, job: VideoJob, scene_id: str, *, status: str, output: str | None = None
    ) -> Scene:
        """Проставить статус/выход сцены и атомарно сохранить `job.json`.

        Публичный шов (на нём держится приёмочный тест). Учёт попыток и дублей
        ведёт цикл генерации, поэтому здесь attempts НЕ инкрементируем."""
        s = job.scene(scene_id)
        s.status = status
        if output is not None:
            s.output = output
        self.save(job)
        events.emit("video.scene", job_id=job.id, scene_id=scene_id, status=status, output=output)
        return s

    # --- допуск Resource Brain (обёртка, единственная точка acquire/release) -

    def _acquire_lease(self):
        """Взять аренду под видео-сцену или пробросить `errors.ResourceExhausted`.

        Снимок НЕ передаём — Brain берёт живой снимок пробы; его отсутствие Brain
        трактует консервативно (отказ), а не как «всё свободно»."""
        req = WorkloadRequest(kind="video", estimated_ram=self.est_ram, estimated_disk=self.est_disk)
        lease = self.brain.acquire(req, ttl=self.lease_ttl)  # ResourceExhausted пробрасывается
        self._active_leases.add(lease.id)
        return lease

    def _release_lease(self, lease_id: str) -> None:
        self.brain.release(lease_id)  # идемпотентно
        self._active_leases.discard(lease_id)

    def release_all_leases(self) -> None:
        """Снять все удержанные нами аренды (вызывает stop() подсистемы — чтобы не
        осталось осиротевших броней). Идемпотентно."""
        for lease_id in list(self._active_leases):
            self._release_lease(lease_id)

    # --- генерация сцены -----------------------------------------------------

    async def _generate_once(self, job: VideoJob, scene: Scene) -> None:
        """Одна попытка генерации: допуск → провайдер → валидация.

        Инвариант: провайдер вызывается ТОЛЬКО при удержанной аренде; аренда
        снимается в `finally` при любом исходе (успех/ошибка/отмена)."""
        with correlation.scope(job_id=job.id):
            lease = self._acquire_lease()  # ResourceExhausted → наружу, провайдер не тронут
            out_path: str
            try:
                scene_dir = self._scene_dir(job, scene.id)
                scene_dir.mkdir(parents=True, exist_ok=True)
                scene.attempts += 1
                scene.status = SCENE_RUNNING
                self.save(job)
                out_path = await self.provider.generate(
                    prompt=scene.prompt,
                    duration_s=scene.duration_s,
                    output_dir=str(scene_dir),
                )
            finally:
                self._release_lease(lease.id)

        take_name = Path(out_path).name
        # Дубль фиксируем ДО валидации: даже забракованная попытка остаётся на
        # диске и в списке takes — ретрай не затрёт её, а получит новый номер.
        if take_name not in scene.takes:
            scene.takes.append(take_name)
        await validate_video_output(out_path)  # VideoInvalidOutput при пустом/битом
        scene.output = take_name
        scene.status = SCENE_COMPLETE
        scene.error = None
        self.save(job)
        events.emit("video.scene", job_id=job.id, scene_id=scene.id, status=SCENE_COMPLETE, output=take_name)

    async def _run_scene(self, job: VideoJob, scene: Scene) -> None:
        """Сгенерировать сцену с ограниченным числом попыток.

        `ResourceExhausted` — это backpressure (отказ в допуске), НЕ провал
        генерации: он пробрасывается наружу и НЕ съедает попытку. Ошибки
        провайдера/валидации — съедают попытку и ведут к ретраю (новый take)."""
        last: errors.BossmanError | None = None
        while scene.attempts < self.max_attempts:
            try:
                await self._generate_once(job, scene)
                return
            except errors.ResourceExhausted:
                raise  # backpressure — не трогаем попытки, отдаём наверх
            except (errors.VideoProviderFailed, errors.VideoInvalidOutput) as exc:
                last = exc
                scene.error = exc.detail
                self.save(job)
                _log.warning("scene %s attempt %d failed: %s", scene.id, scene.attempts, exc.code.value)
        scene.status = SCENE_FAILED
        scene.error = last.detail if last else "attempts exhausted"
        self.save(job)
        events.emit("video.scene", job_id=job.id, scene_id=scene.id, status=SCENE_FAILED)
        if self.halt_on_failure:
            raise errors.VideoProviderFailed(
                f"scene {scene.id} failed after {scene.attempts} attempts",
                extra={"scene": scene.id},
            )

    async def run_job(self, job: VideoJob, *, provider: VideoProvider | None = None) -> VideoJob:
        """Исполнить джобу: пропустить готовые сцены (возобновление), сгенерировать
        остальные. Возвращает джобу в финальном состоянии."""
        if provider is not None:
            self.provider = provider
        job.state = JobState.RUNNING
        self.save(job)
        try:
            for scene in job.scenes:
                if scene.status == SCENE_COMPLETE:
                    continue  # возобновление: готовую сцену НЕ перегенерируем
                await self._run_scene(job, scene)
                if scene.status == SCENE_FAILED and self.halt_on_failure:
                    job.state = JobState.FAILED
                    self.save(job)
                    return job
        except errors.ResourceExhausted:
            # Backpressure: возвращаем джобу в очередь (на диске QUEUED), проброс.
            job.state = JobState.QUEUED
            self.save(job)
            raise
        job.state = (
            JobState.COMPLETE
            if all(s.status == SCENE_COMPLETE for s in job.scenes)
            else JobState.FAILED
        )
        self.save(job)
        events.emit("video.job", job_id=job.id, state=job.state.value)
        return job

    # --- опциональное зеркало в Postgres (best-effort, тестам не нужен DB) ---

    def _mirror_db(self, job: VideoJob) -> None:
        """Best-effort зеркало строки джобы в Postgres. Полностью защищено: любое
        исключение (нет пула/таблицы/loop) проглатывается — durable-истина всё
        равно `job.json`, второго durable-хранилища мы не заводим."""
        try:
            import asyncio

            from .. import db

            async def _write() -> None:
                await db.execute(
                    "INSERT INTO video_jobs (id, title, state, updated_at) "
                    "VALUES ($1,$2,$3, now()) "
                    "ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state, updated_at=now()",
                    job.id, job.title, job.state.value,
                )

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_write())
        except Exception:  # noqa: BLE001 — зеркало никогда не влияет на конвейер
            pass


# Ре-экспорт имён модели в пространство pipeline — совместимость с прототипом
# (`from .pipeline import VideoFactory, VideoJob, Scene, JobState`).
__all__ = ["VideoFactory", "VideoJob", "Scene", "JobState"]
