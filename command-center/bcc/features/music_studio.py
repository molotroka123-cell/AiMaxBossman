"""Local Music Studio — ACE-Step 1.5 adapter and service lifecycle.

Owner-facing product contract:
prompt -> local ACE-Step task -> poll -> download -> verify bytes -> persist.
No cloud fallback and no mock is reported as generation success.

The generator is a separate local service (ACE-Step REST on 127.0.0.1:8001). Until 2026-09-30
the page only said "ConnectError: All connection attempts failed" when it was not there. Now
/api/music/health names the real state — NOT_INSTALLED / NOT_RUNNING / STARTING / NOT_READY /
READY / NOT_CONFIGURED — with a remedy, and POST /api/music/service/start|stop run the
installed service as a child process of Bossman (loopback only, log, single instance, STOP).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..probe_cache import probes
from . import Feature

router = APIRouter(prefix="/music", tags=["music-studio"])

PROVIDER = "ACE-Step 1.5"
DOC = "docs/music/MUSIC_STUDIO.md"
INSTALL_COMMAND = r"python tools\music_studio_install.py install"
DEFAULT_LM = "acestep-5Hz-lm-1.7B"
PORT_PROBE_TIMEOUT = 0.7     # a listening loopback port accepts at once; Windows needs ~2 s to refuse
HEALTH_TIMEOUT = 4.0
STOP_GRACE = 8.0
START_SETTLE = 1.2           # long enough to see an immediate crash (missing DLL, bad venv)
LOG_TAIL_LINES = 40
LOG_TAIL_BYTES = 64 * 1024
LOG_TRIM_BYTES = 8 * 1024 * 1024


class MusicIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    lyrics: str = Field(default="[inst]", max_length=12000)
    duration: int = Field(default=90, ge=10, le=600)
    bpm: int | None = Field(default=None, ge=40, le=240)
    key_scale: str = Field(default="", max_length=40)
    time_signature: str = Field(default="4", pattern=r"^(2|3|4|6)$")
    thinking: bool = True
    model: str = Field(default="acestep-v15-turbo", max_length=120)


def _base() -> str:
    return os.environ.get("BOSSMAN_ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("BOSSMAN_ACESTEP_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _loopback_only(url: str) -> None:
    """Local generation must not silently become an arbitrary network egress."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ACE-Step URL must be http(s)")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("ACE-Step host does not resolve") from exc
    if not infos:
        raise ValueError("ACE-Step host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_loopback:
            raise ValueError("ACE-Step must be a local loopback service")


def _endpoint() -> tuple[str, int]:
    """Host/port Bossman probes and binds. `localhost` is pinned to the IPv4 loopback."""
    parsed = urlsplit(_base())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return ("127.0.0.1" if host == "localhost" else host), port


async def _json(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    response = await client.request(method, _base() + path, headers=_headers(), **kwargs)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("code") != 200 or body.get("error"):
        raise RuntimeError("ACE-Step returned a non-success envelope")
    return body.get("data")


# ------------------------------------------------------------------ installation on disk

def _install_candidates() -> list[Path]:
    """Where an ACE-Step install may live, most specific first (nothing here is downloaded)."""
    out: list[Path] = []
    explicit = os.environ.get("BOSSMAN_ACESTEP_DIR", "").strip()
    if explicit:
        out.append(Path(explicit))
    media = os.environ.get("BOSSMAN_MEDIA_RUNTIME", "").strip()
    if media:
        out.append(Path(media) / "acestep")
    try:   # <parent>/<checkout>/command-center/bcc/features/music_studio.py -> <parent>/media-runtime
        out.append(Path(__file__).resolve().parents[4] / "media-runtime" / "acestep")
    except IndexError:
        pass
    out.append(Path.home() / "Bossman" / "media-runtime" / "acestep")
    unique: list[Path] = []
    for path in out:
        if path not in unique:
            unique.append(path)
    return unique


def _venv_python(root: Path) -> Path | None:
    override = os.environ.get("BOSSMAN_ACESTEP_PYTHON", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    for name in ("venv_rocm", "venv", ".venv"):
        for parts in (("Scripts", "python.exe"), ("bin", "python")):
            path = root.joinpath(name, *parts)
            if path.is_file():
                return path
    return None


def find_install() -> dict[str, Any]:
    """The first complete installation, else the most complete partial one (for an honest message)."""
    candidates = _install_candidates()
    partial: dict[str, Any] | None = None
    for root in candidates:
        if not root.is_dir():
            continue
        script = root / "repo" / "acestep" / "api_server.py"
        python = _venv_python(root)
        missing = []
        if not script.is_file():
            missing.append("исходники ACE-Step (repo/acestep/api_server.py)")
        if python is None:
            missing.append("окружение venv_rocm (Python 3.12 + PyTorch ROCm)")
        info = {"installed": not missing, "root": str(root), "repo": str(root / "repo"),
                "script": str(script), "python": str(python) if python else None,
                "missing": missing}
        if not missing:
            return info
        if partial is None or len(missing) < len(partial["missing"]):
            partial = info
    if partial is not None:
        return partial
    return {"installed": False, "root": None, "repo": None, "script": None, "python": None,
            "missing": ["каталог установки"], "searched": [str(path) for path in candidates]}


# ------------------------------------------------------------------ child process registry

@dataclass
class _Service:
    proc: Any                  # subprocess.Popen or _Recovered
    argv: list[str]
    port: int
    log_path: Path
    started_at: float          # epoch, for the owner
    started_mono: float        # monotonic, for uptime


class _Recovered:
    """Our own child that outlived a Bossman restart. psutil guards signals against PID reuse."""

    def __init__(self, pid: int):
        self.pid = pid
        self._process = psutil.Process(pid)

    def poll(self):
        try:
            running = self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return 0
        return None if running else 0


_service: _Service | None = None
_last_exit: dict[str, Any] | None = None
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def _service_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "music" / "service"


def _log_path(data_dir: Path) -> Path:
    return _service_dir(data_dir) / "acestep.log"


def _record_path(data_dir: Path) -> Path:
    return _service_dir(data_dir) / "acestep.json"


def _record_write(rec: _Service, data_dir: Path) -> None:
    try:
        identity = psutil.Process(rec.proc.pid)
        _record_path(data_dir).write_text(json.dumps({
            "pid": rec.proc.pid, "port": rec.port, "argv": rec.argv,
            "started_at": rec.started_at, "process_created": identity.create_time(),
            "process_exe": identity.exe(), "process_argv": identity.cmdline(),
        }, ensure_ascii=False), encoding="utf-8")
    except (OSError, psutil.Error):
        pass        # the record is a convenience for recovery, never a condition for starting


def _record_drop(data_dir: Path | None) -> None:
    if data_dir is not None:
        with contextlib.suppress(OSError):
            _record_path(data_dir).unlink(missing_ok=True)


def _recover(data_dir: Path) -> _Service | None:
    """A child we started before Bossman restarted: pid + creation time + argv must all match."""
    try:
        data = json.loads(_record_path(data_dir).read_text(encoding="utf-8"))
        process = psutil.Process(int(data["pid"]))
        same = (process.create_time() == data["process_created"]
                and process.exe() == data["process_exe"]
                and process.cmdline() == data["process_argv"])
    except (OSError, ValueError, TypeError, KeyError, psutil.Error):
        same = False
        data = {}
    if not same:
        if data or _record_path(data_dir).exists():
            _record_drop(data_dir)
        return None
    return _Service(proc=_Recovered(int(data["pid"])), argv=list(data["argv"]), port=int(data["port"]),
                    log_path=_log_path(data_dir), started_at=float(data["started_at"]),
                    started_mono=time.monotonic() - max(0.0, time.time() - float(data["started_at"])))


def _owned(data_dir: Path | None) -> _Service | None:
    """The live service THIS Bossman started. A dead child stops being ours and is remembered as an exit."""
    global _service, _last_exit
    rec = _service
    if rec is not None:
        code = rec.proc.poll()
        if code is None:
            return rec
        _last_exit = {"code": code, "at": time.time(),
                      "ran_seconds": round(time.monotonic() - rec.started_mono, 1)}
        _service = None
        _record_drop(data_dir)
        return None
    if data_dir is not None:
        rec = _recover(data_dir)
        if rec is not None:
            _service = rec
            return rec
    return None


def _kill_tree(proc: Any, grace: float = STOP_GRACE) -> None:
    """Stop our child and everything it spawned (on Windows a venv python.exe is a launcher
    whose real interpreter is a child). Only descendants of the verified pid are touched."""
    try:
        parent = psutil.Process(proc.pid)
        victims = parent.children(recursive=True) + [parent]
    except psutil.Error:
        return
    for victim in victims:
        with contextlib.suppress(psutil.Error):
            victim.terminate()
    _gone, alive = psutil.wait_procs(victims, timeout=grace)
    for victim in alive:
        with contextlib.suppress(psutil.Error):
            victim.kill()
    if alive:
        psutil.wait_procs(alive, timeout=grace)


# ------------------------------------------------------------------ log

_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|secret|token|password|authorization|bearer)\b\s*[:=]\s*\S+")
_SECRET_VALUE_RE = re.compile(r"\b(sk|xoxb|ghp|gho|hf)_?-?[A-Za-z0-9_\-]{12,}")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_NATIVE_FRAME_RE = re.compile(r"^0x[0-9A-Fa-f]{8,},")      # a native crash dump: hundreds of frames, no news


def _redact(line: str) -> str:
    line = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", line)
    return _SECRET_VALUE_RE.sub("***", line)[:400]


def _log_tail(path: Path | None, lines: int = LOG_TAIL_LINES) -> list[str]:
    """Last readable lines of the service log: what a terminal would show (progress bars keep their
    last redraw), without native stack frames, secrets masked."""
    if path is None:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return []
    rows: list[str] = []
    for row in raw.decode("utf-8", "replace").split("\n"):
        row = _ANSI_RE.sub("", row).rstrip("\r").rsplit("\r", 1)[-1].strip()
        if row and not _NATIVE_FRAME_RE.match(row) and (not rows or rows[-1] != row):
            rows.append(row)
    return [_redact(row) for row in rows[-lines:]]


# ------------------------------------------------------------------ health

async def _port_open(host: str, port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), PORT_PROBE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


def _health_shell(data_dir: Path | None) -> dict[str, Any]:
    install = find_install()
    rec = _owned(data_dir)
    return {"provider": PROVIDER, "local": True, "base_url": _base(),
            "installed": install["installed"], "install_dir": install.get("root"),
            "owned": rec is not None, "running": rec is not None,
            "pid": rec.proc.pid if rec else None,
            "uptime_seconds": round(time.monotonic() - rec.started_mono, 1) if rec else None,
            "log_path": str(rec.log_path) if rec else (str(_log_path(data_dir)) if data_dir else None)}


def _not_installed(install: dict[str, Any], base: str) -> dict[str, Any]:
    if install.get("root") is None:
        what = "не найден каталог установки"
    else:
        what = "установка неполная — нет: " + ", ".join(install["missing"])
    return {"status": "NOT_INSTALLED", "action": "install", "can_start": False,
            "reason": f"ACE-Step 1.5 не установлен на этом ПК ({what}); по адресу {base} никто не отвечает.",
            "remedy": ("Установите ACE-Step один раз: команда ниже или раздел «Установка и запуск "
                       f"на ПК владельца» в {DOC}. После установки нажмите «Запустить ACE-Step»."),
            "install_command": INSTALL_COMMAND}


async def _probe_health(data_dir: Path | None = None) -> dict:
    """Why music generation is (not) available — never a raw transport error."""
    base = _base()
    shell = _health_shell(data_dir)
    try:
        _loopback_only(base)
    except ValueError as exc:
        return {**shell, "status": "NOT_CONFIGURED", "action": None, "can_start": False,
                "reason": (f"Адрес BOSSMAN_ACESTEP_URL ({base}) не подходит: Music Studio работает "
                           f"только с локальным сервисом ACE-Step (loopback). {exc}."),
                "remedy": "Уберите переменную BOSSMAN_ACESTEP_URL или укажите http://127.0.0.1:8001.",
                "diagnostics": {"error_type": type(exc).__name__}}
    host, port = _endpoint()
    rec = _owned(data_dir)
    shell = _health_shell(data_dir)
    if not await _port_open(host, port):
        if rec is not None:
            seconds = int(time.monotonic() - rec.started_mono)
            return {**shell, "status": "STARTING", "action": "wait", "can_start": False,
                    "reason": (f"ACE-Step запускается (pid {rec.proc.pid}, {seconds} с): "
                               f"сервис ещё не открыл порт {port}."),
                    "remedy": ("Подождите: обычно 1–3 минуты, первая загрузка моделей дольше. "
                               "Страница обновится сама; ход запуска виден в журнале."),
                    "log_tail": _log_tail(rec.log_path, 12)}
        install = find_install()
        if not install["installed"]:
            return {**shell, **_not_installed(install, base)}
        payload = {**shell, "status": "NOT_RUNNING", "action": "start", "can_start": True,
                   "reason": f"ACE-Step установлен, но не запущен: по адресу {base} никто не отвечает.",
                   "remedy": f"Нажмите «Запустить ACE-Step» на этой странице (сервис стартует на этом ПК, {base}).",
                   "diagnostics": {"error_type": "PortClosed"}}
        if _last_exit is not None:
            payload["reason"] = (f"ACE-Step завершился (код {_last_exit['code']}) после "
                                 f"{_last_exit['ran_seconds']} с работы и больше не отвечает.")
            payload["remedy"] = ("Посмотрите журнал ниже, затем нажмите «Запустить ACE-Step» ещё раз. "
                                 "Если он падает снова — раздел «Диагностика» в " + DOC + ".")
            payload["log_tail"] = _log_tail(_log_path(data_dir) if data_dir else None, 12)
            payload["last_exit_code"] = _last_exit["code"]
        return payload
    # The port answers: is it ACE-Step, and are the models loaded?
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT, trust_env=False) as client:
            response = await client.get(base + "/health", headers=_headers())
            response.raise_for_status()
            body = response.json()
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return {**shell, "status": "NOT_READY", "action": "wait", "can_start": False,
                "reason": f"Порт {port} открыт, но ACE-Step не ответил на проверку за {HEALTH_TIMEOUT:.0f} с.",
                "remedy": "Подождите и нажмите «Проверить снова»; если не проходит — смотрите журнал.",
                "log_tail": _log_tail(rec.log_path, 12) if rec else [],
                "diagnostics": {"error_type": type(exc).__name__}}
    except (httpx.HTTPError, ValueError) as exc:
        return {**shell, "status": "NOT_READY", "action": None, "can_start": False,
                "reason": (f"На порту {port} отвечает не ACE-Step: ответ на /health не похож на "
                           f"ACE-Step 1.5 ({type(exc).__name__})."),
                "remedy": (f"Освободите порт {port} или задайте BOSSMAN_ACESTEP_URL на порт ACE-Step. "
                           "Чужие процессы Bossman не останавливает."),
                "diagnostics": {"error_type": type(exc).__name__}}
    data = body.get("data") if isinstance(body, dict) and body.get("code") == 200 else None
    if not isinstance(data, dict) or data.get("status") != "ok" or "models_initialized" not in data:
        return {**shell, "status": "NOT_READY", "action": None, "can_start": False,
                "reason": (f"На порту {port} отвечает не ACE-Step: ответ на /health не похож на "
                           "ACE-Step 1.5 (нет status/models_initialized)."),
                "remedy": (f"Освободите порт {port} или задайте BOSSMAN_ACESTEP_URL на порт ACE-Step. "
                           "Чужие процессы Bossman не останавливает."),
                "diagnostics": {"error_type": "UnexpectedHealthShape"}}
    if not data.get("models_initialized"):
        return {**shell, "status": "NOT_READY", "action": "wait", "can_start": False,
                "reason": "ACE-Step запущен, но модели ещё загружаются — генерация пока невозможна.",
                "remedy": "Подождите: страница обновится сама. Журнал ниже показывает ход загрузки.",
                "log_tail": _log_tail(rec.log_path if rec else (_log_path(data_dir) if data_dir else None), 12)}
    return {**shell, "status": "READY", "action": None, "can_start": False,
            "reason": "ACE-Step 1.5 запущен и готов к генерации.", "remedy": "",
            "loaded_model": data.get("loaded_model"),
            "loaded_lm_model": data.get("loaded_lm_model"),
            "llm_initialized": bool(data.get("llm_initialized"))}


def _data_dir(request: Request) -> Path | None:
    settings = getattr(getattr(request.app.state, "svc", None), "settings", None)
    value = getattr(settings, "data_dir", None)
    return Path(value) if value else None


@router.get("/health")
async def health(request: Request):
    data_dir = _data_dir(request)
    # Short TTL: the owner watches this page while the service starts.
    return await probes(request.app.state.svc).get("music.health", lambda: _probe_health(data_dir),
                                                   ttl=3.0, stale_ttl=8.0)


_CODES = {"NOT_INSTALLED": "MUSIC_NOT_INSTALLED", "NOT_RUNNING": "MUSIC_NOT_RUNNING",
          "STARTING": "MUSIC_STARTING", "NOT_READY": "MUSIC_NOT_READY",
          "NOT_CONFIGURED": "MUSIC_NOT_CONFIGURED"}


async def _unavailable(request: Request, exc: Exception) -> HTTPException:
    """A request to ACE-Step failed: say WHY (from the real state), not the exception class."""
    state = await _probe_health(_data_dir(request))
    status = state["status"]
    if status != "READY":
        return HTTPException(503, {"message": state["reason"], "code": _CODES.get(status, "MUSIC_UNAVAILABLE"),
                                   "hint": state["remedy"]})
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return HTTPException(503, {"message": "ACE-Step отклонил ключ доступа.", "code": "MUSIC_AUTH",
                                   "hint": "Проверьте BOSSMAN_ACESTEP_API_KEY: он должен совпадать с ключом сервиса."})
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(503, {"message": "ACE-Step не ответил вовремя.", "code": "MUSIC_TIMEOUT",
                                   "hint": "Возможно, идёт другая генерация или загрузка модели. Повторите через минуту."})
    return HTTPException(503, {"message": "ACE-Step запущен, но не принял запрос.", "code": "MUSIC_REQUEST_FAILED",
                               "hint": "Повторите; если не проходит — журнал сервиса (кнопка «Журнал» на странице).",
                               "error_type": type(exc).__name__})


# ------------------------------------------------------------------ lifecycle

_SECRETISH = re.compile(r"(?i)(token|secret|passw|credential|api[_-]?key|private)")


def _child_env(port: int) -> dict[str, str]:
    """The service gets a clean environment: no Bossman token/keys, no provider API keys."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("BCC_", "BOSSMAN_")) and not _SECRETISH.search(key)
           and key.upper() != "HSA_OVERRIDE_GFX_VERSION"}    # gfx1151 is native; the RDNA3 override breaks it
    env.update({
        "ACESTEP_NO_INIT": "false",             # load models at start, so /health means READY
        "ACESTEP_LM_BACKEND": "pt",             # no nano-vllm/flash-attn on ROCm Windows
        "TORCH_COMPILE_BACKEND": "eager",       # no Triton on ROCm Windows
        "MIOPEN_FIND_MODE": "FAST",             # otherwise the first VAE decode hangs for minutes
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        "ACESTEP_API_HOST": "127.0.0.1", "ACESTEP_API_PORT": str(port),
    })
    # Upstream auto-picks the 4B language model on a big GPU: +8.4 GB download, and on the owner's
    # gfx1151 loading it crashed natively (0xC0000005 on shard 2/2, 2026-09-30). 1.7B is part of the
    # main download, loads fine and generated a verified track. ACESTEP_LM_MODEL_PATH overrides it.
    env.setdefault("ACESTEP_LM_MODEL_PATH", DEFAULT_LM)
    key = os.environ.get("BOSSMAN_ACESTEP_API_KEY", "")
    if key:
        env["ACESTEP_API_KEY"] = key            # via environment, never argv (argv is logged and recorded)
    return env


def _launch_argv(install: dict[str, Any], port: int) -> list[str]:
    launcher = Path(install["root"]) / "bossman_acestep_launcher.py"
    script = launcher if launcher.is_file() else Path(install["script"])
    return [install["python"], "-u", str(script), "--host", "127.0.0.1", "--port", str(port)]


def _spawn(install: dict[str, Any], port: int, data_dir: Path) -> _Service:
    log = _log_path(data_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if log.stat().st_size > LOG_TRIM_BYTES:
            log.replace(log.with_suffix(".log.1"))
    argv = _launch_argv(install, port)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log.open("ab") as handle:
        handle.write(f"\n[bossman] {time.strftime('%Y-%m-%d %H:%M:%S')} запуск: {' '.join(argv)}\n".encode("utf-8"))
        handle.flush()
        proc = subprocess.Popen(                     # noqa: S603 — argv list built by us, no shell
            argv, cwd=install["repo"], env=_child_env(port), stdout=handle, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    rec = _Service(proc=proc, argv=argv, port=port, log_path=log,
                   started_at=time.time(), started_mono=time.monotonic())
    _record_write(rec, data_dir)
    return rec


async def start_service(data_dir: Path) -> dict[str, Any]:
    global _service, _last_exit
    async with _get_lock():
        try:
            _loopback_only(_base())
        except ValueError as exc:
            raise HTTPException(409, {"code": "MUSIC_NOT_LOOPBACK",
                                      "message": f"Запуск отклонён: адрес {_base()} не локальный (loopback). {exc}.",
                                      "hint": "Bossman запускает ACE-Step только на этом ПК: уберите BOSSMAN_ACESTEP_URL."}) from None
        host, port = _endpoint()
        rec = _owned(data_dir)
        if rec is not None:
            return {"ok": True, "started": False, "already_running": True, "pid": rec.proc.pid,
                    "port": port, "log_path": str(rec.log_path), "command": rec.argv,
                    "message": f"ACE-Step уже запущен Bossman (pid {rec.proc.pid})."}
        install = find_install()
        if not install["installed"]:
            raise HTTPException(409, {"code": "MUSIC_NOT_INSTALLED",
                                      "message": "ACE-Step не установлен — запускать нечего.",
                                      "hint": f"Сначала установите его: {DOC}, раздел «Установка и запуск на ПК владельца»."})
        if await _port_open(host, port):
            raise HTTPException(409, {"code": "MUSIC_PORT_BUSY",
                                      "message": f"Порт {port} уже занят процессом, который Bossman не запускал.",
                                      "hint": "Если это ACE-Step — обновите страницу. Иначе освободите порт: чужие процессы Bossman не трогает."})
        _last_exit = None
        try:
            rec = _spawn(install, port, data_dir)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, {"code": "MUSIC_SPAWN_FAILED",
                                      "message": f"Не удалось запустить ACE-Step ({type(exc).__name__}).",
                                      "hint": f"Проверьте установку: {DOC}, раздел «Диагностика»."}) from None
        _service = rec
    await asyncio.sleep(START_SETTLE)
    code = rec.proc.poll()
    if code is not None:
        async with _get_lock():
            _owned(data_dir)                       # records the exit, drops the stale record
        return {"ok": False, "started": False, "already_running": False, "reason": "exited",
                "exit_code": code, "pid": None, "port": port, "status": "NOT_RUNNING",
                "log_path": str(rec.log_path), "log_tail": _log_tail(rec.log_path, 15),
                "command": rec.argv,
                "message": f"ACE-Step завершился сразу после запуска (код {code}). Причина — в журнале."}
    return {"ok": True, "started": True, "already_running": False, "pid": rec.proc.pid, "port": port,
            "status": "STARTING", "log_path": str(rec.log_path), "command": rec.argv,
            "message": (f"ACE-Step запускается (pid {rec.proc.pid}). Загрузка моделей занимает 1–3 минуты, "
                        "первый запуск на новом ПК может быть дольше.")}


async def stop_service(data_dir: Path) -> dict[str, Any]:
    global _service, _last_exit
    host, port = _endpoint()
    async with _get_lock():
        rec = _owned(data_dir)
        if rec is None:
            busy = await _port_open(host, port)
            return {"ok": True, "stopped": False, "owned": False, "port": port, "port_busy": busy,
                    "message": (f"На порту {port} отвечает процесс, которого Bossman не запускал, — он не тронут."
                                if busy else "ACE-Step и так не запущен.")}
        pid = rec.proc.pid
        await asyncio.to_thread(_kill_tree, rec.proc)
        if rec.proc.poll() is None:
            return {"ok": False, "stopped": False, "owned": True, "pid": pid, "port": port,
                    "message": "Процесс не остановился; обновите состояние и повторите."}
        _service, _last_exit = None, None          # an owner STOP is not a crash
        _record_drop(data_dir)
        return {"ok": True, "stopped": True, "owned": True, "pid": pid, "port": port,
                "message": "ACE-Step остановлен."}


def _invalidate(request: Request) -> None:
    probes(request.app.state.svc).clear()


@router.post("/service/start")
async def service_start(request: Request):
    data_dir = _data_dir(request)
    if data_dir is None:
        raise HTTPException(500, {"message": "не задан каталог данных Bossman"})
    try:
        out = await start_service(data_dir)
    finally:
        _invalidate(request)
    if out.get("started"):
        await request.app.state.svc.bus.emit("music.service.started", pid=out["pid"])
    return out


@router.post("/service/stop")
async def service_stop(request: Request):
    data_dir = _data_dir(request)
    if data_dir is None:
        raise HTTPException(500, {"message": "не задан каталог данных Bossman"})
    try:
        out = await stop_service(data_dir)
    finally:
        _invalidate(request)
    if out.get("stopped"):
        await request.app.state.svc.bus.emit("music.service.stopped", pid=out["pid"])
    return out


@router.get("/service")
async def service_info(request: Request):
    """Diagnostics for the owner: what is installed, what Bossman runs, the tail of the service log."""
    data_dir = _data_dir(request)
    install = find_install()
    rec = _owned(data_dir)
    log = rec.log_path if rec else (_log_path(data_dir) if data_dir else None)
    return {"install": install, "base_url": _base(), "owned": rec is not None,
            "pid": rec.proc.pid if rec else None,
            "uptime_seconds": round(time.monotonic() - rec.started_mono, 1) if rec else None,
            "command": rec.argv if rec else None, "last_exit": _last_exit,
            "log_path": str(log) if log else None, "log_tail": _log_tail(log)}


# ------------------------------------------------------------------ generation

@router.post("/generate")
async def generate(body: MusicIn, request: Request):
    try:
        _loopback_only(_base())
    except ValueError as exc:
        raise HTTPException(409, {"message": f"Адрес ACE-Step не локальный (loopback): {exc}.",
                                  "code": "MUSIC_NOT_LOOPBACK",
                                  "hint": "Уберите BOSSMAN_ACESTEP_URL или укажите http://127.0.0.1:8001."}) from None
    payload = {
        "prompt": body.prompt,
        "lyrics": body.lyrics,
        "audio_duration": body.duration,
        "time_signature": body.time_signature,
        "thinking": body.thinking,
        "model": body.model,
    }
    if body.bpm is not None:
        payload["bpm"] = body.bpm
    if body.key_scale:
        payload["key_scale"] = body.key_scale
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            data = await _json(client, "POST", "/release_task", json=payload)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise await _unavailable(request, exc) from None
    task_id = data.get("task_id") if isinstance(data, dict) else None
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(502, "ACE-Step did not return task_id")
    await request.app.state.svc.bus.emit("music.generation.queued", task_id=task_id,
                                         model=body.model, duration=body.duration)
    return {"task_id": task_id, "status": "queued", "provider": PROVIDER}


@router.get("/tasks/{task_id}")
async def task(task_id: str, request: Request):
    try:
        _loopback_only(_base())
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            data = await _json(client, "POST", "/query_result",
                               json={"task_id_list": [task_id]})
    except (ValueError, httpx.HTTPError, RuntimeError) as exc:
        raise await _unavailable(request, exc) from None
    if not isinstance(data, list) or not data:
        raise HTTPException(502, "ACE-Step returned no task state")
    row = data[0]
    state = row.get("status")
    if state == 0:
        return {"task_id": task_id, "status": "running"}
    if state == 2:
        return {"task_id": task_id, "status": "failed"}
    if state != 1:
        raise HTTPException(502, "ACE-Step returned unknown task state")
    try:
        outputs = json.loads(row.get("result") or "[]")
    except json.JSONDecodeError:
        raise HTTPException(502, "ACE-Step result is malformed") from None
    refs = [x.get("file") for x in outputs if isinstance(x, dict) and x.get("file")]
    if not refs:
        raise HTTPException(502, "ACE-Step succeeded without audio output")
    return {"task_id": task_id, "status": "completed", "outputs": refs,
            "metadata": [x.get("metas") or {} for x in outputs if isinstance(x, dict)]}


@router.post("/tasks/{task_id}/save")
async def save(task_id: str, request: Request):
    body: dict = {}
    with contextlib.suppress(Exception):
        raw = await request.json()
        body = raw if isinstance(raw, dict) else {}
    master_preset = body.get("master")
    if master_preset is not None and master_preset not in STUDIO_MASTER_FILTERS:
        raise HTTPException(422, f"unknown master preset: {str(master_preset)[:40]}")
    target_duration = body.get("duration")
    if target_duration is not None and not (isinstance(target_duration, (int, float)) and 10 <= target_duration <= 600):
        raise HTTPException(422, "duration must be a number of seconds between 10 and 600")
    state = await task(task_id, request)
    if state.get("status") != "completed":
        raise HTTPException(409, "music task is not completed")
    ref = state["outputs"][0]
    target = urljoin(_base() + "/", ref.lstrip("/"))
    if urlsplit(target).netloc != urlsplit(_base()).netloc:
        raise HTTPException(409, "ACE-Step output escaped configured local provider")
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.get(target, headers=_headers())
            response.raise_for_status()
            payload = response.content
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"audio download failed: {type(exc).__name__}") from None
    if len(payload) < 1024:
        raise HTTPException(502, "generated audio is unexpectedly small")
    # Minimal magic verification; full ffprobe happens in installed/hardware acceptance.
    if not (payload.startswith(b"ID3") or payload[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
            or payload.startswith(b"RIFF") or payload.startswith(b"fLaC") or payload.startswith(b"OggS")):
        raise HTTPException(502, "provider output is not a recognized audio container")
    digest = hashlib.sha256(payload).hexdigest()
    root = Path(request.app.state.svc.settings.data_dir) / "music"
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".wav" if payload.startswith(b"RIFF") else ".flac" if payload.startswith(b"fLaC") else ".ogg" if payload.startswith(b"OggS") else ".mp3"
    path = root / f"{task_id}-{digest[:12]}{suffix}"
    path.write_bytes(payload)
    await request.app.state.svc.bus.emit("music.generation.saved", task_id=task_id,
                                         sha256=digest, bytes=len(payload))
    out = {"task_id": task_id, "status": "saved", "path": str(path),
           "sha256": digest, "bytes": len(payload), "provider": PROVIDER}
    if master_preset:
        try:
            out["master"] = await asyncio.to_thread(master_audio_track, path,
                                                    root / f"{task_id}-{digest[:12]}-{master_preset}.mp3",
                                                    preset=master_preset, target_duration_s=target_duration)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            # The raw track is saved either way; a failed master is reported, never silently dropped.
            out["master"] = {"status": "failed", "preset": master_preset, "error": str(exc)[:300]}
    return out


@router.get("/presets")
async def presets():
    return {"items": [
        {"id": "phonk", "label": "Phonk", "prompt": "dark aggressive phonk, distorted cowbell melody, punchy 808 bass, Memphis-inspired drums, instrumental", "bpm": 130},
        {"id": "brazilian-phonk", "label": "Brazilian Phonk", "prompt": "brazilian phonk, hype aggressive funk mandelao drums, heavily distorted 808 bass slides, bright saturated cowbell lead, chopped vocal shouts as texture, instrumental", "bpm": 130, "master": "hype"},
        {"id": "drift-phonk", "label": "Drift Phonk", "prompt": "high-energy drift phonk, saturated cowbells, hard clipped 808, fast driving drums, instrumental", "bpm": 150},
        {"id": "ultrafunk", "label": "Ultra Funk", "prompt": "ultrafunk, heavy club bass, chopped rhythmic vocal textures, aggressive electronic percussion", "bpm": 130},
        {"id": "nightcore", "label": "Nightcore", "prompt": "fast bright nightcore-inspired electronic pop, energetic drums, high-register vocal treatment", "bpm": 170},
        {"id": "electro", "label": "Electro", "prompt": "energetic electronic dance track, driving kick, bright synth hook, club arrangement", "bpm": 128},
        {"id": "cinematic-veo", "label": "Studio VEO Master", "prompt": "studio mastered cinematic electronic pop and instrumental arrangement, deep warm sub-bass, crisp de-essed vocals, wide stereo synth pads, punchy transient drums, high dynamic clarity", "bpm": 124},
    ]}


# ------------------------------------------------------------------ studio mastering DSP
STUDIO_MASTER_FILTERS: dict[str, str] = {
    "studio_veo": (
        "highpass=f=28:poles=2,"
        "equalizer=f=65:t=q:w=1.2:g=1.8,"
        "equalizer=f=280:t=q:w=1.8:g=-2.0,"
        "equalizer=f=3200:t=q:w=1.4:g=1.5,"
        "treble=g=2.0:f=10500,"
        "acompressor=threshold=-16dB:ratio=2.5:attack=15:release=180:makeup=2,"
        "extrastereo=m=1.18,"
        "loudnorm=I=-14:TP=-1.0:LRA=11"
    ),
    "vocal_cover": (
        "[0:a]highpass=f=95:poles=2,"
        "equalizer=f=260:t=q:w=1.8:g=-2.5,"
        "equalizer=f=3500:t=q:w=1.3:g=2.5,"
        "equalizer=f=6800:t=q:w=2.5:g=-3.5,"
        "acompressor=threshold=-20dB:ratio=3.5:attack=8:release=120:makeup=4,"
        "acompressor=threshold=-12dB:ratio=2.0:attack=25:release=220:makeup=1.5,"
        "aecho=0.85:0.88:35|55:0.16|0.11,"
        "treble=g=2.2:f=11000,"
        "volume=1.35[v];"
        "[1:a]highpass=f=25:poles=2,"
        "equalizer=f=2800:t=q:w=1.2:g=-2.5,"
        "volume=0.82[b];"
        "[v][b]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,"
        "acompressor=threshold=-14dB:ratio=2.0:attack=20:release=200:makeup=1.5,"
        "extrastereo=m=1.12,"
        "loudnorm=I=-14:TP=-1.0:LRA=11[out]"
    ),
    # Music 2.0 (27.10): loud club/phonk master. The chain lands NEAR the target; master_audio_track then
    # measures EBU R128 and corrects the gain, so the number returned is measured, not assumed.
    "hype": (
        "highpass=f=30:poles=2,"
        "equalizer=f=55:t=q:w=1.0:g=2.5,"
        "equalizer=f=300:t=q:w=1.6:g=-2.0,"
        "acompressor=threshold=-18dB:ratio=3:attack=10:release=150:makeup=3,"
        "loudnorm=I=-9:TP=-1.0:LRA=7"
    ),
}
MASTER_TARGET_LUFS: dict[str, float] = {"studio_veo": -14.0, "vocal_cover": -14.0, "hype": -9.0}
LUFS_TOLERANCE = 0.7          # corrective pass when the measured loudness misses the target by more than this
TRUE_PEAK_CEILING_DB = -1.0


def measure_tempo(path: Path, ffmpeg: str | None = None, lo_bpm: float = 70.0, hi_bpm: float = 180.0) -> float | None:
    """Estimate tempo (BPM) from the audio itself: spectral-flux onset envelope + autocorrelation, numpy only.
    Returns None when the signal has no usable beat (silence, too short). Half/double-tempo ambiguity is inherent."""
    import numpy as np
    ffmpeg = ffmpeg or _find_ffmpeg()
    if not ffmpeg:
        return None
    sr = 11025
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, timeout=180, check=False).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop, win = 256, 1024
    if x.size < sr * 4:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop] * np.hanning(win)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = flux - flux.mean()
    if not np.any(flux):
        return None
    ac = np.correlate(flux, flux, mode="full")[flux.size - 1:]
    fps = sr / hop
    lo, hi = int(fps * 60 / hi_bpm), int(fps * 60 / lo_bpm)
    if hi >= ac.size or lo < 1:
        return None
    best = float(lo + int(np.argmax(ac[lo:hi + 1])))
    i = int(best)
    if 0 < i < ac.size - 1:                      # parabolic refinement of the peak (sub-frame precision)
        a, b, c = ac[i - 1], ac[i], ac[i + 1]
        if (a - 2 * b + c) != 0:
            best = i + 0.5 * (a - c) / (a - 2 * b + c)
    return round(60.0 * fps / best, 1) if best > 0 else None


def measure_loudness(path: Path, ffmpeg: str | None = None) -> dict[str, float]:
    """Measure integrated loudness (LUFS), true peak (dBTP) and duration with FFmpeg ebur128."""
    ffmpeg = ffmpeg or _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден для замера громкости")
    proc = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                           "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=180, check=False)
    log = proc.stderr or ""
    summary = log.rsplit("Summary:", 1)[-1]
    i_m = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", summary)
    p_m = re.search(r"Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", summary)
    d_m = re.findall(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if not i_m:
        raise RuntimeError(f"замер громкости не удался (exit={proc.returncode})")
    peak = float("-inf") if not p_m or p_m.group(1) == "-inf" else float(p_m.group(1))
    h, m, s = d_m[-1] if d_m else ("0", "0", "0")
    return {"lufs": float(i_m.group(1)), "true_peak_dbtp": peak, "duration_s": round(int(h) * 3600 + int(m) * 60 + float(s), 2)}


def _find_ffmpeg() -> str | None:
    import shutil
    which = shutil.which("ffmpeg")
    if which:
        return which
    for candidate in (
        Path.home() / "Bossman" / "media-runtime" / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:/Users/asd/Bossman/media-runtime/ffmpeg/bin/ffmpeg.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def master_audio_track(
    input_path: Path | None = None,
    output_path: Path | None = None,
    *,
    preset: str = "studio_veo",
    vocal_path: Path | None = None,
    instrumental_path: Path | None = None,
    bitrate: str = "320k",
    also_ogg: bool = False,
    target_duration_s: float | None = None,
) -> dict[str, Any]:
    """Run local deterministic FFmpeg studio mastering (EQ, de-esser, 2-stage compression,
    vocal-pocket carving, stereo widening, and EBU R128 -14 LUFS / -1.0 dBTP loudness normalization)."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден для мастеринга аудио")
    eff_vocal = vocal_path if vocal_path is not None else (input_path if instrumental_path is not None else None)
    if eff_vocal is not None and instrumental_path is not None:
        v_path = Path(eff_vocal).resolve()
        i_path = Path(instrumental_path).resolve()
        if not v_path.is_file() or not i_path.is_file():
            raise FileNotFoundError("не найден файл вокала или инструментала")
        out = Path(output_path) if output_path else v_path.with_name(f"{v_path.stem}_mastered.mp3")
        out.parent.mkdir(parents=True, exist_ok=True)
        codec_args = ["-b:a", bitrate] if out.suffix.lower() in (".mp3", ".ogg", ".opus", ".m4a") else []
        argv = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(v_path), "-i", str(i_path),
            "-filter_complex", STUDIO_MASTER_FILTERS["vocal_cover"],
            "-map", "[out]", "-ar", "44100", *codec_args, str(out),
        ]
    else:
        if input_path is None:
            raise ValueError("укажите input_path или пару vocal_path + instrumental_path")
        src = Path(input_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"исходный аудиофайл не найден: {src}")
        chain = STUDIO_MASTER_FILTERS.get(preset) or STUDIO_MASTER_FILTERS["studio_veo"]
        out = Path(output_path) if output_path else src.with_name(f"{src.stem}_mastered{src.suffix}")
        out.parent.mkdir(parents=True, exist_ok=True)
        codec_args = ["-b:a", bitrate] if out.suffix.lower() in (".mp3", ".ogg", ".opus", ".m4a") else []
        argv = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-af", chain, "-ar", "44100", *codec_args, str(out),
        ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 1024:
        raise RuntimeError(f"ошибка мастеринга ffmpeg (exit={proc.returncode}): {(proc.stderr or '').strip()[:300]}")
    ogg_path: str | None = None
    if also_ogg and out.suffix.lower() != ".ogg":
        ogg_target = out.with_suffix(".ogg")
        ogg_proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(out), "-c:a", "libopus", "-b:a", "192k", str(ogg_target)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if ogg_proc.returncode == 0 and ogg_target.is_file():
            ogg_path = str(ogg_target)
    eff_preset = "vocal_cover" if (eff_vocal and instrumental_path) else (preset if preset in STUDIO_MASTER_FILTERS else "studio_veo")
    target = MASTER_TARGET_LUFS.get(eff_preset, -14.0)

    def _apply(af: str, tag: str) -> bool:
        fixed = out.with_name(f"{out.stem}.{tag}{out.suffix}")
        proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(out), "-af", af,
                               "-ar", "44100", *codec_args, str(fixed)], capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0 or not fixed.is_file():
            return False
        fixed.replace(out)
        return True

    if target_duration_s:
        # ACE-Step often ends the music early and pads the tail with silence (27.10: 4-8.5 s of dead air in all three
        # proof tracks). Drop the trailing silence, then cut to the requested length with a short fade-out.
        dur = float(target_duration_s)
        _apply("areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.3,areverse,"
               f"atrim=0:{dur:.2f},afade=t=out:st={max(0.0, dur - 1.5):.2f}:d=1.5", "trim")
    # Single-pass loudnorm undershoots on dense material (phonk: -10.4 for a -9 target) and its peak control is
    # sample-based (27.10 proof: +0.4 dBTP after mp3). Each pass: gain, then a 4x-oversampled limiter 1 dB under the
    # ceiling (headroom for the lossy encoder); re-measure; at most 3 passes; report what was measured.
    limit = 10 ** ((TRUE_PEAK_CEILING_DB - 1.0) / 20)
    measured = measure_loudness(out, ffmpeg)
    passes = 1
    while passes <= 3 and (abs(measured["lufs"] - target) > LUFS_TOLERANCE or measured["true_peak_dbtp"] > TRUE_PEAK_CEILING_DB + 0.1):
        gain = target - measured["lufs"] if abs(measured["lufs"] - target) > LUFS_TOLERANCE / 2 else 0.0
        if not _apply(f"volume={gain:.2f}dB,aresample=176400,alimiter=limit={limit:.4f}:level=false,aresample=44100",
                      f"fix{passes}"):
            break
        measured = measure_loudness(out, ffmpeg)
        passes += 1
    data = out.read_bytes()
    return {
        "status": "mastered",
        "preset": eff_preset,
        "output_path": str(out),
        "ogg_path": ogg_path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "target_lufs": target,
        "measured_lufs": measured["lufs"],
        "true_peak_dbtp": measured["true_peak_dbtp"],
        "duration_s": measured["duration_s"],
        "passes": passes,
        "within_tolerance": abs(measured["lufs"] - target) <= LUFS_TOLERANCE,
        "bpm_measured": measure_tempo(out, ffmpeg),
    }


FEATURE = Feature(name="music_studio", router=router)
