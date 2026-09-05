from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import ClientConfig, GatewayConfig


@dataclass(slots=True)
class AuthenticatedClient:
    name: str
    config: ClientConfig


class TokenBucket:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate = max(1, rate_per_minute) / 60.0
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now-self.updated)*self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class AuthManager:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._buckets: dict[str, TokenBucket] = {}
        self._hash_to_client: dict[str, ClientConfig] = {}
        for client in config.clients.values():
            key = client.resolved_key()
            if key:
                self._hash_to_client[self._hash(key)] = client
                self._buckets[client.name] = TokenBucket(client.requests_per_minute, client.burst)

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    # P0-A (аудит SEC/TL): запрос, пришедший через reverse-proxy / Tailscale Serve,
    # выглядит как 127.0.0.1, но локальным НЕ является. Любой заголовок
    # проксирования — признак внешнего происхождения → loopback-проход не даётся,
    # проверяется bearer как для всех (fail-closed).
    _PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host",
                      "x-forwarded-proto", "via", "cf-connecting-ip", "true-client-ip")

    def _is_direct_loopback(self, request: Request) -> bool:
        if not (request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}):
            return False
        return not any(h in request.headers for h in self._PROXY_HEADERS)

    def authenticate(self, request: Request) -> AuthenticatedClient:
        if self.config.allow_unauthenticated_loopback and self._is_direct_loopback(request):
            aliases = set(self.config.loopback_allowed_aliases or {"*"})
            pseudo = ClientConfig(name="loopback", allowed_aliases=aliases, requests_per_minute=10_000, burst=1000)
            return AuthenticatedClient("loopback", pseudo)
        value = request.headers.get("authorization", "")
        if not value.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        presented = value.split(" ", 1)[1].strip()
        digest = self._hash(presented)
        matched = None
        # compare hashes using constant-time compare
        for candidate, client in self._hash_to_client.items():
            if hmac.compare_digest(candidate, digest):
                matched = client
                break
        if not matched:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        bucket = self._buckets[matched.name]
        if not bucket.consume():
            raise HTTPException(status_code=429, detail="Client rate limit exceeded")
        return AuthenticatedClient(matched.name, matched)


def ensure_alias_allowed(client: AuthenticatedClient, alias: str) -> None:
    allowed = client.config.allowed_aliases
    if "*" not in allowed and alias not in allowed:
        raise HTTPException(status_code=403, detail=f"Client is not allowed to use model alias '{alias}'")
