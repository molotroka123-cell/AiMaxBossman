"""Telegram section of Bossman settings: configure, check and run the companion.

Reuses the existing `bcc.telegram_companion` storage instead of inventing a
second one: the same `config.json` (no secrets) and the same encrypted
`credentials.enc` next to it. The bot token goes in once and never comes back
out through the API; only its last four characters are shown.

Endpoints (mounted under /api with the normal session/CSRF or token auth):
  GET  /telegram/settings  — saved settings (token masked) + companion status
  PUT  /telegram/settings  — validate server-side, write atomically, encrypt token
  POST /telegram/models    — list served model ids on loopback endpoints (no Telegram)
  POST /telegram/test      — Telegram getMe, ONLY when the owner presses the button
  POST /telegram/commands  — setMyCommands, ONLY when the owner presses the button
  GET  /telegram/status    — running / stopped / error + last stable error code
  POST /telegram/start     — start the companion process started by this server
  POST /telegram/stop      — stop only the process this server started

Scope (owner decision 2026-09-22): Telegram is for CONVERSATION with local
models (plus photo analysis and, when enabled, local image generation through
Bossman Studio) only. Delegation (/task) is not available yet: this API always saves
the owner without an executor, so the companion refuses /task and /confirm
without calling Bossman. No computer, browser or file action is reachable
from Telegram through this section.

Model choice is two roles, each bound to a live loopback endpoint and to the
exact id that endpoint serves: "best" (smartest) and "fastest". In the
companion config they are stored as local_* (best) and fast_* (fastest).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter()

SECRET_FIELDS = ("bot_token", "core_token", "local_token", "cloud_token", "proxy")
TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{20,100}$")
CODE_RE = re.compile(r"TELEGRAM_COMPANION=([A-Z0-9_]{3,80})")
# Owner's local endpoints: 8081 MAIN Qwen3.8-27B, 8082 FAST Qwen3.6-35B-A3B, 8083 GPT-OSS-120B.
ENDPOINTS = ("http://127.0.0.1:8083/v1", "http://127.0.0.1:8081/v1", "http://127.0.0.1:8082/v1")
BEST_ORDER = ("http://127.0.0.1:8083/v1", "http://127.0.0.1:8081/v1")
# Generation speed measured on the owner's machine 2026-09-22 (llama.cpp logs /
# one tiny probe); used only to suggest a default, the owner can change it.
MEASURED_TOK_S = {"http://127.0.0.1:8082/v1": 53.1, "http://127.0.0.1:8083/v1": 15.7,
                  "http://127.0.0.1:8081/v1": 8.8}
DEFAULTS = {"core_url": "http://127.0.0.1:8800", "local_timeout": 180.0, "max_tokens": 1024}

# Test seams: no network and no real process in tests.
TELEGRAM_TRANSPORT = None      # httpx transport for "Test bot"
MODELS_TRANSPORT = None        # httpx transport for "Check models"
_PROC: dict = {"proc": None, "started": None, "log": None}


def config_path() -> Path:
    override = os.environ.get("BOSSMAN_TELEGRAM_CONFIG")
    if override:
        return Path(override)
    from ..telegram_companion.__main__ import default_config
    return default_config()


# ---------------------------------------------------------------- storage

def _read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    return data


def _read_secrets(home: Path) -> dict:
    file = home / "credentials.enc"
    if not file.is_file():
        return {}
    from ..secrets import Vault
    value = Vault(home).decrypt(file.read_text(encoding="utf-8"))
    if value is None:
        raise HTTPException(409, "Сохранённые ключи Telegram не расшифровываются. Удалите credentials.enc и сохраните заново.")
    data = json.loads(value)
    return data if isinstance(data, dict) else {}


def _atomic_write(target: Path, text: str) -> None:
    from ..auth import _restrict_to_owner
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    _restrict_to_owner(tmp)
    os.replace(tmp, target)


def _default_persona() -> str:
    from ..telegram_companion.config import DEFAULT_PERSONA
    return DEFAULT_PERSONA


def _mask(token: str) -> str:
    return ("…" + token[-4:]) if token else ""


def _public(cfg: dict, secrets: dict) -> dict:
    people = cfg.get("people") or []
    owner = next((p for p in people if p.get("role") == "owner"), {})
    return {
        "configured": bool(cfg) and bool(secrets.get("bot_token")),
        "enabled": cfg.get("enabled", True) if cfg else False,
        "owner_id": owner.get("user_id"),
        "guest_ids": [p.get("user_id") for p in people if p.get("role") == "guest"],
        "best_url": cfg.get("local_url", ""),
        "best_model": cfg.get("local_model", ""),
        "fastest_url": cfg.get("fast_url", ""),
        "fastest_model": cfg.get("fast_model", ""),
        "default_route": "fastest" if cfg.get("default_route") == "fast" else "best",
        "fast_fallback": cfg.get("fast_fallback", True),
        "local_timeout": cfg.get("local_timeout", DEFAULTS["local_timeout"]),
        "max_tokens": cfg.get("max_tokens", DEFAULTS["max_tokens"]),
        "vision_route": {"main": "best", "fast": "fastest"}.get(cfg.get("vision_route"), "auto"),
        "image_enabled": bool(cfg.get("image_enabled", False)),
        "image_model": cfg.get("image_model", "sdcpp:z-image-turbo"),
        "image_size": cfg.get("image_size", 1024),
        "image_steps": cfg.get("image_steps", 8),
        "image_guests": bool(cfg.get("image_guests", False)),
        "persona": cfg.get("persona") or _default_persona(),
        "learning": {str(p.get("user_id")): bool((cfg.get("learning") or {}).get(str(p.get("user_id")), True))
                     for p in people},
        "retention_days": cfg.get("retention_days", 90),
        "owner_priority": cfg.get("owner_priority", True),
        "delegation": False,
        "delegation_available": False,
        "token_set": bool(secrets.get("bot_token")),
        "token_last4": _mask(secrets.get("bot_token", "")),
        "config_path": str(config_path()),
    }


# ---------------------------------------------------------------- models

async def _served_ids(url: str) -> list[str]:
    """GET <url>/models on a loopback endpoint; raises CompanionError on failure."""
    from ..telegram_companion.adapters import json_request
    async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False,
                                 transport=MODELS_TRANSPORT) as client:
        body = await json_request(client, "GET", url.rstrip("/") + "/models", timeout=5)
    rows = body.get("data") if isinstance(body, dict) else None
    return [r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)] if isinstance(rows, list) else []


async def _has_vision(url: str) -> bool | None:
    """llama.cpp /props modalities.vision (server started with --mmproj); None = unknown."""
    from ..telegram_companion.adapters import json_request
    from ..telegram_companion.config import CompanionError
    base = url[:-3] if url.endswith("/v1") else url
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False,
                                     transport=MODELS_TRANSPORT) as client:
            body = await json_request(client, "GET", base + "/props", timeout=5)
    except CompanionError:
        return None
    modalities = body.get("modalities") if isinstance(body, dict) else None
    return bool(isinstance(modalities, dict) and modalities.get("vision") is True)


def _loopback(url: str, label: str) -> str:
    from ..telegram_companion.config import local_url
    try:
        return local_url(url.strip())
    except ValueError:
        raise HTTPException(422, f"{label}: разрешён только локальный адрес вида http://127.0.0.1:порт/v1") from None


def suggest(endpoints: list[dict]) -> dict:
    """Best = 8083 GPT-OSS if served, else 8081 MAIN, else first served;
    fastest = 8082 if served, else the served endpoint with the highest measured speed."""
    served = [e for e in endpoints if e.get("models")]
    by_url = {e["url"]: e for e in served}
    best = next((by_url[u] for u in BEST_ORDER if u in by_url), served[0] if served else None)
    fastest = max(served, key=lambda e: MEASURED_TOK_S.get(e["url"], 0.0), default=None)
    pick = lambda e: {"url": e["url"], "model": e["models"][0]} if e else None  # noqa: E731
    return {"best": pick(best), "fastest": pick(fastest)}


class ModelsIn(BaseModel):
    urls: list[str] = Field(default_factory=lambda: list(ENDPOINTS), max_length=8)


@router.post("/telegram/models")
async def check_models(body: ModelsIn | None = None):
    from ..telegram_companion.config import CompanionError
    urls = (body.urls if body else None) or list(ENDPOINTS)
    endpoints = []
    for raw in urls:
        url = _loopback(raw, "Сервер модели")
        try:
            ids = await _served_ids(url)
            endpoints.append({"url": url, "status": "OK" if ids else "NO_MODELS", "models": ids,
                              "measured_tok_s": MEASURED_TOK_S.get(url),
                              "vision": await _has_vision(url) if ids else None})
        except CompanionError as exc:
            endpoints.append({"url": url, "status": str(exc), "models": [],
                              "measured_tok_s": MEASURED_TOK_S.get(url), "vision": None})
    return {"endpoints": endpoints, "suggested": suggest(endpoints), "scope": "CATALOG_ONLY_NOT_INFERENCE"}


# ---------------------------------------------------------------- settings

class SettingsIn(BaseModel):
    bot_token: str | None = Field(default=None, max_length=200)   # empty/None = keep saved one
    owner_id: int = Field(gt=0, lt=2**52)
    guest_ids: list[int] = Field(default_factory=list, max_length=7)
    best_url: str
    best_model: str = Field(min_length=1, max_length=1000)
    fastest_url: str = ""
    fastest_model: str = Field(default="", max_length=1000)
    default_route: str = "best"
    fast_fallback: bool = True
    local_timeout: float = Field(default=DEFAULTS["local_timeout"], ge=1, le=600, allow_inf_nan=False)
    max_tokens: int = Field(default=DEFAULTS["max_tokens"], ge=64, le=2048)
    vision_route: str = "auto"      # auto | best | fastest — "Модель для фото"
    image_enabled: bool = False
    image_model: str = Field(default="sdcpp:z-image-turbo", pattern=r"^sdcpp:[A-Za-z0-9_.-]{1,100}$")
    image_size: int = 1024
    image_steps: int = Field(default=8, ge=4, le=20)
    image_guests: bool = False
    persona: str = Field(default="", max_length=3000)            # empty = default «Манера общения»
    learning: dict[str, bool] = Field(default_factory=dict)       # {"<user_id>": learn on this person}
    retention_days: int = Field(default=90, ge=1, le=3650)
    owner_priority: bool = True
    delegation: bool = False
    enabled: bool = True


@router.get("/telegram/settings")
async def get_settings():
    path = config_path()
    try:
        cfg = _read_config(path)
    except (OSError, ValueError):
        raise HTTPException(409, "config.json компаньона повреждён; исправьте или удалите его и сохраните заново") from None
    return {**_public(cfg, _read_secrets(path.parent)), "status": _status()}


@router.put("/telegram/settings")
async def put_settings(body: SettingsIn, request: Request):
    from ..telegram_companion.config import CompanionError, Person, Settings
    path = config_path()
    home = path.parent
    try:
        existing = _read_config(path)
    except (OSError, ValueError):
        existing = {}
    secrets = _read_secrets(home)

    if body.delegation:
        raise HTTPException(422, "Поручения из Telegram пока недоступны: Telegram — только для беседы.")
    token = (body.bot_token or "").strip()
    if token and not TOKEN_RE.match(token):
        raise HTTPException(422, "Токен бота не похож на токен от @BotFather (цифры:буквы).")
    if not token:
        token = secrets.get("bot_token", "")
    if not token:
        raise HTTPException(422, "Нужен токен бота от @BotFather.")
    if any(g <= 0 or g >= 2**52 for g in body.guest_ids):
        raise HTTPException(422, "Telegram ID гостей — только положительные числа.")
    if body.owner_id in body.guest_ids or len(set(body.guest_ids)) != len(body.guest_ids):
        raise HTTPException(422, "Telegram ID повторяются: владелец и гости должны быть разными.")
    if body.default_route not in {"best", "fastest"}:
        raise HTTPException(422, "Модель для чата: best или fastest.")
    if body.image_size not in {512, 768, 1024}:
        raise HTTPException(422, "Размер картинки: 512, 768 или 1024.")
    if body.vision_route not in {"auto", "best", "fastest"}:
        raise HTTPException(422, "Модель для фото: auto, best или fastest.")

    best_url = _loopback(body.best_url, "Лучшая модель")
    fastest_model = body.fastest_model.strip()
    fastest_url = _loopback(body.fastest_url, "Самая быстрая модель") if fastest_model else ""
    if body.default_route == "fastest" and not fastest_model:
        raise HTTPException(422, "Чтобы чат отвечал самой быстрой моделью, выберите её.")
    if body.vision_route == "fastest" and not fastest_model:
        raise HTTPException(422, "Чтобы фото смотрела самая быстрая модель, выберите её.")

    # The exact served id must be used (llama-server answers with its own id).
    warnings = []
    for label, url, model in (("Лучшая", best_url, body.best_model.strip()), ("Самая быстрая", fastest_url, fastest_model)):
        if not model:
            continue
        try:
            ids = await _served_ids(url)
        except CompanionError:
            warnings.append(f"{label}: сервер модели сейчас не отвечает, имя модели не проверено")
            continue
        if ids and model not in ids:
            raise HTTPException(422, f"{label}: сервер отдаёт другую модель. Выберите из списка: {', '.join(ids[:5])}")

    # Chat-only: nobody gets an executor from this section.
    people = [Person(body.owner_id, body.owner_id, "owner", None)]
    people += [Person(gid, gid, "guest", None) for gid in body.guest_ids]

    cfg = {k: v for k, v in existing.items() if k not in SECRET_FIELDS}
    cfg.update({
        "people": [vars(p) for p in people],
        "local_url": best_url, "local_model": body.best_model.strip(),
        "fast_url": fastest_url, "fast_model": fastest_model,
        "default_route": "fast" if body.default_route == "fastest" else "main",
        "fast_fallback": body.fast_fallback,
        "local_timeout": float(body.local_timeout), "max_tokens": body.max_tokens,
        "vision_route": {"best": "main", "fastest": "fast"}.get(body.vision_route, "auto"),
        "image_enabled": body.image_enabled, "image_model": body.image_model,
        "image_size": body.image_size, "image_steps": body.image_steps, "image_guests": body.image_guests,
        "persona": body.persona.strip() or _default_persona(),
        "learning": {k: v for k, v in body.learning.items()
                     if k.isdigit() and int(k) in {body.owner_id, *body.guest_ids}},
        "retention_days": body.retention_days, "owner_priority": body.owner_priority,
        "enabled": body.enabled,
        "core_url": existing.get("core_url", DEFAULTS["core_url"]),
        # Telegram never falls back to a cloud model from this section.
        "cloud_daily_usd": 0.0, "cloud_model": "",
    })
    # Least privilege: chat-only needs no Command Center token (/status uses the
    # public liveness probe) and no cloud key.
    new_secrets = {**{k: secrets.get(k, "") for k in SECRET_FIELDS},
                   "bot_token": token, "cloud_token": "", "core_token": ""}
    if body.image_enabled:
        # /img goes through this Command Center's Studio API (provenance, verification, gallery).
        new_secrets["core_token"] = request.app.state.svc.auth.token
    try:
        Settings(**{**cfg, "people": tuple(people)}, **new_secrets)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Настройки не приняты: {exc}") from None

    from ..auth import _restrict_to_owner
    from ..secrets import Vault
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_to_owner(home)
    vault = Vault(home)
    _restrict_to_owner(vault.path)
    _atomic_write(home / "credentials.enc", vault.encrypt(json.dumps(new_secrets)))
    _atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    status = _status()
    if status["state"] == "running":
        warnings.append("Компаньон запущен: список людей применяется сразу, остальное — после перезапуска (Стоп → Старт).")
    if not body.enabled and _PROC["proc"] is not None and _PROC["proc"].poll() is None:
        _stop()
        status = _status()
    return {**_public(cfg, new_secrets), "status": status, "warnings": warnings}


# ---------------------------------------------------------------- test bot

@router.post("/telegram/test")
async def test_bot():
    """Owner-pressed only: getMe + webhook check. Sends no message to anyone."""
    from ..telegram_companion.adapters import Telegram
    from ..telegram_companion.config import CompanionError, load
    path = config_path()
    try:
        settings = load(path)
    except (OSError, ValueError, TypeError):
        raise HTTPException(409, "Сначала сохраните настройки Telegram.") from None
    telegram = Telegram(settings, transport=TELEGRAM_TRANSPORT)
    try:
        result = await telegram.preflight()
        return {"ok": True, "status": result["status"], "username": result.get("username", "")}
    except CompanionError as exc:
        return {"ok": False, "status": str(exc)}
    finally:
        await telegram.close()


# ---------------------------------------------------------------- токен: ротация и отзыв

class TokenIn(BaseModel):
    bot_token: str = Field(min_length=1, max_length=200)


def _write_secrets(home: Path, secrets: dict) -> None:
    from ..auth import _restrict_to_owner
    from ..secrets import Vault
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_to_owner(home)
    vault = Vault(home)
    _restrict_to_owner(vault.path)
    _atomic_write(home / "credentials.enc", vault.encrypt(json.dumps(secrets)))


@router.post("/telegram/token")
async def rotate_token(body: TokenIn):
    """Ротация токена бота — командой ВЛАДЕЛЬЦА в настройках Bossman, не в чате.

    Старый токен перестаёт опрашивать Telegram до записи нового: мост
    останавливается, потом переписывается секрет. В ответе — только маска;
    сам токен не возвращается, не логируется и никуда не отправляется.
    Отзыв старого токена на стороне Telegram делает @BotFather — это внешняя
    система, и утверждать за неё я не могу.
    """
    path = config_path()
    home = path.parent
    token = body.bot_token.strip()
    if not TOKEN_RE.match(token):
        raise HTTPException(422, "Токен бота не похож на токен от @BotFather (цифры:буквы).")
    secrets = _read_secrets(home)
    if not secrets.get("bot_token"):
        raise HTTPException(409, "Сначала сохраните настройки Telegram: ротировать нечего.")
    if token == secrets["bot_token"]:
        raise HTTPException(422, "Это тот же самый токен. Сначала выпустите новый в @BotFather.")
    await asyncio.to_thread(_stop)              # старый токен больше не опрашивает Telegram
    _write_secrets(home, {**secrets, "bot_token": token})
    return {"rotated": True, "bot_token_masked": _mask(token), "status": _status(),
            "next": "Старый токен отзовите в @BotFather (/revoke) — это делается вне Bossman. "
                    "Затем запустите мост заново."}


@router.delete("/telegram/token")
async def revoke_token():
    """Отзыв токена: мост останавливается, сохранённый токен стирается, Telegram
    выключается. Разговоры и локальная память не трогаются — их удаляет /forget
    или раздел людей."""
    path = config_path()
    home = path.parent
    secrets = _read_secrets(home)
    if not secrets.get("bot_token"):
        return {"revoked": False, "reason": "NO_TOKEN_STORED", "status": _status()}
    await asyncio.to_thread(_stop)
    _write_secrets(home, {**secrets, "bot_token": ""})
    try:
        cfg = _read_config(path)
    except (OSError, ValueError):
        cfg = {}
    if cfg:
        cfg["enabled"] = False
        _atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return {"revoked": True, "bot_token_masked": "", "status": _status(),
            "next": "Токен стёрт локально. Обязательно отзовите его и в @BotFather (/revoke): "
                    "пока он там жив, им может пользоваться тот, кто его видел."}


@router.post("/telegram/commands")
async def set_commands():
    """Owner-pressed only: register the bot's command list (setMyCommands)."""
    from ..telegram_companion.adapters import Telegram
    from ..telegram_companion.config import CompanionError, load
    from ..telegram_companion.service import BOT_COMMANDS
    try:
        settings = load(config_path())
    except (OSError, ValueError, TypeError):
        raise HTTPException(409, "Сначала сохраните настройки Telegram.") from None
    telegram = Telegram(settings, transport=TELEGRAM_TRANSPORT)
    try:
        await telegram.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]})
        return {"ok": True, "commands": [c for c, _ in BOT_COMMANDS]}
    except CompanionError as exc:
        return {"ok": False, "status": str(exc)}
    finally:
        await telegram.close()


# ---------------------------------------------------------------- per-user learning (local)

def _store():
    """The companion's own encrypted store (same files the companion uses)."""
    from ..telegram_companion.store import Store
    home = config_path().parent
    if not (home / "companion.sqlite3").exists():
        return None
    return Store(home)


def _people() -> list[dict]:
    try:
        cfg = _read_config(config_path())
    except (OSError, ValueError):
        cfg = {}
    return [p for p in cfg.get("people") or [] if isinstance(p, dict) and isinstance(p.get("user_id"), int)]


def _key(uid: int) -> str:
    if uid not in {p["user_id"] for p in _people()}:
        raise HTTPException(404, "Такого пользователя нет в списке Telegram.")
    return f"{uid}:{uid}"


@router.get("/telegram/people")
async def people():
    store = _store()
    cfg = _read_config(config_path()) if config_path().is_file() else {}
    learning = cfg.get("learning") or {}
    out = []
    try:
        for p in _people():
            key = f"{p['user_id']}:{p['user_id']}"
            profile = store.profile(key) if store else None
            out.append({"user_id": p["user_id"], "role": p.get("role"),
                        "learning": bool(learning.get(str(p["user_id"]), True)),
                        "paused": bool(store.get("learn_paused:" + key, False)) if store else False,
                        "notice_seen": bool(store.get("notice:" + key, False)) if store else False,
                        "entries": store.log_count(key) if store else 0,
                        "profile": profile})
    finally:
        if store:
            store.close()
    return {"items": out}


class ProfileIn(BaseModel):
    text: str = Field(max_length=2000)


@router.put("/telegram/profile/{uid}")
async def edit_profile(uid: int, body: ProfileIn):
    key = _key(uid)
    store = _store()
    if store is None:
        raise HTTPException(409, "Компаньон ещё не создавал хранилище: сначала запустите его.")
    try:
        from ..telegram_companion.service import clean_profile
        last = store.log_entries(key, limit=1)
        return store.put_profile(key, clean_profile(body.text), last[-1]["id"] if last else 0, edited_by_owner=True)
    finally:
        store.close()


@router.delete("/telegram/profile/{uid}")
async def delete_profile(uid: int):
    key = _key(uid)
    store = _store()
    if store is not None:
        try:
            store.delete_profile(key)
        finally:
            store.close()
    return {"ok": True}


@router.post("/telegram/export")
async def export():
    """Sanitised JSONL (per user + combined) under the companion data dir; nothing leaves the machine."""
    from bossman.ai_lab.sanitizer import SANITIZER_VERSION, sanitize_obj
    from ..auth import _restrict_to_owner
    import datetime as _dt
    store = _store()
    if store is None:
        return {"ok": True, "files": [], "records": 0}
    out_dir = config_path().parent / "exports"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_to_owner(out_dir)
    files, total, combined = [], 0, []
    try:
        for p in _people():
            key = f"{p['user_id']}:{p['user_id']}"
            lines = []
            for e in store.log_entries(key, limit=100000):
                record = sanitize_obj({"user_id": p["user_id"], "role": p.get("role"),
                                       "ts": _dt.datetime.fromtimestamp(e["ts"], _dt.timezone.utc).isoformat(),
                                       "messages": [{"role": "user", "content": e["user"]},
                                                    {"role": "assistant", "content": e["assistant"]}],
                                       "sanitizer": SANITIZER_VERSION})
                lines.append(json.dumps(record, ensure_ascii=False))
            if lines:
                target = out_dir / f"user-{p['user_id']}.jsonl"
                _atomic_write(target, "\n".join(lines) + "\n")
                files.append({"path": str(target), "records": len(lines), "user_id": p["user_id"]})
                combined += lines
                total += len(lines)
        if combined:
            target = out_dir / "all-users.jsonl"
            _atomic_write(target, "\n".join(combined) + "\n")
            files.append({"path": str(target), "records": len(combined), "user_id": None})
    finally:
        store.close()
    return {"ok": True, "files": files, "records": total, "dir": str(out_dir)}


# ---------------------------------------------------------------- process

def _command(path: Path) -> list[str]:
    """Run exactly the code this server runs (installed wheel or source checkout)."""
    import bcc
    roots = [str(Path(bcc.__file__).resolve().parents[1])]
    spec = importlib.util.find_spec("bossman")
    if spec is not None and spec.origin:
        roots.append(str(Path(spec.origin).resolve().parents[1]))
    boot = ("import sys; n=int(sys.argv[1]); sys.path[:0]=sys.argv[2:2+n]; "
            "from bcc.telegram_companion.__main__ import main; raise SystemExit(main(sys.argv[2+n:]))")
    return [sys.executable, "-I", "-c", boot, str(len(roots)), *roots, "--config", str(path)]


def _last_code(log: Path | None) -> str:
    if log is None or not log.is_file():
        return ""
    try:
        tail = log.read_bytes()[-4096:].decode("utf-8", "replace")
    except OSError:
        return ""
    codes = CODE_RE.findall(tail)
    return codes[-1] if codes else ""


def _status() -> dict:
    proc = _PROC["proc"]
    if proc is None:
        return {"state": "stopped", "managed": False, "last_error": _last_code(_PROC["log"])}
    code = proc.poll()
    if code is None:
        return {"state": "running", "managed": True, "pid": proc.pid,
                "since": _PROC["started"], "last_error": _last_code(_PROC["log"])}
    return {"state": "stopped" if code == 0 else "error", "managed": True, "exit_code": code,
            "last_error": _last_code(_PROC["log"]) or ("" if code == 0 else "EXITED_WITH_ERROR")}


def _stop() -> None:
    proc = _PROC["proc"]
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@router.get("/telegram/status")
async def status():
    return _status()


@router.post("/telegram/start")
async def start():
    from ..telegram_companion.config import load
    path = config_path()
    try:
        settings = load(path)
    except (OSError, ValueError, TypeError):
        raise HTTPException(409, "Сначала сохраните корректные настройки Telegram.") from None
    if not settings.enabled:
        raise HTTPException(409, "Telegram выключен в настройках. Включите переключатель и сохраните.")
    if not settings.bot_token:
        raise HTTPException(409, "Не сохранён токен бота.")
    if _PROC["proc"] is not None and _PROC["proc"].poll() is None:
        return _status()
    log = path.parent / "companion.log"
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log, "wb") as out:   # companion prints only stable codes, never secrets
        proc = subprocess.Popen(_command(path), stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, creationflags=flags)
    _PROC.update(proc=proc, started=time.time(), log=log)
    await asyncio.sleep(0.5)
    return _status()


@router.post("/telegram/stop")
async def stop():
    await asyncio.to_thread(_stop)
    return _status()


FEATURE = Feature(name="telegram_settings", router=router)
