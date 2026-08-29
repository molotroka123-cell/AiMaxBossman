from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .backends import OpenAIBackend
from .config import GatewayConfig, ModelTarget


class RouteNotFound(RuntimeError):
    pass


@dataclass(slots=True)
class Route:
    alias: str
    backend_name: str
    model: str
    target: ModelTarget
    backend: OpenAIBackend


class ModelRouter:
    def __init__(self, config: GatewayConfig, backends: dict[str, OpenAIBackend] | None = None):
        self.config = config
        self.backends = backends or {name: OpenAIBackend(cfg) for name, cfg in config.backends.items() if cfg.enabled}

    async def close(self) -> None:
        await asyncio.gather(*(b.close() for b in self.backends.values()), return_exceptions=True)

    async def refresh_health(self, force: bool = False) -> dict[str, dict]:
        now = time.time()
        async def one(name: str, backend: OpenAIBackend):
            if force or now - backend.health.checked_at >= self.config.health_ttl_seconds:
                await backend.probe()
            return name, {
                "healthy": backend.health.healthy,
                "checked_at": backend.health.checked_at,
                "error": backend.health.error,
                "latency_ms": backend.health.latency_ms,
            }
        pairs = await asyncio.gather(*(one(n,b) for n,b in self.backends.items()))
        return dict(pairs)

    def resolve(self, alias: str, required_capabilities: set[str] | None = None) -> list[Route]:
        cfg = self.config.aliases.get(alias)
        if not cfg:
            raise RouteNotFound(f"Unknown model alias: {alias}")
        required = set(required_capabilities or ()) | cfg.required_capabilities
        candidates = []
        for t in sorted(cfg.targets, key=lambda x: x.priority):
            backend = self.backends.get(t.backend)
            if not backend:
                continue
            if required and not required.issubset(t.capabilities):
                continue
            candidates.append(Route(alias, t.backend, t.model, t, backend))
        if not candidates:
            raise RouteNotFound(f"No configured target for alias '{alias}' with capabilities {sorted(required)}")
        # healthy targets first; unchecked targets are optimistically usable
        return sorted(candidates, key=lambda r: (r.backend.health.checked_at > 0 and not r.backend.health.healthy, r.target.priority))

    def list_models(self) -> list[dict]:
        out = []
        for alias, cfg in self.config.aliases.items():
            out.append({
                "id": alias,
                "object": "model",
                "owned_by": "bossman-gateway",
                "capabilities": sorted(cfg.required_capabilities | set().union(*(t.capabilities for t in cfg.targets))),
            })
        return out
