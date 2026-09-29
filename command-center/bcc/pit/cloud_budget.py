"""Zero-cost cloud budget shared by every Jeff surface (Telegram bot and window).

One OpenRouter key backs both surfaces, so the daily request counter, the
provider-wide pause and per-model cooldowns live in one small JSON file under
``pit-v1.7`` instead of each surface's conversation store. The owner audit
(`python -m bcc.pit.cli routes`) reads the same file. No content, no secrets.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL_COOLDOWN_SECONDS = 120
PROVIDER_COOLDOWN_SECONDS = 900
FILE_NAME = "cloud_budget.json"


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def classify_rate_limit(message: str) -> str:
    """'provider_daily_limit' for an account-wide free-tier cap, else 'model_rate_limited'."""
    lowered = str(message or "").lower()
    if "per-day" in lowered or "per day" in lowered or "daily" in lowered:
        return "provider_daily_limit"
    return "model_rate_limited"


class CloudBudget:
    _lock = threading.Lock()

    def __init__(self, home: Path, daily_budget: int):
        self.path = Path(home) / FILE_NAME
        self.daily_budget = int(daily_budget)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if data.get("date") != _day():
            data = {"date": _day(), "used": 0, "cooldown_until": data.get("cooldown_until", 0),
                    "stop_reason": data.get("stop_reason"), "models": data.get("models", {})}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def status(self) -> dict:
        data = self._load()
        now = time.time()
        until = float(data.get("cooldown_until", 0) or 0)
        models = {m: t for m, t in (data.get("models") or {}).items() if float(t) > now}
        return {"date": data["date"], "used_today": int(data.get("used", 0)), "budget": self.daily_budget,
                "cooldown_active": until > now or bool(models),
                "cooldown_until": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until))
                                   if until > now else None),
                "models_cooling": sorted(models),
                "last_stop_reason": data.get("stop_reason")}

    def blocked(self) -> str:
        """Provider-wide reason cloud may not be used now ('' = allowed)."""
        data = self._load()
        if float(data.get("cooldown_until", 0) or 0) > time.time():
            return str(data.get("stop_reason") or "rate_limited")
        if int(data.get("used", 0)) >= self.daily_budget:
            return "daily_budget"
        return ""

    def model_blocked(self, model: str) -> bool:
        return float((self._load().get("models") or {}).get(model, 0) or 0) > time.time()

    def spend(self) -> None:
        with self._lock:
            data = self._load()
            data["used"] = int(data.get("used", 0)) + 1
            self._save(data)

    def stop(self, reason: str, *, until: float | None = None) -> bool:
        """Record a provider-wide pause. Returns True when this is a new event."""
        with self._lock:
            data = self._load()
            new = data.get("stop_reason") != reason or float(data.get("cooldown_until", 0) or 0) < time.time()
            data["stop_reason"] = reason
            if until is not None:
                data["cooldown_until"] = until
            self._save(data)
            return new

    def cool_model(self, model: str, seconds: float = MODEL_COOLDOWN_SECONDS) -> None:
        with self._lock:
            data = self._load()
            models = dict(data.get("models") or {})
            models[str(model)[:120]] = time.time() + seconds
            data["models"] = {m: t for m, t in models.items() if float(t) > time.time()}
            data["stop_reason"] = "rate_limited"
            self._save(data)
