"""V2.6 — Verified Execution Cache (модуль E): evidence-aware кэш результатов.

НЕ кэш «ответов LLM как истины»: переиспользуются только результаты с
provenance и валидными условиями. Ключ включает отпечаток входа + тип + отпечаток
окружения + версию инструмента/политики. Security-sensitive классы результатов
не кэшируются НИКОГДА (fail-closed по имени kind). Хранение — in-proc bounded
LRU + TTL: дёшево, без новой инфраструктуры; инвалидация по префиксу.
Hit/miss-счётчики — для честного измерения пользы.
"""
from __future__ import annotations

import hashlib
import time as _time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# Классы результатов, которые ЗАПРЕЩЕНО кэшировать (живое/чувствительное):
NEVER_CACHE_KINDS = frozenset({
    "live_balance", "browser_state", "email_search", "security_state",
    "market_data", "news", "credentials", "approval",
})

MAX_ENTRIES = 512


def fingerprint(*parts: Any) -> str:
    """Стабильный отпечаток входа/окружения: sha256 конкатенации repr-частей."""
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


@dataclass(slots=True)
class CacheRecord:
    result: Any
    verified: bool
    evidence: str = ""              # откуда результат (provenance)
    created_at: float = field(default_factory=_time.time)
    expires_at: float = 0.0         # 0 = без TTL (инвалидация только явная)
    env_fingerprint: str = ""

    def valid(self, *, env_fingerprint: str = "", now: float | None = None) -> bool:
        t = now or _time.time()
        if self.expires_at and t >= self.expires_at:
            return False
        if self.env_fingerprint and env_fingerprint \
                and self.env_fingerprint != env_fingerprint:
            return False            # окружение изменилось — результат невалиден
        return True


class ExecutionCache:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._data: OrderedDict[str, CacheRecord] = OrderedDict()
        self._max = max_entries
        self.hits = 0
        self.misses = 0
        self.blocked_by_reuse_gate = 0
        self.last_reuse_refusal = ""
        self.reuse_gate = None            # Callable[[str], tuple[bool, str]] | None

    def _reuse_gate_active(self) -> bool:
        try:
            from .learning_guard.runtime_bridge import reuse_allowed, reuse_experiment_enabled  # noqa: WPS433
        except Exception:  # noqa: BLE001
            return False
        if not reuse_experiment_enabled():
            return False
        if self.reuse_gate is None:
            self.reuse_gate = reuse_allowed
        return True
        self.rejected_kinds = 0

    @staticmethod
    def key(kind: str, *parts: Any) -> str:
        return f"{kind}:{fingerprint(*parts)}"

    def put(self, key: str, result: Any, *, verified: bool, evidence: str = "",
            ttl_s: float = 0.0, env_fingerprint: str = "") -> bool:
        kind = key.split(":", 1)[0]
        if kind in NEVER_CACHE_KINDS:
            self.rejected_kinds += 1
            return False            # fail-closed: живое/чувствительное не кэшируем
        rec = CacheRecord(result=result, verified=verified, evidence=evidence,
                          expires_at=(_time.time() + ttl_s) if ttl_s else 0.0,
                          env_fingerprint=env_fingerprint)
        self._data[key] = rec
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)   # LRU-вытеснение
        return True

    def get(self, key: str, *, env_fingerprint: str = "",
            require_verified: bool = False, task_class: str = "") -> CacheRecord | None:
        # Audit P0 wiring: under BOSSMAN_COGNITIVE_REUSE_EXPERIMENT (OFF by default)
        # reuse is served only when the recorded same-model A/B allows it; the flag
        # off leaves the cache untouched.
        if self._reuse_gate_active():
            allowed, why = self.reuse_gate(task_class or key.split(":", 1)[0])
            if not allowed:
                self.misses += 1
                self.blocked_by_reuse_gate += 1
                self.last_reuse_refusal = why
                return None
        rec = self._data.get(key)
        if rec is None or not rec.valid(env_fingerprint=env_fingerprint):
            if rec is not None:
                self._data.pop(key, None)    # протухло — выкинуть
            self.misses += 1
            return None
        if require_verified and not rec.verified:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return rec

    def invalidate(self, prefix: str) -> int:
        doomed = [k for k in self._data if k.startswith(prefix)]
        for k in doomed:
            self._data.pop(k, None)
        return len(doomed)

    def stats(self) -> dict:
        return {"entries": len(self._data), "hits": self.hits,
                "misses": self.misses, "rejected_kinds": self.rejected_kinds}


# Процессный синглтон (как context_engine.get_engine) — НЕ вторая инфраструктура.
_CACHE: ExecutionCache | None = None


def get_cache() -> ExecutionCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = ExecutionCache()
    return _CACHE
