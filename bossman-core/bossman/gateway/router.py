from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .backends import CircuitOpenError, OpenAIBackend
from .config import GatewayConfig, ModelTarget


class RouteNotFound(RuntimeError):
    pass


class CloudPolicyDenied(RuntimeError):
    """Алиас обслуживается только облаком, а облако запрещено политикой.

    Это НЕ «маршрут не найден»: маршрут есть, но он ведёт наружу, а владелец
    объявил, что данные наружу не уходят. Отдаётся как отдельный исход, чтобы
    ядро отличило «нечем обслужить» от «политика запретила отправку»."""
    pass


@dataclass(slots=True)
class Route:
    alias: str
    backend_name: str
    model: str
    target: ModelTarget
    backend: OpenAIBackend
    is_cloud: bool = False


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

    def resolve(self, alias: str, required_capabilities: set[str] | None = None,
                cloud_allowed: bool = True) -> list[Route]:
        """Маршруты под алиас.

        cloud_allowed=False (облачная политика never, либо ask без подтверждения)
        ВЫРЕЗАЕТ облачные цели ещё до сети: политику держит сам Gateway, а не
        надежда, что ядро правильно угадает облачность по имени алиаса. Если
        после этого не осталось ни одной цели — CloudPolicyDenied: данные не
        уходят никуда.
        """
        cfg = self.config.aliases.get(alias)
        if not cfg:
            raise RouteNotFound(f"Unknown model alias: {alias}")
        required = set(required_capabilities or ()) | cfg.required_capabilities
        candidates = []
        skipped_open: list[str] = []
        dropped_cloud = False
        for t in sorted(cfg.targets, key=lambda x: x.priority):
            backend = self.backends.get(t.backend)
            if not backend:
                continue
            if required and not required.issubset(t.capabilities):
                continue
            is_cloud = bool(getattr(backend.config, "cloud", False))
            if is_cloud and not cloud_allowed:
                dropped_cloud = True
                continue        # облачная цель при запрете облака — мимо, не в сеть
            if not backend.breaker.allow_attempt():
                # Разомкнутый автомат: цель ПРОПУСКАЕТСЯ, а не деприоритизируется
                # (иначе каждый запрос снова платит её полный таймаут). В
                # HALF_OPEN это одна пробная попытка; провал переоткроет автомат.
                skipped_open.append(f"{t.backend}/{t.model}")
                continue
            candidates.append(Route(alias, t.backend, t.model, t, backend, is_cloud))
        if not candidates:
            if dropped_cloud:
                raise CloudPolicyDenied(
                    f"алиас '{alias}' обслуживается только облаком, а облачная "
                    f"политика это запрещает — данные не отправлены")
            if skipped_open:
                raise CircuitOpenError(
                    f"все цели алиаса '{alias}' разомкнуты автоматом "
                    f"({', '.join(skipped_open)}) — отказ сразу, бэкенды не дёргаем")
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
