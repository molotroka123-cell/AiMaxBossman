"""Честный офлайн-режим: что работает без сети — сказано ДО нажатия кнопки.

Пробел, который закрывает модуль: приложение никогда не объявляло заранее, какая
его часть требует внешнего адреса. Владелец узнавал об этом из тридцатисекундного
ожидания и невнятной ошибки. Здесь ответ даётся до запуска действия и с причиной.

Три состояния, а не два. `offline_ok` — подсистема работает без сети;
`needs_network` — нужен внешний адрес, и он назван; `unknown` — про подсистему
неизвестно, работает ли она офлайн. Последнее состояние настоящее, а не заглушка:
подменить «неизвестно» на «работает» — это ровно тот обман, ради устранения
которого модуль написан. Поэтому в матрицу не попадает ни одной догадки: модуль,
про который нет записи в KNOWN, честно получает `unknown`.

Матрица собирается из РЕАЛЬНО смонтированных фич (`svc.features`), а не из
списка в коде: список тут только уточняет, кто из них ходит наружу. Появилась
новая фича — она сразу видна как `unknown`, а не молча отсутствует.

Проверка сети при выключенном флаге НЕ делает ни одного внешнего запроса, и
говорит об этом в ответе (`external.checked = false`). Петлевая проба безопасна
всегда, поэтому выполняется и при выключенном флаге.

Приём с прокси (повторён из `bcc/desktop.py` и `bcc/providers.py::_client`):
переменные окружения ALL_PROXY/HTTP_PROXY/HTTPS_PROXY предназначены для внешних
адресов. Применить их к 127.0.0.1 — значит отправить локальный запрос туда, где
про этот адрес ничего не знают: соединение висит до таймаута, и диагноз
получается ложный — «локальная служба мертва» при работающей службе. Поэтому
петлевые пробы идут с `trust_env=False`, а внешние — с `trust_env=True`, тем же
транспортом, каким наружу ходит само приложение.

Ручки (обе только читают, работают и при выключенном флаге):
  GET /offline                  — матрица возможностей + состояние сети;
  GET /offline/can/{capability} — сработает ли конкретная возможность сейчас.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import providers as providers_t
from ..providers import is_local_url
from . import Feature

FLAG = "BOSSMAN_OFFLINE_MODE_ENABLED"
router = APIRouter()

# Пробы короткие намеренно: смысл модуля — быстрый честный отказ вместо ожидания.
LOOPBACK_TIMEOUT = 1.5
EXTERNAL_TIMEOUT = 2.5
MAX_EXTERNAL_TARGETS = 6

OFFLINE_OK = "offline_ok"
NEEDS_NETWORK = "needs_network"
UNKNOWN = "unknown"

# Имена переменных прокси показываем, значения — никогда: в них бывает
# user:password@host, а это секрет владельца.
PROXY_VARS = ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
              "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# --- требования подсистем -----------------------------------------------------

LOCAL = "local"            # сеть не нужна вообще
LOOPBACK = "loopback"      # нужна служба на этой же машине (127.0.0.1)
PROVIDERS = "providers"    # зависит от того, какие провайдеры настроены владельцем
EXTERNAL = "external"      # нужен внешний адрес
UNSURE = "unsure"          # честно неизвестно


@dataclass(frozen=True)
class Requirement:
    kind: str
    detail: str
    targets: tuple[str, ...] = ()      # URL'ы, которые можно проверить пробой


def _opencode_url() -> str:
    """Адрес OpenCode берём там же, где его берёт сам мост (features/tools_opencode)."""
    try:
        from .tools_opencode import DEFAULT_URL as default
    except Exception:                                   # модуль мог быть выключен
        default = "http://127.0.0.1:4096"
    return os.environ.get("OPENCODE_URL", default)


OPENROUTER_URL = "https://openrouter.ai/api/v1"          # то же, что в features/openrouter

# Записи составлены по коду соответствующих модулей, а не по названиям. Где
# уверенности нет — стоит UNSURE с причиной, и это окончательный ответ, а не TODO.
KNOWN: dict[str, Requirement] = {
    "core.api": Requirement(LOCAL, "сервер слушает 127.0.0.1: интерфейс и API живут без сети"),
    "core.db": Requirement(LOCAL, "SQLite в data_dir — единственный носитель состояния"),
    "core.providers": Requirement(PROVIDERS, "путь к модели: зависит от настроенных провайдеров"),

    "agentmap": Requirement(LOCAL, "граф собирается из таблиц агентов и раннов"),
    "apps": Requirement(LOOPBACK, "карточки приложений проверяют порты на 127.0.0.1"),
    "apps_control": Requirement(LOOPBACK, "запуск приложения и проверка его готовности "
                                          "стучатся только в 127.0.0.1"),
    "benchlab": Requirement(PROVIDERS, "бенчмарк гоняет живые модели через адаптер провайдера"),
    "browser": Requirement(EXTERNAL, "навигация ведёт на сайт, адрес которого заранее неизвестен"),
    "cache_intel": Requirement(LOCAL, "агрегирует уже записанные события кэша"),
    "code_intel": Requirement(LOCAL, "LSP и индекс кода — локальные процессы и файлы"),
    "command_bar": Requirement(UNSURE, "выполняет любую ручку этого же приложения: "
                                       "локальную или уходящую в модель — какую именно, "
                                       "решает введённая команда"),
    "coding_sessions": Requirement(LOCAL, "git-worktree на этой машине"),
    "deep_fix": Requirement(LOCAL, "хэш плана и хуки движка, всё внутри БД"),
    "forks": Requirement(PROVIDERS, "форк переигрывает ран, то есть снова зовёт модель"),
    "governor": Requirement(LOCAL, "решения принимаются по записям в БД"),
    "healing": Requirement(LOCAL, "считает окно ошибок; сам наружу не ходит"),
    "images": Requirement(LOCAL, "исполняется только детерминированный локальный провайдер"),
    "missions": Requirement(PROVIDERS, "миссия ставит задачи, задачи идут в модель"),
    "nl_orchestra": Requirement(LOCAL, "разбор состава оркестра и запись в БД"),
    "nl_permissions": Requirement(LOCAL, "компиляция прав из текста, без вызовов наружу"),
    "offline_mode": Requirement(LOCAL, "этот модуль: петлевая проба и чтение матрицы"),
    "opencode": Requirement(LOOPBACK, "OpenCode — служба на этой машине",
                            targets=()),               # адрес подставляется динамически
    "openrouter": Requirement(EXTERNAL, "каталог и вызовы моделей OpenRouter",
                              targets=(OPENROUTER_URL,)),
    "plugins": Requirement(UNSURE, "адаптеров много: часть уходит в канал или облачный LLM, "
                                   "часть локальна; какой вызовут — заранее неизвестно"),
    "resources": Requirement(LOCAL, "метрики машины и резервы памяти"),
    "review_gate": Requirement(PROVIDERS, "ревью выполняет модель"),
    "router": Requirement(PROVIDERS, "выбор модели упирается в доступность провайдера"),
    "skills": Requirement(PROVIDERS, "запуск скилла идёт обычной задачей через модель"),
    "snapshot": Requirement(LOCAL, "копия БД и манифест на диске"),
    "task_exchange": Requirement(LOCAL, "обмен задачами через файлы в data_dir"),
    "terminal": Requirement(LOCAL, "сам терминал локален; куда пойдёт запущенная "
                                   "команда — вне зоны знания этого модуля"),
    "tools_browser": Requirement(EXTERNAL, "инструмент открывает внешние страницы, "
                                           "адрес заранее неизвестен"),
    "tools_code": Requirement(LOCAL, "поиск по локальному индексу кода"),
    "tools_facts": Requirement(LOCAL, "факты хранятся в БД"),
    "tools_mcp": Requirement(UNSURE, "транспорт stdio локален, но что делает снаружи сам "
                                     "MCP-сервер — неизвестно"),
    "tools_memory": Requirement(LOCAL, "vault и индекс памяти лежат на диске"),
    "tools_openclaw": Requirement(UNSURE, "адрес Gateway задаёт владелец: он бывает и "
                                          "локальным, и внешним"),
    "tools_opencode": Requirement(LOOPBACK, "инструменты обращаются к OpenCode на этой машине"),
    "tools_terminal": Requirement(LOCAL, "запуск команд на этой машине"),
    "workflow": Requirement(LOCAL, "канвас и валидация графа — данные в БД"),
}

UNLISTED = Requirement(UNSURE, "записи в матрице нет: работает ли подсистема офлайн — неизвестно")


def requirement_for(name: str) -> Requirement:
    req = KNOWN.get(name, UNLISTED)
    if name in ("opencode", "tools_opencode") and not req.targets:
        return Requirement(req.kind, req.detail, (_opencode_url(),))
    return req


# --- пробы --------------------------------------------------------------------

def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def free_port() -> int:
    """Эфемерный порт, тут же отпущенный: заведомо закрытый адрес для контрольной пробы.

    Нужен, чтобы проверить саму проверку: если «живым» опознаётся и закрытый порт,
    отчёт бесполезен — значит, соединения кто-то перехватывает.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def http_probe(url: str, *, timeout: float, trust_env: bool) -> dict:
    """Один HTTP GET с коротким таймаутом. Любой ответ сервера = адрес живой.

    В detail попадает ТОЛЬКО имя класса ошибки: текст исключения httpx содержит
    URL прокси, а там бывает пароль.
    """
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env,
                                     follow_redirects=False) as client:
            resp = await client.get(url)
        return {"alive": True, "status": resp.status_code, "detail": f"HTTP {resp.status_code}",
                "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:                    # httpx.HTTPError, ImportError (socks) и прочее
        return {"alive": False, "status": None, "detail": type(exc).__name__,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}


async def probe_loopback(timeout: float = LOOPBACK_TIMEOUT) -> dict:
    """Жив ли петлевой транспорт. Цель — собственный одноразовый слушатель.

    Своя же служба — единственный адрес, про который заранее известно, что он
    обязан ответить: чужая служба может быть просто не запущена, и её молчание
    ничего не говорит о сети. Плюс контрольная проба закрытого порта.
    """
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(4096)
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n"
                         b"Connection: close\r\n\r\n")
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        alive = await http_probe(f"http://127.0.0.1:{port}/", timeout=timeout, trust_env=False)
    finally:
        server.close()
        await server.wait_closed()

    closed = free_port()
    control = await http_probe(f"http://127.0.0.1:{closed}/", timeout=timeout, trust_env=False)
    return {
        "alive": bool(alive["alive"]),
        "target": f"127.0.0.1:{port}",
        "detail": alive["detail"],
        "elapsed_ms": alive["elapsed_ms"],
        "proxy_env_ignored": True,
        "sanity": {"closed_port": f"127.0.0.1:{closed}",
                   "refused": not control["alive"],
                   "detail": control["detail"]},
    }


# --- провайдеры как источник внешних адресов ----------------------------------

@dataclass(frozen=True)
class ProvidersView:
    readable: bool
    total: int
    external: tuple[str, ...]          # base_url провайдеров, которые не локальные


async def _providers_view(svc) -> ProvidersView:
    try:
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(providers_t.c.base_url))).fetchall()
    except Exception:
        # БД не прочиталась — это тоже честное «неизвестно», а не «всё хорошо».
        return ProvidersView(readable=False, total=0, external=())
    urls = [(r._mapping["base_url"] or "").strip() for r in rows]
    urls = [u for u in urls if u]
    external = tuple(dict.fromkeys(u for u in urls if not is_local_url(u)))
    return ProvidersView(readable=True, total=len(urls), external=external)


# --- матрица ------------------------------------------------------------------

def _capability_names(svc) -> list[str]:
    """Реально смонтированные фичи + ядро. Список берётся из приложения, не из кода."""
    names = sorted({f.name for f in getattr(svc, "features", [])})
    core = [n for n in KNOWN if n.startswith("core.")]
    return sorted(core) + names


def resolve(name: str, provs: ProvidersView) -> dict:
    """Строка матрицы: статус, что нужно наружу, и почему именно так."""
    req = requirement_for(name)
    row = {"capability": name, "requirement": req.kind, "detail": req.detail,
           "requires": [], "targets": list(req.targets)}

    if req.kind == LOCAL:
        row["status"] = OFFLINE_OK
        row["reason"] = "сеть не нужна: " + req.detail
    elif req.kind == LOOPBACK:
        row["status"] = OFFLINE_OK
        row["requires"] = [_host(t) or t for t in req.targets] or ["127.0.0.1"]
        row["reason"] = ("наружу не ходит, но нужна служба на этой машине: " + req.detail)
    elif req.kind == EXTERNAL:
        row["status"] = NEEDS_NETWORK
        row["requires"] = [_host(t) for t in req.targets if _host(t)] or ["внешний адрес"]
        row["reason"] = "нужен внешний адрес: " + req.detail
    elif req.kind == PROVIDERS:
        if not provs.readable:
            row["status"] = UNKNOWN
            row["reason"] = "список провайдеров не прочитан — судить не о чем"
        elif provs.total == 0:
            row["status"] = UNKNOWN
            row["reason"] = "провайдеры не настроены: заранее сказать нельзя"
        elif not provs.external:
            row["status"] = OFFLINE_OK
            row["reason"] = "все настроенные провайдеры — локальные адреса"
        else:
            row["status"] = NEEDS_NETWORK
            row["requires"] = [h for h in (_host(u) for u in provs.external) if h]
            row["targets"] = list(provs.external)
            row["reason"] = "настроен внешний провайдер: " + ", ".join(row["requires"])
    else:
        row["status"] = UNKNOWN
        row["reason"] = req.detail
    return row


def _proxy_env() -> list[str]:
    """Имена заданных переменных прокси (значения не показываем никогда)."""
    return [name for name in PROXY_VARS if os.environ.get(name, "").strip()]


async def _external_state(rows: list[dict], provs: ProvidersView) -> dict:
    """Внешняя доступность. При выключенном флаге НИ ОДНОГО запроса наружу."""
    targets: list[str] = []
    for row in rows:
        if row["status"] == NEEDS_NETWORK:
            targets.extend(row["targets"])
    targets.extend(provs.external)
    targets = list(dict.fromkeys(t for t in targets if _host(t)))[:MAX_EXTERNAL_TARGETS]

    if not enabled():
        return {"checked": False,
                "reason": f"флаг {FLAG} выключен: наружу не ходим и о доступности не судим",
                "targets": [_host(t) for t in targets], "results": []}
    if not targets:
        return {"checked": False, "reason": "проверять нечего: внешних адресов не настроено",
                "targets": [], "results": []}

    probes = await asyncio.gather(*[
        http_probe(t, timeout=EXTERNAL_TIMEOUT, trust_env=True) for t in targets])
    return {"checked": True,
            "reason": "проверены только адреса, уже настроенные в приложении",
            "targets": [_host(t) for t in targets],
            "results": [{"host": _host(t), **p} for t, p in zip(targets, probes)]}


# --- ручки --------------------------------------------------------------------

@router.get("/offline")
async def offline_report(request: Request):
    svc = request.app.state.svc
    provs = await _providers_view(svc)
    rows = [resolve(name, provs) for name in _capability_names(svc)]
    loopback = await probe_loopback()
    external = await _external_state(rows, provs)
    summary = {OFFLINE_OK: 0, NEEDS_NETWORK: 0, UNKNOWN: 0}
    for row in rows:
        summary[row["status"]] += 1
    return {
        "enabled": enabled(),
        "flag": FLAG,
        "network": {"loopback": loopback, "external": external, "proxy_env": _proxy_env()},
        "capabilities": rows,
        "summary": summary,
        "notes": [
            "три состояния: offline_ok / needs_network / unknown; "
            "«неизвестно» не значит «работает»",
            "петлевые пробы идут мимо прокси из окружения: "
            "прокси предназначен для внешних адресов",
        ],
    }


@router.get("/offline/can/{capability}")
async def can(request: Request, capability: str):
    """Сработает ли действие сейчас — ДО того, как его запустили.

    Ответ трёхзначный: yes / no / unknown. `will_work=null` означает именно
    «не знаем», и подменять его на true нельзя — на этом обмане и строится
    ожидание в тридцать секунд вместо честного отказа.
    """
    svc = request.app.state.svc
    names = _capability_names(svc)
    if capability not in names:
        raise HTTPException(404, {"message": f"нет такой возможности: {capability}",
                                  "known": names})

    provs = await _providers_view(svc)
    row = resolve(capability, provs)
    loopback = await probe_loopback()
    checked = {"loopback": True, "external": False}
    answer, will_work, reason = UNKNOWN, None, row["reason"]

    if not loopback["alive"]:
        answer, will_work = "no", False
        reason = ("петлевая сеть не работает на этой машине — не сработает ничего, "
                  f"включая интерфейс ({loopback['detail']})")
    elif row["status"] == OFFLINE_OK and row["requirement"] == LOCAL:
        answer, will_work = "yes", True
    elif row["status"] == OFFLINE_OK and row["requirement"] == LOOPBACK:
        # Локальная служба может быть просто не запущена — это отдельный «нет».
        target = (row["targets"] or [""])[0]
        if target:
            probe = await http_probe(target, timeout=LOOPBACK_TIMEOUT, trust_env=False)
            answer = "yes" if probe["alive"] else "no"
            will_work = bool(probe["alive"])
            reason = (f"служба на {_host(target)} отвечает" if probe["alive"]
                      else f"служба на {_host(target)} не слушает ({probe['detail']}) — "
                           f"действие не сработает, пока её не запустить")
        else:
            answer, will_work = "yes", True
    elif row["status"] == OFFLINE_OK:
        answer, will_work = "yes", True
    elif row["status"] == NEEDS_NETWORK:
        if not enabled():
            reason = (f"{row['reason']}; доступность не проверена: флаг {FLAG} выключен, "
                      "внешних запросов модуль не делает")
        elif not row["targets"]:
            reason = f"{row['reason']}; проверить нечего: адрес заранее неизвестен"
        else:
            checked["external"] = True
            probes = await asyncio.gather(*[
                http_probe(t, timeout=EXTERNAL_TIMEOUT, trust_env=True) for t in row["targets"]])
            dead = [(t, p) for t, p in zip(row["targets"], probes) if not p["alive"]]
            if dead:
                answer, will_work = "no", False
                reason = "не отвечает: " + ", ".join(f"{_host(t)} ({p['detail']})"
                                                     for t, p in dead)
            else:
                answer, will_work = "yes", True
                reason = "отвечают: " + ", ".join(_host(t) for t in row["targets"])

    return {"capability": capability, "answer": answer, "will_work": will_work,
            "status": row["status"], "requires": row["requires"], "reason": reason,
            "checked": checked, "enabled": enabled(), "flag": FLAG}


FEATURE = Feature(name="offline_mode", router=router)
