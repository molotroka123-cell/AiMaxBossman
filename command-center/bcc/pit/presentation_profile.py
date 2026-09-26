"""Participant presentation profile for Jeff.

This is deliberately NOT an authority/profile router. It stores only how Jeff
should be presented to one participant (avatar + voice preferences) inside that
participant's existing PersonaVault directory.

No model/provider/tool permissions are accepted here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any

from .vault import PersonaVault, _atomic_json

PROFILE_FILE = "presentation.json"

ALLOWED_AVATARS = frozenset({"aurora", "ember", "mono", "cobalt"})
VOICE_REPLY_MODES = frozenset({"TEXT_ONLY", "VOICE_ON_REQUEST", "VOICE_AUTO"})

DEFAULT_AVATAR = "aurora"
DEFAULT_VOICE_MODE = "TEXT_ONLY"


@dataclass(frozen=True)
class PresentationProfile:
    avatar_id: str = DEFAULT_AVATAR
    voice_id: str = ""
    voice_rate: float = 1.0
    voice_pitch: float = 1.0
    voice_reply_mode: str = DEFAULT_VOICE_MODE
    schema: str = "bossman.pit.presentation/1"

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_number(value: Any, *, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < low or number > high:
        return default
    return round(number, 2)


def normalize_profile(data: dict[str, Any] | None) -> PresentationProfile:
    data = data if isinstance(data, dict) else {}
    avatar = str(data.get("avatar_id") or DEFAULT_AVATAR)
    if avatar not in ALLOWED_AVATARS:
        avatar = DEFAULT_AVATAR

    voice_id = str(data.get("voice_id") or "").strip()[:160]
    # Presentation metadata must remain text-only. Control characters can turn
    # logs/UI into an authority-confusing surface even though this is local data.
    voice_id = "".join(ch for ch in voice_id if ch.isprintable())

    mode = str(data.get("voice_reply_mode") or DEFAULT_VOICE_MODE).upper()
    if mode not in VOICE_REPLY_MODES:
        mode = DEFAULT_VOICE_MODE

    return PresentationProfile(
        avatar_id=avatar,
        voice_id=voice_id,
        voice_rate=_bounded_number(data.get("voice_rate"), low=0.7, high=1.4, default=1.0),
        voice_pitch=_bounded_number(data.get("voice_pitch"), low=0.7, high=1.3, default=1.0),
        voice_reply_mode=mode,
    )


def load_presentation(vault: PersonaVault, person_key: str) -> PresentationProfile:
    path = vault.person_dir(person_key) / PROFILE_FILE
    if not path.is_file():
        return PresentationProfile()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return PresentationProfile()
    return normalize_profile(raw)


def save_presentation(
    vault: PersonaVault,
    person_key: str,
    *,
    avatar_id: str | None = None,
    voice_id: str | None = None,
    voice_rate: float | None = None,
    voice_pitch: float | None = None,
    voice_reply_mode: str | None = None,
) -> PresentationProfile:
    current = load_presentation(vault, person_key)
    patch: dict[str, Any] = current.public()
    for key, value in {
        "avatar_id": avatar_id,
        "voice_id": voice_id,
        "voice_rate": voice_rate,
        "voice_pitch": voice_pitch,
        "voice_reply_mode": voice_reply_mode,
    }.items():
        if value is not None:
            patch[key] = value
    profile = normalize_profile(patch)
    _atomic_json(vault.ensure(person_key) / PROFILE_FILE, profile.public())
    return profile


def clear_presentation(vault: PersonaVault, person_key: str) -> None:
    path = vault.person_dir(person_key) / PROFILE_FILE
    path.unlink(missing_ok=True)
