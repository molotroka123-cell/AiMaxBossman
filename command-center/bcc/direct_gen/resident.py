"""Resident diffusion engine for the Direct window (OPT-IN, default OFF).

Every photo job normally starts a fresh ``sd-cli`` process, so the weights are read and moved to the GPU again for
each queued job (32-1500 s of "loading" per job on this machine). With ``BOSSMAN_DIRECT_GEN_RESIDENT=1`` the
engine keeps one ``sd-server`` (stable-diffusion.cpp) alive while consecutive jobs need the SAME weights/flags,
restarts it only when the next job needs different ones, and kills it after an idle period so the PC is free again.

The contract with the rest of the code is the existing ``build_argv`` list: it is parsed into
  * startup flags  (model files, cfg, sampler, offload ...)  -> identity of the loaded engine,
  * per-job values (prompt, size, steps, seed, -r references, -o output) -> one HTTP request.
Anything the server path does not map one-to-one (video, init image/mask, LoRA, unknown flags, a missing or
too-old server binary, a server that fails to start) falls back to the unchanged ``sd-cli`` runner, and the
runner records which engine really ran (``engine``) and why (``fallback_reason``).

Server API used (stable-diffusion.cpp ``sd-server``, verified in its docs/source at master-975-95b3090):
  GET  /sdcpp/v1/capabilities         readiness probe
  POST /sdcpp/v1/img_gen              async job, returns {"id": ...}; fields omitted fall back to the startup flags
  GET  /sdcpp/v1/jobs/{id}            status queued|generating|completed|failed|cancelled, result.images[0].b64_json
  POST /sdcpp/v1/jobs/{id}/cancel     only queued jobs (``cancel_generating`` is false) -> STOP kills the server
There is no authentication; the server binds 127.0.0.1 on a random free port and dies with the backend (Job Object).

Modes (``BOSSMAN_DIRECT_GEN_RESIDENT``; owner 19.10: keep the model loaded between same-model jobs of a queue):
  ``1``/``on``     the server is used for every photo job (the old opt-in behaviour, idle unload 120 s);
  ``auto``         queue-run only: the server is started when the NEXT queued job needs the same weights/flags, kept
                   while that holds, and released as soon as the next job differs (or after 60 s idle). A single job
                   keeps the proven per-job sd-cli path;
  unset (default)  ``auto`` only for models that passed the live parity benchmark (``resident-proof.json`` in the
                   direct-gen data folder, written by ``tools/direct_gen_bench/warm_queue_bench.py`` for this exact
                   sd-server binary); every other model stays on sd-cli. No proof file = the old path, unchanged;
  ``0``/``off``    never (an unknown value counts as off).
Any server error falls back to sd-cli for that job; STOP kills the server.

Keep-warm photo EDIT models (owner 23.10: «Не разгружай модель, пусть подряд генерирует всё»; a lone
qwen-image-2.1-uc-edit job paid a 1599 s cold sd-cli load while warm jobs loaded in 78 s): for ``KEEP_WARM_MODELS``
the resident server is used even for a SINGLE job (wherever the mode above already allows the server for that model,
i.e. on / auto / proven - never when it is off) and stays loaded for ``KEEP_WARM_S`` after each job (the window
restarts with every job), so the next edit skips the load. It is still released at once for another model / engine,
on STOP or cancel, on low free memory (``KEEP_WARM_MIN_FREE_GIB``) or an active owner STOP flag (checked after the job
and every ``KEEP_WARM_CHECK_S`` while kept). ``BOSSMAN_DIRECT_GEN_KEEP_WARM_S=0`` turns keep-warm off.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .sdcli import PROGRESS, RunResult, SdCliRunner

ENV_FLAG = "BOSSMAN_DIRECT_GEN_RESIDENT"
ENV_IDLE = "BOSSMAN_DIRECT_GEN_RESIDENT_IDLE_S"
ENV_BIN = "BOSSMAN_DIRECT_GEN_SD_BIN"
DEFAULT_IDLE_S = 120.0
AUTO_IDLE_S = 60.0
PROOF_FILE = "resident-proof.json"
PROOF_SCHEMA = "bossman.direct-gen.resident-proof/1"
MODE_ON, MODE_AUTO, MODE_PROVEN, MODE_OFF = "on", "auto", "auto-if-proven", "off"
# Keep-warm photo edit models (see the module docstring). Video models are never kept: they are not resident at all.
ENV_KEEP_WARM = "BOSSMAN_DIRECT_GEN_KEEP_WARM_S"
KEEP_WARM_MODELS = frozenset({"qwen-image-2.1-uc-edit", "qwen-image-edit-2509"})
KEEP_WARM_S = 30 * 60.0            # the model stays loaded this long after each job (restarted by every job)
KEEP_WARM_MIN_FREE_GIB = 16.0      # less free memory than this while kept -> released (other programs need it)
KEEP_WARM_CHECK_S = 15.0           # how often a kept server re-checks free memory and the owner STOP flag

# Flags that carry the job itself (one HTTP request each) -> aliases normalised to the short form.
_JOB_VALUE = {"-p": "-p", "--prompt": "-p", "-n": "-n", "--negative-prompt": "-n", "-W": "-W", "--width": "-W",
              "-H": "-H", "--height": "-H", "--steps": "--steps", "-s": "-s", "--seed": "-s",
              "-o": "-o", "--output": "-o", "-r": "-r", "--ref-image": "-r"}
# Startup flags the server accepts with identical meaning (identity of the loaded engine).
_START_VALUE = {"--diffusion-model", "-m", "--model", "--vae", "--llm", "--llm_vision", "--clip_l", "--clip_g",
                "--t5xxl", "--cfg-scale", "--sampling-method", "--flow-shift", "--guidance", "--scheduler"}
_START_BOOL = {"--offload-to-cpu", "--diffusion-fa", "--fa", "--vae-tiling", "--increase-ref-index"}


def mode(env: dict | None = None) -> str:
    """on | auto | auto-if-proven (unset, the default) | off (explicit off or an unknown value: the safe side)."""
    raw = str((env if env is not None else os.environ).get(ENV_FLAG, "")).strip().lower()
    if raw in ("1", "true", "yes", "on", "always"):
        return MODE_ON
    if raw in ("auto", "queue"):
        return MODE_AUTO
    if raw == "":
        return MODE_PROVEN
    return MODE_OFF


def enabled(env: dict | None = None) -> bool:
    """The old always-on switch (``mode == on``)."""
    return mode(env) == MODE_ON


def idle_seconds(env: dict | None = None) -> float:
    env = env if env is not None else os.environ
    default = DEFAULT_IDLE_S if mode(env) == MODE_ON else AUTO_IDLE_S
    try:
        return max(0.0, float(env.get(ENV_IDLE, default)))
    except (TypeError, ValueError):
        return default


def keep_warm_seconds(env: dict | None = None) -> float:
    """Keep-warm window for ``KEEP_WARM_MODELS`` (0 = keep-warm off; junk -> the default)."""
    env = env if env is not None else os.environ
    try:
        return max(0.0, float(env.get(ENV_KEEP_WARM, KEEP_WARM_S)))
    except (TypeError, ValueError):
        return KEEP_WARM_S


def proven_models(direct_gen_dir: Path, server_bin: Path) -> set[str]:
    """Model ids whose resident path passed the live parity benchmark with THIS sd-server binary (size + mtime).
    Unreadable / another binary / no PASS -> empty set (the per-job sd-cli path stays)."""
    try:
        data = json.loads((Path(direct_gen_dir) / PROOF_FILE).read_text(encoding="utf-8"))
        st = Path(server_bin).stat()
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict) or data.get("schema") != PROOF_SCHEMA:
        return set()
    try:
        if int(data.get("server_size")) != st.st_size or int(data.get("server_mtime")) != int(st.st_mtime):
            return set()
    except (TypeError, ValueError):
        return set()
    models = data.get("models") or {}
    if not isinstance(models, dict):
        return set()
    return {m for m, r in models.items() if isinstance(r, dict) and r.get("verdict") == "PASS"}


def server_binary_for(cli: Path) -> Path:
    return Path(cli).with_name("sd-server.exe" if str(cli).lower().endswith(".exe") else "sd-server")


# ----------------------------------------------------------------------------- argv -> plan


@dataclass(frozen=True)
class Plan:
    """What a ``build_argv`` list means for the resident server. ``fallback`` non-empty = use sd-cli instead."""
    fallback: str = ""
    startup: tuple[str, ...] = ()          # tokens handed to sd-server at start (order preserved)
    prompt: str = ""
    negative: str = ""
    width: int = 512
    height: int = 512
    steps: int = 20
    seed: int = 42
    out: str = ""
    refs: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, ...]:
        return self.startup


def plan_argv(argv: list[str]) -> Plan:
    """Split an sd-cli argv (``argv[0]`` is the binary) into startup flags and one job. Pure; no I/O."""
    startup: list[str] = []
    job: dict[str, Any] = {}
    refs: list[str] = []
    toks = list(argv[1:])
    i = 0
    while i < len(toks):
        flag = toks[i]
        if flag in _JOB_VALUE:
            if i + 1 >= len(toks):
                return Plan(fallback=f"flag {flag} has no value")
            val = toks[i + 1]
            short = _JOB_VALUE[flag]
            if short == "-r":
                refs.append(val)
            else:
                job[short] = val
            i += 2
        elif flag in _START_VALUE:
            if i + 1 >= len(toks):
                return Plan(fallback=f"flag {flag} has no value")
            startup += [flag, toks[i + 1]]
            i += 2
        elif flag in _START_BOOL:
            startup.append(flag)
            i += 1
        else:
            return Plan(fallback=f"{flag} is not expressible through sd-server")
    for need in ("-p", "-o"):
        if need not in job:
            return Plan(fallback=f"argv has no {need}")
    if "<lora:" in job["-p"]:
        return Plan(fallback="LoRA tag in the prompt (sd-server takes LoRA only via the structured field)")
    try:
        return Plan(startup=tuple(startup), prompt=job["-p"], negative=job.get("-n", ""),
                    width=int(job.get("-W", 512)), height=int(job.get("-H", 512)),
                    steps=int(job.get("--steps", 20)), seed=int(job.get("-s", 42)),
                    out=job["-o"], refs=tuple(refs))
    except ValueError as exc:
        return Plan(fallback=f"non-numeric size/steps/seed: {exc}")


def to_request(plan: Plan, read_bytes: Callable[[str], bytes] = lambda p: Path(p).read_bytes()) -> dict:
    """The ``POST /sdcpp/v1/img_gen`` body. Everything not named here comes from the server's startup flags."""
    body: dict[str, Any] = {"prompt": plan.prompt, "negative_prompt": plan.negative, "width": plan.width,
                            "height": plan.height, "seed": plan.seed, "batch_count": 1,
                            "sample_params": {"sample_steps": plan.steps}, "output_format": "png", "preview": "none"}
    if plan.refs:
        body["ref_images"] = [base64.b64encode(read_bytes(p)).decode() for p in plan.refs]
    return body


# ----------------------------------------------------------------------------- engine


class ServerStartError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None


async def _spawn(argv: list[str], cwd: Path):
    return await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, cwd=str(cwd), limit=1 << 20)


def _kill(proc: Any) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        from ..studio.providers.sdcpp import _kill_tree
        _kill_tree(proc.pid)
    except Exception:  # noqa: BLE001 - the direct handle below still runs
        pass
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


def _bind(pid: int) -> None:
    with contextlib.suppress(Exception):
        from ..studio.providers.sdcpp import bind_to_owner_lifetime
        bind_to_owner_lifetime(pid)


def _high_priority(pid: int) -> None:
    """Same as SdCliRunner: the generation engine runs at HIGH priority (Windows); best effort."""
    with contextlib.suppress(Exception):
        import psutil
        if hasattr(psutil, "HIGH_PRIORITY_CLASS"):
            psutil.Process(pid).nice(psutil.HIGH_PRIORITY_CLASS)


class ResidentEngine:
    """One sd-server process, shared by the photo jobs of this backend. Single user at a time (the service gate)."""

    def __init__(self, server_bin: Path, *, idle_s: float = DEFAULT_IDLE_S, spawn=_spawn, http=_http, kill=_kill,
                 bind=_bind, port_picker=_free_port, ready_timeout_s: float = 3600.0, poll_s: float = 0.5,
                 max_no_progress_s: float = 600.0, cwd: Path | None = None, prioritize=_high_priority) -> None:
        self.server_bin, self.idle_s = Path(server_bin), idle_s
        self._spawn, self._http, self._kill, self._bind, self._port = spawn, http, kill, bind, port_picker
        self._prioritize = prioritize
        self.ready_timeout_s, self.poll_s = ready_timeout_s, poll_s
        self.max_no_progress_s = max_no_progress_s
        self.last_progress_at = 0.0
        self.cwd = Path(cwd) if cwd else Path(server_bin).parent
        self.proc: Any = None
        self.key: tuple[str, ...] | None = None
        self.tag: str = ""
        self.port = 0
        self.started_at = 0.0
        self.startup_s = 0.0
        self.log: deque[str] = deque(maxlen=60)
        self.on_progress: Callable[[int, int], Awaitable[None]] | None = None
        self.starts = 0
        self.busy = False
        self.keep_until = 0.0               # monotonic deadline of a keep-warm window (0 = none)
        self.keep_released = ""             # why the last keep-warm window ended early (status only)
        self._reader: asyncio.Task | None = None
        self._idle: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    # -- state
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def holds(self, key: tuple[str, ...]) -> bool:
        return self.alive() and self.key == key

    def kept(self) -> bool:
        """Inside a keep-warm window: the loaded weights are kept on purpose for the next job of the same model."""
        return self.alive() and self.keep_until > time.monotonic()

    def status(self) -> dict:
        return {"loaded": self.alive(), "tag": self.tag if self.alive() else "", "starts": self.starts,
                "idle_s": self.idle_s, "busy": self.busy, "binary": self.server_bin.name,
                "binary_present": self.server_bin.is_file(),
                "keep_warm_left_s": round(max(0.0, self.keep_until - time.monotonic()), 1) if self.kept() else 0.0,
                "keep_warm_released": self.keep_released}

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- lifecycle
    async def prepare(self, tag: str) -> bool:
        """Before a job of model ``tag``: True = this model is already resident (memory check not needed);
        otherwise whatever is resident is released first so its memory counts as free again."""
        self._cancel_idle()
        if self.alive() and self.tag == tag:
            return True
        await self.stop()
        return False

    async def prepare_key(self, key: tuple[str, ...] | None) -> bool:
        """Like ``prepare`` but by the exact startup flags: True = these weights/flags are loaded right now (the memory
        check may be skipped); otherwise the server is released first (``None`` = this job will not use it)."""
        self._cancel_idle()
        if key is not None and self.holds(key):
            return True
        await self.stop()
        return False

    async def ensure(self, plan: Plan, tag: str = "") -> bool:
        """Make the server for ``plan.startup`` ready. Returns True when it was already warm."""
        async with self._lock:
            self._cancel_idle()
            if self.holds(plan.key):
                return True
            await self._stop_locked()
            await self._start(plan, tag)
            return False

    async def _start(self, plan: Plan, tag: str) -> None:
        self.port = self._port()
        argv = [str(self.server_bin), "--listen-ip", "127.0.0.1", "--listen-port", str(self.port), *plan.startup]
        t0 = time.monotonic()
        try:
            self.proc = await self._spawn(argv, self.cwd)
        except OSError as exc:
            raise ServerStartError(f"cannot start sd-server: {exc}") from None
        self.starts += 1
        self.log.clear()
        self._bind(self.proc.pid)
        self._prioritize(self.proc.pid)
        self._reader = asyncio.create_task(self._read_log(self.proc))
        deadline = t0 + self.ready_timeout_s
        while True:
            if self.proc.returncode is not None:
                tail = " | ".join(list(self.log)[-4:])[:500]
                self.proc, self.key = None, None
                raise ServerStartError(f"sd-server exited during start: {tail}")
            try:
                status, _ = await asyncio.to_thread(self._http, "GET", self.base + "/sdcpp/v1/capabilities", None, 5.0)
            except (OSError, ValueError):
                status = 0
            if status == 200:
                break
            if time.monotonic() > deadline:
                await self._stop_locked()
                raise ServerStartError("sd-server did not become ready in time")
            await asyncio.sleep(self.poll_s)
        self.key, self.tag = plan.key, tag
        self.started_at, self.startup_s = time.time(), round(time.monotonic() - t0, 2)

    async def _read_log(self, proc: Any) -> None:
        buf = b""
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for raw in parts:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    m = PROGRESS.search(line)
                    cb = self.on_progress
                    if m:
                        self.last_progress_at = time.monotonic()
                    if m and cb is not None:
                        await cb(int(m.group(1)), int(m.group(2)))
                    elif not m:
                        self.log.append(line[:400])
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - log pump must never take the job down
            pass

    def kill_now(self) -> None:
        """STOP: hard-kill the server tree (the API cannot cancel a generating job)."""
        self._cancel_idle()
        self._kill(self.proc)
        self.key, self.keep_until = None, 0.0

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self._cancel_idle()
        proc, self.proc, self.key, self.tag, self.keep_until = self.proc, None, None, "", 0.0
        if proc is not None:
            self._kill(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 15)
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None

    # -- idle unload
    def release(self) -> None:
        """A job finished: arm the idle timer (keeps the model for the next queued job, frees the PC otherwise)."""
        self.busy = False
        self._cancel_idle()
        if self.alive():
            self._idle = asyncio.get_running_loop().call_later(self.idle_s, lambda: asyncio.ensure_future(self._idle_stop()))

    def keep_warm(self, seconds: float) -> bool:
        """After a job of a keep-warm model: keep the loaded weights for ``seconds`` (replaces the idle timer; every job
        restarts the window). False = nothing to keep (server not running, or keep-warm off)."""
        self._cancel_idle()
        if not self.alive() or seconds <= 0:
            self.keep_until = 0.0
            return False
        self.keep_until, self.keep_released = time.monotonic() + seconds, ""
        self._idle = asyncio.get_running_loop().call_later(seconds, lambda: asyncio.ensure_future(self._idle_stop()))
        return True

    async def _idle_stop(self) -> None:
        if not self.busy and self.alive():
            await self.stop()

    def _cancel_idle(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None


# ----------------------------------------------------------------------------- runner (drop-in for SdCliRunner)


def _atomic_write_bytes(path_str: str | Path, data: bytes) -> None:
    dst = Path(path_str)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f"{dst.name}.part")
    part.write_bytes(data)
    os.replace(part, dst)


class ResidentRunner:
    """Same interface as ``SdCliRunner`` (``run`` / ``kill`` / ``proc``); chooses server or sd-cli per job."""

    def __init__(self, engine: ResidentEngine, *, tag: str = "", cli_factory: Callable[[], Any] = SdCliRunner,
                 read_bytes: Callable[[str], bytes] = lambda p: Path(p).read_bytes(),
                 write_bytes: Callable[[str, bytes], None] = _atomic_write_bytes) -> None:
        self.engine, self.tag = engine, tag
        self._cli_factory, self._read, self._write = cli_factory, read_bytes, write_bytes
        self._cli: Any = None
        self._using_server = False
        self.engine_name = "sd-cli"
        self.fallback_reason = ""
        self.warm = False
        self.startup_s = 0.0
        self.use_server = True              # set by the service per job (auto: only inside a same-model queue run)
        self.decision = ""                  # why this engine was chosen (sd-cli although the server was possible, keep-warm)
        self.ready_s: float | None = None   # seconds from run() until the server held the weights (None: sd-cli)
        self._killed = False

    @property
    def proc(self):
        return self.engine.proc if self._using_server else getattr(self._cli, "proc", None)

    def kill(self) -> None:
        self._killed = True                            # STOP: a dead server is NOT a reason to rerun on sd-cli
        if self._using_server:
            self.engine.kill_now()
        elif self._cli is not None:
            self._cli.kill()

    async def _cli_run(self, argv, cwd, on_progress, reason: str) -> RunResult:
        self.engine_name, self.fallback_reason, self._using_server = "sd-cli", reason, False
        self.warm, self.ready_s = False, None          # the result comes from a fresh, cold sd-cli process
        await self.engine.stop()                       # its weights must not stay resident next to the sd-cli process
        self._cli = self._cli_factory()
        return await self._cli.run(argv, cwd=cwd, on_progress=on_progress)

    async def run(self, argv: list[str], *, cwd: Path, on_progress=None) -> RunResult:
        plan = plan_argv(argv)
        if plan.fallback:
            return await self._cli_run(argv, cwd, on_progress, plan.fallback)
        if not self.use_server:
            return await self._cli_run(argv, cwd, on_progress, "")
        self.engine.busy = True
        started = time.monotonic()
        try:
            try:
                self.warm = await self.engine.ensure(plan, self.tag)
            except ServerStartError as exc:
                return await self._cli_run(argv, cwd, on_progress, f"sd-server unavailable: {exc}")
            self.engine_name, self.fallback_reason, self._using_server = "sd-server (resident)", "", True
            self.startup_s = 0.0 if self.warm else self.engine.startup_s
            self.ready_s = round(time.monotonic() - started, 2)
            self.engine.on_progress = on_progress
            result = await self._request(plan, started)
            if result.returncode != 0 and not self._killed:
                # any server-side error (refused, failed, crashed, no image): drop the server, rerun on proven sd-cli
                tail = " | ".join(result.log[-2:])[:300]
                self.engine.kill_now()
                self.engine.on_progress = None
                return await self._cli_run(argv, cwd, on_progress, f"sd-server error, rerun on sd-cli: {tail}")
            return result
        except asyncio.CancelledError:
            self.engine.kill_now()
            raise
        finally:
            self.engine.on_progress = None
            if self._using_server:
                self.engine.release()
            else:
                self.engine.busy = False

    async def _request(self, plan: Plan, started: float) -> RunResult:
        eng = self.engine
        peak, ps = 0, None
        with contextlib.suppress(Exception):
            import psutil
            ps = psutil.Process(eng.proc.pid)

        def fail(msg: str) -> RunResult:
            return RunResult(1, [*list(eng.log)[-8:], msg][-60:], peak, round(time.monotonic() - started, 2))

        status, data = await asyncio.to_thread(eng._http, "POST", eng.base + "/sdcpp/v1/img_gen",
                                               to_request(plan, self._read), 120.0)
        if status not in (200, 202) or not isinstance(data, dict) or not data.get("id"):
            eng.kill_now()                                 # unknown server state: do not keep it resident
            return fail(f"sd-server refused the job (HTTP {status}): {str(data)[:300]}")
        url = f"{eng.base}/sdcpp/v1/jobs/{data['id']}"
        eng.last_progress_at = time.monotonic()
        last_seen_progress: Any = None
        while True:
            if eng.proc is None or eng.proc.returncode is not None:
                return fail("sd-server exited during generation")
            if ps is not None:
                with contextlib.suppress(Exception):
                    peak = max(peak, ps.memory_info().rss)
            try:
                status, job = await asyncio.to_thread(eng._http, "GET", url, None, 30.0)
            except (OSError, ValueError):
                status, job = 0, None
            if status == 200 and isinstance(job, dict):
                prog = job.get("progress") if "progress" in job else job.get("step")
                if prog is not None and prog != last_seen_progress:
                    last_seen_progress = prog
                    eng.last_progress_at = time.monotonic()
                st = job.get("status")
                if st == "completed":
                    images = (job.get("result") or {}).get("images") or []
                    if not images or not images[0].get("b64_json"):
                        return fail("sd-server completed without an image")
                    self._write(plan.out, base64.b64decode(images[0]["b64_json"]))
                    return RunResult(0, list(eng.log)[-8:], peak, round(time.monotonic() - started, 2))
                if st in ("failed", "cancelled"):
                    err = job.get("error") or {}
                    eng.kill_now() if st == "failed" else None
                    return fail(f"sd-server job {st}: {err.get('message', '')}")
            if eng.max_no_progress_s > 0 and (time.monotonic() - eng.last_progress_at) > eng.max_no_progress_s:
                eng.kill_now()
                return fail(f"sd-server stalled without progress for >{eng.max_no_progress_s}s")
            await asyncio.sleep(eng.poll_s)
