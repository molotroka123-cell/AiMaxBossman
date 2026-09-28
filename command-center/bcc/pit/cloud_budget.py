"""Zero-cost cloud budget shared by every Jeff surface (Telegram bot and window).

One OpenRouter key backs both surfaces, so the daily request counter, the
provider-wide pause and per-model cooldowns live in one small JSON file under
``pit-v1.7`` instead of each surface's conversation store. The owner audit
(`python -m bcc.pit.cli routes`) reads the same file. No content, no secrets.

The Telegram bot and the Jeff window are separate PROCESSES. Every read and
read-modify-write therefore holds a kernel byte-range lock on
``cloud_budget.lock`` (released by the OS if a process dies). Without it, on
Windows ``os.replace`` failed while the other process was reading, and a
failed read was mistaken for "no file": the counter restarted at zero and a
provider daily-limit pause was erased, so Jeff hammered a capped provider.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL_COOLDOWN_SECONDS = 120
PROVIDER_COOLDOWN_SECONDS = 900
FILE_NAME = "cloud_budget.json"
LOCK_NAME = "cloud_budget.lock"
LOCK_WAIT_SECONDS = 10.0


class BudgetStateUnreadable(OSError):
    """The budget file exists but cannot be read now: never treat it as empty."""


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


@contextlib.contextmanager
def _process_lock(path: Path):
    """Exclusive cross-process lock on byte 0 of ``path``; unlocked before close."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BudgetStateUnreadable("cloud budget lock busy") from None
                time.sleep(0.005)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class CloudBudget:
    _lock = threading.Lock()

    def __init__(self, home: Path, daily_budget: int):
        self.path = Path(home) / FILE_NAME
        self.lock_path = Path(home) / LOCK_NAME
        self.daily_budget = int(daily_budget)

    @contextlib.contextmanager
    def _locked(self):
        with self._lock, _process_lock(self.lock_path):
            yield

    def _read(self) -> dict:
        last: OSError | None = None
        for _ in range(5):
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return {}
            except OSError as exc:          # e.g. antivirus holding the file
                last = exc
                time.sleep(0.02)
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if isinstance(data, dict):
                return data
            return self._quarantine_corrupt()
        raise BudgetStateUnreadable("cloud budget unreadable") from last

    def _quarantine_corrupt(self) -> dict:
        """A corrupt counter is not "nothing spent today": starting clean handed
        out a whole new daily budget (and dropped a provider pause). The bad file
        is set aside for the owner and today's budget counts as exhausted; the
        next UTC day starts normally. Called under the lock."""
        aside = self.path.with_name(f"{self.path.stem}.corrupt-{int(time.time())}.json")
        with contextlib.suppress(OSError):
            os.replace(self.path, aside)
        data = {"date": _day(), "used": max(0, self.daily_budget), "exhausted": True,
                "cooldown_until": 0, "stop_reason": "budget_corrupt", "models": {}}
        self._save(data)                    # persisted: the next read must not start clean either
        return data

    def _spent_out(self, data: dict) -> bool:
        return bool(data.get("exhausted")) or int(data.get("used", 0)) >= self.daily_budget

    def _load(self) -> dict:
        data = self._read()
        if data.get("date") != _day():
            data = {"date": _day(), "used": 0, "cooldown_until": data.get("cooldown_until", 0),
                    "stop_reason": data.get("stop_reason"), "models": data.get("models", {})}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    raise
                time.sleep(0.02)

    def status(self) -> dict:
        with self._locked():
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
        """Provider-wide reason cloud may not be used now ('' = allowed).

        Fails closed: an unreadable budget blocks cloud (local still answers).
        """
        try:
            with self._locked():
                data = self._load()
        except OSError:
            return "budget_unreadable"
        if float(data.get("cooldown_until", 0) or 0) > time.time():
            return str(data.get("stop_reason") or "rate_limited")
        if self._spent_out(data):
            return "daily_budget"
        return ""

    def try_spend(self) -> str:
        """Check AND count one request under one lock ('' = counted, may send).

        `blocked()` then `spend()` were two lock sections: concurrent turns (bot
        and window, or two chats) all passed the check at budget-1 and all sent.
        """
        try:
            with self._locked():
                data = self._load()
                if float(data.get("cooldown_until", 0) or 0) > time.time():
                    return str(data.get("stop_reason") or "rate_limited")
                if self._spent_out(data):
                    return "daily_budget"
                data["used"] = int(data.get("used", 0)) + 1
                self._save(data)
            return ""
        except OSError:
            return "budget_unreadable"

    def model_blocked(self, model: str) -> bool:
        try:
            with self._locked():
                models = self._load().get("models") or {}
        except OSError:
            return True
        return float(models.get(model, 0) or 0) > time.time()

    def spend(self) -> bool:
        """Count one sent request. False when the counter could not be written."""
        try:
            with self._locked():
                data = self._load()
                data["used"] = int(data.get("used", 0)) + 1
                self._save(data)
            return True
        except OSError:
            return False

    def stop(self, reason: str, *, until: float | None = None) -> bool:
        """Record a provider-wide pause. Returns True when this is a new event.

        Without ``until`` (the daily budget) only a changed reason is new, so a
        spent budget is logged once, not on every following turn.
        """
        try:
            with self._locked():
                data = self._load()
                if until is None:
                    new = data.get("stop_reason") != reason
                else:
                    new = (data.get("stop_reason") != reason
                           or float(data.get("cooldown_until", 0) or 0) < time.time())
                if not new and until is None:
                    return False
                data["stop_reason"] = reason
                if until is not None:
                    data["cooldown_until"] = until
                self._save(data)
                return new
        except OSError:
            return False

    def cool_model(self, model: str, seconds: float = MODEL_COOLDOWN_SECONDS) -> bool:
        try:
            with self._locked():
                data = self._load()
                models = dict(data.get("models") or {})
                models[str(model)[:120]] = time.time() + seconds
                data["models"] = {m: t for m, t in models.items() if float(t) > time.time()}
                data["stop_reason"] = "rate_limited"
                self._save(data)
            return True
        except OSError:
            return False
