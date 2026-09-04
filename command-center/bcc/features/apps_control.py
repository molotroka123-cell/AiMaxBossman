"""Запуск и остановка приложений: единственное место, где ядро порождает процесс.

До этого модуля лаунчер умел только смотреть. Владелец видел «Остановлено» и
подсказку `cd apps/<id> && <id> serve` — команду, которой у него нет: консольные
скрипты приложений не установлены, поэтому подсказка не работала ни разу. Кнопки
не было вовсе, и «включить приложение через дашборд» было невозможно.

Здесь появляется настоящая кнопка. Всё остальное в файле — ограничения, потому
что цена ошибки тут не «некрасивая карточка», а чужой процесс в системе
владельца:

* флаг BOSSMAN_APPS_CONTROL_ENABLED выключен по умолчанию. Пока владелец сам не
  разрешил, ручки, меняющие состояние, отвечают отказом и НИЧЕГО не порождают;
* запускается только то, что найдено среди манифестов. Идентификатор из запроса
  используется как ключ словаря обнаруженных приложений и никогда не попадает в
  путь — поэтому `..`, абсолютный путь и разделители не открывают ничего нового:
  такого ключа просто нет;
* команда собирается из манифеста и раскладки пакета, argv — список, shell нет.
  Ни один символ пользовательского ввода в argv не попадает;
* один процесс на приложение. Повторный запуск не плодит второй, а честно
  говорит «уже запущено». Занятый порт, за которым стоит НЕ наш процесс, — чужая
  территория: мы туда не лезем и не выдаём его за свой;
* остановка гасит только процесс из нашего реестра: сначала terminate, потом,
  если не понял, kill. Процесс, которого мы не запускали, не трогаем никогда;
* дочернему процессу передаётся минимальное окружение — секреты ядра в него не
  утекают, в argv их нет по построению, а хвост журнала перед отдачей проходит
  через маскирование;
* вывод процесса пишется в data_dir/apps/<id>.log ограниченного размера. Если
  приложение не поднялось за отведённое время, ручка говорит об этом прямо и
  показывает последние строки его вывода — молчание здесь худший из ответов.
"""
from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import Feature
from . import apps as apps_feature

FLAG = "BOSSMAN_APPS_CONTROL_ENABLED"

READY_TIMEOUT = 25.0        # uvicorn на холодном импорте поднимается небыстро
READY_INTERVAL = 0.3
SETTLE_SECONDS = 0.6        # приложению без порта проверять готовность нечем
STOP_GRACE = 5.0            # столько ждём мягкого выхода, прежде чем kill
STOP_POLL = 0.1
PORT_PROBE_TIMEOUT = 0.35
LOG_MAX_BYTES = 256 * 1024
LOG_TAIL_LINES = 40
LOG_TAIL_BYTES = 16 * 1024

# Окружение дочернего процесса собирается из этого списка, а не наследуется
# целиком: в окружении ядра лежат ключи провайдеров и токен UI, и приложению
# они не нужны. Прокси-переменных здесь тоже нет — по той же причине, по
# которой их не видит _probe: сосед на 127.0.0.1 не ходит через прокси.
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
            "SYSTEMROOT", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ------------------------------------------------------------------ реестр процессов

@dataclass
class _Managed:
    """Процесс, который запустили МЫ. Всё остальное на этой машине — не наше."""
    app_id: str
    proc: subprocess.Popen
    argv: list[str]
    port: int | None
    log_path: Path
    log_file: Any
    started_at: float          # epoch, для показа человеку
    started_mono: float        # monotonic, для аптайма


_processes: dict[str, _Managed] = {}
_lock = asyncio.Lock()         # старт и остановка — критическая секция на весь модуль


def _owned(app_id: str) -> _Managed | None:
    """Живая запись о нашем процессе. Умерший процесс перестаёт быть нашим.

    Запись о процессе, которого больше нет, — это ложь в чистом виде: по ней
    остановка «гасила» бы уже свободный pid, а порт при этом мог занять кто
    угодно другой.
    """
    rec = _processes.get(app_id)
    if rec is None:
        return None
    if rec.proc.poll() is not None:
        _forget(rec)
        return None
    return rec


def _forget(rec: _Managed) -> None:
    _processes.pop(rec.app_id, None)
    try:
        rec.log_file.close()
    except OSError:
        pass


# ------------------------------------------------------------------ поиск приложения

def known_app_dirs() -> dict[str, Path]:
    """id → каталог. Ключи берутся из манифестов, а не из запроса.

    Каталог должен называться так же, как id в манифесте: иначе непонятно, что
    считать приложением, а гадать в модуле, который порождает процессы, нельзя.
    """
    out: dict[str, Path] = {}
    for path in apps_feature._manifest_files():
        raw = apps_feature._load(path)
        if not raw or not raw.get("id"):
            continue
        app_id = str(raw["id"])
        if app_id != path.parent.name:
            continue
        out[app_id] = path.parent
    return out


def find_app_dir(app_id: str) -> Path | None:
    """Каталог приложения или None. Обход каталогов невозможен по построению:
    здесь только поиск по словарю, никакой склейки путей с вводом."""
    if not isinstance(app_id, str) or not app_id:
        return None
    return known_app_dirs().get(app_id)


def _launch_card(raw: dict[str, Any]) -> dict[str, Any]:
    """Паспорт приложения в объёме, нужном для запуска: имя, порт, адрес здоровья.

    Полную карточку строит apps.py для интерфейса, и она привязана к
    расположению репозитория. Запуску это не нужно, а лишняя привязка означала
    бы, что модуль ломается там, где каталог приложений лежит не там, где ждали.
    Разбор адреса здоровья берётся из общего с apps.py помощника: два ответа на
    вопрос «куда стучаться» разошлись бы в первый же день.
    """
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    calls = apps_feature._http_calls(raw)
    port = raw.get("default_port")
    return {
        "id": str(raw["id"]),
        "name": str(raw.get("name") or raw["id"]),
        "port": int(port) if isinstance(port, int) else None,
        "health_path": str(ui.get("health_path")
                           or calls.get("health", "").split(" ")[-1] or "/health"),
        "metrics_path": "",          # готовность решает здоровье, метрики тут ни при чём
    }


def _require_app(app_id: str) -> tuple[Path, dict[str, Any]]:
    app_dir = find_app_dir(app_id)
    if app_dir is None:
        raise HTTPException(404, {
            "message": f"приложение {app_id!r} не найдено среди манифестов",
            "hint": "запускать можно только то, у чего есть apps/<id>/app.manifest.yaml"})
    raw = apps_feature._load(app_dir / "app.manifest.yaml")
    if not raw or not raw.get("id"):
        raise HTTPException(404, {"message": f"манифест приложения {app_id!r} не читается"})
    return app_dir, _launch_card(raw)


# ------------------------------------------------------------------ команда запуска

def _pyproject_scripts(app_dir: Path) -> dict[str, str]:
    """[project.scripts] приложения: имя команды → 'модуль:функция'."""
    path = app_dir / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}
    scripts = (data.get("project") or {}).get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items()}


def _package_roots(app_dir: Path) -> list[Path]:
    """Куда смотреть импорту. Раскладка src/ — та причина, по которой
    `python -m ...` у владельца не работает без подсказки: пакет лежит в src,
    а туда никто не смотрит, пока приложение не установлено."""
    roots: list[Path] = []
    src = app_dir / "src"
    if src.is_dir():
        roots.append(src)
    roots.append(app_dir)
    return roots


def _entry_module(app_dir: Path, raw: dict[str, Any]) -> str | None:
    """Модуль запуска: манифест называет консольную команду, pyproject —
    во что она разворачивается. Ввод пользователя сюда не приходит."""
    entrypoints = raw.get("entrypoints") if isinstance(raw.get("entrypoints"), dict) else {}
    scripts = _pyproject_scripts(app_dir)
    app_id = str(raw.get("id") or "")
    target = None
    cli = entrypoints.get("cli")
    if isinstance(cli, str) and cli in scripts:
        target = scripts[cli]
    elif app_id in scripts:
        target = scripts[app_id]
    elif len(scripts) == 1:
        target = next(iter(scripts.values()))
    if target:
        module = target.split(":")[0].strip()
        if module:
            return module
    # запасной путь: пакет с __main__.py, названный как id
    package = app_id.replace("-", "_")
    if package:
        for root in _package_roots(app_dir):
            if (root / package / "__main__.py").exists():
                return package
    return None


def command_for(app_id: str) -> dict[str, Any]:
    """Как именно мы запустим приложение. Отдаётся и в UI — как запасной путь,
    если владелец хочет сделать это руками."""
    app_dir, card = _require_app(app_id)
    raw = apps_feature._load(app_dir / "app.manifest.yaml") or {}
    module = _entry_module(app_dir, raw)
    if not module:
        return {"module": "", "argv": [], "cwd": str(app_dir), "manual": "",
                "problem": "не удалось определить модуль запуска: в pyproject.toml "
                           "приложения нет [project.scripts], а пакета с __main__.py нет"}
    # sys.executable — тот же интерпретатор, что и у ядра: приложение не должно
    # зависеть от того, что окажется словом `python` в PATH службы.
    argv = [sys.executable, "-m", module, "serve"]
    # В подсказке человеку — `python`, а не абсолютный путь: её набирают руками.
    prefix = "PYTHONPATH=src " if (app_dir / "src").is_dir() else ""
    manual = f"cd apps/{app_id} && {prefix}python -m {module} serve"
    return {"module": module, "argv": argv, "cwd": str(app_dir), "manual": manual,
            "problem": ""}


def _child_env(app_dir: Path, port: int | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    # Без этого вывод приложения застревает в буфере, и хвост журнала оказывается
    # пустым ровно тогда, когда он нужен — когда приложение не поднялось.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(str(r) for r in _package_roots(app_dir))
    if port:
        env["APP_PORT"] = str(port)
        env["PORT"] = str(port)
    return env


# ------------------------------------------------------------------ журнал

def log_path_for(data_dir: Path, app_id: str) -> Path:
    directory = Path(data_dir) / "apps"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{app_id}.log"


def trim_log(path: Path) -> None:
    """Держать журнал в рамках, переписывая файл НА МЕСТЕ.

    Подменять файл новым (os.replace) нельзя: у запущенного приложения открыт
    дескриптор, оно продолжило бы писать в отвязанный inode — и вывод исчез бы
    незаметно. Дописывание идёт в режиме append, поэтому после усечения
    следующая строка ляжет в новый конец файла.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= LOG_MAX_BYTES:
        return
    keep = LOG_MAX_BYTES // 2
    try:
        with path.open("r+b") as fh:
            fh.seek(size - keep)
            tail = fh.read()
            fh.seek(0)
            fh.write("[bcc] ... начало журнала отброшено ...\n".encode() + tail)
            fh.truncate()
    except OSError:
        pass


_SECRET_KEY_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|secret|token|password|passwd|authorization|bearer)"
    r"\b\s*[:=]\s*\S+")
_SECRET_VALUE_RE = re.compile(r"\b(sk|xoxb|ghp|gho)-[A-Za-z0-9_\-]{6,}")


def redact(line: str) -> str:
    """Приложение может напечатать свой ключ в первой же строке ошибки. Ручка
    отдаёт хвост журнала в браузер, поэтому маскируем на выходе, а не надеемся."""
    line = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}=***", line)
    return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}-***", line)


def log_tail(path: Path, lines: int = LOG_TAIL_LINES) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    rows = [row.rstrip() for row in text.splitlines() if row.strip()]
    return [redact(row) for row in rows[-lines:]]


# ------------------------------------------------------------------ порт

def port_busy(port: int | None) -> bool:
    """Кто-то слушает порт приложения. ЧЕЙ это процесс — отдельный вопрос,
    и ответ на него даёт только наш реестр."""
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=PORT_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------ запуск

async def _wait_ready(card: dict[str, Any], proc: subprocess.Popen,
                      timeout: float) -> tuple[str, int | None]:
    """'ready' | 'exited' | 'timeout' | 'no_probe'. Ожидание конечное: молча
    ждать вечно — тот же отказ, только без объяснения."""
    if not card.get("port"):
        await asyncio.sleep(SETTLE_SECONDS)
        code = proc.poll()
        return ("exited", code) if code is not None else ("no_probe", None)
    deadline = time.monotonic() + timeout
    while True:
        code = proc.poll()
        if code is not None:
            return "exited", code
        live = await apps_feature._probe(card)     # тот же приём: httpx, trust_env=False
        if live.get("reachable"):
            return "ready", None
        if time.monotonic() >= deadline:
            return "timeout", None
        await asyncio.sleep(READY_INTERVAL)


def _spawn(app_id: str, app_dir: Path, card: dict[str, Any],
           command: dict[str, Any], data_dir: Path) -> _Managed:
    path = log_path_for(data_dir, app_id)
    trim_log(path)
    handle = path.open("ab")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    # argv собран нами и секретов не содержит, поэтому его можно записать —
    # это единственный способ потом понять, чем именно приложение запускали.
    handle.write(f"\n[bcc] {stamp} запуск: {' '.join(command['argv'])}\n".encode())
    handle.flush()
    extra: dict[str, Any] = {}
    if os.name == "posix":
        # Своя сессия: Ctrl-C в терминале ядра не должен гасить приложения
        # владельца, а наш terminate обязан бить точно в этот процесс.
        extra["start_new_session"] = True
    proc = subprocess.Popen(                       # noqa: S603 — argv-список, без shell
        command["argv"],
        cwd=str(app_dir),
        env=_child_env(app_dir, card.get("port")),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        **extra,
    )
    rec = _Managed(app_id=app_id, proc=proc, argv=list(command["argv"]),
                   port=card.get("port"), log_path=path, log_file=handle,
                   started_at=time.time(), started_mono=time.monotonic())
    _processes[app_id] = rec
    return rec


async def start_app(app_id: str, data_dir: Path,
                    timeout: float | None = None) -> dict[str, Any]:
    app_dir, card = _require_app(app_id)
    port = card.get("port")
    async with _lock:
        rec = _owned(app_id)
        if rec is not None:
            return {"ok": True, "app_id": app_id, "started": False, "already_running": True,
                    "ready": True, "pid": rec.proc.pid, "port": port,
                    "message": f"{card['name']} уже запущено (pid {rec.proc.pid})",
                    "log_path": str(rec.log_path), "log_tail": [],
                    "command": rec.argv}
        if port_busy(port):
            # Порт занят, а в реестре пусто — значит, сервер там не наш. Ни
            # запускать второй, ни присваивать чужой мы не имеем права.
            raise HTTPException(409, {
                "message": f"порт {port} уже занят процессом, которого BOSSMAN не запускал",
                "hint": "остановите его сами или освободите порт — чужой процесс мы не трогаем"})
        command = command_for(app_id)
        if command["problem"]:
            raise HTTPException(409, {"message": command["problem"],
                                      "hint": "нужен [project.scripts] в pyproject.toml приложения"})
        rec = _spawn(app_id, app_dir, card, command, data_dir)

    outcome, code = await _wait_ready(card, rec.proc, READY_TIMEOUT if timeout is None else timeout)
    base = {"app_id": app_id, "port": port, "pid": rec.proc.pid,
            "log_path": str(rec.log_path), "command": rec.argv}
    if outcome == "ready":
        return {**base, "ok": True, "started": True, "already_running": False, "ready": True,
                "message": f"{card['name']} запущено и отвечает на порту {port}",
                "log_tail": []}
    if outcome == "no_probe":
        return {**base, "ok": True, "started": True, "already_running": False, "ready": False,
                "message": f"{card['name']} запущено (pid {rec.proc.pid}), но проверить "
                           f"готовность нечем: в манифесте нет default_port",
                "log_tail": log_tail(rec.log_path)}
    if outcome == "exited":
        async with _lock:
            _forget(rec)
        return {**base, "ok": False, "started": False, "already_running": False, "ready": False,
                "reason": "exited", "exit_code": code, "pid": None,
                "message": f"{card['name']} завершилось сразу после запуска (код {code})",
                "log_tail": log_tail(rec.log_path)}
    return {**base, "ok": False, "started": True, "already_running": False, "ready": False,
            "reason": "not_ready",
            "message": f"{card['name']} запущено (pid {rec.proc.pid}), но за "
                       f"{READY_TIMEOUT if timeout is None else timeout:.0f} с не ответило "
                       f"на порту {port}",
            "log_tail": log_tail(rec.log_path)}


async def stop_app(app_id: str) -> dict[str, Any]:
    _, card = _require_app(app_id)
    port = card.get("port")
    async with _lock:
        rec = _owned(app_id)
        if rec is None:
            busy = port_busy(port)
            message = (f"на порту {port} отвечает процесс, которого BOSSMAN не запускал — "
                       f"он не тронут") if busy else f"{card['name']} и так не запущено"
            return {"ok": True, "app_id": app_id, "stopped": False, "owned": False,
                    "port": port, "port_busy": busy, "message": message}
        pid = rec.proc.pid
        rec.proc.terminate()
        signal_used = "terminate"
        deadline = time.monotonic() + STOP_GRACE
        while rec.proc.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(STOP_POLL)
        if rec.proc.poll() is None:
            # Мягкий сигнал не понят. Убиваем — но по-прежнему только СВОЙ pid.
            rec.proc.kill()
            signal_used = "kill"
            deadline = time.monotonic() + STOP_GRACE
            while rec.proc.poll() is None and time.monotonic() < deadline:
                await asyncio.sleep(STOP_POLL)
        code = rec.proc.poll()
        _forget(rec)
        return {"ok": True, "app_id": app_id, "stopped": True, "owned": True,
                "pid": pid, "port": port, "signal": signal_used, "exit_code": code,
                "message": f"{card['name']} остановлено ({signal_used})"}


def process_info(app_id: str, data_dir: Path) -> dict[str, Any]:
    _, card = _require_app(app_id)
    port = card.get("port")
    rec = _owned(app_id)
    path = rec.log_path if rec else log_path_for(data_dir, app_id)
    return {"app_id": app_id, "enabled": enabled(), "owned": rec is not None,
            "running": rec is not None, "pid": rec.proc.pid if rec else None,
            "started_at": rec.started_at if rec else None,
            "uptime_seconds": round(time.monotonic() - rec.started_mono, 1) if rec else None,
            "port": port, "port_busy": port_busy(port),
            "command": rec.argv if rec else command_for(app_id)["argv"],
            "manual_command": command_for(app_id)["manual"],
            "log_path": str(path), "log_tail": log_tail(path)}


# ------------------------------------------------------------------ HTTP

router = APIRouter(tags=["apps"])


def _require_flag() -> None:
    if not enabled():
        raise HTTPException(409, {
            "message": "управление приложениями выключено",
            "hint": f"чтобы BOSSMAN мог запускать приложения, установите {FLAG}=1 "
                    f"и перезапустите Command Center"})


@router.post("/apps/{app_id}/start")
async def start(app_id: str, request: Request) -> dict:
    _require_flag()
    svc = request.app.state.svc
    return await start_app(app_id, svc.settings.data_dir)


@router.post("/apps/{app_id}/stop")
async def stop(app_id: str, request: Request) -> dict:
    _require_flag()
    return await stop_app(app_id)


@router.get("/apps/{app_id}/process")
async def process(app_id: str, request: Request) -> dict:
    """Чтение состояния флага не требует: «выключено» — тоже ответ, и человек
    должен видеть его до того, как нажмёт кнопку."""
    svc = request.app.state.svc
    return process_info(app_id, svc.settings.data_dir)


async def _tick(svc) -> None:
    """Пока приложение работает, его журнал растёт. Здесь он остаётся в рамках,
    а записи об умерших процессах перестают врать про «запущено»."""
    for app_id in list(_processes):
        rec = _owned(app_id)
        if rec is not None:
            trim_log(rec.log_path)


FEATURE = Feature(name="apps_control", router=router, tick=_tick, tick_seconds=30.0)
