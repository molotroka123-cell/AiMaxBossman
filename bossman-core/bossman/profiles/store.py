"""Durable-хранилище профилей (JSON, атомарная запись). Переживает рестарт.

Без второго секрет-стора: профиль не хранит секретов — только идентификаторы
привязки (device_id, telegram_user_id) и тумблеры. Путь — под workspace_dir.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from .models import Profile, normalize_toggles

_SAFE = re.compile(r"[^a-z0-9._-]+")


def safe_id(raw: str) -> str:
    s = _SAFE.sub("-", str(raw or "").strip().lower()).strip("-.")
    return s[:48]


def new_profile_id(name: str) -> str:
    base = safe_id(name) or "profile"
    return f"{base}-{secrets.token_hex(3)}"


class ProfileStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "profiles.json"

    # ---- persistence ----

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self._path)

    # ---- CRUD ----

    def create(self, name: str, *, device_id: str | None = None,
               telegram_user_id: str | None = None, toggles: dict | None = None) -> Profile:
        data = self._load()
        pid = new_profile_id(name)
        while pid in data:                      # маловероятная коллизия — перегенерируем
            pid = new_profile_id(name)
        now = time.time()
        prof = Profile(
            id=pid, name=str(name or pid), device_id=device_id,
            telegram_user_id=(str(telegram_user_id) if telegram_user_id else None),
            enabled=True, toggles=normalize_toggles(toggles),
            memory_namespace=f"profile:{pid}", created_at=now, updated_at=now)
        data[pid] = prof.to_row()
        self._save(data)
        return prof

    def get(self, profile_id: str) -> Profile | None:
        row = self._load().get(str(profile_id))
        return Profile.from_row(row) if row else None

    def by_device(self, device_id: str | None) -> Profile | None:
        if not device_id:
            return None
        for row in self._load().values():
            if row.get("device_id") == device_id:
                return Profile.from_row(row)
        return None

    def by_telegram(self, telegram_user_id: str | None) -> Profile | None:
        if not telegram_user_id:
            return None
        tid = str(telegram_user_id)
        for row in self._load().values():
            if row.get("telegram_user_id") == tid:
                return Profile.from_row(row)
        return None

    def list(self) -> list[Profile]:
        return [Profile.from_row(r) for r in self._load().values()]

    def update_toggles(self, profile_id: str, patch: dict) -> Profile | None:
        data = self._load()
        row = data.get(str(profile_id))
        if not row:
            return None
        merged = dict(row.get("toggles") or {})
        merged.update(patch or {})
        row["toggles"] = normalize_toggles(merged)
        row["updated_at"] = time.time()
        self._save(data)
        return Profile.from_row(row)

    def set_enabled(self, profile_id: str, enabled: bool) -> Profile | None:
        data = self._load()
        row = data.get(str(profile_id))
        if not row:
            return None
        row["enabled"] = bool(enabled)
        row["updated_at"] = time.time()
        self._save(data)
        return Profile.from_row(row)

    def bind(self, profile_id: str, *, device_id: str | None = None,
             telegram_user_id: str | None = None) -> Profile | None:
        data = self._load()
        row = data.get(str(profile_id))
        if not row:
            return None
        if device_id is not None:
            row["device_id"] = device_id
        if telegram_user_id is not None:
            row["telegram_user_id"] = str(telegram_user_id)
        row["updated_at"] = time.time()
        self._save(data)
        return Profile.from_row(row)
