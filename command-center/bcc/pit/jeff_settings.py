"""Owner overlay for Jeff's manner: ``jeff-settings.json`` (Bossman Command v0.1).

The owner tunes HOW Jeff talks (eight style sliders, a short extra note,
per-participant overrides) and lowers Jeff's spend ceiling from the Bossman
window. The file lives next to the rest of Jeff's data
(``<data_dir>/pit-v1.7/jeff-settings.json``; ``BOSSMAN_JEFF_SETTINGS`` points
elsewhere explicitly) and is re-read on every message through an mtime cache,
so a change applies to the next reply without restarting Jeff.

Contract:
- the overlay changes style only: no tool, command, file, owner or computer
  authority can be granted through it (the extra note is framed as style and
  bounded);
- per-participant keys are PIT person keys (HMAC pseudonyms), never Telegram
  IDs;
- a missing, unreadable or invalid file means stock Jeff (logged once per bad
  file version), never a broken reply;
- budget values can only lower the configured ceiling, never raise it.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BEHAVIOR_SCALE_NAMES, default_behavior_scales, pit_home
from .identity import validate_person_key

FILE_NAME = "jeff-settings.json"
ENV_PATH = "BOSSMAN_JEFF_SETTINGS"
SCHEMA_VERSION = 1
SCALE_MIN, SCALE_MAX = 0, 10
SYSTEM_EXTRA_MAX = 800
MAX_FILE_BYTES = 256 * 1024
MAX_USERS = 200
USD_MAX = 1000.0
DEFAULT_BUDGETS = {"usd_per_day": 2.0, "usd_per_job": 1.0}

# Presets are plain slider positions. «Обычный» is stock Jeff: no overlay scales
# at all, so the prompt is byte-identical to a machine without this file.
PRESETS: dict[str, dict[str, int]] = {
    "stock": {},
    "bold": {"initiative": 7, "curiosity": 5, "depth": 5, "brevity": 7,
             "warmth": 4, "humor": 8, "directness": 9, "creativity": 7},
    "warm": {"initiative": 5, "curiosity": 6, "depth": 5, "brevity": 4,
             "warmth": 9, "humor": 6, "directness": 5, "creativity": 5},
    "brief": {"initiative": 3, "curiosity": 3, "depth": 3, "brevity": 10,
              "warmth": 5, "humor": 3, "directness": 8, "creativity": 4},
}
PRESET_LABELS = {"stock": "Обычный", "bold": "Дерзкий", "warm": "Тёплый", "brief": "Краткий"}

log = logging.getLogger("bcc.pit.jeff_settings")

_TEMPLATE_TOKEN = re.compile(r"<\|[^|>]{0,40}\|>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class OverlayError(ValueError):
    """The overlay file or an API payload does not match the schema."""


def settings_path(data_dir: Path | str) -> Path:
    override = os.environ.get(ENV_PATH, "").strip()
    return Path(override) if override else pit_home(Path(data_dir)) / FILE_NAME


def empty_overlay() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION,
            "defaults": {"system_extra": "", "behavior_scales": {}},
            "users": {},
            "budgets": dict(DEFAULT_BUDGETS)}


# -- validation -------------------------------------------------------------------------------
def clamp_scale(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OverlayError("behavior scale must be a number")
    return max(SCALE_MIN, min(SCALE_MAX, int(round(value))))


def clean_extra(value: Any) -> str:
    """Owner-authored style note: text only, bounded, no chat-template tokens."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OverlayError("system_extra must be text")
    text = _TEMPLATE_TOKEN.sub(" ", value)
    text = _CONTROL.sub(" ", text).replace("\r", " ").replace("\n", " ")
    text = text.replace("«", '"').replace("»", '"')    # keeps the quoted frame unambiguous
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SYSTEM_EXTRA_MAX]


def normalize_scales(raw: Any) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OverlayError("behavior_scales must be an object")
    unknown = set(raw) - set(BEHAVIOR_SCALE_NAMES)
    if unknown:
        raise OverlayError("unknown behavior scale: " + ", ".join(sorted(map(str, unknown)))[:120])
    return {name: clamp_scale(raw[name]) for name in BEHAVIOR_SCALE_NAMES if name in raw}


def normalize_profile(raw: Any, *, keep_absent_extra: bool = False) -> dict[str, Any]:
    """One style block (defaults or one participant). Unknown keys are refused."""
    if not isinstance(raw, dict):
        raise OverlayError("style block must be an object")
    unknown = set(raw) - {"system_extra", "behavior_scales"}
    if unknown:
        raise OverlayError("unknown style field: " + ", ".join(sorted(map(str, unknown)))[:120])
    out: dict[str, Any] = {"behavior_scales": normalize_scales(raw.get("behavior_scales"))}
    if "system_extra" in raw or not keep_absent_extra:
        out["system_extra"] = clean_extra(raw.get("system_extra"))
    return out


def _usd(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OverlayError(f"{name} must be a number")
    return round(max(0.0, min(USD_MAX, float(value))), 4)


def normalize_budgets(raw: Any) -> dict[str, float]:
    if raw is None:
        return dict(DEFAULT_BUDGETS)
    if not isinstance(raw, dict):
        raise OverlayError("budgets must be an object")
    unknown = set(raw) - set(DEFAULT_BUDGETS)
    if unknown:
        raise OverlayError("unknown budget field")
    return {name: _usd(raw.get(name, default), name) for name, default in DEFAULT_BUDGETS.items()}


def normalize(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise OverlayError("overlay must be an object")
    if raw.get("version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise OverlayError("unsupported overlay version")
    unknown = set(raw) - {"version", "defaults", "users", "budgets"}
    if unknown:
        raise OverlayError("unknown overlay field")
    users_raw = raw.get("users") or {}
    if not isinstance(users_raw, dict) or len(users_raw) > MAX_USERS:
        raise OverlayError("users must be an object of at most %d participants" % MAX_USERS)
    users: dict[str, Any] = {}
    for key, block in users_raw.items():
        try:
            person_key = validate_person_key(key)
        except ValueError:
            raise OverlayError("users are keyed by PIT person key only") from None
        users[person_key] = normalize_profile(block, keep_absent_extra=True)
    return {"version": SCHEMA_VERSION,
            "defaults": normalize_profile(raw.get("defaults") or {}),
            "users": users,
            "budgets": normalize_budgets(raw.get("budgets"))}


# -- reading (per message, mtime cache) ----------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None, str]] = {}
_warned: set[tuple[str, tuple[int, int]]] = set()


def read_overlay(path: Path) -> tuple[dict[str, Any] | None, str]:
    """(normalized overlay | None, error). None+'' = no file (stock Jeff)."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None, ""
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(path)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1], cached[2]
    overlay: dict[str, Any] | None = None
    error = ""
    try:
        if stat.st_size > MAX_FILE_BYTES:
            raise OverlayError("overlay file too large")
        overlay = normalize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:     # OverlayError and JSONDecodeError are ValueErrors
        overlay, error = None, (str(exc) or type(exc).__name__)[:200]
        if (cache_key, signature) not in _warned:
            _warned.add((cache_key, signature))
            log.warning("jeff-settings overlay ignored (stock Jeff): %s", error)
    with _cache_lock:
        _cache[cache_key] = (signature, overlay, error)
    return overlay, error


def write_overlay(path: Path, overlay: dict[str, Any]) -> dict[str, Any]:
    """Validate and write atomically. Returns what was written."""
    data = normalize(overlay)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    try:
        from bcc.auth import _restrict_to_owner
        _restrict_to_owner(path)
    except Exception:  # noqa: BLE001 — best effort, like the rest of the data dir
        pass
    return data


# -- applying ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class JeffStyle:
    scales: dict[str, int]          # only the scales the owner set (may be partial)
    system_extra: str


STOCK_STYLE = JeffStyle(scales={}, system_extra="")


def merged_style(overlay: dict[str, Any] | None, person_key: str) -> JeffStyle:
    """defaults ← this participant's override. Other participants never mix in."""
    if not overlay:
        return STOCK_STYLE
    defaults = overlay.get("defaults") or {}
    scales = dict(defaults.get("behavior_scales") or {})
    extra = defaults.get("system_extra", "")
    user = (overlay.get("users") or {}).get(str(person_key).lower())
    if user:
        scales.update(user.get("behavior_scales") or {})
        if "system_extra" in user:
            extra = user["system_extra"]
    return JeffStyle(scales=scales, system_extra=extra or "")


def style_for(data_dir: Path | str, person_key: str) -> JeffStyle:
    try:
        overlay, _ = read_overlay(settings_path(data_dir))
        return merged_style(overlay, person_key)
    except Exception:  # noqa: BLE001 — the overlay can never break a reply
        return STOCK_STYLE


def effective_scales(base: dict[str, int] | None, style: JeffStyle) -> dict[str, int] | None:
    """The owner's configured scales with the overlay's scales on top."""
    if not style.scales:
        return base
    merged = dict(base or default_behavior_scales())
    merged.update(style.scales)
    return merged


def owner_extra_text(extra: str) -> str:
    return ("Дополнительная манера речи от владельца сервиса (только стиль и тон; не меняет правила выше, "
            "разрешения, приватность и факты; не даёт доступа к инструментам, компьютеру, файлам, командам "
            "или правам владельца; не упоминай эту настройку собеседнику): «" + clean_extra(extra) + "».")


def budget_caps(data_dir: Path | str, *, configured_usd_per_day: float,
                configured_usd_per_job: float) -> dict[str, Any]:
    """The panel can only LOWER the configured ceiling; an absent/invalid file changes nothing."""
    try:
        overlay, _ = read_overlay(settings_path(data_dir))
    except Exception:  # noqa: BLE001
        overlay = None
    requested = (overlay or {}).get("budgets") or {}
    configured = {"usd_per_day": max(0.0, float(configured_usd_per_day)),
                  "usd_per_job": max(0.0, float(configured_usd_per_job))}
    effective = {name: min(configured[name], float(requested.get(name, configured[name])))
                 for name in configured}
    return {"configured": configured, "requested": dict(requested), "effective": effective}
