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
import json
import hashlib
import hmac
import base64
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import httpx
import psutil

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


# Ключ владельца в settings: постоянное решение, переживающее перезапуск и
# не требующее ритуала с переменной окружения (находка B3 cloud-QA: все девять
# приложений отвечали 409, пока владелец не выставил FLAG=1 и не перезапустил
# Command Center — а узнать это было неоткуда).
SETTING_KEY = "apps.control_enabled"
# Пин развёртывания: "on"/"off" закрепляют политику так, что API её не меняет.
# Нужен для закрытых установок, где решение принимает не тот, у кого есть UI.
LOCK_ENV = "BOSSMAN_APPS_CONTROL_LOCK"

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")
_POLICY_UNREADABLE = object()


def _env_flag() -> bool | None:
    """Прежняя переменная окружения — теперь ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ, а не
    единственный способ. None = владелец её не трогал."""
    raw = os.environ.get(FLAG, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _env_lock() -> bool | None:
    raw = os.environ.get(LOCK_ENV, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def enabled() -> bool:
    """Синхронный ответ БЕЗ базы: только окружение.

    Оставлен для путей, у которых нет `svc` (и как безопасный запас, если
    настройка не читается). Он НЕ видит постоянного решения владельца, поэтому
    все ручки и инструменты ходят через `is_enabled(svc)`."""
    lock = _env_lock()
    if lock is not None:
        return lock
    return bool(_env_flag())


async def _stored(svc) -> bool | None | object:
    """Постоянное решение владельца или None, если он его не принимал."""
    import sqlalchemy as sa
    from ..db import settings_kv
    try:
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(settings_kv.c.value_enc)
                                   .where(settings_kv.c.key == SETTING_KEY))).first()
        if row is None:
            return None
        if row[0] is None:
            return _POLICY_UNREADABLE
        raw = svc.vault.decrypt(row[0])
    except Exception:  # noqa: BLE001 — нечитаемая настройка не открывает доступ
        return _POLICY_UNREADABLE
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return _POLICY_UNREADABLE


async def policy(svc) -> dict[str, Any]:
    """Действующая политика и ОТКУДА она взялась — целиком, для владельца.

    Порядок разрешения (первое найденное побеждает):
      1. пин развёртывания `BOSSMAN_APPS_CONTROL_LOCK` — API его не меняет;
      2. постоянное решение владельца в settings — действует СРАЗУ, без
         перезапуска (в этом и была суть находки B3);
      3. прежняя переменная окружения `BOSSMAN_APPS_CONTROL_ENABLED`;
      4. выключено. Умолчание остаётся закрытым осознанно: этот модуль —
         единственное место, где ядро порождает процесс на машине владельца,
         и «по умолчанию можно» здесь означало бы «можно у всех, кто не знал».
    """
    lock = _env_lock()
    if lock is not None:
        return {"enabled": lock, "source": "deployment_lock", "locked": True,
                "can_change": False,
                "hint": f"политика закреплена переменной {LOCK_ENV}; "
                        f"через API её не изменить"}
    stored = await _stored(svc)
    if stored is _POLICY_UNREADABLE:
        return {"enabled": False, "source": "owner_setting_unreadable", "locked": False,
                "can_change": True, "hint": "saved owner policy cannot be read; app effects are denied. Set the policy explicitly again or repair local storage"}
    if stored is not None:
        return {"enabled": stored, "source": "owner_setting", "locked": False,
                "can_change": True,
                "hint": ("управление включено владельцем; действует сразу, "
                         "перезапуск не нужен") if stored else
                        ("управление выключено владельцем; включить — "
                         "PUT /api/apps/control/policy {\"enabled\": true}")}
    env = _env_flag()
    if env is not None:
        return {"enabled": env, "source": "environment", "locked": False,
                "can_change": True,
                "hint": f"значение по умолчанию взято из {FLAG}; решение владельца "
                        f"через API переопределит его без перезапуска"}
    return {"enabled": False, "source": "default", "locked": False, "can_change": True,
            "hint": "управление приложениями по умолчанию выключено; включить — "
                    "PUT /api/apps/control/policy {\"enabled\": true} "
                    "(действует сразу, перезапуск не нужен)"}


async def is_enabled(svc) -> bool:
    return bool((await policy(svc))["enabled"])


async def set_policy(svc, *, want: bool, by: str = "owner") -> dict[str, Any]:
    """Записать постоянное решение владельца. Пин развёртывания сильнее."""
    import sqlalchemy as sa
    from ..db import settings_kv
    current = await policy(svc)
    if current["locked"]:
        raise HTTPException(403, {"code": "APPS_CONTROL_LOCKED",
                                  "message": "политика закреплена развёртыванием",
                                  "hint": current["hint"]})
    enc = svc.vault.encrypt("1" if want else "0")
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == SETTING_KEY))
        await s.execute(sa.insert(settings_kv).values(key=SETTING_KEY, value_enc=enc))
        await s.commit()
    await svc.bus.emit("apps.control_policy_changed", enabled=want, by=by)
    return await policy(svc)


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


# ------------------------------------------------------------------ после перезапуска
#
# Реестр процессов живёт в памяти, а Command Center перезапускают часто. После
# перезапуска BOSSMAN забывал, что сам запустил приложение: `_owned` пуст, порт
# занят — и запуск отвечал «порт занят процессом, которого BOSSMAN не запускал»
# про свой собственный процесс, а остановка отказывалась его трогать. Приложение
# становилось и незапускаемым, и неостанавливаемым через интерфейс.
#
# Поэтому факт запуска переживает перезапуск: маленькая запись на диске.
# A PID alone is not authority. Recovery also verifies the process creation
# time, executable and full argv; psutil rechecks PID identity when signalling.

_RECORDS_DIRNAME = "apps-control"


def _record_path(app_id: str, data_dir: Path) -> Path:
    return Path(data_dir) / _RECORDS_DIRNAME / f"{app_id}.json"


def _process_namespace_verified() -> bool:
    """Some sandboxes expose host /proc beside virtual PIDs. Never signal on
    metadata from that different namespace, even if a numeric PID exists."""
    try:
        own = psutil.Process(os.getpid())
        return Path(own.exe()).resolve() == Path(sys.executable).resolve()
    except (OSError, psutil.Error):
        return False


def _record_write(rec: _Managed, data_dir: Path) -> None:
    path = _record_path(rec.app_id, data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not _process_namespace_verified():
            return
        identity = psutil.Process(rec.proc.pid)
        path.write_text(json.dumps({
            "app_id": rec.app_id, "pid": rec.proc.pid, "port": rec.port,
            "argv": list(rec.argv), "log_path": str(rec.log_path),
            "started_at": rec.started_at, "process_created": identity.create_time(),
            "process_exe": identity.exe(), "process_argv": identity.cmdline()}, ensure_ascii=False), encoding="utf-8")
    except (OSError, psutil.Error):
        pass          # запись — удобство, а не условие запуска


def _record_drop(app_id: str, data_dir: Path | None) -> None:
    if data_dir is None:
        return
    try:
        _record_path(app_id, data_dir).unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    # Windows os.kill(pid, 0) sends CTRL_C_EVENT; it is not a harmless
    # existence probe and can interrupt BCC's shared console during recovery.
    if type(pid) is not int or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except (OSError, psutil.Error):
        return False


def _recorded(app_id: str, data_dir: Path | None, port: Any) -> dict | None:
    """Наш процесс, переживший перезапуск сервера, — или None.

    Требуются ОБА признака: pid жив и порт занят. Одного pid мало (его мог
    переиспользовать кто угодно), одного занятого порта — тоже (там мог сесть
    чужой сервер). Запись, не прошедшую проверку, удаляем: устаревшая запись
    врёт не меньше, чем её отсутствие.
    """
    if data_dir is None:
        return None
    if not _process_namespace_verified():
        return None
    path = _record_path(app_id, data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    try:
        process = psutil.Process(int(pid))
        matches = (data.get("app_id") == app_id and data.get("port") == port
                   and process.create_time() == data.get("process_created")
                   and process.exe() == data.get("process_exe")
                   and process.cmdline() == data.get("process_argv")
                   and process.cmdline() == data.get("argv"))
    except (psutil.Error, ValueError, TypeError):
        matches = False
    if not (matches and _pid_alive(pid) and port_busy(port)):
        _record_drop(app_id, data_dir)
        return None
    return data


class _RecoveredProcess:
    """psutil guards signals against PID reuse using process creation time."""
    def __init__(self, pid: int):
        self._process = psutil.Process(pid)
        self.pid = pid

    def poll(self):
        try:
            return None if self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE else 0
        except psutil.NoSuchProcess:
            return 0

    def terminate(self):
        self._process.terminate()

    def kill(self):
        self._process.kill()

    def wait(self, timeout=None):
        return self._process.wait(timeout)


def _restore_process(app_id: str, data_dir: Path, port: Any) -> _Managed | None:
    prior = _recorded(app_id, data_dir, port)
    if prior is None:
        return None
    # Recheck after creating the guarded handle; never signal by a bare PID.
    process = _RecoveredProcess(int(prior["pid"]))
    if process._process.create_time() != prior["process_created"]:
        return None
    log = log_path_for(data_dir, app_id)
    started = float(prior["started_at"])
    rec = _Managed(app_id, process, list(prior["argv"]), port, log,
                   log.open("ab"), started, time.monotonic() - max(0, time.time() - started))
    _processes[app_id] = rec
    return rec


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


def _forget(rec: _Managed, data_dir: Path | None = None) -> None:
    _processes.pop(rec.app_id, None)
    _record_drop(rec.app_id, data_dir)
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


def _module_importable(app_dir: Path, module: str) -> str:
    """Проверить, что объявленный модуль запуска существует. Пустая строка — да.

    Объявление в `pyproject.toml` — это намерение, а не факт. У `social-farm`
    строка `social-farm = "social_farm.main:main"` стояла на месте, а
    `main.py` не существовал: кнопка «Запустить» порождала процесс, который
    умирал с `ModuleNotFoundError` через доли секунды. Владелец видел
    приложение, которое «не открывается», и никакой причины.

    Проверка идёт в отдельном процессе с тем же PYTHONPATH, что и запуск.
    В этом же процессе её делать нельзя: импорт чужого приложения выполнит его
    код прямо внутри Command Center.
    """
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    src = app_dir / "src"
    if src.is_dir():
        env["PYTHONPATH"] = str(src)
    try:
        probe = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 3)",
             module],
            cwd=str(app_dir), env=env, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"не удалось проверить модуль запуска {module}: {type(exc).__name__}"
    if probe.returncode == 0:
        return ""
    if probe.returncode == 3:
        return (f"модуль запуска {module} объявлен, но не существует — "
                f"процесс умрёт сразу после старта")
    detail = (probe.stderr or probe.stdout or "").strip().splitlines()
    return (f"модуль запуска {module} не импортируется: "
            f"{detail[-1] if detail else f'код {probe.returncode}'}")


def command_for(app_id: str) -> dict[str, Any]:
    """Как именно мы запустим приложение. Отдаётся и в UI — как запасной путь,
    если владелец хочет сделать это руками."""
    app_dir, card = _require_app(app_id)
    raw = apps_feature._load(app_dir / "app.manifest.yaml") or {}
    module = _entry_module(app_dir, raw)
    if not module:
        # Разные причины требуют разных действий владельца, и «нет точки
        # входа» у приложения, которого просто нет на диске, отправляет его
        # искать pyproject там, где нет ни строчки кода.
        has_code = any(app_dir.glob("*.py")) or any(
            root.is_dir() and any(root.rglob("*.py"))
            for root in (app_dir / "src", app_dir / app_id.replace("-", "_")))
        problem = ("приложение объявлено манифестом, но его кода нет в этом "
                   "репозитории — запускать нечего"
                   if not has_code else
                   "не удалось определить модуль запуска: в pyproject.toml "
                   "приложения нет [project.scripts], а пакета с __main__.py нет")
        return {"module": "", "argv": [], "cwd": str(app_dir), "manual": "",
                "problem": problem}
    # sys.executable — тот же интерпретатор, что и у ядра: приложение не должно
    # зависеть от того, что окажется словом `python` в PATH службы.
    argv = [sys.executable, "-m", module, "serve"]
    # В подсказке человеку — `python`, а не абсолютный путь: её набирают руками.
    prefix = "PYTHONPATH=src " if (app_dir / "src").is_dir() else ""
    manual = (f"cd apps/{app_id} && {prefix}python -m {module} serve"
              if (app_dir / "src").is_dir() else f"python -m {module} serve")
    # Несуществующий модуль называется ЗДЕСЬ, до нажатия кнопки. Иначе
    # владелец получает «приложение не открывается» без единой причины.
    problem = _module_importable(app_dir, module)
    return {"module": module, "argv": argv, "cwd": str(app_dir), "manual": manual,
            "problem": problem}


def _app_token(data_dir: Path, app_id: str) -> str:
    path = Path(data_dir) / "app-data" / app_id / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink():
            raise ValueError("app token must not be a symlink")
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError("app token is invalid; repair the app data directory")
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def _child_env(app_dir: Path, port: int | None,
               data_dir: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    # Без этого вывод приложения застревает в буфере, и хвост журнала оказывается
    # пустым ровно тогда, когда он нужен — когда приложение не поднялось.
    env["PYTHONUNBUFFERED"] = "1"
    # Живой прогон владельца (Windows, 20260906, H-CLUSTER): приложение с
    # кириллическим выводом падало с UnicodeEncodeError и отдавало exit 1 —
    # ядро показывало «завершилось с ошибкой» вместо «не поднялось». Причина не
    # в приложении: мы фильтруем env, дочерний python не наследует ничего про
    # кодировку и берёт локаль хоста (cp1252), а журнал мы читаем как UTF-8.
    # Кодировка дочернего процесса — часть контракта запуска, а не его дело.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(str(r) for r in _package_roots(app_dir))
    if data_dir is not None:
        env["BOSSMAN_APPS_DATA"] = str(Path(data_dir).resolve() / "app-data")
        if app_dir.name == "file-commander-mini":
            workspace = Path(data_dir).resolve() / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            env["FILE_COMMANDER_ROOTS"] = os.environ.get("FILE_COMMANDER_ROOTS") or str(workspace)
            # Even a broad owner root may not grant access to Bossman's secrets.
            env["FILE_COMMANDER_PROTECTED_ROOTS"] = os.pathsep.join(
                str(Path(data_dir).resolve() / name)
                for name in ("app-data", "apps-control", "secrets", "vault", "bcc.db", "bcc.db-wal", "bcc.db-shm", "token", "vault.key"))
            env["BOSSMAN_APP_TOKEN"] = _app_token(data_dir, app_dir.name)
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
        if live.get("status") == "LIVE":
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
    runtime_dir = app_dir
    if not (app_dir / "src").is_dir():
        runtime_dir = Path(data_dir) / "app-runtime" / app_id
        runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(                   # noqa: S603 — argv-список, без shell
            command["argv"], cwd=str(runtime_dir),
            env=_child_env(app_dir, card.get("port"), data_dir),
            stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            close_fds=True, **extra)
    except (OSError, ValueError):
        handle.close()
        raise
    rec = _Managed(app_id=app_id, proc=proc, argv=list(command["argv"]),
                   port=card.get("port"), log_path=path, log_file=handle,
                   started_at=time.time(), started_mono=time.monotonic())
    _processes[app_id] = rec
    _record_write(rec, data_dir)
    return rec


async def start_app(app_id: str, data_dir: Path,
                    timeout: float | None = None, *, svc=None) -> dict[str, Any]:
    app_dir, card = _require_app(app_id)
    port = card.get("port")
    async with _lock:
        if svc is not None:
            await _require_enabled(svc)
        rec = _owned(app_id)
        if rec is not None:
            ready = (await apps_feature._probe(card)).get("status") == "LIVE"
            return {"ok": ready, "app_id": app_id, "started": False, "already_running": True,
                    "ready": ready, "pid": rec.proc.pid, "port": port,
                    "message": f"{card['name']} уже запущено (pid {rec.proc.pid})",
                    "log_path": str(rec.log_path), "log_tail": [],
                    "command": rec.argv}
        restored = _restore_process(app_id, data_dir, port)
        if restored is not None:
            # Наш же процесс, переживший перезапуск сервера. Раньше здесь
            # выдавалось «процесс, которого BOSSMAN не запускал» — про
            # собственное приложение, и запустить его больше было нельзя.
            ready = (await apps_feature._probe(card)).get("status") == "LIVE"
            return {"ok": ready, "app_id": app_id, "started": False,
                    "already_running": True, "ready": ready, "recovered": True,
                    "pid": restored.proc.pid, "port": port,
                    "message": f"{card['name']} уже работает (pid {restored.proc.pid}): "
                               f"его запустил BOSSMAN до перезапуска сервера",
                    "log_path": str(restored.log_path), "log_tail": [],
                    "command": restored.argv}
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
        if svc is not None:
            await _require_enabled(svc)
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
            _forget(rec, data_dir)
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


async def stop_app(app_id: str, data_dir: Path | None = None, *, svc=None) -> dict[str, Any]:
    _, card = _require_app(app_id)
    port = card.get("port")
    async with _lock:
        if svc is not None:
            await _require_enabled(svc)
        rec = _owned(app_id) or (_restore_process(app_id, data_dir, port) if data_dir is not None else None)
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
        if code is None:
            return {"ok": False, "app_id": app_id, "stopped": False, "owned": True,
                    "pid": pid, "port": port, "message": "process did not stop; refresh state and retry stop"}
        _forget(rec, data_dir)
        return {"ok": True, "app_id": app_id, "stopped": True, "owned": True,
                "pid": pid, "port": port, "signal": signal_used, "exit_code": code,
                "message": f"{card['name']} остановлено ({signal_used})"}


def process_info(app_id: str, data_dir: Path) -> dict[str, Any]:
    _, card = _require_app(app_id)
    port = card.get("port")
    rec = _owned(app_id) or _restore_process(app_id, data_dir, port)
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


async def _require_enabled(svc) -> None:
    """Отказ ПОЛИТИКИ, а не конфликт состояния.

    Прежде это был 409 — тот же код, что у «порт занят чужим процессом» и «в
    манифесте нет console script». Cloud-QA прогон увидел девять одинаковых
    409 на девяти приложениях и не мог отличить «владелец не разрешал» от
    «что-то сломалось». Теперь политика отвечает 403 с машиночитаемым `code`,
    а 409 остаётся тем, чем и был: конфликтом реального состояния."""
    current = await policy(svc)
    if not current["enabled"]:
        raise HTTPException(403, {
            "code": "APPS_CONTROL_DISABLED",
            "message": "управление приложениями выключено политикой владельца",
            "policy": current,
            "hint": current["hint"]})


@router.get("/apps/control/policy")
async def get_control_policy(request: Request) -> dict:
    """Действующая политика и её источник. Читается всегда — владелец должен
    видеть, ПОЧЕМУ кнопка не работает, не заглядывая в переменные окружения."""
    return await policy(request.app.state.svc)


@router.put("/apps/control/policy")
async def put_control_policy(request: Request) -> dict:
    """Постоянное решение владельца: действует сразу, переживает перезапуск.

    Это и есть закрытие B3 — управление приложениями перестаёт зависеть от
    незадокументированного ритуала «выставить переменную и перезапустить»."""
    svc = request.app.state.svc
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        raise HTTPException(422, {"code": "APPS_CONTROL_BAD_REQUEST",
                                  "message": "нужно {\"enabled\": true|false}"})
    return await set_policy(svc, want=body["enabled"], by=str(body.get("by") or "owner"))


@router.post("/apps/{app_id}/start")
async def start(app_id: str, request: Request) -> dict:
    svc = request.app.state.svc
    await _require_enabled(svc)
    return await start_app(app_id, svc.settings.data_dir, svc=svc)


@router.post("/apps/{app_id}/stop")
async def stop(app_id: str, request: Request) -> dict:
    svc = request.app.state.svc
    await _require_enabled(svc)
    return await stop_app(app_id, svc.settings.data_dir, svc=svc)


@router.get("/apps/{app_id}/process")
async def process(app_id: str, request: Request) -> dict:
    """Чтение состояния политики не требует: «выключено» — тоже ответ, и человек
    должен видеть его до того, как нажмёт кнопку."""
    svc = request.app.state.svc
    info = process_info(app_id, svc.settings.data_dir)
    current = await policy(svc)
    # `enabled` из process_info видит только окружение; действующая политика
    # знает и о решении владельца — наружу уходит именно она.
    info["enabled"] = current["enabled"]
    info["control_policy"] = current
    return info


_FILE_GET = {"", "health", "capabilities", "metrics", "api/roots", "api/rules", "api/audit", "api/files/batches"}
_FILE_POST = {"api/files/scan", "api/files/duplicates", "api/files/organize-plan",
              "api/files/apply", "api/files/cleanup-summary", "api/files/rename-plan",
              "api/files/rule-plan", "api/files/project-groups", "api/rules"}
_PROXY_MAX_BYTES = 8 * 1024 * 1024


@router.api_route("/apps/file-commander-mini/view/{app_path:path}", methods=["GET", "POST"])
async def file_commander_view(app_path: str, request: Request) -> Response:
    """Only File Commander's existing operations, under BCC session auth.

    No arbitrary URL, redirects, headers, credentials or app identifier come
    from the browser. UI bytes come from the reviewed package, never a port.
    Requests and responses are MAC-bound; no bearer secret crosses a socket.
    """
    valid = (app_path in _FILE_GET if request.method == "GET" else
             app_path in _FILE_POST or bool(re.fullmatch(r"api/files/undo/[a-f0-9-]{36}", app_path)))
    if not valid:
        raise HTTPException(404, "File Commander operation not found")
    svc = request.app.state.svc
    if request.method != "GET":
        await _require_enabled(svc)
    app_dir, card = _require_app("file-commander-mini")
    if not app_path:
        path = app_dir / "ui.html"
        if not path.is_file():
            path = app_dir / "src" / "file_commander_mini" / "ui.html"
        if not path.is_file():
            raise HTTPException(503, "File Commander packaged UI is missing; repair the installation")
        html = path.read_text(encoding="utf-8")
        script = re.search(r"<script>([\s\S]+?)</script>", html)
        if script is None:
            raise HTTPException(503, "File Commander packaged UI is invalid")
        digest = base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode()
        return Response(html, media_type="text/html", headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'sha256-" + digest + "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'self'",
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    if not card.get("port"):
        raise HTTPException(409, "File Commander has no configured port")
    payload = bytearray()
    async for part in request.stream():
        payload.extend(part)
        if len(payload) > 1024 * 1024:
            raise HTTPException(413, "File Commander request is too large")
    token = _app_token(svc.settings.data_dir, "file-commander-mini")
    nonce, stamp = secrets.token_hex(32), str(int(time.time()))
    target = "/" + app_path
    if request.url.query:
        target += "?" + request.url.query
    url = httpx.URL(f"http://127.0.0.1:{card['port']}" + target)
    target = url.raw_path.decode("ascii")
    message = "\n".join(("request", request.method, target, stamp, nonce, hashlib.sha256(payload).hexdigest()))
    signature = hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()
    if request.method != "GET":
        await _require_enabled(svc)
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            async with client.stream(
                request.method, url, content=bytes(payload), headers={
                    "X-Bossman-App-Signature": signature, "X-Bossman-App-Timestamp": stamp,
                    "X-Bossman-App-Nonce": nonce, "Content-Type": "application/json"}) as upstream:
                body = bytearray()
                async for part in upstream.aiter_bytes():
                    body.extend(part)
                    if len(body) > _PROXY_MAX_BYTES:
                        raise HTTPException(502, "File Commander response exceeded its limit")
                if 300 <= upstream.status_code < 400:
                    raise HTTPException(502, "Unexpected File Commander redirect")
                proof = "\n".join(("response", nonce, str(upstream.status_code), hashlib.sha256(body).hexdigest()))
                expected = hmac.new(token.encode(), proof.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(upstream.headers.get("X-Bossman-App-Response", ""), expected):
                    raise HTTPException(502, {"code": "FILE_COMMANDER_IDENTITY_UNVERIFIED",
                                              "message": "File Commander response identity could not be verified; no automatic retry was attempted"})
                return Response(bytes(body), status_code=upstream.status_code,
                                media_type="application/json",
                                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except httpx.HTTPError as exc:
        raise HTTPException(502, {"code": "FILE_COMMANDER_UNAVAILABLE",
                                  "message": "File Commander is not responding; refresh Apps and start it again"}) from exc


async def _tick(svc) -> None:
    """Пока приложение работает, его журнал растёт. Здесь он остаётся в рамках,
    а записи об умерших процессах перестают врать про «запущено»."""
    for app_id in list(_processes):
        rec = _owned(app_id)
        if rec is not None:
            trim_log(rec.log_path)


FEATURE = Feature(name="apps_control", router=router, tick=_tick, tick_seconds=30.0)
