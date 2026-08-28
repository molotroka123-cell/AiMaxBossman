"""Реестр приложений: карточки лаунчера строятся из манифестов, а не из вёрстки.

До этого модуля связь «ядро ↔ приложение» существовала только на бумаге: файлы
`apps/*/app.manifest.yaml` лежали в репозитории, но их не читала ни одна строка
кода. Карточку нового приложения пришлось бы дорисовывать руками в интерфейсе —
и она разошлась бы с действительностью в первый же день.

Здесь манифест становится источником истины. Приложение появляется на главной,
добавив блок `ui:` в свой манифест, и ничего больше.

Границы, которые модуль не переходит:

* ядро НЕ импортирует код приложений и не запускает их. Оно читает файл-описание
  и, если приложение слушает порт, спрашивает у него состояние по HTTP;
* приложение, которое не отвечает, показывается как остановленное с честной
  причиной, а не пропадает из списка. Пропавшая карточка выглядит как «такого
  приложения нет», а это неправда;
* ничего из манифеста не исполняется. Это данные.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request

from ..config import ROOT
from . import Feature

APPS_DIR = ROOT.parent / "apps"
PROBE_TIMEOUT = 1.2          # приложение на этой же машине отвечает мгновенно
CACHE_TTL = 10.0             # чтобы открытая главная не долбила соседей опросами

_cache: dict[str, Any] = {"at": 0.0, "apps": []}


# ------------------------------------------------------------------ манифесты

def _manifest_files() -> list[Path]:
    if not APPS_DIR.is_dir():
        return []
    return sorted(APPS_DIR.glob("*/app.manifest.yaml"))


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _http_calls(raw: dict[str, Any]) -> dict[str, str]:
    """Карта операций контракта. Два приложения назвали блок по-разному."""
    for key in ("control_contract", "control"):
        block = raw.get(key)
        if isinstance(block, dict):
            calls = block.get("http") or block.get("calls")
            if isinstance(calls, dict):
                return {str(k): str(v) for k, v in calls.items()}
    return {}


def _describe(path: Path) -> dict[str, Any] | None:
    raw = _load(path)
    if not raw or not raw.get("id"):
        return None
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    calls = _http_calls(raw)
    port = raw.get("default_port")
    return {
        "id": str(raw["id"]),
        "name": str(raw.get("name") or raw["id"]),
        "subtitle": str(ui.get("subtitle") or "").strip(),
        "description": " ".join(str(raw.get("description") or "").split()),
        "version": str(raw.get("version") or ""),
        "app_number": raw.get("app_number"),
        "icon": str(ui.get("icon") or "box"),
        "accent": str(ui.get("accent") or ""),
        "theme": str(ui.get("theme") or ""),
        "order": int(ui.get("order") or 99),
        "port": int(port) if isinstance(port, int) else None,
        "health_path": str(ui.get("health_path") or calls.get("health", "").split(" ")[-1]
                           or "/health"),
        "metrics_path": str(ui.get("metrics_path") or calls.get("metrics", "").split(" ")[-1]
                            or ""),
        "facts": [f for f in (ui.get("facts") or []) if isinstance(f, dict)],
        "actions": [a for a in (ui.get("actions") or []) if isinstance(a, dict)],
        "commands": [str(c) for c in (raw.get("commands") or [])],
        "operations": sorted(calls),
        "permissions": raw.get("permissions") if isinstance(raw.get("permissions"),
                                                            dict) else {},
        "providers": raw.get("providers") if isinstance(raw.get("providers"), dict) else {},
        "manifest_path": str(path.relative_to(ROOT.parent)),
        "route": f"app/{raw['id']}",
    }


# ------------------------------------------------------------------ живое состояние

async def _probe(app: dict[str, Any]) -> dict[str, Any]:
    """Спросить приложение, как оно себя чувствует. Молчание — это ответ.

    Локальный адрес никогда не идёт через прокси: переменные окружения с
    прокси превратили бы «сосед на 127.0.0.1» в таймаут и показали бы
    работающее приложение остановленным.
    """
    port = app.get("port")
    if not port:
        return {"reachable": False, "status": "NOT_CONFIGURED",
                "detail": "в манифесте не указан default_port"}
    base = f"http://127.0.0.1:{port}"
    out: dict[str, Any] = {"reachable": False, "status": "STOPPED", "detail": "",
                           "health": {}, "metrics": {}}
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, trust_env=False) as client:
            health = await client.get(base + (app.get("health_path") or "/health"))
            out["reachable"] = health.status_code < 500
            out["status"] = "LIVE" if health.status_code < 400 else "DEGRADED"
            try:
                out["health"] = health.json()
            except ValueError:
                out["health"] = {}
            if app.get("metrics_path"):
                try:
                    metrics = await client.get(base + app["metrics_path"])
                    if metrics.status_code < 400:
                        out["metrics"] = metrics.json()
                except (httpx.HTTPError, ValueError):
                    pass          # метрики необязательны, здоровье важнее
    except httpx.HTTPError as exc:
        out["detail"] = f"{type(exc).__name__}: приложение не отвечает на {base}"
    return out


def _dig(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _resolve_facts(app: dict[str, Any], live: dict[str, Any]) -> list[dict[str, str]]:
    """Значения фактов карточки: из манифеста либо из живого ответа приложения.

    Факт, значение которого взять неоткуда, показывается с запасным текстом, а
    не выбрасывается: исчезнувшая строка читается как «этого не бывает», а
    правда в том, что данных сейчас нет.
    """
    source = {"health": live.get("health") or {}, "metrics": live.get("metrics") or {}}
    facts: list[dict[str, str]] = []
    for item in app.get("facts") or []:
        label = str(item.get("label") or "")
        if not label:
            continue
        if item.get("value") is not None:
            facts.append({"label": label, "value": str(item["value"]), "live": False})
            continue
        found = _dig(source, str(item.get("from") or "")) if item.get("from") else None
        if found is None:
            facts.append({"label": label, "value": str(item.get("fallback") or "—"),
                          "live": False})
        else:
            facts.append({"label": label, "value": str(found), "live": True})
    return facts


async def collect(force: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    if not force and _cache["apps"] and now - float(_cache["at"]) < CACHE_TTL:
        return _cache["apps"]

    described = [d for d in (_describe(p) for p in _manifest_files()) if d]
    if described:
        probes = await asyncio.gather(*(_probe(a) for a in described))
    else:
        probes = []
    result = []
    for app, live in zip(described, probes):
        card = dict(app)
        card["reachable"] = live["reachable"]
        card["status"] = live["status"]
        card["detail"] = live.get("detail", "")
        card["facts"] = _resolve_facts(app, live)
        card["base_url"] = f"http://127.0.0.1:{app['port']}" if app.get("port") else ""
        result.append(card)
    result.sort(key=lambda a: (a["order"], a["name"]))
    _cache.update({"at": now, "apps": result})
    return result


# ------------------------------------------------------------------ HTTP

router = APIRouter(tags=["apps"])


@router.get("/apps")
async def list_apps(request: Request, refresh: bool = False) -> dict:
    apps = await collect(force=refresh)
    return {"apps": apps,
            "total": len(apps),
            "live": sum(1 for a in apps if a["status"] == "LIVE"),
            "apps_dir": str(APPS_DIR)}


@router.get("/apps/{app_id}")
async def get_app(app_id: str, request: Request) -> dict:
    for app in await collect():
        if app["id"] == app_id:
            return {"app": app}
    raise HTTPException(404, {"message": f"приложение {app_id} не найдено в {APPS_DIR}"})


FEATURE = Feature(name="apps", router=router)
