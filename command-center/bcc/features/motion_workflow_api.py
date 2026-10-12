"""Motion Studio workflow API: materials -> analysis -> plan -> edit -> run -> progress -> result -> reopen.

Extends Motion Studio (it is not a separate product).  A thin HTTP layer over the existing core
``bcc.motion_workflow`` (plan / analyze / store) and the local ffmpeg renderer ``bcc.motion_workflow.render``.
No network call, no paid API: cloud spend is 0 by construction.

  GET  /motion-workflow/status                          renderer readiness, analyzers, limits, what runs / does not
  GET  /motion-workflow/limits                          declared limits + machine-time model (shown BEFORE any run)
  GET  /motion-workflow/projects                        list;   POST /projects   create (JSON, or a video upload)
  GET  /motion-workflow/projects/{id}                   open: plan, readiness, estimate, limits, history, jobs
  POST /motion-workflow/projects/{id}/source            upload / replace the source video (multipart or raw body)
  POST /motion-workflow/projects/{id}/analyze           source video -> plan (UNKNOWN stays UNKNOWN)
  GET  /motion-workflow/projects/{id}/{plan|readiness|estimate|limits|history|analysis}
  GET  /motion-workflow/projects/{id}/versions/{n}      one stored version of the plan
  POST /motion-workflow/projects/{id}/ops               edit (version history, optimistic expected_version)
  POST /motion-workflow/projects/{id}/revert            new version equal to an older one
  GET|POST /motion-workflow/projects/{id}/references    list / upload (@name, kind)
  POST /motion-workflow/projects/{id}/jobs              {mode: preview|full} -> 202
  GET  /motion-workflow/projects/{id}/jobs[/{job}]      state, progress 0..1, stage, log tail, result
  POST /motion-workflow/projects/{id}/jobs/{job}/{render|resume|cancel}
  GET  /motion-workflow/projects/{id}/jobs/{job}/{check|file}

A render runs in a worker thread that drives an ffmpeg SUBPROCESS (never in the event loop), one job at a
time, with a hard time limit and cancel.  A job is ``done`` only after the output passed ``verify_output``;
elements the renderer cannot execute (face / clothes / background / product replace) are reported
``not_executed`` per scene with a reason and the job result says so.  A job found running at startup is
``interrupted`` (or finished and re-verified from disk), never left ``running``.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib.util
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.requests import Request as StarletteRequest

from ..motion_workflow import analyze as analyze_mod
from ..motion_workflow import plan as planmod
from ..motion_workflow import render as render_mod
from ..motion_workflow import store as store_mod
from . import Feature

router = APIRouter(prefix="/motion-workflow", tags=["motion-workflow"])

FILE_NAMES = ("output.mp4", "output-telegram.mp4", "cover.png", "render_report.json", "render.log", "job.log")
PREVIEW_FILE_RE = re.compile(r"^preview/[A-Za-z0-9][A-Za-z0-9_-]{0,31}_[123]\.png$")
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{12}$")
EXT_OPS = ("set_cover", "set_soundtrack")
UPLOAD_SLACK = 1024 * 1024
ORPHAN_AGE_SECONDS = 3600.0
CHUNK = 1024 * 1024
PROGRESS_PERSIST_SECONDS = 1.0
CANCEL_WAIT_SECONDS = 8.0
RUNNING_STATES = store_mod.RUNNING_STATES

KIND_LABEL = {"character": "персонаж", "product": "товар", "clothing": "одежда", "background": "фон",
              "audio": "аудио", "text": "текст"}


# ============================================================================================ errors

def _err(status: int, code: str, message: str, hint: str | None = None, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"message": message, "code": code}
    if hint:
        detail["hint"] = hint
    detail.update(extra)
    return HTTPException(status, detail)


@contextlib.contextmanager
def _translate():
    """Core / store / renderer exceptions -> honest HTTP errors (never a bare 500 with a type name)."""
    try:
        yield
    except HTTPException:
        raise
    except store_mod.PathEscapeError as exc:
        raise _err(400, "PATH_ESCAPE", str(exc)) from None
    except store_mod.ProjectNotFound as exc:
        raise _err(404, "NOT_FOUND", str(exc)) from None
    except store_mod.ConflictError as exc:
        raise _err(409, "CONFLICT", str(exc), hint="откройте проект заново: он изменился") from None
    except store_mod.StoreBusyError as exc:
        raise _err(503, "BUSY", str(exc), hint="повторите через несколько секунд") from None
    except store_mod.JobStateError as exc:
        raise _err(409, "JOB_STATE", str(exc)) from None
    except store_mod.IntegrityError as exc:
        raise _err(409, "INTEGRITY", str(exc), hint="файл проекта повреждён или изменён вне Bossman") from None
    except planmod.MissingReferenceError as exc:
        raise _err(422, "MISSING_REFERENCE", str(exc), problems=exc.problems) from None
    except planmod.LimitError as exc:
        raise _err(422, "LIMIT_EXCEEDED", str(exc), problems=exc.problems) from None
    except planmod.PlanError as exc:
        raise _err(422, "PLAN_INVALID", str(exc), problems=exc.problems) from None
    except analyze_mod.AnalyzerUnavailable as exc:
        raise _err(503, "ANALYZER_UNAVAILABLE", str(exc), hint="нужен ffmpeg и ffprobe") from None
    except analyze_mod.AnalysisError as exc:
        raise _err(422, "ANALYSIS_FAILED", str(exc)) from None
    except render_mod.RenderUnavailable as exc:
        raise _err(503, "RENDERER_UNAVAILABLE", str(exc), hint="нужен ffmpeg с libx264, aac и drawtext") from None
    except store_mod.StoreError as exc:
        raise _err(409, "STORE_ERROR", str(exc)) from None


# ============================================================================================ helpers

def _safe_name(raw: str | None, default: str) -> str:
    """A single file name: no directories, no control or reserved characters, bounded length."""
    base = re.split(r"[\\/]", str(raw or ""))[-1].strip().strip(".")
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", base)[:100]
    return base or default


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def _now() -> str:
    return planmod.now_iso()


def _write_json(path: Path, obj: Any) -> None:
    store_mod.atomic_write_json(path, obj)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _env_summary(env: dict[str, Any]) -> dict[str, Any]:
    return {"ready": env["ready"], "text_ready": env["text_ready"], "problems": env["problems"],
            "warnings": env["warnings"], "ffmpeg": bool(env.get("ffmpeg")), "ffprobe": bool(env.get("ffprobe")),
            "ffmpeg_version": env.get("version"), "font": Path(env["font"]).name if env.get("font") else None,
            "drawtext": bool(env.get("has_drawtext")), "loudnorm": bool(env.get("has_loudnorm"))}


# ============================================================================================ runtime

class JobRun:
    """In-memory state of one job started by this process."""

    def __init__(self, project_id: str, job_id: str, mode: str, telegram: bool, timeout: float | None) -> None:
        self.project_id, self.job_id, self.mode = project_id, job_id, mode
        self.telegram, self.timeout = telegram, timeout
        self.cancel = threading.Event()
        self.cancel_reason: str | None = None
        self.stage = "queued"
        self.progress = 0.0
        self.detail: dict[str, Any] = {}
        self.log: deque[str] = deque(maxlen=80)
        self.thread: threading.Thread | None = None
        self.started = time.time()
        self.finished = False
        self._persisted = 0.0

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class Runtime:
    """Store + job table of one backend instance (one per app; the store is the source of truth)."""

    def __init__(self, data_dir: str | os.PathLike) -> None:
        self.data_dir = Path(data_dir)
        root = self.data_dir / store_mod.SUBDIR
        root.mkdir(parents=True, exist_ok=True)
        self.store = store_mod.Store(self.data_dir, allowed_import_roots=[root])
        self.root = self.store.root
        self.incoming = self.root / ".incoming"
        self.incoming.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.runs: dict[tuple[str, str], JobRun] = {}
        self.analysis: dict[str, dict[str, Any]] = {}
        self.closed = False
        self.recovery: dict[str, Any] = {"store": self.store.recovery_report, "orphans_killed": [],
                                         "finished_from_disk": [], "incoming_removed": 0}
        self._startup_recovery()

    # ------------------------------------------------------------------ paths

    def pdir(self, project_id: str) -> Path:
        """The project folder; the id is validated as ONE path component and the project must exist."""
        try:
            planmod.safe_id(project_id, "project_id")
        except planmod.PlanError as exc:
            raise store_mod.PathEscapeError(str(exc)) from None
        path = self.root / project_id
        if path.resolve().parent != self.root:
            raise store_mod.PathEscapeError(f"project path escapes the store: {project_id}")
        if not ((path / "project.json").is_file() or (path / "versions").is_dir()):
            raise store_mod.ProjectNotFound(f"no such project: {project_id}")
        return path

    def out_dir(self, project_id: str, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
            raise store_mod.PathEscapeError(f"job id must match {_JOB_ID_RE.pattern}: {str(job_id)[:60]!r}")
        path = self.pdir(project_id) / "outputs" / job_id
        if path.resolve().parent.parent.parent != self.root:
            raise store_mod.PathEscapeError("output path escapes the project")
        return path

    # ------------------------------------------------------------------ recovery

    def _startup_recovery(self) -> None:
        """After Store.recover(): kill ffmpeg children that outlived a dead backend, finish from disk the
        render that was complete and verified, drop stale half-uploaded files."""
        now = time.time()
        for child in list(self.incoming.iterdir()):
            with contextlib.suppress(OSError):
                if now - child.stat().st_mtime > ORPHAN_AGE_SECONDS:
                    shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
                    self.recovery["incoming_removed"] += 1
        for item in self.store.list_projects():
            pid = item["project_id"]
            for job in self.store.list_jobs(pid):
                if job["state"] != "INTERRUPTED":
                    continue
                folder = self.root / pid / "outputs" / job["job_id"]
                if self._kill_orphan(folder):
                    self.recovery["orphans_killed"].append({"project_id": pid, "job_id": job["job_id"]})
                with contextlib.suppress(Exception):
                    if self._finish_from_disk(pid, job, folder):
                        self.recovery["finished_from_disk"].append({"project_id": pid, "job_id": job["job_id"]})

    @staticmethod
    def _kill_orphan(folder: Path) -> bool:
        pid_file = folder / "ffmpeg.pid"
        data = _read_json(pid_file)
        killed = False
        if isinstance(data, dict) and isinstance(data.get("pid"), int):
            try:
                import psutil
                proc = psutil.Process(int(data["pid"]))
                argv = [str(a) for a in proc.cmdline()]
                mine = any(str(folder.resolve()).lower() in a.lower() or str(folder).lower() in a.lower()
                           for a in argv) and Path(argv[0]).stem.lower().startswith("ffmpeg")
                if mine:
                    proc.kill()
                    proc.wait(timeout=10)
                    killed = True
            except Exception:  # noqa: BLE001 - gone, access denied or psutil missing: nothing to kill
                killed = False
        with contextlib.suppress(OSError):
            pid_file.unlink()
        with contextlib.suppress(OSError):
            (folder / "output.part.mp4").unlink()
        return killed

    def _finish_from_disk(self, pid: str, job: dict[str, Any], folder: Path) -> bool:
        """A render that was verified and reported but whose process died before ``job_finish_render``."""
        interrupted = job.get("interrupted") or {}
        if interrupted.get("phase") != "render":
            return False
        report = _read_json(folder / "render_report.json")
        output = folder / "output.mp4"
        if not (isinstance(report, dict) and output.is_file()):
            return False
        if report.get("status") != "done" or report.get("verified") is not True or report.get("job", {}).get("mode") != "full":
            return False
        if report["job"].get("plan_content_sha256") != job["plan_content_sha256"]:
            return False
        recorded = (report.get("output") or {}).get("sha256")
        if not recorded or analyze_mod.sha256_file(output) != recorded:
            return False
        env = render_mod.environment()
        if not env["ready"]:
            return False
        params = report.get("parameters") or {}
        res, fps = params.get("resolution") or {}, params.get("fps") or {}
        audio_action = (params.get("audio") or {}).get("action", "keep")
        inp = report.get("input") or {}
        expect = {"width": res.get("width"), "height": res.get("height"), "fps": fps.get("value"),
                  "duration": inp.get("duration"), "frames": None if fps.get("changed") else inp.get("frames"),
                  "audio": "none" if audio_action in ("mute", "none") else "aac"}
        if None in (expect["width"], expect["height"], expect["fps"], expect["duration"]):
            return False
        check = render_mod.verify_output(output, env, expect)
        if not check["ok"]:
            return False
        s = self.store
        s.job_resume(pid, job["job_id"])
        fresh = s.get_job(pid, job["job_id"])
        for sid in fresh["scene_ids"]:
            if sid not in fresh["phases"]["render"]["completed"]:
                s.job_complete_scene(pid, job["job_id"], sid, output={"recovered_from_disk": True})
        s.job_finish_render(pid, job["job_id"], output={
            "file": "output.mp4", "sha256": recorded, "bytes": output.stat().st_size, "report": "render_report.json",
            "verified": True, "recovered_from_disk": True, "execution": report.get("execution")})
        return True

    # ------------------------------------------------------------------ shutdown

    def shutdown(self, wait: float = 4.0) -> None:
        self.closed = True
        with self.lock:
            runs = [r for r in self.runs.values() if r.alive()]
        for run in runs:
            run.cancel_reason = run.cancel_reason or "backend stopped"
            run.cancel.set()
        deadline = time.monotonic() + wait
        for run in runs:
            if run.thread is not None:
                run.thread.join(max(0.0, deadline - time.monotonic()))

    # ------------------------------------------------------------------ plan helpers

    def texts_for(self, project_id: str, plan: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, ref in (plan.get("references") or {}).items():
            if isinstance(ref, dict) and ref.get("kind") == "text":
                with contextlib.suppress(Exception):
                    path = self.store.asset_path(project_id, ref["sha256"], ref.get("ext"))
                    out[name] = path.read_text(encoding="utf-8")
        return out

    def ref_paths(self, project_id: str, plan: dict[str, Any]) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for name, ref in (plan.get("references") or {}).items():
            if isinstance(ref, dict) and ref.get("kind") == "audio":
                with contextlib.suppress(Exception):
                    out[name] = self.store.asset_path(project_id, ref["sha256"], ref.get("ext"))
        return out

    def source_path(self, project_id: str, plan: dict[str, Any]) -> Path | None:
        source = plan.get("source") or {}
        if not source.get("asset"):
            return None
        with contextlib.suppress(Exception):
            return self.store.asset_path(project_id, source["asset"], source.get("ext"))
        return None

    def render_blockers(self, project_id: str, plan: dict[str, Any], env: dict[str, Any],
                        resolved: dict[str, Any] | None = None) -> list[str]:
        out: list[str] = []
        if not env["ready"]:
            out.append("renderer unavailable: " + "; ".join(env["problems"]))
        source = plan.get("source")
        if not source or not source.get("asset"):
            out.append("no analyzed source video: upload a video and run the analysis")
        elif self.source_path(project_id, plan) is None:
            out.append("the source video file is missing from the project assets")
        resolved = resolved or render_mod.resolve(plan, self.texts_for(project_id, plan))
        if (resolved["overlays"] or resolved["cover"]["title"]) and not env["text_ready"]:
            out.append("text burn-in is unavailable: " + "; ".join(env["warnings"] or ["no font"]))
        if resolved["audio"]["action"] == "replace":
            name = resolved["audio"]["reference"]
            if name not in self.ref_paths(project_id, plan):
                out.append(f"audio reference @{name} is not available on disk")
        return out

    # ------------------------------------------------------------------ staging

    def stage_dir(self) -> Path:
        path = self.incoming / uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        return path

    def project_incoming(self, project_id: str) -> Path:
        return self.pdir(project_id) / "incoming"

    def staged_source(self, project_id: str) -> dict[str, Any] | None:
        meta = _read_json(self.project_incoming(project_id) / "source.json")
        if isinstance(meta, dict) and (self.project_incoming(project_id) / str(meta.get("file", ""))).is_file():
            return meta
        return None

    def stage_source(self, project_id: str, staged: Path, original_name: str) -> dict[str, Any]:
        folder = self.project_incoming(project_id)
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        name = _safe_name(original_name, "source" + _ext(staged.name))
        target = folder / name
        os.replace(staged, target)
        meta = {"file": name, "bytes": target.stat().st_size, "ts": _now()}
        _write_json(folder / "source.json", meta)
        return meta

    # ------------------------------------------------------------------ analysis

    def analyze(self, project_id: str, *, threshold: float | None, min_scene: float | None) -> dict[str, Any]:
        with self.lock:
            current = self.analysis.get(project_id)
            if current and current.get("state") == "running":
                raise _err(409, "ANALYSIS_RUNNING", "анализ этого проекта уже идёт")
            self.analysis[project_id] = {"state": "running", "stage": "start", "fraction": 0.0,
                                         "started": time.time(), "error": None}
        state = self.analysis[project_id]
        try:
            plan = self.store.get_project(project_id)
            staged = self.staged_source(project_id)
            if staged is not None:
                path = self.project_incoming(project_id) / staged["file"]
            else:
                path = self.source_path(project_id, plan)
            if path is None:
                raise _err(409, "NO_SOURCE", "в проекте нет исходного видео",
                           hint="загрузите видео в проект, затем запустите анализ")
            info = analyze_mod.probe(path)           # refuse an over-limit clip BEFORE it is copied into assets
            if info["duration"] > planmod.LIMITS["max_duration_seconds"] + 1e-6:
                raise planmod.LimitError(f"source duration {info['duration']}s exceeds the declared limit of "
                                         f"{planmod.LIMITS['max_duration_seconds']}s")
            kwargs: dict[str, Any] = {}
            if threshold is not None:
                kwargs["threshold"] = threshold
            if min_scene is not None:
                kwargs["min_scene_seconds"] = min_scene

            def progress(stage: str, fraction: float) -> None:
                state["stage"], state["fraction"] = stage, round(float(fraction), 3)

            result = self.store.analyze_source(project_id, path, author="owner", progress=progress, **kwargs)
            shutil.rmtree(self.project_incoming(project_id), ignore_errors=True)
            state.update(state="done", stage="done", fraction=1.0, finished=time.time())
            return result
        except BaseException as exc:
            state.update(state="failed", error=str(exc)[:400], finished=time.time())
            raise

    # ------------------------------------------------------------------ jobs

    def active_run(self) -> JobRun | None:
        with self.lock:
            for run in self.runs.values():
                if run.alive():
                    return run
        return None

    def out_files(self, project_id: str, job_id: str) -> list[str]:
        folder = self.out_dir(project_id, job_id)
        found = [n for n in FILE_NAMES if (folder / n).is_file()]
        preview = folder / "preview"
        if preview.is_dir():
            found.extend(sorted(f"preview/{p.name}" for p in preview.glob("*.png") if p.name.endswith(("_1.png", "_2.png", "_3.png"))
                                and not p.name.endswith(".part.png")))
        return found

    @staticmethod
    def api_state(job: dict[str, Any]) -> str:
        state = job["state"]
        if state in RUNNING_STATES:
            return "running"
        if state == "PLANNED":
            return "planned"
        if state == "PREVIEW_DONE":
            return "preview_done"
        if state == "RENDER_DONE":
            return "done"
        reason = str(((job.get("interrupted") or {}).get("reason")) or job.get("error") or "")
        if reason.startswith("cancelled"):
            return "cancelled"
        return "failed" if state == "FAILED" else "interrupted"

    def job_meta(self, project_id: str, job_id: str) -> dict[str, Any]:
        meta = _read_json(self.out_dir(project_id, job_id) / "meta.json")
        return meta if isinstance(meta, dict) else {}

    def job_view(self, project_id: str, job_id: str, *, light: bool = False) -> dict[str, Any]:
        s = self.store
        job = s.get_job(project_id, job_id)
        key = (project_id, job_id)
        run = self.runs.get(key)
        if job["state"] in RUNNING_STATES and (run is None or not run.alive()):
            # a RUNNING job nobody in this process is driving must not stay "running" forever
            with self.lock:
                run = self.runs.get(key)
                if run is None or not run.alive():
                    with contextlib.suppress(store_mod.StoreError):
                        s.job_interrupt(project_id, job_id, "worker lost: no live render for this job")
                    job = s.get_job(project_id, job_id)
        folder = self.out_dir(project_id, job_id)
        meta = self.job_meta(project_id, job_id)
        progress = _read_json(folder / "progress.json") or {}
        state = self.api_state(job)
        live = run if (run is not None and run.alive()) else None
        view: dict[str, Any] = {
            "job_id": job_id, "project_id": project_id, "state": state, "store_state": job["state"],
            "phase": job.get("phase"), "mode": meta.get("mode") or ("full" if job["state"] in ("RENDER_RUNNING", "RENDER_DONE") else "preview"),
            "telegram_variant": bool(meta.get("telegram_variant")),
            "plan_version": job.get("plan_version"), "created_ts": job.get("created_ts"),
            "updated_ts": job.get("updated_ts"), "attempt": job.get("attempt"),
            "scene_ids": job.get("scene_ids"), "next_scene": job.get("next_scene"),
            "last_completed_scene": job.get("last_completed_scene"),
            "completed": {p: list(job["phases"][p]["completed"]) for p in ("preview", "render")},
            "stage": live.stage if live else progress.get("stage"),
            "progress": round(live.progress, 4) if live else (1.0 if state == "done" else progress.get("progress", 0.0)),
            "progress_detail": live.detail if live else progress.get("detail"),
            "progress_is_live": live is not None,
            "error": job.get("error"), "interrupted": job.get("interrupted"),
            "estimate": job.get("estimate"), "cloud_spend_usd": job.get("cloud_spend_usd", 0.0),
            "paid_calls": job.get("paid_calls", 0), "output": job.get("output"),
            "history": job.get("history", [])[-12:],
        }
        if live:
            view["log_tail"] = "\n".join(live.log)[-2000:] + "\n" + render_mod._tail_file(folder / "render.log", 700)
        else:
            view["log_tail"] = (render_mod._tail_file(folder / "job.log", 1500)
                                + ("\n" + render_mod._tail_file(folder / "render.log", 700)
                                   if state in ("failed", "interrupted", "cancelled") else ""))
        view["log_tail"] = view["log_tail"].strip()
        if not light:
            view["outputs"] = self.out_files(project_id, job_id)
            report = _read_json(folder / "render_report.json")
            if isinstance(report, dict):
                view["result"] = self.result_summary(report)
            if state == "done" and not (folder / "output.mp4").is_file():
                view["output_missing"] = True
        return view

    @staticmethod
    def result_summary(report: dict[str, Any]) -> dict[str, Any]:
        not_exec: list[dict[str, Any]] = []
        for scene in report.get("scenes") or []:
            for item in scene.get("items") or []:
                if item.get("status") == "not_executed":
                    not_exec.append({"scene_id": scene.get("scene_id"), "element": item.get("element"),
                                     "reason_code": item.get("reason_code"), "reason": item.get("reason")})
        for op in report.get("operations") or []:
            if op.get("status") == "not_executed" and op.get("kind") != "element":
                not_exec.append({"scene_id": None, "element": op.get("lane") or op.get("kind"),
                                 "reason_code": op.get("reason_code"), "reason": op.get("reason")})
        out = report.get("output") or {}
        params = report.get("parameters") or {}
        return {"status": report.get("status"), "verified": report.get("verified"), "mode": (report.get("job") or {}).get("mode"),
                "error": report.get("error"), "execution": report.get("execution"), "not_executed": not_exec,
                "applied": [{"scene_id": s.get("scene_id"), "element": i.get("element"), "detail": i.get("detail"),
                             "note": i.get("note")}
                            for s in report.get("scenes") or [] for i in s.get("items") or []
                            if i.get("status") in ("applied", "previewed")],
                "output": {"file": out.get("file"), "bytes": out.get("bytes"), "sha256": out.get("sha256"),
                           "video": out.get("video"), "audio": out.get("audio"), "format": out.get("format")} if out else None,
                "parameters": {"resolution": params.get("resolution"), "fps": params.get("fps"),
                               "video": params.get("video"), "audio": params.get("audio"),
                               "cover": params.get("cover"), "text_overlays": len(params.get("text_overlays") or []),
                               "subtitle_cues": params.get("subtitle_cues")},
                "ffmpeg": (report.get("environment") or {}).get("ffmpeg"),
                "verification": {"ok": (report.get("verification") or {}).get("ok"),
                                 "checks": (report.get("verification") or {}).get("checks")},
                "text_evidence": report.get("text_evidence"), "timing": report.get("timing"),
                "warnings": report.get("warnings"), "cloud_spend_usd": report.get("cloud_spend_usd", 0.0),
                "paid_calls": report.get("paid_calls", 0)}

    def start(self, project_id: str, job_id: str, mode: str, telegram: bool, timeout: float | None) -> JobRun:
        run = JobRun(project_id, job_id, mode, telegram, timeout)
        folder = self.out_dir(project_id, job_id)
        folder.mkdir(parents=True, exist_ok=True)
        meta = self.job_meta(project_id, job_id)
        meta.update({"mode": mode, "telegram_variant": telegram, "timeout_seconds": timeout,
                     "created_ts": meta.get("created_ts") or _now(), "last_started_ts": _now()})
        _write_json(folder / "meta.json", meta)
        with self.lock:
            self.runs[(project_id, job_id)] = run
        thread = threading.Thread(target=self._execute, args=(run,), name=f"mw-job-{job_id}", daemon=True)
        run.thread = thread
        thread.start()
        return run

    # ---- the worker ----

    def _log(self, run: JobRun, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        run.log.append(line)
        with contextlib.suppress(OSError):
            with open(self.out_dir(run.project_id, run.job_id) / "job.log", "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _progress(self, run: JobRun, stage: str, fraction: float, detail: dict[str, Any] | None = None) -> None:
        fraction = min(1.0, max(0.0, float(fraction)))
        if stage == "prepare":
            overall = 0.0
        elif stage == "preview":
            overall = fraction * (0.08 if run.mode == "full" else 0.9)
        elif stage == "render":
            overall = 0.08 + 0.84 * fraction
        else:           # verify / cover / report: the tail of the job
            base = 0.92 if run.mode == "full" else 0.9
            overall = base + (1.0 - base) * fraction
        run.stage, run.progress, run.detail = stage, round(overall, 4), detail or {}
        now = time.monotonic()
        if now - run._persisted >= PROGRESS_PERSIST_SECONDS or fraction >= 1.0:
            run._persisted = now
            with contextlib.suppress(Exception):
                _write_json(self.out_dir(run.project_id, run.job_id) / "progress.json",
                            {"job_id": run.job_id, "stage": stage, "progress": run.progress, "detail": run.detail,
                             "ts": _now()})

    def _execute(self, run: JobRun) -> None:
        s = self.store
        pid, jid = run.project_id, run.job_id
        folder = self.out_dir(pid, jid)
        wall = time.monotonic()
        timings: dict[str, Any] = {}
        prepared: dict[str, Any] | None = None
        render_result: dict[str, Any] | None = None
        preview_result: dict[str, Any] | None = None
        verification: dict[str, Any] | None = None
        try:
            plan = s.job_plan(pid, jid)
            job = s.get_job(pid, jid)
            src = self.source_path(pid, plan)
            if src is None:
                raise render_mod.RenderError("the source video of this plan version is missing from the assets")
            self._log(run, f"job {jid}: plan v{plan['version']}, mode {run.mode}, attempt {job.get('attempt', 1)}")
            self._progress(run, "prepare", 0.0)
            prepared = render_mod.prepare(plan, source_path=src, ref_paths=self.ref_paths(pid, plan),
                                          texts=self.texts_for(pid, plan))
            counts = prepared["resolved"]["counts"]
            self._log(run, "plan resolved: " + render_mod.summary_line(counts))
            state = job["state"]
            if state == "INTERRUPTED":
                job = s.job_resume(pid, jid)
                state = job["state"]
                self._log(run, f"resumed {job.get('phase')} (attempt {job.get('attempt')})")
            if state == "PLANNED":
                job = s.job_start_preview(pid, jid)
                state = job["state"]
            if state == "PREVIEW_RUNNING":
                done = set(job["phases"]["preview"]["completed"])
                t = time.monotonic()
                self._log(run, "preview: three sample frames per scene + cover")

                def on_preview_scene(sid: str, names: list[str]) -> None:
                    s.job_complete_scene(pid, jid, sid, output={"frames": names})

                preview_result = render_mod.run_preview(
                    prepared, folder, cancel=run.cancel, on_scene=on_preview_scene, skip_scenes=done,
                    on_progress=lambda stage, f, d: self._progress(run, stage, f, d))
                timings["preview_seconds"] = round(time.monotonic() - t, 3)
                s.job_finish_preview(pid, jid)
                state = "PREVIEW_DONE"
                _write_json(folder / "preview.json", {"frames": preview_result["frames"],
                                                      "cover": preview_result["cover"],
                                                      "commands": preview_result["commands"],
                                                      "warnings": preview_result["warnings"]})
            if run.mode == "full" and state == "PREVIEW_DONE":
                job = s.job_start_render(pid, jid)
                state = job["state"]
            if state == "RENDER_RUNNING":
                self._render_phase(run, prepared, job, folder, timings)
            elif state == "PREVIEW_DONE":
                saved = _read_json(folder / "preview.json") or {}
                report = render_mod.build_report(
                    prepared=prepared, job=self._job_ref(plan, job), mode="preview", status="preview_done",
                    render=None, verification=None, preview=saved or preview_result, cover=(saved or {}).get("cover"),
                    output_file=None, evidence=[], timings={**timings, "wall_total_seconds": round(time.monotonic() - wall, 3),
                                                            "estimate": self._estimate_of(job)},
                    telegram=None)
                render_mod.write_report(report, folder / "render_report.json")
                self._progress(run, "report", 1.0)
                self._log(run, "preview done")
            run.finished = True
        except render_mod.RenderCancelled:
            self._settle_interrupt(run, run.cancel_reason or "cancelled")
        except BaseException as exc:  # noqa: BLE001 - every failure must end in a recorded state
            self._settle_failure(run, exc, prepared, render_result, verification, timings, wall)
        finally:
            run.finished = True
            with contextlib.suppress(Exception):
                shutil.rmtree(folder / "work", ignore_errors=True)
            with contextlib.suppress(Exception):
                self._progress(run, run.stage, run.progress if run.stage != "queued" else 0.0)

    @staticmethod
    def _estimate_of(job: dict[str, Any]) -> dict[str, Any]:
        est = job.get("estimate") or {}
        return {"preview_seconds": est.get("preview_seconds"), "render_seconds": est.get("render_seconds"),
                "basis": est.get("basis")}

    @staticmethod
    def _job_ref(plan: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": plan.get("project_id"), "job_id": job.get("job_id"), "plan_version": plan.get("version"),
                "plan_content_sha256": job.get("plan_content_sha256"), "attempt": job.get("attempt", 1)}

    def _render_phase(self, run: JobRun, prepared: dict[str, Any], job: dict[str, Any], folder: Path,
                      timings: dict[str, Any]) -> None:
        s = self.store
        pid, jid = run.project_id, run.job_id
        plan = s.job_plan(pid, jid)
        env = prepared["env"]
        done = set(job["phases"]["render"]["completed"])
        self._log(run, "render: single ffmpeg pass")
        t = time.monotonic()

        def on_scene(sid: str) -> None:
            s.job_complete_scene(pid, jid, sid, output={"rendered_to": round(run.detail.get("out_time") or 0.0, 3)})

        render_result = render_mod.run_render(
            prepared, folder, cancel=run.cancel, on_scene=on_scene, skip_scenes=done, timeout=run.timeout,
            telegram_variant=run.telegram, on_progress=lambda stage, f, d: self._progress(run, stage, f, d))
        timings["render_seconds"] = round(time.monotonic() - t, 3)
        self._log(run, f"render finished in {timings['render_seconds']} s; verifying the file")
        self._progress(run, "verify", 0.0)
        t = time.monotonic()
        expect = render_mod.expected_output(prepared)
        part = Path(render_result["part"])
        verification = render_mod.verify_output(part, env, expect, cancel=run.cancel)
        timings["verify_seconds"] = round(time.monotonic() - t, 3)
        if not verification["ok"]:
            failed = [c["name"] for c in verification["checks"] if not c["ok"]]
            raise _VerificationFailed("the output file failed verification: " + ", ".join(failed), verification,
                                      render_result)
        output = folder / "output.mp4"
        render_mod.finalize_output(part, output)
        self._progress(run, "cover", 0.0)
        t = time.monotonic()
        cover = render_mod.make_cover(prepared, folder, from_video=str(output), cancel=run.cancel)
        timings["cover_seconds"] = round(time.monotonic() - t, 3)
        telegram = None
        if run.telegram:
            telegram = render_mod.make_telegram_variant(prepared, output, folder / "output-telegram.mp4",
                                                        cancel=run.cancel)
        self._progress(run, "report", 0.0)
        t = time.monotonic()
        evidence = render_mod.overlay_evidence(prepared, output, folder / "work", cancel=run.cancel)
        timings["evidence_seconds"] = round(time.monotonic() - t, 3)
        timings["wall_total_seconds"] = round(time.time() - run.started, 3)
        timings["estimate"] = self._estimate_of(job)
        report = render_mod.build_report(
            prepared=prepared, job=self._job_ref(plan, job), mode="full", status="done", render=render_result,
            verification=verification, preview=_read_json(folder / "preview.json"), cover=cover, output_file=output,
            evidence=evidence, timings=timings, telegram=telegram)
        render_mod.write_report(report, folder / "render_report.json")
        s.job_finish_render(pid, jid, output={
            "file": "output.mp4", "sha256": report["output"]["sha256"], "bytes": report["output"]["bytes"],
            "report": "render_report.json", "verified": True, "execution": report["execution"],
            "telegram_variant": telegram["file"] if telegram else None})
        self._progress(run, "report", 1.0)
        self._log(run, "done: " + report["execution"]["summary"])

    def _settle_interrupt(self, run: JobRun, reason: str) -> None:
        s = self.store
        pid, jid = run.project_id, run.job_id
        folder = self.out_dir(pid, jid)
        with contextlib.suppress(OSError):
            (folder / "output.part.mp4").unlink()
        self._log(run, f"stopped: {reason}")
        try:
            job = s.get_job(pid, jid)
            if job["state"] in RUNNING_STATES:
                s.job_interrupt(pid, jid, reason)
            elif job["state"] in ("PLANNED", "PREVIEW_DONE", "INTERRUPTED"):
                if job["state"] != "INTERRUPTED":
                    s.job_fail(pid, jid, reason)
        except store_mod.StoreError:
            pass

    def _settle_failure(self, run: JobRun, exc: BaseException, prepared: dict[str, Any] | None,
                        render_result: dict[str, Any] | None, verification: dict[str, Any] | None,
                        timings: dict[str, Any], wall: float) -> None:
        s = self.store
        pid, jid = run.project_id, run.job_id
        folder = self.out_dir(pid, jid)
        if isinstance(exc, _VerificationFailed):
            verification, render_result = exc.verification, exc.render
        reason = f"{type(exc).__name__}: {exc}"[:500] if not isinstance(exc, render_mod.RenderError) \
            else str(exc)[:500]
        if render_result is None and isinstance(exc, render_mod.RenderError):
            render_result = exc.context
        self._log(run, f"FAILED: {reason}")
        with contextlib.suppress(OSError):
            (folder / "output.part.mp4").unlink()
        if prepared is not None:
            try:
                plan = s.job_plan(pid, jid)
                job = s.get_job(pid, jid)
                report = render_mod.build_report(
                    prepared=prepared, job=self._job_ref(plan, job), mode=run.mode, status="failed",
                    render=render_result, verification=verification, preview=_read_json(folder / "preview.json"),
                    cover=None, output_file=None, evidence=[],
                    timings={**timings, "wall_total_seconds": round(time.monotonic() - wall, 3)},
                    telegram=None, error=reason)
                report["verified"] = False
                render_mod.write_report(report, folder / "render_report.json")
            except Exception:  # noqa: BLE001 - the failure itself is what must be recorded
                pass
        with contextlib.suppress(store_mod.StoreError):
            s.job_fail(pid, jid, reason)


class _VerificationFailed(render_mod.RenderError):
    code = "verification_failed"

    def __init__(self, message: str, verification: dict[str, Any], render: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.verification, self.render = verification, render


# ============================================================================================ plan edits

def _op_set_cover(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    if op.get("clear"):
        plan.pop("cover", None)
        return
    time_value, title, position = op.get("time"), op.get("title", ""), op.get("position", "center")
    duration = float((plan.get("source") or {}).get("duration") or 0.0)
    if time_value is not None:
        if not isinstance(time_value, (int, float)) or isinstance(time_value, bool) or time_value < 0:
            raise planmod.PlanError("cover time must be a number >= 0 (seconds) or null")
        if duration and time_value > duration:
            raise planmod.PlanError(f"cover time {time_value}s is after the end of the video ({duration}s)")
    if not isinstance(title, str) or len(title) > render_mod.MAX_COVER_TITLE_CHARS or "\x00" in title:
        raise planmod.PlanError(f"cover title must be text of at most {render_mod.MAX_COVER_TITLE_CHARS} characters")
    if position not in render_mod.POSITIONS:
        raise planmod.PlanError(f"cover position must be one of {render_mod.POSITIONS}")
    plan["cover"] = {"time": float(time_value) if time_value is not None else None, "title": title.strip(),
                     "position": position}


def _op_set_soundtrack(plan: dict[str, Any], op: dict[str, Any], _author: str) -> None:
    mode = op.get("mode")
    if mode not in ("keep", "mute", "replace"):
        raise planmod.PlanError("soundtrack mode must be keep, mute or replace")
    tracks = plan["tracks"]
    options = op.get("options")
    if options is not None:
        if not isinstance(options, dict):
            raise planmod.PlanError("soundtrack options must be an object")
        unknown_keys = sorted(set(options) - set(render_mod.DEFAULT_REPLACE_AUDIO))
        if unknown_keys:
            raise planmod.PlanError(f"unknown soundtrack options: {unknown_keys}")
        for key, value in options.items():
            if key == "loudnorm":
                if not isinstance(value, bool):
                    raise planmod.PlanError("soundtrack option loudnorm must be true or false")
            elif not isinstance(value, (int, float)) or isinstance(value, bool) or not -1e6 < value < 1e6:
                raise planmod.PlanError(f"soundtrack option {key} must be a number")
            elif key in render_mod.AUDIO_OPTION_LIMITS:
                lo, hi = render_mod.AUDIO_OPTION_LIMITS[key]
                if not lo <= value <= hi:
                    raise planmod.PlanError(f"soundtrack option {key} must be in [{lo}, {hi}]")
        merged = dict(plan.get("audio_options") or {})
        merged.update(options)
        plan["audio_options"] = merged
    if mode == "replace":
        ref = op.get("reference")
        name = ref[1:] if isinstance(ref, str) and ref.startswith("@") else ref
        entry = (plan.get("references") or {}).get(name) if isinstance(name, str) else None
        if not entry or entry.get("kind") != "audio":
            raise planmod.PlanError(f"soundtrack replace needs an audio reference; {ref!r} is not one")
        tracks["music"].update({"enabled": True, "mode": "replace", "source": f"@{name}"})
        tracks["speech"].update({"enabled": True, "mode": "mute", "source": "original"})
    else:
        for lane in ("speech", "music"):
            tracks[lane].update({"enabled": True, "mode": mode, "source": "original"})


EXT_FUNCS = {"set_cover": _op_set_cover, "set_soundtrack": _op_set_soundtrack}


def _check_cues(ops: list[dict[str, Any]]) -> None:
    for op in ops:
        if op.get("op") == "set_track" and op.get("lane") == "subtitles":
            cues = (op.get("settings") or {}).get("cues")
            if cues is None:
                continue
            if not isinstance(cues, list):
                raise planmod.PlanError("subtitle cues must be a list")
            if len(cues) > planmod.LIMITS["max_subtitle_cues"]:
                raise planmod.LimitError(f"{len(cues)} subtitle cues exceed the declared limit of "
                                         f"{planmod.LIMITS['max_subtitle_cues']}")
            problems = []
            for index, cue in enumerate(cues):
                for found in render_mod.cue_problems(cue):
                    problems.append(f"cue {index}: {found}")
            if problems:
                raise planmod.PlanError("; ".join(problems[:6]), problems)
            op["settings"]["cues"] = sorted(
                ({"start": float(c["start"]), "end": float(c["end"]), "text": c["text"].strip()} for c in cues),
                key=lambda c: (c["start"], c["end"]))


def apply_edit(plan: dict[str, Any], ops: list[dict[str, Any]], author: str) -> dict[str, Any]:
    """Core ops go through ``plan.apply_ops`` (all-or-nothing); the three extension ops are applied on top
    of the same copy, so one request is one atomic version."""
    for op in ops:
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            raise planmod.PlanError("every operation must be an object with an 'op' name")
    _check_cues(ops)
    core = [o for o in ops if o["op"] not in EXT_FUNCS]
    ext = [o for o in ops if o["op"] in EXT_FUNCS]
    result = planmod.apply_ops(plan, core, author=author) if core else copy.deepcopy(plan)
    for op in ext:
        EXT_FUNCS[op["op"]](result, op, author)
    if ext:
        if planmod.content_sha256(result) != planmod.content_sha256(plan):
            result["updated_ts"] = _now()
            result["limits"] = planmod.limits_snapshot()
            if result.get("status") in ("analyzed", "draft"):
                result["status"] = "edited"
        planmod.validate(result)
    return result


# ============================================================================================ uploads

def _multipart_available() -> bool:
    return importlib.util.find_spec("python_multipart") is not None


class _TooLarge(Exception):
    pass


def _limited_request(request: Request, limit: int) -> StarletteRequest:
    total = 0
    original = request.receive

    async def receive():
        nonlocal total
        message = await original()
        if message.get("type") == "http.request":
            total += len(message.get("body") or b"")
            if total > limit:
                raise _TooLarge()
        return message

    return StarletteRequest(request.scope, receive)


async def _receive_upload(request: Request, rt: Runtime, *, limit: int, allowed: tuple[str, ...],
                          what: str) -> tuple[Path, str, dict[str, str]]:
    """Stream an uploaded file into a private staging folder.  Returns (path, original name, form fields).

    Accepts ``multipart/form-data`` (field ``file`` + text fields) or a raw body with ``?name=<file name>``.
    The size limit is enforced WHILE receiving: an oversize upload is refused with 413 and nothing is kept."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit + UPLOAD_SLACK:
        raise _err(413, "TOO_LARGE", f"{what}: размер {int(declared)} байт больше лимита {limit} байт")
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    folder = rt.stage_dir()
    try:
        fields: dict[str, str] = {}
        if ctype == "multipart/form-data":
            if not _multipart_available():
                raise _err(415, "MULTIPART_UNAVAILABLE", "multipart/form-data не поддерживается в этой сборке",
                           hint="отправьте файл телом запроса и укажите имя в параметре ?name=")
            limited = _limited_request(request, limit + UPLOAD_SLACK)
            try:
                form = await limited.form()
            except _TooLarge:
                raise _err(413, "TOO_LARGE", f"{what}: больше лимита {limit} байт") from None
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise _err(422, "NO_FILE", "в форме нет поля file с файлом")
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
            name = _safe_name(getattr(upload, "filename", None), "upload")
            ext = _ext(name)
            if ext not in allowed:
                raise _err(422, "BAD_EXTENSION", f"{what}: расширение {ext or '(нет)'} не разрешено", allowed=list(allowed))
            target = folder / name
            size = 0
            with open(target, "wb") as out:
                while True:
                    block = await upload.read(CHUNK)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        raise _err(413, "TOO_LARGE", f"{what}: больше лимита {limit} байт")
                    out.write(block)
            await form.close()
        else:
            name = _safe_name(request.query_params.get("name") or request.headers.get("x-file-name"), "")
            if not name:
                raise _err(422, "NO_NAME", "для загрузки телом запроса укажите имя файла: ?name=clip.mp4")
            ext = _ext(name)
            if ext not in allowed:
                raise _err(422, "BAD_EXTENSION", f"{what}: расширение {ext or '(нет)'} не разрешено", allowed=list(allowed))
            fields = {k: v for k, v in request.query_params.items() if k != "name"}
            target = folder / name
            size = 0
            buffer = bytearray()
            with open(target, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise _err(413, "TOO_LARGE", f"{what}: больше лимита {limit} байт")
                    buffer += chunk
                    if len(buffer) >= CHUNK:
                        await asyncio.to_thread(out.write, bytes(buffer))
                        buffer.clear()
                if buffer:
                    await asyncio.to_thread(out.write, bytes(buffer))
        if size == 0:
            raise _err(422, "EMPTY_FILE", f"{what}: файл пустой")
        return target, name, fields
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


# ============================================================================================ models

class CreateIn(BaseModel):
    title: str = Field(default="", max_length=120)
    category: str = Field(default="motion", pattern="^(motion|clipping|stylized)$")
    mode: str = Field(default="own_video", pattern="^(preset|own_video)$")


class AnalyzeIn(BaseModel):
    threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    min_scene_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class OpsIn(BaseModel):
    ops: list[dict] = Field(min_length=1, max_length=400)
    expected_version: int | None = Field(default=None, ge=1)
    summary: str | None = Field(default=None, max_length=400)


class RevertIn(BaseModel):
    to_version: int = Field(ge=1)


class JobIn(BaseModel):
    mode: str = Field(default="preview", pattern="^(preview|full)$")
    telegram_variant: bool = False
    timeout_seconds: float | None = Field(default=None, ge=10.0, le=7200.0)


class TextRefIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=20000)
    replace: bool = False


# ============================================================================================ runtime access

def _runtime_for(svc: Any) -> Runtime:
    rt = getattr(svc, "motion_workflow_runtime", None)
    if rt is None or rt.closed:
        rt = Runtime(svc.settings.data_dir)
        svc.motion_workflow_runtime = rt
    return rt


def _rt(request: Request) -> Runtime:
    return _runtime_for(request.app.state.svc)


async def _setup(svc: Any) -> None:
    rt = _runtime_for(svc)

    async def guard() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.to_thread(rt.shutdown)

    svc._tasks.append(asyncio.create_task(guard(), name="bcc-motion-workflow-guard"))


def _project_summary(rt: Runtime, item: dict[str, Any]) -> dict[str, Any]:
    pid = item["project_id"]
    out = dict(item)
    jobs = rt.store.list_jobs(pid)
    if jobs:
        last = jobs[-1]
        out["last_job"] = {"job_id": last["job_id"], "state": rt.api_state(last), "created_ts": last.get("created_ts")}
        out["has_output"] = any(j["state"] == "RENDER_DONE" for j in jobs)
    else:
        out["last_job"], out["has_output"] = None, False
    out["source_staged"] = rt.staged_source(pid) is not None
    return out


def _references(rt: Runtime, plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, ref in (plan.get("references") or {}).items():
        used = [s["id"] for s in plan.get("scenes") or [] if name in planmod.scene_bindings(s)]
        lanes = [lane for lane, data in (plan.get("tracks") or {}).items()
                 if isinstance(data, dict) and data.get("source") == f"@{name}"]
        out.append({"name": name, "kind": ref.get("kind"), "kind_label": KIND_LABEL.get(ref.get("kind")),
                    "ext": ref.get("ext"), "bytes": ref.get("bytes"), "sha256": ref.get("sha256"),
                    "original_name": ref.get("original_name"), "used_by_scenes": used, "used_by_lanes": lanes})
    return out


def _execution_preview(resolved: dict[str, Any]) -> dict[str, Any]:
    return {"counts": resolved["counts"], "summary": render_mod.summary_line(resolved["counts"]),
            "not_executed": [o for o in resolved["operations"] if o["status"] == "not_executed"],
            "applied": [o for o in resolved["operations"] if o["status"] == "applied"],
            "audio": {"action": resolved["audio"]["action"], "reference": resolved["audio"]["reference"],
                      "options": resolved["audio"]["options"], "note": resolved["audio"]["note"],
                      "not_executed": resolved["audio"]["not_executed"]},
            "cover": resolved["cover"], "overlays": len(resolved["overlays"]),
            "output": {k: resolved["geometry"][k] for k in ("width", "height", "fps", "size_changed", "fps_changed")},
            "warnings": resolved["warnings"]}


def _project_view(rt: Runtime, project_id: str, *, jobs: bool = True) -> dict[str, Any]:
    s = rt.store
    plan = s.get_project(project_id)
    env = render_mod.environment()
    ready = planmod.readiness(plan)
    resolved = render_mod.resolve(plan, rt.texts_for(project_id, plan))
    blockers = rt.render_blockers(project_id, plan, env, resolved)
    unknowns = planmod.find_unknowns(plan)
    source = plan.get("source") or {}
    view: dict[str, Any] = {
        "project": planmod.summary(plan), "plan": plan, "version": plan["version"],
        "readiness": {**ready, "render_blockers": blockers, "can_run": bool(ready["ready"] and not blockers)},
        "estimate": planmod.estimate(plan), "limits": planmod.limits_report(plan),
        "execution_preview": _execution_preview(resolved),
        "unknowns": {"count": len(unknowns), "items": unknowns[:60]},
        "history": s.history(project_id, limit=20),
        "source": {"analyzed": bool(source.get("asset")), "info": source or None,
                   "staged": rt.staged_source(project_id), "file_url": None},
        "references": _references(rt, plan),
        "analysis": rt.analysis.get(project_id),
        "renderer": _env_summary(env), "capabilities": render_mod.CAPABILITIES,
        "cloud_statement": planmod.CLOUD_STATEMENT,
    }
    if source.get("asset") and rt.source_path(project_id, plan) is not None:
        view["source"]["file_url"] = f"/api/motion-workflow/projects/{project_id}/source"
    if jobs:
        view["jobs"] = [rt.job_view(project_id, j["job_id"], light=False) for j in reversed(s.list_jobs(project_id))][:12]
        view["active_job"] = next((j["job_id"] for j in view["jobs"] if j["state"] == "running"), None)
    return view


# ============================================================================================ routes: global

@router.get("/status")
def status(request: Request):
    rt = _rt(request)
    env = render_mod.environment()
    return {"available": True, "renderer": _env_summary(env), "analyzers": analyze_mod.available(include_paths=False),
            "capabilities": render_mod.CAPABILITIES, "limits": planmod.limits_snapshot(),
            "cost_model": planmod.COST_MODEL, "cloud_statement": planmod.CLOUD_STATEMENT,
            "upload": {"multipart": _multipart_available(), "raw_body": True,
                       "max_source_bytes": planmod.LIMITS["max_source_bytes"],
                       "max_reference_bytes": planmod.LIMITS["max_reference_bytes"]},
            "recovery": {"orphans_killed": rt.recovery["orphans_killed"],
                         "finished_from_disk": rt.recovery["finished_from_disk"],
                         "interrupted_at_startup": (rt.recovery["store"] or {}).get("interrupted", [])},
            "active_job": ({"project_id": r.project_id, "job_id": r.job_id} if (r := rt.active_run()) else None),
            "multi_process_note": "one backend process per data directory is supported"}


@router.get("/limits")
def limits():
    return {"limits": planmod.limits_snapshot(), "cost_model": planmod.COST_MODEL,
            "cloud_statement": planmod.CLOUD_STATEMENT,
            "renderer": {"text_overlay_max_chars": render_mod.MAX_OVERLAY_CHARS,
                         "cover_title_max_chars": render_mod.MAX_COVER_TITLE_CHARS,
                         "audio_option_limits": render_mod.AUDIO_OPTION_LIMITS}}


@router.get("/projects")
def projects(request: Request):
    rt = _rt(request)
    items = [_project_summary(rt, it) for it in rt.store.list_projects()]
    items.sort(key=lambda it: it.get("updated_ts") or "", reverse=True)
    return {"projects": items}


@router.post("/projects", status_code=201)
async def create_project(request: Request):
    """JSON {title, category, mode} creates an empty project; a multipart or raw video body creates the
    project AND stages the upload as its source (analysis is a separate step)."""
    rt = _rt(request)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    upload = "name" in request.query_params or ctype.startswith("multipart/") or ctype not in ("", "application/json")
    if not upload:
        try:
            body = CreateIn(**(await request.json() if ctype else {}))
        except Exception as exc:  # noqa: BLE001 - body validation -> 422 with the reason
            raise _err(422, "BAD_BODY", f"неверное тело запроса: {str(exc)[:200]}") from None
        with _translate():
            plan = await asyncio.to_thread(rt.store.create_project, title=body.title, category=body.category,
                                           mode=body.mode)
        return await asyncio.to_thread(_project_view, rt, plan["project_id"])
    staged, name, fields = await _receive_upload(request, rt, limit=planmod.LIMITS["max_source_bytes"],
                                                 allowed=planmod.VIDEO_EXTENSIONS, what="исходное видео")
    try:
        category = fields.get("category", "motion")
        mode = fields.get("mode", "own_video")
        title = (fields.get("title") or Path(name).stem)[:120]
        with _translate():
            plan = await asyncio.to_thread(rt.store.create_project, title=title, category=category, mode=mode)
            await asyncio.to_thread(rt.stage_source, plan["project_id"], staged, name)
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)
    return await asyncio.to_thread(_project_view, rt, plan["project_id"])


@router.get("/projects/{project_id}")
def open_project(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return _project_view(rt, project_id)


@router.post("/projects/{project_id}/source", status_code=201)
async def upload_source(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        await asyncio.to_thread(rt.store.get_project, project_id)
    staged, name, _fields = await _receive_upload(request, rt, limit=planmod.LIMITS["max_source_bytes"],
                                                  allowed=planmod.VIDEO_EXTENSIONS, what="исходное видео")
    try:
        with _translate():
            await asyncio.to_thread(rt.stage_source, project_id, staged, name)
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)
    return await asyncio.to_thread(_project_view, rt, project_id)


@router.post("/projects/{project_id}/analyze")
async def analyze(project_id: str, request: Request, body: AnalyzeIn | None = None):
    rt = _rt(request)
    body = body or AnalyzeIn()
    with _translate():
        await asyncio.to_thread(rt.store.get_project, project_id)
        result = await asyncio.to_thread(rt.analyze, project_id, threshold=body.threshold,
                                         min_scene=body.min_scene_seconds)
        view = await asyncio.to_thread(_project_view, rt, project_id)
    view["analyzed_version"] = result["version"]
    return view


@router.get("/projects/{project_id}/analysis")
def analysis_state(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        rt.store.get_project(project_id)
    return rt.analysis.get(project_id) or {"state": "idle"}


@router.get("/projects/{project_id}/plan")
def get_plan(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
    return {"version": plan["version"], "plan": plan}


@router.get("/projects/{project_id}/readiness")
def get_readiness(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
        ready = planmod.readiness(plan)
        blockers = rt.render_blockers(project_id, plan, render_mod.environment())
    return {**ready, "render_blockers": blockers, "can_run": bool(ready["ready"] and not blockers)}


@router.get("/projects/{project_id}/estimate")
def get_estimate(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return rt.store.estimate(project_id)


@router.get("/projects/{project_id}/limits")
def get_limits(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return rt.store.limits(project_id)


@router.get("/projects/{project_id}/history")
def get_history(project_id: str, request: Request, limit: int = 50):
    rt = _rt(request)
    with _translate():
        return {"history": rt.store.history(project_id, limit=max(1, min(limit, 200))),
                "problems": rt.store.verify(project_id)}


@router.get("/projects/{project_id}/versions/{version}")
def get_version(project_id: str, version: int, request: Request):
    rt = _rt(request)
    with _translate():
        return rt.store.get_version(project_id, version)


@router.post("/projects/{project_id}/ops")
def edit_plan(project_id: str, body: OpsIn, request: Request):
    rt = _rt(request)
    with _translate():
        try:
            result = rt.store.update(
                project_id, lambda plan: apply_edit(plan, [dict(o) for o in body.ops], "owner"), author="owner",
                summary=body.summary, expected_version=body.expected_version)
        except store_mod.ConflictError as exc:
            current = rt.store.get_project(project_id)["version"]
            raise _err(409, "VERSION_CONFLICT", str(exc), hint="проект изменился: откройте его заново",
                       current_version=current) from None
        view = _project_view(rt, project_id, jobs=False)
    return {"version": result["version"], "changed": result["changed"], "summary": result["summary"],
            "diff": result["diff"], **{k: view[k] for k in ("plan", "readiness", "estimate", "limits",
                                                             "execution_preview", "unknowns", "references")}}


@router.post("/projects/{project_id}/revert")
def revert_plan(project_id: str, body: RevertIn, request: Request):
    rt = _rt(request)
    with _translate():
        result = rt.store.revert(project_id, body.to_version)
        view = _project_view(rt, project_id, jobs=False)
    return {"version": result["version"], "changed": result["changed"], "summary": result["summary"],
            "plan": view["plan"], "readiness": view["readiness"], "execution_preview": view["execution_preview"]}


# ============================================================================================ routes: references

@router.get("/projects/{project_id}/references")
def list_references(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
    return {"references": _references(rt, plan), "kinds": list(planmod.REFERENCE_KINDS),
            "extensions": {k: list(v) for k, v in planmod.KIND_EXTENSIONS.items()},
            "max_references": planmod.LIMITS["max_references"]}


@router.post("/projects/{project_id}/references", status_code=201)
async def add_reference(project_id: str, request: Request):
    """JSON ``{name, text}`` binds a text reference; a multipart / raw upload binds an image, audio or
    text file as ``@name`` of the given kind."""
    rt = _rt(request)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    with _translate():
        await asyncio.to_thread(rt.store.get_project, project_id)
    if ctype == "application/json":
        try:
            body = TextRefIn(**(await request.json()))
        except Exception as exc:  # noqa: BLE001
            raise _err(422, "BAD_BODY", f"неверное тело запроса: {str(exc)[:200]}") from None
        with _translate():
            await asyncio.to_thread(rt.store.add_text_reference, project_id, body.name, body.text,
                                    replace=body.replace)
        return await asyncio.to_thread(_project_view, rt, project_id, jobs=False)
    kind = request.query_params.get("kind", "")
    if ctype == "multipart/form-data":
        # the kind is a form field there; the limit has to be known before the stream is read, so the
        # widest declared reference limit is used and the per-kind cap is enforced right after
        limit = max(planmod.LIMITS["max_reference_bytes"].values())
    else:
        if kind not in planmod.REFERENCE_KINDS:
            raise _err(422, "BAD_KIND", f"kind должен быть одним из {list(planmod.REFERENCE_KINDS)}")
        limit = planmod.LIMITS["max_reference_bytes"][planmod.KIND_SIZE_CLASS[kind]]
    allowed = tuple(sorted({e for exts in planmod.KIND_EXTENSIONS.values() for e in exts})) \
        if ctype == "multipart/form-data" else planmod.KIND_EXTENSIONS[kind]
    staged, name, fields = await _receive_upload(request, rt, limit=limit, allowed=allowed, what="материал")
    try:
        kind = fields.get("kind", kind)
        if kind not in planmod.REFERENCE_KINDS:
            raise _err(422, "BAD_KIND", f"kind должен быть одним из {list(planmod.REFERENCE_KINDS)}")
        ref_name = fields.get("name") if ctype == "multipart/form-data" else request.query_params.get("ref")
        ref_name = ref_name or Path(name).stem[:32]
        replace = str(fields.get("replace", "")).lower() in ("1", "true", "yes")
        with _translate():
            cap = planmod.LIMITS["max_reference_bytes"][planmod.KIND_SIZE_CLASS[kind]]
            if staged.stat().st_size > cap:
                raise _err(413, "TOO_LARGE", f"материал {kind}: {staged.stat().st_size} байт больше лимита {cap}")
            await asyncio.to_thread(rt.store.add_reference, project_id, ref_name, kind, staged, replace=replace)
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)
    return await asyncio.to_thread(_project_view, rt, project_id, jobs=False)


@router.delete("/projects/{project_id}/references/{name}")
def remove_reference(project_id: str, name: str, request: Request):
    rt = _rt(request)
    with _translate():
        rt.store.remove_reference(project_id, name)
        return _project_view(rt, project_id, jobs=False)


@router.get("/projects/{project_id}/references/{name}/file")
def reference_file(project_id: str, name: str, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
        ref = (plan.get("references") or {}).get(name)
        if not ref:
            raise _err(404, "NOT_FOUND", f"нет материала @{name}")
        path = rt.store.asset_path(project_id, ref["sha256"], ref.get("ext"))
    return FileResponse(path, filename=ref.get("original_name") or path.name)


@router.get("/projects/{project_id}/source")
def source_file(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
        path = rt.source_path(project_id, plan)
        if path is None:
            raise _err(404, "NOT_FOUND", "у проекта нет проанализированного исходного видео")
    return FileResponse(path, filename=(plan.get("source") or {}).get("file_name") or path.name)


# ============================================================================================ routes: jobs

@router.post("/projects/{project_id}/jobs", status_code=202)
def start_job(project_id: str, body: JobIn, request: Request):
    rt = _rt(request)
    with _translate():
        plan = rt.store.get_project(project_id)
        env = render_mod.environment()
        if not env["ready"]:
            raise _err(503, "RENDERER_UNAVAILABLE", "рендерер недоступен: " + "; ".join(env["problems"]),
                       hint="нужен ffmpeg с libx264 и aac, и ffprobe", problems=env["problems"])
        with rt.lock:                    # check + start are one step: two quick POSTs cannot both run
            active = rt.active_run()
            if active is not None:
                raise _err(409, "JOB_RUNNING", "уже идёт другая задача рендера: дождитесь её или остановите",
                           active={"project_id": active.project_id, "job_id": active.job_id})
            blockers = rt.render_blockers(project_id, plan, env)
            if blockers:
                raise _err(422, "NOT_READY", "проект не готов к запуску: " + "; ".join(blockers[:4]),
                           problems=blockers)
            job = rt.store.create_job(project_id)             # PlanError(blockers) if the plan is not ready
            rt.start(project_id, job["job_id"], body.mode, body.telegram_variant, body.timeout_seconds)
        return rt.job_view(project_id, job["job_id"], light=True)


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return {"jobs": [rt.job_view(project_id, j["job_id"], light=True)
                         for j in reversed(rt.store.list_jobs(project_id))]}


@router.get("/projects/{project_id}/jobs/{job_id}")
def get_job(project_id: str, job_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return rt.job_view(project_id, job_id)


def _continue(rt: Runtime, project_id: str, job_id: str, want: str) -> dict[str, Any]:
    env = render_mod.environment()
    if not env["ready"]:
        raise _err(503, "RENDERER_UNAVAILABLE", "рендерер недоступен: " + "; ".join(env["problems"]))
    with rt.lock:
        job = rt.store.get_job(project_id, job_id)
        active = rt.active_run()
        if active is not None:
            raise _err(409, "JOB_RUNNING", "уже идёт задача рендера: дождитесь её или остановите")
        if want == "render":
            if job["state"] != "PREVIEW_DONE":
                raise _err(409, "JOB_STATE", f"полный рендер продолжает задачу в состоянии «предпросмотр готов» "
                                             f"(сейчас {rt.api_state(job)})")
            mode = "full"
        else:
            if job["state"] != "INTERRUPTED":
                raise _err(409, "JOB_STATE",
                           f"возобновить можно только прерванную задачу (сейчас {rt.api_state(job)})")
            phase = (job.get("interrupted") or {}).get("phase")
            mode = "full" if phase == "render" or rt.job_meta(project_id, job_id).get("mode") == "full" \
                else "preview"
        meta = rt.job_meta(project_id, job_id)
        rt.start(project_id, job_id, mode, bool(meta.get("telegram_variant")), meta.get("timeout_seconds"))
    return rt.job_view(project_id, job_id, light=True)


@router.post("/projects/{project_id}/jobs/{job_id}/render", status_code=202)
def render_job(project_id: str, job_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return _continue(rt, project_id, job_id, "render")


@router.post("/projects/{project_id}/jobs/{job_id}/resume", status_code=202)
def resume_job(project_id: str, job_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        return _continue(rt, project_id, job_id, "resume")


@router.post("/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: str, job_id: str, request: Request):
    rt = _rt(request)
    with _translate():
        job = rt.store.get_job(project_id, job_id)
        run = rt.runs.get((project_id, job_id))
        if run is not None and run.alive():
            run.cancel_reason = "cancelled by owner"
            run.cancel.set()
            if run.thread is not None:
                run.thread.join(CANCEL_WAIT_SECONDS)
        elif job["state"] in ("PLANNED", "PREVIEW_DONE"):
            rt.store.job_fail(project_id, job_id, "cancelled by owner")
        elif job["state"] in RUNNING_STATES:
            rt.store.job_interrupt(project_id, job_id, "cancelled by owner")
        elif job["state"] in ("RENDER_DONE", "FAILED"):
            raise _err(409, "JOB_STATE", f"задача уже завершена ({rt.api_state(job)})")
        return rt.job_view(project_id, job_id)


@router.get("/projects/{project_id}/jobs/{job_id}/check")
async def check_job(project_id: str, job_id: str, request: Request):
    """Verify the output file NOW (ffprobe + full decode) and compare it with the report."""
    rt = _rt(request)
    with _translate():
        job = await asyncio.to_thread(rt.store.get_job, project_id, job_id)
        folder = rt.out_dir(project_id, job_id)
    if job["state"] in RUNNING_STATES:
        raise _err(409, "JOB_RUNNING", "рендер ещё идёт")
    output = folder / "output.mp4"
    report = _read_json(folder / "render_report.json") or {}
    if not output.is_file():
        return {"verified": False, "reason": "файл output.mp4 не создан", "state": rt.api_state(job)}
    env = render_mod.environment()
    if not env["ready"]:
        return {"verified": False, "reason": "ffprobe/ffmpeg недоступны: файл нельзя проверить"}
    params = report.get("parameters") or {}
    res, fps = params.get("resolution") or {}, params.get("fps") or {}
    inp = report.get("input") or {}
    audio_action = (params.get("audio") or {}).get("action", "keep")
    expect = {"width": res.get("width"), "height": res.get("height"), "fps": fps.get("value"),
              "duration": inp.get("duration"), "frames": None if fps.get("changed") else inp.get("frames"),
              "audio": "none" if audio_action in ("mute", "none") else "aac"}
    if None in (expect["width"], expect["height"], expect["fps"], expect["duration"]):
        return {"verified": False, "reason": "нет render_report.json: нечего сравнивать с файлом"}
    check = await asyncio.to_thread(render_mod.verify_output, output, env, expect)
    sha = await asyncio.to_thread(analyze_mod.sha256_file, output)
    recorded = (report.get("output") or {}).get("sha256")
    ok = bool(check["ok"] and recorded == sha)
    return {"verified": ok, "sha256": sha, "matches_report": recorded == sha, "bytes": output.stat().st_size,
            "duration_s": check.get("format", {}).get("duration"), "video": check.get("video"),
            "audio": check.get("audio"), "checks": check["checks"],
            "reason": "" if ok else ("файл не совпадает с отчётом (sha256)" if recorded != sha
                                     else "проверка файла не пройдена")}


@router.get("/projects/{project_id}/jobs/{job_id}/file")
def job_file(project_id: str, job_id: str, name: str, request: Request):
    rt = _rt(request)
    with _translate():
        folder = rt.out_dir(project_id, job_id)
        rt.store.get_job(project_id, job_id)
    if name not in FILE_NAMES and not PREVIEW_FILE_RE.match(name):
        raise _err(404, "NOT_FOUND", "такого файла нет")
    path = (folder / name).resolve()
    if folder.resolve() not in path.parents or not path.is_file():
        raise _err(404, "NOT_FOUND", "такого файла нет")
    return FileResponse(path, filename=path.name)


FEATURE = Feature(name="motion_workflow_api", router=router, setup=_setup)
