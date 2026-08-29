"""The job pipeline: explicit stages from request to a printable artifact.

    intake -> generate/import -> inspect -> repair -> re-inspect
           -> units/scale/orient/place -> printability verdict
           -> deterministic export -> [optional slice] -> [G-code safety scan]
           -> printer preparation (dry run) -> STOP

The pipeline stops there. Physical printing is a separate, explicitly
confirmed call into `printer.execute_physical`; no code path in this module can
reach a heater or a motor.

The verdict is never inferred from "an STL file exists". Every stage records
its own status, and `printable` is set only by `printability.decide_printability`.

Alongside the stage log the run keeps an `EvidenceLedger`: one label per engine
(`PASS` / `FAIL` / `NOT_RUN`) saying what actually executed on this host. A
printable verdict says nothing about whether a slicer ran or a printer printed,
and the ledger is where that distinction is written down instead of implied.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import printability as printability_mod
from .artifacts import list_artifacts, write_manifest, write_report
from .cad.compiler import compile_mesh, compile_scad
from .cad.external import cadquery_export, openscad_info
from .config import Settings
from .evidence import FAIL, NOT_RUN, PASS, EvidenceLedger
from .errors import (
    Ai3dError,
    CapabilityUnavailableError,
    DiskQuotaError,
    JobCancelledError,
    JobTimeoutError,
    MeshLoadError,
)
from .gcode import scan_gcode
from .mesh import Mesh, load_stl, mesh_digest, sha256_file, write_stl
from .meshcheck import cross_check_with_trimesh, inspect_mesh
from .paths import dir_size_bytes, safe_artifact_name
from .printer import confirmation_token, dry_run
from .profile import PrinterProfile
from .repair import repair_mesh
from .requirements import evaluate_requirements
from .slicer import slice_auto
from .spec import DesignSpec
from .tolerance import CalibrationProfile
from .storage import (
    CANCELLED,
    FAILED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    JobStore,
    StageRecord,
)

STL_NAME = "model.stl"
SCAD_NAME = "model.scad"
STEP_NAME = "model.step"
GCODE_NAME = "model.gcode"
VALIDATION_NAME = "validation.json"
REPORT_NAME = "print_report.md"
SPEC_NAME = "design_spec.json"
SOURCE_DIR = "source"


@dataclass(slots=True)
class JobRequest:
    kind: str  # "design" | "import"
    spec: DesignSpec | None = None
    source_stl: str | None = None
    source_units: str = "mm"
    scale: float = 1.0
    auto_orient: bool = True
    place_on_bed: bool = True
    drop_small_components: bool = False
    slice_after_build: bool = False
    slicer_settings: dict = field(default_factory=dict)
    calibrated_tolerance_mm: float | None = None
    calibration: "CalibrationProfile | None" = None
    scale_to_fit: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "spec": json.loads(self.spec.canonical_json()) if self.spec else None,
            "source_stl": self.source_stl,
            "source_units": self.source_units,
            "scale": self.scale,
            "auto_orient": self.auto_orient,
            "place_on_bed": self.place_on_bed,
            "drop_small_components": self.drop_small_components,
            "slice_after_build": self.slice_after_build,
            "slicer_settings": self.slicer_settings,
            "calibrated_tolerance_mm": self.calibrated_tolerance_mm,
            "calibration_profile": self.calibration.as_dict() if self.calibration else None,
            "scale_to_fit": self.scale_to_fit,
        }


class Pipeline:
    def __init__(self, settings: Settings, profile: PrinterProfile, store: JobStore) -> None:
        self.settings = settings
        self.profile = profile
        self.store = store

    # ------------------------------------------------------------- helpers
    def _checkpoint(self, job_id: str, deadline: float | None = None) -> None:
        """Cancellation, timeout and disk quota, checked between every stage.

        `asyncio.wait_for` alone is not enough: a stage that never awaits would
        run to completion inside a single event-loop step, so the deadline is
        also enforced explicitly here.
        """
        if self.store.is_cancelled(job_id):
            raise JobCancelledError(f"job {job_id!r} was cancelled")
        if deadline is not None and time.monotonic() > deadline:
            raise JobTimeoutError(
                f"job {job_id!r} exceeded {self.settings.job_timeout_s}s",
                detail={"timeout_s": self.settings.job_timeout_s},
            )
        self.store.check_job_quota(job_id)

    def _stage(self, job_id: str, name: str, status: str, started: float, detail: dict | None = None) -> None:
        self.store.add_stage(job_id, StageRecord(name, status, started, time.time(), detail or {}))

    # ---------------------------------------------------------------- main
    async def run(self, job_id: str, request: JobRequest) -> dict:
        record = self.store.get(job_id)
        job_dir = Path(record.directory)
        self.store.update(job_id, status=RUNNING)
        # Owned here, not inside _run_stages, so that a job which dies partway
        # through still reports what had and had not run when it died.
        evidence = EvidenceLedger()
        try:
            deadline = time.monotonic() + self.settings.job_timeout_s
            result = await asyncio.wait_for(
                self._run_stages(job_id, job_dir, request, deadline, evidence),
                timeout=self.settings.job_timeout_s,
            )
        except (asyncio.TimeoutError, JobTimeoutError):
            detail = {"timeout_s": self.settings.job_timeout_s}
            payload = {
                "error": "JOB_TIMEOUT",
                "message": f"job exceeded {self.settings.job_timeout_s}s",
                "detail": detail,
            }
            self.store.update(job_id, status=TIMED_OUT, error=payload)
            return {"job_id": job_id, "status": TIMED_OUT, "evidence": evidence.as_dict(), **payload}
        except JobCancelledError as exc:
            self.store.update(job_id, status=CANCELLED, error=exc.as_dict())
            return {"job_id": job_id, "status": CANCELLED, "evidence": evidence.as_dict(), **exc.as_dict()}
        except Ai3dError as exc:
            self.store.update(job_id, status=FAILED, error=exc.as_dict())
            self._finalise_artifacts(job_id, job_dir)
            return {"job_id": job_id, "status": FAILED, "evidence": evidence.as_dict(), **exc.as_dict()}
        except Exception as exc:  # unexpected: still recorded, never swallowed
            payload = {"error": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}", "detail": {}}
            self.store.update(job_id, status=FAILED, error=payload)
            self._finalise_artifacts(job_id, job_dir)
            return {"job_id": job_id, "status": FAILED, "evidence": evidence.as_dict(), **payload}

        status = SUCCEEDED if result.get("printable") else FAILED
        self.store.update(job_id, status=status, result=result)
        return result

    async def _run_stages(
        self,
        job_id: str,
        job_dir: Path,
        request: JobRequest,
        deadline: float | None = None,
        evidence: EvidenceLedger | None = None,
    ) -> dict:
        job_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        stages: dict[str, str] = {}
        evidence = evidence if evidence is not None else EvidenceLedger()
        scad_info = openscad_info(self.settings.openscad_bin)
        evidence.not_run(
            "openscad_render",
            scad_info["reason"] or "OpenSCAD is installed but this build never invokes it",
            engine="openscad",
        )

        # ---------------------------------------------------------- intake
        started = time.time()
        self._checkpoint(job_id, deadline)
        gate = None
        if request.kind == "design":
            if request.spec is None:
                raise CapabilityUnavailableError("design job without a DesignSpec")
            gate = evaluate_requirements(
                request.spec, request.calibrated_tolerance_mm,
                profile=self.profile, calibration=request.calibration,
            )
            (job_dir / SPEC_NAME).write_text(
                json.dumps(json.loads(request.spec.canonical_json()), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            warnings.extend(gate.warnings)
            if not gate.ready:
                stages["intake"] = "blocked"
                self._stage(job_id, "intake", "failed", started, gate.as_dict())
                evidence.record(
                    "spec_compiled", FAIL, engine="pydantic+requirement-gate",
                    detail={"status": gate.status, "questions": gate.questions},
                )
                blocked = f"blocked at the requirement gate: {gate.status}"
                for key in ("cad_engine", "step_export", "mesh_validation",
                            "mesh_cross_check", "printability", "slicer", "gcode_safety"):
                    evidence.not_run(key, blocked)
                return self._finish(
                    job_id, job_dir, request,
                    printable=False, status=gate.status,
                    reasons=gate.questions, warnings=warnings, stages=stages,
                    mesh_report=None, fit_report=None, verdict=None,
                    evidence=evidence,
                    # A job blocked at the gate still has to say what the gate
                    # was reading, including which calibration backed it.
                    extra={"requirement_gate": gate.as_dict()},
                )
            stages["intake"] = "ok"
            self._stage(job_id, "intake", "ok", started, gate.as_dict())
            evidence.record(
                "spec_compiled", PASS, engine="pydantic+requirement-gate",
                detail={"features": len(request.spec.features), "status": gate.status},
            )
        else:
            source = self._stage_source_file(job_dir, request)
            stages["intake"] = "ok"
            self._stage(job_id, "intake", "ok", started, {"source": str(source)})
            evidence.not_run("spec_compiled", "this is an STL import job, not a DesignSpec build")

        # -------------------------------------------------- generate/import
        started = time.time()
        self._checkpoint(job_id, deadline)
        if request.kind == "design":
            assert request.spec is not None
            (job_dir / SCAD_NAME).write_text(compile_scad(request.spec), encoding="utf-8")
            try:
                compiled = compile_mesh(request.spec)
            except Ai3dError as exc:
                # The kernel ran and rejected the spec. That is a FAIL with a
                # reason, not a stage that never happened.
                evidence.record(
                    "cad_engine", FAIL, engine="native",
                    reason=exc.message,
                    detail=exc.as_dict().get("detail", {}),
                )
                raise
            mesh = compiled.mesh
            generate_detail = compiled.as_dict()
            step_result = cadquery_export(request.spec, job_dir / STEP_NAME, job_dir / "model_cadquery.stl")
            generate_detail["step_export"] = {
                "status": step_result["status"], "error": step_result.get("error")
            }
            if step_result["status"] == "NOT_AVAILABLE":
                warnings.append(f"STEP export unavailable: {step_result.get('error')}")
                evidence.not_run("step_export", step_result.get("error") or "CadQuery not available",
                                 engine="cadquery")
            elif step_result["status"] == "PASS":
                evidence.record("step_export", PASS, engine="cadquery",
                                detail={"step": step_result.get("step")})
            else:
                evidence.record("step_export", FAIL, engine="cadquery",
                                reason=step_result.get("error") or "",
                                detail={"status": step_result["status"]})
            evidence.record(
                "cad_engine", PASS,
                engine=f"{compiled.engine}/csg:{compiled.backend}",
                detail={"triangles": len(mesh.faces), "features_applied": compiled.features_applied},
            )
        else:
            source = job_dir / SOURCE_DIR / safe_artifact_name(Path(request.source_stl or "input.stl").name)
            mesh = load_stl(source, max_triangles=self.settings.max_triangles)
            generate_detail = {"engine": "stl-import", "triangles": len(mesh.faces), "source": str(source)}
            evidence.not_run("cad_engine", "no CAD kernel is involved in an STL import job")
            evidence.not_run("step_export", "STEP export only applies to a DesignSpec build")
        stages["generate"] = "ok"
        self._stage(job_id, "generate", "ok", started, generate_detail)

        # ------------------------------------------------------ inspect raw
        started = time.time()
        self._checkpoint(job_id, deadline)
        raw_report = inspect_mesh(mesh)
        stages["inspect_raw"] = raw_report.status.lower()
        self._stage(job_id, "inspect_raw", "ok", started, raw_report.as_dict())

        # ----------------------------------------------------------- repair
        started = time.time()
        self._checkpoint(job_id, deadline)
        mesh, repair_report = repair_mesh(mesh, drop_extra_components=request.drop_small_components)
        stages["repair"] = "ok" if repair_report.changed else "skipped"
        self._stage(job_id, "repair", "ok", started, repair_report.as_dict())
        if repair_report.changed:
            warnings.append("mesh was modified by repair: " + "; ".join(repair_report.actions))

        # --------------------------------------------- units / scale / place
        started = time.time()
        self._checkpoint(job_id, deadline)
        mesh, units_report = printability_mod.normalize_units(mesh, request.source_units)
        warnings.extend(units_report.warnings)
        if request.scale != 1.0:
            if request.scale <= 0:
                raise CapabilityUnavailableError("scale must be positive")
            mesh = printability_mod.apply_scale(mesh, request.scale)
        transform_detail = {"units": units_report.as_dict(), "scale": request.scale}

        fit_report = printability_mod.evaluate_fit(mesh, self.profile, allow_rotate=request.auto_orient)
        if not fit_report.fits and request.scale_to_fit:
            mesh, factor = printability_mod.scale_to_fit(mesh, self.profile)
            transform_detail["scale_to_fit_factor"] = factor
            warnings.append(f"model was scaled by {factor:.4f} to fit the build volume")
            fit_report = printability_mod.evaluate_fit(mesh, self.profile, allow_rotate=request.auto_orient)
        if fit_report.fits and fit_report.axis_permutation and fit_report.rotated:
            mesh = printability_mod.orient_mesh(mesh, fit_report.axis_permutation)
        if request.place_on_bed:
            mesh = printability_mod.place_on_bed(mesh, self.profile)
        transform_detail["fit"] = fit_report.as_dict()
        stages["transform"] = "ok"
        self._stage(job_id, "transform", "ok", started, transform_detail)

        # ------------------------------------------------ inspect repaired
        started = time.time()
        self._checkpoint(job_id, deadline)
        final_report = inspect_mesh(mesh)
        stages["inspect_final"] = final_report.status.lower()
        self._stage(job_id, "inspect_final", "ok", started, final_report.as_dict())
        evidence.record(
            "mesh_validation",
            FAIL if final_report.status == "FAIL" else PASS,
            engine="meshcheck",
            detail={
                "triangles": final_report.triangles,
                "watertight": final_report.is_watertight,
                "edge_manifold": final_report.is_edge_manifold,
                "winding_consistent": final_report.is_winding_consistent,
                "components": final_report.components,
                "volume_mm3": final_report.signed_volume_mm3,
                "extents_mm": list(final_report.extents_mm),
                "errors": final_report.errors,
            },
        )

        # ----------------------------------------------------- printability
        started = time.time()
        extra = list(printability_mod.thin_feature_warnings(mesh, self.profile))
        verdict = printability_mod.decide_printability(
            final_report, fit_report, extra_warnings=extra,
        )
        warnings.extend(w for w in verdict.warnings if w not in warnings)
        stages["printability"] = "ok" if verdict.printable else "failed"
        self._stage(job_id, "printability", "ok" if verdict.printable else "failed", started, verdict.as_dict())
        evidence.record(
            "printability", PASS if verdict.printable else FAIL,
            engine="printability.decide_printability",
            detail={"status": verdict.status, "reasons": verdict.reasons, "checks": verdict.checks},
        )

        # ----------------------------------------------------------- export
        started = time.time()
        self._checkpoint(job_id, deadline)
        stl_path = job_dir / STL_NAME
        if verdict.printable:
            write_stl(mesh, stl_path)
            export_detail = {
                "stl": str(stl_path),
                "sha256": sha256_file(stl_path),
                "mesh_digest": mesh_digest(mesh),
                "deterministic": True,
            }
            stages["export"] = "ok"
        else:
            # A rejected model still gets its geometry written, but into a name
            # that cannot be mistaken for a print-ready artifact.
            rejected = job_dir / "model.rejected.stl"
            write_stl(mesh, rejected)
            export_detail = {
                "stl": None,
                "rejected_stl": str(rejected),
                "sha256": sha256_file(rejected),
                "reason": "geometry did not pass the printability gate; not exported as model.stl",
            }
            stages["export"] = "failed"
        self._stage(job_id, "export", stages["export"], started, export_detail)

        cross = cross_check_with_trimesh(stl_path if verdict.printable else job_dir / "model.rejected.stl")
        if cross["status"] == "OK":
            evidence.record(
                "mesh_cross_check", PASS,
                engine=f"trimesh {_trimesh_version()}", detail=cross,
            )
        elif cross["status"] == "NOT_AVAILABLE":
            evidence.not_run("mesh_cross_check", cross.get("reason", "trimesh not importable"),
                             engine="trimesh")
        else:
            evidence.record("mesh_cross_check", FAIL, engine="trimesh",
                            reason=cross.get("reason", ""), detail=cross)

        # ------------------------------------------------------------ slice
        slice_result = None
        scan_result = None
        dry = None
        if verdict.printable and request.slice_after_build:
            started = time.time()
            self._checkpoint(job_id, deadline)
            slice_result = await slice_auto(
                stl_path, job_dir / GCODE_NAME,
                curaengine_bin=self.settings.curaengine_bin,
                cura_definition=self.settings.cura_definition,
                prusaslicer_bin=self.settings.prusaslicer_bin,
                settings=request.slicer_settings,
                timeout_s=min(self.settings.job_timeout_s, 300.0),
            )
            stages["slice"] = {"PASS": "ok", "NOT_AVAILABLE": "not_available"}.get(slice_result.status, "failed")
            self._stage(job_id, "slice", stages["slice"], started, slice_result.as_dict())
            if slice_result.status == "NOT_AVAILABLE":
                warnings.append(f"slicing unavailable: {slice_result.error}")
                evidence.not_run("slicer", slice_result.error or "no slicer on this host",
                                 engine=slice_result.engine)
                evidence.not_run("gcode_safety", "no slicer produced G-code to scan")
            elif slice_result.ok:
                evidence.record("slicer", PASS, engine=slice_result.engine,
                                detail={"gcode": slice_result.gcode_path,
                                        "version": slice_result.engine_version,
                                        "command": slice_result.command})
            else:
                evidence.record("slicer", FAIL, engine=slice_result.engine,
                                reason=slice_result.error or "",
                                detail={"returncode": slice_result.returncode})
                evidence.not_run("gcode_safety", "the slicer failed, so there is no G-code to scan")

            if slice_result.ok:
                started = time.time()
                gcode_text = (job_dir / GCODE_NAME).read_text(encoding="utf-8", errors="replace")
                scan = scan_gcode(gcode_text, self.profile, strict_unknown=self.settings.strict_gcode)
                scan_result = scan.as_dict()
                stages["gcode_scan"] = {"PASS": "ok", "WARN": "ok"}.get(scan.status, "failed")
                self._stage(job_id, "gcode_scan", stages["gcode_scan"], started, scan_result)
                dry = dry_run(gcode_text, self.profile, scan).as_dict()
                self._stage(job_id, "print_dry_run", "ok", started, dry)
                evidence.record(
                    "gcode_safety", FAIL if scan.status == "FAILED" else PASS,
                    engine="gcode.scan_gcode",
                    detail={
                        "status": scan.status,
                        "units_mode": scan.units_mode,
                        "commands_scanned": scan.commands_scanned,
                        "max_nozzle_target_c": scan.max_nozzle_target_c,
                        "max_bed_target_c": scan.max_bed_target_c,
                        "errors": [i for i in scan.issues if i["severity"] == "ERROR"],
                    },
                )
        elif request.slice_after_build:
            stages["slice"] = "skipped"
            self._stage(job_id, "slice", "skipped", time.time(), {"reason": "model is not printable"})
            evidence.not_run("slicer", "the model did not pass the printability gate")
            evidence.not_run("gcode_safety", "nothing was sliced, so there is no G-code to scan")

        # ------------------------------------------------ printer preparation
        started = time.time()
        artifact_for_print = None
        artifact_sha = ""
        if verdict.printable:
            if slice_result is not None and slice_result.ok and scan_result and scan_result["status"] != "FAILED":
                artifact_for_print = job_dir / GCODE_NAME
            else:
                artifact_for_print = stl_path
            artifact_sha = sha256_file(artifact_for_print)
        prepare_detail = {
            "artifact": str(artifact_for_print) if artifact_for_print else None,
            "artifact_sha256": artifact_sha,
            "confirmation_token": confirmation_token(job_id, artifact_sha) if artifact_sha else None,
            "transport": self.settings.printer_transport,
            "physical_action_taken": False,
            "note": (
                "Preparation only. Starting a print, preheating or moving the machine requires a "
                "separate confirmed call and is BLOCKED BY HARDWARE on this host."
            ),
        }
        stages["printer_prepare"] = "ok" if artifact_for_print else "skipped"
        self._stage(job_id, "printer_prepare", stages["printer_prepare"], started, prepare_detail)

        return self._finish(
            job_id, job_dir, request,
            printable=verdict.printable,
            status=verdict.status,
            reasons=verdict.reasons,
            warnings=warnings,
            stages=stages,
            mesh_report=final_report,
            fit_report=fit_report,
            verdict=verdict,
            evidence=evidence,
            extra={
                "raw_mesh_report": raw_report.as_dict(),
                "repair": repair_report.as_dict(),
                "generate": generate_detail,
                "transform": transform_detail,
                "export": export_detail,
                "trimesh_cross_check": cross,
                "slice": slice_result.as_dict() if slice_result else None,
                "gcode_scan": scan_result,
                "print_dry_run": dry,
                "printer_prepare": prepare_detail,
                "requirement_gate": gate.as_dict() if gate else None,
            },
        )

    # ----------------------------------------------------------- utilities
    def _stage_source_file(self, job_dir: Path, request: JobRequest) -> Path:
        if not request.source_stl:
            raise MeshLoadError("import job without a source file")
        source = Path(request.source_stl)
        if not source.is_file():
            raise MeshLoadError(f"source file {source} does not exist")
        size = source.stat().st_size
        if size > self.settings.max_upload_bytes:
            raise DiskQuotaError(
                f"source file is {size} bytes, above the {self.settings.max_upload_bytes} byte limit",
                detail={"bytes": size, "limit": self.settings.max_upload_bytes},
            )
        target_dir = job_dir / SOURCE_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_artifact_name(source.name)
        target.write_bytes(source.read_bytes())
        return target

    def _finalise_artifacts(self, job_id: str, job_dir: Path) -> None:
        try:
            write_manifest(job_dir, extra={"job_id": job_id})
        except Exception:
            pass

    def _finish(
        self,
        job_id: str,
        job_dir: Path,
        request: JobRequest,
        *,
        printable: bool,
        status: str,
        reasons: list[str],
        warnings: list[str],
        stages: dict,
        mesh_report,
        fit_report,
        verdict,
        evidence: EvidenceLedger,
        extra: dict | None = None,
    ) -> dict:
        result = {
            "job_id": job_id,
            "app": "ai-3d-maker",
            "printable": printable,
            "status": status,
            "printer": {
                "model": self.profile.model,
                "profile_id": self.profile.id,
                "verified_build_volume_mm": [self.profile.build_x, self.profile.build_y, self.profile.build_z],
            },
            "stages": stages,
            "reasons": reasons,
            "warnings": warnings,
            "mesh": mesh_report.as_dict() if mesh_report else None,
            "fit": fit_report.as_dict() if fit_report else None,
            "printability": verdict.as_dict() if verdict else None,
            "evidence": evidence.as_dict(),
            "physical_print": {
                "performed": False,
                "requires_explicit_human_confirmation": True,
                "status": "BLOCKED_BY_HARDWARE",
            },
        }
        if extra:
            result["detail"] = extra

        (job_dir / VALIDATION_NAME).write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

        prepare = (extra or {}).get("printer_prepare") or {}
        write_report(job_dir / REPORT_NAME, {
            "job_id": job_id,
            "design_name": request.spec.name if request.spec else Path(request.source_stl or "imported").name,
            "status": status,
            "printability": "PRINTABLE" if printable else "NOT_PRINTABLE",
            "printer": self.profile.model,
            "profile_id": self.profile.id,
            "build_volume_mm": [self.profile.build_x, self.profile.build_y, self.profile.build_z],
            "triangles": mesh_report.triangles if mesh_report else None,
            "extents_mm": [round(v, 4) for v in mesh_report.extents_mm] if mesh_report else None,
            "watertight": mesh_report.is_watertight if mesh_report else None,
            "manifold": mesh_report.is_edge_manifold if mesh_report else None,
            "components": mesh_report.components if mesh_report else None,
            "volume_mm3": round(mesh_report.signed_volume_mm3, 4) if mesh_report else None,
            "stages": stages,
            "reasons": reasons,
            "warnings": warnings,
            "artifacts": [a.as_dict() for a in list_artifacts(job_dir)],
            "confirmation_token": prepare.get("confirmation_token"),
            "transport": self.settings.printer_transport,
            "evidence": evidence.summary_lines(),
        })
        write_manifest(job_dir, extra={
            "job_id": job_id,
            "printable": printable,
            "status": status,
            "bytes_used": dir_size_bytes(job_dir),
        })
        return result


def _trimesh_version() -> str:
    try:
        import trimesh  # noqa: PLC0415

        return str(getattr(trimesh, "__version__", "unknown"))
    except Exception:  # pragma: no cover - only when trimesh is absent
        return "unknown"


def build_mesh_from_spec(spec: DesignSpec) -> Mesh:
    """Convenience for tests and CLI: spec -> mesh, no filesystem involved."""
    return compile_mesh(spec).mesh
