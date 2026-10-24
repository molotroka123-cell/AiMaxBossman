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

Публикация делается только по нажатию владельца. С 24.10.2026 (решение
владельца о приватности) она НЕ коммитит и НЕ пушит: вычищенный журнал
сохраняется в `data_dir/testing/published/` этой машины. Абзацы выше про git и
GitHub описывают прежнее поведение; чистка секретов осталась прежней.
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
from starlette.requests import ClientDisconnect

from ..config import ROOT
from ..plugin_security import redact_text
from . import Feature

FLAG = "BOSSMAN_TESTING_PERIOD"
router = APIRouter()

LOG_DIRNAME = "testing"
# Куда кнопка «опубликовать» складывает вычищенный журнал: ВНУТРИ data_dir
# инсталляции, а не в docs/ рабочей копии. Владелец 24.10.2026: журналы сессий —
# личная переписка с системой, в git им не место (прежние копии остались в
# истории git; новые туда не попадают).
PUBLISH_SUBDIR = Path("testing") / "published"
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
            elif (name == "features" and isinstance(value, list)
                  and all(isinstance(v, str) for v in value)):
                # Environment snapshots are only useful when their live feature
                # inventory is complete. The generic 50-item log cap started
                # silently dropping later feature names (including
                # testing_period) once the product grew beyond 50 modules. Keep
                # this bounded but high enough for the explicit inventory.
                out[name] = [_safe_payload(v, depth + 1) for v in value[:200]]
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

    def __init__(self, settings, *, rotate_existing: bool = False) -> None:
        self.settings = settings
        self.session_id = uuid.uuid4().hex[:12]
        self.started_at = _now_iso()
        self.counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._full = False
        self.rotated_from: str | None = None
        if rotate_existing:
            self._rotate_on_init()

    @property
    def path(self) -> Path:
        return _log_path(self.settings)

    def _rotate_on_init(self) -> None:
        """Если session-log.jsonl остался от прошлой серверной сессии или переполнен (>= MAX_LOG_BYTES),
        архивируем его рядом в session-log-<old_session>.jsonl, сохраняя записи текущего запуска desktop.launch."""
        try:
            path = self.path
            if not path.is_file():
                return
            size = path.stat().st_size
            if size == 0:
                return
            oversized = size >= MAX_LOG_BYTES
            prev_session: str | None = None
            trailing_launcher: list[str] = []
            if not oversized:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sess = str(rec.get("session") or "")
                    src = str(rec.get("source") or "")
                    if src == "desktop" or sess == "launcher":
                        trailing_launcher.append(line)
                    elif sess and sess != self.session_id:
                        prev_session = sess
                        trailing_launcher = []
            if not oversized and prev_session is None:
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            suffix = prev_session or "full"
            archive_path = path.parent / f"session-log-{stamp}-{suffix}.jsonl"
            os.replace(path, archive_path)
            self.rotated_from = archive_path.name
            if trailing_launcher:
                path.write_text("\n".join(trailing_launcher) + "\n", encoding="utf-8")
        except OSError:
            pass

    def rotate_now(self) -> dict[str, Any]:
        """Принудительная ротация журнала для начала чистой сессии без потери старого архива."""
        old_session = self.session_id
        try:
            path = self.path
            if path.is_file() and path.stat().st_size > 0:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                archive_path = path.parent / f"session-log-{stamp}-{old_session}.jsonl"
                os.replace(path, archive_path)
                self.rotated_from = archive_path.name
        except OSError:
            pass
        self.session_id = uuid.uuid4().hex[:12]
        self.started_at = _now_iso()
        self.counts = {}
        self._full = False
        return {"session": self.session_id, "rotated_from": self.rotated_from}

    @property
    def is_full(self) -> bool:
        return bool(self._full or self.size_bytes() > MAX_LOG_BYTES)

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

    async def write_many(self, source: str, items: list[tuple[str, dict | None]]) -> int:
        """One batch, one lock, one file open. The browser sends up to MAX_EVENTS_PER_POST events per POST;
        appending them one by one (lock + thread hop + open per event) made a busy page's batch outlive the
        UI sweep's action timeout (installed sweep of bundle 0b15e00c, 11.10). Returns the number written."""
        if not enabled() or not items:
            return 0
        records = [{"ts": _now_iso(), "session": self.session_id, "source": source, "kind": kind,
                    "data": _safe_payload(payload or {})} for kind, payload in items]
        try:
            async with self._lock:
                for rec in records:
                    self.counts[rec["kind"]] = self.counts.get(rec["kind"], 0) + 1
                return await asyncio.to_thread(self._append_many, records)
        except asyncio.CancelledError:
            raise
        except Exception:              # noqa: BLE001 - the observer never takes down what it observes
            return 0

    def _append_many(self, records: list[dict]) -> int:
        try:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self._full and path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                self._full = True
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": _now_iso(), "session": self.session_id, "source": "server",
                                         "kind": "log.full",
                                         "data": {"limit_bytes": MAX_LOG_BYTES}}, ensure_ascii=False) + "\n")
            if self._full:
                return 0
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
            return len(records)
        except OSError:
            return 0

    def _append(self, record: dict) -> bool:
        try:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self._full and path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                # Предел, а не тихое переполнение диска: об этом надо сказать вслух.
                self._full = True
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": _now_iso(), "session": self.session_id, "source": "server",
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
LAUNCH_REASONS = ("start", "ok", "no-browser-found", "port-busy-foreign", "backend-build-mismatch",
                  "server-start-failed", "no-server-flag", "browser-launch-failed",
                  "refused-second-window", "window-not-ready", "data-root-holder-silent")


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


GIT_TIMEOUT_S = 60.0


class GitUnavailable(RuntimeError):
    """git не запускается: нет в PATH, урезанный образ, запрет запуска."""


def _git(args: list[str], cwd: Path, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Запустить git; наружу — только CompletedProcess или GitUnavailable.

    * Вывод git — UTF-8. Без явной кодировки `text=True` декодирует его
      кодировкой процесса: на Windows это ANSI (cp1251), кириллица становится
      кракозябрами, а байт 0x98 («И») — UnicodeDecodeError и голым 500.
    * Нет git (журнал владельца: `FileNotFoundError: [WinError 2]`, шесть 500
      подряд) — типизированный отказ, а не исключение из ручки.
    * Зависший шаг (push ждёт сеть или учётные данные) — код 124 и понятная
      причина в шаге, а не TimeoutExpired → 500.
    """
    limit = GIT_TIMEOUT_S if timeout is None else timeout
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,  # noqa: S603
                              text=True, encoding="utf-8", errors="replace",
                              timeout=limit)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["git", *args], 124, stdout="",
            stderr=f"git {args[0] if args else ''} не ответил за {limit:g} с — остановлен")
    except OSError as exc:
        raise GitUnavailable(f"git не запускается: {exc}") from exc


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


# Строгий UX-whitelist для публикации: никаких промптов, ответов моделей, путей на диске и имён медиафайлов.
_UI_UX_KEYS = frozenset({
    "element", "page", "to", "from", "form", "method", "path", "status", "ms",
    "waited_ms", "checked", "hits", "within_ms", "screen", "dpr", "lang",
    "theme", "online", "state", "dropped", "line", "code",
})
_SERVER_UX_KEYS = frozenset({
    "method", "path", "status", "ms", "host", "port", "version", "python",
    "platform", "release", "testing_period", "flags_on", "flags_off_count",
    "features", "uptime_s", "events", "top", "bytes", "limit_bytes",
})
_BUS_UX_KEYS = frozenset({
    "status", "code", "duration_ms", "step", "attempt", "ok", "verified",
})
_DISK_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"'<>|]*|\\\\[^\s\"'<>|]+|/(?:Users|home|var|tmp|opt|etc|private|mnt|Volumes)/[^\s\"'<>|]+)",
    re.IGNORECASE,
)
_MEDIA_FILE_RE = re.compile(
    r"\b[^\s/\\\"'<>|]+\.(?:mp4|mov|mkv|avi|webm|mp3|wav|ogg|flac|m4a|jpg|jpeg|png|webp|gif|bmp|pdf|xlsx|xls|docx|doc|csv|zip|7z|tar|gz|jsonl|db|sqlite|pem|key|kdbx|pfx|p12|env)\b",
    re.IGNORECASE,
)


def _sanitize_ux_string(val: str, *, is_route: bool = False) -> tuple[str, int]:
    """Удаляет пути к файлам на диске, имена медиафайлов и query-параметры из UX-строки."""
    cleaned = str(val or "")
    redacted = 0
    if is_route and "?" in cleaned:
        cleaned = cleaned.split("?", 1)[0]
        redacted += 1
    if _DISK_PATH_RE.search(cleaned):
        cleaned, n = _DISK_PATH_RE.subn("[путь скрыт]", cleaned)
        redacted += n
    if _MEDIA_FILE_RE.search(cleaned):
        cleaned, n = _MEDIA_FILE_RE.subn("[файл скрыт]", cleaned)
        redacted += n
    return cleaned[:160], redacted


def sanitize_event_for_publish(event: dict[str, Any], session_id: str) -> tuple[dict[str, Any] | None, int]:
    """Фильтрует событие строго по текущей сессии (`event['session'] == session_id`)
    и оставляет только разрешённые UX-поля без промптов, ответов моделей, путей на диске и медиафайлов."""
    if not isinstance(event, dict) or event.get("session") != session_id:
        return None, 0
    source = str(event.get("source") or "")
    kind = str(event.get("kind") or "")
    if source not in {"ui", "server", "bus", "desktop"} or not kind:
        return None, 0
    # Потоковые дельты/ответы моделей с шины не публикуем вовсе
    if source == "bus" and kind in {
        "run.assistant_delta", "run.assistant_message", "run.reasoning_delta",
        "run.answer_delta", "run.answer_reset", "run.log",
    }:
        return None, 1

    raw_data = event.get("data")
    if not isinstance(raw_data, dict):
        raw_data = {}

    allowed_keys = (
        _UI_UX_KEYS if source == "ui"
        else _SERVER_UX_KEYS if source == "server"
        else _BUS_UX_KEYS
    )
    clean_data: dict[str, Any] = {}
    redacted = 0

    for k, v in raw_data.items():
        key = str(k)
        if key not in allowed_keys:
            redacted += 1
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            clean_data[key] = v
        elif isinstance(v, str):
            s_val, n_red = _sanitize_ux_string(v, is_route=(key in {"path", "page", "to", "from"}))
            clean_data[key] = s_val
            redacted += n_red
        elif key in {"flags_on", "features"} and isinstance(v, list):
            clean_data[key] = [str(item)[:64] for item in v[:200] if isinstance(item, str)]
        elif key == "top" and isinstance(v, dict):
            clean_data[key] = {str(tk)[:64]: int(tv) for tk, tv in list(v.items())[:10] if isinstance(tv, (int, float))}
        else:
            redacted += 1

    clean_kind, kind_red = _sanitize_ux_string(kind)
    redacted += kind_red
    return {
        "ts": str(event.get("ts") or ""),
        "session": session_id,
        "source": source,
        "kind": clean_kind[:80],
        "data": clean_data,
    }, redacted


def sanitize_events_for_publish(events: list[dict[str, Any]], session_id: str) -> tuple[list[dict[str, Any]], int]:
    """Возвращает список очищенных UX-событий строго текущей сессии и счётчик отфильтрованных полей."""
    out: list[dict[str, Any]] = []
    redacted_total = 0
    for ev in events:
        clean_ev, n_red = sanitize_event_for_publish(ev, session_id)
        redacted_total += n_red
        if clean_ev is not None:
            out.append(clean_ev)
    return out, redacted_total


# ------------------------------------------------------------------ ручки

def _log_of(request: Request) -> SessionLog:
    log = getattr(request.app.state.svc, "testing_log", None)
    if log is None:
        raise HTTPException(503, {"message": "журнал тестового периода не инициализирован"})
    return log


@router.get("/testing/status")
async def status(request: Request):
    """Состояние режима. Читающая: отвечает и при выключенном режиме.
    Если достигнут лимит MAX_LOG_BYTES (log.is_full), честно сообщает recording=False и full=True."""
    if not enabled():
        return {"enabled": False, "recording": False, "full": False, "banner": None, "session": None, "events": 0, "bytes": 0}
    log = _log_of(request)
    size = log.size_bytes()
    is_full = log.is_full
    return {
        "enabled": True,
        "recording": not is_full,
        "full": is_full,
        "banner": (
            "TESTING PERIOD — запись остановлена (лимит журнала 32 МБ исчерпан)"
            if is_full
            else "TESTING PERIOD — идёт запись действий"
        ),
        "session": log.session_id,
        "started_at": log.started_at,
        "events": sum(log.counts.values()),
        "by_kind": dict(sorted(log.counts.items(), key=lambda kv: -kv[1])),
        "bytes": size,
        "max_bytes": MAX_LOG_BYTES,
        "rotated_from": log.rotated_from,
        "log_path": str(log.path),
    }


@router.post("/testing/rotate")
async def rotate_session(request: Request):
    """Архивирует текущий session-log.jsonl и начинает новую чистую сессию."""
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    log = _log_of(request)
    svc = request.app.state.svc
    res = await asyncio.to_thread(log.rotate_now)
    await log.write("server", "session.start", {"host": svc.settings.host, "port": svc.settings.port})
    await log.write("server", "session.env", _env_snapshot(svc))
    return {
        "ok": True,
        "session": log.session_id,
        "rotated_from": res.get("rotated_from"),
        "recording": not log.is_full,
        "full": log.is_full,
    }


@router.get("/testing/preview")
async def preview_publish(request: Request):
    """Безопасный предпросмотр пакета публикации (строго текущая сессия, только UX-поля, без коммита/push)."""
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    log = _log_of(request)
    svc = request.app.state.svc
    raw_events = log.read(MAX_PUBLISH_EVENTS)
    secrets = _secret_values(svc)
    _, raw_redactions = scrub(json.dumps(raw_events, ensure_ascii=False), secrets)
    ux_events, _ = sanitize_events_for_publish(raw_events, log.session_id)
    raw_jsonl = "\n".join(json.dumps(e, ensure_ascii=False) for e in ux_events)
    clean_jsonl, clean_redactions = scrub(raw_jsonl, secrets)
    parsed_clean = [json.loads(line) for line in clean_jsonl.splitlines() if line.strip()]
    summary = _summary(parsed_clean)
    report, more = scrub(_render_report(parsed_clean, summary, log.session_id), secrets)
    return {
        "session": log.session_id,
        "raw_total": len(raw_events),
        "session_ux_total": len(parsed_clean),
        "redactions": max(raw_redactions, clean_redactions) + more,
        "summary": summary,
        "events": parsed_clean[:200],
        "report": report,
    }


@router.post("/testing/log")
async def ingest(request: Request):
    """Пачка событий из браузера: клики, переходы, ошибки JS, неудачные запросы."""
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    try:
        body = await request.json()
    except ClientDisconnect:
        return {"accepted": 0, "dropped": 0, "disconnected": True}
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, {"message": "тело запроса не разбирается как JSON"})
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        raise HTTPException(400, {"message": "ожидается {\"events\": [...]}"})
    log = _log_of(request)
    items = [(str(event.get("kind") or "ui.event")[:60],
              event.get("data") if isinstance(event.get("data"), dict) else {"value": event.get("data")})
             for event in events[:MAX_EVENTS_PER_POST] if isinstance(event, dict)]
    written = await log.write_many("ui", items)
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
    """Кнопка владельца: вычистить журнал и сохранить его в data_dir/testing/published.

    С 24.10.2026 журнал не коммитится и не пушится: он остаётся на этой машине.
    Если папка данных лежит в рабочей копии git и не игнорируется — отказ.
    """
    if not enabled():
        raise HTTPException(409, {"message": "режим тестового периода выключен"})
    log = _log_of(request)
    svc = request.app.state.svc
    try:
        return await asyncio.to_thread(_publish_sync, log, svc)
    except GitUnavailable as exc:
        raise HTTPException(503, {
            "message": "публикация невозможна: git не установлен или не найден в PATH. "
                       "Журнал остаётся на этой машине; поставьте Git и нажмите ещё раз",
            "detail": str(exc)[:300]}) from exc


def _publish_sync(log: SessionLog, svc) -> dict:
    raw_events = log.read(MAX_PUBLISH_EVENTS)
    if not raw_events:
        raise HTTPException(400, {"message": "журнал пуст — публиковать нечего"})

    secrets = _secret_values(svc)
    raw_all = "\n".join(json.dumps(e, ensure_ascii=False) for e in raw_events)
    _, raw_secret_redactions = scrub(raw_all, secrets)

    # Строгая фильтрация по текущей сессии (session == log.session_id) и UX-whitelist полей
    ux_events, _ = sanitize_events_for_publish(raw_events, log.session_id)
    if not ux_events:
        raise HTTPException(400, {"message": "журнал текущей сессии пуст — публиковать нечего"})

    raw_ux = "\n".join(json.dumps(e, ensure_ascii=False) for e in ux_events)
    clean_jsonl, ux_secret_redactions = scrub(raw_ux, secrets)
    parsed_clean = [json.loads(line) for line in clean_jsonl.splitlines() if line.strip()]
    summary = _summary(parsed_clean)
    report, more = scrub(_render_report(parsed_clean, summary, log.session_id), secrets)
    redactions = max(raw_secret_redactions, ux_secret_redactions) + more

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    base = f"{stamp}__{log.session_id}"
    out_dir = publish_dir(svc.settings.data_dir)
    if _could_reach_git(out_dir):
        # data_dir внутри рабочей копии git: файл лёг бы рядом с кодом и уехал бы
        # с первым же `git add`. Отказ, а не «сохранили куда-то».
        raise HTTPException(409, {"message": "публикация отменена: папка данных лежит внутри "
                                             "рабочей копии git — журнал туда не пишем"})
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{base}.md"
    jsonl_path = out_dir / f"{base}.jsonl"
    md_path.write_text(report, encoding="utf-8")
    jsonl_path.write_text(clean_jsonl + "\n", encoding="utf-8")

    # Последняя страховка: секрета не должно быть даже в локальной копии.
    for path in (md_path, jsonl_path):
        body = path.read_text(encoding="utf-8", errors="replace")
        for value in secrets:
            if value and value in body:
                path.unlink(missing_ok=True)
                raise HTTPException(500, {"message": "публикация отменена: секрет уцелел в файле"})

    # Ни git add, ни commit, ни push: журнал остаётся на этой машине.
    return {"published": True, "local_only": True,
            "dir": mask_home(str(out_dir)),
            "files": [md_path.name, jsonl_path.name],
            "redactions": redactions, "summary": summary, "steps": []}


def publish_dir(data_dir) -> Path:
    """Каталог вычищенных журналов: всегда внутри data_dir инсталляции."""
    return Path(data_dir) / PUBLISH_SUBDIR


def _could_reach_git(path: Path) -> bool:
    """Может ли журнал из этого каталога попасть в git.

    Каталог вне рабочей копии (маркера .git выше нет) — безопасен. Внутри рабочей
    копии безопасен только путь, который git игнорирует (так устроен data_dir
    исходной установки: `command-center/data/` в .gitignore). Если git не
    отвечает, считаем путь опасным: отказ лучше журнала в коммите.
    """
    try:
        current = Path(path).resolve()
    except OSError:
        current = Path(path).absolute()
    worktree = next((c for c in (current, *current.parents) if (c / ".git").exists()), None)
    if worktree is None:
        return False
    probe = current / "probe.jsonl"
    try:
        proc = _git(["check-ignore", "-q", "--", str(probe)], worktree)
    except GitUnavailable:
        return True
    return proc.returncode != 0


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
    _log_dir(svc.settings).mkdir(parents=True, exist_ok=True)
    log = SessionLog(svc.settings, rotate_existing=True)
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
