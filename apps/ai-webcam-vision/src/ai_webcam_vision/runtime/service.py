"""Composition root: the whole workload behind one object.

Nothing in this module imports BOSSMAN. The control plane talks to the HTTP
contract in :mod:`ai_webcam_vision.api`; the workload lives here.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .. import __version__
from ..config import CameraMode, Settings
from ..crm import CrmContext, build_crm
from ..crm.base import CrmClient
from ..errors import (
    BaselineMissing,
    CaptureError,
    DependencyMissing,
    PrivacyDenied,
    StaleFrame,
    VisionError,
)
from ..logging_setup import get_logger
from ..pipeline import (
    ANALYZER_ID,
    ANALYZER_VERSION,
    Analyzer,
    BaselineStore,
    BoundedFrameQueue,
    MotionGate,
    SnapshotStore,
    State,
    StateDebouncer,
    Thresholds,
    classify,
)
from ..secretstore import scrub
from ..storage import SCHEMA_VERSION, Store
from ..transport import FfmpegRunner, FrameSource, build_source
from ..transport.base import Frame, ProbeResult
from ..transport.retry import RetryStats, backoff_delays, with_retry
from .jobs import Job, JobManager
from .resources import detect_accelerator, resource_snapshot

log = get_logger("service")

CONTRACT_VERSION = "1.0"
APP_ID = "ai-webcam-vision"


@dataclass
class Counters:
    frames_captured: int = 0
    frames_analyzed: int = 0
    frames_dropped: int = 0
    frames_stale: int = 0
    capture_failures: int = 0
    retry_sleeps: int = 0
    reconnects: int = 0
    observations_stored: int = 0
    snapshots_written: int = 0
    last_capture_at: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class HealthState(StrEnum):
    """What an owner needs to know. "degraded" alone is not actionable."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CAMERA_OFFLINE = "camera_offline"
    CRM_UNAVAILABLE = "crm_unavailable"
    DETECTOR_UNAVAILABLE = "detector_unavailable"


@dataclass
class SourceHealth:
    state: str = "unknown"          # unknown | ok | degraded | unavailable
    detail: str = ""
    checked_at: str | None = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "consecutive_failures": self.consecutive_failures,
        }


@dataclass
class ComponentHealth:
    """One named part of the system: camera, CRM or detector.

    ``disabled`` is not a fault — it is a configured decision, and conflating
    the two is how an owner learns to ignore the health page.
    """

    state: str = "unknown"   # unknown | ok | degraded | offline | unavailable | disabled
    detail: str = ""
    checked_at: str | None = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "consecutive_failures": self.consecutive_failures,
        }


@dataclass
class ObservationResult:
    samples: int
    states: list[str] = field(default_factory=list)
    dropped_frames: int = 0
    failures: int = 0

    def to_dict(self) -> dict:
        return {
            "samples": self.samples,
            "states": self.states,
            "dropped_frames": self.dropped_frames,
            "failures": self.failures,
        }


class VisionService:
    def __init__(
        self,
        settings: Settings,
        *,
        source: FrameSource | None = None,
        crm: CrmClient | None = None,
        runner: FfmpegRunner | None = None,
        store: Store | None = None,
        sleep=None,
    ) -> None:
        self.settings = settings
        self.started_at = datetime.now(timezone.utc)
        self.runner = runner or FfmpegRunner(settings.ffmpeg_path)
        self.source = source or build_source(settings, self.runner)
        self.crm = crm or build_crm(settings)
        self.store = store or Store(settings.db_path, timezone_name=settings.timezone_name)
        self.baseline = BaselineStore(settings.baseline_path)
        self.analyzer = Analyzer(self.baseline, settings.chair_zone, settings.work_zone)
        self.motion = MotionGate(settings.motion_hold_seconds)
        self.queue = BoundedFrameQueue(settings.frame_queue_max)
        self.debouncer = StateDebouncer(settings.debounce_samples)
        self.snapshots = SnapshotStore(settings.snapshot_dir, settings.privacy)
        self.thresholds = Thresholds(
            room=settings.room_threshold,
            chair=settings.chair_threshold,
            work=settings.work_threshold,
        )
        self.counters = Counters()
        self.source_health = SourceHealth()
        self.crm_health = ComponentHealth(
            state="disabled" if self.crm.descriptor.kind == "disabled" else "unknown",
            detail=self.crm.descriptor.detail,
        )
        self.jobs = JobManager(on_change=self._persist_job)
        #: The persistent runtime loop. Created on :meth:`start` when
        #: ``AWV_RUNTIME_ENABLED`` is set; a job-only deployment keeps None.
        self.runtime = None
        self._sleep = sleep or asyncio.sleep
        self._stopping = asyncio.Event()
        self._closed = False
        self._register_jobs()

    # --------------------------------------------------------------- setup
    def _register_jobs(self) -> None:
        self.jobs.register("probe", self._job_probe)
        self.jobs.register("baseline", self._job_baseline)
        self.jobs.register("sample", self._job_sample)
        self.jobs.register("observe", self._job_observe)
        self.jobs.register("snapshot", self._job_snapshot)

    def _persist_job(self, job: Job) -> None:
        self.store.upsert_job(job.to_record())

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.runtime_enabled and self.runtime is None:
            from .supervisor import RuntimeSupervisor

            self.runtime = RuntimeSupervisor(self)
            await self.runtime.start()
        log.info("service started room=%s mode=%s", self.settings.room_id, self.settings.camera_mode.value)

    async def aclose(self) -> None:
        """Clean shutdown: stop loops, cancel jobs, close every resource."""
        if self._closed:
            return
        self._closed = True
        self._stopping.set()
        if self.runtime is not None:
            await self.runtime.stop()
        await self.jobs.shutdown()
        await self.source.aclose()
        await self.crm.aclose()
        self.queue.clear()
        log.info("service stopped")

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    # ------------------------------------------------------------- capture
    def _sample_interval(self) -> float:
        base = self.settings.active_interval if self.motion.active() else self.settings.idle_interval
        floor = 1.0 / self.settings.max_sample_rate_hz
        return max(base, floor)

    async def _grab_with_retry(self) -> Frame:
        stats = RetryStats()

        def on_retry(attempt: int, delay: float, exc: VisionError) -> None:
            self.counters.retry_sleeps += 1
            self.counters.capture_failures += 1
            self.source_health.consecutive_failures += 1
            self.counters.last_error = exc.safe_message
            self.counters.last_error_code = exc.code
            log.warning("capture attempt %s failed, retrying in %.2fs", attempt, delay)

        try:
            frame = await with_retry(
                self.source.grab,
                self.settings.retry,
                sleep=self._sleep,
                on_retry=on_retry,
                stats=stats,
            )
        except VisionError as exc:
            self.counters.capture_failures += 1
            self.source_health.consecutive_failures += 1
            self.source_health.state = "unavailable"
            self.source_health.detail = exc.safe_message
            self.source_health.checked_at = datetime.now(timezone.utc).isoformat()
            self.counters.last_error = exc.safe_message
            self.counters.last_error_code = exc.code
            raise

        if stats.attempts > 1:
            self.counters.reconnects += 1
        self.counters.frames_captured += 1
        self.counters.last_capture_at = frame.ts.isoformat()
        self.source_health.consecutive_failures = 0
        self.source_health.state = "ok"
        self.source_health.detail = "last capture succeeded"
        self.source_health.checked_at = frame.ts.isoformat()
        return frame

    async def probe(self) -> ProbeResult:
        result = await self.source.probe()
        self.source_health.checked_at = datetime.now(timezone.utc).isoformat()
        if result.ok:
            self.source_health.state = "ok"
            self.source_health.detail = "probe succeeded"
        else:
            self.source_health.state = "unavailable"
            self.source_health.detail = result.error or "probe failed"
            self.counters.last_error = result.error
            self.counters.last_error_code = result.error_code
        return result

    async def capture_baseline(self) -> dict:
        frame = await self._grab_with_retry()
        self.baseline.save(frame)
        self.analyzer.reset()
        log.info("baseline captured %sx%s", frame.width, frame.height)
        return {
            "captured_at": frame.ts.isoformat(),
            "width": frame.width,
            "height": frame.height,
            "path": str(self.baseline.path),
            "source": self.source.descriptor.to_dict(),
        }

    # -------------------------------------------------------------- sample
    @property
    def max_frame_age(self) -> float:
        return self.settings.max_frame_age

    def _reject_if_stale(self, frame: Frame) -> None:
        """A late frame is evidence about the past, never about now.

        Without this a capture that took a minute (slow reconnect, congested
        VPN, an ffmpeg that only just gave up) is timestamped and stored as
        the room's current state, inventing occupancy that already ended.
        """
        age = (datetime.now(timezone.utc) - frame.ts).total_seconds()
        if age > self.max_frame_age:
            self.counters.frames_stale += 1
            self.source_health.state = "degraded"
            self.source_health.detail = f"frame arrived {age:.1f}s late"
            raise StaleFrame(
                f"frame is {age:.1f}s old, budget is {self.max_frame_age:.1f}s"
            )

    async def _analyze_and_store(self, frame: Frame) -> dict:
        self._reject_if_stale(frame)
        evidence = self.analyzer.analyze(frame, self.motion.active())
        self.counters.frames_analyzed += 1
        crm_context = await self._crm_context(evidence.ts)
        classification = classify(evidence, crm_context, self.thresholds)
        debounced = self.debouncer.feed(classification.state)
        self.store.add_observation(
            room_id=self.settings.room_id,
            evidence=evidence,
            crm=crm_context,
            classification=classification,
            debounced_state=debounced,
            source_kind=self.source.descriptor.kind.value,
            source_is_mock=self.source.descriptor.is_mock_camera,
        )
        self.counters.observations_stored += 1
        return {
            "room_id": self.settings.room_id,
            "evidence": evidence.to_dict(),
            "classification": classification.to_dict(),
            "debounced_state": debounced.value,
            "crm": crm_context.to_dict(),
            "source": self.source.descriptor.to_dict(),
            "motion": self.motion.state().to_dict(),
        }

    async def _crm_context(self, at: datetime) -> CrmContext:
        now = datetime.now(timezone.utc).isoformat()
        try:
            context = await self.crm.context(self.settings.room_id, at)
        except VisionError as exc:
            log.warning("CRM lookup failed")
            self.counters.last_error = exc.safe_message
            self.counters.last_error_code = exc.code
            self.crm_health.state = "unavailable"
            self.crm_health.detail = exc.safe_message
            self.crm_health.checked_at = now
            self.crm_health.consecutive_failures += 1
            return CrmContext(available=False, source=f"error:{exc.code}", is_mock=self.crm.descriptor.is_mock)
        self.crm_health.checked_at = now
        self.crm_health.consecutive_failures = 0
        if self.crm.descriptor.kind == "disabled":
            self.crm_health.state = "disabled"
            self.crm_health.detail = "no CRM configured"
        elif context.stale:
            self.crm_health.state = "degraded"
            self.crm_health.detail = "CRM answered with data older than the freshness budget"
        else:
            self.crm_health.state = "ok"
            self.crm_health.detail = "last lookup succeeded"
        return context

    async def sample_once(self) -> dict:
        frame = await self._grab_with_retry()
        dropped = self.queue.put(frame)
        self.counters.frames_dropped += dropped
        current = self.queue.latest() or frame
        return await self._analyze_and_store(current)

    # ------------------------------------------------------------- observe
    async def observe(self, *, duration: float, max_samples: int | None = None) -> ObservationResult:
        """Run producer and consumer until the budget or shutdown ends it."""
        if not self.baseline.exists:
            raise BaselineMissing("capture an empty-room baseline before observing")

        result = ObservationResult(samples=0)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, duration)

        async def produce() -> None:
            while not stop.is_set() and not self._stopping.is_set():
                if loop.time() >= deadline:
                    break
                try:
                    frame = await self._grab_with_retry()
                except VisionError as exc:
                    result.failures += 1
                    log.error("capture gave up after bounded retries: %s", exc.code)
                    stop.set()
                    raise
                dropped = self.queue.put(frame)
                result.dropped_frames += dropped
                self.counters.frames_dropped += dropped
                await self._sleep(self._sample_interval())

        async def consume() -> None:
            while not stop.is_set() and not self._stopping.is_set():
                frame = self.queue.latest()
                if frame is None:
                    if loop.time() >= deadline:
                        break
                    await self._sleep(min(0.05, self._sample_interval()))
                    continue
                observation = await self._analyze_and_store(frame)
                result.samples += 1
                result.states.append(observation["debounced_state"])
                if max_samples is not None and result.samples >= max_samples:
                    stop.set()
                    break
                if loop.time() >= deadline and self.queue.stats().size == 0:
                    break

        producer = asyncio.create_task(produce(), name="observe:producer")
        consumer = asyncio.create_task(consume(), name="observe:consumer")
        try:
            done, pending = await asyncio.wait(
                {producer, consumer},
                timeout=max(1.0, duration + 30.0),
                return_when=asyncio.FIRST_EXCEPTION,
            )
            stop.set()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=5)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            stop.set()
            for task in (producer, consumer):
                if not task.done():
                    task.cancel()
            await asyncio.gather(producer, consumer, return_exceptions=True)
        return result

    # ------------------------------------------------------------ snapshot
    async def capture_snapshot(self) -> dict:
        if not self.snapshots.enabled:
            raise PrivacyDenied("snapshots are disabled; set AWV_SNAPSHOTS_ENABLED=true to allow them")
        result = await self.snapshots.capture(self.source)
        self.counters.snapshots_written += 1
        return result.to_dict()

    # ---------------------------------------------------------- job bodies
    async def _job_probe(self, job: Job) -> dict:
        return (await self.probe()).to_dict()

    async def _job_baseline(self, job: Job) -> dict:
        result = await self.capture_baseline()
        self._record_artifact(job, "baseline", str(self.baseline.path), self.baseline.path.stat().st_size,
                              {"width": result["width"], "height": result["height"], "format": "npy-gray"})
        return result

    async def _job_sample(self, job: Job) -> dict:
        return await self.sample_once()

    async def _job_observe(self, job: Job) -> dict:
        duration = float(job.params.get("duration_seconds", 10.0))
        max_samples = job.params.get("max_samples")
        result = await self.observe(
            duration=min(duration, 3600.0),
            max_samples=int(max_samples) if max_samples is not None else None,
        )
        return result.to_dict()

    async def _job_snapshot(self, job: Job) -> dict:
        result = await self.capture_snapshot()
        self._record_artifact(job, "snapshot", result["path"], result["bytes"], {
            "max_width": result["max_width"],
            "blur_sigma": result["blur_sigma"],
            "grayscale": True,
        })
        return result

    def _record_artifact(self, job: Job, kind: str, path: str | None, size: int, meta: dict) -> None:
        artifact_id = uuid.uuid4().hex
        created = datetime.now(timezone.utc)
        self.store.add_artifact(
            artifact_id=artifact_id,
            job_id=job.id,
            kind=kind,
            path=path,
            size_bytes=size,
            created_at=created,
            meta=meta,
        )
        job.artifacts.append({
            "id": artifact_id,
            "job_id": job.id,
            "kind": kind,
            "path": path,
            "bytes": size,
            "created_at": created.isoformat(),
            "meta": meta,
        })

    # -------------------------------------------------------------- health
    def _components(self, ffmpeg, needs_ffmpeg: bool) -> dict[str, ComponentHealth]:
        """Camera, CRM and detector reported separately.

        Three different faults need three different actions: reboot the
        camera, call the CRM vendor, capture a baseline. A single "degraded"
        tells the owner none of that.
        """
        camera_state = {
            "unavailable": "offline",
            "ok": "ok",
            "degraded": "degraded",
        }.get(self.source_health.state, "unknown")
        camera = ComponentHealth(
            state=camera_state,
            detail=self.source_health.detail or self.source.descriptor.detail,
            checked_at=self.source_health.checked_at,
            consecutive_failures=self.source_health.consecutive_failures,
        )

        if needs_ffmpeg and not ffmpeg.available:
            detector = ComponentHealth(state="unavailable", detail=ffmpeg.reason or "ffmpeg unavailable")
        elif not self.baseline.exists:
            detector = ComponentHealth(
                state="unavailable",
                detail="empty-room baseline not captured",
            )
        else:
            detector = ComponentHealth(
                state="ok",
                detail=f"{ANALYZER_ID}:{ANALYZER_VERSION} ready",
            )
        return {"camera": camera, "crm": self.crm_health, "detector": detector}

    @staticmethod
    def _health_state(components: dict[str, ComponentHealth]) -> HealthState:
        """Worst-first: without a detector nothing else matters, and a dead
        camera outranks a dead CRM because there is no evidence at all."""
        if components["detector"].state == "unavailable":
            return HealthState.DETECTOR_UNAVAILABLE
        if components["camera"].state == "offline":
            return HealthState.CAMERA_OFFLINE
        if components["crm"].state == "unavailable":
            return HealthState.CRM_UNAVAILABLE
        if any(c.state in {"degraded", "unknown"} for c in components.values()):
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    def health(self) -> dict:
        ffmpeg = self.runner.info()
        needs_ffmpeg = self.settings.camera_mode in (CameraMode.RTSP, CameraMode.FILE)
        blockers: list[str] = []
        if needs_ffmpeg and not ffmpeg.available:
            blockers.append(ffmpeg.reason or "ffmpeg unavailable")
        if not self.baseline.exists:
            blockers.append("empty-room baseline not captured")
        if self.source_health.state == "unavailable":
            blockers.append(f"source unavailable: {self.source_health.detail}")
        if self.crm_health.state == "unavailable":
            blockers.append(f"CRM unavailable: {self.crm_health.detail}")

        components = self._components(ffmpeg, needs_ffmpeg)
        health_state = self._health_state(components)

        if needs_ffmpeg and not ffmpeg.available:
            status = "unavailable"
        elif blockers:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "health_state": health_state.value,
            "components": {name: item.to_dict() for name, item in components.items()},
            "runtime": self.runtime.status() if self.runtime is not None else {"state": "stopped", "running": False},
            "app": {"id": APP_ID, "version": __version__, "contract": CONTRACT_VERSION},
            "room_id": self.settings.room_id,
            "uptime_seconds": round((datetime.now(timezone.utc) - self.started_at).total_seconds(), 2),
            "blockers": blockers,
            "camera": {
                "mode": self.settings.camera_mode.value,
                **self.source.descriptor.to_dict(),
                "health": self.source_health.to_dict(),
            },
            "crm": self.crm.descriptor.to_dict(),
            "analyzer": {
                "id": ANALYZER_ID,
                "version": ANALYZER_VERSION,
                "is_mock": False,
                "kind": "deterministic_pixel_heuristic",
                "provider": "local-numpy",
                "note": "no ML model and no external model provider is used",
            },
            "ffmpeg": ffmpeg.to_dict(),
            "compute": detect_accelerator(probe_nvidia=False).to_dict(),
            "baseline": {"present": self.baseline.exists, "path": str(self.baseline.path)},
            "motion": self.motion.state().to_dict(),
            "queue": self.queue.stats().to_dict(),
            "counters": self.counters.to_dict(),
            "storage": {"path": str(self.settings.db_path), "schema_version": SCHEMA_VERSION},
        }

    def capabilities(self) -> dict:
        return {
            "app": {"id": APP_ID, "version": __version__, "contract": CONTRACT_VERSION},
            "role": "workload",
            "control_plane": "external (BOSSMAN); this app imports nothing from it",
            "job_types": self.jobs.job_types,
            "endpoints": {
                "health": "GET /api/v1/health",
                "capabilities": "GET /api/v1/capabilities",
                "jobs.create": "POST /api/v1/jobs",
                "jobs.status": "GET /api/v1/jobs/{job_id}",
                "jobs.cancel": "POST /api/v1/jobs/{job_id}/cancel",
                "jobs.list": "GET /api/v1/jobs",
                "artifacts.list": "GET /api/v1/artifacts",
                "metrics": "GET /api/v1/metrics",
                "room_metrics": "GET /api/v1/rooms/{room_id}/metrics/today",
                "motion_hook": "POST /hooks/motion",
            },
            "camera": {
                "mode": self.settings.camera_mode.value,
                **self.source.descriptor.to_dict(),
            },
            "crm": self.crm.descriptor.to_dict(),
            "model": {
                "provider": "none",
                "is_mock": False,
                "analyzer": f"{ANALYZER_ID}:{ANALYZER_VERSION}",
                "note": "pixel heuristic, not machine learning; no VLM/LLM is loaded or called",
            },
            "compute": detect_accelerator().to_dict(),
            "ffmpeg": self.runner.info().to_dict(),
            "limits": {
                "frame_queue_max": self.settings.frame_queue_max,
                "max_sample_rate_hz": self.settings.max_sample_rate_hz,
                "connect_timeout_seconds": self.settings.connect_timeout,
                "capture_timeout_seconds": self.settings.capture_timeout,
                "retry_backoff_seconds": backoff_delays(self.settings.retry),
                "retry_max_attempts": self.settings.retry.max_attempts,
            },
            "privacy": {
                **self.settings.public_dict()["privacy"],
                "snapshot_policy": self.snapshots.policy(),
                "denied_by_design": [
                    "audio_capture",
                    "face_identification",
                    "patient_identification_from_pixels",
                    "raw_video_retention",
                ],
                "egress": {
                    "crm_enabled": self.settings.privacy.crm_egress_enabled,
                    "telemetry_enabled": self.settings.privacy.telemetry_enabled,
                    "note": "no outbound traffic occurs unless explicitly enabled",
                },
            },
            "states": [state.value for state in State],
            "config": self.settings.public_dict(),
        }

    def metrics(self) -> dict:
        today = self.room_metrics_today()
        return {
            "app": {"id": APP_ID, "version": __version__},
            #: Seconds of the current day actually covered by observations.
            #: The launcher card reads this; it must exist, and it must be
            #: measured coverage rather than wall-clock time.
            "monitored_today": today["monitored_seconds"],
            "today": today,
            "counters": self.counters.to_dict(),
            "queue": self.queue.stats().to_dict(),
            "motion": self.motion.state().to_dict(),
            "source_health": self.source_health.to_dict(),
            "observations": self.store.count_observations(self.settings.room_id),
            "compute": detect_accelerator(probe_nvidia=False).to_dict(),
            "resources": resource_snapshot(),
        }

    def room_metrics_today(self, room_id: str | None = None, at: datetime | None = None) -> dict:
        room = room_id or self.settings.room_id
        start, end = self.store.today_bounds(at)
        return self.store.metrics(room, start, end)

    def safe_error(self, exc: BaseException) -> dict:
        if isinstance(exc, VisionError):
            return {"error": exc.safe_message, "code": exc.code}
        return {"error": scrub(f"{type(exc).__name__}: {exc}"), "code": "internal_error"}


def build_service(settings: Settings | None = None, **kwargs: Any) -> VisionService:
    return VisionService(settings or Settings.from_env(), **kwargs)
