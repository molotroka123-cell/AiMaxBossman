"""«Отправить отчёт о сбое» — один архив вместо ручного сбора логов, флаг OFF.

BOSSMAN_DIAG_BUNDLE_ENABLED=1 включает сборку; иначе POST отказывает и в
data_dir не появляется ни одного файла — поведение приложения ровно такое же,
как без этого модуля.

Зачем модуль вообще: когда окно не открылось, владелец собирал логи руками —
что-то забывал, что-то присылал испорченным (файл менялся прогоном тестов уже
после сбоя). Одна команда снимает срез сразу и целиком, поэтому присланное
описывает именно тот момент.

Что попадает в архив (всё — из ``settings.data_dir``, ничего из репозитория):

  * ``desktop-run.log`` и ``desktop-console.log`` — ХВОСТ последних килобайт,
    а не файл целиком: интересен конец, а лог живёт месяцами;
  * ``desktop.lock`` — содержимое и живость записанного там pid (ответ на
    «приложение уже запущено или замок остался от умершего процесса»);
  * последние события из таблицы ``events``;
  * версия приложения, версия python, платформа, имя ОС;
  * настроенные адрес и порт + проверка, отвечает ли на них хоть что-нибудь;
  * перечень файлов data_dir с размерами — ИМЕНА и РАЗМЕРЫ, не содержимое.

Чего в архиве нет и не будет (главное свойство, см. ``EXCLUDED``): файла
токена и самого токена в любом виде, ``secret.key``, содержимого ``.env``,
ключей провайдеров, базы данных целиком. Мало не класть секретные файлы —
секрет попадает в архив ЧЕРЕЗ логи и события, поэтому каждая текстовая часть
перед упаковкой проходит ``scrub_text``: точные известные значения (токен
инсталляции, ключ Fernet, значения из ``.env``) вырезаются по совпадению, всё
остальное — общими паттернами проекта (``plugin_security.redact_text``).
Число вычищенных мест возвращается ручкой: молчаливая чистка неотличима от
неработающей, владелец должен видеть, что она сработала.

Имена файлов и проверку порта берём из ``bcc.desktop`` — источник правды один,
иначе переименование лога тихо оставит архив без него.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from .. import desktop
from ..db import events as events_t, rows_dicts, utcnow
from ..plugin_security import redact, redact_text
from . import Feature

FLAG = "BOSSMAN_DIAG_BUNDLE_ENABLED"
router = APIRouter()

# Хвост лога: конец файла отвечает на «почему не открылось», начало — нет.
LOG_TAIL_BYTES = 128 * 1024
EVENTS_LIMIT = 200
# Предел на СУММУ распакованных частей: архив пересылают почтой и мессенджером,
# и он обязан оставаться отчётом, а не выгрузкой всего каталога.
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_LISTED_FILES = 2000
PROBE_TIMEOUT = 1.0
BUNDLE_DIRNAME = "diag"
MARK = "***REDACTED***"
# Короткие «секреты» вырезать нельзя: строка из трёх символов встречается в
# любом тексте, и архив превратился бы в решето из пометок.
MIN_SECRET_LEN = 6

REPORT_NAME = "report.json"
EVENTS_NAME = "events.json"
FILES_NAME = "files.json"
LOCK_NAME = "desktop.lock"

# Чего в архиве не будет никогда и почему (ответ GET-ручки и часть отчёта).
EXCLUDED = [
    {"path": desktop.TOKEN_FILE_NAME, "reason": "токен доступа к Control API"},
    {"path": "secret.key", "reason": "ключ Fernet — им расшифровываются все ключи провайдеров"},
    {"path": ".env", "reason": "переменные окружения: ключи провайдеров и прочие секреты"},
    {"path": "*.db, *.sqlite*", "reason": "база целиком — переписка, задачи и зашифрованные ключи"},
    {"path": "browser/", "reason": "профиль браузера: cookie и живые сессии сайтов"},
    {"path": "*.key, *.pem", "reason": "любые ключи и сертификаты"},
]

# Страховка на будущее: даже если кто-то добавит часть с таким именем, она не
# попадёт в архив. Проверка по имени, а не по намерению автора правки.
DENY_NAMES = {desktop.TOKEN_FILE_NAME, "secret.key", ".env"}
DENY_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".key", ".pem", ".env")

# «Длинная строка после слова token/key/secret» без разделителя `:`/`=` —
# ровно так секрет и попадает в лог, а KV-паттерн проекта такое не ловит.
_RE_TOKEN_NEAR = re.compile(
    r"(?i)\b(token|api[_-]?key|key|secret|password)\b\s+([A-Za-z0-9._\-+/=]{12,})")


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- чистка

def scrub_text(text: str, secrets: set[str]) -> tuple[str, int]:
    """Вычистить секреты из текста; вернуть текст и ЧИСЛО вычищенных мест.

    Порядок важен: сначала точные известные значения (их мы знаем и обязаны
    убрать даже там, где они не похожи на ключ), потом общие паттерны.
    """
    if not text:
        return text or "", 0
    hits = 0
    for value in sorted(secrets, key=len, reverse=True):
        if len(value) < MIN_SECRET_LEN:
            continue
        found = text.count(value)
        if found:
            hits += found
            text = text.replace(value, MARK)
    text, near = _RE_TOKEN_NEAR.subn(lambda m: f"{m.group(1)} {MARK}", text)
    hits += near
    # Метки, поставленные выше, в счёт общих паттернов попасть не должны:
    # считаем только прирост после redact_text.
    before = text.count(MARK)
    text = redact_text(text)
    hits += max(0, text.count(MARK) - before)
    return text, hits


def known_secrets(svc, data_dir: Path) -> set[str]:
    """Точные значения, которые в архиве недопустимы ни в каком виде.

    Файлы читаем, но в архив НЕ кладём: они нужны как образцы для вырезания из
    логов и событий, куда секрет и попадает на самом деле.
    """
    values: set[str] = set()
    token = getattr(getattr(svc, "auth", None), "token", None)
    if isinstance(token, str) and token.strip():
        values.add(token.strip())
    for name in (desktop.TOKEN_FILE_NAME, "secret.key"):
        try:
            raw = (data_dir / name).read_bytes()
        except OSError:
            continue
        value = raw.decode("utf-8", "replace").strip()
        if len(value) >= MIN_SECRET_LEN:
            values.add(value)
    try:
        env_text = (data_dir / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        env_text = ""
    for line in env_text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1].strip().strip("'\"")
        if len(value) >= MIN_SECRET_LEN:
            values.add(value)
    return values


def _denied(name: str) -> bool:
    base = Path(name).name
    return base in DENY_NAMES or base.lower().endswith(DENY_SUFFIXES)


# ---------------------------------------------------------------- сбор частей

def _read_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> tuple[str | None, dict]:
    """Последние ``limit`` байт файла. ``None``, если файла нет — это не сбой.

    Срез делается по границе строки: обрезок первой строки не только нечитаем,
    но и способен разорвать секрет пополам, обманув чистку.
    """
    meta: dict[str, Any] = {"name": path.name, "present": False}
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            raw = fh.read(limit)
    except OSError as exc:
        meta["error"] = str(exc)
        return None, meta
    truncated = size > limit
    text = raw.decode("utf-8", "replace")
    if truncated:
        cut = text.find("\n")
        text = text[cut + 1:] if cut >= 0 else text
    meta.update(present=True, file_bytes=size, truncated=truncated)
    return text, meta


def _list_files(data_dir: Path) -> dict:
    """Имена и размеры файлов data_dir. Содержимого здесь нет и быть не может."""
    entries: list[dict] = []
    truncated = False
    total = 0
    for root, dirs, names in os.walk(data_dir):
        dirs.sort()
        for name in sorted(names):
            path = Path(root) / name
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            total += max(0, size)
            if len(entries) >= MAX_LISTED_FILES:
                truncated = True
                continue
            entries.append({"path": str(path.relative_to(data_dir)).replace(os.sep, "/"),
                            "bytes": size})
    return {"data_dir": str(data_dir), "files": entries, "count": len(entries),
            "total_bytes": total, "truncated": truncated}


def _lock_state(data_dir: Path) -> dict:
    """Замок окна: содержимое и жив ли записанный pid.

    Живой pid означает «приложение уже работает», мёртвый — «замок остался от
    упавшего процесса», и это две совершенно разные причины «не открылось».
    """
    path = desktop._desktop_lock_path(data_dir)
    state: dict[str, Any] = {"path": str(path), "present": path.exists()}
    if not state["present"]:
        return state
    try:
        state["raw"] = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError as exc:
        state["error"] = str(exc)
        return state
    data = desktop._read_lock(data_dir)
    if data is None:
        state["parsed"] = False
        return state
    pid = data.get("pid")
    state.update(parsed=True, pid=pid, port=data.get("port"),
                 pid_alive=desktop._pid_alive(pid) if isinstance(pid, int) else None)
    return state


def _is_loopback(host: str) -> bool:
    return host in ("localhost", "::1", "0.0.0.0", "::") or host.startswith("127.")


def _probe_port(host: str, port: int) -> dict:
    """Отвечает ли что-нибудь на настроенном адресе.

    Прокси из окружения при этом ОБЯЗАН быть отключён (``desktop.port_busy``
    ходит через ``ProxyHandler({})``): для локального адреса прокси только
    врёт — вернёт свой отказ, и владелец решит, что сервер мёртв. Наружу не
    ходим вовсе: не-loopback адрес честно помечаем как непроверенный.
    """
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{target}:{port}/"
    if not _is_loopback(host):
        return {"url": url, "checked": False,
                "reason": "адрес не loopback — наружу диагностика не ходит"}
    started = time.monotonic()
    answering = desktop.port_busy(url, timeout=PROBE_TIMEOUT)
    return {"url": url, "checked": True, "answering": answering,
            "took_ms": int((time.monotonic() - started) * 1000),
            "proxy_bypassed": True}


def _environment() -> dict:
    return {"app_version": __version__,
            "python": platform.python_version(),
            "python_build": sys.version.split()[0] + " " + platform.python_implementation(),
            "platform": platform.platform(),
            "os_name": os.name,
            "system": platform.system(),
            "machine": platform.machine()}


# ---------------------------------------------------------------- сборка архива

def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _build(data_dir: Path, host: str, port: int, events: list[dict],
           secrets: set[str]) -> dict:
    """Собрать архив. Блокирующая работа: вызывается через ``asyncio.to_thread``."""
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir = data_dir / BUNDLE_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    redactions = 0
    parts: list[dict] = []
    payload: list[tuple[str, str]] = []
    budget = MAX_BUNDLE_BYTES

    def add(name: str, text: str, meta: dict) -> None:
        """Положить часть, если она влезает в предел. Отказ — тоже в отчёт."""
        nonlocal budget
        if _denied(name):        # страховка: такое имя в архиве недопустимо
            meta.update(included=False, reason="имя в списке запрещённых")
            parts.append(meta)
            return
        blob = text.encode("utf-8")
        if len(blob) > budget:
            meta.update(included=False, bytes=len(blob),
                        reason="не влезло в предел архива")
            parts.append(meta)
            return
        budget -= len(blob)
        payload.append((name, text))
        meta.update(included=True, bytes=len(blob))
        parts.append(meta)

    # 1. логи окна — хвостами
    for path in (desktop._run_log_path(data_dir), desktop._gui_console_log_path(data_dir)):
        text, meta = _read_tail(path)
        if text is None:
            meta["included"] = False
            meta.setdefault("reason", "файла нет — сбор продолжается")
            parts.append(meta)
            continue
        text, hits = scrub_text(text, secrets)
        redactions += hits
        meta["redactions"] = hits
        add(path.name, text, meta)

    # 2. замок окна
    lock = _lock_state(data_dir)
    if lock.get("present"):
        text, hits = scrub_text(lock.get("raw") or "", secrets)
        redactions += hits
        lock["raw"] = text
        add(LOCK_NAME, text, {"name": LOCK_NAME, "present": True, "redactions": hits})
    else:
        parts.append({"name": LOCK_NAME, "present": False, "included": False,
                      "reason": "замка нет — окно не запускалось или закрылось штатно"})

    # 3. события: сначала чистка по именам ключей (как в шине), потом по тексту
    raw_events = _dump(events)
    safe_events = _dump(redact(events, secret_values=secrets))
    struct_hits = max(0, safe_events.count(MARK) - raw_events.count(MARK))
    events_text, hits = scrub_text(safe_events, secrets)
    redactions += struct_hits + hits
    add(EVENTS_NAME, events_text,
        {"name": EVENTS_NAME, "present": True, "count": len(events),
         "redactions": struct_hits + hits})

    # 4. перечень файлов data_dir
    listing = _list_files(data_dir)
    listing_text, hits = scrub_text(_dump(listing), secrets)
    redactions += hits
    add(FILES_NAME, listing_text,
        {"name": FILES_NAME, "present": True, "count": listing["count"], "redactions": hits})

    # 5. отчёт — последним: он описывает уже собранные части
    report = {
        "created_at": utcnow().isoformat(),
        "environment": _environment(),
        "settings": {"host": host, "port": port, "data_dir": str(data_dir)},
        "port_probe": _probe_port(host, port),
        "lock": lock,
        "parts": parts,
        "excluded": EXCLUDED,
        "limits": {"log_tail_bytes": LOG_TAIL_BYTES, "events": EVENTS_LIMIT,
                   "max_bundle_bytes": MAX_BUNDLE_BYTES},
        # Счётчик по остальным частям: сам отчёт чистится следующей строкой, и
        # его собственные попадания добавляются уже к ответу ручки.
        "redactions_in_parts": redactions,
    }
    report_text, hits = scrub_text(_dump(report), secrets)
    redactions += hits
    payload.append((REPORT_NAME, report_text))
    parts.append({"name": REPORT_NAME, "present": True, "included": True,
                  "bytes": len(report_text.encode("utf-8")), "redactions": hits})

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"diag-bundle-{stamp}.zip"
    n = 1
    while path.exists():                       # два сбора в одну секунду — не редкость
        path = out_dir / f"diag-bundle-{stamp}-{n}.zip"
        n += 1
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in payload:
            zf.writestr(name, text)
    return {"path": str(path), "name": path.name, "size_bytes": path.stat().st_size,
            "parts": parts, "redactions": redactions,
            "included": [name for name, _ in payload], "excluded": EXCLUDED}


# ---------------------------------------------------------------- ручки

@router.get("/diag/bundle")
async def preview(request: Request):
    """Что попадёт в архив и чего в нём не будет. Ничего не собирает.

    Работает и при выключенном флаге: владелец должен иметь возможность
    заранее увидеть, что именно он отправит, — прежде чем включать сбор.
    """
    svc = request.app.state.svc
    data_dir = Path(svc.settings.data_dir)
    return {
        "enabled": enabled(), "flag": FLAG,
        "data_dir": str(data_dir),
        "bundle_dir": str(data_dir / BUNDLE_DIRNAME),
        "will_include": [
            {"name": desktop._run_log_path(data_dir).name,
             "note": f"последние {LOG_TAIL_BYTES // 1024} КБ"},
            {"name": desktop.GUI_CONSOLE_LOG_NAME,
             "note": f"последние {LOG_TAIL_BYTES // 1024} КБ"},
            {"name": LOCK_NAME, "note": "содержимое замка и живость pid"},
            {"name": EVENTS_NAME, "note": f"последние {EVENTS_LIMIT} событий"},
            {"name": FILES_NAME, "note": "имена и размеры файлов data_dir"},
            {"name": REPORT_NAME, "note": "версии, платформа, адрес/порт и проверка порта"},
        ],
        "will_exclude": EXCLUDED,
        "limits": {"log_tail_bytes": LOG_TAIL_BYTES, "events": EVENTS_LIMIT,
                   "max_bundle_bytes": MAX_BUNDLE_BYTES},
    }


@router.post("/diag/bundle")
async def build(request: Request):
    """Собрать архив в data_dir. При выключенном флаге — 409 и ни одного файла."""
    if not enabled():
        raise HTTPException(409, {"message": f"сбор отчёта выключен ({FLAG})",
                                  "hint": f"включить: {FLAG}=1"})
    svc = request.app.state.svc
    data_dir = Path(svc.settings.data_dir)

    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(events_t)
                                .order_by(events_t.c.id.desc())
                                .limit(EVENTS_LIMIT))).fetchall()
    events = list(reversed(rows_dicts(rows)))

    secrets = known_secrets(svc, data_dir)
    result = await asyncio.to_thread(_build, data_dir, str(svc.settings.host),
                                     int(svc.settings.port), events, secrets)
    # В событие идут только путь и размер: имя архива секретом не является,
    # а его содержимое в ленту активности не попадает.
    await svc.bus.emit("diag.bundle_created", name=result["name"],
                       size_bytes=result["size_bytes"], redactions=result["redactions"])
    return {"enabled": True, **result}


FEATURE = Feature(name="diag_bundle", router=router)
