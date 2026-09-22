"""Local AI generation through stable-diffusion.cpp (Vulkan) — Studio provider.

Why this engine: on the owner's Radeon 8060S the LLM stack already runs on
Vulkan (llama.cpp). The Wan 2.2 14B ComfyUI/ROCm path takes ~27 min for 81
frames at 42 GB peak (AMD ROCm blog). stable-diffusion.cpp runs the same model families from GGUF on
the same Vulkan driver, as one short-lived child process per job: nothing stays
resident next to MAIN/FAST after the job.

Models (pinned files, see `models/media/MANIFEST.json`):
  * `sdcpp:wan2.2-ti2v-5b`  — Wan2.2 TI2V-5B, text→video and image→video
    (`start` role), Apache-2.0.
  * `sdcpp:z-image-turbo`   — Z-Image-Turbo, text→image, Apache-2.0.

Honesty boundary: the engine writes .webm/.png; the Studio persists only
bytes that pass ffprobe + full decode (runtime.verify_file). The MP4 made from
the engine's .webm is a container transcode of the model output — the trace
records the raw engine output hash, the command line, model file hashes,
elapsed time and peak memory, so an FFmpeg render can never pose as generation.

Configuration (env, owner-set; loopback/no network):
  BOSSMAN_SDCPP_BIN      path to sd-cli.exe
  BOSSMAN_MEDIA_MODELS   directory with MANIFEST.json and the model files

Restart safety (MEDIA-RESTART): an engine must never outlive the backend that
started it unobserved. Two independent layers:
  1. Windows: the engine is created suspended, put into a Job Object with
     KILL_ON_JOB_CLOSE held only by this backend, then resumed — when the backend
     dies the kernel kills the whole engine tree.
  2. Every platform: a durable record `<storage_root>/engine-work/<rid>.engine.json`
     (owner backend pid+create_time, engine tree pid+create_time) is written before
     the engine runs. A new SdCppProvider reaps records whose owner backend is dead:
     it kills only processes whose pid AND create_time match the record (PID reuse
     safe), deletes the orphan's output (never accepted as a generation) and logs it.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from bcc.studio.catalog import validate_settings
from bcc.studio.provider import (Fetched, GenerationPlane, ProviderOutput, ProviderStatus,
                                 Submitted)
from bcc.studio.providers import _winjob

log = logging.getLogger(__name__)
RECORD_SUFFIX = ".engine.json"
_RID = re.compile(r"^[0-9a-f]{16}$")
# Layer-1 switch; a regression test turns it off inside a child backend to prove layer 2 alone.
USE_JOB_OBJECT = True

BIN_ENV = "BOSSMAN_SDCPP_BIN"
MODELS_ENV = "BOSSMAN_MEDIA_MODELS"

# model id -> manifest entry name; the files and their sha256 come from the manifest.
ENGINES = {
    "sdcpp:wan2.2-ti2v-5b": "wan2.2-ti2v-5b",
    "sdcpp:z-image-turbo": "z-image-turbo",
}


def configuration() -> dict | None:
    """Engine binary + verified manifest, or None (not configured)."""
    binary = os.environ.get(BIN_ENV, "").strip()
    root = os.environ.get(MODELS_ENV, "").strip()
    if not binary or not root:
        return None
    exe, models = Path(binary), Path(root)
    manifest_path = models / "MANIFEST.json"
    if not exe.is_file() or not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"bin": exe, "root": models, "manifest": manifest}


def engine_files(cfg: dict, model_id: str) -> dict[str, Path]:
    entry = cfg["manifest"]["engines"][ENGINES[model_id]]
    out = {}
    for role, spec in entry["files"].items():
        path = (cfg["root"] / spec["path"]).resolve()
        if cfg["root"].resolve() not in path.parents:
            raise PermissionError(f"{role}: model path escapes the media model directory")
        if not path.is_file() or path.stat().st_size != int(spec["bytes"]):
            raise FileNotFoundError(f"{role}: {spec['path']} missing or size differs from manifest")
        _verified_sha(path, str(spec["sha256"]))     # MEDIA-HASH: same-size corruption is caught here
        out[role] = path
    return out


def _argv(cfg: dict, model_id: str, plane: GenerationPlane, settings: dict,
          files: dict[str, Path], out: Path, init: Path | None) -> list[str]:
    argv = [str(cfg["bin"])]
    if model_id == "sdcpp:wan2.2-ti2v-5b":
        argv += ["-M", "vid_gen", "--diffusion-model", str(files["diffusion"]),
                 "--vae", str(files["vae"]), "--t5xxl", str(files["text_encoder"]),
                 "--video-frames", str(settings["frames"]), "--fps", str(settings["fps"]),
                 "--flow-shift", "5.0", "--cfg-scale", str(settings["cfg_scale"]),
                 "--sampling-method", "euler"]
        if init is not None:
            argv += ["-i", str(init)]
    else:
        argv += ["--diffusion-model", str(files["diffusion"]), "--vae", str(files["vae"]),
                 "--llm", str(files["text_encoder"]), "--cfg-scale", "1.0"]
    argv += ["-p", plane.prompt, "-W", str(settings["width"]), "-H", str(settings["height"]),
             "--steps", str(settings["steps"]), "-s", str(settings["seed"]),
             "-o", str(out)]
    # Живой прогон 2026-09-21 (Radeon 8060S, Vulkan): с --diffusion-fa/--vae-tiling
    # при 480x288/10 шагов Wan выдавал цветной шум; без них 832x480/20 шагов —
    # правильный ролик. Флаги не включаем, пока причина не изолирована.
    return argv


class SdCppProvider:
    name = "sdcpp"

    def __init__(self, cfg: dict, storage_root: Path, model: dict):
        self.cfg = cfg
        self.root = Path(storage_root).resolve()
        self.model = model
        self.work = self.root / "engine-work"
        self.work.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self.traces: dict[str, dict] = {}
        self.reaped: list[dict] = []
        try:                                          # MEDIA-RESTART: stop a dead backend's engines
            self.reaped = reap_orphans(self.root)
        except Exception:                             # a reap problem must not block Studio
            log.exception("sdcpp restart reap failed for %s", self.root)

    async def submit(self, plane: GenerationPlane) -> Submitted:
        if plane.model not in ENGINES:
            raise ValueError("model: unknown sdcpp engine")
        if not isinstance(plane.prompt, str) or not plane.prompt.strip():
            raise ValueError("prompt: required")
        settings = validate_settings(self.model, plane.settings)
        files = await asyncio.to_thread(engine_files, self.cfg, plane.model)
        rid = uuid.uuid4().hex[:16]
        video = self.model["surface"] == "video"
        raw = self.work / f"{rid}.{'webm' if video else 'png'}"
        init = None
        if plane.media:
            if not video:
                raise ValueError("media: image model is text-to-image only")
            import base64
            uri = plane.media[0]["data_uri"]
            head, b64 = uri.split(",", 1)
            ext = ".jpg" if "jpeg" in head else ".png"
            init = self.work / f"{rid}-start{ext}"
            init.write_bytes(base64.b64decode(b64))
        argv = _argv(self.cfg, plane.model, plane, settings, files, raw, init)
        job = {"settings": settings, "raw": raw, "init": init, "canceled": False,
               "argv": argv, "files": files, "started": time.time(), "log": []}
        self._jobs[rid] = job
        job["task"] = asyncio.create_task(self._run(rid, job))
        return Submitted(rid, cancel_ref=rid)

    async def _run(self, rid: str, job: dict) -> None:
        if job.get("canceled"):
            return                                    # MEDIA-CANCEL: cancel before spawn — do not start inference
        import psutil
        from bcc.video_studio.media import child_priority_kwargs
        kwargs = child_priority_kwargs()
        winjob = _winjob.create_kill_on_close_job() if USE_JOB_OBJECT else None
        if _winjob.WINDOWS:
            # MEDIA-RESTART: born suspended, so job + record exist before it runs a single step.
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | _winjob.CREATE_SUSPENDED
        try:
            proc = await asyncio.create_subprocess_exec(
                *job["argv"], stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, cwd=str(self.work), **kwargs)
        except BaseException:
            _winjob.close(winjob)
            raise
        job["proc"] = proc
        try:
            if winjob is not None and not _winjob.assign(winjob, proc.pid):
                log.warning("sdcpp %s: engine not in a kill-on-close job; relying on restart reap", rid)
            _write_record(self.work, rid, [proc.pid])
        except BaseException:
            _kill_tree(proc.pid)                      # fail closed: never an untracked engine
            _winjob.close(winjob)
            raise
        if _winjob.WINDOWS:
            with contextlib.suppress(psutil.Error):
                psutil.Process(proc.pid).resume()
        try:
            await self._supervise(rid, job, proc)
        finally:
            _winjob.close(winjob)                     # leftovers of the tree die with the job
            with contextlib.suppress(OSError, ValueError):
                _write_record(self.work, rid, [], exited=True)

    async def _supervise(self, rid: str, job: dict, proc) -> None:
        import psutil
        if job.get("canceled"):                       # MEDIA-CANCEL: cancel raced the spawn — kill at once
            _kill_tree(proc.pid)
            with contextlib.suppress(BaseException):
                await proc.wait()
            job["returncode"] = proc.returncode
            return
        peak = 0
        try:
            ps = psutil.Process(proc.pid)
        except psutil.Error:
            ps = None

        async def sample():
            nonlocal peak
            tree = {proc.pid}
            while proc.returncode is None:
                with contextlib.suppress(psutil.Error, AttributeError):
                    peak = max(peak, ps.memory_info().rss)
                with contextlib.suppress(psutil.Error, AttributeError, OSError, ValueError):
                    now = {proc.pid, *(c.pid for c in ps.children(recursive=True))}
                    if not now <= tree:               # engine spawned children: record them too
                        _write_record(self.work, rid, sorted(tree | now))
                        tree |= now                   # only once durable; else retried next tick
                await asyncio.sleep(0.5)

        deadline = job["started"] + int(self.model.get("deadline_seconds") or 3600)
        sampler = asyncio.create_task(sample())
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    job["timed_out"] = True           # MEDIA-CANCEL: never wait forever
                    _kill_tree(proc.pid)
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 30))
                except asyncio.TimeoutError:
                    if job.get("canceled"):
                        break                          # cancel() already killed the tree
                    continue
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    job["log"] = (job["log"] + [text])[-60:]
            await proc.wait()
        finally:
            sampler.cancel()
            with contextlib.suppress(BaseException):
                await sampler
        job["returncode"] = proc.returncode
        job["peak_rss"] = peak
        job["elapsed_s"] = round(time.time() - job["started"], 2)

    async def status(self, request_id: str) -> ProviderStatus:
        job = self._jobs.get(request_id)
        if job is None:
            raise ValueError("request_id: unknown to this adapter")
        if job["canceled"]:
            return ProviderStatus("canceled")
        task = job["task"]
        if not task.done():
            return ProviderStatus("running")
        if task.exception() is not None:
            return ProviderStatus("failed", "provider_down")
        if job.get("returncode") != 0 or not job["raw"].is_file() or job["raw"].stat().st_size == 0:
            _record_path(self.work, request_id).unlink(missing_ok=True)
            return ProviderStatus("failed", "malformed")
        return ProviderStatus("completed", outputs=(ProviderOutput(f"{request_id}:0"),))

    async def cancel(self, request_id: str) -> None:
        job = self._jobs.get(request_id)
        if job is None:
            return
        job["canceled"] = True
        proc = job.get("proc")
        if proc is not None and proc.returncode is None:
            _kill_tree(proc.pid)                       # MEDIA-CANCEL: engine may spawn children
        task = job.get("task")
        if task is not None:
            with contextlib.suppress(BaseException):
                await task
        for p in (job["raw"], job["init"]):
            if p is not None:
                Path(p).unlink(missing_ok=True)
        _record_path(self.work, request_id).unlink(missing_ok=True)

    async def fetch(self, output: ProviderOutput, dest: Path) -> Fetched:
        rid = output.ref.split(":", 1)[0]
        job = self._jobs.get(rid)
        if job is None or job["canceled"]:
            raise ValueError("output: unknown or canceled")
        dest = Path(dest).resolve()
        if self.root not in dest.parents:
            raise PermissionError("output path escapes media root")
        raw: Path = job["raw"]
        raw_sha = await asyncio.to_thread(_sha256, raw)
        if self.model["surface"] == "video":
            from bcc.video_studio.media import binary, process
            # Container transcode of the model's own frames (VP8 .webm → H.264 .mp4).
            await process([binary("ffmpeg"), "-v", "error", "-y", "-i", str(raw), "-an",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                           "-movflags", "+faststart", str(dest)], timeout=600)
            mime = "video/mp4"
        else:
            shutil.copyfile(raw, dest)
            mime = "image/png"
        data_sha = await asyncio.to_thread(_sha256, dest)
        self.traces[rid] = {
            "engine": "stable-diffusion.cpp", "backend": "vulkan",
            "engine_binary_sha256": await asyncio.to_thread(_sha256, Path(self.cfg["bin"])),
            "engine_release": self.cfg["manifest"].get("engine", {}).get("release"),
            "model_files": {k: {"name": v.name,
                                "expected_sha256": _mf_sha(self.cfg, self.model["id"], k),
                                "observed_sha256": _verified_sha(v, _mf_sha(self.cfg, self.model["id"], k))}
                            for k, v in job["files"].items()},
            "argv": [Path(job["argv"][0]).name] + [a if not os.path.isabs(a) else Path(a).name
                                                   for a in job["argv"][1:]],
            "elapsed_s": job.get("elapsed_s"), "peak_rss_bytes": job.get("peak_rss"),
            "raw_output": {"name": raw.name, "sha256": raw_sha, "bytes": raw.stat().st_size},
            "image_to_video": job["init"] is not None,
            "log_tail": job["log"][-12:],
        }
        with contextlib.suppress(OSError):
            raw.unlink()
            if job["init"] is not None:
                Path(job["init"]).unlink(missing_ok=True)
        _record_path(self.work, rid).unlink(missing_ok=True)
        s = job["settings"]
        return Fetched(dest, dest.stat().st_size, mime, data_sha, s.get("width"), s.get("height"))


_VERIFIED: dict[tuple, str] = {}


def _mf_sha(cfg: dict, model_id: str, role: str) -> str:
    return str(cfg["manifest"]["engines"][ENGINES[model_id]]["files"][role]["sha256"])


def _verified_sha(path: Path, expected: str) -> str:
    """Observed sha256 must equal the manifest sha256. Cached by (path,size,mtime)
    so a health/submit call does not re-hash gigabytes once a file is verified."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    cached = _VERIFIED.get(key)
    if cached is not None:
        if cached != expected:
            raise ValueError(f"{path.name}: sha256 does not match manifest — corrupted or wrong revision")
        return cached
    observed = _sha256(path)
    if observed != expected:
        raise ValueError(f"{path.name}: sha256 {observed[:12]}… does not match manifest "
                         f"{expected[:12]}… — corrupted or wrong revision")
    _VERIFIED[key] = observed
    return observed


def _kill_tree(pid: int) -> None:
    import psutil
    with contextlib.suppress(psutil.Error, ProcessLookupError):
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
        for pr in procs:
            with contextlib.suppress(psutil.Error, ProcessLookupError):
                pr.kill()
        psutil.wait_procs(procs, timeout=5)


# ---- MEDIA-RESTART: durable engine records + restart reap -------------------------

_SELF: dict = {}


def _identity(pid: int) -> dict | None:
    import psutil
    try:
        return {"pid": int(pid), "ctime": psutil.Process(int(pid)).create_time()}
    except (psutil.Error, ValueError, OverflowError):
        return None


def _owner() -> dict:
    """This backend's identity (pid + process create_time)."""
    if _SELF.get("pid") != os.getpid():               # recompute after fork
        _SELF.clear()
        _SELF.update(_identity(os.getpid()) or {"pid": os.getpid(), "ctime": None})
    return dict(_SELF)


def _matches(ident):
    """The live psutil.Process for a recorded {pid, ctime} — only if BOTH still match."""
    import psutil
    try:
        pid, ctime = int(ident["pid"]), float(ident["ctime"])
        p = psutil.Process(pid)
        if abs(p.create_time() - ctime) > 0.01:        # PID reused by an unrelated process
            return None
        return p if p.is_running() else None
    except (psutil.Error, KeyError, TypeError, ValueError, OverflowError):
        return None


def _record_path(work: Path, rid: str) -> Path:
    return Path(work) / f"{rid}{RECORD_SUFFIX}"


def _write_record(work: Path, rid: str, pids: list[int], exited: bool = False) -> None:
    """Atomically (re)write the durable record of engine job `rid`; recorded
    processes are kept, new pids are added with their create_time."""
    work = Path(work)
    path = _record_path(work, rid)
    known = []
    if path.is_file():
        with contextlib.suppress(OSError, ValueError, AttributeError):
            known = list(json.loads(path.read_text(encoding="utf-8")).get("procs") or [])
    seen = {(k.get("pid"), k.get("ctime")) for k in known}
    for pid in pids:
        ident = _identity(pid)
        if ident and (ident["pid"], ident["ctime"]) not in seen:
            known.append(ident)
            seen.add((ident["pid"], ident["ctime"]))
    record = {"v": 1, "rid": rid, "root": str(work.parent), "owner": _owner(),
              "procs": known, "exited": exited, "written": time.time()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                       # Windows: a reader holds the target open
            if attempt == 4:
                raise
            time.sleep(0.05)


def reap_orphans(storage_root: Path) -> list[dict]:
    """Stop engines left behind by a DEAD backend of THIS storage root.

    Only records in `<storage_root>/engine-work` naming this root are considered,
    only when their owner backend (pid + create_time) is gone — a live backend
    (including this one: dispatch builds a provider per job) keeps its engines —
    and only processes whose pid AND create_time match the record are killed,
    together with their live descendants. The orphan's output and start frame are
    deleted: a late orphan output is never accepted as a generation.
    """
    import psutil
    root = Path(storage_root).resolve()
    work = root / "engine-work"
    reaped: list[dict] = []
    if not work.is_dir():
        return reaped
    for path in sorted(work.glob(f"*{RECORD_SUFFIX}")):
        rid = path.name[: -len(RECORD_SUFFIX)]
        if not _RID.match(rid):
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            same_root = Path(str(rec.get("root", ""))).resolve() == root
        except (OSError, ValueError, AttributeError):
            continue                                  # unreadable/foreign: leave it alone
        if rec.get("rid") != rid or not same_root:
            continue                                  # not a record of this storage root
        if _matches(rec.get("owner") or {}) is not None:
            continue                                  # its backend is alive and observing it
        victims: dict[int, object] = {}
        for ident in rec.get("procs") or []:
            p = _matches(ident)
            if p is None:
                continue                              # gone, or PID now belongs to someone else
            victims[p.pid] = p
            with contextlib.suppress(psutil.Error):
                for c in p.children(recursive=True):
                    victims.setdefault(c.pid, c)
        for p in victims.values():
            with contextlib.suppress(psutil.Error):
                p.kill()
        alive = []
        if victims:
            _, alive = psutil.wait_procs(list(victims.values()), timeout=5)
        removed = []
        for f in [work / f"{rid}.png", work / f"{rid}.webm", *work.glob(f"{rid}-start.*")]:
            with contextlib.suppress(OSError):
                if f.is_file():
                    f.unlink()
                    removed.append(f.name)
        entry = {"rid": rid, "killed": sorted(victims), "survivors": sorted(p.pid for p in alive),
                 "removed_outputs": removed}
        if alive:                                     # keep the record: retried on next start
            log.error("sdcpp restart reap: orphan engine %s could not be stopped: %s", rid, entry)
        else:
            path.unlink(missing_ok=True)
            log.warning("sdcpp restart reap: orphan of a dead backend handled: %s", entry)
        reaped.append(entry)
    return reaped


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
