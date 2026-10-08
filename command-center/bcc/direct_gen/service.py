"""Direct Generation job service: lifecycle, STOP, retry, DIRECT/ASSISTED, events.

One job at a time on the GPU (a FIFO lock), because heavy stages must not run
concurrently. Jobs are asyncio tasks of this process; STOP cancels the task and
interrupts exactly that ComfyUI prompt. A job that was live when the process
died is marked failed/"interrupted" on the next start, never silently resumed.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..single_flight import await_shared
from . import catalog, sdcli
from .client import ComfyUIVideoClient, classify_error, validate_template
from .prompt_vault import PromptVault
from .store import JobStore, valid_participant

TERMINAL = ("completed", "failed", "cancelled")
LIVE = ("queued", "loading", "generating", "postprocessing")
PLACEHOLDER = re.compile(r"^\{\{([a-z_]+)\}\}$")
KNOWN = {"prompt", "negative", "seed", "width", "height", "frames", "fps", "duration", "image", "audio"}
MAX_PROMPT = 8000
MAX_INPUT_BYTES = 32 * 1024 * 1024
ASSIST_SYSTEM = (
    "You edit text-to-video prompts. Rewrite the user's text as one clearer, concrete shot "
    "description (subject, setting, camera, light, motion). Keep every element the user "
    "specified; do not add, remove, soften or censor anything. Reply with the rewritten "
    "prompt only.")


class DirectGenError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    @property
    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def placeholders(template: Any) -> set[str]:
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            m = PLACEHOLDER.fullmatch(v)
            if m:
                found.add(m.group(1))
    walk(template)
    return found


def fill_template(template: dict, values: dict[str, Any]) -> dict:
    """Replace whole-string `{{name}}` values once; the prompt is inserted verbatim."""
    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            m = PLACEHOLDER.fullmatch(v)
            if m and m.group(1) in values:
                return values[m.group(1)]
        return v
    return walk(copy.deepcopy(template))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_verify(path: Path) -> dict:
    """ffprobe the saved file. Unverified (no ffprobe) is reported as such, not as success."""
    exe = shutil.which("ffprobe")
    if not exe:
        return {"verified": False, "reason": "ffprobe not available; file not independently checked"}
    try:
        out = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                              str(path)], capture_output=True, text=True, timeout=60, check=False)
        info = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"verified": False, "reason": f"ffprobe failed: {type(exc).__name__}"}
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if out.returncode != 0 or video is None or duration <= 0:
        return {"verified": False, "playable": False, "reason": "ffprobe found no playable video stream"}
    return {"verified": True, "duration_s": round(duration, 3), "codec": video.get("codec_name"),
            "width": video.get("width"), "height": video.get("height")}


class DirectGenService:
    def __init__(self, svc: Any, *, client: Any = None, models_dir: Path | None = None,
                 workflows_dir: Path | None = None, poll_seconds: float = 1.0,
                 verify: Callable[[Path], dict] | None = None, timeout_seconds: float = 3 * 3600,
                 media_dir: Path | None = None, sd_bin: Path | None = None,
                 sd_runner: Callable[[], Any] | None = None,
                 free_memory: Callable[[], int | None] | None = None,
                 foreign_engines: Callable[[], list[int]] | None = None):
        self.svc = svc
        self.data_dir = Path(svc.settings.data_dir) / "direct-gen"
        self.store = JobStore(Path(svc.settings.data_dir))
        self.prompt_vault = PromptVault(Path(svc.settings.data_dir))
        self.models_dir = Path(models_dir) if models_dir else catalog.default_models_dir()
        self.workflows_dir = Path(workflows_dir) if workflows_dir else self.data_dir / "workflows"
        self._client = client
        self.poll_seconds = poll_seconds
        self.verify = verify or default_verify
        self.timeout_seconds = timeout_seconds
        self.media_dir = Path(media_dir) if media_dir else sdcli.default_media_root()
        self.sd_bin = Path(sd_bin) if sd_bin else sdcli.default_binary()
        self._sd_runner = sd_runner or sdcli.SdCliRunner
        self._free_memory = free_memory or sdcli.free_memory_bytes
        self._foreign = foreign_engines or sdcli.foreign_engines
        self._tasks: dict[str, asyncio.Task] = {}
        self._waiting: list[str] = []
        self._running: str | None = None
        self._stopping: set[str] = set()
        self._lock: asyncio.Lock | None = None
        self.qwen_lookup: Callable[[], Awaitable[tuple[Any, str] | None]] = self._default_qwen
        self._recover()

    # ---------- plumbing ----------

    @property
    def client(self):
        if self._client is None:
            self._client = ComfyUIVideoClient(os.environ.get("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188"))
        return self._client

    def active_tasks(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def _recover(self) -> None:
        for job in self.store.all_unfinished():
            job["status"], job["stage"] = "failed", "failed"
            job["error"] = {"code": "interrupted", "message": "Bossman restarted while this job was running; the run was not resumed."}
            job["finished_at"] = now_iso()
            job["timeline"].append({"stage": "failed", "at": job["finished_at"], "note": "interrupted"})
            self.store.save(job)

    async def _emit(self, job: dict) -> None:
        with contextlib.suppress(Exception):
            await self.svc.bus.emit(
                "direct_gen.job", job_id=job["job_id"], participant=job["participant"][:12],
                status=job["status"], stage=job["stage"], progress=job["progress"],
                elapsed_s=self._elapsed(job), model=job["model"],
                error_code=(job.get("error") or {}).get("code"))

    @staticmethod
    def _elapsed(job: dict) -> float:
        if not job.get("started_at"):
            return 0.0
        end = datetime.fromisoformat(job["finished_at"]) if job.get("finished_at") else datetime.now(timezone.utc)
        return round((end - datetime.fromisoformat(job["started_at"])).total_seconds(), 2)

    async def _set(self, job: dict, stage: str, *, status: str | None = None, note: str = "") -> None:
        job["stage"] = stage
        job["status"] = status or stage
        job["timeline"].append({"stage": stage, "at": now_iso(), **({"note": note} if note else {})})
        self.store.save(job)
        await self._emit(job)

    def view(self, job: dict) -> dict:
        out = dict(job)
        out["elapsed_s"] = self._elapsed(job)
        if job["status"] == "queued":
            ahead = self._waiting.index(job["job_id"]) if job["job_id"] in self._waiting else 0
            out["queue_position"] = ahead + (1 if self._running else 0)
        return out

    # ---------- catalogue / status ----------

    def models(self) -> list[dict]:
        over = catalog.overrides(self.data_dir)
        return sdcli.list_models(self.media_dir, self.sd_bin, over) \
            + catalog.list_models(self.models_dir, self.workflows_dir, self.data_dir)

    async def status(self) -> dict:
        runtime: dict[str, Any] = {"name": "comfyui"}
        try:
            await asyncio.wait_for(self.client.health(), 5)
            runtime.update(reachable=True, reason="")
        except Exception as exc:  # noqa: BLE001 - shown to the user as-is
            runtime.update(reachable=False, reason=f"{type(exc).__name__}: {str(exc)[:200] or 'no answer'}")
        memory: dict[str, Any] = {"measured": False}
        with contextlib.suppress(Exception):
            live = await self.svc.metrics.read_async()
            if live.get("ram_total_mb") is not None and live.get("ram_used_mb") is not None:
                memory = {"measured": True, "total_mb": live["ram_total_mb"], "used_mb": live["ram_used_mb"],
                          "free_mb": live["ram_total_mb"] - live["ram_used_mb"]}
        return {"runtime": runtime, "sd_cli": {"present": self.sd_bin.is_file()}, "memory": memory, "queue": {"running": self._running, "waiting": len(self._waiting)},
                "assist": await self.assist_status()}

    async def _default_qwen(self) -> tuple[Any, str] | None:
        registry = getattr(self.svc, "registry", None)
        if registry is None:
            return None
        for model in await registry.list_models():
            name = f"{model.get('name', '')} {model.get('alias', '')}".lower()
            if "qwen" in name and model.get("status") not in ("offline", "error"):
                adapter, row = await registry.adapter_for(model["id"])
                return adapter, row["name"]
        return None

    async def assist_status(self) -> dict:
        try:
            found = await self.qwen_lookup()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"Qwen lookup failed: {type(exc).__name__}"}
        if not found:
            return {"available": False, "reason": "no usable Qwen model in the model registry"}
        return {"available": True, "model": found[1], "reason": ""}

    # ---------- ASSISTED ----------

    async def assist(self, participant: str, prompt: str) -> dict:
        participant = valid_participant(participant)
        if not prompt.strip() or len(prompt) > MAX_PROMPT:
            raise DirectGenError(422, "invalid_prompt", "prompt must be 1..8000 characters")
        found = await self.qwen_lookup()
        if not found:
            raise DirectGenError(409, "assist_unavailable", "ASSISTED needs a local Qwen model; none is usable in the registry")
        adapter, name = found
        try:
            result = await asyncio.wait_for(adapter.chat(name, [
                {"role": "system", "content": ASSIST_SYSTEM}, {"role": "user", "content": prompt}]), 180)
        except Exception as exc:  # noqa: BLE001
            raise DirectGenError(502, "assist_failed", f"Qwen did not answer: {type(exc).__name__}: {str(exc)[:200]}") from None
        suggested = (result.text or "").strip()
        if not suggested:
            raise DirectGenError(502, "assist_failed", "Qwen returned an empty edit")
        record = {"assist_id": uuid.uuid4().hex, "participant": participant, "model": name,
                  "original": prompt, "suggested": suggested, "created_at": now_iso()}
        self.store.save_assist(participant, record)
        return {"assist_id": record["assist_id"], "original": prompt, "suggested": suggested,
                "model": name, "applied": False}

    # ---------- create / retry ----------

    def _text_fields(self, participant: str, body: dict) -> tuple[str, str, str, str, str | None]:
        """mode, raw prompt, negative, effective prompt, assist model. The text is never edited here."""
        mode = body.get("mode", "DIRECT")
        if mode not in ("DIRECT", "ASSISTED"):
            raise DirectGenError(422, "invalid_mode", "mode must be DIRECT or ASSISTED")
        raw = body.get("prompt")
        if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_PROMPT:
            raise DirectGenError(422, "invalid_prompt", "prompt must be 1..8000 characters")
        negative = body.get("negative") or ""
        if not isinstance(negative, str) or len(negative) > MAX_PROMPT:
            raise DirectGenError(422, "invalid_negative", "negative prompt too long")
        effective, assist_model = raw, None
        if mode == "ASSISTED":
            try:
                rec = self.store.load_assist(participant, str(body.get("assist_id", "")))
            except KeyError:
                raise DirectGenError(404, "unknown_assist", "assist suggestion not found") from None
            choice = body.get("assist_choice")
            if choice not in ("original", "suggested"):
                raise DirectGenError(422, "assist_choice_required", "choose 'original' or 'suggested' explicitly")
            if raw != rec["original"]:
                raise DirectGenError(422, "assist_mismatch", "prompt differs from the assisted original")
            effective = rec["suggested"] if choice == "suggested" else rec["original"]
            assist_model = rec["model"]
        return mode, raw, negative, effective, assist_model

    @staticmethod
    def _seed(body: dict) -> int:
        seed = body.get("seed")
        if seed is None:
            seed = secrets.randbelow(2**31)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
            raise DirectGenError(422, "invalid_seed", "seed must be a non-negative integer")
        return seed

    async def create(self, participant: str, body: dict) -> dict:
        participant = valid_participant(participant)
        if body.get("model") in sdcli.BY_ID:
            return await self._create_sd(participant, body)
        spec = catalog.BY_ID.get(body.get("model"))
        if spec is None:
            raise DirectGenError(404, "unknown_model", "no such model")
        info = catalog.describe(spec, self.models_dir, self.workflows_dir, None)
        if not info["available"]:
            raise DirectGenError(409, "model_unavailable", info["reason"])
        mode, raw, negative, effective, assist_model = self._text_fields(participant, body)
        duration = body.get("duration", 4)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= spec.max_seconds:
            raise DirectGenError(422, "invalid_duration", f"duration must be >0 and <= {spec.max_seconds} s for this model")
        try:
            width, height = catalog.parse_resolution(spec, body.get("resolution", "480x320"))
        except ValueError as exc:
            raise DirectGenError(422, "invalid_resolution", str(exc)) from None
        seed = self._seed(body)
        try:
            template = json.loads(catalog.template_path(self.workflows_dir, spec.id).read_text(encoding="utf-8"))
            validate_template(template)
        except (OSError, ValueError) as exc:
            raise DirectGenError(409, "model_unavailable", f"workflow template is unusable: {exc}") from None
        needs = placeholders(template)
        if needs - KNOWN:
            raise DirectGenError(409, "model_unavailable", "workflow template uses unknown placeholders: " + ", ".join(sorted(needs - KNOWN)))
        inputs: dict[str, bytes] = {}
        for field, key in (("image", "image_b64"), ("audio", "audio_b64")):
            data = self._decode(body.get(key), field)
            if data is None and getattr(spec, f"requires_{field}"):
                raise DirectGenError(422, f"{field}_required", f"{spec.label} needs a {field} input")
            if data is not None and field not in needs:
                raise DirectGenError(422, f"{field}_unsupported", f"{spec.label} workflow takes no {field} input")
            if data is None and field in needs:
                raise DirectGenError(422, f"{field}_required", f"the workflow of {spec.label} needs a {field} input")
            if data is not None:
                inputs[field] = data
        try:
            await asyncio.wait_for(self.client.health(), 5)
        except Exception as exc:  # noqa: BLE001
            raise DirectGenError(409, "runtime_unreachable",
                                 f"ComfyUI is not reachable ({type(exc).__name__}); start it and retry") from None
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id, "participant": participant, "model": spec.id, "kind": "video", "mode": mode,
            "status": "queued", "stage": "queued", "created_at": now_iso(), "started_at": None, "finished_at": None,
            "raw_prompt": raw, "effective_prompt": effective, "negative": negative,
            "params": {"duration": duration, "width": width, "height": height, "seed": seed, "fps": spec.fps,
                       "frames": catalog.frames_for(spec, duration)},
            "assist": {"assist_id": body.get("assist_id"), "choice": body.get("assist_choice")} if mode == "ASSISTED" else None,
            "progress": {"kind": "none"}, "result": None, "error": None, "retry_of": body.get("retry_of"),
            "provenance": {"model_id": spec.id, "runtime": "comfyui", "weights": info["weights"],
                           "source": info["source"], "revision": info["revision"], "license": info["license"],
                           "workflow_template": f"{spec.id}.json", "assist_model": assist_model},
            "inputs": {k: len(v) for k, v in inputs.items()},
            "timeline": [{"stage": "queued", "at": now_iso()}],
        }
        folder = self.store.job_dir(participant, job_id)
        folder.mkdir(parents=True, exist_ok=True)
        for k, data in inputs.items():
            (folder / f"input_{k}.bin").write_bytes(data)
        self.prompt_vault.record(job)
        self.store.save(job)
        self._waiting.append(job_id)
        self._tasks[job_id] = asyncio.create_task(self._run(job, template, inputs), name=f"direct-gen-{job_id[:8]}")
        await self._emit(job)
        return self.view(job)

    async def _create_sd(self, participant: str, body: dict) -> dict:
        """Photo/video through sd-cli: exactly the chosen model, the prompt verbatim, no fallback."""
        spec = sdcli.BY_ID[body["model"]]
        info = sdcli.describe(spec, self.media_dir, self.sd_bin, catalog.overrides(self.data_dir))
        if not info["available"]:
            raise DirectGenError(409, "model_unavailable", info["reason"])
        mode, raw, negative, effective, assist_model = self._text_fields(participant, body)
        try:
            width, height = catalog.parse_resolution(spec, body.get("resolution") or spec.default_resolution)
        except ValueError as exc:
            raise DirectGenError(422, "invalid_resolution", str(exc)) from None
        steps = body.get("steps") or spec.default_steps
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= spec.max_steps:
            raise DirectGenError(422, "invalid_steps", f"steps must be 1..{spec.max_steps} for this model")
        duration, frames = 0, 1
        if spec.kind == "video":
            duration = body.get("duration", 1)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= spec.max_seconds:
                raise DirectGenError(422, "invalid_duration", f"duration must be >0 and <= {spec.max_seconds} s for this model")
            frames = sdcli.frames_for(spec, duration)
        seed = self._seed(body)
        if self._decode(body.get("audio_b64"), "audio") is not None:
            raise DirectGenError(422, "audio_unsupported", f"{spec.label} takes no audio input")
        image = self._decode(body.get("image_b64"), "image")
        if image is None and spec.requires_image:
            raise DirectGenError(422, "image_required", f"{spec.label} needs a reference image")
        if image is not None and not info["accepts_image"]:
            raise DirectGenError(422, "image_unsupported", f"{spec.label} takes no image input")
        if image is not None and sdcli.image_extension(image) is None:
            raise DirectGenError(422, "image_invalid", "image must be PNG or JPEG")
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id, "participant": participant, "model": spec.id, "kind": spec.kind, "mode": mode,
            "status": "queued", "stage": "queued", "created_at": now_iso(), "started_at": None, "finished_at": None,
            "raw_prompt": raw, "effective_prompt": effective, "negative": negative,
            "params": {"duration": duration, "width": width, "height": height, "seed": seed, "steps": steps,
                       "fps": spec.fps, "frames": frames},
            "assist": {"assist_id": body.get("assist_id"), "choice": body.get("assist_choice")} if mode == "ASSISTED" else None,
            "progress": {"kind": "none"}, "result": None, "error": None, "retry_of": body.get("retry_of"),
            "provenance": {"model_id": spec.id, "runtime": "sdcpp", "weights": info["weights"],
                           "source": info["source"], "revision": info["revision"], "license": info["license"],
                           "assist_model": assist_model},
            "inputs": {"image": len(image)} if image else {},
            "timeline": [{"stage": "queued", "at": now_iso()}],
        }
        folder = self.store.job_dir(participant, job_id)
        folder.mkdir(parents=True, exist_ok=True)
        if image:
            (folder / "input_image.bin").write_bytes(image)
        self.prompt_vault.record(job)
        self.store.save(job)
        self._waiting.append(job_id)
        self._tasks[job_id] = asyncio.create_task(self._run_sd(job, spec, image), name=f"direct-gen-{job_id[:8]}")
        await self._emit(job)
        return self.view(job)

    def _preflight(self, spec: sdcli.SdSpec) -> None:
        busy = self._foreign()
        if busy:
            raise DirectGenError(0, "gpu_busy", f"another sd-cli process (PID {busy[0]}) already holds the GPU; nothing was started")
        need, free = sdcli.required_bytes(spec, self.media_dir), self._free_memory()
        if free is not None and free < need:
            raise DirectGenError(0, "insufficient_memory",
                                 f"{spec.label} needs about {need / sdcli.GIB:.0f} GiB of free memory, "
                                 f"{free / sdcli.GIB:.1f} GiB is free; nothing was started")

    async def _run_sd(self, job: dict, spec: sdcli.SdSpec, image: bytes | None) -> None:
        job_id, runner = job["job_id"], self._sd_runner()
        try:
            async with self._gate():
                if job_id in self._waiting:
                    self._waiting.remove(job_id)
                self._running = job_id
                job["started_at"] = now_iso()
                await self._set(job, "loading")
                await asyncio.to_thread(self._preflight, spec)
                folder = self.store.job_dir(job["participant"], job_id)
                work, result = folder / "work", folder / "result"
                work.mkdir(exist_ok=True)
                result.mkdir(exist_ok=True)
                out = result / ("video.webm" if spec.kind == "video" else "image.png")
                init = None
                if image:
                    init = work / f"input{sdcli.image_extension(image)}"
                    init.write_bytes(image)
                p = job["params"]
                argv = sdcli.build_argv(spec, self.sd_bin, self.media_dir, p, job["effective_prompt"],
                                        job["negative"], out, init)
                job["provenance"]["command"] = [Path(a).name if os.path.isabs(a) else a for a in argv]
                job["provenance"]["command"][job["provenance"]["command"].index("-p") + 1] = "<prompt>"

                async def on_progress(value: int, maximum: int) -> None:
                    prev = job["progress"]
                    job["progress"] = {"kind": "steps", "value": value, "max": maximum,
                                       "percent": round(100.0 * value / maximum, 1)}
                    if job["stage"] == "loading":
                        await self._set(job, "generating")
                    elif prev.get("value") != value:
                        self.store.save(job)
                        await self._emit(job)

                run = await runner.run(argv, cwd=work, on_progress=on_progress)
                shutil.rmtree(work, ignore_errors=True)
                job["provenance"].update(engine_returncode=run.returncode, engine_seconds=run.elapsed_s,
                                         peak_rss_mb=round(run.peak_rss / 1048576))
                if run.returncode != 0:
                    tail = " | ".join(run.log[-4:])[:600]
                    raise DirectGenError(0, classify_error(tail) if classify_error(tail) == "out_of_memory" else "engine_error",
                                         f"sd-cli exited with code {run.returncode}: {tail}")
                await self._set(job, "postprocessing")
                await self._collect_sd(job, spec, out)
                job["finished_at"] = now_iso()
                await self._set(job, "completed")
        except asyncio.CancelledError:
            runner.kill()
            if job_id in self._stopping:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(runner.proc.wait(), 15) if runner.proc else None
                job["finished_at"] = now_iso()
                shutil.rmtree(self.store.job_dir(job["participant"], job_id) / "result", ignore_errors=True)
                shutil.rmtree(self.store.job_dir(job["participant"], job_id) / "work", ignore_errors=True)
                await self._set(job, "cancelled")
            raise
        except DirectGenError as exc:
            await self._fail(job, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - reported honestly below
            await self._fail(job, "runtime_error", f"{type(exc).__name__}: {str(exc)[:600]}")
        finally:
            if job_id in self._waiting:
                self._waiting.remove(job_id)
            if self._running == job_id:
                self._running = None
            self._stopping.discard(job_id)

    async def _collect_sd(self, job: dict, spec: sdcli.SdSpec, out: Path) -> None:
        if not out.is_file() or out.stat().st_size <= 0:
            raise DirectGenError(0, "empty_output", "sd-cli finished but wrote no file")
        extra: dict[str, Any] = {}
        if spec.kind == "photo":
            size = sdcli.png_size(out)
            if size is None:
                raise DirectGenError(0, "bad_output", "output is not a PNG image")
            extra = {"verified": True, "width": size[0], "height": size[1]}
            mime = "image/png"
        else:
            check = await asyncio.to_thread(self.verify, out)
            if check.get("playable") is False:
                raise DirectGenError(0, "bad_output", check.get("reason", "output is not a playable video"))
            extra = {k: v for k, v in check.items() if k in ("verified", "reason", "duration_s", "codec", "width", "height")}
            mime = "video/webm"
        job["result"] = {"file": out.name, "bytes": out.stat().st_size, "sha256": _sha256_file(out), "mime": mime, **extra}

    @staticmethod
    def _decode(value: Any, field: str) -> bytes | None:
        if value in (None, ""):
            return None
        try:
            data = base64.b64decode(str(value).split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise DirectGenError(422, f"{field}_invalid", f"{field} must be base64") from None
        if not data or len(data) > MAX_INPUT_BYTES:
            raise DirectGenError(422, f"{field}_invalid", f"{field} must be 1 byte..32 MiB")
        return data

    async def retry(self, participant: str, job_id: str) -> dict:
        old = self.get(participant, job_id)
        body = {"model": old["model"], "mode": "DIRECT" if old["mode"] == "DIRECT" else "ASSISTED",
                "prompt": old["raw_prompt"], "negative": old["negative"], "duration": old["params"]["duration"],
                "resolution": f'{old["params"]["width"]}x{old["params"]["height"]}',
                "seed": old["params"]["seed"], "steps": old["params"].get("steps"), "retry_of": job_id}
        if old["mode"] == "ASSISTED" and old.get("assist"):
            body.update(assist_id=old["assist"]["assist_id"], assist_choice=old["assist"]["choice"])
        folder = self.store.job_dir(old["participant"], job_id)
        for field in ("image", "audio"):
            p = folder / f"input_{field}.bin"
            if p.is_file():
                body[f"{field}_b64"] = base64.b64encode(p.read_bytes()).decode()
        return await self.create(participant, body)

    # ---------- read ----------

    def get(self, participant: str, job_id: str) -> dict:
        try:
            return self.view(self.store.load(valid_participant(participant), job_id))
        except KeyError:
            raise DirectGenError(404, "unknown_job", "job not found") from None

    def list(self, participant: str) -> list[dict]:
        return [self.view(j) for j in self.store.list(valid_participant(participant))]

    def result_path(self, participant: str, job_id: str) -> Path:
        job = self.get(participant, job_id)
        if job["status"] != "completed" or not job.get("result"):
            raise DirectGenError(409, "no_result", "job has no finished result")
        path = self.store.job_dir(job["participant"], job_id) / "result" / job["result"]["file"]
        if not path.is_file():
            raise DirectGenError(404, "result_missing", "result file is gone from storage")
        return path

    # ---------- STOP ----------

    async def cancel(self, participant: str, job_id: str) -> dict:
        job = self.get(participant, job_id)  # 404 for other participants
        task = self._tasks.get(job_id)
        if job["status"] in TERMINAL or task is None or task.done():
            raise DirectGenError(409, "already_finished", f"job is already {job['status']}")
        self._stopping.add(job_id)
        task.cancel()
        await asyncio.wait({task}, timeout=30)
        return self.get(participant, job_id)

    # ---------- execution ----------

    def _gate(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(self, job: dict, template: dict, inputs: dict[str, bytes]) -> None:
        prompt_id: str | None = None
        job_id = job["job_id"]
        try:
            async with self._gate():
                if job_id in self._waiting:
                    self._waiting.remove(job_id)
                self._running = job_id
                job["started_at"] = now_iso()
                await self._set(job, "loading")
                # The submission outlives a STOP that lands mid-request: the job must still learn the
                # prompt id, or ComfyUI would keep running with nobody left to interrupt it.
                # await_shared, not asyncio.shield: see bcc/single_flight.py (Python 3.14 shield callback).
                submission = asyncio.ensure_future(self._submit(job, template, inputs))
                try:
                    prompt_id = await await_shared(submission)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        prompt_id = await asyncio.wait_for(await_shared(submission), 30)
                    raise
                job["provenance"]["comfy_prompt_id"] = prompt_id
                self.store.save(job)
                outputs = await self._watch(job, prompt_id)
                await self._set(job, "postprocessing")
                await self._collect(job, outputs)
                job["finished_at"] = now_iso()
                await self._set(job, "completed")
        except asyncio.CancelledError:
            if job_id in self._stopping:
                await self._stop_cleanup(job, prompt_id)
            elif prompt_id:  # process shutdown: do not leave the GPU job behind, keep the record as it was
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.client.interrupt(prompt_id), 10)
            raise
        except DirectGenError as exc:
            await self._fail(job, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - reported honestly below
            await self._fail(job, "runtime_error", f"{type(exc).__name__}: {str(exc)[:600]}")
        finally:
            if job_id in self._waiting:
                self._waiting.remove(job_id)
            if self._running == job_id:
                self._running = None
            self._stopping.discard(job_id)

    async def _stop_cleanup(self, job: dict, prompt_id: str | None) -> None:
        note = ""
        if prompt_id:
            try:
                await asyncio.wait_for(self.client.interrupt(prompt_id), 15)
            except Exception as exc:  # noqa: BLE001
                note = f"interrupt request failed: {type(exc).__name__}: {str(exc)[:200]}"
                job["warning"] = note
        job["finished_at"] = now_iso()
        part = self.store.job_dir(job["participant"], job["job_id"]) / "result"
        shutil.rmtree(part, ignore_errors=True)
        await self._set(job, "cancelled", note=note)

    async def _fail(self, job: dict, code: str, message: str) -> None:
        job["error"] = {"code": code, "message": message}
        job["finished_at"] = now_iso()
        await self._set(job, "failed", note=code)

    async def _submit(self, job: dict, template: dict, inputs: dict[str, bytes]) -> str:
        p = job["params"]
        values: dict[str, Any] = {
            "prompt": job["effective_prompt"], "negative": job["negative"], "seed": p["seed"],
            "width": p["width"], "height": p["height"], "frames": p["frames"], "fps": p["fps"], "duration": p["duration"]}
        for field, data in inputs.items():
            values[field] = await self.client.upload(f"bossman_{job['job_id'][:12]}_{field}.bin", data)
        workflow = fill_template(template, values)
        job["provenance"]["workflow_sha256"] = hashlib.sha256(
            json.dumps(workflow, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            return await self.client.submit_video(workflow, job["job_id"])
        except ValueError as exc:
            raise DirectGenError(0, "rejected", str(exc)) from None

    async def _watch(self, job: dict, prompt_id: str) -> list[dict]:
        deadline = time.monotonic() + self.timeout_seconds
        last: tuple = ()
        while True:
            state = await self.client.poll(prompt_id)
            step = await self.client.progress(prompt_id)
            if step and step.get("max"):
                job["progress"] = {"kind": "steps", "value": step["value"], "max": step["max"],
                                   "percent": round(100.0 * step["value"] / step["max"], 1)}
            if state["state"] == "failed":
                text = state.get("error") or "ComfyUI reported a failure"
                raise DirectGenError(0, classify_error(text), text)
            if state["state"] == "completed":
                return state["outputs"]
            if (step or state["state"] == "running") and job["stage"] == "loading":
                await self._set(job, "generating")
            snapshot = (job["stage"], json.dumps(job["progress"], sort_keys=True))
            if snapshot != last:
                last = snapshot
                self.store.save(job)
                await self._emit(job)
            if time.monotonic() > deadline:
                with contextlib.suppress(Exception):
                    await self.client.interrupt(prompt_id)
                raise DirectGenError(0, "timeout", f"no result after {int(self.timeout_seconds)} s; the run was interrupted")
            await asyncio.sleep(self.poll_seconds)

    async def _collect(self, job: dict, outputs: list[dict]) -> None:
        descriptor = outputs[0]
        folder = self.store.job_dir(job["participant"], job["job_id"]) / "result"
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(descriptor["filename"]).suffix.lower() or ".mp4"
        dest = folder / f"video{suffix}"
        size = await self.client.download(descriptor, dest)
        if size <= 0 or not dest.is_file() or dest.stat().st_size <= 0:
            raise DirectGenError(0, "empty_output", "ComfyUI produced an empty video file")
        check = await asyncio.to_thread(self.verify, dest)
        if check.get("playable") is False:
            raise DirectGenError(0, "bad_output", check.get("reason", "output is not a playable video"))
        job["result"] = {"file": dest.name, "bytes": dest.stat().st_size, "sha256": _sha256_file(dest),
                         "mime": {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
                                  ".mov": "video/quicktime", ".webp": "image/webp", ".gif": "image/gif"}.get(suffix, "application/octet-stream"),
                         **{k: v for k, v in check.items() if k in ("verified", "reason", "duration_s", "codec", "width", "height")}}
