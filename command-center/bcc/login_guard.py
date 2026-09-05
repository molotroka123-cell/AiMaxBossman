"""SEC-03 (TZ-02 §2.2) — rate-limit на POST /api/login.

Token bucket на клиента (IP): B=5 неудачных попыток за 60 с, затем 429 с
`Retry-After`; после 20 неудач за час — lockout 15 мин и событие `auth.lockout`.
In-memory (без Redis): цель — не дать перебирать токен и не жечь БД, а не
распределённая защита. Успешный вход сбрасывает счётчик клиента.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Client:
    failures: list[float] = field(default_factory=list)      # моменты неудач за последний час
    locked_until: float = 0.0


class LoginRateLimiter:
    def __init__(self, *, burst: int = 5, window_s: float = 60.0, hour_limit: int = 20,
                 lockout_s: float = 900.0, clock=time.monotonic) -> None:
        self.burst, self.window_s, self.hour_limit, self.lockout_s = burst, window_s, hour_limit, lockout_s
        self.clock = clock
        self._clients: dict[str, _Client] = {}

    def _client(self, key: str) -> _Client:
        c = self._clients.setdefault(key, _Client())
        now = self.clock()
        c.failures = [t for t in c.failures if now - t < 3600.0]
        return c

    def check(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_s). Проверяется ДО сравнения токена."""
        c = self._client(key)
        now = self.clock()
        if c.locked_until > now:
            return False, round(c.locked_until - now, 1)
        recent = [t for t in c.failures if now - t < self.window_s]
        if len(recent) >= self.burst:
            n = len(recent) - self.burst
            delay = min(2.0 ** n, 300.0)                 # экспоненциальная задержка после исчерпания корзины
            retry = max(self.window_s - (now - recent[-self.burst]), delay)
            return False, round(retry, 1)
        return True, 0.0

    def failure(self, key: str) -> bool:
        """Зафиксировать неудачу; True — наступил lockout."""
        c = self._client(key)
        c.failures.append(self.clock())
        if len(c.failures) >= self.hour_limit:
            c.locked_until = self.clock() + self.lockout_s
            c.failures.clear()
            return True
        return False

    def success(self, key: str) -> None:
        self._clients.pop(key, None)
