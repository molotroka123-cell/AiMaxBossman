"""Motion Studio in the Command Center: Epic preset, preview, music, subtitles, full render, file check.

A thin owner-facing control surface over the EXISTING command-line tool
``tools/motion_studio/make_video.py`` (spec validator, Epic renderer, original
score, Kokoro voice). Nothing is re-implemented here and no cloud is used:

  GET  /motion-studio/status              — tool present?, dependencies, examples, recent jobs
  GET  /motion-studio/examples/{name}     — a bundled spec (starting point for the form)
  POST /motion-studio/jobs                — {mode: preview|full, spec | example, ...}
  GET  /motion-studio/jobs, /jobs/{id}    — state (running / done / failed / cancelled / interrupted) + log tail
  POST /motion-studio/jobs/{id}/cancel
  GET  /motion-studio/jobs/{id}/check     — file check: ffprobe streams/duration/sha256; verified only if real
  GET  /motion-studio/jobs/{id}/file      — download one whitelisted output

The render runs in its own process (never in the Jeff or backend event loop),
one job at a time, with a hard time limit. A job is «done» only when its file
exists and passes the check; otherwise it is reported failed with the reason.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter(prefix="/motion-studio", tags=["motion-studio"])

MAX_SPEC_BYTES = 256 * 1024
JOB_TIMEOUT_SECONDS = 40 * 60
LOG_TAIL = 2000
OUTPUTS = {"video.mp4", "video-telegram.mp4", "soundtrack.wav", "spec.json"}
_JOBS: dict[str, dict[str, Any]] = {}
_PROCS: dict[str, asyncio.subprocess.Process] = {}
_SEEN: dict[Path, tuple[int, int, Any]] = {}   # job.json -> (mtime_ns, size, its job id; "" = unreadable)
_SETTLING: dict[str, asyncio.Task] = {}        # job id -> the one settle running for it in a worker thread


def tool_dir() -> Path:
    override = os.environ.get("BOSSMAN_MOTION_STUDIO_DIR", "").strip()
    if override:
        return Path(override)
    source = Path(__file__).resolve().parents[3] / "tools" / "motion_studio"
    # Installed bundle: this module sits in runtime/Lib/site-packages; the builder ships the tool to app-support.
    installed = Path(sys.executable).resolve().parents[1] / "app-support" / "motion_studio"
    return installed if not source.is_dir() and installed.is_dir() else source


def _root(request: Request) -> Path:
    return Path(request.app.state.svc.settings.data_dir) / "motion-studio"


def _deps() -> dict[str, bool]:
    return {"numpy": importlib.util.find_spec("numpy") is not None,
            "pillow": importlib.util.find_spec("PIL") is not None,
            "scipy": importlib.util.find_spec("scipy") is not None,
            "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}


def _examples() -> list[str]:
    folder = tool_dir() / "examples"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


class JobIn(BaseModel):
    mode: str = Field(default="preview", pattern="^(preview|full)$")
    example: str | None = Field(default=None, max_length=80)
    spec: dict | None = None
    times: list[float] = Field(default_factory=lambda: [1.0, 5.0, 9.0], max_length=8)
    no_voice: bool = True
    tts_models: str = Field(default="", max_length=400)


def _spec_facts(spec: dict) -> dict[str, Any]:
    scenes = spec.get("scenes") if isinstance(spec, dict) else None
    lines = sum(len(sc.get("vo") or []) for sc in scenes or [] if isinstance(sc, dict))
    return {"scenes": len(scenes or []), "subtitle_lines": lines}


def _save(job: dict[str, Any]) -> None:
    path = Path(job["dir"]) / "job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _orphan(job: dict[str, Any]):
    """This job's render process when only its pid survived a backend restart, else None.

    A bare «pid exists» is not enough: after a restart the pid may belong to an unrelated
    program (pid reuse), which must neither keep the job «running» nor ever be killed.
    The process counts as ours only while its command line is make_video.py on THIS job's spec.
    """
    try:
        import psutil
        proc = psutil.Process(int(job.get("pid") or 0))
        argv = proc.cmdline()
    except Exception:  # noqa: BLE001 — gone, zombie, access denied or psutil missing: not confirmed ours
        return None
    spec = str(Path(job["dir"]) / "spec.json")
    return proc if spec in argv and any(Path(a).name == "make_video.py" for a in argv) else None


def _kill_orphan(proc) -> None:
    import psutil
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
        pass


def _load_jobs(root: Path) -> None:
    """Add jobs found on disk (also ones another process started) that are not in memory yet.

    Runs on every 2 s poll, so each file is parsed once: a job already in memory is not re-read
    (its file was never applied again anyway), a broken file only after its mtime/size changes.
    One directory listing per poll; only folders of jobs not in memory yet are looked into.
    """
    for folder in root.iterdir() if root.is_dir() else []:
        path = folder / "job.json"
        seen = _SEEN.get(path)
        if seen is not None and seen[2] and seen[2] in _JOBS:
            continue
        try:
            st = path.stat()   # no job.json (or not a folder): OSError, skipped like glob skipped it
            if seen is not None and not seen[2] and seen[:2] == (st.st_mtime_ns, st.st_size):
                continue
            job = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        except ValueError:
            job = None
        ok = isinstance(job, dict) and job.get("id")
        _SEEN[path] = (st.st_mtime_ns, st.st_size, job["id"] if ok else "")
        if ok and job["id"] not in _JOBS:
            _JOBS[job["id"]] = job


async def _settle_once(job: dict[str, Any], code: int | None) -> None:
    """Run ``_settle`` in a worker thread (ffprobe may take a minute), once per job: concurrent
    polls of the same finished render wait for that one settle instead of starting their own."""
    task = _SETTLING.get(job["id"])
    if task is None:
        task = _SETTLING[job["id"]] = asyncio.create_task(asyncio.to_thread(_settle, job, code))
        task.add_done_callback(lambda _done, key=job["id"]: _SETTLING.pop(key, None))
    await asyncio.shield(task)


async def _refresh(job: dict[str, Any]) -> dict[str, Any]:
    """Settle a job whose process has ended; a lost process after restart is «interrupted»."""
    if job.get("state") != "running":
        return job
    proc = _PROCS.get(job["id"])
    if proc is not None:
        if proc.returncode is None:
            if time.time() - float(job["started"]) > JOB_TIMEOUT_SECONDS:
                proc.kill()
                job.update(state="failed", error="время рендера вышло", finished=time.time())
                _save(job)
            return job
        await _settle_once(job, int(proc.returncode))
        return job
    orphan = _orphan(job)
    if orphan is None:
        await _settle_once(job, None)
    elif time.time() - float(job["started"]) > JOB_TIMEOUT_SECONDS:
        with contextlib.suppress(Exception):   # it may have ended on its own in the meantime
            orphan.kill()
        job.update(state="failed", error="время рендера вышло", finished=time.time())
        _save(job)
    return job


def _settle(job: dict[str, Any], code: int | None) -> None:
    job["finished"] = time.time()
    if code is None:
        job.update(state="interrupted", error="процесс рендера потерян (перезапуск?)")
    elif code != 0:
        job.update(state="failed", error=f"make_video завершился с кодом {code}")
    else:
        report = _check(job)
        if report["verified"]:
            job.update(state="done", error="")
        else:
            job.update(state="failed", error="результат не прошёл проверку файла: " + report["reason"])
    _save(job)


def _log_tail(job: dict[str, Any]) -> str:
    """The last LOG_TAIL characters of the render log, reading only its end (it is polled every 2 s)."""
    try:
        with (Path(job["dir"]) / "make_video.log").open("rb") as fh:
            # a character is at most 4 UTF-8 bytes; 3 more cover a character cut at the window start
            fh.seek(max(0, fh.seek(0, os.SEEK_END) - LOG_TAIL * 4 - 3))
            raw = fh.read()
    except OSError:
        return ""
    # the same text read_text() gave: errors replaced, universal newlines
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")[-LOG_TAIL:]


def _public(job: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "mode", "state", "error", "started", "finished", "style", "no_voice", "scenes",
            "subtitle_lines", "times", "music", "source")
    out = {k: job.get(k) for k in keep}
    out["log_tail"] = _log_tail(job)
    out["outputs"] = sorted(n for n in os.listdir(job["dir"])
                            if n in OUTPUTS or n.startswith("epic-preview-")) if Path(job["dir"]).is_dir() else []
    return out


def _check(job: dict[str, Any]) -> dict[str, Any]:
    """File check. Preview: real PNGs. Full: ffprobe shows a video and an audio stream with a duration."""
    folder = Path(job["dir"])
    if job["mode"] == "preview":
        images = sorted(folder.glob("epic-preview-*.png"))
        good = [p for p in images if p.stat().st_size > 1000]
        return {"verified": bool(good) and len(good) == len(images), "kind": "preview",
                "files": [{"name": p.name, "bytes": p.stat().st_size} for p in images],
                "reason": "" if good else "нет ни одного кадра предпросмотра"}
    video = folder / "video.mp4"
    if not video.is_file() or video.stat().st_size < 1000:
        return {"verified": False, "kind": "video", "reason": "файл video.mp4 не создан"}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"verified": False, "kind": "video", "reason": "ffprobe не найден: файл нельзя проверить"}
    try:
        raw = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60)
        info = json.loads(raw.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"verified": False, "kind": "video", "reason": f"ffprobe не прочитал файл: {type(exc).__name__}"}
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((info.get("format") or {}).get("duration") or 0)
    with video.open("rb") as fh:   # in chunks: a full render is never held in memory whole
        sha = hashlib.file_digest(fh, "sha256").hexdigest()
    problems = []
    if v is None:
        problems.append("нет видеодорожки")
    if a is None:
        problems.append("нет звуковой дорожки (музыка не попала в файл)")
    if duration <= 0:
        problems.append("нулевая длительность")
    return {"verified": not problems, "kind": "video", "reason": "; ".join(problems),
            "duration_s": round(duration, 2), "bytes": video.stat().st_size, "sha256": sha,
            "video": {"codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height")} if v else None,
            "audio": {"codec": a.get("codec_name")} if a else None,
            "music": (folder / "soundtrack.wav").is_file(), "subtitle_lines": job.get("subtitle_lines", 0)}


@router.get("/status")
async def status(request: Request):
    root = _root(request)
    _load_jobs(root)
    tool = tool_dir()
    deps = _deps()
    available = (tool / "make_video.py").is_file()
    # make_video.py imports the score module (scipy) even for a preview.
    missing = [name for name in ("numpy", "pillow", "scipy") if not deps[name]]
    return {"available": available, "tool_dir": str(tool) if available else "",
            "presets": ["epic"], "examples": _examples(), "deps": deps,
            "ready_preview": available and not missing,
            "ready_full": available and not missing and deps["ffmpeg"] and deps["ffprobe"],
            "why_not": ("инструмент tools/motion_studio не найден в этой сборке" if not available else
                        ("не хватает: " + ", ".join(missing)) if missing else ""),
            "jobs": [_public(await _refresh(j)) for j in sorted(_JOBS.values(), key=lambda j: -j["started"])[:10]]}


@router.get("/examples/{name}")
async def example(name: str):
    if name not in _examples():
        raise HTTPException(404, "Такого примера нет.")
    return json.loads((tool_dir() / "examples" / f"{name}.json").read_text(encoding="utf-8"))


@router.post("/jobs", status_code=202)
async def start_job(body: JobIn, request: Request):
    tool = tool_dir()
    if not (tool / "make_video.py").is_file():
        raise HTTPException(409, "Motion Studio не входит в эту сборку (нет tools/motion_studio).")
    root = _root(request)
    _load_jobs(root)
    for job in list(_JOBS.values()):   # a copy: other polls may add jobs while this one awaits a settle
        if (await _refresh(job)).get("state") == "running":
            raise HTTPException(409, "Рендер уже идёт: дождитесь его или остановите.")
    if body.example and body.spec:
        raise HTTPException(422, "Укажите либо example, либо spec.")
    if body.example:
        if body.example not in _examples():
            raise HTTPException(404, "Такого примера нет.")
        spec = json.loads((tool / "examples" / f"{body.example}.json").read_text(encoding="utf-8"))
    elif body.spec:
        spec = body.spec
    else:
        raise HTTPException(422, "Нужен example или spec.")
    raw = json.dumps(spec, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_SPEC_BYTES or not isinstance(spec.get("scenes"), list):
        raise HTTPException(422, "Сценарий слишком большой или без списка scenes.")
    if body.mode == "full" and not body.no_voice and not body.tts_models:
        raise HTTPException(422, "Для озвучки нужна папка с моделями Kokoro (или включите «без озвучки»).")
    if body.mode == "full" and not _deps()["ffmpeg"]:
        raise HTTPException(409, "Нужен ffmpeg для полного рендера.")
    job_id = uuid.uuid4().hex[:12]
    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.json").write_text(raw, encoding="utf-8")
    argv = [sys.executable, str(tool / "make_video.py"), str(folder / "spec.json"), "--work", str(folder),
            "--style", "epic"]
    if body.mode == "preview":
        argv += ["--preview", *[f"{float(t):g}" for t in body.times]]
    elif body.no_voice:
        argv += ["--no-voice"]
    else:
        argv += ["--tts-models", body.tts_models]
    log = open(folder / "make_video.log", "wb")
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(tool), stdout=log, stderr=asyncio.subprocess.STDOUT)
    finally:
        log.close()
    job = {"id": job_id, "mode": body.mode, "state": "running", "error": "", "started": time.time(),
           "finished": None, "style": "epic", "no_voice": body.no_voice, "pid": proc.pid, "dir": str(folder),
           "times": body.times if body.mode == "preview" else [], "music": body.mode == "full",
           "source": body.example or "custom", **_spec_facts(spec)}
    _JOBS[job_id] = job
    _PROCS[job_id] = proc
    _save(job)
    return _public(job)


async def _job(job_id: str, request: Request) -> dict[str, Any]:
    _load_jobs(_root(request))
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Такого рендера нет.")
    return await _refresh(job)


@router.get("/jobs")
async def jobs(request: Request):
    _load_jobs(_root(request))
    return {"jobs": [_public(await _refresh(j)) for j in sorted(_JOBS.values(), key=lambda j: -j["started"])[:20]]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    return _public(await _job(job_id, request))


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    job = await _job(job_id, request)
    proc = _PROCS.get(job_id)
    if job["state"] == "running":
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        elif proc is None and (orphan := _orphan(job)) is not None:
            await asyncio.to_thread(_kill_orphan, orphan)   # render outlived a restart: stop it for real
        job.update(state="cancelled", error="остановлено владельцем", finished=time.time())
        _save(job)
    return _public(job)


@router.get("/jobs/{job_id}/check")
async def check(job_id: str, request: Request):
    job = await _job(job_id, request)
    if job["state"] == "running":
        raise HTTPException(409, "Рендер ещё идёт.")
    return await asyncio.to_thread(_check, job)


@router.get("/jobs/{job_id}/file")
async def file(job_id: str, name: str, request: Request):
    job = await _job(job_id, request)
    if not (name in OUTPUTS or (name.startswith("epic-preview-") and name.endswith(".png") and "/" not in name
                                and "\\" not in name)):
        raise HTTPException(404, "Такого файла нет.")
    path = Path(job["dir"]) / name
    if not path.is_file():
        raise HTTPException(404, "Такого файла нет.")
    return FileResponse(path, filename=name)


FEATURE = Feature(name="motion_studio", router=router)
