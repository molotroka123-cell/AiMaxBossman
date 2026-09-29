"""Jeff settings panel (Bossman Command v0.1): owner-only API over jeff-settings.json.

The owner tunes how Jeff (the participant bot/window) talks: eight style
sliders, a short style note, per-participant overrides and spend caps. The
overlay file is the one Jeff itself re-reads on every message
(``bcc.pit.jeff_settings``); this module only validates and writes it.

Endpoints (mounted under /api with the normal session/CSRF or token auth —
participants have no Command Center session, so they can never reach them):
  GET    /jeff-settings                    — overlay + presets + known participants
  PUT    /jeff-settings                    — defaults (style) + budgets; overrides kept
  GET    /jeff-settings/users/{person_key} — one participant's override
  PUT    /jeff-settings/users/{person_key} — set it
  DELETE /jeff-settings/users/{person_key} — remove it (defaults apply again)
  POST   /jeff-settings/reset              — stock Jeff for everyone (budgets kept)

Participants are listed by display name (Jeff window account) or a neutral
label; the API never returns a Telegram ID, only PIT person keys (HMAC).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..pit import jeff_settings as js
from ..pit.config import BEHAVIOR_SCALE_NAMES, default_behavior_scales, pit_home
from ..pit.identity import validate_person_key
from . import Feature

router = APIRouter()

SCALE_LABELS = {
    "initiative": "Инициатива", "curiosity": "Любопытство", "depth": "Глубина",
    "brevity": "Краткость", "warmth": "Теплота", "humor": "Юмор",
    "directness": "Прямота", "creativity": "Креативность",
}
SCALE_HINTS = {
    "initiative": "сам предлагает следующий шаг",
    "curiosity": "уточняет недостающее по задаче",
    "depth": "больше деталей и объяснений",
    "brevity": "меньше слов и повторов",
    "warmth": "дружелюбнее и мягче",
    "humor": "больше уместных шуток",
    "directness": "говорит прямо, без обиняков",
    "creativity": "нестандартные идеи",
}


# ---------------------------------------------------------------- locating Jeff's data

def _cc_data_dir(request: Request) -> Path:
    return Path(request.app.state.svc.settings.data_dir)


def _pit_config(cc_dir: Path) -> dict:
    try:
        data = json.loads((pit_home(cc_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _jeff_data_dir(cc_dir: Path, cfg: dict) -> Path:
    """Where Jeff itself reads the overlay: the PIT config's data_dir (it may be repointed)."""
    raw = str(cfg.get("data_dir") or "").strip()
    return Path(raw) if raw else cc_dir


def _salt(cc_dir: Path) -> bytes | None:
    try:
        from ..pit.config import _read_credentials
        value = _read_credentials(pit_home(cc_dir)).get("identity_salt", "")
        raw = bytes.fromhex(value) if value else b""
    except Exception:  # noqa: BLE001 — no salt only means neutral labels
        return None
    return raw if len(raw) >= 16 else None


def _paths(request: Request) -> tuple[Path, Path, dict]:
    cc_dir = _cc_data_dir(request)
    cfg = _pit_config(cc_dir)
    jeff_dir = _jeff_data_dir(cc_dir, cfg)
    return js.settings_path(jeff_dir), jeff_dir, cfg


def _stock_scales(cfg: dict) -> dict[str, int]:
    raw = cfg.get("behavior_scales")
    if isinstance(raw, dict) and set(raw) == set(BEHAVIOR_SCALE_NAMES):
        try:
            return {name: js.clamp_scale(raw[name]) for name in BEHAVIOR_SCALE_NAMES}
        except js.OverlayError:
            pass
    return default_behavior_scales()


def participants(cc_dir: Path, jeff_dir: Path, cfg: dict, overlay: dict) -> list[dict]:
    """Known participants by display name / neutral label; values are PIT person keys."""
    from ..pit.identity import derive_person_key
    from ..pit.web import derive_web_person_key
    out: list[dict] = []
    seen: set[str] = set()

    def add(key: str, label: str, surface: str) -> None:
        if key in seen:
            return
        seen.add(key)
        out.append({"key": key, "label": label, "surface": surface,
                    "has_override": key in (overlay.get("users") or {})})

    salt = _salt(cc_dir)
    if salt:
        try:
            accounts = json.loads((pit_home(jeff_dir) / "web" / "accounts.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            accounts = {}
        rows = sorted((row for row in (accounts.get("users") or {}).values() if isinstance(row, dict)),
                      key=lambda row: int(row.get("id", 0)))
        for row in rows:
            try:
                add(derive_web_person_key(int(row["id"]), salt), str(row.get("name") or "")[:32]
                    or "Участник окна Jeff", "window")
            except (KeyError, TypeError, ValueError):
                continue
        guest_no = 0
        for person in cfg.get("people") or []:
            if not isinstance(person, dict):
                continue
            try:
                key = derive_person_key(int(person["user_id"]), salt)
            except (KeyError, TypeError, ValueError):
                continue
            if person.get("role") == "owner":
                label = "Telegram · владелец"
            else:
                guest_no += 1
                label = f"Telegram · участник {guest_no}"
            add(key, label, "telegram")
    root = pit_home(jeff_dir) / "personalities"
    try:
        extra = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        extra = []
    for name in extra:
        try:
            key = validate_person_key(name)
        except ValueError:
            continue
        add(key, f"Участник {key[:6]}", "telegram")
    for key in sorted((overlay.get("users") or {})):
        add(key, f"Участник {key[:6]} (не найден)", "unknown")
    return out


# ---------------------------------------------------------------- storage helpers

def _current(path: Path) -> tuple[dict, bool, str]:
    """(overlay to edit, valid, error). An invalid file is shown, never silently used."""
    if not path.is_file():
        return js.empty_overlay(), True, ""
    try:
        return js.normalize(json.loads(path.read_text(encoding="utf-8"))), True, ""
    except (OSError, ValueError) as exc:
        return js.empty_overlay(), False, str(exc)[:200]


def _save(path: Path, overlay: dict) -> dict:
    if path.is_file():
        _, valid, _ = _current(path)
        if not valid:
            # Keep the owner's broken hand edit instead of overwriting it blind.
            backup = path.with_name(path.name + f".invalid-{time.strftime('%Y%m%d-%H%M%S')}.bak")
            os.replace(path, backup)
    try:
        return js.write_overlay(path, overlay)
    except js.OverlayError as exc:
        raise HTTPException(422, f"Настройки Jeff не сохранены: {exc}") from None


def _key(person_key: str) -> str:
    try:
        return validate_person_key(person_key)
    except ValueError:
        raise HTTPException(422, "Участник задаётся ключом Jeff (64 hex), не Telegram ID.") from None


# ---------------------------------------------------------------- API

class StyleIn(BaseModel):
    system_extra: str | None = Field(default=None, max_length=4000)
    behavior_scales: dict[str, float] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    defaults: StyleIn = Field(default_factory=StyleIn)
    budgets: dict[str, float] | None = None


def _style_payload(body: StyleIn, *, keep_absent_extra: bool) -> dict:
    raw: dict = {"behavior_scales": dict(body.behavior_scales)}
    if body.system_extra is not None or not keep_absent_extra:
        raw["system_extra"] = body.system_extra or ""
    try:
        return js.normalize_profile(raw, keep_absent_extra=keep_absent_extra)
    except js.OverlayError as exc:
        raise HTTPException(422, f"Неверные настройки: {exc}") from None


@router.get("/jeff-settings")
async def get_settings(request: Request):
    path, jeff_dir, cfg = _paths(request)
    overlay, valid, error = _current(path)
    budget = js.budget_caps(jeff_dir, configured_usd_per_day=_configured()[0],
                            configured_usd_per_job=_configured()[1])
    return {
        "path": str(path), "exists": path.is_file(), "valid": valid, "error": error,
        "settings": overlay,
        "presets": js.PRESETS, "preset_labels": js.PRESET_LABELS,
        "scale_names": list(BEHAVIOR_SCALE_NAMES), "scale_labels": SCALE_LABELS,
        "scale_hints": SCALE_HINTS, "scale_range": [js.SCALE_MIN, js.SCALE_MAX],
        "stock_scales": _stock_scales(cfg), "system_extra_max": js.SYSTEM_EXTRA_MAX,
        "jeff_configured": bool(cfg),
        "participants": participants(_cc_data_dir(request), jeff_dir, cfg, overlay),
        "budget": {"configured": budget["configured"], "effective": budget["effective"]},
    }


def _configured() -> tuple[float, float]:
    from ..pit.cloud_budget import CONFIGURED_USD_PER_DAY, CONFIGURED_USD_PER_JOB
    return CONFIGURED_USD_PER_DAY, CONFIGURED_USD_PER_JOB


@router.put("/jeff-settings")
async def put_settings(body: SettingsIn, request: Request):
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    overlay["defaults"] = _style_payload(body.defaults, keep_absent_extra=False)
    if body.budgets is not None:
        try:
            overlay["budgets"] = js.normalize_budgets(dict(body.budgets))
        except js.OverlayError as exc:
            raise HTTPException(422, f"Неверный бюджет: {exc}") from None
    return {"ok": True, "settings": _save(path, overlay)}


@router.get("/jeff-settings/users/{person_key}")
async def get_user(person_key: str, request: Request):
    key = _key(person_key)
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    return {"key": key, "override": (overlay.get("users") or {}).get(key)}


@router.put("/jeff-settings/users/{person_key}")
async def put_user(person_key: str, body: StyleIn, request: Request):
    key = _key(person_key)
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    users = dict(overlay.get("users") or {})
    users[key] = _style_payload(body, keep_absent_extra=True)
    overlay["users"] = users
    saved = _save(path, overlay)
    return {"ok": True, "key": key, "override": saved["users"][key]}


@router.delete("/jeff-settings/users/{person_key}")
async def delete_user(person_key: str, request: Request):
    key = _key(person_key)
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    users = dict(overlay.get("users") or {})
    removed = users.pop(key, None) is not None
    overlay["users"] = users
    _save(path, overlay)
    return {"ok": True, "removed": removed}


@router.post("/jeff-settings/reset")
async def reset(request: Request):
    """«Откат к обычному»: stock Jeff for everyone; spend caps stay as the owner set them."""
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    fresh = js.empty_overlay()
    fresh["budgets"] = overlay.get("budgets") or fresh["budgets"]
    return {"ok": True, "settings": _save(path, fresh)}


FEATURE = Feature(name="jeff_settings", router=router)
