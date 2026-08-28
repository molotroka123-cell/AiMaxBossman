"""The control contract.

BOSSMAN (or any other control plane) is expected to drive this app through
exactly these operations:

    health, capabilities, jobs.create, jobs.status, jobs.cancel,
    jobs.list, artifacts.list, metrics

The contract is plain Python and plain dicts. It imports nothing from `bcc.*`
and nothing from any BOSSMAN package: this application is a workload, not a
plugin. `api.py` is a thin HTTP skin over this same object.
"""

from __future__ import annotations

import asyncio
import os
import platform
import resource
import time
import uuid
from pathlib import Path

from . import __version__
from .artifacts import list_artifacts
from .capabilities import capability_map
from .config import Settings, load_settings
from .errors import Ai3dError, ConfirmationRequiredError, JobNotFoundError, UnsafeGcodeError
from .gcode import GCodeScan, scan_gcode
from .mesh import sha256_file
from .paths import resolve_within, safe_job_id
from .pipeline import GCODE_NAME, JobRequest, Pipeline
from .printer import PhysicalAction, PhysicalRequest, Transport, confirmation_token, execute_physical
from .profile import PrinterProfile, load_material_defaults
from .spec import DesignSpec
from .storage import JobStore

CONTRACT_VERSION = "ai-3d-maker/control/1"

OPERATIONS = (
    "health",
    "capabilities",
    "jobs.create",
    "jobs.status",
    "jobs.cancel",
    "jobs.list",
    "artifacts.list",
    "metrics",
    "gcode.scan",
    "printer.confirm",
)


class ControlPlane:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.profile = PrinterProfile.load(self.settings.printer_profile)
        self.store = JobStore(
            self.settings.jobs_dir,
            job_quota_bytes=self.settings.job_disk_quota_bytes,
            total_quota_bytes=self.settings.total_disk_quota_bytes,
            max_retained=self.settings.max_jobs_retained,
        )
        self.pipeline = Pipeline(self.settings, self.profile, self.store)
        self.started_at = time.time()
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------- health
    def health(self) -> dict:
        return {
            "status": "ok",
            "app": "ai-3d-maker",
            "version": __version__,
            "contract": CONTRACT_VERSION,
            "uptime_s": round(time.time() - self.started_at, 3),
            "printer_profile": {
                "id": self.profile.id,
                "model": self.profile.model,
                "source": self.profile.source_path,
            },
            "physical_printing_enabled": self.settings.allow_physical_print,
            "transport": self.settings.printer_transport,
        }

    def capabilities(self) -> dict:
        payload = capability_map(self.settings)
        payload["contract"] = CONTRACT_VERSION
        payload["operations"] = list(OPERATIONS)
        payload["printer_profile"] = self.profile.as_dict()
        try:
            payload["material_defaults_unverified"] = load_material_defaults(self.settings.material_profile)
        except Exception as exc:
            payload["material_defaults_unverified"] = {"error": str(exc)}
        return payload

    # --------------------------------------------------------------- jobs
    def _parse_request(self, payload: dict) -> JobRequest:
        kind = payload.get("kind") or ("design" if payload.get("spec") else "import")
        spec = None
        if kind == "design":
            raw = payload.get("spec")
            if raw is None:
                raise Ai3dError("a design job requires a 'spec' object")
            spec = DesignSpec.model_validate(raw)
        return JobRequest(
            kind=kind,
            spec=spec,
            source_stl=payload.get("source_stl"),
            source_units=payload.get("source_units", "mm"),
            scale=float(payload.get("scale", 1.0)),
            auto_orient=bool(payload.get("auto_orient", True)),
            place_on_bed=bool(payload.get("place_on_bed", True)),
            drop_small_components=bool(payload.get("drop_small_components", False)),
            slice_after_build=bool(payload.get("slice", False)),
            slicer_settings=dict(payload.get("slicer_settings") or {}),
            calibrated_tolerance_mm=payload.get("calibrated_tolerance_mm"),
            scale_to_fit=bool(payload.get("scale_to_fit", False)),
        )

    async def jobs_create(self, payload: dict) -> dict:
        """Create and run a job. `wait=False` returns as soon as it is scheduled."""
        request = self._parse_request(payload)
        job_id = safe_job_id(payload.get("job_id") or f"job-{uuid.uuid4().hex[:12]}")
        record = self.store.create(job_id, request.kind, request.as_dict())
        wait = bool(payload.get("wait", True))
        if wait:
            result = await self.pipeline.run(job_id, request)
            return {"job_id": job_id, "accepted": True, "waited": True, "result": result}
        task = asyncio.create_task(self.pipeline.run(job_id, request))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))
        return {"job_id": job_id, "accepted": True, "waited": False, "status": record.status}

    def jobs_status(self, job_id: str) -> dict:
        record = self.store.get(job_id)
        return record.as_dict()

    def jobs_cancel(self, job_id: str) -> dict:
        record = self.store.request_cancel(job_id)
        task = self._tasks.get(record.id)
        if task is not None and not task.done():
            task.cancel()
        return {"job_id": record.id, "status": record.status, "cancel_requested": record.cancel_requested}

    def jobs_list(self, *, limit: int = 50, offset: int = 0, status: str | None = None) -> dict:
        records = self.store.list(limit=limit, offset=offset, status=status)
        return {
            "jobs": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                    "printable": (r.result or {}).get("printable"),
                    "bytes_used": r.bytes_used,
                }
                for r in records
            ],
            "count": len(records),
            "limit": limit,
            "offset": offset,
        }

    # ---------------------------------------------------------- artifacts
    def artifacts_list(self, job_id: str) -> dict:
        record = self.store.get(job_id)
        job_dir = Path(record.directory)
        entries = list_artifacts(job_dir)
        return {
            "job_id": record.id,
            "status": record.status,
            "printable": (record.result or {}).get("printable"),
            "directory": str(job_dir),
            "artifacts": [e.as_dict() for e in entries],
            "count": len(entries),
            "note": "checksums are recomputed from disk on every call",
        }

    def artifact_path(self, job_id: str, name: str) -> Path:
        record = self.store.get(job_id)
        path = resolve_within(Path(record.directory), name)
        if not path.is_file():
            raise JobNotFoundError(f"artifact {name!r} not found in job {record.id!r}")
        return path

    # ------------------------------------------------------------ metrics
    def metrics(self) -> dict:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "app": "ai-3d-maker",
            "version": __version__,
            "uptime_s": round(time.time() - self.started_at, 3),
            "jobs": self.store.metrics(),
            "process": {
                "pid": os.getpid(),
                "max_rss_kb": usage.ru_maxrss,
                "user_cpu_s": round(usage.ru_utime, 3),
                "system_cpu_s": round(usage.ru_stime, 3),
                "active_tasks": len(self._tasks),
            },
            "host": {
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
            },
            "limits": {
                "job_timeout_s": self.settings.job_timeout_s,
                "job_disk_quota_bytes": self.settings.job_disk_quota_bytes,
                "total_disk_quota_bytes": self.settings.total_disk_quota_bytes,
                "max_upload_bytes": self.settings.max_upload_bytes,
                "max_triangles": self.settings.max_triangles,
            },
        }

    # -------------------------------------------------------------- gcode
    def gcode_scan(self, text: str) -> dict:
        return scan_gcode(text, self.profile, strict_unknown=self.settings.strict_gcode).as_dict()

    # ----------------------------------------------------------- physical
    def printer_confirm(self, payload: dict) -> dict:
        """The one entry point that can reach hardware. Everything else cannot."""
        job_id = safe_job_id(payload.get("job_id", ""))
        record = self.store.get(job_id)
        job_dir = Path(record.directory)
        action = PhysicalAction(payload.get("action", "transfer_to_media"))
        artifact_name = payload.get("artifact")
        if artifact_name:
            artifact = self.artifact_path(job_id, artifact_name)
        else:
            gcode = job_dir / GCODE_NAME
            stl = job_dir / "model.stl"
            artifact = gcode if gcode.is_file() else stl
        if not artifact.is_file():
            raise Ai3dError(f"job {job_id!r} has no artifact to send")

        digest = sha256_file(artifact)
        scan: GCodeScan | None = None
        if artifact.suffix.lower() == ".gcode":
            scan = scan_gcode(
                artifact.read_text(encoding="utf-8", errors="replace"),
                self.profile,
                strict_unknown=self.settings.strict_gcode,
            )

        request = PhysicalRequest(
            action=action,
            job_id=job_id,
            artifact_path=artifact,
            artifact_sha256=digest,
            confirmation=payload.get("confirmation", ""),
            transport=Transport(payload.get("transport") or self.settings.printer_transport),
        )
        try:
            result = execute_physical(
                request,
                allow_physical=self.settings.allow_physical_print,
                scan=scan,
                media_dir=self.settings.printer_media_dir,
            )
        except (ConfirmationRequiredError, UnsafeGcodeError) as exc:
            payload_out = exc.as_dict()
            payload_out["job_id"] = job_id
            payload_out["artifact"] = str(artifact)
            payload_out["artifact_sha256"] = digest
            return payload_out
        out = result.as_dict()
        out["job_id"] = job_id
        out["artifact"] = str(artifact)
        out["artifact_sha256"] = digest
        return out

    def confirmation_for(self, job_id: str, artifact_name: str | None = None) -> dict:
        record = self.store.get(job_id)
        job_dir = Path(record.directory)
        if artifact_name:
            artifact = self.artifact_path(job_id, artifact_name)
        else:
            gcode = job_dir / GCODE_NAME
            stl = job_dir / "model.stl"
            artifact = gcode if gcode.is_file() else stl
        if not artifact.is_file():
            raise Ai3dError(f"job {job_id!r} has no artifact to confirm")
        digest = sha256_file(artifact)
        return {
            "job_id": record.id,
            "artifact": artifact.name,
            "artifact_sha256": digest,
            "confirmation": confirmation_token(record.id, digest),
            "note": "a human must review the artifact before echoing this token back",
        }
