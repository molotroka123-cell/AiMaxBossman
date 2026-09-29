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
from ..pit import participant_admin as pa
from ..pit import participant_profile as pp
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
        prof, _ = pp.read_profile(jeff_dir, key)
        out.append({"key": key, "label": label, "surface": surface,
                    "has_override": key in (overlay.get("users") or {}),
                    "display_name": prof["display_name"], "role_label": prof["role_label"],
                    "access": prof["access"]})

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
    try:
        profiled = sorted(p.stem for p in pp.profile_dir(jeff_dir).glob("*.json"))
    except OSError:
        profiled = []
    for name in profiled:
        try:
            add(validate_person_key(name), f"Участник {name[:6]} (не найден)", "unknown")
        except ValueError:
            continue
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


# ---------------------------------------------------------------- Jeff Admin (Bossman 1.9)
# Per-participant profile, stored context, clear/revoke, readiness and events. Same router,
# same owner-only auth as everything above; every call names exactly one person key.

class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    role_label: str | None = Field(default=None, max_length=200)
    allowed_topics: list[str] | None = None
    blocked_topics: list[str] | None = None
    language: str | None = None
    voice_reply: bool | None = None
    telegram_enabled: bool | None = None


class ClearIn(BaseModel):
    scope: str = "all"


class RevokeIn(BaseModel):
    clear_data: bool = True


def _vault(request: Request):
    from ..pit.vault import PersonaVault
    _, jeff_dir, cfg = _paths(request)
    salt = _salt(_cc_data_dir(request))
    if not salt:
        raise HTTPException(409, "Jeff ещё не настроен: нет ключа идентификации участников.")
    return PersonaVault(jeff_dir, salt), jeff_dir, cfg, salt


def _person_row(request: Request, key: str) -> dict:
    path, jeff_dir, cfg = _paths(request)
    overlay, _, _ = _current(path)
    for row in participants(_cc_data_dir(request), jeff_dir, cfg, overlay):
        if row["key"] == key:
            return row
    return {"key": key, "label": f"Участник {key[:6]}", "surface": "unknown",
            "has_override": False, "display_name": "", "role_label": "", "access": "active"}


@router.get("/jeff-settings/participants/{person_key}")
async def get_participant(person_key: str, request: Request):
    key = _key(person_key)
    vault, jeff_dir, _, _ = _vault(request)
    path, _, _ = _paths(request)
    overlay, _, _ = _current(path)
    return {"participant": _person_row(request, key), **pa.profile_view(jeff_dir, key),
            "style_override": (overlay.get("users") or {}).get(key),
            "context": pa.stored_context(vault, key),
            "languages": list(pp.LANGUAGES),
            "isolation": pa.isolation_report(vault)}


@router.put("/jeff-settings/participants/{person_key}/profile")
async def put_participant_profile(person_key: str, body: ProfileIn, request: Request):
    key = _key(person_key)
    _, jeff_dir, _ = _paths(request)
    current, _ = pp.read_profile(jeff_dir, key)
    merged = dict(current)
    merged.update(body.model_dump(exclude_none=True))
    merged["access"] = current["access"]      # only /revoke and /restore change access
    try:
        saved = pp.write_profile(jeff_dir, key, merged)
    except js.OverlayError as exc:
        raise HTTPException(422, f"Профиль участника не сохранён: {exc}") from None
    pa.log_event(jeff_dir, key, "profile_saved")
    return {"ok": True, "profile": saved}


@router.post("/jeff-settings/participants/{person_key}/clear")
async def clear_participant(person_key: str, body: ClearIn, request: Request):
    key = _key(person_key)
    vault, jeff_dir, cfg, salt = _vault(request)
    if body.scope not in pa.CLEAR_SCOPES:
        raise HTTPException(422, "Неизвестная область очистки.")
    result = pa.clear(vault, key, body.scope)
    if body.scope in {"history", "all"}:
        result["chat_windows_cleared"] = pa.forget_chat_history(
            _cc_data_dir(request), jeff_dir, cfg, salt, key)
    return {"ok": True, **result}


@router.delete("/jeff-settings/participants/{person_key}/facts/{fact_id}")
async def delete_participant_fact(person_key: str, fact_id: str, request: Request):
    key = _key(person_key)
    vault, _, _, _ = _vault(request)
    if not pa.delete_fact(vault, key, fact_id):
        raise HTTPException(404, "Такого факта нет у этого участника.")
    return {"ok": True}


@router.post("/jeff-settings/participants/{person_key}/pause-memory")
async def pause_participant_memory(person_key: str, request: Request):
    key = _key(person_key)
    vault, _, _, _ = _vault(request)
    return {"ok": True, "paused": pa.pause_memory(vault, key)}


@router.post("/jeff-settings/participants/{person_key}/revoke")
async def revoke_participant(person_key: str, body: RevokeIn, request: Request):
    """Close Jeff for one person and (by default) erase what Jeff stored about them."""
    key = _key(person_key)
    vault, jeff_dir, cfg, salt = _vault(request)
    current, _ = pp.read_profile(jeff_dir, key)
    current["access"] = "revoked"
    pp.write_profile(jeff_dir, key, current)
    result: dict = {}
    if body.clear_data:
        result = pa.clear(vault, key, "all")
        result["chat_windows_cleared"] = pa.forget_chat_history(
            _cc_data_dir(request), jeff_dir, cfg, salt, key)
    pa.log_event(jeff_dir, key, "revoked", cleared=body.clear_data)
    return {"ok": True, "access": "revoked", **result}


@router.post("/jeff-settings/participants/{person_key}/restore")
async def restore_participant(person_key: str, request: Request):
    key = _key(person_key)
    _, jeff_dir, _ = _paths(request)
    current, _ = pp.read_profile(jeff_dir, key)
    current["access"] = "active"
    pp.write_profile(jeff_dir, key, current)
    pa.log_event(jeff_dir, key, "restored")
    return {"ok": True, "access": "active"}


def _tail_jsonl(path: Path, last: int, fields: tuple[str, ...]) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-last:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append({k: row[k] for k in fields if k in row})
    return rows


@router.get("/jeff-settings/status")
async def jeff_status(request: Request):
    """Readiness, errors and recent events for the owner. No secrets, prompts or message text."""
    from ..pit import heartbeat as hb
    from ..pit import speech
    from ..pit.cloud_budget import CloudBudget
    path, jeff_dir, cfg = _paths(request)
    overlay, valid, error = _current(path)
    home = pit_home(jeff_dir)
    creds: dict = {}
    creds_error = ""
    try:
        from ..pit.config import _read_credentials
        creds = _read_credentials(home)
    except Exception as exc:  # noqa: BLE001
        creds_error = type(exc).__name__
    try:
        accounts = json.loads((home / "web" / "accounts.json").read_text(encoding="utf-8"))
        web_accounts = len(accounts.get("users") or {})
    except (OSError, ValueError):
        web_accounts = 0
    try:
        budget = CloudBudget(home, int(cfg.get("cloud_daily_request_budget", 200) or 200)).status()
    except Exception as exc:  # noqa: BLE001
        budget = {"error": type(exc).__name__}
    errors = []
    if not valid:
        errors.append({"where": "settings", "error": error})
    if creds_error:
        errors.append({"where": "credentials", "error": creds_error})
    from ..pit.cli import _queue_pending, _store_state
    for name in ("transport_error", "provider_last_error"):
        value = _store_state(home, name)
        if value:
            errors.append({"where": name, "error": str(value)[:200]})
    errors.extend({"where": "runtime", "error": r.get("kind", ""), "at": r.get("at", "")}
                  for r in _tail_jsonl(home / "logs" / "runtime_error.jsonl", 5, ("at", "kind")))
    people = participants(_cc_data_dir(request), jeff_dir, cfg, overlay)
    isolation = None
    salt = _salt(_cc_data_dir(request))
    if salt:
        from ..pit.vault import PersonaVault
        isolation = pa.isolation_report(PersonaVault(jeff_dir, salt))
    return {
        "readiness": {
            "jeff_configured": bool(cfg), "settings_valid": valid,
            "telegram_token_set": bool(creds.get("bot_token")), "web_only": bool(cfg.get("web_only")),
            "window_accounts": web_accounts, "participants": len(people),
            "revoked": sum(1 for p in people if p["access"] == "revoked"),
            "voice_asr": speech.asr_status(), "voice_tts": speech.tts_status(),
            "cloud_budget": budget, "ready": bool(cfg) and valid and not creds_error,
        },
        "isolation": isolation,
        "heartbeat": {"telegram": hb.read(home), "window": hb.read(home / "web"),
                      "poller_processes": hb.jeff_process_count(), "queue": _queue_pending(home)},
        "errors": errors,
        "events": {"owner": pa.recent_owner_events(jeff_dir, 20),
                   "replies": _tail_jsonl(home / "logs" / "delivery_log.jsonl", 10, ("at", "update_id"))},
    }


FEATURE = Feature(name="jeff_settings", router=router)
