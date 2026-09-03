"""Режим тестового периода: всё, что делает владелец, пишется и уезжает в GitHub одной кнопкой.

Зачем. Перед открытием владелец сам гоняет приложение и хочет, чтобы каждое
действие, каждый ответ и каждая ошибка остались записанными, а не пересказанными
по памяти. Отсюда три вещи: плашка наверху (режим виден, а не подразумевается),
журнал всего происходящего и кнопка «опубликовать», которая коммитит журнал в
репозиторий.

Что записывается:

  * со стороны браузера — клики по кнопкам и ссылкам, переходы между страницами,
    отправка форм, ошибки JS и неудачные запросы (`POST /testing/log`);
  * со стороны сервера — каждый HTTP-запрос: метод, путь, код, длительность
    (middleware в `api.py`, включается только в этом режиме);
  * события шины — то, что система делает сама.

Границы секрета — главное в этом модуле, потому что журнал уезжает в git:

  * заголовки авторизации, cookie и CSRF не пишутся вообще;
  * тела запросов не пишутся: там бывают токены и ключи провайдеров;
  * перед публикацией каждая строка проходит чистку `plugin_security.redact_text`
    плюс точные значения токена инсталляции и содержимого `secret.key`;
  * счётчик вычищенных мест виден в ответе — владелец должен видеть, что чистка
    работала, а не молчала.

Режим включён по умолчанию (`BOSSMAN_TESTING_PERIOD=0` выключает целиком: без
плашки, без журнала, без ручек). Это осознанное отступление от общего правила
«флаг выключен»: тестовый период на то и период, что его включают и потом
снимают одной переменной.

Публикация — единственная операция, выходящая наружу. Она делается только по
нажатию владельца, коммитит в текущую ветку и не делает force. Если ветка ушла
вперёд, публикация честно отказывается и говорит об этом, а не переписывает
чужую работу.
"""
from __future__ import annotations

import asyncio
import json
import contextlib
import os
import platform
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..config import ROOT
from ..plugin_security import redact_text
from . import Feature

FLAG = "BOSSMAN_TESTING_PERIOD"
router = APIRouter()

LOG_DIRNAME = "testing"
PUBLISH_SUBDIR = Path("docs/testing/sessions")
MAX_EVENTS_PER_POST = 200
MAX_FIELD_CHARS = 2000
MAX_LOG_BYTES = 32 * 1024 * 1024      # журнал одной инсталляции: дальше не растим
MAX_PUBLISH_EVENTS = 20000

# Ключи, значения которых не пишем никогда — даже если браузер их прислал.
FORBIDDEN_KEYS = {"token", "authorization", "cookie", "csrf", "password", "secret",
                  "api_key", "apikey", "key"}


def enabled() -> bool:
    """Режим включён, пока его явно не выключили.

    Обратная логика остальных флагов сделана намеренно: владелец просил режим
    сейчас и снимет его позже одной переменной, а не наоборот.
    """
    return os.environ.get(FLAG, "1").strip().lower() not in ("0", "false", "no", "off")


def _log_dir_for(data_dir) -> Path:
    """Каталог журнала по самому каталогу данных.

    Отдельно от _log_dir, потому что процесс лаунчера объекта настроек ещё не
    имеет: у него на руках только путь.
    """
    return Path(data_dir) / LOG_DIRNAME


def _log_path_for(data_dir) -> Path:
    return _log_dir_for(data_dir) / "session-log.jsonl"


def _log_dir(settings) -> Path:
    return _log_dir_for(settings.data_dir)


def _log_path(settings) -> Path:
    return _log_path_for(settings.data_dir)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clip(value: Any) -> Any:
    """Длинные строки режем: журнал должен оставаться читаемым и переносимым."""
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"…[обрезано, всего {len(value)} символов]"
    return value


def _safe_payload(data: Any, depth: int = 0) -> Any:
    """Выбрасывает поля, значения которых не должны попадать в журнал вообще.

    Чистка при публикации — вторая линия. Первая — просто не записывать.
    """
    if depth > 4:
        return "…[слишком глубоко]"
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            name = str(key)
            if name.lower() in FORBIDDEN_KEYS:
                out[name] = "[не записывается]"
            else:
                out[name] = _safe_payload(value, depth + 1)
        return out
    if isinstance(data, list):
        return [_safe_payload(v, depth + 1) for v in data[:50]]
    return _clip(data)


class SessionLog:
    """Журнал тестового периода: append-only JSONL в data_dir.

    Своей таблицы не заводим: журнал должен пережить любую операцию с базой и
    уехать в git одним файлом, а не выгрузкой.
    """

    def __init__(self, settings) -> None:
        self.settings = settings
        self.session_id = uuid.uuid4().hex[:12]
        self.started_at = _now_iso()
        self.counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._full = False

    @property
    def path(self) -> Path:
        return _log_path(self.settings)

    async def write(self, source: str, kind: str, payload: dict | None = None) -> bool:
        if not enabled():
            return False
        record = {
            "ts": _now_iso(),
            "session": self.session_id,
            "source": source,          # ui | server | bus
            "kind": kind,
            "data": _safe_payload(payload or {}),
        }
        try:
            async with self._lock:
                self.counts[kind] = self.counts.get(kind, 0) + 1
                return await asyncio.to_thread(self._append, record)
        except asyncio.CancelledError:
            raise
        except Exception:              # noqa: BLE001
            # Наблюдатель не имеет права уронить то, за чем наблюдает: любая
            # неожиданность на пути записи гасится здесь, а не всплывает в
            # обработчик запроса владельца.
            return False

    def _append(self, record: dict) -> bool:
        try:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self._full and path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                # Предел, а не тихое переполнение диска: об этом надо сказать вслух.
                self._full = True
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": _now_iso(), "source": "server",
                                         "kind": "log.full",
                                         "data": {"limit_bytes": MAX_LOG_BYTES}},
                                        ensure_ascii=False) + "\n")
            if self._full:
                return False
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except OSError:
            # Журнал не имеет права уронить приложение, которое он наблюдает.
            return False

    def read(self, limit: int = 200) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"ts": "", "source": "server", "kind": "log.corrupt",
                            "data": {"raw": line[:200]}})
        return out

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


# Закрытый список исходов запуска. Ровно то, что умеет записать desktop.run —
# без запаса «на будущее»: причина, которую никто не пишет, при разборе журнала
# выглядит как «такого не случалось», хотя её просто нет в коде.
LAUNCH_REASONS = ("start", "ok", "no-browser-found", "port-busy-foreign",
                  "server-start-failed", "no-server-flag", "browser-launch-failed",
                  "refused-second-window", "window-not-ready")


def mask_home(value: str) -> str:
    """Домашний каталог заменяем на ~: в путях Windows видно имя пользователя."""
    text = str(value or "")
    try:
        home = str(Path.home())
    except (OSError, RuntimeError):
        return text
    return text.replace(home, "~") if home and len(home) > 3 else text


def record_launch(data_dir, kind: str, payload: dict) -> bool:
    """Записать событие запуска в ТОТ ЖЕ журнал — из процесса лаунчера.

    Зачем отдельная функция. Журнал заводится в setup(), то есть уже ПОСЛЕ того,
    как сервер поднялся. Если окно не открылось совсем — браузер не найден, порт
    занят чужим, сервер не встал, launcher упал — сервера нет, журнала нет, и в
    присланном файле про этот запуск нет ни строчки. Следы остаются только в
    desktop-run.log, который в git не уезжает и которого получатель не видит.
    Здесь исход запуска ложится в общий jsonl и уедет со следующей удачной
    сессией.

    Пишем синхронно и с теми же ограничителями, что и SessionLog: выключенный
    режим и предел размера обязаны действовать и на этот путь, иначе процесс
    лаунчера обошёл бы их стороной.
    """
    if not enabled():
        return False
    try:
        path = _log_path_for(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            return False
        record = {"ts": _now_iso(), "session": "launcher", "source": "desktop",
                  "kind": kind, "data": _safe_payload(payload or {})}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        # Наблюдатель не имеет права помешать запуску, который он наблюдает.
        return False


def _secret_values(svc) -> set[str]:
    """Точные значения, которые надо вырезать перед публикацией."""
    values: set[str] = set()
    token = getattr(getattr(svc, "auth", None), "token", "")
    if isinstance(token, str) and len(token) >= 8:
        values.add(token)
    for name in ("token", "secret.key"):
        try:
            text = (Path(svc.settings.data_dir) / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if len(text) >= 8:
            values.add(text)
    return values


_LONG_SECRET = re.compile(
    r"((?:token|key|secret|password|bearer)\W{0,3})([A-Za-z0-9_\-.]{16,})", re.IGNORECASE)


def scrub(text: str, secrets: set[str]) -> tuple[str, int]:
    """Чистка перед публикацией. Возвращает текст и число вычищенных мест."""
    count = 0
    for value in sorted(secrets, key=len, reverse=True):
        if value and value in text:
            count += text.count(value)
            text = text.replace(value, "[вычищено]")

    def _mask(match: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}[вычищено]"

    text = _LONG_SECRET.sub(_mask, text)
    cleaned = redact_text(text)
    if cleaned != text:
        count += 1
    return cleaned, count


def _git(args: list[str], cwd: Path, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,  # noqa: S603
                          text=True, timeout=timeout)


def _summary(events: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    errors = []
    for event in events:
        by_kind[event.get("kind", "?")] = by_kind.get(event.get("kind", "?"), 0) + 1
        by_source[event.get("source", "?")] = by_source.get(event.get("source", "?"), 0) + 1
        kind = str(event.get("kind", ""))
        if "error" in kind or "fail" in kind:
            errors.append(event)
    return {"total": len(events), "by_kind": by_kind, "by_source": by_source,
            "errors": errors[-100:], "error_count": len(errors)}


def _render_report(events: list[dict], summary: dict, session_id: str) -> str:
    head = [
        "# Тестовый период — журнал сессии",
        "",
        f"Сессия `{session_id}` · записей {summary['total']} · ошибок {summary['error_count']}",
        f"Опубликовано: {_now_iso()}",
        "",
        "Журнал вычищен от секретов перед публикацией: значения токена, ключа",
        "инсталляции и полей авторизации в него не попадают.",
        "",
        "## Что происходило",
        "",
        "| Событие | Сколько |",
        "|---|---|",
    ]
    for kind, count in sorted(summary["by_kind"].items(), key=lambda kv: -kv[1]):
        head.append(f"| `{kind}` | {count} |")
    head += ["", "## Источники", "", "| Откуда | Сколько |", "|---|---|"]
    for source, count in sorted(summary["by_source"].items(), key=lambda kv: -kv[1]):
        head.append(f"| `{source}` | {count} |")
    if summary["errors"]:
        head += ["", "## Ошибки", ""]
        for event in summary["errors"]:
            data = json.dumps(event.get("data", {}), ensure_ascii=False)[:400]
            head.append(f"- `{event.get('ts', '')}` `{event.get('kind', '')}` — {data}")
    else:
        head += ["", "## Ошибки", "", "За эту сессию ошибок не записано."]
    head += ["", "Полный журнал — в файле `.jsonl` рядом с этим отчётом.", ""]
    return "\n".join(head)


def _env_snapshot(svc) -> dict:
    """Что за сборка и с какими флагами шёл этот прогон.

    Без этого два присланных журнала несопоставимы: непонятно, одна ли версия,
    те же ли фичи включены, та же ли платформа. Пишем только состояние флагов
    (да/нет) — не значения переменных: там бывают пути и адреса.
    """
    from .. import __version__

    flags = {name: os.environ.get(name, "").strip().lower() in ("1", "true", "yes")
             for name in sorted(os.environ) if name.startswith("BOSSMAN_")
             and name.endswith("_ENABLED")}
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "release": platform.release(),
        "testing_period": enabled(),
        "flags_on": sorted(k for k, v in flags.items() if v),
        "flags_off_count": sum(1 for v in flags.values() if not v),
        "features": sorted(f.name for f in getattr(svc, "features", []) or []),
        "host": svc.settings.host,
        "port": svc.settings.port,
    }


# ------------------------------------------------------------------ ручки

def _log_of(request: Request) -> SessionLog:
    log = getattr(request.app.state.svc, "testing_log", None)
    if log is None:
        raise HTTPException(503, {"message": "журнал тестового периода не инициализирован"})
    return log


@router.get("/testing/status")
async def status(request: Request):
    """Состояние режима. Читающая: отвечает и при выключенном режиме."""
    if not enabled():
        return {"enabled": False, "banner": None, "session": None, "events": 0, "bytes": 0}
    log = _log_of(request)
    return {
        "enabled": True,
        "banner": "TESTING PERIOD — идёт запись действий",
        "session": log.session_id,
        "started_at": log.started_at,
        "events": sum(log.counts.values()),
        "by_kind": dict(sorted(log.counts.items(), key=lambda kv: -kv[1])),
        "bytes": log.size_bytes(),
        "log_path": str(log.path),
    }


@router.post("/testing/log")
async def ingest(request: Request):
    """Пачка событий из браузера: клики, переходы, ошибки JS, неудачные запросы."""
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    body = await request.json()
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        raise HTTPException(400, {"message": "ожидается {\"events\": [...]}"})
    log = _log_of(request)
    written = 0
    for event in events[:MAX_EVENTS_PER_POST]:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "ui.event")[:60]
        if await log.write("ui", kind, event.get("data") if isinstance(event.get("data"), dict) else {"value": event.get("data")}):
            written += 1
    return {"accepted": written, "dropped": max(0, len(events) - written),
            "total": sum(log.counts.values())}


@router.get("/testing/events")
async def events(request: Request, limit: int = 200):
    if not enabled():
        return {"enabled": False, "events": []}
    limit = max(1, min(int(limit), 2000))
    log = _log_of(request)
    return {"enabled": True, "session": log.session_id, "events": log.read(limit)}


@router.post("/testing/publish")
async def publish(request: Request):
    """Кнопка владельца: вычистить журнал и отправить его в GitHub.

    Единственная операция модуля, выходящая наружу, и она происходит только по
    нажатию. Force не делаем никогда: если ветка ушла вперёд, отказываемся и
    говорим об этом, а не переписываем чужую работу.
    """
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    log = _log_of(request)
    svc = request.app.state.svc
    return await asyncio.to_thread(_publish_sync, log, svc)


def _publish_sync(log: SessionLog, svc) -> dict:
    events = log.read(MAX_PUBLISH_EVENTS)
    if not events:
        raise HTTPException(400, {"message": "журнал пуст — публиковать нечего"})

    secrets = _secret_values(svc)
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    clean_jsonl, redactions = scrub(raw, secrets)
    summary = _summary([json.loads(line) for line in clean_jsonl.splitlines() if line.strip()])
    report, more = scrub(_render_report(events, summary, log.session_id), secrets)
    redactions += more

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    base = f"{stamp}__{log.session_id}"
    repo = _repo_root(ROOT)
    if repo is None:
        raise HTTPException(400, {"message": "не найден git-репозиторий для публикации"})
    out_dir = repo / PUBLISH_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{base}.md"
    jsonl_path = out_dir / f"{base}.jsonl"
    md_path.write_text(report, encoding="utf-8")
    jsonl_path.write_text(clean_jsonl + "\n", encoding="utf-8")

    # Последняя страховка: секрета не должно быть в том, что уходит в git.
    for path in (md_path, jsonl_path):
        body = path.read_text(encoding="utf-8", errors="replace")
        for value in secrets:
            if value and value in body:
                path.unlink(missing_ok=True)
                raise HTTPException(500, {"message": "публикация отменена: секрет уцелел в файле"})

    rel = [str(p.relative_to(repo)) for p in (md_path, jsonl_path)]
    steps: list[dict] = []

    def _step(name: str, args: list[str]) -> subprocess.CompletedProcess:
        proc = _git(args, repo)
        steps.append({"step": name, "code": proc.returncode,
                      "out": (proc.stdout or "").strip()[:400],
                      "err": (proc.stderr or "").strip()[:400]})
        return proc

    add = _step("add", ["add", "--", *rel])
    if add.returncode != 0:
        return {"published": False, "reason": "git add не прошёл", "steps": steps,
                "files": rel, "redactions": redactions}
    message = (f"chore(testing): журнал тестового периода {log.session_id} "
               f"({summary['total']} записей, {summary['error_count']} ошибок)")
    commit = _step("commit", ["commit", "-m", message, "--", *rel])
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout or ""):
        return {"published": False, "reason": "git commit не прошёл", "steps": steps,
                "files": rel, "redactions": redactions}

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() or "HEAD"
    push = _step("push", ["push", "origin", branch])
    if push.returncode != 0:
        return {"published": False, "committed": True, "branch": branch,
                "reason": "коммит создан, но push не прошёл — ветка могла уйти вперёд; "
                          "force не делаем",
                "steps": steps, "files": rel, "redactions": redactions,
                "summary": summary}
    sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()[:12]
    return {"published": True, "branch": branch, "sha": sha, "files": rel,
            "redactions": redactions, "summary": summary, "steps": steps}


def _repo_root(start: Path) -> Path | None:
    proc = _git(["rev-parse", "--show-toplevel"], start if start.exists() else ROOT)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


# ------------------------------------------------------------------ подключение

async def setup(svc) -> None:
    """Заводит журнал и подписку на шину: система сама тоже пишет, что делает."""
    if not enabled():
        return
    log = SessionLog(svc.settings)
    _log_dir(svc.settings).mkdir(parents=True, exist_ok=True)
    svc.testing_log = log
    await log.write("server", "session.start",
                    {"host": svc.settings.host, "port": svc.settings.port})
    await log.write("server", "session.env", _env_snapshot(svc))

    queue = svc.bus.subscribe()

    async def _pump() -> None:
        try:
            while True:
                event = await queue.get()
                kind = str(event.get("kind") or "bus.event")
                if kind.startswith("testing."):
                    continue           # иначе подписка кормит сама себя
                await log.write("bus", kind,
                                {k: v for k, v in event.items() if k != "kind"})
        except asyncio.CancelledError:
            svc.bus.unsubscribe(queue)
            raise
        except Exception:              # noqa: BLE001 — журнал не роняет приложение
            svc.bus.unsubscribe(queue)
            return

    task = asyncio.create_task(_pump(), name="bcc-testing-period")
    if hasattr(svc, "_tasks"):
        svc._tasks.append(task)

    # Штатная остановка должна быть видна. Её ОТСУТСТВИЕ — тоже улика: значит
    # процесс не дожил до неё, а журнал просто оборвался.
    stop_original = svc.stop

    async def _stop_with_record():
        with contextlib.suppress(Exception):
            await log.write("server", "session.stop", {
                "uptime_s": round(time.monotonic() - started_at, 1),
                "events": sum(log.counts.values()),
                "top": dict(sorted(log.counts.items(), key=lambda kv: -kv[1])[:5]),
                "bytes": log.size_bytes(),
            })
        return await stop_original()

    started_at = time.monotonic()
    svc.stop = _stop_with_record


FEATURE = Feature(name="testing_period", router=router, setup=setup)
